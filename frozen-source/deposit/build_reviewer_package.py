#!/usr/bin/env python3
"""Build and independently verify a sealed post-run reviewer package."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from marlrefine.image_archive import (
    DOCKER_SAVE_OCI_LAYOUT_FORMAT,
    OCI_MANIFEST_IMAGE_ID_KIND,
    ImageArchiveError,
    validate_docker_image_archive,
)

SCHEMA_VERSION = 1
PACKAGE_PREFIX = "marlrefine-reviewer-package-v1"
PROTOCOL_ROOT_NAME = "marl-adapter-conformance-protocol-v1.1"
PROTOCOL_IMAGE_ARCHIVE_FILENAME = (
    "marl-adapter-conformance-protocol-v1.docker.tar"
)
IMAGE_ARCHIVE_FORMAT = DOCKER_SAVE_OCI_LAYOUT_FORMAT
VERIFICATION_OUTPUT_NORMALIZATION = "uv_bytecode_and_pytest_elapsed_redacted_lf_v1"
MANIFEST_NAME = f"{PACKAGE_PREFIX}/MANIFEST.json"
CHECKSUMS_NAME = f"{PACKAGE_PREFIX}/SHA256SUMS"
PAYLOAD_PREFIX = f"{PACKAGE_PREFIX}/files/"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
EVIDENCE_ROLE_PREFIX = "evidence:"
MAX_JSON_BYTES = 256 * 1024 * 1024
MAX_NESTED_BUNDLE_BYTES = 4 * 1024 * 1024 * 1024
MAX_NESTED_BUNDLE_MEMBERS = 50_000
MAX_PACKAGE_PAYLOAD_BYTES = 128 * 1024 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = MAX_PACKAGE_PAYLOAD_BYTES + (1024**3)
MIN_BUILD_HEADROOM_BYTES = 64 * 1024 * 1024

REQUIRED_SINGLETON_ROLES = (
    "pre_run_bundle",
    "pre_run_identity",
    "archive_receipt",
    "archive_gate_log",
    "raw_batch",
    "external_baselines",
    "mutation_batch",
    "frozen_analysis",
    "latex_macros",
    "manual_adjudication",
    "container_identity",
    "container_image_archive",
    "reproduction_readme",
    "deviation_log",
    "run_diary",
)
REQUIRED_ROLE_SET = frozenset(REQUIRED_SINGLETON_ROLES)
PROTOCOL_GENERATED_PATHS = frozenset(
    {
        "artifacts/discovery_api_baselines.json",
        "artifacts/discovery_controls.json",
        "artifacts/discovery_repairs.json",
        "artifacts/pilot.jsonl",
        "artifacts/registry_census.json",
        "container/IMAGE_IDENTITY.json",
        "manifests/mutation_v1.json",
        "manifests/study_v1_draft.json",
    }
)
PROTOCOL_FREEZE_KEYS = frozenset(
    {
        "archive_git_revision",
        "container_image_archive",
        "generated_evidence_paths",
        "git_identity_model",
        "manifest_sha256",
        "mutation_manifest_sha256",
        "source_git_revision",
        "source_tree_sha256",
        "uv_lock_sha256",
    }
)
CONTAINER_IDENTITY_KEYS = frozenset(
    {
        "base_image",
        "container_runtime",
        "dockerfile_sha256",
        "image_config_digest",
        "image_id",
        "image_id_kind",
        "image_manifest_digest",
        "image_archive",
        "image_reference",
        "platform",
        "repo_digests",
        "schema_version",
        "source_tree_sha256",
        "study_manifest",
        "verification_command",
        "verification_output_sha256",
        "verification_output_normalization",
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
STUDY_DOCKER_ENGINE = "Docker Engine 29.5.2 with containerd image store"

_MANUAL_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "raw_batch_sha256",
        "status",
        "roots",
        "finding_dispositions",
        "controls",
        "optional_measurements",
    }
)
_REQUIRED_CONTROL_IDS = frozenset(
    {
        "native_clone_replay_v1",
        "openspiel_turn_based_simultaneous_v1",
        "pettingzoo_parallel_to_aec_v1",
    }
)

# These identify workstation/user-specific paths, not portable container paths
# such as /run/archive_receipt.json.
_MACHINE_PATH_PATTERNS = (
    re.compile(rb"file:///(?:Users|home|tmp|private/(?:tmp|var/folders))/"),
    re.compile(rb"(?<![A-Za-z0-9])/(?:Users|home)/[^/\s\"']+/"),
    re.compile(rb"(?<![A-Za-z0-9])/(?:tmp|private/(?:tmp|var/folders))/"),
    re.compile(rb"(?i)(?<![A-Za-z0-9])[A-Z]:\\(?:Users|Documents and Settings)\\"),
)
_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".bz2",
        ".gz",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".tar",
        ".whl",
        ".xz",
        ".zip",
    }
)


class ReviewerPackageError(ValueError):
    """A reviewer-package input or archive violates the sealed format."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewerPackageError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReviewerPackageError(f"non-finite JSON number: {value}")


