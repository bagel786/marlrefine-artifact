"""Fail-closed execution and classification for the prospective study.

The primary batch is intentionally quiet: no callback or per-case console
output exists, and the JSONL destination becomes visible only after all cases
finish.  A completed batch may be resumed exactly once for cases classified as
infrastructure failures; semantic outcomes are immutable inputs to the retry.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from marlrefine.adapters.openspiel_shimmy import (
    CHANCE_POLICY_ID,
    PROTOCOL_VERSION,
    TraceRun,
    run_trace,
)
from marlrefine.archive import protocol_freeze_identity
from marlrefine.baselines import BASELINE_NAMES
from marlrefine.census import default_loadable_types
from marlrefine.evaluation import (
    OBLIGATION_LEDGER_SCHEMA_ID,
    validate_serialized_obligation_evaluations,
)
from marlrefine.policies import (
    POLICY_ENGINE_ID,
    TRACE_POLICIES,
    TRACE_POLICY_NAMES,
    TracePolicy,
)
from marlrefine.provenance import (
    BYTE_BOUND_DISTRIBUTIONS,
    PINNED_PACKAGES,
    runtime_provenance,
)
from marlrefine.serialization import to_jsonable, write_json, write_jsonl
from marlrefine.study import (
    DISCOVERY_GAME_NAMES,
    KNOWN_DESCRIPTIVE_EXCLUSION_NAMES,
    PRIMARY_OUTCOME_CLASSIFIER_ID,
    PROSPECTIVE_DESTINATION_CALL_CAP,
    PROSPECTIVE_MAX_CASE_ATTEMPTS,
    PROSPECTIVE_RETRY_ELIGIBILITY,
    external_baseline_protocol,
    prospective_execution_contract,
)

CLASSIFIER_ID = PRIMARY_OUTCOME_CLASSIFIER_ID
BATCH_SCHEMA_VERSION = 2
EXPECTED_ACCOUNTING_SIZE = 106
EXPECTED_SEMANTIC_COHORT_SIZE = 105
KNOWN_DESCRIPTIVE_EXCLUSION = "crossword"
MAX_CASE_ATTEMPTS = PROSPECTIVE_MAX_CASE_ATTEMPTS
PRIMARY_CHECKPOINT_SCHEMA_VERSION = 1
PRIMARY_CHECKPOINT_SUFFIX = ".marlrefine-primary-checkpoint"
PRIMARY_CHECKPOINT_HEADER = "header.json"
ACCEPTED_FROZEN_STATUSES = frozenset({"frozen_pending_archive", "timestamp_archived"})
INFRASTRUCTURE_CODES = frozenset(
    {
        "destination_call_budget_exhausted",
        "instrumentation_history_not_prefix_monotone",
        "instrumentation_replay_failed",
        "progress_instrumentation_inconsistent",
        "source_setup_failed",
    }
)
UNALIGNABLE_CODES = frozenset({"unalignable_chance"})
DOI_PATTERN = re.compile(r"^10\.5281/zenodo\.\d+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
LOCAL_AUTHORIZATION_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "authorized_at_utc",
        "authorization_id",
        "source_git_revision",
        "preregistered",
        "public_archive",
    }
)
MAX_ZENODO_METADATA_BYTES = 2 * 1024 * 1024
MAX_IDENTITY_BYTES = 1024 * 1024
MAX_PROTOCOL_BUNDLE_BYTES = 512 * 1024 * 1024
HEADER_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "classifier_id",
        "obligation_ledger_schema_id",
        "manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "receipt_sha256",
        "archive_identifier",
        "archive_published_at_utc",
        "case_count",
        "decision_cap",
        "destination_call_cap",
        "max_case_attempts",
        "retry_eligibility",
        "known_descriptive_exclusions",
        "resume_infrastructure_from_sha256",
        "runtime",
    }
)
CASE_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "classifier_id",
        "manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "case",
        "attempt",
        "prior_record_sha256",
        "run",
        "infrastructure_error",
        "captured_stdout",
        "captured_stderr",
        "elapsed_ns",
        "status",
    }
)
CASE_METADATA_KEYS = frozenset(
    {
        "case_id",
        "ordinal",
        "game_name",
        "trace_policy_name",
        "trace_policy_id",
        "trace_policy_seed",
        "environment_seed",
    }
)
TRACE_RUN_KEYS = frozenset(
    {
        "game_spec",
        "seed",
        "applicable",
        "source_events",
        "destination_events",
        "alignment",
        "baselines",
        "violations",
        "obligation_evaluations",
        "summary",
    }
)
FOOTER_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "classifier_id",
        "manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "case_count",
        "status_counts",
        "resumed_infrastructure_cases",
    }
)
RUNTIME_PROVENANCE_KEYS = frozenset(
    {
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
)


class ProspectiveGateError(RuntimeError):
    """The archive, source, manifest, or prospective schedule is not frozen."""


class ResumeError(RuntimeError):
    """A prior batch cannot be used for the frozen infrastructure retry."""


class OutcomeStatus(StrEnum):
    """Mutually exclusive primary outcome taxonomy."""

    PASS = "pass"
    FAIL = "fail"
    INAPPLICABLE = "inapplicable"
    INFRASTRUCTURE = "infrastructure"
    UNALIGNABLE = "unalignable"


@dataclass(frozen=True, slots=True)
class ArchiveGate:
    """Verified identities needed to execute a prospective case."""

    manifest: dict[str, Any]
    manifest_sha256: str
    source_tree_sha256: str
    uv_lock_sha256: str
    receipt_sha256: str
    archive_identifier: str
    published_at_utc: str


@dataclass(frozen=True, slots=True)
class ProspectiveCase:
    """One game/policy pair in the frozen semantic cohort."""

    ordinal: int
    game_name: str
    policy: TracePolicy

    @property
    def case_id(self) -> str:
        return f"{self.game_name}::{self.policy.name}"


@dataclass(frozen=True, slots=True)
class ProspectivePlan:
    """The exact ordered batch derived from a frozen study manifest."""

    gate: ArchiveGate
    decision_cap: int
    destination_call_cap: int
    cases: tuple[ProspectiveCase, ...]


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """Aggregate-only result returned after the primary artifact is sealed."""

    output: Path
    case_count: int
    status_counts: dict[str, int]
    resumed_infrastructure_cases: int


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProspectiveGateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_object(path: Path, *, gate_error: type[RuntimeError]) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise gate_error(f"cannot read canonical JSON from {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise gate_error(f"{path.name} must contain one JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ProspectiveGateError(f"cannot hash {path.name}: {exc}") from exc
    return digest.hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProspectiveGateError(f"{label} must be an object")
    return value


def _require_string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProspectiveGateError(f"{label} must be a JSON string array")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ProspectiveGateError(f"{label} contains duplicate names")
    return result


def _published_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ProspectiveGateError("receipt published_at_utc must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveGateError("receipt published_at_utc is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ProspectiveGateError("receipt published_at_utc must carry a UTC offset")
    if parsed > datetime.now(UTC) + timedelta(minutes=5):
        raise ProspectiveGateError("receipt publication timestamp is in the future")
    return parsed.astimezone(UTC).isoformat()


def _archive_identifier(receipt: Mapping[str, Any]) -> str:
    doi = receipt.get("doi")
    archive_url = receipt.get("archive_url")
    record_id = receipt.get("record_id")
    if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0:
        raise ProspectiveGateError("receipt record_id must be a positive integer")
    expected_doi = f"10.5281/zenodo.{record_id}"
    expected_url = f"https://zenodo.org/records/{record_id}"
    if doi != expected_doi or not DOI_PATTERN.fullmatch(str(doi)):
        raise ProspectiveGateError("receipt DOI does not match its Zenodo record_id")
    if archive_url != expected_url:
        raise ProspectiveGateError(
            "receipt archive_url is not the canonical Zenodo record URL"
        )
    return expected_doi


def _receipt_file(
    receipt: Mapping[str, Any],
    field: str,
) -> tuple[str, str]:
    value = _require_mapping(receipt.get(field), f"receipt {field}")
    filename = value.get("filename")
    digest = value.get("sha256")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ProspectiveGateError(f"receipt {field} filename is invalid")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ProspectiveGateError(f"receipt {field} SHA-256 is invalid")
    return filename, digest


def _read_https(
    url: str,
    *,
    maximum_bytes: int,
) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "zenodo.org":
        raise ProspectiveGateError("Zenodo API supplied an untrusted file URL")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "marlrefine-archive-gate/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > maximum_bytes:
                raise ProspectiveGateError("Zenodo response exceeds the safety limit")
            payload = response.read(maximum_bytes + 1)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ProspectiveGateError(
            f"Zenodo verification request failed: {exc}"
        ) from exc
    if len(payload) > maximum_bytes:
        raise ProspectiveGateError("Zenodo response exceeds the safety limit")
    return payload


def _hash_https(url: str, *, maximum_bytes: int) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "zenodo.org":
        raise ProspectiveGateError("Zenodo API supplied an untrusted file URL")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "marlrefine-archive-gate/1"},
    )
    digest = hashlib.sha256()
    consumed = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > maximum_bytes:
                raise ProspectiveGateError("Zenodo response exceeds the safety limit")
            while block := response.read(1024 * 1024):
                consumed += len(block)
                if consumed > maximum_bytes:
                    raise ProspectiveGateError(
                        "Zenodo response exceeds the safety limit"
                    )
                digest.update(block)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ProspectiveGateError(
            f"Zenodo verification request failed: {exc}"
        ) from exc
    return digest.hexdigest()


def _zenodo_file_url(
    record: Mapping[str, Any],
    filename: str,
) -> str:
    files = record.get("files")
    if not isinstance(files, list):
        raise ProspectiveGateError("Zenodo record has no file inventory")
    matches = [
        item
        for item in files
        if isinstance(item, Mapping) and item.get("key") == filename
    ]
    if len(matches) != 1:
        raise ProspectiveGateError(
            f"Zenodo record must contain exactly one {filename!r} file"
        )
    links = _require_mapping(matches[0].get("links"), f"Zenodo file {filename} links")
    content = links.get("content")
    if not isinstance(content, str):
        raise ProspectiveGateError(f"Zenodo file {filename} has no content URL")
    return content


def _verify_zenodo_publication(
    receipt: Mapping[str, Any],
    *,
    manifest_sha256: str,
    source_tree_sha256: str,
    uv_lock_sha256: str,
    published_at_utc: str,
) -> None:
    """Fetch the public immutable record and verify its deposited identities.

    The trust boundary is Zenodo over platform TLS. A local JSON receipt alone
    is never sufficient to authorize prospective execution.
    """
    record_id = int(receipt["record_id"])
    api_url = f"https://zenodo.org/api/records/{record_id}"
    api_payload = _read_https(api_url, maximum_bytes=MAX_ZENODO_METADATA_BYTES)
    try:
        record = json.loads(api_payload)
    except json.JSONDecodeError as exc:
        raise ProspectiveGateError("Zenodo returned invalid record metadata") from exc
    if not isinstance(record, Mapping):
        raise ProspectiveGateError("Zenodo record metadata is not an object")
    remote_doi = record.get("doi")
    if remote_doi is None and isinstance(record.get("metadata"), Mapping):
        remote_doi = record["metadata"].get("doi")
    if remote_doi != receipt.get("doi"):
        raise ProspectiveGateError("public Zenodo DOI differs from the receipt")
    remote_created = record.get("created")
    try:
        normalized_remote_created = _published_timestamp(remote_created)
    except ProspectiveGateError as exc:
        raise ProspectiveGateError(
            "public Zenodo record has no valid creation timestamp"
        ) from exc
    if normalized_remote_created != published_at_utc:
        raise ProspectiveGateError(
            "receipt published_at_utc differs from the public Zenodo timestamp"
        )

    bundle_filename, bundle_sha256 = _receipt_file(receipt, "protocol_bundle")
    identity_filename, identity_sha256 = _receipt_file(receipt, "identity_file")
    identity_payload = _read_https(
        _zenodo_file_url(record, identity_filename),
        maximum_bytes=MAX_IDENTITY_BYTES,
    )
    if hashlib.sha256(identity_payload).hexdigest() != identity_sha256:
        raise ProspectiveGateError("deposited protocol identity hash differs")
    try:
        identity = json.loads(
            identity_payload,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ProspectiveGateError) as exc:
        raise ProspectiveGateError("deposited protocol identity is invalid") from exc
    expected_identity = protocol_freeze_identity(
        manifest_sha256=manifest_sha256,
        source_tree_sha256=source_tree_sha256,
        uv_lock_sha256=uv_lock_sha256,
        bundle_filename=bundle_filename,
        bundle_sha256=bundle_sha256,
    )
    if identity != expected_identity:
        raise ProspectiveGateError(
            "deposited protocol identity does not match local frozen inputs"
        )

    observed_bundle_sha256 = _hash_https(
        _zenodo_file_url(record, bundle_filename),
        maximum_bytes=MAX_PROTOCOL_BUNDLE_BYTES,
    )
    if observed_bundle_sha256 != bundle_sha256:
        raise ProspectiveGateError("deposited protocol bundle hash differs")


def _verify_frozen_partition(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Verify the exact 113 = 7 discovery + 105 semantic + 1 exclusion split."""
    population = _require_mapping(manifest.get("population"), "population")
    population_names = _require_string_tuple(
        population.get("names"),
        "population.names",
    )
    if population.get("size") != 113 or len(population_names) != 113:
        raise ProspectiveGateError("population must contain exactly 113 names")
    if population_names != tuple(sorted(population_names)):
        raise ProspectiveGateError("population names must be canonically sorted")
    registered_names = tuple(game.short_name for game in default_loadable_types())
    if population_names != registered_names:
        raise ProspectiveGateError(
            "manifest population differs from the pinned runtime registry"
        )

    discovery = _require_mapping(manifest.get("discovery"), "discovery")
    discovery_names = _require_string_tuple(
        discovery.get("names"),
        "discovery.names",
    )
    if discovery.get("size") != 7 or discovery_names != DISCOVERY_GAME_NAMES:
        raise ProspectiveGateError("discovery partition differs from the frozen 7")

    validation = _require_mapping(manifest.get("validation"), "validation")
    accounting_names = _require_string_tuple(
        validation.get("accounting_names"),
        "validation.accounting_names",
    )
    expected_accounting = tuple(
        name for name in population_names if name not in discovery_names
    )
    if (
        validation.get("accounting_size") != EXPECTED_ACCOUNTING_SIZE
        or accounting_names != expected_accounting
    ):
        raise ProspectiveGateError(
            "validation accounting is not exactly population minus discovery"
        )

    exclusions = _require_mapping(
        validation.get("descriptive_exclusions"),
        "validation.descriptive_exclusions",
    )
    exclusion_names = _require_string_tuple(
        exclusions.get("names"),
        "validation.descriptive_exclusions.names",
    )
    if (
        exclusions.get("size") != 1
        or exclusion_names != KNOWN_DESCRIPTIVE_EXCLUSION_NAMES
    ):
        raise ProspectiveGateError(
            "crossword must be the sole known descriptive capability exclusion"
        )

    semantic = _require_mapping(
        validation.get("semantic_cohort"),
        "validation.semantic_cohort",
    )
    semantic_names = _require_string_tuple(
        semantic.get("names"),
        "validation.semantic_cohort.names",
    )
    expected_semantic = tuple(
        name for name in accounting_names if name not in exclusion_names
    )
    if (
        semantic.get("size") != EXPECTED_SEMANTIC_COHORT_SIZE
        or semantic_names != expected_semantic
    ):
        raise ProspectiveGateError(
            "semantic cohort is not exactly accounting minus crossword"
        )
    if (
        set(discovery_names).intersection(accounting_names)
        or set(semantic_names).intersection(exclusion_names)
        or set(population_names)
        != set(discovery_names).union(semantic_names, exclusion_names)
    ):
        raise ProspectiveGateError("study partitions are not disjoint and exhaustive")
    return semantic_names


