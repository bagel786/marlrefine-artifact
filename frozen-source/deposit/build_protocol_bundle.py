#!/usr/bin/env python3
"""Build a deterministic, checksummed Zenodo protocol-deposit archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from marlrefine.archive import protocol_freeze_identity, two_commit_freeze_identity
from marlrefine.image_archive import (
    DOCKER_SAVE_OCI_LAYOUT_FORMAT,
    OCI_MANIFEST_IMAGE_ID_KIND,
    ImageArchiveError,
    validate_docker_image_archive,
)
from marlrefine.mutation_study import build_mutation_manifest
from marlrefine.mutations import (
    CANDIDATE_POOL,
    MUTANTS_PER_FAMILY,
    MUTATION_ENGINE_ID,
    MUTATION_FAMILIES,
    MUTATION_PROTOCOL_ID,
    POOL_PER_FAMILY,
    PROGRESS_INSTRUMENTATION_CONTROLS,
    candidate_manifest_records,
    mutation_engine_source_sha256,
)
from marlrefine.provenance import (
    BYTE_BOUND_DISTRIBUTIONS,
    SOURCE_ROOT_FILES,
    runtime_provenance,
    source_identity_paths,
)
from marlrefine.serialization import write_json
from marlrefine.study import (
    MUTATION_MANIFEST_PATH,
    external_baseline_protocol,
    prospective_execution_contract,
)

ROOT_FILES = SOURCE_ROOT_FILES
DISCOVERY_ARTIFACTS = (
    "artifacts/discovery_api_baselines.json",
    "artifacts/discovery_controls.json",
    "artifacts/discovery_repairs.json",
    "artifacts/pilot.jsonl",
    "artifacts/registry_census.json",
)
FROZEN_GENERATED_PATHS = frozenset(
    {
        "manifests/study_v1_draft.json",
        "manifests/mutation_v1.json",
        "artifacts/discovery_api_baselines.json",
        "artifacts/discovery_controls.json",
        "artifacts/discovery_repairs.json",
        "artifacts/pilot.jsonl",
        "artifacts/registry_census.json",
        "container/IMAGE_IDENTITY.json",
    }
)
IMAGE_ARCHIVE_FORMAT = DOCKER_SAVE_OCI_LAYOUT_FORMAT
IMAGE_ARCHIVE_FILENAME = "marl-adapter-conformance-protocol-v1.docker.tar"
VERIFICATION_OUTPUT_NORMALIZATION = "uv_bytecode_and_pytest_elapsed_redacted_lf_v1"
IMAGE_IDENTITY_KEYS = frozenset(
    {
        "base_image",
        "container_runtime",
        "dockerfile_sha256",
        "image_archive",
        "image_config_digest",
        "image_id",
        "image_id_kind",
        "image_manifest_digest",
        "image_reference",
        "platform",
        "repo_digests",
        "schema_version",
        "source_tree_sha256",
        "study_manifest",
        "verification_command",
        "verification_output_normalization",
        "verification_output_sha256",
        "verification_status",
    }
)
CONTAINER_RUNTIME_KEYS = frozenset(
    {
        "installed_distribution_sha256",
        "packages",
        "python",
        "source_tree_sha256",
        "uv_lock_sha256",
    }
)
CONTAINER_PLATFORM_KEYS = frozenset({"architecture", "os"})
STALE_CONTRACT_EVIDENCE_PHRASES = frozenset(
    {
        "before freeze",
        "pre-freeze",
        "private working document",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_IDS = frozenset(
    {
        "native_clone_replay_v1",
        "openspiel_turn_based_simultaneous_v1",
        "pettingzoo_parallel_to_aec_v1",
    }
)
PILOT_CASE_IDS = frozenset(
    {
        "buffered_reward",
        "chance_horizon",
        "terminal_cleanup",
        "configuration_reset",
        "mean_field",
    }
)
ARTIFACT_IDENTITIES = {
    "artifacts/discovery_api_baselines.json": (
        "pettingzoo_api_test_discovery_baseline",
        1,
    ),
    "artifacts/discovery_controls.json": (
        "marlrefine_discovery_semantic_controls",
        1,
    ),
    "artifacts/discovery_repairs.json": (
        "marlrefine_discovery_causal_treatments",
        1,
    ),
    "artifacts/registry_census.json": ("openspiel_registry_census", 1),
}
ZENODO_LICENSE_SCOPES = {
    "apache-2.0": (
        "Author-owned software source, executable scripts, build configuration, "
        "and container recipes; see LICENSE."
    ),
    "cc-by-4.0": (
        "Documentation, protocol, metadata, manifests, and "
        "generated research data; see LICENSE-docs-data."
    ),
}
ZENODO_PUBLIC_CREATOR = {
    "affiliation": "Independent Researcher",
    "name": "Baig, Safiullah",
    "orcid": "0009-0008-5547-6088",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {token}")
            ),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain one JSON object")
    return value


def _validate_zenodo_metadata(path: Path) -> dict[str, Any]:
    metadata = _read_json_object(path, "Zenodo metadata checklist")
    if metadata.get("metadata_kind") != "manual_zenodo_ui_checklist_not_api_payload":
        raise RuntimeError("Zenodo metadata must identify itself as a manual checklist")
    if metadata.get("visibility") != "public":
        raise RuntimeError("Zenodo metadata must declare public file visibility")
    if metadata.get("resource_type") != "publication-report":
        raise RuntimeError("Zenodo metadata must use the publication-report type")
    if metadata.get("creators") != [ZENODO_PUBLIC_CREATOR]:
        raise RuntimeError(
            "Zenodo metadata must use the privacy-minimized creator identity"
        )
    public_metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    if re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", public_metadata):
        raise RuntimeError("Zenodo metadata contains a public email address")
    if "license" in metadata:
        raise RuntimeError("legacy single-license Zenodo metadata is not permitted")

    licenses = metadata.get("licenses")
    if not isinstance(licenses, list) or any(
        not isinstance(entry, dict) for entry in licenses
    ):
        raise RuntimeError("Zenodo metadata licenses must be a list of objects")
    if any(
        not isinstance(entry.get("id"), str)
        or not isinstance(entry.get("scope"), str)
        for entry in licenses
    ):
        raise RuntimeError("Zenodo license IDs and scopes must be strings")
    observed = {entry.get("id"): entry.get("scope") for entry in licenses}
    if len(observed) != len(licenses) or observed != ZENODO_LICENSE_SCOPES:
        raise RuntimeError(
            "Zenodo metadata must declare the exact Apache-2.0 and CC-BY-4.0 "
            "file-scoped rights"
        )
    return metadata


def _validate_contract_evidence(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"cannot read contract-evidence ledger: {exc}") from exc
    normalized = text.casefold()
    stale = sorted(
        phrase for phrase in STALE_CONTRACT_EVIDENCE_PHRASES if phrase in normalized
    )
    if stale:
        raise RuntimeError(
            "contract-evidence ledger contains stale private/pre-freeze wording: "
            f"{stale}"
        )


def _validate_generated_evidence_commit(diff_status: str) -> tuple[str, ...]:
    records: list[tuple[str, str]] = []
    for line in diff_status.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not all(fields):
            raise RuntimeError(
                f"generated-evidence commit has malformed Git status: {line!r}"
            )
        records.append((fields[0], fields[1]))

    non_additions = sorted(record for record in records if record[0] != "A")
    if non_additions:
        raise RuntimeError(
            "generated-evidence commit must contain only added files; "
            f"observed non-addition statuses: {non_additions}"
        )

    paths = tuple(path for _, path in records)
    if len(paths) != len(set(paths)):
        raise RuntimeError("generated-evidence commit contains duplicate path statuses")
    missing = sorted(FROZEN_GENERATED_PATHS.difference(paths))
    unexpected = sorted(set(paths).difference(FROZEN_GENERATED_PATHS))
    if missing or unexpected or len(paths) != len(FROZEN_GENERATED_PATHS):
        raise RuntimeError(
            "generated-evidence commit must add exactly the eight frozen generated "
            f"files; missing={missing}, unexpected={unexpected}"
        )
    return tuple(sorted(paths))


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise RuntimeError(f"{label} is not a lowercase SHA-256 digest")
    return value


def _validate_environment(
    payload: dict[str, Any],
    *,
    label: str,
    manifest_environment: dict[str, Any],
    manifest_sha256: str,
    source_tree_sha256: str,
    uv_lock_sha256: str,
) -> None:
    environment = payload.get("environment")
    if not isinstance(environment, dict):
        raise RuntimeError(f"{label} environment is missing")
    if environment.get("source_tree_sha256") != source_tree_sha256:
        raise RuntimeError(f"{label} source identity is stale")
    if environment.get("uv_lock_sha256") != uv_lock_sha256:
        raise RuntimeError(f"{label} dependency-lock identity is stale")
    for field in ("packages", "installed_distribution_sha256"):
        if environment.get(field) != manifest_environment.get(field):
            raise RuntimeError(f"{label} {field} differs from the manifest")
    expected_python = {
        key: manifest_environment.get("python", {}).get(key)
        for key in ("implementation", "version")
    }
    observed_python = {
        key: environment.get("python", {}).get(key)
        for key in ("implementation", "version")
    }
    if observed_python != expected_python:
        raise RuntimeError(f"{label} Python identity differs from the manifest")
    if payload.get("study_manifest") != {
        "path": "manifests/study_v1_draft.json",
        "sha256": manifest_sha256,
    }:
        raise RuntimeError(f"{label} does not bind the frozen manifest bytes")


def _reject_machine_paths(path: Path, label: str) -> None:
    payload = path.read_bytes()
    if b"/Users/" in payload or re.search(rb"[A-Za-z]:\\\\Users\\\\", payload):
        raise RuntimeError(f"{label} leaks a machine-specific absolute path")


def _validate_mutation_manifest(
    root: Path,
    *,
    manifest: dict[str, Any],
    source_tree_sha256: str,
    uv_lock_sha256: str,
) -> str:
    """Require the exact unexecuted mutation pool bound by the main manifest."""
    path = root / MUTATION_MANIFEST_PATH
    _reject_machine_paths(path, "mutation manifest")
    payload = _read_json_object(path, "mutation manifest")
    observed_sha256 = _sha256(path)

    evaluation = manifest.get("mutation_evaluation")
    if not isinstance(evaluation, dict):
        raise RuntimeError("study manifest mutation evaluation is missing")
    expected_evaluation = {
        "required_for_primary_study": True,
        "mutation_manifest_path": MUTATION_MANIFEST_PATH,
        "mutation_manifest_sha256": observed_sha256,
        "candidate_pool_count": len(CANDIDATE_POOL),
        "candidate_pool_per_family": POOL_PER_FAMILY,
        "family_count": len(MUTATION_FAMILIES),
        "families": list(MUTATION_FAMILIES),
        "required_eligible_per_family": MUTANTS_PER_FAMILY,
        "required_selected_count": len(MUTATION_FAMILIES) * MUTANTS_PER_FAMILY,
    }
    if any(evaluation.get(key) != value for key, value in expected_evaluation.items()):
        raise RuntimeError(
            "study manifest does not bind the mandatory 48-to-24 mutation cohort"
        )
    study_prearchive = evaluation.get("prearchive_activity")
    if (
        not isinstance(study_prearchive, dict)
        or study_prearchive.get("candidate_or_control_outcomes_executed") != 0
    ):
        raise RuntimeError("study manifest does not prohibit prearchive outcomes")

    if (
        payload.get("schema_version") != 1
        or payload.get("artifact_type")
        != "marlrefine_sealed_mutation_manifest"
        or payload.get("manifest_status") != "frozen_pending_archive"
        or payload.get("protocol_id") != MUTATION_PROTOCOL_ID
        or payload.get("candidates") != list(candidate_manifest_records())
    ):
        raise RuntimeError(
            "sealed mutation manifest identity or candidate pool is invalid"
        )
    prearchive = payload.get("prearchive_activity")
    if (
        not isinstance(prearchive, dict)
        or prearchive.get("candidate_or_control_outcomes_executed") != 0
    ):
        raise RuntimeError("sealed mutation manifest records prearchive execution")
    expected_controls = [
        control.to_manifest_record() for control in PROGRESS_INSTRUMENTATION_CONTROLS
    ]
    if payload.get("progress_instrumentation_controls") != expected_controls:
        raise RuntimeError("sealed mutation progress controls differ from code")
    if payload.get("mutation_engine") != {
        "engine_id": MUTATION_ENGINE_ID,
        "source_module": "src/marlrefine/mutations.py",
        "source_sha256": mutation_engine_source_sha256(),
    }:
        raise RuntimeError("sealed mutation engine identity differs from code")
    expected_contract = build_mutation_manifest(
        manifest_status="draft_not_timestamp_archived"
    )
    for field in (
        "selection",
        "reference_adapter",
        "execution",
        "scoring",
        "prearchive_activity",
    ):
        if payload.get(field) != expected_contract[field]:
            raise RuntimeError(f"sealed mutation {field} contract differs from code")
    selection = payload.get("selection")
    expected_selection = {
        "families": list(MUTATION_FAMILIES),
        "required_eligible_per_family": MUTANTS_PER_FAMILY,
        "candidate_pool_per_family": POOL_PER_FAMILY,
        "required_total": len(MUTATION_FAMILIES) * MUTANTS_PER_FAMILY,
        "candidate_pool_total": len(CANDIDATE_POOL),
    }
    if not isinstance(selection, dict) or any(
        selection.get(key) != value for key, value in expected_selection.items()
    ):
        raise RuntimeError("sealed mutation manifest selection contract is invalid")
    if evaluation.get("selection_rule") != selection.get("replacement_rule"):
        raise RuntimeError("study and mutation manifest selection rules differ")

    manifest_environment = manifest.get("environment")
    mutation_environment = payload.get("environment")
    if not isinstance(manifest_environment, dict) or not isinstance(
        mutation_environment, dict
    ):
        raise RuntimeError("study or mutation manifest environment is missing")
    if (
        mutation_environment.get("source_tree_sha256") != source_tree_sha256
        or mutation_environment.get("uv_lock_sha256") != uv_lock_sha256
        or mutation_environment.get("git_revision")
        != manifest_environment.get("git_revision")
        or mutation_environment.get("git_dirty") is not False
    ):
        raise RuntimeError("mutation manifest source commit A environment is stale")
    for field in ("python", "packages", "installed_distribution_sha256"):
        if mutation_environment.get(field) != manifest_environment.get(field):
            raise RuntimeError(f"mutation manifest {field} differs from study manifest")
    return observed_sha256


def _validate_discovery_artifacts(
    root: Path,
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    source_tree_sha256: str,
    uv_lock_sha256: str,
) -> None:
    manifest_environment = manifest.get("environment")
    if not isinstance(manifest_environment, dict):
        raise RuntimeError("study manifest environment is missing")

    payloads: dict[str, dict[str, Any]] = {}
    for relative in DISCOVERY_ARTIFACTS:
        path = root / relative
        _reject_machine_paths(path, relative)
        if relative.endswith("pilot.jsonl"):
            continue
        payload = _read_json_object(path, relative)
        expected_type, expected_schema = ARTIFACT_IDENTITIES[relative]
        if (
            payload.get("artifact_type") != expected_type
            or payload.get("schema_version") != expected_schema
        ):
            raise RuntimeError(f"{relative} artifact identity is invalid")
        _validate_environment(
            payload,
            label=relative,
            manifest_environment=manifest_environment,
            manifest_sha256=manifest_sha256,
            source_tree_sha256=source_tree_sha256,
            uv_lock_sha256=uv_lock_sha256,
        )
        payloads[relative] = payload

    controls = payloads["artifacts/discovery_controls.json"]
    runs = controls.get("runs")
    if controls.get("all_passed") is not True or not isinstance(runs, list):
        raise RuntimeError("discovery controls are not an all-passing panel")
    if (
        len(runs) != len(CONTROL_IDS)
        or {run.get("control_id") for run in runs if isinstance(run, dict)}
        != CONTROL_IDS
    ):
        raise RuntimeError("discovery controls do not contain the frozen panel")
    if any(not isinstance(run, dict) or run.get("violations") != [] for run in runs):
        raise RuntimeError("one or more discovery controls contain an alarm")

    census = payloads["artifacts/registry_census.json"]
    records = census.get("records")
    population = manifest.get("population", {})
    if (
        census.get("population_size") != 113
        or not isinstance(records, list)
        or len(records) != 113
        or [record.get("short_name") for record in records if isinstance(record, dict)]
        != population.get("names")
    ):
        raise RuntimeError("registry census does not equal the frozen population")

    api_results = payloads["artifacts/discovery_api_baselines.json"].get("results")
    discovery_names = manifest.get("discovery", {}).get("names")
    if (
        not isinstance(api_results, list)
        or [
            result.get("game_spec")
            for result in api_results
            if isinstance(result, dict)
        ]
        != discovery_names
    ):
        raise RuntimeError("discovery API baseline panel differs from the manifest")

    repairs = payloads["artifacts/discovery_repairs.json"]
    repair_results = repairs.get("results")
    combined = repairs.get("combined_regression")
    if (
        not isinstance(repair_results, list)
        or len(repair_results) != 6
        or any(
            not isinstance(result, dict)
            or result.get("targeted_mechanism_removed") is not True
            or result.get("treatment_outcome_valid") is not True
            or result.get("unexpected_codes_after") != []
            for result in repair_results
        )
        or not isinstance(combined, dict)
        or combined.get("all_passed") is not True
        or not isinstance(combined.get("runs"), list)
        or len(combined.get("runs", [])) != 4
        or any(
            not isinstance(run, dict)
            or run.get("applicable") is not True
            or run.get("violations") != []
            for run in combined.get("runs", [])
        )
    ):
        raise RuntimeError("discovery repair evidence is incomplete or failing")

    pilot_path = root / "artifacts/pilot.jsonl"
    pilot_bytes = pilot_path.read_bytes()
    if not pilot_bytes.endswith(b"\n") or b"\r" in pilot_bytes:
        raise RuntimeError("pilot JSONL is not LF-terminated canonical text")
    pilot_records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        pilot_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            record = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON constant {token}")
                ),
            )
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"invalid pilot JSONL line {line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise RuntimeError(f"pilot JSONL line {line_number} is not an object")
        canonical = json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if line != canonical:
            raise RuntimeError(f"pilot JSONL line {line_number} is not canonical")
        if (
            record.get("artifact_type") != "marlrefine_trace_run"
            or record.get("schema_version") != 1
            or not isinstance(record.get("run"), dict)
        ):
            raise RuntimeError(f"pilot JSONL line {line_number} identity is invalid")
        _validate_environment(
            record,
            label=f"pilot line {line_number}",
            manifest_environment=manifest_environment,
            manifest_sha256=manifest_sha256,
            source_tree_sha256=source_tree_sha256,
            uv_lock_sha256=uv_lock_sha256,
        )
        pilot_records.append(record)
    if (
        len(pilot_records) != len(PILOT_CASE_IDS)
        or {record.get("case_id") for record in pilot_records} != PILOT_CASE_IDS
    ):
        raise RuntimeError("pilot JSONL does not contain the frozen discovery cases")


def _validate_image_identity(
    root: Path,
    *,
    image_archive_path: Path,
    manifest: dict[str, Any],
    manifest_sha256: str,
    source_tree_sha256: str,
    uv_lock_sha256: str,
) -> dict[str, Any]:
    path = root / "container/IMAGE_IDENTITY.json"
    _reject_machine_paths(path, "container image identity")
    identity = _read_json_object(path, "container image identity")
    if set(identity) != IMAGE_IDENTITY_KEYS:
        raise RuntimeError("container image identity keys differ from schema 2")
    environment = manifest.get("environment")
    runtime = identity.get("container_runtime")
    if not isinstance(environment, dict) or not isinstance(runtime, dict):
        raise RuntimeError("container or manifest runtime identity is missing")
    if (
        type(identity.get("schema_version")) is not int
        or identity.get("schema_version") != 2
    ):
        raise RuntimeError("container image identity schema is invalid")
    base_image = identity.get("base_image")
    if not isinstance(base_image, str) or re.fullmatch(
        r"[^\s@]+@sha256:[0-9a-f]{64}", base_image
    ) is None:
        raise RuntimeError("container base image identity is invalid")
    image_reference = identity.get("image_reference")
    if not isinstance(image_reference, str) or re.fullmatch(
        r"\S+", image_reference
    ) is None:
        raise RuntimeError("container image reference is invalid")
    if set(runtime) != CONTAINER_RUNTIME_KEYS:
        raise RuntimeError("container runtime keys differ from schema 2")
    platform = identity.get("platform")
    if (
        not isinstance(platform, dict)
        or set(platform) != CONTAINER_PLATFORM_KEYS
        or any(not isinstance(value, str) or not value for value in platform.values())
    ):
        raise RuntimeError("container platform identity is invalid")
    if identity.get("verification_status") != "tests_passed":
        raise RuntimeError("container image was not recorded as test-passing")
    image_id = identity.get("image_id")
    if not isinstance(image_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_id
    ):
        raise RuntimeError("container image ID is invalid")
    archive = identity.get("image_archive")
    if not isinstance(archive, dict) or set(archive) != {
        "format",
        "filename",
        "sha256",
        "size_bytes",
    }:
        raise RuntimeError("container image archive identity is invalid")
    if (
        archive.get("format") != IMAGE_ARCHIVE_FORMAT
        or archive.get("filename") != IMAGE_ARCHIVE_FILENAME
        or not isinstance(archive.get("size_bytes"), int)
        or isinstance(archive.get("size_bytes"), bool)
        or archive.get("size_bytes", 0) <= 0
    ):
        raise RuntimeError("container image archive identity is invalid")
    archive_sha256 = _require_sha256(
        archive.get("sha256"), "container image archive identity"
    )
    if not image_archive_path.is_file():
        raise RuntimeError(f"Docker image archive is missing: {image_archive_path}")
    if image_archive_path.name != archive["filename"]:
        raise RuntimeError("Docker image archive filename differs from its identity")
    if (
        image_archive_path.stat().st_size != archive["size_bytes"]
        or _sha256(image_archive_path) != archive_sha256
    ):
        raise RuntimeError("Docker image archive differs from its identity")
    try:
        content_identity = validate_docker_image_archive(
            image_archive_path,
            image_id=image_id,
            expected_platform=platform,
        )
    except ImageArchiveError as exc:
        raise RuntimeError(f"invalid Docker image archive: {exc}") from exc
    if (
        identity.get("image_id_kind") != OCI_MANIFEST_IMAGE_ID_KIND
        or identity.get("image_manifest_digest")
        != content_identity["image_manifest_digest"]
        or identity.get("image_config_digest")
        != content_identity["image_config_digest"]
    ):
        raise RuntimeError("Docker image archive content identity differs")
    if identity.get("source_tree_sha256") != source_tree_sha256:
        raise RuntimeError("container image source identity is stale")
    if identity.get("dockerfile_sha256") != _sha256(root / "Dockerfile"):
        raise RuntimeError("container image Dockerfile identity is stale")
    if identity.get("study_manifest") != {
        "path": "manifests/study_v1_draft.json",
        "sha256": manifest_sha256,
    }:
        raise RuntimeError("container image does not bind the frozen manifest")
    if runtime.get("source_tree_sha256") != source_tree_sha256:
        raise RuntimeError("container runtime source identity is stale")
    if runtime.get("uv_lock_sha256") != uv_lock_sha256:
        raise RuntimeError("container runtime lock identity is stale")
    if runtime.get("packages") != environment.get("packages"):
        raise RuntimeError("container runtime packages differ from the manifest")
    if runtime.get("installed_distribution_sha256") != environment.get(
        "installed_distribution_sha256"
    ):
        raise RuntimeError("container distribution bytes differ from the manifest")
    manifest_python = environment.get("python")
    runtime_python = runtime.get("python")
    if not isinstance(manifest_python, dict) or not isinstance(runtime_python, dict):
        raise RuntimeError("container or manifest Python identity is invalid")
    expected_python = {
        key: manifest_python.get(key)
        for key in ("implementation", "version", "executable_name")
    }
    observed_python = {
        key: runtime_python.get(key)
        for key in ("implementation", "version", "executable_name")
    }
    if observed_python != expected_python:
        raise RuntimeError("container Python identity differs from the manifest")
    _require_sha256(
        identity.get("verification_output_sha256"),
        "container verification output identity",
    )
    if (
        identity.get("verification_output_normalization")
        != VERIFICATION_OUTPUT_NORMALIZATION
        or identity.get("verification_command") != f"docker run --rm {image_id}"
    ):
        raise RuntimeError("container verification attestation is invalid")
    repo_digests = identity.get("repo_digests")
    if (
        not isinstance(repo_digests, list)
        or repo_digests != sorted(set(repo_digests))
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", item) is None
            for item in repo_digests
        )
    ):
        raise RuntimeError("container repository digests are invalid")
    return dict(archive)


def _validate_freeze_inputs(root: Path, image_archive_path: Path) -> dict[str, Any]:
    if not (root / ".git").is_dir():
        raise RuntimeError("freeze bundle requires an initialized Git repository")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("freeze bundle requires a completely clean Git worktree")

    metadata_path = root / "deposit/zenodo_metadata.json"
    _validate_zenodo_metadata(metadata_path)
    metadata_text = metadata_path.read_text(encoding="utf-8")
    if "REPLACE_BEFORE_DEPOSIT" in metadata_text:
        raise RuntimeError("Zenodo metadata still contains replacement placeholders")
    _validate_contract_evidence(root / "docs/contract_evidence.md")

    required = tuple(root / path for path in (*ROOT_FILES, *DISCOVERY_ARTIFACTS))
    required += (root / "container/IMAGE_IDENTITY.json",)
    required += (root / MUTATION_MANIFEST_PATH,)
    missing = tuple(
        str(path.relative_to(root)) for path in required if not path.is_file()
    )
    if missing:
        raise RuntimeError(f"freeze inputs are missing: {missing}")

    manifest_path = root / "manifests/study_v1_draft.json"
    manifest = _read_json_object(manifest_path, "study manifest")
    if manifest.get("manifest_status") != "frozen_pending_archive":
        raise RuntimeError("study manifest is not a freeze candidate")
    if manifest.get("schema_version") != 2:
        raise RuntimeError("study manifest schema is not 2")
    if manifest.get("execution_contract") != prospective_execution_contract():
        raise RuntimeError("study manifest execution contract is stale")
    if manifest.get("external_baselines") != external_baseline_protocol():
        raise RuntimeError("study manifest external-baseline contract is stale")

    provenance = runtime_provenance()
    manifest_environment = manifest.get("environment", {})
    if not isinstance(manifest_environment, dict):
        raise RuntimeError("study manifest environment is missing")
    distribution_hashes = manifest_environment.get("installed_distribution_sha256")
    if (
        not isinstance(distribution_hashes, dict)
        or set(distribution_hashes) != set(BYTE_BOUND_DISTRIBUTIONS)
        or any(
            not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value)
            for value in distribution_hashes.values()
        )
    ):
        raise RuntimeError("study manifest distribution-byte identity is incomplete")
    recorded_source_hash = manifest.get("environment", {}).get("source_tree_sha256")
    if recorded_source_hash != provenance["source_tree_sha256"]:
        raise RuntimeError("study manifest source hash is stale")
    uv_lock_sha256 = _sha256(root / "uv.lock")
    if manifest_environment.get("uv_lock_sha256") != uv_lock_sha256:
        raise RuntimeError("study manifest dependency-lock hash is stale")
    mutation_manifest_sha256 = _validate_mutation_manifest(
        root,
        manifest=manifest,
        source_tree_sha256=provenance["source_tree_sha256"],
        uv_lock_sha256=uv_lock_sha256,
    )
    manifest_sha256 = _sha256(manifest_path)
    _validate_discovery_artifacts(
        root,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        source_tree_sha256=provenance["source_tree_sha256"],
        uv_lock_sha256=uv_lock_sha256,
    )
    image_archive = _validate_image_identity(
        root,
        image_archive_path=image_archive_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        source_tree_sha256=provenance["source_tree_sha256"],
        uv_lock_sha256=uv_lock_sha256,
    )
    archive_revision = _git(root, "rev-parse", "HEAD")
    source_parent = _git(root, "rev-parse", "HEAD^")
    changed_paths = _validate_generated_evidence_commit(
        _git(
            root,
            "diff",
            "--name-status",
            "--no-renames",
            f"{source_parent}..{archive_revision}",
        )
    )
    try:
        git_identity = two_commit_freeze_identity(
            manifest_environment,
            source_parent_revision=source_parent,
            archive_revision=archive_revision,
            changed_paths=changed_paths,
            allowed_generated_paths=FROZEN_GENERATED_PATHS,
            required_manifest_path="manifests/study_v1_draft.json",
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return {
        **git_identity,
        "container_image_archive": image_archive,
        "manifest_sha256": manifest_sha256,
        "mutation_manifest_sha256": mutation_manifest_sha256,
        "source_tree_sha256": provenance["source_tree_sha256"],
        "uv_lock_sha256": uv_lock_sha256,
    }


def _iter_source_paths(root: Path):
    yield from source_identity_paths(root)
    for relative in (
        "manifests/study_v1_draft.json",
        MUTATION_MANIFEST_PATH,
        *DISCOVERY_ARTIFACTS,
        "container/IMAGE_IDENTITY.json",
    ):
        yield root / relative


def _copy_inputs(root: Path, staging: Path) -> None:
    seen: set[Path] = set()
    for source in _iter_source_paths(root):
        relative = source.relative_to(root)
        if relative in seen:
            continue
        seen.add(relative)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _write_checksums(staging: Path) -> None:
    paths = sorted(
        path
        for path in staging.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = (
        f"{_sha256(path)}  {path.relative_to(staging).as_posix()}\n" for path in paths
    )
    (staging / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def _normalized_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if info.isdir():
        info.mode = 0o755
    elif info.issym() or info.islnk():
        info.mode = 0o777
    elif info.isfile():
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def _write_deterministic_archive(staging: Path, output: Path) -> None:
    root_name = staging.name
    with (
        output.open("wb") as raw_handle,
        gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_handle,
            mtime=0,
        ) as gzip_handle,
        tarfile.open(
            fileobj=gzip_handle,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as archive,
    ):
        archive.add(
            staging,
            arcname=root_name,
            recursive=False,
            filter=_normalized_tar_info,
        )
        for path in sorted(
            staging.rglob("*"),
            key=lambda item: item.relative_to(staging).as_posix(),
        ):
            archive.add(
                path,
                arcname=(f"{root_name}/{path.relative_to(staging).as_posix()}"),
                recursive=False,
                filter=_normalized_tar_info,
            )


def _protocol_identity(freeze: dict[str, Any], bundle: Path) -> dict[str, Any]:
    """Bind the completed bundle to the precomputed freeze identities."""
    return protocol_freeze_identity(
        manifest_sha256=freeze["manifest_sha256"],
        source_tree_sha256=freeze["source_tree_sha256"],
        uv_lock_sha256=freeze["uv_lock_sha256"],
        bundle_filename=bundle.name,
        bundle_sha256=_sha256(bundle),
    )


def build_bundle(
    root: Path,
    output: Path,
    *,
    image_archive: Path,
    identity_output: Path | None = None,
) -> Path:
    """Validate, stage, checksum, and atomically publish one tar archive."""
    freeze = _validate_freeze_inputs(root, image_archive)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing bundle: {output}")
    if identity_output is not None and identity_output.exists():
        raise FileExistsError(
            f"refusing to overwrite existing identity: {identity_output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / "marl-adapter-conformance-protocol-v1.1"
        staging.mkdir()
        _copy_inputs(root, staging)
        write_json(staging / "FREEZE_METADATA.json", freeze)
        _write_checksums(staging)
        temporary_archive = Path(temporary) / output.name
        _write_deterministic_archive(staging, temporary_archive)
        temporary_archive.replace(output)
    if identity_output is not None:
        write_json(identity_output, _protocol_identity(freeze, output))
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image-archive",
        type=Path,
        default=Path(
            "dist/private-execution-image/"
            "marl-adapter-conformance-protocol-v1.docker.tar"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/marl-adapter-conformance-protocol-v1.1.tar.gz"),
    )
    parser.add_argument(
        "--identity-output",
        type=Path,
        default=Path("dist/protocol_identity.json"),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    identity_output = args.identity_output.resolve()
    output = build_bundle(
        root,
        args.output.resolve(),
        image_archive=args.image_archive.resolve(),
        identity_output=identity_output,
    )
    print(
        f"wrote {output} sha256={_sha256(output)}; "
        f"identity={identity_output} sha256={_sha256(identity_output)}"
    )


if __name__ == "__main__":
    main()