def _loads_json(payload: bytes, label: str) -> Any:
    if len(payload) > MAX_JSON_BYTES:
        raise ReviewerPackageError(f"{label} exceeds the JSON safety limit")
    try:
        text = payload.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ReviewerPackageError) as exc:
        raise ReviewerPackageError(f"invalid JSON in {label}: {exc}") from exc


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    value = _loads_json(payload, label)
    if not isinstance(value, dict):
        raise ReviewerPackageError(f"{label} must contain one JSON object")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewerPackageError(f"value is not canonical JSON: {exc}") from exc
    return f"{text}\n".encode()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise ReviewerPackageError(f"cannot hash {path.name}: {exc}") from exc
    return digest.hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ReviewerPackageError(f"{label} must be a lowercase SHA-256")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewerPackageError(f"{label} must be an object")
    return value


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewerPackageError(f"{label} must be a non-empty relative path")
    if "\\" in value or "\x00" in value or value.startswith("~"):
        raise ReviewerPackageError(f"{label} is not a canonical POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise ReviewerPackageError(f"{label} is not a canonical relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ReviewerPackageError(f"{label} contains traversal or empty parts")
    if re.match(r"^[A-Za-z]:", value):
        raise ReviewerPackageError(f"{label} contains a drive-qualified path")
    if path.name.casefold() == "reviewer_package_identity.tex":
        raise ReviewerPackageError(
            f"{label} names the post-seal paper identity overlay"
        )
    return value


def _archive_relative_path(value: str, label: str) -> str:
    result = _relative_path(value, label)
    if result.startswith("/"):
        raise ReviewerPackageError(f"{label} is absolute")
    return result


def _validate_role(value: Any, label: str) -> str:
    if not isinstance(value, str) or not ROLE_PATTERN.fullmatch(value):
        raise ReviewerPackageError(f"{label} is not a stable role identifier")
    if value not in REQUIRED_ROLE_SET and not value.startswith(
        EVIDENCE_ROLE_PREFIX
    ):
        raise ReviewerPackageError(f"unexpected singleton role: {value}")
    if value.startswith(EVIDENCE_ROLE_PREFIX) and len(value) == len(
        EVIDENCE_ROLE_PREFIX
    ):
        raise ReviewerPackageError("evidence roles require a unique suffix")
    return value


def _validate_inventory_object(value: Mapping[str, Any]) -> list[dict[str, str]]:
    if frozenset(value) != frozenset(
        {"schema_version", "artifact_type", "entries"}
    ):
        raise ReviewerPackageError("inventory keys differ from schema version 1")
    if type(value.get("schema_version")) is not int or value.get(
        "schema_version"
    ) != SCHEMA_VERSION or value.get(
        "artifact_type"
    ) != "marlrefine_reviewer_package_inventory":
        raise ReviewerPackageError("inventory schema or artifact type differs")
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        raise ReviewerPackageError("inventory entries must be an array")
    if len(raw_entries) > MAX_NESTED_BUNDLE_MEMBERS:
        raise ReviewerPackageError("inventory has too many entries")
    entries: list[dict[str, str]] = []
    paths: set[str] = set()
    roles: set[str] = set()
    for index, raw_entry in enumerate(raw_entries):
        entry = _mapping(raw_entry, f"inventory entries[{index}]")
        if frozenset(entry) != frozenset({"path", "role", "sha256"}):
            raise ReviewerPackageError(
                f"inventory entries[{index}] keys differ from schema"
            )
        path = _relative_path(entry.get("path"), f"inventory entries[{index}].path")
        role = _validate_role(entry.get("role"), f"inventory entries[{index}].role")
        digest = _sha(
            entry.get("sha256"), f"inventory entries[{index}].sha256"
        )
        if path in paths:
            raise ReviewerPackageError(f"duplicate inventory path: {path}")
        if role in roles:
            raise ReviewerPackageError(f"duplicate inventory role: {role}")
        paths.add(path)
        roles.add(role)
        entries.append({"path": path, "role": role, "sha256": digest})
    missing = sorted(REQUIRED_ROLE_SET - roles)
    if missing:
        raise ReviewerPackageError(f"inventory is missing required roles: {missing}")
    return sorted(entries, key=lambda item: item["path"])


def _read_inventory(path: Path) -> list[dict[str, str]]:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ReviewerPackageError(f"cannot inspect inventory: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ReviewerPackageError("inventory must be a regular non-symlink file")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ReviewerPackageError(f"cannot read inventory: {exc}") from exc
    value = _json_object(payload, "inventory")
    if payload != _canonical_json_bytes(value):
        raise ReviewerPackageError("inventory is not canonical JSON")
    return _validate_inventory_object(value)


def _assert_regular_unlinked_file(root: Path, relative: str) -> Path:
    root = root.resolve(strict=True)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise ReviewerPackageError(
                f"missing inventory path {relative}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ReviewerPackageError(f"inventory path uses a symlink: {relative}")
    if not stat.S_ISREG(mode):
        raise ReviewerPackageError(f"inventory path is not a regular file: {relative}")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ReviewerPackageError(
            f"inventory path escapes the root: {relative}"
        ) from exc
    return current


def _scan_machine_paths(payload: bytes, label: str, root_marker: bytes | None) -> None:
    # The deposited negative tests deliberately contain these canonical fake
    # workstation paths. They are fixtures, not captured host provenance.
    sanitized = (
        payload.replace(b"/Users/example/", b"/fixture/example/")
        .replace(b"/home/example/", b"/fixture/example/")
        .replace(b"C:\\Users\\example\\", b"C:\\fixture\\example\\")
    )
    for pattern in _MACHINE_PATH_PATTERNS:
        if pattern.search(sanitized):
            raise ReviewerPackageError(f"machine-specific path found in {label}")
    if root_marker and root_marker in payload:
        raise ReviewerPackageError(f"package root path leaked into {label}")


def _is_text_path(path: str) -> bool:
    return Path(path).suffix.lower() not in _BINARY_SUFFIXES


def _is_text_entry(entry: Mapping[str, Any]) -> bool:
    return _is_text_path(str(entry["path"]))


def _validate_source_entries(
    root: Path,
    entries: Sequence[dict[str, str]],
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    root_marker = str(root.resolve(strict=True)).encode()
    for entry in entries:
        source = _assert_regular_unlinked_file(root, entry["path"])
        digest = hashlib.sha256()
        scanned = b""
        size = 0
        try:
            with source.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    size += len(block)
                    digest.update(block)
                    if _is_text_entry(entry):
                        window = scanned[-512:] + block
                        _scan_machine_paths(window, entry["path"], root_marker)
                        scanned = window
        except OSError as exc:
            raise ReviewerPackageError(f"cannot read {entry['path']}: {exc}") from exc
        if digest.hexdigest() != entry["sha256"]:
            raise ReviewerPackageError(
                f"inventory SHA-256 differs for {entry['path']}"
            )
        validated.append(
            {
                **entry,
                "archive_path": f"{PAYLOAD_PREFIX}{entry['path']}",
                "size_bytes": size,
                "source": source,
            }
        )
    return validated


def _preflight_output_space(
    output_parent: Path, entries: Sequence[Mapping[str, Any]]
) -> None:
    payload_bytes = sum(int(entry["size_bytes"]) for entry in entries)
    if payload_bytes > MAX_PACKAGE_PAYLOAD_BYTES:
        raise ReviewerPackageError("reviewer package payload exceeds the size limit")
    required = (2 * payload_bytes) + max(
        payload_bytes // 10, MIN_BUILD_HEADROOM_BYTES
    )
    try:
        free = shutil.disk_usage(output_parent).free
    except OSError as exc:
        raise ReviewerPackageError(
            f"cannot measure package output space: {exc}"
        ) from exc
    if free < required:
        raise ReviewerPackageError(
            "insufficient free space for deterministic reviewer-package staging: "
            f"need {required} bytes, found {free}"
        )


def _role_entries(entries: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(entry["role"]): entry for entry in entries}


def _read_limited(path: Path, label: str, limit: int = MAX_JSON_BYTES) -> bytes:
    try:
        if path.stat().st_size > limit:
            raise ReviewerPackageError(f"{label} exceeds the safety limit")
        return path.read_bytes()
    except OSError as exc:
        raise ReviewerPackageError(f"cannot read {label}: {exc}") from exc


def _read_role_json(
    by_role: Mapping[str, Mapping[str, Any]], role: str
) -> dict[str, Any]:
    entry = by_role[role]
    source = entry.get("source")
    if not isinstance(source, Path):
        raise ReviewerPackageError(f"internal source missing for role {role}")
    payload = _read_limited(source, role)
    value = _json_object(payload, role)
    if payload != _canonical_json_bytes(value):
        raise ReviewerPackageError(f"{role} is not canonical JSON")
    return value


def _extract_identity_sha(value: Any, label: str) -> str:
    if isinstance(value, Mapping):
        return _sha(value.get("sha256"), f"{label}.sha256")
    return _sha(value, label)


def _validate_analysis_bindings(
    analysis: Mapping[str, Any], role_hashes: Mapping[str, str]
) -> None:
    try:
        from marlrefine.analysis import ANALYSIS_ID, ANALYSIS_SCHEMA_VERSION
    except ImportError as exc:
        raise ReviewerPackageError(
            "cannot import the frozen analysis schema constants"
        ) from exc
    if (
        analysis.get("artifact_type") != "marlrefine_frozen_prospective_analysis"
        or type(analysis.get("schema_version")) is not int
        or analysis.get("schema_version") != ANALYSIS_SCHEMA_VERSION
        or analysis.get("analysis_id") != ANALYSIS_ID
    ):
        raise ReviewerPackageError("frozen analysis schema, type, or ID differs")
    identities = _mapping(
        analysis.get("input_identities"), "analysis input_identities"
    )
    # Each accepted spelling still has one mandatory target and exact hash. This
    # permits the final explicit mapping to rename a key without accepting an
    # unbound input.
    aliases = {
        "raw_batch": ("raw_batch", "raw_batch_sha256"),
        "archive_receipt": ("archive_receipt", "archive_receipt_sha256"),
        "manual_adjudication": (
            "manual_adjudication",
            "manual_adjudication_sha256",
        ),
        "external_baselines": (
            "external_baselines",
            "external_baselines_sha256",
            "external_baseline_sha256",
        ),
        "mutation_batch": ("mutation_batch", "mutation_batch_sha256"),
    }
    for role, names in aliases.items():
        present = [name for name in names if name in identities]
        if len(present) != 1:
            raise ReviewerPackageError(
                f"analysis must carry exactly one explicit {role} identity"
            )
        observed = _extract_identity_sha(
            identities[present[0]], f"analysis input_identities.{present[0]}"
        )
        if observed != role_hashes[role]:
            raise ReviewerPackageError(f"analysis {role} identity differs")
    manual = _mapping(
        analysis.get("manual_adjudication"), "analysis manual_adjudication"
    )
    if manual.get("status") != "complete":
        raise ReviewerPackageError("analysis manual adjudication is not complete")
    source = _mapping(manual.get("source"), "analysis manual source")
    if _sha(source.get("sha256"), "analysis manual source SHA-256") != role_hashes[
        "manual_adjudication"
    ]:
        raise ReviewerPackageError("analysis manual source identity differs")


def _validate_analysis_runtime(
    analysis: Mapping[str, Any], protocol_root: Path
) -> None:
    batch_runtime = _mapping(analysis.get("runtime"), "analysis batch runtime")
    analysis_runtime = _mapping(
        analysis.get("analysis_runtime"), "analysis execution runtime"
    )
    batch_stable = _stable_runtime(batch_runtime, "batch runtime")
    analysis_stable = _stable_runtime(analysis_runtime, "analysis execution runtime")
    if analysis_stable != batch_stable:
        raise ReviewerPackageError(
            "analysis execution runtime differs from the stable batch runtime"
        )
    live_runtime = _runtime_with_frozen_source(protocol_root)
    live_stable = _stable_runtime(live_runtime, "live frozen-source verifier runtime")
    if live_stable != batch_stable:
        raise ReviewerPackageError(
            "live frozen-source verifier runtime differs from the batch runtime"
        )
    extracted_source_sha, _ = _protocol_source_tree_sha256(protocol_root)
    if extracted_source_sha != batch_stable["source_tree_sha256"]:
        raise ReviewerPackageError(
            "extracted frozen analysis source differs from the batch runtime"
        )


def _stable_runtime(
    value: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    expected_keys = {
        "created_at_utc",
        "python",
        "platform",
        "packages",
        "installed_distribution_sha256",
        "uv_lock_sha256",
        "source_identity_scope",
        "source_tree_sha256",
        "git_revision",
        "git_dirty",
    }
    if set(value) != expected_keys:
        raise ReviewerPackageError(f"{label} keys differ from runtime schema")
    created = value.get("created_at_utc")
    if not isinstance(created, str):
        raise ReviewerPackageError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewerPackageError(f"{label} timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ReviewerPackageError(f"{label} timestamp is not UTC")
    python = _mapping(value.get("python"), f"{label} Python")
    if set(python) != {"implementation", "version", "executable_name"} or not all(
        isinstance(item, str) and item for item in python.values()
    ):
        raise ReviewerPackageError(f"{label} Python identity is invalid")
    platform = _mapping(value.get("platform"), f"{label} platform")
    if set(platform) != {"system", "release", "machine", "platform"} or not all(
        isinstance(item, str) and item for item in platform.values()
    ):
        raise ReviewerPackageError(f"{label} platform identity is invalid")
    packages = _mapping(value.get("packages"), f"{label} packages")
    if not packages or any(
        not isinstance(name, str)
        or not name
        or (version is not None and not isinstance(version, str))
        for name, version in packages.items()
    ):
        raise ReviewerPackageError(f"{label} package identity is invalid")
    distributions = _mapping(
        value.get("installed_distribution_sha256"), f"{label} distributions"
    )
    if not distributions or any(
        not isinstance(name, str)
        or not name
        or not isinstance(digest, str)
        or not SHA256_PATTERN.fullmatch(digest)
        for name, digest in distributions.items()
    ):
        raise ReviewerPackageError(f"{label} distribution identity is invalid")
    source_scope = value.get("source_identity_scope")
    if source_scope not in {"project_tree", "package_tree"}:
        raise ReviewerPackageError(f"{label} source identity scope is invalid")
    source_sha = _sha(value.get("source_tree_sha256"), f"{label} source tree")
    lock_sha = _sha(value.get("uv_lock_sha256"), f"{label} lockfile")
    revision = value.get("git_revision")
    dirty = value.get("git_dirty")
    if revision is None and dirty is None:
        pass
    else:
        if not isinstance(revision, str) or not re.fullmatch(
            r"[0-9a-f]{40}", revision
        ):
            raise ReviewerPackageError(f"{label} Git revision is invalid")
        if type(dirty) is not bool:
            raise ReviewerPackageError(f"{label} Git dirty flag is invalid")
    return {
        "git_dirty": dirty,
        "git_revision": revision,
        "installed_distribution_sha256": dict(distributions),
        "packages": dict(packages),
        "platform": dict(platform),
        "python": dict(python),
        "source_identity_scope": source_scope,
        "source_tree_sha256": source_sha,
        "uv_lock_sha256": lock_sha,
    }


def _run_frozen_python(
    protocol_root: Path,
    script: str,
    arguments: Sequence[Path],
    *,
    label: str,
) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        "-I",
        "-c",
        script,
        str(protocol_root / "src"),
        *(str(path) for path in arguments),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=protocol_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewerPackageError(f"{label} failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        suffix = detail[-1] if detail else f"exit {result.returncode}"
        raise ReviewerPackageError(f"{label} failed: {suffix}")
    if result.stdout:
        raise ReviewerPackageError(f"{label} wrote unexpected stdout")


def _analyze_with_frozen_source(
    protocol_root: Path,
    *,
    raw_batch_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    manual_path: Path,
    external_baselines_path: Path,
    mutation_batch_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    script = """
import json
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
from marlrefine.analysis import analyze_prospective_batch
value = analyze_prospective_batch(
    pathlib.Path(sys.argv[2]),
    pathlib.Path(sys.argv[3]),
    pathlib.Path(sys.argv[4]),
    manual_adjudication_path=pathlib.Path(sys.argv[5]),
    external_baseline_path=pathlib.Path(sys.argv[6]),
    mutation_batch_path=pathlib.Path(sys.argv[7]),
)
pathlib.Path(sys.argv[8]).write_text(
    json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
    + "\\n",
    encoding="utf-8",
)
"""
    _run_frozen_python(
        protocol_root,
        script,
        (
            raw_batch_path,
            manifest_path,
            receipt_path,
            manual_path,
            external_baselines_path,
            mutation_batch_path,
            output_path,
        ),
        label="read-only frozen analysis recomputation",
    )
    payload = _read_limited(output_path, "recomputed frozen analysis")
    value = _json_object(payload, "recomputed frozen analysis")
    if payload != _canonical_json_bytes(value):
        raise ReviewerPackageError("recomputed frozen analysis is not canonical JSON")
    return value


def _runtime_with_frozen_source(protocol_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        output_path = Path(temporary) / "runtime.json"
        script = """
import json
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
from marlrefine.provenance import runtime_provenance
pathlib.Path(sys.argv[2]).write_text(
    json.dumps(
        runtime_provenance(),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\\n",
    encoding="utf-8",
)
"""
        _run_frozen_python(
            protocol_root,
            script,
            (output_path,),
            label="frozen-source live runtime identification",
        )
        payload = _read_limited(output_path, "live frozen-source runtime")
        value = _json_object(payload, "live frozen-source runtime")
        if payload != _canonical_json_bytes(value):
            raise ReviewerPackageError(
                "live frozen-source runtime is not canonical JSON"
            )
        return value


def _render_latex_with_frozen_source(
    protocol_root: Path,
    analysis: Mapping[str, Any],
    temporary_dir: Path,
) -> bytes:
    analysis_path = temporary_dir / "packaged-analysis.json"
    output_path = temporary_dir / "expected-results.tex"
    analysis_path.write_bytes(_canonical_json_bytes(analysis))
    script = """
import json
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
from marlrefine.analysis import latex_result_macros
analysis = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
pathlib.Path(sys.argv[3]).write_text(
    latex_result_macros(analysis), encoding="utf-8", newline="\\n"
)
"""
    _run_frozen_python(
        protocol_root,
        script,
        (analysis_path, output_path),
        label="deterministic frozen-source LaTeX rendering",
    )
    return _read_limited(output_path, "rendered LaTeX macros")


def _validate_latex_binding(
    analysis: Mapping[str, Any], latex_path: Path, protocol_root: Path
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        expected = _render_latex_with_frozen_source(
            protocol_root, analysis, Path(temporary)
        )
    observed = _read_limited(latex_path, "LaTeX macros")
    if observed != expected:
        raise ReviewerPackageError(
            "LaTeX macros are not the deterministic frozen-analysis rendering"
        )


def _validate_container_shape(container: Mapping[str, Any]) -> None:
    if set(container) != CONTAINER_IDENTITY_KEYS:
        raise ReviewerPackageError("container identity keys differ from schema 2")
    if type(container.get("schema_version")) is not int or container.get(
        "schema_version"
    ) != 2:
        raise ReviewerPackageError("container identity schema differs")
    if container.get("verification_status") != "tests_passed":
        raise ReviewerPackageError("container identity is not test-passing")
    image_id = container.get("image_id")
    if not isinstance(image_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_id
    ):
        raise ReviewerPackageError("container image ID is invalid")
    image_id_kind = container.get("image_id_kind")
    if (
        type(image_id_kind) is not str
        or image_id_kind != OCI_MANIFEST_IMAGE_ID_KIND
    ):
        raise ReviewerPackageError("container image ID kind is invalid")
    image_manifest_digest = container.get("image_manifest_digest")
    if (
        type(image_manifest_digest) is not str
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_manifest_digest)
        or image_manifest_digest != image_id
    ):
        raise ReviewerPackageError(
            "container image manifest digest differs from the image ID"
        )
    image_config_digest = container.get("image_config_digest")
    if type(image_config_digest) is not str or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_config_digest
    ):
        raise ReviewerPackageError("container image config digest is invalid")
    base_image = container.get("base_image")
    if not isinstance(base_image, str) or re.fullmatch(
        r"[^\s@]+@sha256:[0-9a-f]{64}", base_image
    ) is None:
        raise ReviewerPackageError("container base image identity is invalid")
    image_reference = container.get("image_reference")
    if not isinstance(image_reference, str) or not image_reference.strip():
        raise ReviewerPackageError("container image reference is invalid")
    repo_digests = container.get("repo_digests")
    if (
        not isinstance(repo_digests, list)
        or repo_digests != sorted(set(repo_digests))
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", item) is None
            for item in repo_digests
        )
    ):
        raise ReviewerPackageError("container repository digests are invalid")
    image_archive = _mapping(
        container.get("image_archive"), "container image archive"
    )
    if set(image_archive) != {"filename", "format", "sha256", "size_bytes"}:
        raise ReviewerPackageError("container image archive keys differ")
    if (
        image_archive.get("format") != IMAGE_ARCHIVE_FORMAT
        or image_archive.get("filename") != PROTOCOL_IMAGE_ARCHIVE_FILENAME
        or type(image_archive.get("size_bytes")) is not int
        or int(image_archive.get("size_bytes", 0)) <= 0
    ):
        raise ReviewerPackageError("container image archive identity is invalid")
    _sha(image_archive.get("sha256"), "container image archive")
    _sha(container.get("dockerfile_sha256"), "container Dockerfile")
    _sha(
        container.get("verification_output_sha256"),
        "container verification output",
    )
    verification_command = container.get("verification_command")
    if verification_command != f"docker run --rm {image_id}":
        raise ReviewerPackageError("container verification command is invalid")
    if (
        container.get("verification_output_normalization")
        != VERIFICATION_OUTPUT_NORMALIZATION
    ):
        raise ReviewerPackageError("container verification normalization is invalid")
    container_runtime = _mapping(
        container.get("container_runtime"), "container runtime"
    )
    if set(container_runtime) != CONTAINER_RUNTIME_KEYS:
        raise ReviewerPackageError("container runtime keys differ from schema 2")
    if set(_mapping(container.get("platform"), "container platform")) != (
        CONTAINER_PLATFORM_KEYS
    ):
        raise ReviewerPackageError("container platform keys differ from schema 2")


def _validate_container_binding(
    container: Mapping[str, Any],
    analysis: Mapping[str, Any],
    protocol_identity: Mapping[str, Any],
) -> None:
    _validate_container_shape(container)
    inputs = _mapping(analysis.get("input_identities"), "analysis input identities")
    runtime = _mapping(analysis.get("runtime"), "analysis runtime")
    container_runtime = _mapping(
        container.get("container_runtime"), "container runtime"
    )
    source_sha = _sha(inputs.get("source_tree_sha256"), "analysis source tree")
    lock_sha = _sha(inputs.get("uv_lock_sha256"), "analysis lockfile")
    manifest_identity = _mapping(inputs.get("manifest"), "analysis manifest identity")
    manifest_sha = _sha(manifest_identity.get("sha256"), "analysis manifest")
    expected = {
        "source_tree_sha256": source_sha,
        "uv_lock_sha256": lock_sha,
        "manifest_sha256": manifest_sha,
    }
    protocol_expected = {
        "source_tree_sha256": protocol_identity.get("source_tree_sha256"),
        "uv_lock_sha256": protocol_identity.get("uv_lock_sha256"),
        "manifest_sha256": protocol_identity.get("manifest_sha256"),
    }
    if protocol_expected != expected:
        raise ReviewerPackageError(
            "pre-run identity differs from frozen analysis source inputs"
        )
    if container.get("source_tree_sha256") != source_sha:
        raise ReviewerPackageError("container source-tree identity differs")
    if container_runtime.get("source_tree_sha256") != source_sha:
        raise ReviewerPackageError("container runtime source identity differs")
    if container_runtime.get("uv_lock_sha256") != lock_sha:
        raise ReviewerPackageError("container runtime lock identity differs")
    study_manifest = _mapping(
        container.get("study_manifest"), "container study manifest"
    )
    if study_manifest != {
        "path": "manifests/study_v1_draft.json",
        "sha256": manifest_sha,
    }:
        raise ReviewerPackageError("container study-manifest identity differs")
    for field in ("packages", "installed_distribution_sha256"):
        if container_runtime.get(field) != runtime.get(field):
            raise ReviewerPackageError(f"container runtime {field} differs")
    expected_python = _mapping(runtime.get("python"), "analysis runtime Python")
    observed_python = _mapping(
        container_runtime.get("python"), "container runtime Python"
    )
    for field in ("implementation", "version", "executable_name"):
        if observed_python.get(field) != expected_python.get(field):
            raise ReviewerPackageError(f"container Python {field} differs")
    runtime_platform = _mapping(runtime.get("platform"), "analysis runtime platform")
    image_platform = _mapping(container.get("platform"), "container platform")
    observed_os = str(image_platform.get("os", "")).lower()
    expected_os = str(runtime_platform.get("system", "")).lower()
    if observed_os != expected_os:
        raise ReviewerPackageError("container operating system differs")
    architecture_aliases = {
        "aarch64": "arm64",
        "amd64": "amd64",
        "arm64": "arm64",
        "x86_64": "amd64",
    }
    observed_arch = architecture_aliases.get(
        str(image_platform.get("architecture", "")).lower()
    )
    expected_arch = architecture_aliases.get(
        str(runtime_platform.get("machine", "")).lower()
    )
    if observed_arch is None or observed_arch != expected_arch:
        raise ReviewerPackageError("container architecture differs")


def _validate_gate_log(
    gate_path: Path,
    receipt: Mapping[str, Any],
) -> None:
    doi = receipt.get("doi")
    if not isinstance(doi, str) or not re.fullmatch(r"10\.5281/zenodo\.\d+", doi):
        raise ReviewerPackageError("archive receipt DOI is invalid")
    manifest_sha = _sha(receipt.get("manifest_sha256"), "receipt manifest")
    source_sha = _sha(receipt.get("source_tree_sha256"), "receipt source tree")
    lock_sha = _sha(receipt.get("uv_lock_sha256"), "receipt lockfile")
    expected = (
        f"verified {doi}; manifest_sha256={manifest_sha}; "
        f"source_tree_sha256={source_sha}; uv_lock_sha256={lock_sha}; "
        "prospective_cases=840\n"
    ).encode()
    if _read_limited(gate_path, "archive gate log") != expected:
        raise ReviewerPackageError(
            "archive gate log is not the exact successful frozen-gate output"
        )


def _validate_receipt(
    receipt: Mapping[str, Any],
    protocol_identity: Mapping[str, Any],
    by_role: Mapping[str, Mapping[str, Any]],
    role_hashes: Mapping[str, str],
) -> None:
    expected_keys = {
        "schema_version",
        "artifact_type",
        "record_id",
        "doi",
        "archive_url",
        "published_at_utc",
        "manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "protocol_bundle",
        "identity_file",
    }
    if set(receipt) != expected_keys:
        raise ReviewerPackageError("archive receipt keys differ from schema 1")
    record_id = receipt.get("record_id")
    if type(record_id) is not int or record_id <= 0:
        raise ReviewerPackageError("archive receipt record_id is invalid")
    doi = f"10.5281/zenodo.{record_id}"
    if receipt.get("doi") != doi:
        raise ReviewerPackageError("archive receipt DOI differs from record_id")
    if receipt.get("archive_url") != f"https://zenodo.org/records/{record_id}":
        raise ReviewerPackageError("archive receipt URL is not canonical")
    published = receipt.get("published_at_utc")
    if not isinstance(published, str):
        raise ReviewerPackageError("archive receipt publication time is invalid")
    try:
        timestamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewerPackageError(
            "archive receipt publication time is not ISO-8601"
        ) from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ReviewerPackageError("archive receipt publication time is not UTC")
    if timestamp > datetime.now(UTC) + timedelta(minutes=5):
        raise ReviewerPackageError("archive receipt publication time is in the future")
    expected_identity = {
        "artifact_type": "marlrefine_protocol_freeze_identity",
        "manifest_sha256": _sha(receipt.get("manifest_sha256"), "receipt manifest"),
        "protocol_bundle": {
            "filename": Path(str(by_role["pre_run_bundle"]["path"])).name,
            "sha256": role_hashes["pre_run_bundle"],
        },
        "schema_version": 1,
        "source_tree_sha256": _sha(
            receipt.get("source_tree_sha256"), "receipt source tree"
        ),
        "uv_lock_sha256": _sha(receipt.get("uv_lock_sha256"), "receipt lockfile"),
    }
    if protocol_identity != expected_identity:
        raise ReviewerPackageError(
            "pre-run identity is not the exact receipt-bound freeze identity"
        )
    for field, role in (
        ("protocol_bundle", "pre_run_bundle"),
        ("identity_file", "pre_run_identity"),
    ):
        value = _mapping(receipt.get(field), f"receipt {field}")
        expected_file = {
            "filename": Path(str(by_role[role]["path"])).name,
            "sha256": role_hashes[role],
        }
        if value != expected_file:
            raise ReviewerPackageError(f"receipt {field} identity differs")


def _evidence_hashes_from_roots(
    manual: Mapping[str, Any],
) -> set[str]:
    references: set[str] = set()
    roots = manual.get("roots")
    if not isinstance(roots, list):
        raise ReviewerPackageError("manual roots must be an array")
    for index, raw_root in enumerate(roots):
        root = _mapping(raw_root, f"manual roots[{index}]")
        status = root.get("adjudication_status")
        if status == "pending":
            raise ReviewerPackageError(
                "complete manual adjudication has a pending root"
            )
        if status not in {"confirmed", "rejected"}:
            raise ReviewerPackageError(
                f"manual roots[{index}] adjudication status differs"
            )
        for field in ("replay", "repair", "upstream"):
            item = root.get(field)
            if isinstance(item, Mapping) and item.get("status") == "pending":
                raise ReviewerPackageError(
                    f"complete manual root has pending {field} status"
                )
        witness = _mapping(root.get("first_witness"), "retained root first_witness")
        references.add(
            _sha(
                witness.get("evidence_artifact_sha256"),
                "retained root first-witness evidence",
            )
        )
        if status == "confirmed":
            causal_patch = _mapping(
                root.get("causal_patch"), "confirmed root causal_patch"
            )
            references.add(
                _sha(
                    causal_patch.get("patch_sha256"),
                    "confirmed root causal patch SHA-256",
                )
            )
        for field in ("replay", "repair"):
            record = _mapping(root.get(field), f"retained root {field}")
            evidence = record.get("evidence")
            if evidence is not None:
                evidence_map = _mapping(evidence, f"retained root {field} evidence")
                references.add(
                    _sha(
                        evidence_map.get("artifact_sha256"),
                        f"retained root {field} evidence SHA-256",
                    )
                )
        baselines = _mapping(root.get("baselines"), "retained root baselines")
        for name, raw_baseline in baselines.items():
            baseline = _mapping(raw_baseline, f"retained root baseline {name}")
            for field in ("outcome_evidence", "causal_evidence"):
                evidence = baseline.get(field)
                if evidence is not None:
                    evidence_map = _mapping(
                        evidence, f"retained root baseline {name} {field}"
                    )
                    references.add(
                        _sha(
                            evidence_map.get("artifact_sha256"),
                            f"retained root baseline {name} {field} SHA-256",
                        )
                    )
    controls = manual.get("controls")
    if not isinstance(controls, list):
        raise ReviewerPackageError("manual controls must be an array")
    control_ids: list[str] = []
    for index, raw_control in enumerate(controls):
        control = _mapping(raw_control, f"manual controls[{index}]")
        control_id = control.get("control_id")
        if not isinstance(control_id, str):
            raise ReviewerPackageError("manual control ID must be a string")
        control_ids.append(control_id)
        references.add(
            _sha(
                control.get("evidence_artifact_sha256"),
                f"manual controls[{index}] evidence SHA-256",
            )
        )
    if frozenset(control_ids) != _REQUIRED_CONTROL_IDS or len(control_ids) != 3:
        raise ReviewerPackageError(
            "complete manual adjudication must contain the three frozen controls"
        )
    return references


def _confirmed_patch_hashes(manual: Mapping[str, Any]) -> set[str]:
    roots = manual.get("roots")
    if not isinstance(roots, list):
        raise ReviewerPackageError("manual roots must be an array")
    result: set[str] = set()
    for index, raw_root in enumerate(roots):
        root = _mapping(raw_root, f"manual roots[{index}]")
        if root.get("adjudication_status") != "confirmed":
            continue
        patch = _mapping(root.get("causal_patch"), f"manual roots[{index}] patch")
        result.add(
            _sha(
                patch.get("patch_sha256"),
                f"manual roots[{index}] causal patch SHA-256",
            )
        )
    return result


def _validate_causal_patch_files(
    manual: Mapping[str, Any],
    by_role: Mapping[str, Mapping[str, Any]],
) -> None:
    evidence_by_path = {
        str(entry["path"]): entry
        for role, entry in by_role.items()
        if role.startswith(EVIDENCE_ROLE_PREFIX)
    }
    roots = manual.get("roots")
    if not isinstance(roots, list):
        raise ReviewerPackageError("manual roots must be an array")
    for index, raw_root in enumerate(roots):
        root = _mapping(raw_root, f"manual roots[{index}]")
        if root.get("adjudication_status") != "confirmed":
            continue
        patch = _mapping(root.get("causal_patch"), f"manual roots[{index}] patch")
        reference = _relative_path(
            patch.get("evidence_reference"),
            f"manual roots[{index}] causal patch evidence_reference",
        )
        digest = _sha(
            patch.get("patch_sha256"),
            f"manual roots[{index}] causal patch SHA-256",
        )
        entry = evidence_by_path.get(reference)
        if entry is None or entry.get("sha256") != digest:
            raise ReviewerPackageError(
                "confirmed causal patch reference does not resolve to the exact "
                "allowlisted evidence path and SHA-256"
            )
        source = entry.get("source")
        if not isinstance(source, Path):
            raise ReviewerPackageError("internal causal-patch source is missing")
        payload = _read_limited(source, "causal patch")
        if not payload or b"\r" in payload or not payload.endswith(b"\n"):
            raise ReviewerPackageError(
                "confirmed causal patch must be nonempty LF-terminated diff data"
            )
        try:
            text = payload.decode("utf-8")
        except UnicodeError as exc:
            raise ReviewerPackageError("confirmed causal patch is not UTF-8") from exc
        starts_diff = text.startswith("diff --git a/")
        unified = (
            starts_diff
            and re.search(r"(?m)^--- (?:a/|/dev/null)", text) is not None
            and re.search(r"(?m)^\+\+\+ (?:b/|/dev/null)", text) is not None
            and re.search(r"(?m)^@@ .+ @@", text) is not None
        )
        binary = starts_diff and (
            "\nGIT binary patch\n" in text
            or re.search(r"(?m)^Binary files .+ differ$", text) is not None
        )
        if not unified and not binary:
            raise ReviewerPackageError(
                "confirmed causal patch is not a unified or Git binary diff"
            )


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewerPackageError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ReviewerPackageError(f"{label} is not UTC")
    return parsed


def _field_lines(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^- ([^:\n]+):\s*(.*?)\s*$", block):
        name = match.group(1).strip()
        if name in fields:
            raise ReviewerPackageError(f"document repeats field {name}")
        fields[name] = match.group(2).strip()
    return fields


def _command_shaped(value: str) -> bool:
    command = value.strip().strip("`").strip()
    return re.match(
        r"^(?:docker|uv|python(?:3(?:\.\d+)?)?|/usr/bin/env\s+(?:uv|python))\s+",
        command,
    ) is not None


_DOCKER_RUN_FLAG_OPTIONS = frozenset({"--rm"})
_DOCKER_RUN_VALUE_OPTIONS = frozenset({"--mount"})


def _docker_run_image_operand(command_tokens: Sequence[str]) -> str | None:
    """Return the image operand from the study's canonical ``docker run`` form.

    The frozen runbook puts only ``--rm`` and zero or more ``--mount`` options
    before the image.  Keeping this parser deliberately narrow prevents a
    token that merely mentions the recorded digest in the container command
    from being mistaken for the image Docker will actually execute.
    """

    if list(command_tokens[:2]) != ["docker", "run"]:
        return None
    index = 2
    while index < len(command_tokens):
        token = command_tokens[index]
        if token in _DOCKER_RUN_FLAG_OPTIONS:
            index += 1
            continue
        if token in _DOCKER_RUN_VALUE_OPTIONS:
            index += 1
            if (
                index >= len(command_tokens)
                or not command_tokens[index]
                or command_tokens[index].startswith("-")
            ):
                return None
            index += 1
            continue
        if any(
            token.startswith(f"{option}=") and token != f"{option}="
            for option in _DOCKER_RUN_VALUE_OPTIONS
        ):
            index += 1
            continue
        if token.startswith("-"):
            return None
        return token
    return None


def _portable_diary_path(value: str, label: str) -> str:
    if value.startswith("/"):
        path = PurePosixPath(value)
        if (
            path.as_posix() != value
            or any(part in {"", ".", ".."} for part in path.parts[1:])
            or not any(value.startswith(prefix) for prefix in ("/artifact/", "/run/"))
        ):
            raise ReviewerPackageError(f"{label} is not a portable container path")
        return value
    return _relative_path(value, label)


def _validate_document_roles(
    by_role: Mapping[str, Mapping[str, Any]],
    *,
    receipt: Mapping[str, Any],
    container: Mapping[str, Any],
    protocol_root: Path,
    role_hashes: Mapping[str, str],
) -> None:
    required_headings = {
        "reproduction_readme": (
            b"# Reproduction README",
            b"## Clean-environment command",
            b"## Expected hashes",
        ),
        "deviation_log": (b"# Deviation log", b"## Deviation entry"),
        "run_diary": (b"# Run diary", b"## Study identity", b"## Run entry"),
    }
    for role, headings in required_headings.items():
        source = by_role[role].get("source")
        if not isinstance(source, Path):
            raise ReviewerPackageError(f"internal {role} source is missing")
        payload = _read_limited(source, role)
        if not payload or not payload.endswith(b"\n") or b"\r" in payload:
            raise ReviewerPackageError(
                f"{role} must be nonempty canonical LF-terminated text"
            )
        missing = [heading.decode() for heading in headings if heading not in payload]
        if missing:
            raise ReviewerPackageError(
                f"{role} is missing stable headings: {missing}"
            )
        text = payload.decode("utf-8")
        placeholder_tokens = ("<fill", "<replace", "REPLACE_BEFORE", "TODO", "TBD")
        if any(token.lower() in text.lower() for token in placeholder_tokens):
            raise ReviewerPackageError(f"{role} contains an unresolved placeholder")
        if role == "run_diary":
            if "Status: complete through final analysis" not in text:
                raise ReviewerPackageError(
                    "run_diary lacks the complete-through-final-analysis marker"
                )
            study_fields = _field_lines(
                text.split("## Run entry", 1)[0]
            )
            for field in (
                "Protocol record URL/DOI",
                "Archive receipt path and SHA-256",
                "Source commit A",
                "Generated-evidence commit B",
                "Container image ID",
                "Container image archive path and SHA-256",
                "Docker execution engine/store",
                "Container image platform",
                "Exact image backup alias and round-trip SHA-256",
                "Operator",
                "Primary result path (repository-relative or container path)",
                "Free space before first gated command (GiB)",
                "Separate backup target (volume label/stable alias plus relative path)",
                "Backup capacity verified at (UTC)",
                "Deviation-log path",
            ):
                if not study_fields.get(field):
                    raise ReviewerPackageError(
                        f"run_diary has no completed {field} field"
                    )
            freeze = _json_object(
                (protocol_root / "FREEZE_METADATA.json").read_bytes(),
                "protocol freeze metadata",
            )
            if study_fields["Protocol record URL/DOI"] not in {
                str(receipt["doi"]),
                str(receipt["archive_url"]),
            }:
                raise ReviewerPackageError("run_diary protocol record differs")
            receipt_entry = by_role["archive_receipt"]
            if (
                str(receipt_entry["path"])
                not in study_fields["Archive receipt path and SHA-256"]
                or role_hashes["archive_receipt"]
                not in study_fields["Archive receipt path and SHA-256"]
            ):
                raise ReviewerPackageError("run_diary receipt identity differs")
            if study_fields["Source commit A"] != freeze["source_git_revision"]:
                raise ReviewerPackageError("run_diary source commit differs")
            if (
                study_fields["Generated-evidence commit B"]
                != freeze["archive_git_revision"]
            ):
                raise ReviewerPackageError("run_diary archive commit differs")
            if study_fields["Container image ID"] != container["image_id"]:
                raise ReviewerPackageError("run_diary container identity differs")
            image_archive_field = study_fields[
                "Container image archive path and SHA-256"
            ]
            if (
                str(by_role["container_image_archive"]["path"])
                not in image_archive_field
                or role_hashes["container_image_archive"]
                not in image_archive_field
            ):
                raise ReviewerPackageError(
                    "run_diary container image archive identity differs"
                )
            if (
                study_fields["Docker execution engine/store"]
                != STUDY_DOCKER_ENGINE
            ):
                raise ReviewerPackageError(
                    "run_diary Docker execution engine/store differs"
                )
            platform = _mapping(container.get("platform"), "container platform")
            expected_platform = f"{platform.get('os')}/{platform.get('architecture')}"
            if study_fields["Container image platform"] != expected_platform:
                raise ReviewerPackageError("run_diary container platform differs")
            image_backup = study_fields[
                "Exact image backup alias and round-trip SHA-256"
            ]
            image_archive_sha = role_hashes["container_image_archive"]
            if image_archive_sha not in image_backup:
                raise ReviewerPackageError(
                    "run_diary exact-image backup hash differs"
                )
            backup_alias = image_backup.replace(image_archive_sha, "").strip()
            _relative_path(backup_alias, "run_diary exact-image backup alias")
            try:
                free_gib = float(
                    study_fields["Free space before first gated command (GiB)"]
                )
            except ValueError as exc:
                raise ReviewerPackageError(
                    "run_diary free-space value is not numeric"
                ) from exc
            if free_gib < 50:
                raise ReviewerPackageError(
                    "run_diary did not record the 50 GiB minimum preflight"
                )
            for path_field in (
                "Primary result path (repository-relative or container path)",
                "Deviation-log path",
            ):
                _portable_diary_path(
                    study_fields[path_field], f"run_diary {path_field}"
                )
            backup_field = (
                "Separate backup target "
                "(volume label/stable alias plus relative path)"
            )
            _relative_path(
                study_fields[backup_field],
                "run_diary separate backup target",
            )
            _parse_utc(
                study_fields["Backup capacity verified at (UTC)"],
                "run_diary backup-capacity timestamp",
            )
            for artifact_role in (
                "archive_gate_log",
                "raw_batch",
                "external_baselines",
                "mutation_batch",
                "frozen_analysis",
                "latex_macros",
                "manual_adjudication",
            ):
                if (
                    str(by_role[artifact_role]["path"]) not in text
                    or role_hashes[artifact_role] not in text
                ):
                    raise ReviewerPackageError(
                        f"run_diary does not bind {artifact_role} path and SHA-256"
                    )
            completed_stages: set[str] = set()
            required_stages = {
                "verify-archive",
                "prospective batch",
                "external baselines",
                "mutation",
                "preliminary analysis",
                "final analysis",
            }
            allowed_stages = required_stages | {"replay", "evidence development"}
            stage_output_artifacts = {
                "verify-archive": ("archive_gate_log",),
                "prospective batch": ("raw_batch",),
                "external baselines": ("external_baselines",),
                "mutation": ("mutation_batch",),
                "final analysis": (
                    "frozen_analysis",
                    "latex_macros",
                ),
            }
            stage_input_artifacts = {
                "final analysis": ("manual_adjudication",),
            }
            required_entry_fields = {
                "Entry ID",
                "Stage",
                "Started at (UTC)",
                "Ended at (UTC)",
                "Exact command",
                "Input paths and SHA-256 values",
                "Intended output path(s)",
                "Exit code or interruption signal",
                "Completion state (`completed` or `interrupted`)",
                "Published output path(s), or `none`",
                "Published output SHA-256 values, or `none`",
                "Backup copy path(s)",
                "Backup hash verification",
                "Operational notes",
                "Linked deviation ID(s), or `none`",
            }
            blocks = text.split("## Run entry")[1:]
            if not blocks:
                raise ReviewerPackageError("run_diary has no run-entry blocks")
            entry_ids: set[str] = set()
            for index, block in enumerate(blocks):
                fields = _field_lines(block)
                missing_fields = sorted(
                    field
                    for field in required_entry_fields
                    if not fields.get(field)
                )
                if missing_fields:
                    raise ReviewerPackageError(
                        f"run_diary entry {index} has blank fields: {missing_fields}"
                    )
                entry_id = fields["Entry ID"]
                if entry_id in entry_ids:
                    raise ReviewerPackageError("run_diary repeats an entry ID")
                entry_ids.add(entry_id)
                stage = fields["Stage"].strip("`")
                if stage not in allowed_stages:
                    raise ReviewerPackageError(
                        f"run_diary pre-seal stage is invalid: {stage}"
                    )
                started = _parse_utc(
                    fields["Started at (UTC)"], "run_diary start timestamp"
                )
                ended = _parse_utc(
                    fields["Ended at (UTC)"], "run_diary end timestamp"
                )
                if ended < started:
                    raise ReviewerPackageError("run_diary entry ends before it starts")
                command = fields["Exact command"]
                if not _command_shaped(command):
                    raise ReviewerPackageError(
                        "run_diary exact command is not command-shaped"
                    )
                try:
                    command_tokens = shlex.split(command)
                except ValueError as exc:
                    raise ReviewerPackageError(
                        "run_diary exact command has invalid shell quoting"
                    ) from exc
                if _docker_run_image_operand(command_tokens) != container["image_id"]:
                    raise ReviewerPackageError(
                        "run_diary exact command is not bound to the exact "
                        "container image ID"
                    )
                if stage == "preliminary analysis" and (
                    "--manual-adjudication" in command_tokens
                ):
                    raise ReviewerPackageError(
                        "preliminary analysis must omit manual adjudication"
                    )
                if stage == "final analysis" and (
                    "--manual-adjudication" not in command_tokens
                ):
                    raise ReviewerPackageError(
                        "final analysis command lacks manual adjudication"
                    )
                state = fields[
                    "Completion state (`completed` or `interrupted`)"
                ].strip("`")
                if state not in {"completed", "interrupted"}:
                    raise ReviewerPackageError(
                        "run_diary completion state is invalid"
                    )
                if state == "completed":
                    if fields["Exit code or interruption signal"] != "0":
                        raise ReviewerPackageError(
                            "completed run_diary entry did not exit zero"
                        )
                    for output_field in (
                        "Published output path(s), or `none`",
                        "Published output SHA-256 values, or `none`",
                        "Backup copy path(s)",
                        "Backup hash verification",
                    ):
                        value = fields[output_field]
                        if value.casefold() == "none":
                            raise ReviewerPackageError(
                                "completed run_diary entry lacks output/backup binding"
                            )
                    digest_pattern = r"\b[0-9a-f]{64}\b"
                    if not re.search(
                        digest_pattern,
                        fields["Published output SHA-256 values, or `none`"],
                    ) or not re.search(
                        digest_pattern, fields["Backup hash verification"]
                    ):
                        raise ReviewerPackageError(
                            "completed run_diary entry lacks output/backup SHA-256"
                        )
                    for artifact_role in stage_output_artifacts.get(stage, ()):
                        artifact_path = str(by_role[artifact_role]["path"])
                        artifact_sha = role_hashes[artifact_role]
                        if (
                            artifact_path not in fields["Intended output path(s)"]
                            or artifact_path
                            not in fields[
                                "Published output path(s), or `none`"
                            ]
                            or artifact_path not in fields["Backup copy path(s)"]
                            or artifact_sha
                            not in fields[
                                "Published output SHA-256 values, or `none`"
                            ]
                            or artifact_sha
                            not in fields["Backup hash verification"]
                        ):
                            raise ReviewerPackageError(
                                f"run_diary {stage} entry does not bind "
                                f"{artifact_role} output and backup"
                            )
                    for artifact_role in stage_input_artifacts.get(stage, ()):
                        artifact_path = str(by_role[artifact_role]["path"])
                        artifact_sha = role_hashes[artifact_role]
                        if (
                            artifact_path
                            not in fields["Input paths and SHA-256 values"]
                            or artifact_sha
                            not in fields["Input paths and SHA-256 values"]
                        ):
                            raise ReviewerPackageError(
                                f"run_diary {stage} entry does not bind "
                                f"{artifact_role} input"
                            )
                    completed_stages.add(stage)
            missing_stages = sorted(required_stages - completed_stages)
            if missing_stages:
                raise ReviewerPackageError(
                    f"run_diary lacks completed stages: {missing_stages}"
                )
        elif role == "deviation_log":
            no_deviations = re.search(
                r"(?m)^Status: no deviations\s*$", text
            ) is not None
            complete = re.search(r"(?m)^Status: complete\s*$", text) is not None
            if no_deviations == complete:
                raise ReviewerPackageError(
                    "deviation_log must have exactly one final status marker"
                )
            if complete:
                required_deviation_fields = {
                    "Deviation ID",
                    "Recorded at (UTC)",
                    "Affected stage and run-diary entry",
                    "Observed operational departure",
                    "Preservation/disposition of prior files",
                    "Proposed corrective action",
                    "Cases or records affected",
                    "Confirmatory or exploratory classification after correction",
                    "Rationale for that classification",
                    "Approval and date",
                    "Links to superseding artifacts or protocol amendment",
                }
                blocks = text.split("## Deviation entry")[1:]
                if not blocks:
                    raise ReviewerPackageError(
                        "completed deviation_log has no deviation entries"
                    )
                deviation_ids: set[str] = set()
                for index, block in enumerate(blocks):
                    fields = _field_lines(block)
                    missing_fields = sorted(
                        field
                        for field in required_deviation_fields
                        if not fields.get(field)
                    )
                    if missing_fields:
                        raise ReviewerPackageError(
                            "completed deviation_log entry "
                            f"{index} has blank fields: {missing_fields}"
                        )
                    deviation_id = fields["Deviation ID"]
                    if deviation_id in deviation_ids:
                        raise ReviewerPackageError(
                            "completed deviation_log repeats a deviation ID"
                        )
                    deviation_ids.add(deviation_id)
                    _parse_utc(
                        fields["Recorded at (UTC)"],
                        "deviation_log recorded timestamp",
                    )
        elif role == "reproduction_readme":
            if "Status: verified" not in text:
                raise ReviewerPackageError(
                    "reproduction_readme lacks the verified marker"
                )
            command = text.split("## Clean-environment command", 1)[1].split(
                "## Expected hashes", 1
            )[0]
            expected_hashes = text.split("## Expected hashes", 1)[1]
            command_lines = [
                line.strip().strip("`")
                for line in command.splitlines()
                if line.strip() and not line.strip().startswith("```")
            ]
            command_is_valid = any(
                _command_shaped(line) for line in command_lines
            )
            if not command_is_valid or not re.search(
                r"\b[0-9a-f]{64}\b", expected_hashes
            ):
                raise ReviewerPackageError(
                    "reproduction_readme command or expected hashes are incomplete"
                )
            for artifact_role in (
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
            ):
                if (
                    str(by_role[artifact_role]["path"]) not in expected_hashes
                    or role_hashes[artifact_role] not in expected_hashes
                ):
                    raise ReviewerPackageError(
                        "reproduction_readme expected hashes do not bind "
                        f"{artifact_role}"
                    )


def _reject_pending_manual_values(value: Any, label: str = "manual") -> None:
    status_fields = frozenset(
        {
            "adjudication_status",
            "causal_attribution",
            "claim_classification",
            "credit",
            "disposition",
            "status",
        }
    )
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in status_fields and child == "pending":
                raise ReviewerPackageError(
                    f"complete manual adjudication has pending field {label}.{key}"
                )
            _reject_pending_manual_values(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_pending_manual_values(child, f"{label}[{index}]")


def _protocol_source_tree_sha256(root: Path) -> tuple[str, set[str]]:
    try:
        from marlrefine.provenance import source_identity_paths
    except ImportError as exc:
        raise ReviewerPackageError(
            "cannot import protocol source identity rules"
        ) from exc
    digest = hashlib.sha256()
    paths: set[str] = set()
    for path in source_identity_paths(root):
        relative_text = path.relative_to(root).as_posix()
        paths.add(relative_text)
        relative = relative_text.encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest(), paths


def _validate_protocol_bundle(
    bundle_path: Path,
    protocol_identity: Mapping[str, Any],
    standalone_container_path: Path,
    extraction_parent: Path,
) -> tuple[Path, set[str]]:
    """Validate and extract the exact deterministic protocol-freeze format."""
    _validate_gzip_header(
        bundle_path,
        maximum_uncompressed=MAX_NESTED_BUNDLE_BYTES + (64 * 1024 * 1024),
    )
    staging = extraction_parent / PROTOCOL_ROOT_NAME
    file_hashes: dict[str, str] = {}
    directories: set[str] = set()
    consumed_bytes = 0
    try:
        with tarfile.open(bundle_path, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_NESTED_BUNDLE_MEMBERS:
                raise ReviewerPackageError("protocol bundle member count is invalid")
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ReviewerPackageError("protocol bundle has duplicate members")
            root_member = members[0]
            if root_member.name != PROTOCOL_ROOT_NAME or not root_member.isdir():
                raise ReviewerPackageError("protocol bundle root directory differs")
            for member in members:
                _archive_relative_path(member.name, "protocol bundle member")
                if member.uid != 0 or member.gid != 0 or member.mtime != 0:
                    raise ReviewerPackageError(
                        f"protocol member ownership/time differs: {member.name}"
                    )
                if member.uname != "" or member.gname != "":
                    raise ReviewerPackageError(
                        f"protocol member owner names differ: {member.name}"
                    )
                if member.pax_headers not in ({}, {"path": member.name}):
                    raise ReviewerPackageError(
                        f"protocol member PAX metadata differs: {member.name}"
                    )
                if member.name == PROTOCOL_ROOT_NAME:
                    relative = ""
                elif member.name.startswith(f"{PROTOCOL_ROOT_NAME}/"):
                    relative = member.name[len(PROTOCOL_ROOT_NAME) + 1 :]
                    _relative_path(relative, "protocol member relative path")
                else:
                    raise ReviewerPackageError(
                        "protocol member lies outside the fixed root"
                    )
                target = staging if not relative else staging / relative
                if member.isdir():
                    if member.mode != 0o755 or member.size != 0:
                        raise ReviewerPackageError(
                            f"protocol directory metadata differs: {member.name}"
                        )
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                    if relative:
                        directories.add(relative)
                    continue
                if not member.isfile() or member.mode not in {0o644, 0o755}:
                    raise ReviewerPackageError(
                        f"protocol bundle has a link/special file: {member.name}"
                    )
                consumed_bytes += member.size
                if consumed_bytes > MAX_NESTED_BUNDLE_BYTES:
                    raise ReviewerPackageError(
                        "protocol bundle exceeds the uncompressed safety limit"
                    )
                handle = archive.extractfile(member)
                if handle is None:
                    raise ReviewerPackageError(
                        f"cannot read protocol member: {member.name}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                observed_size = 0
                scanned = b""
                with target.open("wb") as output:
                    while block := handle.read(1024 * 1024):
                        observed_size += len(block)
                        digest.update(block)
                        output.write(block)
                        if _is_text_path(relative):
                            window = scanned[-512:] + block
                            _scan_machine_paths(
                                window, f"pre-run bundle {relative}", None
                            )
                            scanned = window
                if observed_size != member.size:
                    raise ReviewerPackageError(
                        f"protocol member is truncated: {member.name}"
                    )
                target.chmod(member.mode)
                file_hashes[relative] = digest.hexdigest()
    except (OSError, tarfile.TarError) as exc:
        raise ReviewerPackageError(f"invalid protocol bundle: {exc}") from exc

    expected_directories = {
        parent.as_posix()
        for relative in file_hashes
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    if directories != expected_directories:
        raise ReviewerPackageError("protocol directory inventory differs")
    expected_names = [PROTOCOL_ROOT_NAME] + [
        f"{PROTOCOL_ROOT_NAME}/{relative}"
        for relative in sorted((*directories, *file_hashes))
    ]
    if names != expected_names:
        raise ReviewerPackageError("protocol members are not canonically ordered")

    checksum_path = staging / "SHA256SUMS"
    if not checksum_path.is_file():
        raise ReviewerPackageError("protocol SHA256SUMS is missing")
    checksum_payload = _read_limited(
        checksum_path, "protocol SHA256SUMS", limit=16 * 1024 * 1024
    )
    expected_checksums = "".join(
        f"{digest}  {relative}\n"
        for relative, digest in sorted(file_hashes.items())
        if relative != "SHA256SUMS"
    ).encode()
    if checksum_payload != expected_checksums:
        raise ReviewerPackageError("protocol SHA256SUMS differs from its files")

    freeze_path = staging / "FREEZE_METADATA.json"
    if not freeze_path.is_file():
        raise ReviewerPackageError("protocol FREEZE_METADATA.json is missing")
    freeze_payload = _read_limited(freeze_path, "protocol freeze metadata")
    freeze = _json_object(freeze_payload, "protocol freeze metadata")
    if freeze_payload != _canonical_json_bytes(freeze):
        raise ReviewerPackageError("protocol freeze metadata is not canonical JSON")
    if set(freeze) != PROTOCOL_FREEZE_KEYS:
        raise ReviewerPackageError("protocol freeze metadata keys differ")
    if freeze.get("git_identity_model") != "two_commit_nonrecursive_v1":
        raise ReviewerPackageError("protocol Git identity model differs")
    for field in ("source_git_revision", "archive_git_revision"):
        if not isinstance(freeze.get(field), str) or not re.fullmatch(
            r"[0-9a-f]{40}", str(freeze.get(field))
        ):
            raise ReviewerPackageError(f"protocol {field} is invalid")
    if freeze.get("source_git_revision") == freeze.get("archive_git_revision"):
        raise ReviewerPackageError("protocol source/archive revisions are identical")
    if freeze.get("generated_evidence_paths") != sorted(
        PROTOCOL_GENERATED_PATHS
    ):
        raise ReviewerPackageError("protocol generated-evidence paths differ")

    try:
        from marlrefine.provenance import SOURCE_ROOT_FILES
    except ImportError as exc:
        raise ReviewerPackageError("cannot import protocol root-file rules") from exc
    source_sha, source_paths = _protocol_source_tree_sha256(staging)
    expected_files = source_paths | PROTOCOL_GENERATED_PATHS | {
        "FREEZE_METADATA.json",
        "SHA256SUMS",
    }
    if set(file_hashes) != expected_files:
        missing = sorted(expected_files - set(file_hashes))
        extra = sorted(set(file_hashes) - expected_files)
        raise ReviewerPackageError(
            f"protocol file inventory differs; missing={missing}, extra={extra}"
        )
    if not set(SOURCE_ROOT_FILES).issubset(source_paths):
        raise ReviewerPackageError("protocol mandatory source root files are missing")

    manifest_path = staging / "manifests/study_v1_draft.json"
    mutation_path = staging / "manifests/mutation_v1.json"
    container_path = staging / "container/IMAGE_IDENTITY.json"
    manifest_sha = _sha256_path(manifest_path)
    mutation_sha = _sha256_path(mutation_path)
    lock_sha = _sha256_path(staging / "uv.lock")
    expected_freeze_hashes = {
        "manifest_sha256": manifest_sha,
        "mutation_manifest_sha256": mutation_sha,
        "source_tree_sha256": source_sha,
        "uv_lock_sha256": lock_sha,
    }
    for field, expected in expected_freeze_hashes.items():
        if _sha(freeze.get(field), f"protocol freeze {field}") != expected:
            raise ReviewerPackageError(f"protocol freeze {field} differs")

    manifest_payload = manifest_path.read_bytes()
    manifest = _json_object(manifest_payload, "protocol study manifest")
    if manifest_payload != _canonical_json_bytes(manifest):
        raise ReviewerPackageError("protocol study manifest is not canonical JSON")
    if type(manifest.get("schema_version")) is not int or manifest.get(
        "schema_version"
    ) != 2 or manifest.get("manifest_status") != "frozen_pending_archive":
        raise ReviewerPackageError("protocol study manifest schema/status differs")
    environment = _mapping(manifest.get("environment"), "protocol environment")
    if environment.get("source_tree_sha256") != source_sha:
        raise ReviewerPackageError("protocol manifest source identity differs")
    if environment.get("uv_lock_sha256") != lock_sha:
        raise ReviewerPackageError("protocol manifest lock identity differs")
    if environment.get("git_revision") != freeze.get("source_git_revision"):
        raise ReviewerPackageError("protocol manifest source revision differs")
    mutation = _mapping(
        manifest.get("mutation_evaluation"), "protocol mutation binding"
    )
    if (
        mutation.get("mutation_manifest_path") != "manifests/mutation_v1.json"
        or mutation.get("mutation_manifest_sha256") != mutation_sha
    ):
        raise ReviewerPackageError("protocol mutation-manifest binding differs")

    if container_path.read_bytes() != standalone_container_path.read_bytes():
        raise ReviewerPackageError(
            "standalone container identity differs from the protocol bundle member"
        )
    container = _json_object(container_path.read_bytes(), "protocol container identity")
    if container_path.read_bytes() != _canonical_json_bytes(container):
        raise ReviewerPackageError("protocol container identity is not canonical JSON")
    _validate_container_shape(container)
    if container.get("source_tree_sha256") != source_sha:
        raise ReviewerPackageError("protocol container source identity differs")
    if container.get("study_manifest") != {
        "path": "manifests/study_v1_draft.json",
        "sha256": manifest_sha,
    }:
        raise ReviewerPackageError("protocol container manifest identity differs")
    if container.get("dockerfile_sha256") != _sha256_path(staging / "Dockerfile"):
        raise ReviewerPackageError("protocol container Dockerfile identity differs")
    if freeze.get("container_image_archive") != container.get("image_archive"):
        raise ReviewerPackageError(
            "protocol freeze image archive commitment differs"
        )

    expected_protocol_identity = {
        "artifact_type": "marlrefine_protocol_freeze_identity",
        "manifest_sha256": manifest_sha,
        "protocol_bundle": {
            "filename": bundle_path.name,
            "sha256": _sha256_path(bundle_path),
        },
        "schema_version": 1,
        "source_tree_sha256": source_sha,
        "uv_lock_sha256": lock_sha,
    }
    if protocol_identity != expected_protocol_identity:
        raise ReviewerPackageError(
            "protocol identity differs from the validated protocol bundle"
        )

    try:
        from deposit.build_protocol_bundle import _write_deterministic_archive
    except ImportError as exc:
        raise ReviewerPackageError("cannot import canonical protocol writer") from exc
    rebuilt = extraction_parent / "rebuilt-protocol.tar.gz"
    _write_deterministic_archive(staging, rebuilt)
    if _sha256_path(rebuilt) != _sha256_path(bundle_path):
        raise ReviewerPackageError(
            "protocol archive differs from canonical reconstruction"
        )
    return staging, set(file_hashes.values())


def _recompute_analysis(
    analysis: Mapping[str, Any],
    *,
    protocol_root: Path,
    raw_batch_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    manual_path: Path,
    external_baselines_path: Path,
    mutation_batch_path: Path,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        recomputed = _analyze_with_frozen_source(
            protocol_root,
            raw_batch_path=raw_batch_path,
            manifest_path=manifest_path,
            receipt_path=receipt_path,
            manual_path=manual_path,
            external_baselines_path=external_baselines_path,
            mutation_batch_path=mutation_batch_path,
            output_path=Path(temporary) / "recomputed-analysis.json",
        )
    packaged = dict(analysis)
    packaged.pop("analysis_runtime", None)
    if _canonical_json_bytes(recomputed) != _canonical_json_bytes(packaged):
        raise ReviewerPackageError(
            "packaged frozen analysis differs from read-only recomputation"
        )


def _validate_semantics(entries: Sequence[Mapping[str, Any]]) -> None:
    by_role = _role_entries(entries)
    role_hashes = {role: str(entry["sha256"]) for role, entry in by_role.items()}
    identity = _read_role_json(by_role, "pre_run_identity")
    if type(identity.get("schema_version")) is not int or identity.get(
        "schema_version"
    ) != 1 or identity.get(
        "artifact_type"
    ) != "marlrefine_protocol_freeze_identity":
        raise ReviewerPackageError("pre-run identity schema or type differs")
    protocol_bundle = _mapping(
        identity.get("protocol_bundle"), "pre-run identity protocol_bundle"
    )
    if _sha(protocol_bundle.get("sha256"), "pre-run bundle identity") != role_hashes[
        "pre_run_bundle"
    ]:
        raise ReviewerPackageError("pre-run identity does not bind the bundle")
    if protocol_bundle.get("filename") != Path(
        str(by_role["pre_run_bundle"]["path"])
    ).name:
        raise ReviewerPackageError("pre-run identity bundle filename differs")
    receipt = _read_role_json(by_role, "archive_receipt")
    if type(receipt.get("schema_version")) is not int or receipt.get(
        "schema_version"
    ) != 1 or receipt.get(
        "artifact_type"
    ) != "marlrefine_protocol_archive_receipt":
        raise ReviewerPackageError("archive receipt schema or type differs")
    _validate_receipt(receipt, identity, by_role, role_hashes)
    receipt_bundle = _mapping(
        receipt.get("protocol_bundle"), "receipt protocol_bundle"
    )
    receipt_identity = _mapping(receipt.get("identity_file"), "receipt identity_file")
    if _sha(receipt_bundle.get("sha256"), "receipt bundle SHA-256") != role_hashes[
        "pre_run_bundle"
    ]:
        raise ReviewerPackageError("receipt does not bind the pre-run bundle")
    if _sha(receipt_identity.get("sha256"), "receipt identity SHA-256") != role_hashes[
        "pre_run_identity"
    ]:
        raise ReviewerPackageError("receipt does not bind the pre-run identity")
    if receipt_bundle.get("filename") != Path(
        str(by_role["pre_run_bundle"]["path"])
    ).name:
        raise ReviewerPackageError("receipt bundle filename differs")
    if receipt_identity.get("filename") != Path(
        str(by_role["pre_run_identity"]["path"])
    ).name:
        raise ReviewerPackageError("receipt identity filename differs")
    gate_source = by_role["archive_gate_log"].get("source")
    if not isinstance(gate_source, Path):
        raise ReviewerPackageError("internal archive gate log source is missing")
    _validate_gate_log(gate_source, receipt)

    manual = _read_role_json(by_role, "manual_adjudication")
    if frozenset(manual) != _MANUAL_KEYS:
        raise ReviewerPackageError("manual adjudication keys differ from schema 5")
    if type(manual.get("schema_version")) is not int or manual.get(
        "schema_version"
    ) != 5 or manual.get(
        "artifact_type"
    ) != "marlrefine_manual_adjudication":
        raise ReviewerPackageError("manual adjudication schema or type differs")
    if manual.get("status") != "complete":
        raise ReviewerPackageError("manual adjudication is not complete")
    _reject_pending_manual_values(manual)
    if _sha(manual.get("raw_batch_sha256"), "manual raw batch SHA-256") != role_hashes[
        "raw_batch"
    ]:
        raise ReviewerPackageError("manual adjudication binds another raw batch")
    optional = _mapping(
        manual.get("optional_measurements"), "manual optional measurements"
    )
    if optional.get("held_out_mutants_killed") is None or optional.get(
        "held_out_mutants_total"
    ) is None:
        raise ReviewerPackageError("manual mutation adjudication is incomplete")

    analysis = _read_role_json(by_role, "frozen_analysis")
    _validate_analysis_bindings(analysis, role_hashes)
    bundle_source = by_role["pre_run_bundle"].get("source")
    if not isinstance(bundle_source, Path):
        raise ReviewerPackageError("internal pre-run bundle source is missing")
    analysis_inputs = _mapping(
        analysis.get("input_identities"), "analysis input identities"
    )
    manifest_identity = _mapping(
        analysis_inputs.get("manifest"), "analysis manifest identity"
    )
    manifest_filename = manifest_identity.get("filename")
    if (
        not isinstance(manifest_filename, str)
        or not manifest_filename
        or Path(manifest_filename).name != manifest_filename
    ):
        raise ReviewerPackageError("analysis manifest filename is invalid")
    manifest_sha = _sha(
        manifest_identity.get("sha256"), "analysis manifest SHA-256"
    )
    if (
        manifest_filename != "study_v1_draft.json"
        or manifest_sha != identity["manifest_sha256"]
    ):
        raise ReviewerPackageError(
            "analysis manifest identity differs from the protocol freeze"
        )
    role_sources: dict[str, Path] = {}
    for role in (
        "raw_batch",
        "archive_receipt",
        "manual_adjudication",
        "external_baselines",
        "mutation_batch",
    ):
        source = by_role[role].get("source")
        if not isinstance(source, Path):
            raise ReviewerPackageError(f"internal {role} source is missing")
        role_sources[role] = source
    container = _read_role_json(by_role, "container_identity")
    container_source = by_role["container_identity"].get("source")
    if not isinstance(container_source, Path):
        raise ReviewerPackageError("internal container identity source is missing")
    image_archive_source = by_role["container_image_archive"].get("source")
    if not isinstance(image_archive_source, Path):
        raise ReviewerPackageError("internal container image archive source is missing")
    image_archive = _mapping(
        container.get("image_archive"), "container image archive"
    )
    if (
        role_hashes["container_image_archive"]
        != _sha(image_archive.get("sha256"), "container image archive")
        or image_archive_source.stat().st_size != image_archive.get("size_bytes")
        or Path(str(by_role["container_image_archive"]["path"])).name
        != image_archive.get("filename")
    ):
        raise ReviewerPackageError(
            "container image archive differs from its committed identity"
        )
    image_id = container.get("image_id")
    if not isinstance(image_id, str):
        raise ReviewerPackageError("container image ID is invalid")
    try:
        content_identity = validate_docker_image_archive(
            image_archive_source,
            image_id=image_id,
            expected_platform=container.get("platform"),
        )
    except ImageArchiveError as exc:
        raise ReviewerPackageError(f"invalid Docker image archive: {exc}") from exc
    expected_content_identity = {
        "archive_format": image_archive["format"],
        "image_config_digest": container["image_config_digest"],
        "image_id_kind": container["image_id_kind"],
        "image_manifest_digest": container["image_manifest_digest"],
    }
    if content_identity != expected_content_identity:
        raise ReviewerPackageError("container image archive content identity differs")
    with tempfile.TemporaryDirectory() as temporary_dir:
        protocol_root, nested_hashes = _validate_protocol_bundle(
            bundle_source,
            identity,
            container_source,
            Path(temporary_dir),
        )
        manifest_path = protocol_root / "manifests/study_v1_draft.json"
        _validate_analysis_runtime(analysis, protocol_root)
        _recompute_analysis(
            analysis,
            protocol_root=protocol_root,
            raw_batch_path=role_sources["raw_batch"],
            manifest_path=manifest_path,
            receipt_path=role_sources["archive_receipt"],
            manual_path=role_sources["manual_adjudication"],
            external_baselines_path=role_sources["external_baselines"],
            mutation_batch_path=role_sources["mutation_batch"],
        )
        latex_source = by_role["latex_macros"].get("source")
        if not isinstance(latex_source, Path):
            raise ReviewerPackageError("internal LaTeX source is missing")
        _validate_latex_binding(analysis, latex_source, protocol_root)
        _validate_container_binding(container, analysis, identity)
        _validate_document_roles(
            by_role,
            receipt=receipt,
            container=container,
            protocol_root=protocol_root,
            role_hashes=role_hashes,
        )

    available_hashes = set(role_hashes.values()) | nested_hashes
    absent = sorted(_evidence_hashes_from_roots(manual) - available_hashes)
    if absent:
        raise ReviewerPackageError(
            "manual evidence hashes are absent from the package or pre-run bundle: "
            f"{absent}"
        )
    evidence_file_hashes = {
        str(entry["sha256"])
        for role, entry in by_role.items()
        if role.startswith(EVIDENCE_ROLE_PREFIX)
    }
    absent_patches = sorted(_confirmed_patch_hashes(manual) - evidence_file_hashes)
    if absent_patches:
        raise ReviewerPackageError(
            "confirmed causal patches lack allowlisted evidence files: "
            f"{absent_patches}"
        )
    _validate_causal_patch_files(manual, by_role)


def _manifest(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "artifact_type": "marlrefine_reviewer_package_manifest",
        "entries": [
            {
                "archive_path": entry["archive_path"],
                "path": entry["path"],
                "role": entry["role"],
                "sha256": entry["sha256"],
                "size_bytes": entry["size_bytes"],
            }
            for entry in entries
        ],
        "package_format": "deterministic_tar_gzip_v1",
        "schema_version": SCHEMA_VERSION,
    }


def _checksums(entries: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        f"{entry['sha256']}  {entry['archive_path']}\n"
        for entry in sorted(entries, key=lambda item: str(item["archive_path"]))
    ).encode()


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    return info


def _write_archive(
    output: Path,
    entries: Sequence[Mapping[str, Any]],
    manifest_payload: bytes,
    checksums_payload: bytes,
) -> None:
    members: list[tuple[str, bytes | Path]] = [
        (MANIFEST_NAME, manifest_payload),
        (CHECKSUMS_NAME, checksums_payload),
    ]
    for entry in entries:
        source = entry.get("source")
        if not isinstance(source, Path):
            raise ReviewerPackageError("internal package source is missing")
        members.append((str(entry["archive_path"]), source))
    members.sort(key=lambda item: item[0])
    with (
        output.open("wb") as raw_handle,
        gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_handle,
            mtime=0,
            compresslevel=9,
        ) as gzip_handle,
        tarfile.open(
            fileobj=gzip_handle,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as archive,
    ):
        for name, source in members:
            if isinstance(source, bytes):
                archive.addfile(_tar_info(name, len(source)), io.BytesIO(source))
            else:
                with source.open("rb") as source_handle:
                    archive.addfile(
                        _tar_info(name, source.stat().st_size), source_handle
                    )


def _identity(
    archive: Path,
    manifest_payload: bytes,
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "artifact_type": "marlrefine_reviewer_package_identity",
        "manifest_sha256": _sha256_bytes(manifest_payload),
        "payload_identities": {
            str(entry["role"]): {
                "path": entry["path"],
                "sha256": entry["sha256"],
            }
            for entry in sorted(entries, key=lambda item: str(item["role"]))
        },
        "reviewer_package": {
            "filename": archive.name,
            "sha256": _sha256_path(archive),
        },
        "schema_version": SCHEMA_VERSION,
    }


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def build_reviewer_package(
    root: Path,
    inventory: Path,
    output: Path,
    identity_output: Path,
) -> Path:
    """Validate an explicit allowlist and atomically write a sealed package."""
    root = root.resolve(strict=True)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite reviewer package: {output}")
    if identity_output.exists() or identity_output.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite reviewer identity: {identity_output}"
        )
    if output.resolve() == identity_output.resolve():
        raise ReviewerPackageError("archive and identity outputs must differ")
    entries = _validate_source_entries(root, _read_inventory(inventory))
    output.parent.mkdir(parents=True, exist_ok=True)
    _preflight_output_space(output.parent, entries)
    _validate_semantics(entries)
    manifest_payload = _canonical_json_bytes(_manifest(entries))
    checksums_payload = _checksums(entries)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary_dir:
        temporary_archive = Path(temporary_dir) / output.name
        _write_archive(
            temporary_archive, entries, manifest_payload, checksums_payload
        )
        identity_payload = _canonical_json_bytes(
            _identity(temporary_archive, manifest_payload, entries)
        )
        temporary_identity = Path(temporary_dir) / f"identity-{identity_output.name}"
        temporary_identity.write_bytes(identity_payload)
        verify_reviewer_package(temporary_archive, temporary_identity)
        temporary_archive.replace(output)
        try:
            _write_atomic(identity_output, identity_payload)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    return output


def _read_member_bytes(
    archive: tarfile.TarFile, member: tarfile.TarInfo, label: str
) -> bytes:
    if member.size > MAX_JSON_BYTES:
        raise ReviewerPackageError(f"{label} exceeds the safety limit")
    handle = archive.extractfile(member)
    if handle is None:
        raise ReviewerPackageError(f"cannot read {label}")
    payload = handle.read(MAX_JSON_BYTES + 1)
    if len(payload) != member.size:
        raise ReviewerPackageError(f"truncated {label}")
    return payload


def _validate_member_metadata(member: tarfile.TarInfo) -> None:
    if not member.isfile():
        raise ReviewerPackageError(f"archive contains non-file member: {member.name}")
    if (
        member.uid != 0
        or member.gid != 0
        or member.uname != ""
        or member.gname != ""
        or member.mtime != 0
        or member.mode != 0o644
    ):
        raise ReviewerPackageError(f"non-canonical metadata for {member.name}")
    if member.pax_headers not in ({}, {"path": member.name}):
        raise ReviewerPackageError(f"unexpected PAX metadata for {member.name}")


def _validated_manifest_entries(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    if frozenset(value) != frozenset(
        {"artifact_type", "entries", "package_format", "schema_version"}
    ):
        raise ReviewerPackageError("reviewer manifest keys differ")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != "marlrefine_reviewer_package_manifest"
        or value.get("package_format") != "deterministic_tar_gzip_v1"
    ):
        raise ReviewerPackageError("reviewer manifest schema differs")
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        raise ReviewerPackageError("reviewer manifest entries must be an array")
    inventory_entries: list[dict[str, str]] = []
    sizes: dict[str, int] = {}
    archive_paths: set[str] = set()
    for index, raw_entry in enumerate(raw_entries):
        entry = _mapping(raw_entry, f"manifest entries[{index}]")
        expected = {"archive_path", "path", "role", "sha256", "size_bytes"}
        if set(entry) != expected:
            raise ReviewerPackageError(f"manifest entries[{index}] keys differ")
        path = _relative_path(entry.get("path"), f"manifest entries[{index}].path")
        archive_path = entry.get("archive_path")
        if archive_path != f"{PAYLOAD_PREFIX}{path}":
            raise ReviewerPackageError("manifest archive path differs")
        if archive_path in archive_paths:
            raise ReviewerPackageError("manifest archive paths are duplicated")
        archive_paths.add(str(archive_path))
        size = entry.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ReviewerPackageError("manifest size must be a nonnegative integer")
        sizes[path] = size
        inventory_entries.append(
            {
                "path": path,
                "role": str(entry.get("role")),
                "sha256": str(entry.get("sha256")),
            }
        )
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "marlrefine_reviewer_package_inventory",
        "entries": inventory_entries,
    }
    validated = _validate_inventory_object(inventory)
    if [item["path"] for item in validated] != [
        item["path"] for item in inventory_entries
    ]:
        raise ReviewerPackageError("manifest entries are not sorted by path")
    if sum(sizes.values()) > MAX_PACKAGE_PAYLOAD_BYTES:
        raise ReviewerPackageError("reviewer manifest payload exceeds the size limit")
    return [
        {
            **entry,
            "archive_path": f"{PAYLOAD_PREFIX}{entry['path']}",
            "size_bytes": sizes[entry["path"]],
        }
        for entry in validated
    ]


def _validate_identity(
    payload: bytes,
    archive_path: Path,
    manifest_payload: bytes,
    entries: Sequence[Mapping[str, Any]],
) -> None:
    identity = _json_object(payload, "reviewer identity")
    if payload != _canonical_json_bytes(identity):
        raise ReviewerPackageError("reviewer identity is not canonical JSON")
    expected_keys = {
        "artifact_type",
        "manifest_sha256",
        "payload_identities",
        "reviewer_package",
        "schema_version",
    }
    if (
        set(identity) != expected_keys
        or type(identity.get("schema_version")) is not int
        or identity.get("schema_version") != 1
    ):
        raise ReviewerPackageError("reviewer identity schema differs")
    if identity.get("artifact_type") != "marlrefine_reviewer_package_identity":
        raise ReviewerPackageError("reviewer identity artifact type differs")
    if _sha(identity.get("manifest_sha256"), "identity manifest SHA-256") != (
        _sha256_bytes(manifest_payload)
    ):
        raise ReviewerPackageError("identity manifest SHA-256 differs")
    package = _mapping(identity.get("reviewer_package"), "identity package")
    if set(package) != {"filename", "sha256"}:
        raise ReviewerPackageError("identity package keys differ")
    if package.get("filename") != archive_path.name:
        raise ReviewerPackageError("identity package filename differs")
    if _sha(package.get("sha256"), "identity package SHA-256") != _sha256_path(
        archive_path
    ):
        raise ReviewerPackageError("identity package SHA-256 differs")
    expected_payload = {
        str(entry["role"]): {
            "path": entry["path"],
            "sha256": entry["sha256"],
        }
        for entry in sorted(entries, key=lambda item: str(item["role"]))
    }
    if identity.get("payload_identities") != expected_payload:
        raise ReviewerPackageError("identity payload mapping differs")


def _validate_gzip_header(
    path: Path,
    *,
    maximum_uncompressed: int = MAX_ARCHIVE_UNCOMPRESSED_BYTES,
) -> None:
    try:
        with path.open("rb") as handle:
            header = handle.read(10)
    except OSError as exc:
        raise ReviewerPackageError(f"cannot read reviewer archive: {exc}") from exc
    if len(header) != 10 or header[:3] != b"\x1f\x8b\x08":
        raise ReviewerPackageError("reviewer package is not gzip")
    flags = header[3]
    if flags != 0 or header[4:8] != b"\x00\x00\x00\x00":
        raise ReviewerPackageError("reviewer package gzip header is not canonical")
    decompressor = zlib.decompressobj(wbits=31)
    produced_bytes = 0
    try:
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                pending = block
                while pending:
                    produced = decompressor.decompress(pending, 1024 * 1024)
                    produced_bytes += len(produced)
                    if produced_bytes > maximum_uncompressed:
                        raise ReviewerPackageError(
                            "reviewer package uncompressed stream exceeds the limit"
                        )
                    pending = decompressor.unconsumed_tail
                    if decompressor.eof:
                        if decompressor.unused_data or pending or handle.read(1):
                            raise ReviewerPackageError(
                                "reviewer package has trailing or concatenated "
                                "gzip data"
                            )
                        return
    except OSError as exc:
        raise ReviewerPackageError(f"cannot read reviewer archive: {exc}") from exc
    if not decompressor.eof:
        raise ReviewerPackageError("reviewer package gzip stream is truncated")


def verify_reviewer_package(archive_path: Path, identity_path: Path) -> dict[str, Any]:
    """Independently validate structure, payloads, semantics, and identity."""
    _validate_gzip_header(archive_path)
    try:
        identity_payload = identity_path.read_bytes()
    except OSError as exc:
        raise ReviewerPackageError(f"cannot read reviewer identity: {exc}") from exc
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_NESTED_BUNDLE_MEMBERS:
                raise ReviewerPackageError("reviewer archive has too many members")
            names = [member.name for member in members]
            if names != sorted(names) or len(names) != len(set(names)):
                raise ReviewerPackageError(
                    "archive members must be uniquely and canonically sorted"
                )
            for member in members:
                _archive_relative_path(member.name, "reviewer archive member")
                _validate_member_metadata(member)
            by_name = {member.name: member for member in members}
            if MANIFEST_NAME not in by_name or CHECKSUMS_NAME not in by_name:
                raise ReviewerPackageError("archive manifest or checksums are missing")
            manifest_payload = _read_member_bytes(
                archive, by_name[MANIFEST_NAME], "reviewer manifest"
            )
            manifest = _json_object(manifest_payload, "reviewer manifest")
            if manifest_payload != _canonical_json_bytes(manifest):
                raise ReviewerPackageError("reviewer manifest is not canonical JSON")
            entries = _validated_manifest_entries(manifest)
            _preflight_output_space(Path(tempfile.gettempdir()), entries)
            expected_names = {
                MANIFEST_NAME,
                CHECKSUMS_NAME,
                *(str(entry["archive_path"]) for entry in entries),
            }
            if set(names) != expected_names:
                raise ReviewerPackageError("archive has missing or unlisted members")
            checksum_payload = _read_member_bytes(
                archive, by_name[CHECKSUMS_NAME], "reviewer checksums"
            )
            if checksum_payload != _checksums(entries):
                raise ReviewerPackageError("reviewer SHA256SUMS differs")
            extracted: list[dict[str, Any]] = []
            with tempfile.TemporaryDirectory() as temporary_dir:
                temporary_root = Path(temporary_dir)
                for entry in entries:
                    member = by_name[str(entry["archive_path"])]
                    if member.size != entry["size_bytes"]:
                        raise ReviewerPackageError(
                            f"payload size differs for {entry['path']}"
                        )
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise ReviewerPackageError(
                            f"cannot read payload {entry['path']}"
                        )
                    target = temporary_root / str(entry["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    scanned = b""
                    with target.open("wb") as output:
                        while block := handle.read(1024 * 1024):
                            digest.update(block)
                            output.write(block)
                            if _is_text_entry(entry):
                                window = scanned[-512:] + block
                                _scan_machine_paths(window, str(entry["path"]), None)
                                scanned = window
                    if digest.hexdigest() != entry["sha256"]:
                        raise ReviewerPackageError(
                            f"payload SHA-256 differs for {entry['path']}"
                        )
                    extracted.append({**entry, "source": target})
                _validate_semantics(extracted)
                with tempfile.TemporaryDirectory() as rebuild_dir:
                    rebuilt = Path(rebuild_dir) / archive_path.name
                    _write_archive(
                        rebuilt,
                        extracted,
                        manifest_payload,
                        checksum_payload,
                    )
                    if _sha256_path(rebuilt) != _sha256_path(archive_path):
                        raise ReviewerPackageError(
                            "archive bytes differ from canonical reconstruction"
                        )
            _validate_identity(
                identity_payload,
                archive_path,
                manifest_payload,
                entries,
            )
    except (OSError, tarfile.TarError) as exc:
        raise ReviewerPackageError(f"invalid reviewer archive: {exc}") from exc
    return {
        "archive_sha256": _sha256_path(archive_path),
        "entry_count": len(entries),
        "manifest_sha256": _sha256_bytes(manifest_payload),
    }


def render_paper_identity(
    archive_path: Path,
    identity_path: Path,
    review_url: str,
    output: Path,
) -> Path:
    """Render the post-seal, non-circular private-review paper overlay."""
    result = verify_reviewer_package(archive_path, identity_path)
    parsed = urllib.parse.urlsplit(review_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or len(review_url) > 2048
        or any(character.isspace() for character in review_url)
        or any(character in review_url for character in "{}\\")
    ):
        raise ReviewerPackageError(
            "private review URL must be a safe absolute HTTPS URL"
        )
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite paper identity overlay: {output}")
    payload = (
        "% Generated after reviewer-package sealing; do not put inside the package.\n"
        f"\\renewcommand{{\\ReviewerPackageURL}}{{\\url{{{review_url}}}}}\n"
        "\\renewcommand{\\ReviewerPackageRevision}{\\texttt{"
        f"{result['archive_sha256']}"
        "}}\n"
    ).encode()
    _write_atomic(output, payload)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or independently verify a sealed reviewer package."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--root", type=Path, default=Path.cwd())
    build.add_argument("--inventory", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--identity-output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--identity", type=Path, required=True)
    overlay = subparsers.add_parser("render-paper-identity")
    overlay.add_argument("--archive", type=Path, required=True)
    overlay.add_argument("--identity", type=Path, required=True)
    overlay.add_argument("--review-url", required=True)
    overlay.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "build":
        archive = build_reviewer_package(
            args.root,
            args.inventory,
            args.output,
            args.identity_output,
        )
        print(
            f"wrote {archive} sha256={_sha256_path(archive)}; "
            f"identity={args.identity_output}"
        )
    elif args.command == "verify":
        result = verify_reviewer_package(args.archive, args.identity)
        print(
            f"verified {args.archive} sha256={result['archive_sha256']}; "
            f"entries={result['entry_count']}"
        )
    else:
        output = render_paper_identity(
            args.archive,
            args.identity,
            args.review_url,
            args.output,
        )
        print(f"wrote private paper identity overlay at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