def _verify_runtime_identity(
    manifest_environment: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    """Bind execution to the frozen interpreter and dependency identities.

    Executable-path fields remain descriptive. Python's implementation/version,
    every study package version, and the complete installed bytes of each
    third-party distribution are normative.
    """
    manifest_python = _require_mapping(
        manifest_environment.get("python"),
        "manifest environment python",
    )
    runtime_python = _require_mapping(
        provenance.get("python"),
        "runtime provenance python",
    )
    python_identity_fields = ("implementation", "version")
    manifest_python_identity = {
        field: manifest_python.get(field) for field in python_identity_fields
    }
    runtime_python_identity = {
        field: runtime_python.get(field) for field in python_identity_fields
    }
    if (
        any(
            not isinstance(value, str) or not value
            for value in manifest_python_identity.values()
        )
        or manifest_python_identity != runtime_python_identity
    ):
        raise ProspectiveGateError(
            "runtime Python implementation/version differs from the freeze"
        )

    manifest_packages = _require_mapping(
        manifest_environment.get("packages"),
        "manifest environment packages",
    )
    runtime_packages = _require_mapping(
        provenance.get("packages"),
        "runtime provenance packages",
    )
    expected_package_names = set(PINNED_PACKAGES)
    if set(manifest_packages) != expected_package_names:
        raise ProspectiveGateError(
            "manifest package identity must contain every pinned runtime package"
        )
    frozen_versions = {name: manifest_packages.get(name) for name in PINNED_PACKAGES}
    observed_versions = {name: runtime_packages.get(name) for name in PINNED_PACKAGES}
    if (
        any(
            not isinstance(value, str) or not value
            for value in frozen_versions.values()
        )
        or frozen_versions != observed_versions
    ):
        raise ProspectiveGateError(
            "installed pinned package versions differ from the freeze"
        )

    manifest_distributions = _require_mapping(
        manifest_environment.get("installed_distribution_sha256"),
        "manifest environment installed_distribution_sha256",
    )
    runtime_distributions = _require_mapping(
        provenance.get("installed_distribution_sha256"),
        "runtime provenance installed_distribution_sha256",
    )
    expected_distributions = set(BYTE_BOUND_DISTRIBUTIONS)
    if set(manifest_distributions) != expected_distributions:
        raise ProspectiveGateError(
            "manifest byte identity must contain every third-party distribution"
        )
    frozen_distribution_hashes = {
        name: manifest_distributions.get(name) for name in BYTE_BOUND_DISTRIBUTIONS
    }
    observed_distribution_hashes = {
        name: runtime_distributions.get(name) for name in BYTE_BOUND_DISTRIBUTIONS
    }
    if (
        any(
            not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value)
            for value in frozen_distribution_hashes.values()
        )
        or frozen_distribution_hashes != observed_distribution_hashes
    ):
        raise ProspectiveGateError(
            "installed distribution bytes differ from the freeze"
        )


