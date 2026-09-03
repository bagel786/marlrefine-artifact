from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import tarfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from marlrefine.archive import protocol_freeze_identity, two_commit_freeze_identity
from marlrefine.mutations import (
    CANDIDATE_POOL,
    MUTANTS_PER_FAMILY,
    MUTATION_FAMILIES,
    POOL_PER_FAMILY,
)

_DEPOSIT_SCRIPT = (
    Path(__file__).resolve().parents[1] / "deposit/build_protocol_bundle.py"
)
_DEPOSIT_SPEC = importlib.util.spec_from_file_location(
    "marlrefine_test_deposit_builder", _DEPOSIT_SCRIPT
)
assert _DEPOSIT_SPEC is not None and _DEPOSIT_SPEC.loader is not None
_DEPOSIT_MODULE = importlib.util.module_from_spec(_DEPOSIT_SPEC)
_DEPOSIT_SPEC.loader.exec_module(_DEPOSIT_MODULE)
FROZEN_GENERATED_PATHS = _DEPOSIT_MODULE.FROZEN_GENERATED_PATHS
_iter_source_paths = _DEPOSIT_MODULE._iter_source_paths
_reject_machine_paths = _DEPOSIT_MODULE._reject_machine_paths
_validate_contract_evidence = _DEPOSIT_MODULE._validate_contract_evidence
_validate_generated_evidence_commit = (
    _DEPOSIT_MODULE._validate_generated_evidence_commit
)
_validate_mutation_manifest = _DEPOSIT_MODULE._validate_mutation_manifest
_validate_image_identity = _DEPOSIT_MODULE._validate_image_identity
_validate_zenodo_metadata = _DEPOSIT_MODULE._validate_zenodo_metadata
_write_deterministic_archive = _DEPOSIT_MODULE._write_deterministic_archive

_IMAGE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "container/write_image_identity.py"
)
_IMAGE_SPEC = importlib.util.spec_from_file_location(
    "marlrefine_test_image_identity", _IMAGE_SCRIPT
)
assert _IMAGE_SPEC is not None and _IMAGE_SPEC.loader is not None
_IMAGE_MODULE = importlib.util.module_from_spec(_IMAGE_SPEC)
_IMAGE_SPEC.loader.exec_module(_IMAGE_MODULE)


