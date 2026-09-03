#!/usr/bin/env python3
"""Record and export the exact locally verified Docker image and freeze inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from marlrefine.image_archive import (
    ImageArchiveError,
    validate_docker_image_archive,
)
from marlrefine.provenance import runtime_provenance
from marlrefine.serialization import write_json

BASE_IMAGE = (
    "python:3.13.2-slim-bookworm@"
    "sha256:6b3223eb4d93718828223966ad316909c39813dee3ee9395204940500792b740"
)
IMAGE_ARCHIVE_FILENAME = "marl-adapter-conformance-protocol-v1.docker.tar"
VERIFICATION_OUTPUT_NORMALIZATION = (
    "uv_bytecode_and_pytest_elapsed_redacted_lf_v1"
)
_UV_BYTECODE_ELAPSED = re.compile(
    r"(?m)^(?P<prefix>Bytecode compiled [0-9]+ files in )"
    r"[0-9]+(?:\.[0-9]+)?(?:ms|s)(?P<suffix>[ \t]*)$"
)
_PYTEST_ELAPSED = re.compile(
    r"(?m)(?P<prefix>\bin )\d+(?:\.\d+)?s"
    r"(?:[ \t]+\([^\r\n)]*\))?(?P<suffix>[ \t]*=*[ \t]*$)"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inspect(image: str) -> dict[str, Any]:
    result = subprocess.run(
        ("docker", "image", "inspect", image),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "docker image inspect failed")
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise RuntimeError(f"expected one image record, observed {len(records)}")
    return records[0]


def _verify(image: str) -> str:
    result = subprocess.run(
        ("docker", "run", "--rm", image),
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"container verification failed:\n{output}")
    return output


def _canonical_verification_output(output: str) -> str:
    """Remove uv/pytest elapsed clocks while preserving substantive output."""
    normalized = output.replace("\r\n", "\n").replace("\r", "\n")
    canonical, uv_replacements = _UV_BYTECODE_ELAPSED.subn(
        lambda match: (
            f"{match.group('prefix')}<elapsed>{match.group('suffix')}"
        ),
        normalized,
    )
    canonical, pytest_replacements = _PYTEST_ELAPSED.subn(
        lambda match: (
            f"{match.group('prefix')}<elapsed>s{match.group('suffix')}"
        ),
        canonical,
    )
    if uv_replacements != 1 or pytest_replacements != 1:
        raise RuntimeError(
            "verification output must contain exactly one uv bytecode and one "
            "pytest elapsed summary"
        )
    return canonical


@contextmanager
def _prepare_image_archive(
    image_id: str,
    output: Path,
    *,
    expected_platform: dict[str, Any],
) -> Iterator[tuple[Path, dict[str, Any], dict[str, str]]]:
    """Export and validate an image in a private sibling file."""
    if output.name != IMAGE_ARCHIVE_FILENAME:
        raise ValueError(
            f"image archive filename must be {IMAGE_ARCHIVE_FILENAME!r}"
        )
    if output.exists():
        raise FileExistsError(f"refusing to overwrite image archive: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        result = subprocess.run(
            ("docker", "image", "save", "--output", str(temporary), image_id),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "docker image save failed")
        try:
            content_identity = validate_docker_image_archive(
                temporary,
                image_id=image_id,
                # Saving by the immutable manifest ID legitimately yields
                # RepoTags=null. The separately recorded tag is informational.
                expected_reference=None,
                expected_platform=expected_platform,
            )
        except ImageArchiveError as exc:
            raise RuntimeError(str(exc)) from exc
        archive = {
            "format": content_identity["archive_format"],
            "filename": IMAGE_ARCHIVE_FILENAME,
            "sha256": _sha256(temporary),
            "size_bytes": temporary.stat().st_size,
        }
        yield temporary, archive, content_identity
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _rollback_published_link(temporary: Path, output: Path) -> None:
    """Remove only the output hard link that points at ``temporary``."""
    try:
        temporary_stat = temporary.stat()
        output_stat = output.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if not os.path.samestat(temporary_stat, output_stat):
        raise RuntimeError(f"refusing to remove replaced output: {output}")
    output.unlink()
    _fsync_directory(output.parent)


def _publish_exclusive(temporary: Path, output: Path) -> None:
    """Publish a fully flushed sibling file without a check/use overwrite race."""
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    try:
        os.link(temporary, output)
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite output: {output}") from exc
    try:
        _fsync_directory(output.parent)
    except BaseException:
        _rollback_published_link(temporary, output)
        raise


def _container_provenance(image: str) -> dict[str, Any]:
    marker = "MARLREFINE_CONTAINER_PROVENANCE="
    code = (
        "import json; "
        "from marlrefine.provenance import runtime_provenance; "
        f"print({marker!r} + json.dumps(runtime_provenance(), sort_keys=True))"
    )
    result = subprocess.run(
        ("docker", "run", "--rm", image, "uv", "run", "python", "-c", code),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "container provenance inspection failed"
        )
    lines = tuple(
        line.removeprefix(marker)
        for line in result.stdout.splitlines()
        if line.startswith(marker)
    )
    if len(lines) != 1:
        raise RuntimeError("container provenance marker is missing or ambiguous")
    payload = json.loads(lines[0])
    if not isinstance(payload, dict):
        raise RuntimeError("container provenance is not a JSON object")
    return payload


@contextmanager
def _prepared_image_identity(
    root: Path,
    image: str,
    archive_output: Path,
) -> Iterator[tuple[dict[str, Any], Path]]:
    """Prepare complete identity bytes and an unpublished validated archive."""
    inspected = _inspect(image)
    image_id = inspected.get("Id")
    if not isinstance(image_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_id
    ):
        raise RuntimeError(f"unexpected Docker image ID: {image_id!r}")
    verification_output = _verify(image_id)
    container_provenance = _container_provenance(image_id)
    host_provenance = runtime_provenance()
    manifest_path = root / "manifests/study_v1_draft.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_environment = manifest.get("environment")
    if not isinstance(manifest_environment, dict):
        raise RuntimeError("study manifest environment is missing")
    for field in ("source_tree_sha256", "uv_lock_sha256"):
        if container_provenance.get(field) != host_provenance.get(field):
            raise RuntimeError(f"container {field} differs from the freeze host")
        if container_provenance.get(field) != manifest_environment.get(field):
            raise RuntimeError(f"container {field} differs from the study manifest")
    frozen_python = {
        key: manifest_environment.get("python", {}).get(key)
        for key in ("implementation", "version")
    }
    container_python = {
        key: container_provenance.get("python", {}).get(key)
        for key in ("implementation", "version")
    }
    if frozen_python != container_python:
        raise RuntimeError("container Python identity differs from the study manifest")
    for field in ("packages", "installed_distribution_sha256"):
        if container_provenance.get(field) != manifest_environment.get(field):
            raise RuntimeError(f"container {field} differs from the study manifest")
    platform = {
        "architecture": inspected.get("Architecture"),
        "os": inspected.get("Os"),
    }
    verification_output_sha256 = hashlib.sha256(
        _canonical_verification_output(verification_output).encode("utf-8")
    ).hexdigest()
    dockerfile_sha256 = _sha256(root / "Dockerfile")
    manifest_sha256 = _sha256(manifest_path)
    with _prepare_image_archive(
        image_id,
        archive_output,
        expected_platform=platform,
    ) as (archive_temporary, image_archive, archive_content):
        payload = {
            "schema_version": 2,
            "image_reference": image,
            "image_id": image_id,
            "image_id_kind": archive_content["image_id_kind"],
            "image_manifest_digest": archive_content["image_manifest_digest"],
            "image_config_digest": archive_content["image_config_digest"],
            "image_archive": image_archive,
            "repo_digests": tuple(sorted(inspected.get("RepoDigests") or ())),
            "platform": platform,
            "base_image": BASE_IMAGE,
            "dockerfile_sha256": dockerfile_sha256,
            "source_tree_sha256": host_provenance["source_tree_sha256"],
            "container_runtime": {
                "installed_distribution_sha256": container_provenance[
                    "installed_distribution_sha256"
                ],
                "packages": container_provenance["packages"],
                "python": container_provenance["python"],
                "source_tree_sha256": container_provenance["source_tree_sha256"],
                "uv_lock_sha256": container_provenance["uv_lock_sha256"],
            },
            "study_manifest": {
                "path": "manifests/study_v1_draft.json",
                "sha256": manifest_sha256,
            },
            "verification_command": "docker run --rm " + image_id,
            "verification_output_sha256": verification_output_sha256,
            "verification_output_normalization": VERIFICATION_OUTPUT_NORMALIZATION,
            "verification_status": "tests_passed",
        }
        yield payload, archive_temporary


def image_identity(root: Path, image: str, archive_output: Path) -> dict[str, Any]:
    """Publish an exact archive after constructing its complete identity payload."""
    with _prepared_image_identity(root, image, archive_output) as (
        payload,
        archive_temporary,
    ):
        _publish_exclusive(archive_temporary, archive_output)
        return payload


def _publish_identity_outputs(
    root: Path,
    image: str,
    archive_output: Path,
    identity_output: Path,
) -> dict[str, Any]:
    """No-clobber publish the archive and identity as one recoverable operation."""
    if archive_output.absolute() == identity_output.absolute():
        raise ValueError("archive and identity outputs must be different paths")
    identity_output.parent.mkdir(parents=True, exist_ok=True)
    if identity_output.exists() or identity_output.is_symlink():
        raise FileExistsError(f"refusing to overwrite output: {identity_output}")

    with _prepared_image_identity(root, image, archive_output) as (
        payload,
        archive_temporary,
    ):
        with tempfile.NamedTemporaryFile(
            dir=identity_output.parent,
            prefix=f".{identity_output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            identity_temporary = Path(handle.name)
        try:
            # Canonicalization and durable temporary-file creation are complete
            # before either public output name appears.
            write_json(identity_temporary, payload)
            _publish_exclusive(archive_temporary, archive_output)
            try:
                _publish_exclusive(identity_temporary, identity_output)
            except BaseException as exc:
                try:
                    _rollback_published_link(archive_temporary, archive_output)
                except BaseException as rollback_exc:
                    exc.add_note(f"archive rollback also failed: {rollback_exc}")
                raise
        finally:
            identity_temporary.unlink(missing_ok=True)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image",
        default="marl-adapter-conformance:protocol-v1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("container/IMAGE_IDENTITY.json"),
    )
    parser.add_argument(
        "--archive-output",
        type=Path,
        default=Path(
            "dist/private-execution-image/"
            "marl-adapter-conformance-protocol-v1.docker.tar"
        ),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    payload = _publish_identity_outputs(
        root,
        args.image,
        args.archive_output.resolve(),
        args.output.resolve(),
    )
    print(f"wrote {args.output}: {payload['image_id']}")


if __name__ == "__main__":
    main()