def verify_archive_gate(
    manifest_path: Path,
    receipt_path: Path,
) -> ArchiveGate:
    """Verify a public receipt or explicit local unregistered authorization."""
    manifest = _read_object(manifest_path, gate_error=ProspectiveGateError)
    receipt = _read_object(receipt_path, gate_error=ProspectiveGateError)
    if manifest.get("schema_version") != 2:
        raise ProspectiveGateError("prospective execution requires manifest schema 2")
    if manifest.get("manifest_status") not in ACCEPTED_FROZEN_STATUSES:
        raise ProspectiveGateError(
            "manifest is not frozen; draft manifests cannot authorize execution"
        )
    artifact_type = receipt.get("artifact_type")
    is_public_archive = artifact_type == "marlrefine_protocol_archive_receipt"
    is_local_authorization = (
        artifact_type == "marlrefine_local_execution_authorization"
    )
    if receipt.get("schema_version") != 1 or not (
        is_public_archive or is_local_authorization
    ):
        raise ProspectiveGateError("execution authorization schema or type is invalid")
    if is_local_authorization:
        if set(receipt) != LOCAL_AUTHORIZATION_KEYS:
            raise ProspectiveGateError("local execution authorization keys differ")
        if receipt.get("preregistered") is not False:
            raise ProspectiveGateError(
                "local execution authorization must state preregistered=false"
            )
        if receipt.get("public_archive") is not False:
            raise ProspectiveGateError(
                "local execution authorization must state public_archive=false"
            )

    manifest_sha256 = _sha256(manifest_path)
    provenance = runtime_provenance()
    source_tree_sha256 = str(provenance["source_tree_sha256"])
    uv_lock_sha256 = str(provenance["uv_lock_sha256"])
    if receipt.get("manifest_sha256") != manifest_sha256:
        raise ProspectiveGateError("receipt does not match the manifest bytes")
    if receipt.get("source_tree_sha256") != source_tree_sha256:
        raise ProspectiveGateError("receipt does not match the executable source tree")
    if receipt.get("uv_lock_sha256") != uv_lock_sha256:
        raise ProspectiveGateError("receipt does not match the dependency lock")
    manifest_environment = _require_mapping(
        manifest.get("environment"),
        "manifest environment",
    )
    _verify_runtime_identity(manifest_environment, provenance)
    if manifest_environment.get("source_tree_sha256") != source_tree_sha256:
        raise ProspectiveGateError(
            "manifest source identity does not match the executable source tree"
        )
    if manifest_environment.get("uv_lock_sha256") != uv_lock_sha256:
        raise ProspectiveGateError(
            "manifest lock identity does not match the installed study lock"
        )

    targets = _require_mapping(manifest.get("target_versions"), "target_versions")
    installed = {
        "open_spiel": version("open-spiel"),
        "pettingzoo": version("pettingzoo"),
        "shimmy": version("shimmy"),
    }
    if dict(targets) != installed:
        raise ProspectiveGateError(
            f"installed package versions differ from the frozen targets: {installed}"
        )

    _verify_frozen_partition(manifest)

    schedule = _require_mapping(manifest.get("trace_schedule"), "trace_schedule")
    policies = _require_string_tuple(
        schedule.get("policies"),
        "trace_schedule.policies",
    )
    if policies != TRACE_POLICY_NAMES or schedule.get("per_case") != len(
        TRACE_POLICIES
    ):
        raise ProspectiveGateError(
            "manifest does not contain the frozen eight policies"
        )
    if schedule.get("decision_cap") != 1000:
        raise ProspectiveGateError("prospective decision_cap must be exactly 1000")
    if schedule.get("destination_call_cap") != PROSPECTIVE_DESTINATION_CALL_CAP:
        raise ProspectiveGateError(
            "prospective destination_call_cap differs from the frozen implementation"
        )
    if schedule.get("outcome_classifier_id") != CLASSIFIER_ID:
        raise ProspectiveGateError("prospective outcome classifier identity differs")
    if schedule.get("max_case_attempts") != MAX_CASE_ATTEMPTS:
        raise ProspectiveGateError("prospective maximum attempt count differs")
    if schedule.get("retry_eligibility") != PROSPECTIVE_RETRY_ELIGIBILITY:
        raise ProspectiveGateError("prospective retry eligibility differs")
    execution_contract = prospective_execution_contract()
    if manifest.get("execution_contract") != execution_contract:
        raise ProspectiveGateError(
            "manifest execution contract differs from the frozen implementation"
        )
    if (
        execution_contract.get("runner_protocol_version") != PROTOCOL_VERSION
        or execution_contract.get("chance_policy_id") != CHANCE_POLICY_ID
        or execution_contract.get("policy_engine_id") != POLICY_ENGINE_ID
        or tuple(execution_contract.get("project_baseline_ids", ())) != BASELINE_NAMES
    ):
        raise ProspectiveGateError(
            "frozen execution contract constants are inconsistent"
        )
    if manifest.get("external_baselines") != external_baseline_protocol():
        raise ProspectiveGateError(
            "manifest external-baseline protocol differs from the frozen implementation"
        )

    if is_public_archive:
        published_at_utc = _published_timestamp(receipt.get("published_at_utc"))
        archive_identifier = _archive_identifier(receipt)
        _receipt_file(receipt, "protocol_bundle")
        _receipt_file(receipt, "identity_file")
        _verify_zenodo_publication(
            receipt,
            manifest_sha256=manifest_sha256,
            source_tree_sha256=source_tree_sha256,
            uv_lock_sha256=uv_lock_sha256,
            published_at_utc=published_at_utc,
        )
    else:
        published_at_utc = _published_timestamp(receipt.get("authorized_at_utc"))
        source_revision = receipt.get("source_git_revision")
        manifest_revision = manifest_environment.get("git_revision")
        if (
            not isinstance(source_revision, str)
            or not GIT_REVISION_PATTERN.fullmatch(source_revision)
            or source_revision != manifest_revision
        ):
            raise ProspectiveGateError(
                "local authorization source revision differs from the manifest"
            )
        expected_authorization_id = f"local-unregistered:{manifest_sha256}"
        if receipt.get("authorization_id") != expected_authorization_id:
            raise ProspectiveGateError("local execution authorization ID differs")
        archive_identifier = expected_authorization_id
    return ArchiveGate(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        source_tree_sha256=source_tree_sha256,
        uv_lock_sha256=uv_lock_sha256,
        receipt_sha256=_sha256(receipt_path),
        archive_identifier=archive_identifier,
        published_at_utc=published_at_utc,
    )