def _write_oci_image_archive(path: Path) -> tuple[str, str]:
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as layer_archive:
        payload = b"frozen image fixture\n"
        member = tarfile.TarInfo("fixture.txt")
        member.size = len(payload)
        layer_archive.addfile(member, io.BytesIO(payload))
    layer = layer_buffer.getvalue()
    layer_digest = hashlib.sha256(layer).hexdigest()
    config = json.dumps(
        {
            "architecture": "amd64",
            "config": {},
            "os": "linux",
            "rootfs": {
                "diff_ids": [f"sha256:{layer_digest}"],
                "type": "layers",
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    config_digest = hashlib.sha256(config).hexdigest()
    config_path = f"blobs/sha256/{config_digest}"
    layer_path = f"blobs/sha256/{layer_digest}"
    manifest = json.dumps(
        {
            "config": {
                "digest": f"sha256:{config_digest}",
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": len(config),
            },
            "layers": [
                {
                    "digest": f"sha256:{layer_digest}",
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "size": len(layer),
                }
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    manifest_path = f"blobs/sha256/{manifest_digest}"
    index = json.dumps(
        {
            "manifests": [
                {
                    "digest": f"sha256:{manifest_digest}",
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": "amd64", "os": "linux"},
                    "size": len(manifest),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    compatibility = json.dumps(
        [
            {
                "Config": config_path,
                "Layers": [layer_path],
                "RepoTags": None,
            }
        ],
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    with tarfile.open(path, mode="w") as archive:
        for directory in ("blobs", "blobs/sha256"):
            member = tarfile.TarInfo(directory)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
        for name, payload in (
            (config_path, config),
            (layer_path, layer),
            (manifest_path, manifest),
            ("index.json", index),
            ("manifest.json", compatibility),
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return f"sha256:{manifest_digest}", f"sha256:{config_digest}"


def test_zenodo_checklist_declares_both_file_scoped_licenses() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = _validate_zenodo_metadata(root / "deposit/zenodo_metadata.json")

    assert {entry["id"] for entry in metadata["licenses"]} == {
        "apache-2.0",
        "cc-by-4.0",
    }
    assert (root / "LICENSE").is_file()
    assert (root / "LICENSE-docs-data").is_file()
    assert "complete frozen executable/protocol source" in metadata["notes"]
    assert "complete frozen source is included" not in metadata["notes"]
    assert metadata["creators"] == [
        {
            "affiliation": "Independent Researcher",
            "name": "Baig, Safiullah",
            "orcid": "0009-0008-5547-6088",
        }
    ]
    serialized = json.dumps(metadata, sort_keys=True)
    assert "@" not in serialized


def test_contract_evidence_is_public_release_ready() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "docs/contract_evidence.md"

    _validate_contract_evidence(path)
    text = path.read_text(encoding="utf-8").casefold()
    assert "status: public protocol evidence ledger" in text
    tagged_blob_links = re.findall(r"https://github\.com/[^)]+/blob/[^)]+", text)
    assert tagged_blob_links
    assert all(re.search(r"#l\d+-l\d+$", link) for link in tagged_blob_links)


@pytest.mark.parametrize(
    "stale_text",
    (
        "Status: private pre-freeze working document",
        "Status: private working document",
        "Before freeze, replace broad file links",
    ),
)
def test_contract_evidence_rejects_stale_release_wording(
    tmp_path: Path, stale_text: str
) -> None:
    path = tmp_path / "contract_evidence.md"
    path.write_text(stale_text, encoding="utf-8")

    with pytest.raises(RuntimeError, match="stale private/pre-freeze wording"):
        _validate_contract_evidence(path)


def test_generated_evidence_commit_requires_exactly_eight_additions() -> None:
    status = "\n".join(f"A\t{path}" for path in sorted(FROZEN_GENERATED_PATHS))

    assert _validate_generated_evidence_commit(status) == tuple(
        sorted(FROZEN_GENERATED_PATHS)
    )


@pytest.mark.parametrize("status_code", ("D", "M", "T"))
def test_generated_evidence_commit_rejects_non_addition_statuses(
    status_code: str,
) -> None:
    paths = sorted(FROZEN_GENERATED_PATHS)
    records = [f"A\t{path}" for path in paths]
    records[0] = f"{status_code}\t{paths[0]}"

    with pytest.raises(RuntimeError, match="only added files"):
        _validate_generated_evidence_commit("\n".join(records))


def test_generated_evidence_commit_rejects_missing_or_extra_paths() -> None:
    paths = sorted(FROZEN_GENERATED_PATHS)
    missing = "\n".join(f"A\t{path}" for path in paths[1:])
    extra = "\n".join(
        [*(f"A\t{path}" for path in paths), "A\tdocs/protocol.md"]
    )

    with pytest.raises(RuntimeError, match="exactly the eight"):
        _validate_generated_evidence_commit(missing)
    with pytest.raises(RuntimeError, match="exactly the eight"):
        _validate_generated_evidence_commit(extra)


def test_protocol_identity_is_separate_and_binds_completed_bundle(tmp_path) -> None:
    bundle = tmp_path / "protocol.tar.gz"
    bundle.write_bytes(b"deterministic protocol fixture")
    identity = protocol_freeze_identity(
        manifest_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        uv_lock_sha256="c" * 64,
        bundle_filename=bundle.name,
        bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
    )
    assert identity == {
        "artifact_type": "marlrefine_protocol_freeze_identity",
        "manifest_sha256": "a" * 64,
        "protocol_bundle": {
            "filename": "protocol.tar.gz",
            "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        },
        "schema_version": 1,
        "source_tree_sha256": "b" * 64,
        "uv_lock_sha256": "c" * 64,
    }


def test_two_commit_identity_allows_only_generated_evidence() -> None:
    allowed = frozenset(
        {
            "artifacts/discovery_controls.json",
            "manifests/study_v1_draft.json",
        }
    )
    identity = two_commit_freeze_identity(
        {"git_revision": "source-a", "git_dirty": False},
        source_parent_revision="source-a",
        archive_revision="archive-b",
        changed_paths=(
            "manifests/study_v1_draft.json",
            "artifacts/discovery_controls.json",
        ),
        allowed_generated_paths=allowed,
        required_manifest_path="manifests/study_v1_draft.json",
    )
    assert identity == {
        "git_identity_model": "two_commit_nonrecursive_v1",
        "source_git_revision": "source-a",
        "archive_git_revision": "archive-b",
        "generated_evidence_paths": (
            "artifacts/discovery_controls.json",
            "manifests/study_v1_draft.json",
        ),
    }

    with pytest.raises(ValueError, match="source-controlled paths"):
        two_commit_freeze_identity(
            {"git_revision": "source-a", "git_dirty": False},
            source_parent_revision="source-a",
            archive_revision="archive-b",
            changed_paths=("README.md", "manifests/study_v1_draft.json"),
            allowed_generated_paths=allowed,
            required_manifest_path="manifests/study_v1_draft.json",
        )


def test_bundle_source_allowlist_excludes_entire_private_manuscript_tree() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = {path.relative_to(root).as_posix() for path in _iter_source_paths(root)}

    assert not any(path.startswith("paper/") for path in paths)
    assert "marlrefine_research_dossier.md" not in paths
    assert "docs/journal_fit_novelty_audit.md" not in paths
    assert "docs/paper_outline.md" not in paths
    assert "docs/related_work.md" not in paths
    assert "docs/protocol.md" in paths
    assert "docs/frozen_analysis.md" in paths
    assert "manifests/README.md" in paths
    assert "manifests/mutation_v1.json" in paths
    assert "container/IMAGE_IDENTITY.json" in paths
    assert not any(path.endswith(".docker.tar") for path in paths)


def test_bundle_validates_exact_mutation_manifest_binding(tmp_path: Path) -> None:
    mutation_path = tmp_path / "manifests/mutation_v1.json"
    mutation_path.parent.mkdir()
    environment = {
        "python": {"implementation": "CPython", "version": "3.13.2"},
        "packages": {"marlrefine": "0.1.0"},
        "installed_distribution_sha256": {"shimmy": "a" * 64},
        "uv_lock_sha256": "b" * 64,
        "source_tree_sha256": "c" * 64,
        "git_revision": "d" * 40,
        "git_dirty": False,
    }
    mutation_payload = _DEPOSIT_MODULE.build_mutation_manifest(
        manifest_status="draft_not_timestamp_archived"
    )
    mutation_payload["manifest_status"] = "frozen_pending_archive"
    mutation_payload["environment"] = environment
    mutation_path.write_text(json.dumps(mutation_payload), encoding="utf-8")
    digest = hashlib.sha256(mutation_path.read_bytes()).hexdigest()
    study_manifest = {
        "environment": environment,
        "mutation_evaluation": {
            "required_for_primary_study": True,
            "mutation_manifest_path": "manifests/mutation_v1.json",
            "mutation_manifest_sha256": digest,
            "candidate_pool_count": len(CANDIDATE_POOL),
            "candidate_pool_per_family": POOL_PER_FAMILY,
            "family_count": len(MUTATION_FAMILIES),
            "families": list(MUTATION_FAMILIES),
            "required_eligible_per_family": MUTANTS_PER_FAMILY,
            "required_selected_count": len(MUTATION_FAMILIES)
            * MUTANTS_PER_FAMILY,
            "selection_rule": mutation_payload["selection"]["replacement_rule"],
            "prearchive_activity": {
                "candidate_or_control_outcomes_executed": 0
            },
        },
    }

    assert (
        _validate_mutation_manifest(
            tmp_path,
            manifest=study_manifest,
            source_tree_sha256="c" * 64,
            uv_lock_sha256="b" * 64,
        )
        == digest
    )

    study_manifest["mutation_evaluation"]["mutation_manifest_sha256"] = "e" * 64
    with pytest.raises(RuntimeError, match="does not bind"):
        _validate_mutation_manifest(
            tmp_path,
            manifest=study_manifest,
            source_tree_sha256="c" * 64,
            uv_lock_sha256="b" * 64,
        )


def test_deterministic_archive_normalizes_metadata_and_modes(tmp_path: Path) -> None:
    staging = tmp_path / "protocol"
    nested = staging / "nested"
    nested.mkdir(parents=True)
    regular = nested / "record.json"
    executable = staging / "runner.py"
    regular.write_text("{}\n", encoding="utf-8")
    executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    regular.chmod(0o600)
    executable.chmod(0o700)

    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    _write_deterministic_archive(staging, first)
    _write_deterministic_archive(staging, second)

    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
    assert members["protocol"].mode == 0o755
    assert members["protocol/nested"].mode == 0o755
    assert members["protocol/nested/record.json"].mode == 0o644
    assert members["protocol/runner.py"].mode == 0o755
    assert all(
        member.uid == member.gid == member.mtime == 0 for member in members.values()
    )


def test_generated_artifacts_reject_machine_specific_paths(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text(
        '{"python":"/Users/example/project/.venv/bin/python"}\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="machine-specific absolute path"):
        _reject_machine_paths(artifact, "synthetic artifact")


def test_image_identity_binds_container_bytes_to_manifest_not_host(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "manifests").mkdir()
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    frozen_environment = {
        "python": {
            "implementation": "CPython",
            "version": "3.13.2",
            "executable_name": "python3",
        },
        "packages": {"shimmy": "2.0.1"},
        "installed_distribution_sha256": {"shimmy": "a" * 64},
        "source_tree_sha256": "b" * 64,
        "uv_lock_sha256": "c" * 64,
    }
    manifest_path = tmp_path / "manifests/study_v1_draft.json"
    manifest_path.write_text(
        json.dumps({"environment": frozen_environment}),
        encoding="utf-8",
    )
    container_provenance = dict(frozen_environment)
    host_provenance = {
        "source_tree_sha256": "b" * 64,
        "uv_lock_sha256": "c" * 64,
        "packages": {"shimmy": "host-build-is-irrelevant"},
        "installed_distribution_sha256": {"shimmy": "d" * 64},
    }
    immutable_id = "sha256:" + "e" * 64
    observed: dict[str, str] = {}

    def verify(image: str) -> str:
        observed["verify"] = image
        return (
            "Bytecode compiled 2943 files in 138ms\n"
            "100 passed in 12.34s (0:00:12)\n"
        )

    monkeypatch.setattr(_IMAGE_MODULE, "_verify", verify)
    monkeypatch.setattr(
        _IMAGE_MODULE,
        "_inspect",
        lambda image: {
            "Id": immutable_id,
            "RepoDigests": [],
            "Architecture": "amd64",
            "Os": "linux",
        },
    )
    def container_identity(image: str) -> dict[str, object]:
        observed["provenance"] = image
        return container_provenance

    monkeypatch.setattr(_IMAGE_MODULE, "_container_provenance", container_identity)
    monkeypatch.setattr(
        _IMAGE_MODULE,
        "runtime_provenance",
        lambda: host_provenance,
    )
    archive_identity = {
        "format": "docker_image_save_oci_layout_tar_v1",
        "filename": "marl-adapter-conformance-protocol-v1.docker.tar",
        "sha256": "f" * 64,
        "size_bytes": 123,
    }
    archive_content_identity = {
        "archive_format": "docker_image_save_oci_layout_tar_v1",
        "image_config_digest": "sha256:" + "d" * 64,
        "image_id_kind": "oci_manifest_digest",
        "image_manifest_digest": immutable_id,
    }

    @contextmanager
    def prepare_image_archive(
        image: str,
        output: Path,
        *,
        expected_platform: dict[str, object],
    ):
        observed["save"] = image
        observed["archive_output"] = str(output)
        observed["archive_platform"] = json.dumps(
            expected_platform, sort_keys=True
        )
        temporary = output.with_name(f".{output.name}.test-tmp")
        temporary.write_bytes(b"synthetic image archive")
        try:
            yield temporary, archive_identity, archive_content_identity
        finally:
            temporary.unlink(missing_ok=True)

    monkeypatch.setattr(
        _IMAGE_MODULE, "_prepare_image_archive", prepare_image_archive
    )

    archive_output = (
        tmp_path / "marl-adapter-conformance-protocol-v1.docker.tar"
    )
    identity = _IMAGE_MODULE.image_identity(
        tmp_path, "synthetic:final-b", archive_output
    )

    assert identity["schema_version"] == 2
    assert identity["image_archive"] == archive_identity
    assert identity["image_id_kind"] == "oci_manifest_digest"
    assert identity["image_manifest_digest"] == immutable_id
    assert identity["image_config_digest"] == "sha256:" + "d" * 64
    assert identity["verification_command"] == f"docker run --rm {immutable_id}"
    assert observed == {
        "archive_output": str(archive_output),
        "archive_platform": '{"architecture": "amd64", "os": "linux"}',
        "provenance": immutable_id,
        "save": immutable_id,
        "verify": immutable_id,
    }
    assert identity["container_runtime"]["installed_distribution_sha256"] == {
        "shimmy": "a" * 64
    }
    assert (
        identity["study_manifest"]["sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )


def test_verification_output_hash_ignores_only_elapsed_clock() -> None:
    first = (
        "Bytecode compiled 2943 files in 138ms\n"
        "................................ [100%]\n"
        "100 passed in 1.23s (0:00:01)\n"
    )
    second = (
        "Bytecode compiled 2943 files in 42.8s\r\n"
        "................................ [100%]\r\n"
        "100 passed in 99.87s (0:01:39)\r\n"
    )

    assert _IMAGE_MODULE._canonical_verification_output(first) == (
        _IMAGE_MODULE._canonical_verification_output(second)
    )
    assert "100 passed" in _IMAGE_MODULE._canonical_verification_output(first)
    assert _IMAGE_MODULE._canonical_verification_output(
        first.replace("100 passed", "99 passed")
    ) != _IMAGE_MODULE._canonical_verification_output(first)
    assert _IMAGE_MODULE._canonical_verification_output(
        first.replace("2943 files", "2942 files")
    ) != _IMAGE_MODULE._canonical_verification_output(first)
    with pytest.raises(RuntimeError, match="exactly one"):
        _IMAGE_MODULE._canonical_verification_output("100 passed\n")


def test_docker_archive_validation_binds_oci_manifest_and_config_digests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.docker.tar"
    image_id, config_digest = _write_oci_image_archive(path)

    observed = _IMAGE_MODULE.validate_docker_image_archive(
        path,
        image_id=image_id,
        expected_platform={"architecture": "amd64", "os": "linux"},
    )
    assert observed == {
        "archive_format": "docker_image_save_oci_layout_tar_v1",
        "image_config_digest": config_digest,
        "image_id_kind": "oci_manifest_digest",
        "image_manifest_digest": image_id,
    }
    with pytest.raises(_IMAGE_MODULE.ImageArchiveError, match="manifest digest"):
        _IMAGE_MODULE.validate_docker_image_archive(
            path, image_id=f"sha256:{'0' * 64}"
        )


def _protocol_image_identity_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], dict[str, object]]:
    (tmp_path / "container").mkdir()
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    archive = tmp_path / _DEPOSIT_MODULE.IMAGE_ARCHIVE_FILENAME
    archive.write_bytes(b"validated archive fixture")
    source_sha = "a" * 64
    lock_sha = "b" * 64
    manifest_sha = "c" * 64
    image_id = "sha256:" + "d" * 64
    config_digest = "sha256:" + "e" * 64
    environment = {
        "installed_distribution_sha256": {"shimmy": "f" * 64},
        "packages": {"shimmy": "2.0.1"},
        "python": {
            "executable_name": "python3",
            "implementation": "CPython",
            "version": "3.13.2",
        },
        "source_tree_sha256": source_sha,
        "uv_lock_sha256": lock_sha,
    }
    identity: dict[str, object] = {
        "base_image": "python@sha256:" + "1" * 64,
        "container_runtime": dict(environment),
        "dockerfile_sha256": hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
        "image_archive": {
            "filename": archive.name,
            "format": _DEPOSIT_MODULE.IMAGE_ARCHIVE_FORMAT,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size_bytes": archive.stat().st_size,
        },
        "image_config_digest": config_digest,
        "image_id": image_id,
        "image_id_kind": _DEPOSIT_MODULE.OCI_MANIFEST_IMAGE_ID_KIND,
        "image_manifest_digest": image_id,
        "image_reference": "marl-adapter-conformance:protocol-v1",
        "platform": {"architecture": "amd64", "os": "linux"},
        "repo_digests": [],
        "schema_version": 2,
        "source_tree_sha256": source_sha,
        "study_manifest": {
            "path": "manifests/study_v1_draft.json",
            "sha256": manifest_sha,
        },
        "verification_command": f"docker run --rm {image_id}",
        "verification_output_normalization": (
            _DEPOSIT_MODULE.VERIFICATION_OUTPUT_NORMALIZATION
        ),
        "verification_output_sha256": "2" * 64,
        "verification_status": "tests_passed",
    }
    monkeypatch.setattr(
        _DEPOSIT_MODULE,
        "validate_docker_image_archive",
        lambda *args, **kwargs: {
            "archive_format": _DEPOSIT_MODULE.IMAGE_ARCHIVE_FORMAT,
            "image_config_digest": config_digest,
            "image_id_kind": _DEPOSIT_MODULE.OCI_MANIFEST_IMAGE_ID_KIND,
            "image_manifest_digest": image_id,
        },
    )
    arguments: dict[str, object] = {
        "root": tmp_path,
        "image_archive_path": archive,
        "manifest": {"environment": environment},
        "manifest_sha256": manifest_sha,
        "source_tree_sha256": source_sha,
        "uv_lock_sha256": lock_sha,
    }
    return identity, arguments


def test_protocol_image_identity_accepts_reviewer_compatible_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity, arguments = _protocol_image_identity_fixture(tmp_path, monkeypatch)
    (tmp_path / "container/IMAGE_IDENTITY.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )

    assert _validate_image_identity(**arguments) == identity["image_archive"]


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("base", "base image"),
        ("reference", "image reference"),
        ("platform", "platform identity"),
        ("runtime", "runtime keys"),
        ("python", "Python identity"),
    ),
)
def test_protocol_image_identity_rejects_nested_schema_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
) -> None:
    identity, arguments = _protocol_image_identity_fixture(tmp_path, monkeypatch)
    if case == "base":
        identity["base_image"] = "python:latest"
    elif case == "reference":
        identity["image_reference"] = " "
    elif case == "platform":
        identity["platform"] = {"architecture": "amd64"}
    elif case == "runtime":
        runtime = dict(identity["container_runtime"])
        runtime["unexpected"] = True
        identity["container_runtime"] = runtime
    else:
        runtime = dict(identity["container_runtime"])
        runtime["python"] = "CPython"
        identity["container_runtime"] = runtime
    (tmp_path / "container/IMAGE_IDENTITY.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match=message):
        _validate_image_identity(**arguments)


def test_identity_serialization_failure_publishes_neither_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "image.docker.tar"
    identity = tmp_path / "IMAGE_IDENTITY.json"

    @contextmanager
    def prepared(*args, **kwargs):
        temporary = tmp_path / ".prepared-image.tar"
        temporary.write_bytes(b"prepared image")
        try:
            yield {"not_json": object()}, temporary
        finally:
            temporary.unlink(missing_ok=True)

    monkeypatch.setattr(_IMAGE_MODULE, "_prepared_image_identity", prepared)

    with pytest.raises(TypeError, match="unsupported artifact"):
        _IMAGE_MODULE._publish_identity_outputs(
            tmp_path, "synthetic:image", archive, identity
        )
    assert not archive.exists()
    assert not identity.exists()


def test_identity_second_publish_failure_rolls_back_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "image.docker.tar"
    identity = tmp_path / "IMAGE_IDENTITY.json"

    @contextmanager
    def prepared(*args, **kwargs):
        temporary = tmp_path / ".prepared-image.tar"
        temporary.write_bytes(b"prepared image")
        try:
            yield {"schema_version": 2}, temporary
        finally:
            temporary.unlink(missing_ok=True)

    original_publish = _IMAGE_MODULE._publish_exclusive

    def fail_identity_publish(temporary: Path, output: Path) -> None:
        if output == identity:
            raise OSError("synthetic identity publish failure")
        original_publish(temporary, output)

    monkeypatch.setattr(_IMAGE_MODULE, "_prepared_image_identity", prepared)
    monkeypatch.setattr(
        _IMAGE_MODULE, "_publish_exclusive", fail_identity_publish
    )

    with pytest.raises(OSError, match="identity publish failure"):
        _IMAGE_MODULE._publish_identity_outputs(
            tmp_path, "synthetic:image", archive, identity
        )
    assert not archive.exists()
    assert not identity.exists()
