"""Strict identity validation for Docker image-save OCI-layout archives."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
import tempfile
import zlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from io import BufferedIOBase
from pathlib import Path, PurePosixPath
from typing import Any

DOCKER_SAVE_OCI_LAYOUT_FORMAT = "docker_image_save_oci_layout_tar_v1"
OCI_MANIFEST_IMAGE_ID_KIND = "oci_manifest_digest"

_SHA256 = re.compile(r"^sha256:([0-9a-f]{64})$")
_BLOB_PATH = re.compile(r"^blobs/sha256/([0-9a-f]{64})$")
_OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_OCI_CONFIG_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.config.v1+json",
        "application/vnd.docker.container.image.v1+json",
    }
)
_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.docker.image.rootfs.diff.tar",
        "application/vnd.docker.image.rootfs.diff.tar.gzip",
    }
)
_GZIP_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.docker.image.rootfs.diff.tar.gzip",
    }
)
_DIRECTORIES = frozenset({"blobs", "blobs/sha256"})
_TOP_LEVEL_FILES = frozenset({"index.json", "manifest.json", "oci-layout"})
_MAX_MEMBERS = 50_000
_MAX_INNER_LAYER_MEMBERS = 50_000
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_COMPRESSED_LAYER_BYTES = 2 * 1024 * 1024 * 1024
_MAX_TOTAL_COMPRESSED_LAYER_BYTES = 8 * 1024 * 1024 * 1024
_MAX_EXPANDED_LAYER_BYTES = 2 * 1024 * 1024 * 1024
_MAX_TOTAL_EXPANDED_LAYER_BYTES = 8 * 1024 * 1024 * 1024
_MAX_TAR_ZERO_PADDING = 16 * 1024 * 1024
_BLOCK_SIZE = 1024 * 1024


class ImageArchiveError(ValueError):
    """Raised when an image archive is unsafe, ambiguous, or identity-mismatched."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ImageArchiveError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _json(payload: bytes, label: str) -> Any:
    try:
        return json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ImageArchiveError(f"non-finite JSON value {token!r}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ImageArchiveError(f"{label} is not valid UTF-8 JSON: {exc}") from exc


def _safe_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members = archive.getmembers()
    if not members or len(members) > _MAX_MEMBERS:
        raise ImageArchiveError("Docker image archive member count is invalid")
    by_name: dict[str, tarfile.TarInfo] = {}
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or path.is_absolute()
            or path.as_posix() != name
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ImageArchiveError(f"unsafe Docker image archive member: {name!r}")
        if name in by_name:
            raise ImageArchiveError("Docker image archive has duplicate members")
        if member.pax_headers or member.linkname:
            raise ImageArchiveError(
                f"Docker image archive member has noncanonical metadata: {name}"
            )
        if name in _DIRECTORIES:
            if not member.isdir() or member.size != 0:
                raise ImageArchiveError(
                    f"Docker image archive directory is invalid: {name}"
                )
        elif name in _TOP_LEVEL_FILES or _BLOB_PATH.fullmatch(name):
            if not member.isfile():
                raise ImageArchiveError(
                    f"Docker image archive file has an invalid type: {name}"
                )
        else:
            raise ImageArchiveError(f"unexpected Docker image archive member: {name}")
        by_name[name] = member
    return by_name


def _member_bytes(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    label: str,
) -> bytes:
    if member.size > _MAX_JSON_BYTES:
        raise ImageArchiveError(f"{label} exceeds the JSON size limit")
    handle = archive.extractfile(member)
    if handle is None:
        raise ImageArchiveError(f"cannot read {label}")
    payload = handle.read(_MAX_JSON_BYTES + 1)
    if len(payload) != member.size:
        raise ImageArchiveError(f"{label} is truncated")
    return payload


def _member_sha256(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    label: str,
) -> str:
    handle = archive.extractfile(member)
    if handle is None:
        raise ImageArchiveError(f"cannot read {label}")
    digest = hashlib.sha256()
    observed_size = 0
    while block := handle.read(1024 * 1024):
        observed_size += len(block)
        digest.update(block)
    if observed_size != member.size:
        raise ImageArchiveError(f"{label} is truncated")
    return f"sha256:{digest.hexdigest()}"


def _validate_tar_trailer(
    handle: BufferedIOBase,
    members: list[tarfile.TarInfo],
    total_size: int,
    label: str,
) -> None:
    """Reject truncated, concatenated, or non-zero data after a tar stream."""
    if total_size < 1024 or total_size % tarfile.BLOCKSIZE:
        raise ImageArchiveError(f"{label} size is not a complete tar stream")
    content_end = max(
        (
            member.offset_data
            + ((member.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE)
            * tarfile.BLOCKSIZE
            for member in members
        ),
        default=0,
    )
    zero_size = total_size - content_end
    if zero_size < 2 * tarfile.BLOCKSIZE or zero_size > _MAX_TAR_ZERO_PADDING:
        raise ImageArchiveError(f"{label} has invalid end-of-archive padding")
    handle.seek(content_end)
    remaining = zero_size
    while remaining:
        block = handle.read(min(_BLOCK_SIZE, remaining))
        if not block or any(block):
            raise ImageArchiveError(f"{label} has non-zero or truncated trailing data")
        remaining -= len(block)


def _write_expanded(
    target: BufferedIOBase,
    payload: bytes,
    digest: Any,
    observed: int,
    label: str,
) -> int:
    observed += len(payload)
    if observed > _MAX_EXPANDED_LAYER_BYTES:
        raise ImageArchiveError(f"{label} exceeds the expanded-size limit")
    digest.update(payload)
    target.write(payload)
    return observed


def _validate_inner_layer_tar(
    handle: BufferedIOBase,
    size: int,
    label: str,
) -> None:
    handle.seek(0)
    members: list[tarfile.TarInfo] = []
    try:
        with tarfile.open(fileobj=handle, mode="r:") as layer_tar:
            # TarFile.getmembers() materializes an attacker-controlled inventory
            # without any opportunity to enforce a bound. Iterate so validation
            # stops immediately after the configured maximum is crossed.
            for member in layer_tar:
                members.append(member)
                if len(members) > _MAX_INNER_LAYER_MEMBERS:
                    raise ImageArchiveError(
                        f"{label} exceeds the layer member-count limit"
                    )
    except (OSError, tarfile.TarError) as exc:
        raise ImageArchiveError(f"{label} tar stream is invalid: {exc}") from exc
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or path.is_absolute()
            or any(part == ".." for part in path.parts)
        ):
            raise ImageArchiveError(f"{label} contains an unsafe member: {name!r}")
        if "\x00" in member.linkname:
            raise ImageArchiveError(f"{label} contains an unsafe link target")
    _validate_tar_trailer(handle, members, size, label)


def _validate_layer_blob(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    expected_digest: str,
    media_type: str,
    label: str,
) -> tuple[str, int]:
    """Stream, decompress, and bind one content-addressed layer to its diff ID."""
    if member.size > _MAX_COMPRESSED_LAYER_BYTES:
        raise ImageArchiveError(f"{label} exceeds the compressed-size limit")
    source = archive.extractfile(member)
    if source is None:
        raise ImageArchiveError(f"cannot read {label}")
    compressed_digest = hashlib.sha256()
    expanded_digest = hashlib.sha256()
    compressed_size = 0
    expanded_size = 0
    decompressor = (
        zlib.decompressobj(16 + zlib.MAX_WBITS)
        if media_type in _GZIP_LAYER_MEDIA_TYPES
        else None
    )
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as expanded:
        while block := source.read(_BLOCK_SIZE):
            compressed_size += len(block)
            if compressed_size > _MAX_COMPRESSED_LAYER_BYTES:
                raise ImageArchiveError(f"{label} exceeds the compressed-size limit")
            compressed_digest.update(block)
            if decompressor is None:
                expanded_size = _write_expanded(
                    expanded, block, expanded_digest, expanded_size, label
                )
                continue
            if decompressor.eof:
                raise ImageArchiveError(f"{label} contains a trailing gzip stream")
            pending = block
            while pending:
                output = decompressor.decompress(pending, _BLOCK_SIZE)
                pending = decompressor.unconsumed_tail
                expanded_size = _write_expanded(
                    expanded, output, expanded_digest, expanded_size, label
                )
                if decompressor.unused_data:
                    raise ImageArchiveError(
                        f"{label} contains data after the gzip stream"
                    )
                if decompressor.eof and pending:
                    raise ImageArchiveError(f"{label} contains a trailing gzip stream")
        if compressed_size != member.size:
            raise ImageArchiveError(f"{label} is truncated")
        observed_digest = f"sha256:{compressed_digest.hexdigest()}"
        if observed_digest != expected_digest:
            raise ImageArchiveError(f"{label} blob hash differs")
        if decompressor is not None:
            if not decompressor.eof or decompressor.unused_data:
                raise ImageArchiveError(
                    f"{label} gzip stream is truncated or ambiguous"
                )
            flushed = decompressor.flush()
            expanded_size = _write_expanded(
                expanded, flushed, expanded_digest, expanded_size, label
            )
        _validate_inner_layer_tar(expanded, expanded_size, label)
    return f"sha256:{expanded_digest.hexdigest()}", expanded_size


@contextmanager
def _open_archive(path: Path) -> Iterator[tarfile.TarFile]:
    try:
        with tarfile.open(path, mode="r:") as archive:
            yield archive
    except (OSError, tarfile.TarError) as exc:
        raise ImageArchiveError(f"cannot read Docker image archive: {exc}") from exc


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ImageArchiveError(f"{label} is not a lowercase SHA-256 digest")
    return value


def _descriptor(
    value: Any,
    label: str,
    *,
    allow_platform: bool,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ImageArchiveError(f"{label} is not an object")
    allowed = {"digest", "mediaType", "size", "annotations"}
    if allow_platform:
        allowed.add("platform")
    if not {"digest", "mediaType", "size"}.issubset(value) or not set(value).issubset(
        allowed
    ):
        raise ImageArchiveError(f"{label} keys are invalid")
    _digest(value.get("digest"), f"{label} digest")
    size = value.get("size")
    if type(size) is not int or size < 0:
        raise ImageArchiveError(f"{label} size is invalid")
    if not isinstance(value.get("mediaType"), str) or not value.get("mediaType"):
        raise ImageArchiveError(f"{label} media type is invalid")
    annotations = value.get("annotations")
    if annotations is not None and (
        not isinstance(annotations, Mapping)
        or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in annotations.items()
        )
    ):
        raise ImageArchiveError(f"{label} annotations are invalid")
    platform = value.get("platform")
    if platform is not None and (
        not isinstance(platform, Mapping)
        or not isinstance(platform.get("architecture"), str)
        or not isinstance(platform.get("os"), str)
    ):
        raise ImageArchiveError(f"{label} platform is invalid")
    return value


def _blob_member(
    by_name: Mapping[str, tarfile.TarInfo],
    descriptor: Mapping[str, Any],
    label: str,
) -> tuple[str, tarfile.TarInfo]:
    digest = _digest(descriptor.get("digest"), f"{label} digest")
    path = f"blobs/sha256/{digest.removeprefix('sha256:')}"
    member = by_name.get(path)
    if member is None or not member.isfile() or member.size != descriptor.get("size"):
        raise ImageArchiveError(f"{label} blob size or path differs")
    return path, member


def _expected_tag(reference: str) -> str:
    final = reference.rsplit("/", 1)[-1]
    if ":" not in final:
        return "latest"
    return final.rsplit(":", 1)[1]


def validate_docker_image_archive(
    path: Path,
    *,
    image_id: str,
    expected_reference: str | None = None,
    expected_platform: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Validate one containerd-style Docker-save OCI layout and its identities."""
    image_id = _digest(image_id, "Docker inspected image ID")
    with _open_archive(path) as archive:
        by_name = _safe_members(archive)
        for required in (*_DIRECTORIES, *_TOP_LEVEL_FILES):
            if required not in by_name:
                raise ImageArchiveError(
                    f"Docker OCI-layout archive is missing {required}"
                )

        layout = _json(
            _member_bytes(archive, by_name["oci-layout"], "OCI layout"),
            "OCI layout",
        )
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise ImageArchiveError("OCI layout version or keys are invalid")

        index = _json(
            _member_bytes(archive, by_name["index.json"], "OCI index"),
            "OCI index",
        )
        if (
            not isinstance(index, Mapping)
            or set(index) != {"manifests", "mediaType", "schemaVersion"}
            or type(index.get("schemaVersion")) is not int
            or index.get("schemaVersion") != 2
            or index.get("mediaType") != _OCI_INDEX_MEDIA_TYPE
            or not isinstance(index.get("manifests"), list)
            or len(index["manifests"]) != 1
        ):
            raise ImageArchiveError("OCI index schema or image count is invalid")
        manifest_descriptor = _descriptor(
            index["manifests"][0], "OCI index manifest", allow_platform=True
        )
        if manifest_descriptor.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE:
            raise ImageArchiveError("OCI index does not describe an image manifest")
        manifest_digest = _digest(
            manifest_descriptor.get("digest"), "OCI manifest digest"
        )
        if image_id != manifest_digest:
            raise ImageArchiveError(
                "Docker inspected image ID differs from the OCI manifest digest"
            )
        manifest_path, manifest_member = _blob_member(
            by_name, manifest_descriptor, "OCI manifest"
        )
        if _member_sha256(archive, manifest_member, "OCI manifest") != manifest_digest:
            raise ImageArchiveError("OCI manifest blob hash differs")
        manifest = _json(
            _member_bytes(archive, manifest_member, "OCI manifest"),
            "OCI manifest",
        )
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != {"config", "layers", "mediaType", "schemaVersion"}
            or type(manifest.get("schemaVersion")) is not int
            or manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE
            or not isinstance(manifest.get("layers"), list)
            or not manifest["layers"]
        ):
            raise ImageArchiveError("OCI manifest schema or layer inventory is invalid")

        config_descriptor = _descriptor(
            manifest.get("config"), "OCI config", allow_platform=False
        )
        if config_descriptor.get("mediaType") not in _OCI_CONFIG_MEDIA_TYPES:
            raise ImageArchiveError("OCI config media type is invalid")
        config_digest = _digest(config_descriptor.get("digest"), "OCI config digest")
        config_path, config_member = _blob_member(
            by_name, config_descriptor, "OCI config"
        )
        config_bytes = _member_bytes(archive, config_member, "OCI config")
        if f"sha256:{hashlib.sha256(config_bytes).hexdigest()}" != config_digest:
            raise ImageArchiveError("OCI config blob hash differs")
        config = _json(config_bytes, "OCI config")
        if not isinstance(config, Mapping):
            raise ImageArchiveError("OCI config is not an object")

        layer_paths: list[str] = []
        layer_diff_ids: list[str] = []
        total_compressed_size = 0
        total_expanded_size = 0
        for index_value, raw_layer in enumerate(manifest["layers"]):
            layer = _descriptor(
                raw_layer, f"OCI layer {index_value}", allow_platform=False
            )
            if layer.get("mediaType") not in _LAYER_MEDIA_TYPES:
                raise ImageArchiveError(
                    f"OCI layer {index_value} media type is invalid"
                )
            layer_digest = _digest(
                layer.get("digest"), f"OCI layer {index_value} digest"
            )
            layer_path, layer_member = _blob_member(
                by_name, layer, f"OCI layer {index_value}"
            )
            total_compressed_size += layer_member.size
            if total_compressed_size > _MAX_TOTAL_COMPRESSED_LAYER_BYTES:
                raise ImageArchiveError(
                    "OCI layers exceed the total compressed-size limit"
                )
            diff_id, expanded_size = _validate_layer_blob(
                archive,
                layer_member,
                expected_digest=layer_digest,
                media_type=layer["mediaType"],
                label=f"OCI layer {index_value}",
            )
            total_expanded_size += expanded_size
            if total_expanded_size > _MAX_TOTAL_EXPANDED_LAYER_BYTES:
                raise ImageArchiveError(
                    "OCI layers exceed the total expanded-size limit"
                )
            layer_paths.append(layer_path)
            layer_diff_ids.append(diff_id)
        if len(layer_paths) != len(set(layer_paths)):
            raise ImageArchiveError("OCI manifest repeats a layer blob")

        blob_paths = {
            name for name in by_name if _BLOB_PATH.fullmatch(name) is not None
        }
        expected_blobs = {manifest_path, config_path, *layer_paths}
        if blob_paths != expected_blobs:
            raise ImageArchiveError(
                "Docker OCI blob inventory has missing or extra files"
            )

        compatibility = _json(
            _member_bytes(
                archive, by_name["manifest.json"], "Docker compatibility manifest"
            ),
            "Docker compatibility manifest",
        )
        if not isinstance(compatibility, list) or len(compatibility) != 1:
            raise ImageArchiveError(
                "Docker compatibility manifest image count is invalid"
            )
        compatibility_record = compatibility[0]
        if (
            not isinstance(compatibility_record, Mapping)
            or set(compatibility_record) != {"Config", "Layers", "RepoTags"}
            or compatibility_record.get("Config") != config_path
            or compatibility_record.get("Layers") != layer_paths
        ):
            raise ImageArchiveError(
                "Docker compatibility manifest differs from the OCI descriptors"
            )
        repo_tags = compatibility_record.get("RepoTags")
        if repo_tags is not None and (
            not isinstance(repo_tags, list)
            or not repo_tags
            or len(repo_tags) != len(set(repo_tags))
            or any(not isinstance(tag, str) or not tag for tag in repo_tags)
        ):
            raise ImageArchiveError("Docker compatibility repository tags are invalid")
        if expected_reference is not None and (
            not isinstance(repo_tags, list) or expected_reference not in repo_tags
        ):
            raise ImageArchiveError(
                "Docker compatibility manifest does not contain the expected tag"
            )

        annotations = manifest_descriptor.get("annotations") or {}
        if expected_reference is not None:
            ref_name = annotations.get("org.opencontainers.image.ref.name")
            if ref_name is not None and ref_name != _expected_tag(expected_reference):
                raise ImageArchiveError("OCI reference-name annotation differs")
            image_name = annotations.get("io.containerd.image.name")
            if image_name is not None and not (
                image_name == expected_reference
                or image_name.endswith(f"/{expected_reference}")
            ):
                raise ImageArchiveError("containerd image-name annotation differs")

        architecture = config.get("architecture")
        operating_system = config.get("os")
        if not isinstance(architecture, str) or not isinstance(operating_system, str):
            raise ImageArchiveError("OCI config platform is missing")
        if expected_platform is not None and (
            architecture != expected_platform.get("architecture")
            or operating_system != expected_platform.get("os")
        ):
            raise ImageArchiveError("OCI config platform differs from the image")
        descriptor_platform = manifest_descriptor.get("platform")
        if descriptor_platform is not None and (
            descriptor_platform.get("architecture") != architecture
            or descriptor_platform.get("os") != operating_system
        ):
            raise ImageArchiveError("OCI index platform differs from the config")
        rootfs = config.get("rootfs")
        if (
            not isinstance(rootfs, Mapping)
            or set(rootfs) != {"diff_ids", "type"}
            or rootfs.get("type") != "layers"
            or not isinstance(rootfs.get("diff_ids"), list)
            or len(rootfs["diff_ids"]) != len(layer_diff_ids)
            or any(
                not isinstance(diff_id, str) or _SHA256.fullmatch(diff_id) is None
                for diff_id in rootfs["diff_ids"]
            )
        ):
            raise ImageArchiveError("OCI config rootfs differs from the layer count")
        if rootfs["diff_ids"] != layer_diff_ids:
            raise ImageArchiveError(
                "OCI config rootfs diff IDs differ from the expanded layers"
            )

        if archive.fileobj is None:
            raise ImageArchiveError("Docker image archive stream is unavailable")
        _validate_tar_trailer(
            archive.fileobj,
            list(by_name.values()),
            path.stat().st_size,
            "Docker image archive",
        )

    return {
        "archive_format": DOCKER_SAVE_OCI_LAYOUT_FORMAT,
        "image_config_digest": config_digest,
        "image_id_kind": OCI_MANIFEST_IMAGE_ID_KIND,
        "image_manifest_digest": manifest_digest,
    }