def build_prospective_plan(
    manifest_path: Path,
    receipt_path: Path,
) -> ProspectivePlan:
    """Derive the ordered game/policy product only after the archive gate passes."""
    gate = verify_archive_gate(manifest_path, receipt_path)
    semantic = gate.manifest["validation"]["semantic_cohort"]
    names = tuple(semantic["names"])
    cases = tuple(
        ProspectiveCase(ordinal, game_name, policy)
        for ordinal, (game_name, policy) in enumerate(
            (game_name, policy) for game_name in names for policy in TRACE_POLICIES
        )
    )
    if any(case.game_name == KNOWN_DESCRIPTIVE_EXCLUSION for case in cases):
        raise ProspectiveGateError("crossword reached the semantic case product")
    return ProspectivePlan(
        gate=gate,
        decision_cap=int(gate.manifest["trace_schedule"]["decision_cap"]),
        destination_call_cap=int(
            gate.manifest["trace_schedule"]["destination_call_cap"]
        ),
        cases=cases,
    )


def classify_run_payload(run: Mapping[str, Any]) -> OutcomeStatus:
    """Apply the frozen, mutually exclusive primary outcome classifier."""
    summary_value = run.get("summary", {})
    violations_value = run.get("violations", [])
    applicable = run.get("applicable")
    if (
        not isinstance(summary_value, Mapping)
        or not isinstance(violations_value, list)
        or not isinstance(applicable, bool)
        or any(
            not isinstance(item, Mapping) or not isinstance(item.get("code"), str)
            for item in violations_value
        )
    ):
        return OutcomeStatus.INFRASTRUCTURE
    summary = summary_value
    violations = violations_value
    codes = {
        item.get("code")
        for item in violations
        if isinstance(item, Mapping) and isinstance(item.get("code"), str)
    }
    setup_status = str(summary.get("setup_status", ""))
    if codes.intersection(UNALIGNABLE_CODES):
        return OutcomeStatus.UNALIGNABLE
    if setup_status.startswith("error:source_setup:") or codes.intersection(
        INFRASTRUCTURE_CODES
    ):
        return OutcomeStatus.INFRASTRUCTURE
    if not applicable:
        return OutcomeStatus.INAPPLICABLE
    if violations:
        return OutcomeStatus.FAIL
    return OutcomeStatus.PASS


def classify_case_record(record: Mapping[str, Any]) -> OutcomeStatus:
    """Classify either a completed TraceRun or a caught runner exception."""
    if record.get("infrastructure_error") is not None:
        return OutcomeStatus.INFRASTRUCTURE
    run = record.get("run")
    if not isinstance(run, Mapping):
        return OutcomeStatus.INFRASTRUCTURE
    return classify_run_payload(run)


def _retryable_infrastructure(record: Mapping[str, Any]) -> bool:
    """Limit the single retry to exceptions outside a completed trace run.

    A fixed-budget exhaustion or source capability failure remains visible in
    the infrastructure status, but rerunning the identical deterministic case
    cannot repair it. Only a caught harness/orchestration exception (no run
    payload was produced) is eligible for attempt two.
    """
    return (
        classify_case_record(record) is OutcomeStatus.INFRASTRUCTURE
        and record.get("run") is None
        and isinstance(record.get("infrastructure_error"), Mapping)
    )


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        to_jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected.difference(observed))
        extra = sorted(observed.difference(expected))
        raise ResumeError(f"{label} schema differs (missing={missing}, extra={extra})")


