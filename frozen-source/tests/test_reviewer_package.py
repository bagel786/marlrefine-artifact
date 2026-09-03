from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deposit.build_protocol_bundle import (
    _write_checksums,
    _write_deterministic_archive,
)
from deposit.build_reviewer_package import (
    PROTOCOL_GENERATED_PATHS,
    PROTOCOL_ROOT_NAME,
    ReviewerPackageError,
    _analyze_with_frozen_source,
    _protocol_source_tree_sha256,
    _render_latex_with_frozen_source,
    _runtime_with_frozen_source,
    _validate_container_shape,
    _validate_protocol_bundle,
    build_reviewer_package,
    render_paper_identity,
    verify_reviewer_package,
)
from marlrefine.provenance import SOURCE_ROOT_FILES

_EXPECTED_REANALYSIS: dict[str, Any] | None = None
_EXPECTED_LATEX = ""
_EXPECTED_PROTOCOL: dict[str, Any] | None = None
_EXPECTED_RUNTIME: dict[str, Any] | None = None


def _docker_image_archive_bytes() -> tuple[bytes, str, str]:
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as layer_archive:
        payload = b"frozen image fixture\n"
        member = tarfile.TarInfo("fixture.txt")
        member.size = len(payload)
        layer_archive.addfile(member, io.BytesIO(payload))
    layer_bytes = layer_buffer.getvalue()
    layer_digest = hashlib.sha256(layer_bytes).hexdigest()
    config_bytes = json.dumps(
        {
            "architecture": "arm64",
            "config": {},
            "history": [{"created_by": "fixture"}],
            "os": "linux",
            "rootfs": {
                "diff_ids": [f"sha256:{layer_digest}"],
                "type": "layers",
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    config_digest = hashlib.sha256(config_bytes).hexdigest()
    config_path = f"blobs/sha256/{config_digest}"
    layer_path = f"blobs/sha256/{layer_digest}"
    oci_manifest_bytes = json.dumps(
        {
            "config": {
                "digest": f"sha256:{config_digest}",
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": len(config_bytes),
            },
            "layers": [
                {
                    "digest": f"sha256:{layer_digest}",
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "size": len(layer_bytes),
                }
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    manifest_digest = hashlib.sha256(oci_manifest_bytes).hexdigest()
    manifest_path = f"blobs/sha256/{manifest_digest}"
    index_bytes = json.dumps(
        {
            "manifests": [
                {
                    "digest": f"sha256:{manifest_digest}",
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": "arm64", "os": "linux"},
                    "size": len(oci_manifest_bytes),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    compatibility_bytes = json.dumps(
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
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
        for directory in ("blobs", "blobs/sha256"):
            member = tarfile.TarInfo(directory)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
        for name, payload in (
            (config_path, config_bytes),
            (layer_path, layer_bytes),
            (manifest_path, oci_manifest_bytes),
            ("index.json", index_bytes),
            ("manifest.json", compatibility_bytes),
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return (
        archive_buffer.getvalue(),
        f"sha256:{manifest_digest}",
        f"sha256:{config_digest}",
    )


@pytest.fixture(autouse=True)
def _read_only_analysis_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    def recompute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert _EXPECTED_REANALYSIS is not None
        return deepcopy(_EXPECTED_REANALYSIS)

    monkeypatch.setattr(
        "deposit.build_reviewer_package._analyze_with_frozen_source", recompute
    )
    monkeypatch.setattr(
        "deposit.build_reviewer_package._render_latex_with_frozen_source",
        lambda *args, **kwargs: _EXPECTED_LATEX.encode(),
    )
    monkeypatch.setattr(
        "deposit.build_reviewer_package._runtime_with_frozen_source",
        lambda *args, **kwargs: deepcopy(_EXPECTED_RUNTIME),
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _protocol_bundle(path: Path, evidence: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / PROTOCOL_ROOT_NAME
        staging.mkdir()
        for name in SOURCE_ROOT_FILES:
            payload = b"version = 1\n" if name == "uv.lock" else f"{name}\n".encode()
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (staging / "tests").mkdir()
        (staging / "tests/test_deposit.py").write_text(
            'NEGATIVE_PATH = "/Users/example/project/.venv/bin/python"\n',
            encoding="utf-8",
        )
        source_sha, _ = _protocol_source_tree_sha256(staging)
        lock_sha = _sha(staging / "uv.lock")
        source_revision = "d" * 40
        archive_revision = "e" * 40
        mutation = {"artifact_type": "sealed-mutation", "schema_version": 1}
        _write_json(staging / "manifests/mutation_v1.json", mutation)
        mutation_sha = _sha(staging / "manifests/mutation_v1.json")
        manifest = {
            "environment": {
                "git_revision": source_revision,
                "source_tree_sha256": source_sha,
                "uv_lock_sha256": lock_sha,
            },
            "manifest_status": "frozen_pending_archive",
            "mutation_evaluation": {
                "mutation_manifest_path": "manifests/mutation_v1.json",
                "mutation_manifest_sha256": mutation_sha,
            },
            "schema_version": 2,
        }
        _write_json(staging / "manifests/study_v1_draft.json", manifest)
        manifest_sha = _sha(staging / "manifests/study_v1_draft.json")
        runtime = {
            "created_at_utc": "2026-08-31T12:00:00+00:00",
            "git_dirty": None,
            "git_revision": None,
            "installed_distribution_sha256": {"shimmy": "b" * 64},
            "packages": {"marlrefine": "0.1.0"},
            "platform": {
                "machine": "aarch64",
                "platform": "Linux-test-aarch64",
                "release": "test-release",
                "system": "Linux",
            },
            "python": {
                "executable_name": "python3",
                "implementation": "CPython",
                "version": "3.13.2",
            },
            "source_identity_scope": "project_tree",
            "source_tree_sha256": source_sha,
            "uv_lock_sha256": lock_sha,
        }
        container_runtime = {
            key: runtime[key]
            for key in (
                "installed_distribution_sha256",
                "packages",
                "python",
                "source_tree_sha256",
                "uv_lock_sha256",
            )
        }
        image_archive_bytes, image_id, image_config_digest = (
            _docker_image_archive_bytes()
        )
        image_archive = {
            "format": "docker_image_save_oci_layout_tar_v1",
            "filename": "marl-adapter-conformance-protocol-v1.docker.tar",
            "sha256": hashlib.sha256(image_archive_bytes).hexdigest(),
            "size_bytes": len(image_archive_bytes),
        }
        container = {
            "base_image": f"python:3.13@sha256:{'9' * 64}",
            "container_runtime": container_runtime,
            "dockerfile_sha256": _sha(staging / "Dockerfile"),
            "image_archive": image_archive,
            "image_config_digest": image_config_digest,
            "image_id": image_id,
            "image_id_kind": "oci_manifest_digest",
            "image_manifest_digest": image_id,
            "image_reference": "marlrefine:test",
            "platform": {"architecture": "arm64", "os": "linux"},
            "repo_digests": [],
            "schema_version": 2,
            "source_tree_sha256": source_sha,
            "study_manifest": {
                "path": "manifests/study_v1_draft.json",
                "sha256": manifest_sha,
            },
            "verification_command": f"docker run --rm {image_id}",
            "verification_output_normalization": (
                "uv_bytecode_and_pytest_elapsed_redacted_lf_v1"
            ),
            "verification_output_sha256": "8" * 64,
            "verification_status": "tests_passed",
        }
        _write_json(staging / "container/IMAGE_IDENTITY.json", container)
        generated = {
            "artifacts/discovery_api_baselines.json": b"{}\n",
            "artifacts/discovery_controls.json": b"{}\n",
            "artifacts/discovery_repairs.json": evidence,
            "artifacts/pilot.jsonl": b"{}\n",
            "artifacts/registry_census.json": b"{}\n",
        }
        for name, payload in generated.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        freeze = {
            "archive_git_revision": archive_revision,
            "container_image_archive": image_archive,
            "generated_evidence_paths": sorted(PROTOCOL_GENERATED_PATHS),
            "git_identity_model": "two_commit_nonrecursive_v1",
            "manifest_sha256": manifest_sha,
            "mutation_manifest_sha256": mutation_sha,
            "source_git_revision": source_revision,
            "source_tree_sha256": source_sha,
            "uv_lock_sha256": lock_sha,
        }
        _write_json(staging / "FREEZE_METADATA.json", freeze)
        _write_checksums(staging)
        _write_deterministic_archive(staging, path)
    return {
        "archive_revision": archive_revision,
        "container": container,
        "image_archive_bytes": image_archive_bytes,
        "evidence_sha": hashlib.sha256(evidence).hexdigest(),
        "lock_sha": lock_sha,
        "manifest_sha": manifest_sha,
        "runtime": runtime,
        "source_revision": source_revision,
        "source_sha": source_sha,
    }


def _fixture(root: Path) -> tuple[Path, dict[str, Path]]:
    files = {
        "pre_run_bundle": root / "dist/protocol.tar.gz",
        "pre_run_identity": root / "dist/protocol_identity.json",
        "archive_receipt": root / "deposit/archive_receipt.json",
        "archive_gate_log": root / "results/archive_gate.log",
        "raw_batch": root / "results/prospective_raw.jsonl",
        "external_baselines": root / "results/external_baselines.json",
        "mutation_batch": root / "results/mutation_batch.json",
        "frozen_analysis": root / "results/frozen_analysis.json",
        "latex_macros": root / "results/generated_results.tex",
        "manual_adjudication": root / "results/manual_adjudication.json",
        "container_identity": root / "container/IMAGE_IDENTITY.json",
        "container_image_archive": (
            root
            / "dist/private-execution-image"
            / "marl-adapter-conformance-protocol-v1.docker.tar"
        ),
        "reproduction_readme": root / "results/REPRODUCE.md",
        "deviation_log": root / "results/deviations.json",
        "run_diary": root / "results/run_diary.md",
        "evidence:root-one-patch": root / "results/root-one.patch",
    }
    protocol = _protocol_bundle(
        files["pre_run_bundle"], b'{"sealed":"discovery evidence"}\n'
    )
    files["container_image_archive"].parent.mkdir(parents=True, exist_ok=True)
    files["container_image_archive"].write_bytes(protocol["image_archive_bytes"])
    nested_evidence_sha = protocol["evidence_sha"]
    manifest_sha = protocol["manifest_sha"]
    lockfile_sha = protocol["lock_sha"]
    source_sha = protocol["source_sha"]
    files["raw_batch"].parent.mkdir(parents=True, exist_ok=True)
    files["raw_batch"].write_text('{"artifact_type":"raw"}\n', encoding="utf-8")
    files["evidence:root-one-patch"].write_text(
        "diff --git a/source.py b/source.py\n"
        "--- a/source.py\n"
        "+++ b/source.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    _write_json(files["external_baselines"], {"artifact_type": "external"})
    _write_json(files["mutation_batch"], {"artifact_type": "mutation"})
    files["deviation_log"].write_text(
        "# Deviation log\n\nStatus: no deviations\n\n"
        "## Deviation entry\n\nNo deviations were recorded.\n",
        encoding="utf-8",
    )
    files["latex_macros"].write_text(
        "% Generated by marlrefine frozen analysis.\n", encoding="utf-8"
    )
    _write_json(
        files["pre_run_identity"],
        {
            "artifact_type": "marlrefine_protocol_freeze_identity",
            "manifest_sha256": manifest_sha,
            "protocol_bundle": {
                "filename": files["pre_run_bundle"].name,
                "sha256": _sha(files["pre_run_bundle"]),
            },
            "schema_version": 1,
            "source_tree_sha256": source_sha,
            "uv_lock_sha256": lockfile_sha,
        },
    )
    _write_json(
        files["archive_receipt"],
        {
            "artifact_type": "marlrefine_protocol_archive_receipt",
            "archive_url": "https://zenodo.org/records/123456",
            "doi": "10.5281/zenodo.123456",
            "identity_file": {
                "filename": files["pre_run_identity"].name,
                "sha256": _sha(files["pre_run_identity"]),
            },
            "protocol_bundle": {
                "filename": files["pre_run_bundle"].name,
                "sha256": _sha(files["pre_run_bundle"]),
            },
            "published_at_utc": "2026-08-31T12:00:00+00:00",
            "record_id": 123456,
            "schema_version": 1,
            "manifest_sha256": manifest_sha,
            "source_tree_sha256": source_sha,
            "uv_lock_sha256": lockfile_sha,
        },
    )
    files["archive_gate_log"].write_text(
        "verified 10.5281/zenodo.123456; "
        f"manifest_sha256={manifest_sha}; "
        f"source_tree_sha256={source_sha}; "
        f"uv_lock_sha256={lockfile_sha}; prospective_cases=840\n",
        encoding="utf-8",
    )
    runtime = protocol["runtime"]
    _write_json(files["container_identity"], protocol["container"])
    raw_sha = _sha(files["raw_batch"])
    _write_json(
        files["manual_adjudication"],
        {
            "artifact_type": "marlrefine_manual_adjudication",
            "controls": [
                {
                    "control_id": control_id,
                    "evidence_artifact_sha256": raw_sha,
                    "observed_alarm_count": 0,
                    "outcome": "pass",
                    "unexplained_alarm_count": 0,
                }
                for control_id in (
                    "native_clone_replay_v1",
                    "openspiel_turn_based_simultaneous_v1",
                    "pettingzoo_parallel_to_aec_v1",
                )
            ],
            "finding_dispositions": [],
            "optional_measurements": {
                "held_out_mutants_killed": 1,
                "held_out_mutants_total": 1,
                "peak_memory_bytes": None,
            },
            "raw_batch_sha256": raw_sha,
            "roots": [
                {
                    "adjudication_status": "confirmed",
                    "baselines": {},
                    "causal_patch": {
                        "evidence_reference": "results/root-one.patch",
                        "patch_sha256": _sha(files["evidence:root-one-patch"]),
                        "stock_source_tree_sha256": source_sha,
                        "treatment_source_tree_sha256": "1" * 64,
                    },
                    "first_witness": {
                        "evidence_artifact_sha256": raw_sha,
                    },
                    "repair": {"evidence": None, "status": "not_applicable"},
                    "replay": {
                        "evidence": {
                            "artifact_sha256": nested_evidence_sha,
                            "evidence_reference": "discovery replay",
                        },
                        "status": "reproduced",
                    },
                    "root_id": "root-one",
                    "upstream": {"reference": None, "status": "not_applicable"},
                }
            ],
            "schema_version": 5,
            "status": "complete",
        },
    )
    global _EXPECTED_PROTOCOL, _EXPECTED_RUNTIME
    _EXPECTED_PROTOCOL = deepcopy(protocol)
    _EXPECTED_RUNTIME = deepcopy(runtime)
    _sync_analysis(files)
    global _EXPECTED_LATEX
    _EXPECTED_LATEX = files["latex_macros"].read_text(encoding="utf-8")
    _write_reviewer_documents(files, protocol)
    inventory = root / "results/reviewer_inventory.json"
    _write_inventory(root, inventory, files)
    return inventory, files


def _write_reviewer_documents(
    files: dict[str, Path], protocol: dict[str, Any]
) -> None:
    root = files["pre_run_bundle"].parents[1]

    def path(role: str) -> str:
        return files[role].relative_to(root).as_posix()

    expected_roles = (
        "pre_run_bundle",
        "pre_run_identity",
        "archive_receipt",
        "raw_batch",
        "external_baselines",
        "mutation_batch",
        "frozen_analysis",
        "latex_macros",
        "manual_adjudication",
        "container_identity",
        "container_image_archive",
    )
    expected = "".join(
        f"- {path(role)}: {_sha(files[role])}\n" for role in expected_roles
    )
    files["reproduction_readme"].write_text(
        "# Reproduction README\n\n"
        "Status: verified\n\n"
        "## Clean-environment command\n\n"
        "uv run python deposit/build_reviewer_package.py verify "
        "--archive reviewer.tar.gz --identity reviewer_identity.json\n\n"
        "## Expected hashes\n\n"
        + expected,
        encoding="utf-8",
    )
    stage_outputs = {
        "verify-archive": ("archive_gate_log",),
        "prospective batch": ("raw_batch",),
        "external baselines": ("external_baselines",),
        "mutation": ("mutation_batch",),
        "preliminary analysis": (),
        "final analysis": ("frozen_analysis", "latex_macros"),
    }
    stage_commands = {
        "verify-archive": "uv run marlrefine verify-archive",
        "prospective batch": "uv run marlrefine prospective",
        "external baselines": "uv run marlrefine prospective-baselines",
        "mutation": "uv run python experiments/run_mutation_study.py",
        "preliminary analysis": (
            "uv run python experiments/analyze_prospective_batch.py "
            "--output /results/prospective_analysis_preliminary.json"
        ),
        "final analysis": (
            "uv run python experiments/analyze_prospective_batch.py "
            f"--manual-adjudication {path('manual_adjudication')}"
        ),
    }
    blocks = []
    for index, (stage, roles) in enumerate(stage_outputs.items(), 1):
        if stage == "preliminary analysis":
            output_paths = "results/prospective_analysis_preliminary.json"
            output_hashes = "1" * 64
        else:
            output_paths = ", ".join(path(role) for role in roles)
            output_hashes = ", ".join(_sha(files[role]) for role in roles)
        inputs = f"{path('archive_receipt')} {_sha(files['archive_receipt'])}"
        if stage == "final analysis":
            inputs += (
                f", {path('manual_adjudication')} "
                f"{_sha(files['manual_adjudication'])}"
            )
        blocks.append(
            "## Run entry\n\n"
            f"- Entry ID: run-{index:02d}\n"
            f"- Stage: {stage}\n"
            "- Started at (UTC): 2026-08-31T12:00:00+00:00\n"
            "- Ended at (UTC): 2026-08-31T12:01:00+00:00\n"
            "- Exact command: docker run --rm "
            f"{protocol['container']['image_id']} {stage_commands[stage]}\n"
            f"- Input paths and SHA-256 values: {inputs}\n"
            f"- Intended output path(s): {output_paths}\n"
            "- Exit code or interruption signal: 0\n"
            "- Completion state (`completed` or `interrupted`): completed\n"
            f"- Published output path(s), or `none`: {output_paths}\n"
            f"- Published output SHA-256 values, or `none`: {output_hashes}\n"
            f"- Backup copy path(s): sealed-backup/{output_paths}\n"
            f"- Backup hash verification: {output_hashes}\n"
            "- Operational notes: completed as frozen\n"
            "- Linked deviation ID(s), or `none`: none\n"
        )
    files["run_diary"].write_text(
        "# Run diary\n\n"
        "Status: complete through final analysis\n\n"
        "## Study identity\n\n"
        "- Protocol record URL/DOI: 10.5281/zenodo.123456\n"
        f"- Archive receipt path and SHA-256: {path('archive_receipt')} "
        f"{_sha(files['archive_receipt'])}\n"
        f"- Source commit A: {protocol['source_revision']}\n"
        f"- Generated-evidence commit B: {protocol['archive_revision']}\n"
        f"- Container image ID: {protocol['container']['image_id']}\n"
        "- Container image archive path and SHA-256: "
        f"{path('container_image_archive')} "
        f"{_sha(files['container_image_archive'])}\n"
        "- Docker execution engine/store: Docker Engine 29.5.2 with "
        "containerd image store\n"
        "- Container image platform: linux/arm64\n"
        "- Exact image backup alias and round-trip SHA-256: "
        "private-github-release/protocol-v1-image "
        f"{_sha(files['container_image_archive'])}\n"
        "- Operator: study-operator\n"
        f"- Primary result path (repository-relative or container path): "
        f"{path('raw_batch')}\n"
        "- Free space before first gated command (GiB): 100\n"
        "- Separate backup target (volume label/stable alias plus relative path): "
        "sealed-backup/results\n"
        "- Backup capacity verified at (UTC): 2026-08-31T11:59:00+00:00\n"
        f"- Deviation-log path: {path('deviation_log')}\n\n"
        + "\n".join(blocks)
        + "\n\n## Artifact identity index\n\n"
        + "".join(
            f"- {path(role)}: {_sha(files[role])}\n"
            for role in (
                "archive_gate_log",
                "raw_batch",
                "external_baselines",
                "mutation_batch",
                "frozen_analysis",
                "latex_macros",
                "manual_adjudication",
            )
        ),
        encoding="utf-8",
    )


def _sync_analysis(files: dict[str, Path]) -> None:
    protocol_identity = json.loads(
        files["pre_run_identity"].read_text(encoding="utf-8")
    )
    assert _EXPECTED_RUNTIME is not None
    _write_json(
        files["frozen_analysis"],
        {
            "analysis_id": "marlrefine_frozen_analysis_v9",
            "artifact_type": "marlrefine_frozen_prospective_analysis",
            "input_identities": {
                "archive_receipt": {
                    "filename": files["archive_receipt"].name,
                    "sha256": _sha(files["archive_receipt"]),
                },
                "external_baselines": {
                    "path_name": files["external_baselines"].name,
                    "sha256": _sha(files["external_baselines"]),
                },
                "manual_adjudication_sha256": _sha(
                    files["manual_adjudication"]
                ),
                "manifest": {
                    "filename": "study_v1_draft.json",
                    "sha256": protocol_identity["manifest_sha256"],
                },
                "mutation_batch": {
                    "path_name": files["mutation_batch"].name,
                    "sha256": _sha(files["mutation_batch"]),
                },
                "raw_batch": {
                    "filename": files["raw_batch"].name,
                    "sha256": _sha(files["raw_batch"]),
                },
                "source_tree_sha256": protocol_identity["source_tree_sha256"],
                "uv_lock_sha256": protocol_identity["uv_lock_sha256"],
            },
            "manual_adjudication": {
                "source": {"sha256": _sha(files["manual_adjudication"])},
                "status": "complete",
            },
            "analysis_runtime": deepcopy(_EXPECTED_RUNTIME),
            "runtime": deepcopy(_EXPECTED_RUNTIME),
            "schema_version": 9,
        },
    )
    global _EXPECTED_REANALYSIS
    expected = json.loads(files["frozen_analysis"].read_text(encoding="utf-8"))
    expected.pop("analysis_runtime", None)
    _EXPECTED_REANALYSIS = expected
    if _EXPECTED_PROTOCOL is not None:
        _write_reviewer_documents(files, _EXPECTED_PROTOCOL)


def _write_inventory(
    root: Path, inventory: Path, files: dict[str, Path]
) -> None:
    _write_json(
        inventory,
        {
            "artifact_type": "marlrefine_reviewer_package_inventory",
            "entries": [
                {
                    "path": path.relative_to(root).as_posix(),
                    "role": role,
                    "sha256": _sha(path),
                }
                for role, path in reversed(tuple(files.items()))
            ],
            "schema_version": 1,
        },
    )


def _rewrite_protocol_member(
    bundle: Path,
    relative: str,
    replacement: bytes,
    *,
    refresh_checksums: bool,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        with tarfile.open(bundle, mode="r:gz") as archive:
            archive.extractall(temporary_path, filter="data")
        staging = temporary_path / PROTOCOL_ROOT_NAME
        (staging / relative).write_bytes(replacement)
        if refresh_checksums:
            _write_checksums(staging)
        rebuilt = temporary_path / "rebuilt.tar.gz"
        _write_deterministic_archive(staging, rebuilt)
        shutil.copyfile(rebuilt, bundle)


def _validate_fixture_protocol(tmp_path: Path, files: dict[str, Path]) -> None:
    identity = json.loads(files["pre_run_identity"].read_text(encoding="utf-8"))
    extraction = tmp_path / "protocol-extraction"
    extraction.mkdir()
    _validate_protocol_bundle(
        files["pre_run_bundle"],
        identity,
        files["container_identity"],
        extraction,
    )


def _build(root: Path, inventory: Path, directory: str = "build") -> tuple[Path, Path]:
    archive = root / directory / "reviewer.tar.gz"
    identity = root / directory / "reviewer_identity.json"
    build_reviewer_package(root, inventory, archive, identity)
    return archive, identity


def test_build_is_deterministic_and_verifies_independently(tmp_path: Path) -> None:
    inventory, _ = _fixture(tmp_path)
    first_archive, first_identity = _build(tmp_path, inventory, "first")
    second_archive, second_identity = _build(tmp_path, inventory, "second")

    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert first_identity.read_bytes() == second_identity.read_bytes()
    result = verify_reviewer_package(first_archive, first_identity)
    assert result["entry_count"] == 16
    assert result["archive_sha256"] == _sha(first_archive)


def test_reviewer_package_rejects_image_archive_not_bound_by_identity(
    tmp_path: Path,
) -> None:
    inventory, files = _fixture(tmp_path)
    files["container_image_archive"].write_bytes(b"substituted image archive")
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="committed identity"):
        _build(tmp_path, inventory)


def test_reviewer_package_rejects_wrong_oci_config_digest(
    tmp_path: Path,
) -> None:
    inventory, files = _fixture(tmp_path)
    container = json.loads(files["container_identity"].read_text(encoding="utf-8"))
    container["image_config_digest"] = f"sha256:{'f' * 64}"
    _write_json(files["container_identity"], container)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="content identity differs"):
        _build(tmp_path, inventory)


def test_protocol_validator_accepts_canonical_example_fixture(tmp_path: Path) -> None:
    _, files = _fixture(tmp_path)
    _validate_fixture_protocol(tmp_path, files)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("image_id_kind", 1, "image ID kind"),
        ("image_id_kind", "docker_config_digest", "image ID kind"),
        (
            "image_manifest_digest",
            f"sha256:{'f' * 64}",
            "manifest digest differs",
        ),
        ("image_manifest_digest", 1, "manifest digest differs"),
        ("image_config_digest", "f" * 64, "config digest"),
        ("image_config_digest", 1, "config digest"),
    ],
)
def test_container_identity_requires_exact_oci_digest_kinds(
    tmp_path: Path,
    field: str,
    value: Any,
    match: str,
) -> None:
    protocol = _protocol_bundle(
        tmp_path / "protocol.tar.gz", b'{"sealed":"fixture"}\n'
    )
    container = deepcopy(protocol["container"])
    container[field] = value

    with pytest.raises(ReviewerPackageError, match=match):
        _validate_container_shape(container)


def test_frozen_source_subprocess_helpers_run_without_stubs(tmp_path: Path) -> None:
    protocol_root = tmp_path / "frozen-protocol"
    package = protocol_root / "src/marlrefine"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "provenance.py").write_text(
        "def runtime_provenance():\n"
        "    return {'origin': 'extracted-frozen-source', 'schema_version': 1}\n",
        encoding="utf-8",
    )
    (package / "analysis.py").write_text(
        "def analyze_prospective_batch(batch, manifest, receipt, *, "
        "manual_adjudication_path, external_baseline_path, "
        "mutation_batch_path):\n"
        "    paths = (batch, manifest, receipt, manual_adjudication_path, "
        "external_baseline_path, mutation_batch_path)\n"
        "    return {'metric': 1, 'input_names': [path.name for path in paths]}\n\n"
        "def latex_result_macros(analysis):\n"
        "    return f\"metric={analysis['metric']}\\n\"\n",
        encoding="utf-8",
    )
    inputs = [tmp_path / f"input-{index}.json" for index in range(6)]
    for path in inputs:
        path.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "subprocess-analysis.json"

    runtime = _runtime_with_frozen_source(protocol_root)
    analysis = _analyze_with_frozen_source(
        protocol_root,
        raw_batch_path=inputs[0],
        manifest_path=inputs[1],
        receipt_path=inputs[2],
        manual_path=inputs[3],
        external_baselines_path=inputs[4],
        mutation_batch_path=inputs[5],
        output_path=output,
    )
    latex_dir = tmp_path / "latex-render"
    latex_dir.mkdir()
    latex = _render_latex_with_frozen_source(
        protocol_root, analysis, latex_dir
    )

    assert runtime == {
        "origin": "extracted-frozen-source",
        "schema_version": 1,
    }
    assert analysis == {
        "input_names": [path.name for path in inputs],
        "metric": 1,
    }
    assert latex == b"metric=1\n"


def test_protocol_validator_rejects_tampered_internal_checksums(
    tmp_path: Path,
) -> None:
    _, files = _fixture(tmp_path)
    _rewrite_protocol_member(
        files["pre_run_bundle"],
        "SHA256SUMS",
        b"0" * 64 + b"  README.md\n",
        refresh_checksums=False,
    )
    with pytest.raises(ReviewerPackageError, match="SHA256SUMS differs"):
        _validate_fixture_protocol(tmp_path, files)


def test_protocol_validator_rejects_tampered_source_tree(tmp_path: Path) -> None:
    _, files = _fixture(tmp_path)
    _rewrite_protocol_member(
        files["pre_run_bundle"],
        "tests/test_deposit.py",
        b"TAMPERED = True\n",
        refresh_checksums=True,
    )
    with pytest.raises(ReviewerPackageError, match="freeze source_tree_sha256 differs"):
        _validate_fixture_protocol(tmp_path, files)


def test_protocol_validator_rejects_tampered_nested_image_identity(
    tmp_path: Path,
) -> None:
    _, files = _fixture(tmp_path)
    container = json.loads(files["container_identity"].read_text(encoding="utf-8"))
    container["image_id"] = f"sha256:{'f' * 64}"
    container["repo_digests"] = [f"marlrefine@sha256:{'f' * 64}"]
    _rewrite_protocol_member(
        files["pre_run_bundle"],
        "container/IMAGE_IDENTITY.json",
        _json_bytes(container),
        refresh_checksums=True,
    )
    with pytest.raises(ReviewerPackageError, match="differs from.*bundle member"):
        _validate_fixture_protocol(tmp_path, files)


def test_protocol_validator_rejects_trailing_gzip_bytes(tmp_path: Path) -> None:
    _, files = _fixture(tmp_path)
    with files["pre_run_bundle"].open("ab") as handle:
        handle.write(b"hidden trailing bytes")
    with pytest.raises(ReviewerPackageError, match="trailing or concatenated"):
        _validate_fixture_protocol(tmp_path, files)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("path", "../outside.json", "traversal|canonical"),
        ("role", "unexpected_result", "unexpected singleton role"),
    ],
)
def test_inventory_rejects_traversal_and_unknown_roles(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    inventory, _ = _fixture(tmp_path)
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["entries"][0][field] = value
    _write_json(inventory, payload)

    with pytest.raises(ReviewerPackageError, match=match):
        _build(tmp_path, inventory)


def test_inventory_rejects_duplicate_roles(tmp_path: Path) -> None:
    inventory, _ = _fixture(tmp_path)
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["entries"][1]["role"] = payload["entries"][0]["role"]
    _write_json(inventory, payload)

    with pytest.raises(ReviewerPackageError, match="duplicate inventory role"):
        _build(tmp_path, inventory)


def test_inventory_rejects_duplicate_paths_and_missing_roles(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["entries"][1]["path"] = payload["entries"][0]["path"]
    _write_json(inventory, payload)
    with pytest.raises(ReviewerPackageError, match="duplicate inventory path"):
        _build(tmp_path, inventory, "duplicate-path")

    _write_inventory(tmp_path, inventory, files)
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["entries"].pop()
    _write_json(inventory, payload)
    with pytest.raises(ReviewerPackageError, match="missing required roles"):
        _build(tmp_path, inventory, "missing-role")


def test_inventory_rejects_noninteger_schema_and_postseal_overlay(
    tmp_path: Path,
) -> None:
    inventory, _ = _fixture(tmp_path)
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["schema_version"] = 1.0
    _write_json(inventory, payload)
    with pytest.raises(ReviewerPackageError, match="inventory schema"):
        _build(tmp_path, inventory, "float-schema")

    inventory, _ = _fixture(tmp_path / "overlay")
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["entries"][0]["path"] = "paper/reviewer_package_identity.tex"
    _write_json(inventory, payload)
    with pytest.raises(ReviewerPackageError, match="post-seal paper identity"):
        _build(tmp_path / "overlay", inventory, "overlay-inventory")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda value: value.update(schema_version=1.0), "schema or type"),
        (lambda value: value.update(unexpected=True), "keys differ"),
        (lambda value: value.update(record_id=123457), "DOI differs"),
        (
            lambda value: value.update(
                archive_url="https://zenodo.org/records/999999"
            ),
            "URL is not canonical",
        ),
        (
            lambda value: value.update(
                published_at_utc="2999-01-01T00:00:00+00:00"
            ),
            "in the future",
        ),
    ],
)
def test_archive_receipt_offline_invariants_are_exact(
    tmp_path: Path,
    mutate: Any,
    match: str,
) -> None:
    inventory, files = _fixture(tmp_path)
    receipt = json.loads(files["archive_receipt"].read_text(encoding="utf-8"))
    mutate(receipt)
    _write_json(files["archive_receipt"], receipt)
    _write_inventory(tmp_path, inventory, files)
    with pytest.raises(ReviewerPackageError, match=match):
        _build(tmp_path, inventory)


def test_inventory_rejects_symlinks(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    deviations = files["deviation_log"]
    target = deviations.with_name("real-deviations.json")
    deviations.replace(target)
    os.symlink(target.name, deviations)

    with pytest.raises(ReviewerPackageError, match="symlink"):
        _build(tmp_path, inventory)


def test_machine_specific_paths_are_rejected(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    machine_path = "/".join(("", "Users", "alice", "private-study", "result.json"))
    files["deviation_log"].write_text(
        f'{{"log":"{machine_path}"}}\n',
        encoding="utf-8",
    )
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="machine-specific path"):
        _build(tmp_path, inventory)


def test_incomplete_manual_adjudication_is_rejected(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    manual = json.loads(files["manual_adjudication"].read_text(encoding="utf-8"))
    manual["status"] = "pending"
    _write_json(files["manual_adjudication"], manual)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="not complete"):
        _build(tmp_path, inventory)


def test_unavailable_confirmed_root_evidence_is_rejected(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    manual = json.loads(files["manual_adjudication"].read_text(encoding="utf-8"))
    manual["roots"][0]["replay"]["evidence"]["artifact_sha256"] = "f" * 64
    _write_json(files["manual_adjudication"], manual)
    _sync_analysis(files)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="evidence hashes are absent"):
        _build(tmp_path, inventory)


def test_unavailable_rejected_root_evidence_is_rejected(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    manual = json.loads(files["manual_adjudication"].read_text(encoding="utf-8"))
    rejected = deepcopy(manual["roots"][0])
    rejected["root_id"] = "rejected-root"
    rejected["adjudication_status"] = "rejected"
    rejected["causal_patch"] = None
    rejected["first_witness"]["evidence_artifact_sha256"] = "f" * 64
    rejected["replay"] = {"evidence": None, "status": "not_applicable"}
    manual["roots"].append(rejected)
    _write_json(files["manual_adjudication"], manual)
    _sync_analysis(files)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="evidence hashes are absent"):
        _build(tmp_path, inventory)


def test_confirmed_root_patch_must_be_an_allowlisted_file(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    manual = json.loads(files["manual_adjudication"].read_text(encoding="utf-8"))
    manual["roots"][0]["causal_patch"]["patch_sha256"] = "f" * 64
    _write_json(files["manual_adjudication"], manual)
    _sync_analysis(files)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="evidence hashes are absent"):
        _build(tmp_path, inventory)

    manual["roots"][0]["causal_patch"]["patch_sha256"] = _sha(
        files["raw_batch"]
    )
    _write_json(files["manual_adjudication"], manual)
    _sync_analysis(files)
    _write_inventory(tmp_path, inventory, files)
    with pytest.raises(ReviewerPackageError, match="lack allowlisted evidence"):
        _build(tmp_path, inventory, "patch-is-not-an-evidence-file")


def test_confirmed_root_patch_requires_exact_reference_and_diff_envelope(
    tmp_path: Path,
) -> None:
    inventory, files = _fixture(tmp_path)
    manual = json.loads(files["manual_adjudication"].read_text(encoding="utf-8"))
    manual["roots"][0]["causal_patch"]["evidence_reference"] = (
        "results/nonexistent.patch"
    )
    _write_json(files["manual_adjudication"], manual)
    _sync_analysis(files)
    _write_inventory(tmp_path, inventory, files)
    with pytest.raises(ReviewerPackageError, match="does not resolve"):
        _build(tmp_path, inventory, "bad-patch-reference")

    inventory, files = _fixture(tmp_path / "bad-envelope")
    files["evidence:root-one-patch"].write_text(
        "not a diff\n", encoding="utf-8"
    )
    manual = json.loads(files["manual_adjudication"].read_text(encoding="utf-8"))
    manual["roots"][0]["causal_patch"]["patch_sha256"] = _sha(
        files["evidence:root-one-patch"]
    )
    _write_json(files["manual_adjudication"], manual)
    _sync_analysis(files)
    _write_inventory(tmp_path / "bad-envelope", inventory, files)
    with pytest.raises(ReviewerPackageError, match="not a unified or Git binary"):
        _build(tmp_path / "bad-envelope", inventory, "bad-patch-envelope")


def test_document_roles_require_stable_nonempty_headings(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    files["run_diary"].write_text("# Notes\n", encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="missing stable headings"):
        _build(tmp_path, inventory)


def test_run_diary_requires_completed_command_and_hash_blocks(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    diary = files["run_diary"].read_text(encoding="utf-8")
    diary = diary.replace(
        "- Exact command: docker run --rm ",
        "- Exact command: verify the archive",
        1,
    )
    files["run_diary"].write_text(diary, encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)
    with pytest.raises(ReviewerPackageError, match="not command-shaped"):
        _build(tmp_path, inventory)


def test_run_diary_rejects_host_only_gated_command(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    diary = files["run_diary"].read_text(encoding="utf-8")
    image_id = json.loads(
        files["container_identity"].read_text(encoding="utf-8")
    )["image_id"]
    diary = diary.replace(
        f"docker run --rm {image_id} uv run marlrefine verify-archive",
        "uv run marlrefine verify-archive",
        1,
    )
    files["run_diary"].write_text(diary, encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="exact container image ID"):
        _build(tmp_path, inventory)


def test_run_diary_accepts_canonical_mount_options_before_exact_image(
    tmp_path: Path,
) -> None:
    inventory, files = _fixture(tmp_path)
    diary = files["run_diary"].read_text(encoding="utf-8")
    image_id = json.loads(
        files["container_identity"].read_text(encoding="utf-8")
    )["image_id"]
    diary = diary.replace(
        f"docker run --rm {image_id} uv run marlrefine verify-archive",
        "docker run --rm "
        "--mount type=bind,src=/host/receipt.json,dst=/run/receipt.json,readonly "
        "--mount=type=bind,src=/host/results,dst=/results "
        f"{image_id} uv run marlrefine verify-archive",
        1,
    )
    files["run_diary"].write_text(diary, encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)

    _build(tmp_path, inventory, "canonical-docker-options")


def test_run_diary_rejects_image_id_after_another_image_operand(
    tmp_path: Path,
) -> None:
    inventory, files = _fixture(tmp_path)
    diary = files["run_diary"].read_text(encoding="utf-8")
    image_id = json.loads(
        files["container_identity"].read_text(encoding="utf-8")
    )["image_id"]
    diary = diary.replace(
        f"docker run --rm {image_id} uv run marlrefine verify-archive",
        f"docker run --rm attacker:latest echo {image_id}",
        1,
    )
    files["run_diary"].write_text(diary, encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="exact container image ID"):
        _build(tmp_path, inventory, "digest-is-only-an-inner-argument")


def test_run_diary_distinguishes_preliminary_and_final_analysis(
    tmp_path: Path,
) -> None:
    inventory, files = _fixture(tmp_path)
    _write_inventory(tmp_path, inventory, files)
    _build(tmp_path, inventory, "analysis-stages-valid")

    diary = files["run_diary"].read_text(encoding="utf-8")
    preliminary = (
        "--output /results/prospective_analysis_preliminary.json"
    )
    files["run_diary"].write_text(
        diary.replace(
            preliminary,
            "--manual-adjudication results/manual_adjudication.json "
            + preliminary,
            1,
        ),
        encoding="utf-8",
    )
    _write_inventory(tmp_path, inventory, files)
    with pytest.raises(ReviewerPackageError, match="must omit manual"):
        _build(tmp_path, inventory, "preliminary-with-manual")

    inventory, files = _fixture(tmp_path / "final-without-manual")
    diary = files["run_diary"].read_text(encoding="utf-8")
    diary = diary.replace("--manual-adjudication", "--adjudication", 1)
    files["run_diary"].write_text(diary, encoding="utf-8")
    _write_inventory(tmp_path / "final-without-manual", inventory, files)
    with pytest.raises(ReviewerPackageError, match="lacks manual adjudication"):
        _build(tmp_path / "final-without-manual", inventory, "final-no-manual")


def test_run_diary_accepts_replay_and_evidence_development_stages(
    tmp_path: Path,
) -> None:
    inventory, files = _fixture(tmp_path)
    diary = files["run_diary"].read_text(encoding="utf-8")
    image_id = json.loads(
        files["container_identity"].read_text(encoding="utf-8")
    )["image_id"]
    extra_blocks = ""
    for entry_id, stage, output, digest in (
        ("run-replay", "replay", "results/root-one-replay.json", "2" * 64),
        (
            "run-evidence",
            "evidence development",
            "results/root-one.patch",
            "3" * 64,
        ),
    ):
        extra_blocks += (
            "## Run entry\n\n"
            f"- Entry ID: {entry_id}\n"
            f"- Stage: {stage}\n"
            "- Started at (UTC): 2026-08-31T12:02:00+00:00\n"
            "- Ended at (UTC): 2026-08-31T12:03:00+00:00\n"
            f"- Exact command: docker run --rm {image_id} uv run python "
            "experiments/replay_prospective_finding.py\n"
            f"- Input paths and SHA-256 values: input.json {'4' * 64}\n"
            f"- Intended output path(s): {output}\n"
            "- Exit code or interruption signal: 0\n"
            "- Completion state (`completed` or `interrupted`): completed\n"
            f"- Published output path(s), or `none`: {output}\n"
            f"- Published output SHA-256 values, or `none`: {digest}\n"
            f"- Backup copy path(s): sealed-backup/{output}\n"
            f"- Backup hash verification: {digest}\n"
            "- Operational notes: retained for adjudication\n"
            "- Linked deviation ID(s), or `none`: none\n\n"
        )
    diary = diary.replace(
        "## Artifact identity index",
        extra_blocks + "## Artifact identity index",
    )
    files["run_diary"].write_text(diary, encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)

    _build(tmp_path, inventory, "evidence-stages-valid")


def test_build_preflights_two_payload_copies_plus_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, _ = _fixture(tmp_path)
    monkeypatch.setattr(
        "deposit.build_reviewer_package.shutil.disk_usage",
        lambda path: SimpleNamespace(free=1),
    )

    with pytest.raises(ReviewerPackageError, match="insufficient free space"):
        _build(tmp_path, inventory)


def test_latex_macros_are_bound_to_the_analysis(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    files["latex_macros"].write_text("% unrelated macros\n", encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="deterministic.*rendering"):
        _build(tmp_path, inventory)


def test_latex_is_deterministically_rerendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, files = _fixture(tmp_path)
    expected = files["latex_macros"].read_text(encoding="utf-8")
    monkeypatch.setattr(
        "marlrefine.analysis.latex_result_macros", lambda value: expected
    )
    _write_inventory(tmp_path, inventory, files)
    _build(tmp_path, inventory, "rerendered")

    files["latex_macros"].write_text("% tampered\n", encoding="utf-8")
    _write_inventory(tmp_path, inventory, files)
    with pytest.raises(ReviewerPackageError, match="deterministic.*rendering"):
        _build(tmp_path, inventory, "rerendered-tampered")


def test_fabricated_analysis_is_rejected_by_full_recomputation(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    analysis = json.loads(files["frozen_analysis"].read_text(encoding="utf-8"))
    analysis["fabricated_conclusion"] = {"confirmed_roots": 999}
    _write_json(files["frozen_analysis"], analysis)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="differs from.*recomputation"):
        _build(tmp_path, inventory)


def test_recomputation_comparison_is_json_type_exact(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    analysis = json.loads(files["frozen_analysis"].read_text(encoding="utf-8"))
    analysis["numeric_metric"] = 1.0
    _write_json(files["frozen_analysis"], analysis)
    recomputed = deepcopy(analysis)
    recomputed.pop("analysis_runtime", None)
    recomputed["numeric_metric"] = 1
    global _EXPECTED_REANALYSIS
    _EXPECTED_REANALYSIS = recomputed
    assert _EXPECTED_PROTOCOL is not None
    _write_reviewer_documents(files, _EXPECTED_PROTOCOL)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="differs from.*recomputation"):
        _build(tmp_path, inventory)


def test_frozen_analysis_rejects_float_schema_version(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    analysis = json.loads(files["frozen_analysis"].read_text(encoding="utf-8"))
    analysis["schema_version"] = 8.0
    _write_json(files["frozen_analysis"], analysis)
    _write_inventory(tmp_path, inventory, files)
    with pytest.raises(ReviewerPackageError, match="schema, type, or ID"):
        _build(tmp_path, inventory)


def test_analysis_runtime_cannot_claim_different_provenance(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    analysis = json.loads(files["frozen_analysis"].read_text(encoding="utf-8"))
    analysis["analysis_runtime"]["platform"]["release"] = "fabricated-release"
    _write_json(files["frozen_analysis"], analysis)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="stable batch runtime"):
        _build(tmp_path, inventory)


def test_container_identity_must_equal_frozen_member(tmp_path: Path) -> None:
    inventory, files = _fixture(tmp_path)
    container = json.loads(files["container_identity"].read_text(encoding="utf-8"))
    container["container_runtime"]["uv_lock_sha256"] = "d" * 64
    _write_json(files["container_identity"], container)
    _write_inventory(tmp_path, inventory, files)

    with pytest.raises(ReviewerPackageError, match="differs from.*bundle member"):
        _build(tmp_path, inventory)


def test_verifier_rejects_identity_tampering(tmp_path: Path) -> None:
    inventory, _ = _fixture(tmp_path)
    archive, identity = _build(tmp_path, inventory)
    payload = json.loads(identity.read_text(encoding="utf-8"))
    payload["reviewer_package"]["sha256"] = "0" * 64
    _write_json(identity, payload)

    with pytest.raises(ReviewerPackageError, match="package SHA-256 differs"):
        verify_reviewer_package(archive, identity)


def test_verifier_rejects_trailing_gzip_data(tmp_path: Path) -> None:
    inventory, _ = _fixture(tmp_path)
    archive, identity = _build(tmp_path, inventory)
    with archive.open("ab") as handle:
        handle.write(b"hidden trailing bytes")

    with pytest.raises(ReviewerPackageError, match="trailing or concatenated"):
        verify_reviewer_package(archive, identity)


def test_post_seal_paper_overlay_is_verified_and_non_circular(tmp_path: Path) -> None:
    inventory, _ = _fixture(tmp_path)
    archive, identity = _build(tmp_path, inventory)
    overlay = tmp_path / "paper/reviewer_package_identity.tex"
    review_url = "https://review.example.org/submission?id=private-token"

    render_paper_identity(archive, identity, review_url, overlay)

    text = overlay.read_text(encoding="utf-8")
    assert review_url in text
    assert _sha(archive) in text
    with tarfile.open(archive, mode="r:gz") as sealed:
        assert all(
            "reviewer_package_identity.tex" not in name
            for name in sealed.getnames()
        )

    with pytest.raises(ReviewerPackageError, match="safe absolute HTTPS"):
        unsafe_url = "file:" + "///" + "/".join(
            ("Users", "alice", "private-review")
        )
        render_paper_identity(
            archive,
            identity,
            unsafe_url,
            tmp_path / "bad-overlay.tex",
        )
