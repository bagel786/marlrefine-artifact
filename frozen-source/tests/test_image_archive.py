from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

import marlrefine.image_archive as image_archive_module
from marlrefine.image_archive import (
    ImageArchiveError,
    validate_docker_image_archive,
)


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _layer_tar(member_count: int = 1) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for index in range(member_count):
            payload = f"fixture {index}\n".encode()
            member = tarfile.TarInfo(f"fixture-{index}.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def _write_oci_archive(
    path: Path,
    *,
    layer_tar: bytes | None = None,
    diff_id: str | None = None,
    compatibility_layers: Any = None,
    extra: tuple[tarfile.TarInfo, bytes] | None = None,
) -> tuple[str, str]:
    expanded = _layer_tar() if layer_tar is None else layer_tar
    compressed = gzip.compress(expanded, mtime=0)
    layer_hex = hashlib.sha256(compressed).hexdigest()
    layer_path = f"blobs/sha256/{layer_hex}"
    config = _json(
        {
            "architecture": "arm64",
            "config": {},
            "os": "linux",
            "rootfs": {
                "diff_ids": [
                    diff_id
                    or f"sha256:{hashlib.sha256(expanded).hexdigest()}"
                ],
                "type": "layers",
            },
        }
    )
    config_hex = hashlib.sha256(config).hexdigest()
    config_path = f"blobs/sha256/{config_hex}"
    manifest = _json(
        {
            "config": {
                "digest": f"sha256:{config_hex}",
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": len(config),
            },
            "layers": [
                {
                    "digest": f"sha256:{layer_hex}",
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "size": len(compressed),
                }
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    manifest_hex = hashlib.sha256(manifest).hexdigest()
    manifest_path = f"blobs/sha256/{manifest_hex}"
    index = _json(
        {
            "manifests": [
                {
                    "digest": f"sha256:{manifest_hex}",
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": "arm64", "os": "linux"},
                    "size": len(manifest),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    compatibility = _json(
        [
            {
                "Config": config_path,
                "Layers": (
                    [layer_path]
                    if compatibility_layers is None
                    else compatibility_layers
                ),
                "RepoTags": None,
            }
        ]
    )
    with tarfile.open(path, mode="w") as archive:
        for directory in ("blobs", "blobs/sha256"):
            member = tarfile.TarInfo(directory)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
        for name, payload in (
            (config_path, config),
            (layer_path, compressed),
            (manifest_path, manifest),
            ("index.json", index),
            ("manifest.json", compatibility),
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        if extra is not None:
            member, payload = extra
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return f"sha256:{manifest_hex}", f"sha256:{config_hex}"


def test_real_oci_layout_semantics_bind_manifest_config_and_diff_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.docker.tar"
    image_id, config_digest = _write_oci_archive(path)

    observed = validate_docker_image_archive(
        path,
        image_id=image_id,
        expected_platform={"architecture": "arm64", "os": "linux"},
    )

    assert observed == {
        "archive_format": "docker_image_save_oci_layout_tar_v1",
        "image_config_digest": config_digest,
        "image_id_kind": "oci_manifest_digest",
        "image_manifest_digest": image_id,
    }


def test_content_addressed_non_tar_layer_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "not-tar.docker.tar"
    image_id, _ = _write_oci_archive(path, layer_tar=b"not even a tar layer")
    with pytest.raises(ImageArchiveError, match="not a tar|tar stream"):
        validate_docker_image_archive(path, image_id=image_id)


def test_wrong_expanded_layer_diff_id_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "wrong-diff.docker.tar"
    image_id, _ = _write_oci_archive(path, diff_id=f"sha256:{'0' * 64}")
    with pytest.raises(ImageArchiveError, match="diff IDs"):
        validate_docker_image_archive(path, image_id=image_id)


def test_inner_layer_member_inventory_is_hard_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "too-many-inner-members.docker.tar"
    image_id, _ = _write_oci_archive(path, layer_tar=_layer_tar(member_count=2))
    monkeypatch.setattr(image_archive_module, "_MAX_INNER_LAYER_MEMBERS", 1)

    with pytest.raises(ImageArchiveError, match="member-count limit"):
        validate_docker_image_archive(path, image_id=image_id)


def test_compatibility_layers_type_confusion_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "layers-object.docker.tar"
    image_id, _ = _write_oci_archive(path, compatibility_layers={"layer": "blob"})
    with pytest.raises(ImageArchiveError, match="compatibility manifest"):
        validate_docker_image_archive(path, image_id=image_id)


@pytest.mark.parametrize("name", ["../escape", "/absolute"])
def test_outer_traversal_member_is_rejected(tmp_path: Path, name: str) -> None:
    path = tmp_path / "traversal.docker.tar"
    image_id, _ = _write_oci_archive(
        path, extra=(tarfile.TarInfo(name), b"attacker")
    )
    with pytest.raises(ImageArchiveError, match="unsafe|unexpected"):
        validate_docker_image_archive(path, image_id=image_id)


def test_outer_link_and_extra_blob_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "link.docker.tar"
    link = tarfile.TarInfo("attacker")
    link.type = tarfile.SYMTYPE
    link.linkname = "manifest.json"
    image_id, _ = _write_oci_archive(path, extra=(link, b""))
    with pytest.raises(
        ImageArchiveError, match="unexpected|invalid type|noncanonical"
    ):
        validate_docker_image_archive(path, image_id=image_id)

    path = tmp_path / "extra-blob.docker.tar"
    extra = tarfile.TarInfo(f"blobs/sha256/{'f' * 64}")
    image_id, _ = _write_oci_archive(path, extra=(extra, b"attacker"))
    with pytest.raises(ImageArchiveError, match="missing or extra"):
        validate_docker_image_archive(path, image_id=image_id)


def test_wrong_manifest_image_id_and_trailing_stream_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-id.docker.tar"
    image_id, _ = _write_oci_archive(path)
    with pytest.raises(ImageArchiveError, match="manifest digest"):
        validate_docker_image_archive(path, image_id=f"sha256:{'0' * 64}")

    with path.open("ab") as handle:
        handle.write(b"attacker trailing stream")
    with pytest.raises(ImageArchiveError, match="complete tar stream|trailing"):
        validate_docker_image_archive(path, image_id=image_id)