def _same_typed_json_value(observed: Any, expected: Any) -> bool:
    """Compare decoded JSON without treating booleans as integers."""
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _same_typed_json_value(observed[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _same_typed_json_value(left, right)
            for left, right in zip(observed, expected, strict=True)
        )
    return observed == expected


def _is_exact_integer(value: Any, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _case_metadata(case: ProspectiveCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "ordinal": case.ordinal,
        "game_name": case.game_name,
        "trace_policy_name": case.policy.name,
        "trace_policy_id": case.policy.policy_id,
        "trace_policy_seed": case.policy.seed,
        "environment_seed": case.policy.environment_seed,
    }


def _execute_case(
    plan: ProspectivePlan,
    case: ProspectiveCase,
    runner: Callable[..., TraceRun],
    *,
    attempt: int,
    prior_record_sha256: str | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_case",
        "classifier_id": CLASSIFIER_ID,
        "manifest_sha256": plan.gate.manifest_sha256,
        "source_tree_sha256": plan.gate.source_tree_sha256,
        "uv_lock_sha256": plan.gate.uv_lock_sha256,
        "case": _case_metadata(case),
        "attempt": attempt,
        "prior_record_sha256": prior_record_sha256,
    }
    started_ns = perf_counter_ns()
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
            trace_run = runner(
                case.game_name,
                seed=case.policy.environment_seed,
                trace_policy=case.policy,
                max_destination_calls=plan.destination_call_cap,
                max_source_decisions=plan.decision_cap,
            )
        run_payload = trace_run.to_dict()
        validate_serialized_obligation_evaluations(
            run_payload.get("obligation_evaluations"),
            violations=run_payload.get("violations"),
            alignment=run_payload.get("alignment"),
            summary=run_payload.get("summary"),
            caller_supplied_nondefault=run_payload.get("summary", {}).get(
                "caller_supplied_nondefault_configuration"
            ),
            label=f"obligation ledger for {case.case_id}",
        )
        base["run"] = run_payload
        base["infrastructure_error"] = None
    except Exception as exc:
        base["run"] = None
        base["infrastructure_error"] = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
    base["captured_stdout"] = captured_stdout.getvalue()
    base["captured_stderr"] = captured_stderr.getvalue()
    base["elapsed_ns"] = perf_counter_ns() - started_ns
    base["status"] = classify_case_record(base).value
    return base


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.endswith(b"\n"):
                    raise ResumeError(
                        f"JSONL line {line_number} lacks its canonical LF terminator"
                    )
                if b"\r" in raw_line:
                    raise ResumeError(f"JSONL line {line_number} contains CR bytes")
                raw_payload = raw_line[:-1]
                if not raw_payload:
                    raise ResumeError(f"blank JSONL line {line_number}")
                try:
                    line = raw_payload.decode("utf-8")
                    value = json.loads(
                        line,
                        object_pairs_hook=_reject_duplicate_keys,
                        parse_constant=lambda token: (_ for _ in ()).throw(
                            ValueError(f"non-finite JSON constant {token}")
                        ),
                    )
                except (
                    UnicodeDecodeError,
                    ValueError,
                    json.JSONDecodeError,
                    ProspectiveGateError,
                ) as exc:
                    raise ResumeError(
                        f"invalid JSONL line {line_number}: {exc}"
                    ) from exc
                if not isinstance(value, dict):
                    raise ResumeError(f"JSONL line {line_number} is not an object")
                canonical = json.dumps(
                    value,
                    allow_nan=False,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if line != canonical:
                    raise ResumeError(f"JSONL line {line_number} is not canonical")
                yield value
    except OSError as exc:
        raise ResumeError(f"cannot read prior batch {path.name}: {exc}") from exc


def _validate_prior_case(
    record: Mapping[str, Any],
    case: ProspectiveCase,
    plan: ProspectivePlan,
) -> OutcomeStatus:
    _require_exact_keys(record, CASE_RECORD_KEYS, "prior case record")
    if not _is_exact_integer(record.get("schema_version"), BATCH_SCHEMA_VERSION):
        raise ResumeError("prior case schema version differs")
    if record.get("artifact_type") != "marlrefine_prospective_case":
        raise ResumeError("prior batch contains a non-case record in its case body")
    if record.get("classifier_id") != CLASSIFIER_ID:
        raise ResumeError("prior batch used a different classifier")
    if record.get("manifest_sha256") != plan.gate.manifest_sha256:
        raise ResumeError("prior case manifest identity differs")
    if record.get("source_tree_sha256") != plan.gate.source_tree_sha256:
        raise ResumeError("prior case source identity differs")
    if record.get("uv_lock_sha256") != plan.gate.uv_lock_sha256:
        raise ResumeError("prior case dependency-lock identity differs")
    case_metadata = record.get("case")
    if not isinstance(case_metadata, Mapping):
        raise ResumeError(f"prior case metadata is invalid for {case.case_id}")
    _require_exact_keys(case_metadata, CASE_METADATA_KEYS, "prior case metadata")
    if not _same_typed_json_value(case_metadata, _case_metadata(case)):
        raise ResumeError(
            f"prior case order or identity differs at ordinal {case.ordinal}"
        )
    run = record.get("run")
    infrastructure_error = record.get("infrastructure_error")
    if run is None:
        if not isinstance(infrastructure_error, Mapping) or set(
            infrastructure_error
        ) != {"exception_type", "message"}:
            raise ResumeError(f"invalid infrastructure error for {case.case_id}")
        if not all(isinstance(value, str) for value in infrastructure_error.values()):
            raise ResumeError(f"invalid infrastructure error for {case.case_id}")
    else:
        if infrastructure_error is not None or not isinstance(run, Mapping):
            raise ResumeError(f"prior run/error union is invalid for {case.case_id}")
        _require_exact_keys(run, TRACE_RUN_KEYS, "prior trace run")
        if run.get("game_spec") != case.game_name:
            raise ResumeError(f"prior run game differs for {case.case_id}")
        if not _is_exact_integer(run.get("seed"), case.policy.environment_seed):
            raise ResumeError(f"prior run seed differs for {case.case_id}")
        if not isinstance(run.get("applicable"), bool):
            raise ResumeError(f"prior run applicability is invalid for {case.case_id}")
        for field in (
            "source_events",
            "destination_events",
            "baselines",
            "violations",
            "obligation_evaluations",
        ):
            if not isinstance(run.get(field), list):
                raise ResumeError(f"prior run {field} is invalid for {case.case_id}")
        if not isinstance(run.get("alignment"), Mapping) or not isinstance(
            run.get("summary"), Mapping
        ):
            raise ResumeError(f"prior run ledger is invalid for {case.case_id}")
        if run["summary"].get("trace_policy_name") != case.policy.name:
            raise ResumeError(f"prior run policy differs for {case.case_id}")
        try:
            validate_serialized_obligation_evaluations(
                run["obligation_evaluations"],
                violations=run["violations"],
                alignment=run["alignment"],
                summary=run["summary"],
                caller_supplied_nondefault=run["summary"].get(
                    "caller_supplied_nondefault_configuration"
                ),
                label=f"prior obligation ledger for {case.case_id}",
            )
        except ValueError as exc:
            raise ResumeError(str(exc)) from exc
    if not isinstance(record.get("captured_stdout"), str) or not isinstance(
        record.get("captured_stderr"), str
    ):
        raise ResumeError(f"prior captured output is invalid for {case.case_id}")
    elapsed_ns = record.get("elapsed_ns")
    if (
        not isinstance(elapsed_ns, int)
        or isinstance(elapsed_ns, bool)
        or elapsed_ns < 0
    ):
        raise ResumeError(f"prior elapsed time is invalid for {case.case_id}")
    classified = classify_case_record(record)
    if record.get("status") != classified.value:
        raise ResumeError(f"stored status disagrees with classifier for {case.case_id}")
    attempt = record.get("attempt")
    if (
        not _is_exact_integer(attempt, 1)
        or record.get("prior_record_sha256") is not None
    ):
        raise ResumeError(f"prior case is not an original attempt for {case.case_id}")
    return classified


def _inspect_resume(path: Path, plan: ProspectivePlan) -> int:
    records = _read_jsonl(path)
    try:
        header = next(records)
    except StopIteration as exc:
        raise ResumeError("prior batch is empty") from exc
    _require_exact_keys(header, HEADER_KEYS, "prior batch header")
    if not _is_exact_integer(header.get("schema_version"), BATCH_SCHEMA_VERSION):
        raise ResumeError("prior batch header schema version differs")
    if header.get("artifact_type") != "marlrefine_prospective_batch_header":
        raise ResumeError("prior batch header is missing")
    if header.get("manifest_sha256") != plan.gate.manifest_sha256:
        raise ResumeError("prior batch manifest identity differs")
    if header.get("source_tree_sha256") != plan.gate.source_tree_sha256:
        raise ResumeError("prior batch source identity differs")
    if header.get("uv_lock_sha256") != plan.gate.uv_lock_sha256:
        raise ResumeError("prior batch dependency-lock identity differs")
    if header.get("classifier_id") != CLASSIFIER_ID:
        raise ResumeError("prior batch classifier identity differs")
    if header.get("obligation_ledger_schema_id") != OBLIGATION_LEDGER_SCHEMA_ID:
        raise ResumeError("prior batch obligation-ledger identity differs")
    if header.get("receipt_sha256") != plan.gate.receipt_sha256:
        raise ResumeError("prior batch archive receipt identity differs")
    if header.get("archive_identifier") != plan.gate.archive_identifier:
        raise ResumeError("prior batch public archive identity differs")
    if header.get("archive_published_at_utc") != plan.gate.published_at_utc:
        raise ResumeError("prior batch publication timestamp differs")
    if not _is_exact_integer(header.get("case_count"), len(plan.cases)):
        raise ResumeError("prior batch header case count differs")
    if not _is_exact_integer(header.get("decision_cap"), plan.decision_cap):
        raise ResumeError("prior batch decision cap differs")
    if not _is_exact_integer(
        header.get("destination_call_cap"), plan.destination_call_cap
    ):
        raise ResumeError("prior batch destination call cap differs")
    if not _is_exact_integer(header.get("max_case_attempts"), MAX_CASE_ATTEMPTS):
        raise ResumeError("prior batch maximum attempt count differs")
    if header.get("retry_eligibility") != PROSPECTIVE_RETRY_ELIGIBILITY:
        raise ResumeError("prior batch retry eligibility differs")
    if header.get("known_descriptive_exclusions") != [KNOWN_DESCRIPTIVE_EXCLUSION]:
        raise ResumeError("prior batch descriptive exclusion differs")
    if header.get("resume_infrastructure_from_sha256") is not None:
        raise ResumeError("only an original primary batch may seed a retry")
    runtime = header.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ResumeError("prior batch runtime provenance is invalid")
    _require_exact_keys(runtime, RUNTIME_PROVENANCE_KEYS, "prior runtime provenance")
    if runtime.get("source_tree_sha256") != plan.gate.source_tree_sha256:
        raise ResumeError("prior runtime source identity differs")
    if runtime.get("uv_lock_sha256") != plan.gate.uv_lock_sha256:
        raise ResumeError("prior runtime dependency-lock identity differs")
    try:
        _verify_runtime_identity(runtime, runtime_provenance())
    except ProspectiveGateError as exc:
        raise ResumeError(
            f"prior runtime identity is no longer executable: {exc}"
        ) from exc

    counts: Counter[str] = Counter()
    rerunnable = 0
    for case in plan.cases:
        try:
            record = next(records)
        except StopIteration as exc:
            raise ResumeError("prior batch ended before all frozen cases") from exc
        status = _validate_prior_case(record, case, plan)
        counts[status.value] += 1
        if _retryable_infrastructure(record):
            rerunnable += 1
    try:
        footer = next(records)
    except StopIteration as exc:
        raise ResumeError("prior batch footer is missing") from exc
    _require_exact_keys(footer, FOOTER_KEYS, "prior batch footer")
    if not _is_exact_integer(footer.get("schema_version"), BATCH_SCHEMA_VERSION):
        raise ResumeError("prior batch footer schema version differs")
    if footer.get("artifact_type") != "marlrefine_prospective_batch_footer":
        raise ResumeError("prior batch footer is invalid")
    if footer.get("classifier_id") != CLASSIFIER_ID:
        raise ResumeError("prior footer classifier identity differs")
    if footer.get("manifest_sha256") != plan.gate.manifest_sha256:
        raise ResumeError("prior footer manifest identity differs")
    if footer.get("source_tree_sha256") != plan.gate.source_tree_sha256:
        raise ResumeError("prior footer source identity differs")
    if footer.get("uv_lock_sha256") != plan.gate.uv_lock_sha256:
        raise ResumeError("prior footer dependency-lock identity differs")
    if not _is_exact_integer(footer.get("case_count"), len(plan.cases)):
        raise ResumeError("prior footer case count differs")
    if not _same_typed_json_value(
        footer.get("status_counts"), dict(sorted(counts.items()))
    ):
        raise ResumeError("prior footer counts do not match the case records")
    if not _is_exact_integer(footer.get("resumed_infrastructure_cases"), 0):
        raise ResumeError("prior footer is not an original primary batch")
    try:
        next(records)
    except StopIteration:
        pass
    else:
        raise ResumeError("prior batch has records after its footer")
    if rerunnable == 0:
        raise ResumeError("prior batch has no eligible infrastructure retry")
    return rerunnable


def _prior_case_records(path: Path) -> Iterator[dict[str, Any]]:
    records = _read_jsonl(path)
    next(records)  # validated header
    for record in records:
        if record.get("artifact_type") == "marlrefine_prospective_batch_footer":
            return
        yield record


def _batch_header(
    plan: ProspectivePlan,
    *,
    resume_infrastructure_from_sha256: str | None,
    runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_batch_header",
        "classifier_id": CLASSIFIER_ID,
        "obligation_ledger_schema_id": OBLIGATION_LEDGER_SCHEMA_ID,
        "manifest_sha256": plan.gate.manifest_sha256,
        "source_tree_sha256": plan.gate.source_tree_sha256,
        "uv_lock_sha256": plan.gate.uv_lock_sha256,
        "receipt_sha256": plan.gate.receipt_sha256,
        "archive_identifier": plan.gate.archive_identifier,
        "archive_published_at_utc": plan.gate.published_at_utc,
        "case_count": len(plan.cases),
        "decision_cap": plan.decision_cap,
        "destination_call_cap": plan.destination_call_cap,
        "max_case_attempts": MAX_CASE_ATTEMPTS,
        "retry_eligibility": PROSPECTIVE_RETRY_ELIGIBILITY,
        "known_descriptive_exclusions": [KNOWN_DESCRIPTIVE_EXCLUSION],
        "resume_infrastructure_from_sha256": (
            resume_infrastructure_from_sha256
        ),
        "runtime": dict(runtime) if runtime is not None else runtime_provenance(),
    }


def _batch_footer(
    plan: ProspectivePlan,
    status_counts: Mapping[str, int],
    *,
    resumed_infrastructure_cases: int,
) -> dict[str, Any]:
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_batch_footer",
        "classifier_id": CLASSIFIER_ID,
        "manifest_sha256": plan.gate.manifest_sha256,
        "source_tree_sha256": plan.gate.source_tree_sha256,
        "uv_lock_sha256": plan.gate.uv_lock_sha256,
        "case_count": len(plan.cases),
        "status_counts": dict(sorted(status_counts.items())),
        "resumed_infrastructure_cases": resumed_infrastructure_cases,
    }


def _primary_checkpoint_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.name}{PRIMARY_CHECKPOINT_SUFFIX}")


def _checkpoint_case_path(checkpoint: Path, ordinal: int) -> Path:
    return checkpoint / f"case-{ordinal:06d}.bin"


def _fsync_directory(path: Path) -> None:
    """Persist checkpoint directory entries where the platform supports it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _checkpoint_wrapper(header: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": PRIMARY_CHECKPOINT_SCHEMA_VERSION,
        "artifact_type": "marlrefine_blind_primary_checkpoint",
        "batch_header": dict(header),
        "disclosure_policy": (
            "no console progress, status counts, semantic summaries, or "
            "status-bearing filenames before atomic batch seal"
        ),
    }


def _validate_checkpoint_header(
    value: Mapping[str, Any], plan: ProspectivePlan
) -> dict[str, Any]:
    if set(value) != {
        "schema_version",
        "artifact_type",
        "batch_header",
        "disclosure_policy",
    }:
        raise ResumeError("primary checkpoint header schema differs")
    if (
        not _is_exact_integer(
            value.get("schema_version"), PRIMARY_CHECKPOINT_SCHEMA_VERSION
        )
        or value.get("artifact_type") != "marlrefine_blind_primary_checkpoint"
    ):
        raise ResumeError("primary checkpoint identity differs")
    header = value.get("batch_header")
    if not isinstance(header, Mapping):
        raise ResumeError("primary checkpoint batch header is invalid")
    _require_exact_keys(header, HEADER_KEYS, "primary checkpoint batch header")
    expected = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_batch_header",
        "classifier_id": CLASSIFIER_ID,
        "obligation_ledger_schema_id": OBLIGATION_LEDGER_SCHEMA_ID,
        "manifest_sha256": plan.gate.manifest_sha256,
        "source_tree_sha256": plan.gate.source_tree_sha256,
        "uv_lock_sha256": plan.gate.uv_lock_sha256,
        "receipt_sha256": plan.gate.receipt_sha256,
        "archive_identifier": plan.gate.archive_identifier,
        "archive_published_at_utc": plan.gate.published_at_utc,
        "case_count": len(plan.cases),
        "decision_cap": plan.decision_cap,
        "destination_call_cap": plan.destination_call_cap,
        "max_case_attempts": MAX_CASE_ATTEMPTS,
        "retry_eligibility": PROSPECTIVE_RETRY_ELIGIBILITY,
        "known_descriptive_exclusions": [KNOWN_DESCRIPTIVE_EXCLUSION],
        "resume_infrastructure_from_sha256": None,
    }
    for field, expected_value in expected.items():
        if not _same_typed_json_value(header.get(field), expected_value):
            raise ResumeError(f"primary checkpoint {field} differs")
    runtime = header.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ResumeError("primary checkpoint runtime is invalid")
    _require_exact_keys(runtime, RUNTIME_PROVENANCE_KEYS, "checkpoint runtime")
    if runtime.get("source_tree_sha256") != plan.gate.source_tree_sha256:
        raise ResumeError("primary checkpoint runtime source identity differs")
    if runtime.get("uv_lock_sha256") != plan.gate.uv_lock_sha256:
        raise ResumeError("primary checkpoint runtime lock identity differs")
    try:
        _verify_runtime_identity(runtime, runtime_provenance())
    except ProspectiveGateError as exc:
        raise ResumeError(
            f"primary checkpoint runtime is no longer executable: {exc}"
        ) from exc
    return dict(header)


def _read_checkpoint_object(path: Path, label: str) -> dict[str, Any]:
    try:
        return _read_object(path, gate_error=ResumeError)
    except ProspectiveGateError as exc:
        raise ResumeError(f"invalid {label}: {exc}") from exc


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        to_jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_checkpoint_case(path: Path, record: Mapping[str, Any]) -> None:
    """Atomically persist one compact opaque shard with no plaintext status."""
    payload = gzip.compress(_canonical_json_bytes(record), compresslevel=6, mtime=0)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _read_checkpoint_case(path: Path, label: str) -> dict[str, Any]:
    try:
        compressed = path.read_bytes()
        payload = gzip.decompress(compressed)
        line = payload.decode("utf-8")
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError, zlib.error) as exc:
        raise ResumeError(f"cannot read {label}: {exc}") from exc
    try:
        value = json.loads(
            line,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {token}")
            ),
        )
    except (ValueError, json.JSONDecodeError, ProspectiveGateError) as exc:
        raise ResumeError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict) or line.encode("utf-8") != _canonical_json_bytes(
        value
    ):
        raise ResumeError(f"{label} is not canonical")
    return value


def _remove_orphan_checkpoint_temporaries(checkpoint: Path) -> None:
    for path in checkpoint.iterdir():
        name = path.name
        if (
            path.is_file()
            and name.startswith(".case-")
            and name.endswith(".tmp")
        ) or (
            path.is_file()
            and name.startswith(f".{PRIMARY_CHECKPOINT_HEADER}.")
            and name.endswith(".tmp")
        ):
            path.unlink(missing_ok=True)


def _open_primary_checkpoint(
    output_path: Path, plan: ProspectivePlan
) -> tuple[Path, dict[str, Any], int, Counter[str]]:
    """Open or create the hidden checkpoint and validate its contiguous prefix."""
    checkpoint = _primary_checkpoint_path(output_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    try:
        checkpoint.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    if not checkpoint.is_dir():
        raise ResumeError("primary checkpoint path is not a directory")
    _remove_orphan_checkpoint_temporaries(checkpoint)
    header_path = checkpoint / PRIMARY_CHECKPOINT_HEADER
    if created:
        header = _batch_header(
            plan,
            resume_infrastructure_from_sha256=None,
        )
        write_json(header_path, _checkpoint_wrapper(header))
        os.chmod(header_path, 0o600)
        _fsync_directory(checkpoint)
    elif not header_path.exists():
        if any(checkpoint.iterdir()):
            raise ResumeError("primary checkpoint header is missing")
        header = _batch_header(
            plan,
            resume_infrastructure_from_sha256=None,
        )
        write_json(header_path, _checkpoint_wrapper(header))
        os.chmod(header_path, 0o600)
        _fsync_directory(checkpoint)

    wrapper = _read_checkpoint_object(header_path, "primary checkpoint header")
    header = _validate_checkpoint_header(wrapper, plan)
    case_pattern = re.compile(r"^case-([0-9]{6})\.bin$")
    observed_indices: set[int] = set()
    for path in checkpoint.iterdir():
        if path.name == PRIMARY_CHECKPOINT_HEADER:
            continue
        match = case_pattern.fullmatch(path.name)
        if not path.is_file() or match is None:
            raise ResumeError(
                f"primary checkpoint contains unexpected entry {path.name!r}"
            )
        observed_indices.add(int(match.group(1)))
    expected_indices = set(range(len(observed_indices)))
    if observed_indices != expected_indices or any(
        index >= len(plan.cases) for index in observed_indices
    ):
        raise ResumeError("primary checkpoint case prefix is not contiguous")

    counts: Counter[str] = Counter()
    for ordinal in range(len(observed_indices)):
        record = _read_checkpoint_case(
            _checkpoint_case_path(checkpoint, ordinal),
            f"primary checkpoint case {ordinal}",
        )
        status = _validate_prior_case(record, plan.cases[ordinal], plan)
        counts[status.value] += 1
    return checkpoint, header, len(observed_indices), counts


def _cleanup_primary_checkpoint(checkpoint: Path, case_count: int) -> None:
    """Remove only the exact private files created by this runner."""
    for ordinal in range(case_count):
        _checkpoint_case_path(checkpoint, ordinal).unlink(missing_ok=True)
    (checkpoint / PRIMARY_CHECKPOINT_HEADER).unlink(missing_ok=True)
    _remove_orphan_checkpoint_temporaries(checkpoint)
    # An unexpected user-created entry is never deleted recursively.
    with suppress(OSError):
        checkpoint.rmdir()


def _execute_primary_with_checkpoint(
    plan: ProspectivePlan,
    output_path: Path,
    runner: Callable[..., TraceRun],
) -> BatchSummary:
    checkpoint, header, next_ordinal, status_counts = _open_primary_checkpoint(
        output_path, plan
    )
    for case in plan.cases[next_ordinal:]:
        record = _execute_case(plan, case, runner, attempt=1)
        case_path = _checkpoint_case_path(checkpoint, case.ordinal)
        if case_path.exists():
            raise ResumeError("primary checkpoint case appeared concurrently")
        _write_checkpoint_case(case_path, record)
        _fsync_directory(checkpoint)
        status_counts[str(record["status"])] += 1

    sealed_counts: Counter[str] = Counter()

    def records() -> Iterator[dict[str, Any]]:
        yield header
        for case in plan.cases:
            record = _read_checkpoint_case(
                _checkpoint_case_path(checkpoint, case.ordinal),
                f"primary checkpoint case {case.ordinal}",
            )
            status = _validate_prior_case(record, case, plan)
            sealed_counts[status.value] += 1
            yield record
        yield _batch_footer(
            plan,
            sealed_counts,
            resumed_infrastructure_cases=0,
        )

    write_jsonl(output_path, records())
    if sealed_counts != status_counts or sum(sealed_counts.values()) != len(plan.cases):
        output_path.unlink(missing_ok=True)
        raise ResumeError("primary checkpoint counts changed during atomic seal")
    _cleanup_primary_checkpoint(checkpoint, len(plan.cases))
    return BatchSummary(
        output=output_path,
        case_count=len(plan.cases),
        status_counts=dict(sorted(sealed_counts.items())),
        resumed_infrastructure_cases=0,
    )


def execute_prospective_batch(
    manifest_path: Path,
    receipt_path: Path,
    output_path: Path,
    *,
    resume_infrastructure_from: Path | None = None,
    runner: Callable[..., TraceRun] = run_trace,
) -> BatchSummary:
    """Execute and atomically seal a quiet prospective semantic batch.

    ``runner`` supports discovery-only test doubles; the CLI does not expose
    that injection and always executes the archived 105-name case product.
    """
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {output_path}")
    if (
        resume_infrastructure_from is not None
        and output_path.resolve() == resume_infrastructure_from.resolve()
    ):
        raise ResumeError("infrastructure retry output must use a new path")

    plan = build_prospective_plan(manifest_path, receipt_path)
    if resume_infrastructure_from is None:
        return _execute_primary_with_checkpoint(plan, output_path, runner)

    resumable_count = 0
    prior_sha256: str | None = None
    prior_sha256 = _sha256(resume_infrastructure_from)
    resumable_count = _inspect_resume(resume_infrastructure_from, plan)
    if _sha256(resume_infrastructure_from) != prior_sha256:
        raise ResumeError("prior batch changed during resume validation")

    status_counts: Counter[str] = Counter()
    resumed_count = 0

    def records() -> Iterator[dict[str, Any]]:
        nonlocal resumed_count
        yield _batch_header(
            plan,
            resume_infrastructure_from_sha256=prior_sha256,
        )
        prior_records = (
            _prior_case_records(resume_infrastructure_from)
            if resume_infrastructure_from is not None
            else None
        )
        if (
            resume_infrastructure_from is not None
            and _sha256(resume_infrastructure_from) != prior_sha256
        ):
            raise ResumeError("prior batch changed before retry execution")
        for case in plan.cases:
            prior = next(prior_records) if prior_records is not None else None
            prior_attempt = int(prior["attempt"]) if prior is not None else 0

            if (
                prior is not None
                and _retryable_infrastructure(prior)
                and prior_attempt < MAX_CASE_ATTEMPTS
            ):
                record = _execute_case(
                    plan,
                    case,
                    runner,
                    attempt=prior_attempt + 1,
                    prior_record_sha256=_canonical_digest(prior),
                )
                resumed_count += 1
            elif prior is not None:
                record = prior
            else:
                record = _execute_case(plan, case, runner, attempt=1)
            status_counts[str(record["status"])] += 1
            yield record
        if resumed_count != resumable_count:
            raise ResumeError(
                "infrastructure retry count changed during sealed execution"
            )
        if (
            resume_infrastructure_from is not None
            and _sha256(resume_infrastructure_from) != prior_sha256
        ):
            raise ResumeError("prior batch changed during retry execution")
        yield _batch_footer(
            plan,
            status_counts,
            resumed_infrastructure_cases=resumed_count,
        )

    write_jsonl(output_path, records())
    if resumed_count != resumable_count:
        raise ResumeError("infrastructure retry count changed during sealed execution")
    return BatchSummary(
        output=output_path,
        case_count=len(plan.cases),
        status_counts=dict(sorted(status_counts.items())),
        resumed_infrastructure_cases=resumed_count,
    )
