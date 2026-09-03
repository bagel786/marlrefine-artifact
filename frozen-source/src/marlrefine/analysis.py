"""Frozen, non-adjudicating analysis of a sealed prospective JSONL batch.

This module reads evidence only after execution.  It cannot run a game, retry a
case, or turn repeated symptoms into causal roots.  Its responsibilities are
limited to byte/schema/identity validation, exact trace and game accounting,
descriptive aggregation, and original-prefix witness localization.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from marlrefine.baselines import BASELINE_NAMES
from marlrefine.evaluation import (
    OBLIGATION_IDS,
    OBLIGATION_LEDGER_SCHEMA_ID,
    validate_serialized_obligation_evaluations,
)
from marlrefine.external_baselines import (
    EXTERNAL_BASELINE_CLASSIFIER_ID,
    EXTERNAL_BASELINE_SCHEMA_VERSION,
)
from marlrefine.localization import LOCALIZER_ID, localize_all_divergences
from marlrefine.mutation_study import (
    MUTATION_BATCH_ARTIFACT_TYPE,
    MUTATION_BATCH_SCHEMA_VERSION,
    MutationBatchValidationError,
    validate_mutation_batch,
)
from marlrefine.mutations import MUTATION_FAMILIES, MUTATION_PROTOCOL_ID
from marlrefine.policies import TRACE_POLICIES, TRACE_POLICY_NAMES
from marlrefine.prospective import (
    BATCH_SCHEMA_VERSION,
    CLASSIFIER_ID,
    EXPECTED_SEMANTIC_COHORT_SIZE,
    KNOWN_DESCRIPTIVE_EXCLUSION,
    LOCAL_AUTHORIZATION_KEYS,
    OutcomeStatus,
    classify_case_record,
)
from marlrefine.provenance import runtime_provenance
from marlrefine.serialization import to_jsonable, write_json
from marlrefine.study import (
    PROSPECTIVE_DESTINATION_CALL_CAP,
    PROSPECTIVE_MAX_CASE_ATTEMPTS,
    PROSPECTIVE_RETRY_ELIGIBILITY,
    SHIMMY_OPENSPIEL_TEST_MEMBER,
    SHIMMY_OPENSPIEL_TEST_SHA256,
    SHIMMY_SDIST_SHA256,
    SHIMMY_SDIST_URL,
    STOCK_API_ACTION_SPACE_SEED,
    STOCK_API_CYCLES,
    external_baseline_protocol,
    prospective_execution_contract,
)

ANALYSIS_SCHEMA_VERSION = 9
ANALYSIS_ID = "marlrefine_frozen_analysis_v9"
STRUCTURED_MANUAL_SCHEMA_VERSION = 5
EXPECTED_TRACE_COUNT = EXPECTED_SEMANTIC_COHORT_SIZE * len(TRACE_POLICIES)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DOI_PATTERN = re.compile(r"^10\.5281/zenodo\.\d+$")

EXTERNAL_BASELINE_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "classifier_id",
        "manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "receipt_sha256",
        "archive_identifier",
        "archive_published_at_utc",
        "runtime",
        "stock_pettingzoo_api_test",
        "released_shimmy_openspiel_suite",
        "elapsed_ns",
    }
)
EXTERNAL_STOCK_PANEL_KEYS = frozenset(
    {
        "cycles",
        "action_space_seed",
        "case_count",
        "status_counts",
        "results",
    }
)
EXTERNAL_STOCK_RESULT_KEYS = frozenset(
    {
        "game_spec",
        "cycles",
        "passed",
        "exception",
        "warnings",
        "captured_output",
    }
)
EXTERNAL_SUITE_KEYS = frozenset(
    {
        "role",
        "sdist_url",
        "sdist_sha256",
        "test_member",
        "test_member_sha256",
        "pytest_args",
        "pythonhashseed",
        "result_classifier",
        "limitations",
        "result",
    }
)
EXTERNAL_SUITE_RESULT_KEYS = frozenset(
    {
        "status",
        "returncode",
        "exception",
        "stdout",
        "stderr",
        "elapsed_ns",
    }
)
MUTATION_BATCH_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "protocol_id",
        "study_manifest_sha256",
        "mutation_manifest_sha256",
        "archive_identifier",
        "archive_published_at_utc",
        "source_tree_sha256",
        "uv_lock_sha256",
        "receipt_sha256",
        "runtime",
        "selection",
        "score",
        "candidate_records",
        "progress_instrumentation_controls",
        "elapsed_ns",
        "self_validation",
    }
)

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
CASE_KEYS = frozenset(
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
RUN_KEYS = frozenset(
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
ALIGNMENT_KEYS = frozenset(
    {
        "source_events",
        "destination_events",
        "segments",
        "initial_progress",
    }
)
SOURCE_EVENT_KEYS = frozenset(
    {"progress", "rewards", "terminated", "truncated", "metadata"}
)
DESTINATION_EVENT_KEYS = frozenset(
    {
        "source_progress",
        "rewards",
        "delivered_rewards",
        "terminated",
        "truncated",
        "cleanup",
        "metadata",
    }
)
SEGMENT_KEYS = frozenset(
    {
        "kind",
        "source_before",
        "source_after",
        "source_span",
        "destination_span",
        "source_events",
        "destination_events",
    }
)
BASELINE_KEYS = frozenset({"baseline", "applicable", "findings", "reason"})
VIOLATION_KEYS = frozenset(
    {
        "obligation",
        "code",
        "message",
        "segment_index",
        "source_span",
        "destination_span",
        "expected",
        "observed",
    }
)
OBLIGATION_EVALUATION_OUTCOMES = (
    "evaluated_pass",
    "evaluated_fail",
    "not_applicable",
    "not_evaluated",
)
EXECUTION_PATH_CATEGORIES = (
    "terminal_complete",
    "bounded_prefix",
    "other_serialized_run",
    "no_run_infrastructure",
)
SEMANTIC_EVIDENCE_CATEGORIES = (
    "observed_failure",
    "no_observed_failure",
    "no_verdict",
)
EXECUTION_COMPLETENESS_CATEGORIES = (
    "terminal_complete",
    "bounded_prefix",
    "semantic_abort",
    "unalignable",
    "infrastructure",
    "inapplicable",
)
NON_SEMANTIC_DIAGNOSTIC_CODES = frozenset(
    {
        "source_setup_failed",
        "destination_call_budget_exhausted",
        "instrumentation_history_not_prefix_monotone",
        "instrumentation_replay_failed",
        "progress_instrumentation_inconsistent",
        "unalignable_chance",
    }
)
STRUCTURAL_COVERAGE_FEATURES = (
    "aligned_transition_segments",
    "one_to_many_transition_segments",
    "many_to_one_transition_segments",
    "destination_buffer_calls",
    "destination_commit_calls",
    "destination_other_stutter_calls",
    "destination_cleanup_calls",
    # Retained aliases for existing downstream artifacts.
    "destination_stutter_calls",
    "terminal_cleanup_calls",
    "source_chance_events",
)
PROGRESS_ANNOTATION_METHOD_ID = "independent_native_replay_event_count_v1"
PROGRESS_INSTRUMENTATION_KEYS = frozenset(
    {
        "method_id",
        "progress_before",
        "progress_after",
        "annotated_progress_after",
        "replayed_source_event_count",
        "source_event_progresses",
        "wrapped_history_delta",
    }
)

STUDY_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "manifest_status",
        "protocol_version",
        "study_scope",
        "target_versions",
        "population",
        "discovery",
        "validation",
        "configuration_evaluation",
        "case_inclusion",
        "trace_schedule",
        "execution_contract",
        "external_baselines",
        "mean_field_success",
        "mutation_evaluation",
        "outcome_reporting",
        "preregistration_warning",
        "environment",
    }
)
ARCHIVE_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "published_at_utc",
        "doi",
        "record_id",
    }
)
ABLATION_LOCALIZATION_RESOLUTION = {
    "strict_lockstep": "destination_call_when_schedule_is_one_to_one",
    "macro_boundary": "advancing_commit_boundary",
    "macro_aggregate": "aligned_transition_block",
    "endpoint": "trace_endpoint",
    "return_only": "episode_return_only",
}

EXCLUSIVE_GAME_BUCKETS = (
    "infrastructure_present",
    "unalignable_present_no_infrastructure",
    "inapplicable_present_no_infrastructure_or_unalignable",
    "violation_present_all_traces_semantically_completed",
    "all_traces_no_observed_violation",
)

DERIVED_MANUAL_FIELDS = (
    "confirmed_roots",
    "discovery_confirmed_roots",
    "prospective_confirmed_roots",
    "root_families",
    "macro_baseline_misses",
    "api_baseline_misses",
    "repair_attempts",
    "repair_successes",
    "repair_failures",
    "repair_non_attempts",
    "repair_not_applicable",
    "control_alarms",
    "upstream_confirmed_roots",
)
OPTIONAL_MANUAL_FIELDS = (
    "held_out_mutants_killed",
    "held_out_mutants_total",
    "peak_memory_bytes",
)
MANUAL_FIELDS = DERIVED_MANUAL_FIELDS + OPTIONAL_MANUAL_FIELDS
LEGACY_MANUAL_FIELDS = (
    "confirmed_roots",
    "root_families",
    "macro_baseline_misses",
    "api_baseline_misses",
    "repair_successes",
    "control_alarms",
    "upstream_confirmed_roots",
    "held_out_mutants_killed",
    "held_out_mutants_total",
    "peak_memory_bytes",
)
LEGACY_MANUAL_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "raw_batch_sha256",
        "status",
        "values",
    }
)
STRUCTURED_MANUAL_ARTIFACT_KEYS = frozenset(
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
ROOT_KEYS = frozenset(
    {
        "root_id",
        "provenance",
        "family",
        "adjudication_status",
        "first_witness",
        "contract",
        "effect_summary",
        "replay",
        "baselines",
        "causal_patch",
        "repair",
        "upstream",
    }
)
WITNESS_REFERENCE_KEYS = frozenset(
    {
        "case_id",
        "evidence_artifact_sha256",
        "localizer_id",
        "localized_witness_sha256",
        "boundary",
    }
)
BOUNDARY_REFERENCE_KEYS = frozenset(
    {
        "segment_index",
        "source_event_stop",
        "destination_event_stop",
        "selected_violation_index",
    }
)
CONTRACT_KEYS = frozenset({"citation", "claim_classification"})
EXTERNAL_BASELINE_NAMES = ("stock_api",)
ROOT_BASELINES_KEYS = frozenset((*BASELINE_NAMES, *EXTERNAL_BASELINE_NAMES))
BASELINE_CREDIT_KEYS = frozenset(
    {
        "outcome",
        "root_witness_reached",
        "outcome_evidence",
        "causal_attribution",
        "causal_evidence",
        "credit",
    }
)
BASELINE_EVIDENCE_KEYS = frozenset({"artifact_sha256", "evidence_reference"})
BASELINE_CAUSAL_EVIDENCE_KEYS = frozenset(
    {
        "artifact_sha256",
        "evidence_reference",
        "isolated_treatment",
        "target_root_present_before",
        "target_root_absent_after",
        "baseline_signal_before",
        "baseline_signal_after",
        "patch_sha256",
    }
)
CAUSAL_PATCH_KEYS = frozenset(
    {
        "stock_source_tree_sha256",
        "treatment_source_tree_sha256",
        "patch_sha256",
        "evidence_reference",
    }
)
REPAIR_KEYS = frozenset({"status", "evidence"})
REPLAY_KEYS = frozenset({"status", "evidence"})
REPLAY_EVIDENCE_KEYS = frozenset(
    {
        "artifact_sha256",
        "evidence_reference",
        "same_case_inputs",
        "finding_reproduced",
        "boundary_reproduced",
    }
)
REPAIR_EVIDENCE_KEYS = frozenset(
    {
        "artifact_sha256",
        "evidence_reference",
        "failing_before",
        "targeted_findings_absent_after",
        "no_new_findings",
        "reachability_preserved",
        "regression_passed",
        "reversion_restores_failure",
        "patch_sha256",
    }
)
UPSTREAM_KEYS = frozenset({"status", "reference"})
CONTROL_KEYS = frozenset(
    {
        "control_id",
        "evidence_artifact_sha256",
        "outcome",
        "observed_alarm_count",
        "unexplained_alarm_count",
    }
)
OPTIONAL_MEASUREMENT_KEYS = frozenset(OPTIONAL_MANUAL_FIELDS)
FINDING_DISPOSITION_KEYS = frozenset(
    {
        "case_id",
        "violation_index",
        "finding_sha256",
        "disposition",
        "root_id",
        "rejection_reason",
    }
)

STABLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
CONFIRMED_CLAIM_CLASSIFICATIONS = frozenset(
    {"defect", "mismatch", "unsupported_capability"}
)
NONCONFIRMED_CLAIM_CLASSIFICATIONS = frozenset(
    {"oracle_issue", "not_a_defect", "pending"}
)
REQUIRED_CONTROL_IDS = frozenset(
    {
        "native_clone_replay_v1",
        "openspiel_turn_based_simultaneous_v1",
        "pettingzoo_parallel_to_aec_v1",
    }
)
UPSTREAM_CONFIRMED_STATUSES = frozenset({"acknowledged", "fixed"})


class BatchValidationError(ValueError):
    """A sealed batch or one of its frozen identity inputs is invalid."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BatchValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise BatchValidationError(f"non-finite JSON number: {value}")


def _loads(text: str, label: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, BatchValidationError) as exc:
        raise BatchValidationError(f"invalid JSON in {label}: {exc}") from exc


def _read_json_object_with_sha256(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise BatchValidationError(f"cannot read {label}: {exc}") from exc
    value = _loads(text, label)
    if not isinstance(value, dict):
        raise BatchValidationError(f"{label} must contain one JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    return _read_json_object_with_sha256(path, label)[0]


def _canonical_line(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _iter_canonical_jsonl(
    path: Path,
    *,
    digest: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield canonical JSONL records without retaining the batch bytes."""
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise BatchValidationError(f"cannot read sealed batch: {exc}") from exc
    with handle:
        saw_record = False
        try:
            for line_number, raw_line in enumerate(handle, start=1):
                saw_record = True
                if digest is not None:
                    digest.update(raw_line)
                if not raw_line.endswith(b"\n"):
                    raise BatchValidationError(
                        "sealed batch must end with a newline"
                    )
                if b"\r" in raw_line:
                    raise BatchValidationError(
                        "sealed batch must use canonical LF newlines"
                    )
                payload = raw_line[:-1]
                if not payload:
                    raise BatchValidationError(
                        "sealed batch contains a blank JSONL line"
                    )
                try:
                    line = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise BatchValidationError(
                        f"batch line {line_number} is not UTF-8"
                    ) from exc
                value = _loads(line, f"batch line {line_number}")
                if not isinstance(value, dict):
                    raise BatchValidationError(
                        f"batch line {line_number} is not an object"
                    )
                if line != _canonical_line(value):
                    raise BatchValidationError(
                        f"batch line {line_number} is not canonical JSON"
                    )
                yield value
        except OSError as exc:
            raise BatchValidationError(f"cannot read sealed batch: {exc}") from exc
        if not saw_record:
            raise BatchValidationError("sealed batch is empty")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise BatchValidationError(f"cannot hash {path.name}: {exc}") from exc
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchValidationError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise BatchValidationError(f"{label} must be an array")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BatchValidationError(
            f"{label} must be an integer greater than or equal to {minimum}"
        )
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BatchValidationError(f"{label} must be a non-empty string")
    return value


def _nonblank_string(value: Any, label: str) -> str:
    text = _string(value, label)
    if text != text.strip():
        raise BatchValidationError(
            f"{label} must not have leading/trailing whitespace"
        )
    return text


def _stable_id(value: Any, label: str) -> str:
    text = _nonblank_string(value, label)
    if not STABLE_ID_PATTERN.fullmatch(text):
        raise BatchValidationError(
            f"{label} must be a stable lowercase identifier"
        )
    return text


def _nullable_nonblank_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _nonblank_string(value, label)


def _sha(value: Any, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise BatchValidationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    observed = frozenset(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise BatchValidationError(
            f"{label} keys differ; missing={missing}, extra={extra}"
        )


def _strict_json_equal(observed: Any, expected: Any, label: str) -> None:
    """Compare JSON values without Python's bool/int/float aliasing."""
    if isinstance(expected, Mapping):
        actual = _mapping(observed, label)
        _exact_keys(actual, frozenset(expected), label)
        for key, expected_value in expected.items():
            _strict_json_equal(actual[key], expected_value, f"{label}.{key}")
        return
    if isinstance(expected, list | tuple):
        actual = _list(observed, label)
        if len(actual) != len(expected):
            raise BatchValidationError(f"{label} array length differs")
        for index, (actual_value, expected_value) in enumerate(
            zip(actual, expected, strict=True)
        ):
            _strict_json_equal(actual_value, expected_value, f"{label}[{index}]")
        return
    if expected is None:
        if observed is not None:
            raise BatchValidationError(f"{label} differs")
        return
    if type(observed) is not type(expected) or observed != expected:
        raise BatchValidationError(f"{label} differs")


def _string_names(value: Any, label: str) -> tuple[str, ...]:
    items = _list(value, label)
    if not all(isinstance(item, str) and item for item in items):
        raise BatchValidationError(f"{label} must contain non-empty strings")
    names = tuple(items)
    if len(names) != len(set(names)):
        raise BatchValidationError(f"{label} contains duplicates")
    return names


def _validate_manifest_and_receipt(
    manifest_path: Path,
    receipt_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    tuple[str, ...],
    str,
    str,
]:
    manifest, manifest_sha256 = _read_json_object_with_sha256(
        manifest_path,
        "frozen manifest",
    )
    receipt, receipt_sha256 = _read_json_object_with_sha256(
        receipt_path,
        "archive receipt",
    )
    _exact_keys(manifest, STUDY_MANIFEST_KEYS, "frozen manifest")
    local_authorization = (
        receipt.get("artifact_type") == "marlrefine_local_execution_authorization"
    )
    _exact_keys(
        receipt,
        LOCAL_AUTHORIZATION_KEYS if local_authorization else ARCHIVE_RECEIPT_KEYS,
        "execution authorization",
    )
    if _integer(manifest.get("schema_version"), "manifest schema_version") != 2:
        raise BatchValidationError("frozen manifest schema_version must be 2")
    if manifest.get("manifest_status") not in {
        "frozen_pending_archive",
        "timestamp_archived",
    }:
        raise BatchValidationError("manifest status does not authorize frozen analysis")
    validation = _mapping(manifest.get("validation"), "manifest.validation")
    if _integer(
        validation.get("accounting_size"),
        "manifest validation accounting_size",
    ) != 106:
        raise BatchValidationError("manifest validation accounting must be 106")
    semantic = _mapping(
        validation.get("semantic_cohort"),
        "manifest.validation.semantic_cohort",
    )
    names = _string_names(
        semantic.get("names"),
        "manifest.validation.semantic_cohort.names",
    )
    if (
        _integer(
            semantic.get("size"),
            "manifest validation semantic cohort size",
        )
        != EXPECTED_SEMANTIC_COHORT_SIZE
        or len(names) != 105
    ):
        raise BatchValidationError("manifest semantic cohort must contain 105 names")
    if KNOWN_DESCRIPTIVE_EXCLUSION in names:
        raise BatchValidationError("crossword leaked into the semantic cohort")
    exclusions = _mapping(
        validation.get("descriptive_exclusions"),
        "manifest.validation.descriptive_exclusions",
    )
    if _string_names(exclusions.get("names"), "descriptive exclusion names") != (
        KNOWN_DESCRIPTIVE_EXCLUSION,
    ):
        raise BatchValidationError("crossword must be the sole descriptive exclusion")
    schedule = _mapping(manifest.get("trace_schedule"), "manifest.trace_schedule")
    if tuple(_list(schedule.get("policies"), "trace schedule policies")) != (
        TRACE_POLICY_NAMES
    ):
        raise BatchValidationError("manifest policy order is not the frozen schedule")
    if (
        _integer(schedule.get("per_case"), "manifest trace schedule per_case") != 8
        or _integer(
            schedule.get("decision_cap"),
            "manifest trace schedule decision_cap",
        )
        != 1000
    ):
        raise BatchValidationError("manifest trace count or decision cap differs")
    if (
        _integer(
            schedule.get("destination_call_cap"),
            "manifest trace schedule destination_call_cap",
        )
        != PROSPECTIVE_DESTINATION_CALL_CAP
    ):
        raise BatchValidationError("manifest destination-call cap differs")
    if schedule.get("outcome_classifier_id") != CLASSIFIER_ID:
        raise BatchValidationError("manifest outcome classifier differs")
    if (
        _integer(
            schedule.get("max_case_attempts"),
            "manifest trace schedule max_case_attempts",
        )
        != PROSPECTIVE_MAX_CASE_ATTEMPTS
    ):
        raise BatchValidationError("manifest maximum case attempts differs")
    _strict_json_equal(
        schedule.get("retry_eligibility"),
        PROSPECTIVE_RETRY_ELIGIBILITY,
        "manifest retry eligibility",
    )
    _strict_json_equal(
        manifest.get("execution_contract"),
        prospective_execution_contract(),
        "manifest prospective execution contract",
    )
    _strict_json_equal(
        manifest.get("external_baselines"),
        external_baseline_protocol(),
        "manifest external-baseline protocol",
    )

    if _integer(receipt.get("schema_version"), "receipt schema_version") != 1:
        raise BatchValidationError("execution authorization schema differs")
    if receipt.get("artifact_type") not in {
        "marlrefine_protocol_archive_receipt",
        "marlrefine_local_execution_authorization",
    }:
        raise BatchValidationError("execution authorization type differs")
    if receipt.get("manifest_sha256") != manifest_sha256:
        raise BatchValidationError("archive receipt does not bind the manifest bytes")
    _sha(receipt.get("source_tree_sha256"), "receipt source_tree_sha256")
    _sha(receipt.get("uv_lock_sha256"), "receipt uv_lock_sha256")
    if local_authorization:
        if (
            receipt.get("preregistered") is not False
            or receipt.get("public_archive") is not False
        ):
            raise BatchValidationError(
                "local authorization must disclose no preregistration/public archive"
            )
        environment = _mapping(manifest.get("environment"), "manifest.environment")
        if receipt.get("source_git_revision") != environment.get("git_revision"):
            raise BatchValidationError(
                "local authorization source revision differs from manifest"
            )
        if receipt.get("authorization_id") != (
            f"local-unregistered:{manifest_sha256}"
        ):
            raise BatchValidationError("local authorization ID differs")
        published = _string(
            receipt.get("authorized_at_utc"),
            "authorization authorized_at_utc",
        )
    else:
        doi = receipt.get("doi")
        if not isinstance(doi, str) or not DOI_PATTERN.fullmatch(doi):
            raise BatchValidationError(
                "archive receipt DOI is not canonical Zenodo form"
            )
        record_id = _integer(receipt.get("record_id"), "receipt record_id", minimum=1)
        if doi != f"10.5281/zenodo.{record_id}":
            raise BatchValidationError("archive receipt DOI and record_id disagree")
        published = _string(
            receipt.get("published_at_utc"),
            "receipt published_at_utc",
        )
    try:
        timestamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatchValidationError("receipt published_at_utc is not ISO-8601") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise BatchValidationError("receipt published_at_utc must be timezone-aware")
    return manifest, receipt, names, manifest_sha256, receipt_sha256


def _manifest_game_strata(
    manifest: Mapping[str, Any],
    game_names: Sequence[str],
) -> dict[str, str]:
    population = _mapping(manifest.get("population"), "manifest.population")
    metadata_rows = _list(
        population.get("registry_metadata"),
        "manifest.population.registry_metadata",
    )
    by_name: dict[str, str] = {}
    for index, value in enumerate(metadata_rows):
        row = _mapping(value, f"manifest population metadata[{index}]")
        name = _nonblank_string(
            row.get("short_name"),
            f"manifest population metadata[{index}].short_name",
        )
        if name in by_name:
            raise BatchValidationError("manifest population metadata duplicates a game")
        dynamics = _stable_id(
            row.get("dynamics"),
            f"manifest population metadata[{index}].dynamics",
        )
        chance_mode = _stable_id(
            row.get("chance_mode"),
            f"manifest population metadata[{index}].chance_mode",
        )
        if chance_mode not in {
            "deterministic",
            "explicit_stochastic",
            "sampled_stochastic",
        }:
            raise BatchValidationError(
                f"manifest population metadata[{index}].chance_mode differs"
            )
        if dynamics == "mean_field":
            stratum = "mean_field"
        elif dynamics in {"sequential", "simultaneous"}:
            stochasticity = (
                "deterministic"
                if chance_mode == "deterministic"
                else "stochastic"
            )
            stratum = f"{dynamics}__{stochasticity}"
        else:
            raise BatchValidationError(
                f"manifest population metadata[{index}].dynamics differs"
            )
        by_name[name] = stratum
    missing = sorted(set(game_names).difference(by_name))
    if missing:
        raise BatchValidationError(
            f"manifest population metadata omits semantic games: {missing}"
        )
    return {name: by_name[name] for name in game_names}


def _validate_header(
    header: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    receipt: Mapping[str, Any],
    receipt_sha256: str,
) -> dict[str, str]:
    _exact_keys(header, HEADER_KEYS, "batch header")
    if (
        _integer(header.get("schema_version"), "batch header schema_version")
        != BATCH_SCHEMA_VERSION
        or header.get("artifact_type") != "marlrefine_prospective_batch_header"
        or header.get("classifier_id") != CLASSIFIER_ID
    ):
        raise BatchValidationError("batch header schema/type/classifier differs")
    if header.get("obligation_ledger_schema_id") != OBLIGATION_LEDGER_SCHEMA_ID:
        raise BatchValidationError("batch header obligation-ledger identity differs")
    identities = {
        "manifest_sha256": manifest_sha256,
        "source_tree_sha256": str(receipt.get("source_tree_sha256")),
        "uv_lock_sha256": str(receipt.get("uv_lock_sha256")),
    }
    for field, expected in identities.items():
        _sha(header.get(field), f"header {field}")
        if header.get(field) != expected:
            raise BatchValidationError(f"header {field} differs from frozen inputs")
    if header.get("receipt_sha256") != receipt_sha256:
        raise BatchValidationError("header receipt identity differs")
    local_authorization = (
        receipt.get("artifact_type") == "marlrefine_local_execution_authorization"
    )
    expected_identifier = (
        receipt.get("authorization_id") if local_authorization else receipt.get("doi")
    )
    if header.get("archive_identifier") != expected_identifier:
        raise BatchValidationError(
            "header execution-authorization identifier differs"
        )
    receipt_timestamp = datetime.fromisoformat(
        str(
            receipt[
                "authorized_at_utc" if local_authorization else "published_at_utc"
            ]
        ).replace("Z", "+00:00")
    )
    header_timestamp_raw = _string(
        header.get("archive_published_at_utc"),
        "header archive_published_at_utc",
    )
    try:
        header_timestamp = datetime.fromisoformat(
            header_timestamp_raw.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise BatchValidationError(
            "header archive_published_at_utc is not ISO-8601"
        ) from exc
    if header_timestamp != receipt_timestamp:
        raise BatchValidationError("header execution-authorization time differs")
    if (
        _integer(header.get("case_count"), "batch header case_count")
        != EXPECTED_TRACE_COUNT
    ):
        raise BatchValidationError("batch header must declare exactly 840 traces")
    if _integer(header.get("decision_cap"), "batch header decision_cap") != 1000:
        raise BatchValidationError("batch header decision cap must be 1000")
    if (
        _integer(
            header.get("destination_call_cap"),
            "batch header destination_call_cap",
        )
        != PROSPECTIVE_DESTINATION_CALL_CAP
    ):
        raise BatchValidationError("batch header destination-call cap differs")
    if (
        _integer(
            header.get("max_case_attempts"),
            "batch header max_case_attempts",
        )
        != PROSPECTIVE_MAX_CASE_ATTEMPTS
    ):
        raise BatchValidationError("batch header maximum case attempts differs")
    _strict_json_equal(
        header.get("retry_eligibility"),
        PROSPECTIVE_RETRY_ELIGIBILITY,
        "batch header retry eligibility",
    )
    if header.get("known_descriptive_exclusions") != [
        KNOWN_DESCRIPTIVE_EXCLUSION
    ]:
        raise BatchValidationError("batch header descriptive exclusion differs")
    _sha(
        header.get("resume_infrastructure_from_sha256"),
        "header resume batch SHA-256",
        optional=True,
    )
    runtime = _mapping(header.get("runtime"), "batch header runtime")
    for field in ("source_tree_sha256", "uv_lock_sha256"):
        if runtime.get(field) != identities[field]:
            raise BatchValidationError(f"header runtime {field} differs")
    environment = _mapping(manifest.get("environment"), "manifest.environment")
    for field in ("source_tree_sha256", "uv_lock_sha256"):
        if environment.get(field) != identities[field]:
            raise BatchValidationError(f"manifest environment {field} differs")
    return identities


def _frozen_source_git_revision(manifest: Mapping[str, Any]) -> str:
    """Return the clean source commit bound by the frozen manifest."""
    environment = _mapping(manifest.get("environment"), "manifest.environment")
    revision = environment.get("git_revision")
    if not isinstance(revision, str) or not GIT_REVISION_PATTERN.fullmatch(
        revision
    ):
        raise BatchValidationError(
            "manifest environment git_revision must be a full lowercase Git SHA"
        )
    if environment.get("git_dirty") is not False:
        raise BatchValidationError(
            "manifest environment must bind a clean source Git revision"
        )
    return revision


def _runtime_identity(value: Any, label: str) -> dict[str, Any]:
    """Return the stable runtime identity, excluding only its creation time."""
    runtime = dict(_mapping(value, label))
    created = runtime.pop("created_at_utc", None)
    if created is not None:
        created_text = _string(created, f"{label}.created_at_utc")
        try:
            timestamp = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BatchValidationError(
                f"{label}.created_at_utc is not ISO-8601"
            ) from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise BatchValidationError(
                f"{label}.created_at_utc must be timezone-aware"
            )
    return runtime


def _require_matching_runtime(
    value: Any,
    expected: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    observed = _runtime_identity(value, label)
    expected_identity = _runtime_identity(expected, "batch execution runtime")
    _strict_json_equal(observed, expected_identity, label)
    return dict(_mapping(value, label))


def _validate_external_baseline_artifact(
    path: Path,
    *,
    manifest_sha256: str,
    receipt_sha256: str,
    identities: Mapping[str, str],
    archive_identifier: str,
    archive_published_at_utc: str,
    batch_runtime: Mapping[str, Any],
    game_names: Sequence[str],
) -> dict[str, Any]:
    payload, artifact_sha256 = _read_json_object_with_sha256(
        path,
        "external-baseline artifact",
    )
    _exact_keys(
        payload,
        EXTERNAL_BASELINE_ARTIFACT_KEYS,
        "external-baseline artifact",
    )
    if (
        _integer(
            payload.get("schema_version"),
            "external-baseline schema_version",
        )
        != EXTERNAL_BASELINE_SCHEMA_VERSION
        or payload.get("artifact_type")
        != "marlrefine_prospective_external_baselines"
        or payload.get("classifier_id") != EXTERNAL_BASELINE_CLASSIFIER_ID
    ):
        raise BatchValidationError("external-baseline artifact identity differs")
    expected_identities = {
        "manifest_sha256": manifest_sha256,
        "source_tree_sha256": identities["source_tree_sha256"],
        "uv_lock_sha256": identities["uv_lock_sha256"],
        "receipt_sha256": receipt_sha256,
        "archive_identifier": archive_identifier,
        "archive_published_at_utc": archive_published_at_utc,
    }
    for field, expected in expected_identities.items():
        if payload.get(field) != expected:
            raise BatchValidationError(
                f"external-baseline artifact {field} differs"
            )
    runtime = _require_matching_runtime(
        payload.get("runtime"),
        batch_runtime,
        "external-baseline runtime",
    )
    elapsed_ns = _integer(
        payload.get("elapsed_ns"),
        "external-baseline elapsed_ns",
    )

    stock = _mapping(
        payload.get("stock_pettingzoo_api_test"),
        "external stock API panel",
    )
    _exact_keys(stock, EXTERNAL_STOCK_PANEL_KEYS, "external stock API panel")
    if (
        _integer(stock.get("cycles"), "external stock API cycles")
        != STOCK_API_CYCLES
        or _integer(
            stock.get("action_space_seed"),
            "external stock API action_space_seed",
        )
        != STOCK_API_ACTION_SPACE_SEED
        or _integer(stock.get("case_count"), "external stock API case_count")
        != len(game_names)
    ):
        raise BatchValidationError("external stock API schedule differs")
    raw_results = _list(stock.get("results"), "external stock API results")
    if len(raw_results) != len(game_names):
        raise BatchValidationError("external stock API result count differs")
    status_counts: Counter[str] = Counter()
    outcomes: list[dict[str, Any]] = []
    outcomes_by_game: dict[str, str] = {}
    for index, (raw_result, expected_game) in enumerate(
        zip(raw_results, game_names, strict=True)
    ):
        label = f"external stock API results[{index}]"
        result = _mapping(raw_result, label)
        _exact_keys(result, EXTERNAL_STOCK_RESULT_KEYS, label)
        if (
            result.get("game_spec") != expected_game
            or _integer(result.get("cycles"), f"{label}.cycles")
            != STOCK_API_CYCLES
        ):
            raise BatchValidationError(f"{label} case or cycle count differs")
        passed_value = result.get("passed")
        _boolean(passed_value, f"{label}.passed")
        passed = bool(passed_value)
        exception = result.get("exception")
        if exception is not None and not isinstance(exception, str):
            raise BatchValidationError(f"{label}.exception must be string or null")
        if passed != (exception is None):
            raise BatchValidationError(f"{label} pass/exception state differs")
        warnings = _list(result.get("warnings"), f"{label}.warnings")
        if not all(isinstance(item, str) for item in warnings):
            raise BatchValidationError(f"{label}.warnings must contain strings")
        if not isinstance(result.get("captured_output"), str):
            raise BatchValidationError(f"{label}.captured_output must be a string")
        outcome = "passed" if passed else "failed"
        status_counts["pass" if passed else "fail"] += 1
        outcomes_by_game[expected_game] = outcome
        outcomes.append(
            {
                "game_name": expected_game,
                "outcome": outcome,
                "exception": exception,
                "warning_count": len(warnings),
            }
        )
    expected_status_counts = dict(sorted(status_counts.items()))
    _strict_json_equal(
        stock.get("status_counts"),
        expected_status_counts,
        "external stock API status counts",
    )

    suite = _mapping(
        payload.get("released_shimmy_openspiel_suite"),
        "external Shimmy suite",
    )
    _exact_keys(suite, EXTERNAL_SUITE_KEYS, "external Shimmy suite")
    protocol_suite = external_baseline_protocol()[
        "released_shimmy_openspiel_suite"
    ]
    expected_suite = {
        "role": "contextual_upstream_suite_evidence_not_cohort_comparator",
        "sdist_url": SHIMMY_SDIST_URL,
        "sdist_sha256": SHIMMY_SDIST_SHA256,
        "test_member": SHIMMY_OPENSPIEL_TEST_MEMBER,
        "test_member_sha256": SHIMMY_OPENSPIEL_TEST_SHA256,
        "pytest_args": ["-q", "--disable-warnings"],
        "pythonhashseed": "0",
        "result_classifier": "pytest_exit_0_pass_1_fail_else_infrastructure_v1",
        "limitations": protocol_suite["limitations"],
    }
    for field, expected in expected_suite.items():
        if suite.get(field) != expected:
            raise BatchValidationError(f"external Shimmy suite {field} differs")
    suite_result = _mapping(suite.get("result"), "external Shimmy suite result")
    _exact_keys(
        suite_result,
        EXTERNAL_SUITE_RESULT_KEYS,
        "external Shimmy suite result",
    )
    suite_status = suite_result.get("status")
    if suite_status not in {"pass", "fail", "infrastructure"}:
        raise BatchValidationError("external Shimmy suite result status differs")
    returncode = suite_result.get("returncode")
    if returncode is not None and (
        isinstance(returncode, bool) or not isinstance(returncode, int)
    ):
        raise BatchValidationError(
            "external Shimmy suite result returncode must be integer or null"
        )
    expected_suite_status = (
        "infrastructure"
        if returncode is None
        else "pass"
        if returncode == 0
        else "fail"
        if returncode == 1
        else "infrastructure"
    )
    if suite_status != expected_suite_status:
        raise BatchValidationError("external Shimmy suite status/returncode differs")
    suite_exception = suite_result.get("exception")
    if suite_exception is not None and not isinstance(suite_exception, str):
        raise BatchValidationError(
            "external Shimmy suite exception must be string or null"
        )
    if returncode is None and not suite_exception:
        raise BatchValidationError(
            "external Shimmy suite null returncode requires an exception"
        )
    if returncode is not None and suite_exception is not None:
        raise BatchValidationError(
            "external Shimmy suite completed returncode cannot carry an exception"
        )
    for field in ("stdout", "stderr"):
        if not isinstance(suite_result.get(field), str):
            raise BatchValidationError(
                f"external Shimmy suite result {field} must be a string"
            )
    suite_elapsed_ns = _integer(
        suite_result.get("elapsed_ns"),
        "external Shimmy suite result elapsed_ns",
    )

    return {
        "source": {"filename": path.name, "sha256": artifact_sha256},
        "classifier_id": EXTERNAL_BASELINE_CLASSIFIER_ID,
        "runtime": runtime,
        "elapsed_ns": elapsed_ns,
        "stock_pettingzoo_api_test": {
            "cycles": STOCK_API_CYCLES,
            "action_space_seed": STOCK_API_ACTION_SPACE_SEED,
            "case_count": len(outcomes),
            "status_counts": expected_status_counts,
            "outcomes": outcomes,
            "outcomes_by_game": outcomes_by_game,
        },
        "released_shimmy_openspiel_suite": {
            "status": suite_status,
            "returncode": returncode,
            "exception": suite_exception,
            "elapsed_ns": suite_elapsed_ns,
        },
    }


def _validate_mutation_batch_artifact(
    path: Path,
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    receipt_sha256: str,
    identities: Mapping[str, str],
    archive_identifier: str,
    archive_published_at_utc: str,
    batch_runtime: Mapping[str, Any],
) -> dict[str, Any]:
    payload, artifact_sha256 = _read_json_object_with_sha256(
        path,
        "mutation-batch artifact",
    )
    _exact_keys(payload, MUTATION_BATCH_ARTIFACT_KEYS, "mutation-batch artifact")
    if (
        _integer(payload.get("schema_version"), "mutation-batch schema_version")
        != MUTATION_BATCH_SCHEMA_VERSION
        or payload.get("artifact_type") != MUTATION_BATCH_ARTIFACT_TYPE
        or payload.get("protocol_id") != MUTATION_PROTOCOL_ID
    ):
        raise BatchValidationError("mutation-batch artifact identity differs")
    mutation_evaluation = _mapping(
        manifest.get("mutation_evaluation"),
        "manifest.mutation_evaluation",
    )
    expected_identities = {
        "study_manifest_sha256": manifest_sha256,
        "mutation_manifest_sha256": mutation_evaluation.get(
            "mutation_manifest_sha256"
        ),
        "source_tree_sha256": identities["source_tree_sha256"],
        "uv_lock_sha256": identities["uv_lock_sha256"],
        "receipt_sha256": receipt_sha256,
        "archive_identifier": archive_identifier,
        "archive_published_at_utc": archive_published_at_utc,
    }
    _sha(
        expected_identities["mutation_manifest_sha256"],
        "manifest mutation-manifest SHA-256",
    )
    for field, expected in expected_identities.items():
        if payload.get(field) != expected:
            raise BatchValidationError(f"mutation-batch artifact {field} differs")
    runtime = _require_matching_runtime(
        payload.get("runtime"),
        batch_runtime,
        "mutation-batch runtime",
    )
    elapsed_ns = _integer(payload.get("elapsed_ns"), "mutation-batch elapsed_ns")
    try:
        validation = validate_mutation_batch(payload)
    except MutationBatchValidationError as exc:
        raise BatchValidationError(f"invalid mutation-batch artifact: {exc}") from exc

    score = _mapping(payload.get("score"), "mutation-batch score")
    selection = _mapping(payload.get("selection"), "mutation-batch selection")
    records = _list(payload.get("candidate_records"), "mutation candidate records")
    selected_clean_alarms_by_family: Counter[str] = Counter()
    for index, raw_record in enumerate(records):
        record = _mapping(raw_record, f"mutation candidate records[{index}]")
        candidate = _mapping(
            record.get("candidate"),
            f"mutation candidate records[{index}].candidate",
        )
        family = str(candidate.get("family"))
        if family not in MUTATION_FAMILIES:
            raise BatchValidationError("mutation candidate family differs")
        reference = _mapping(
            record.get("reference"),
            f"mutation candidate records[{index}].reference",
        )
        alarm = _mapping(
            reference.get("clean_reference_alarm"),
            f"mutation candidate records[{index}].clean_reference_alarm",
        )
        if record.get("selected") is True and alarm.get("any_unexpected_alarm") is True:
            selected_clean_alarms_by_family[family] += 1

    selected_by_family = _mapping(
        score.get("selected_count_by_family"),
        "mutation selected_count_by_family",
    )
    semantic_by_family = _mapping(
        score.get("semantic_killed_by_family"),
        "mutation semantic_killed_by_family",
    )
    crash_by_family = _mapping(
        score.get("crash_only_killed_by_family"),
        "mutation crash_only_killed_by_family",
    )
    family_rows: list[dict[str, Any]] = []
    for family in MUTATION_FAMILIES:
        selected = _integer(selected_by_family.get(family), f"{family} selected")
        semantic = _integer(semantic_by_family.get(family), f"{family} semantic")
        crash_only = _integer(crash_by_family.get(family), f"{family} crash-only")
        family_rows.append(
            {
                "family": family,
                "selected": selected,
                "semantic_kills": semantic,
                "crash_only_kills": crash_only,
                "survived": selected - semantic - crash_only,
                "selected_clean_reference_alarms": (
                    selected_clean_alarms_by_family[family]
                ),
            }
        )
    selected_total = _integer(score.get("selected_total"), "mutation selected total")
    semantic_total = _integer(
        score.get("semantic_killed_total"),
        "mutation semantic-kill total",
    )
    crash_total = _integer(
        score.get("crash_only_killed_total"),
        "mutation crash-only total",
    )
    clean_alarms = dict(
        _mapping(score.get("clean_reference_alarms"), "mutation clean alarms")
    )
    paired_counts = dict(
        _mapping(
            score.get("paired_ablation_signal_counts"),
            "mutation paired comparator counts",
        )
    )
    controls = _mapping(
        payload.get("progress_instrumentation_controls"),
        "mutation progress controls",
    )
    first_detection_counts = _mapping(
        score.get("first_detection_counts"),
        "mutation first-detection counts",
    )
    return {
        "source": {"filename": path.name, "sha256": artifact_sha256},
        "protocol_id": MUTATION_PROTOCOL_ID,
        "runtime": runtime,
        "elapsed_ns": elapsed_ns,
        "validation": validation,
        "selection": {
            "complete": bool(selection.get("complete")),
            "selected_ids_by_family": selection.get("selected_ids_by_family"),
            "selected_count_by_family": dict(selected_by_family),
            "incomplete_families": selection.get("incomplete_families"),
        },
        "overall": {
            "attempted_candidates": len(records),
            "selected_total": selected_total,
            "semantic_kills": semantic_total,
            "crash_only_kills": crash_total,
            "survived": selected_total - semantic_total - crash_total,
        },
        "by_family": family_rows,
        "clean_reference_alarms": clean_alarms,
        "paired_comparator_signal_counts": paired_counts,
        "first_detection_by_obligation": first_detection_counts.get(
            "by_obligation"
        ),
        "first_detection_by_phase": first_detection_counts.get("by_phase"),
        "replacement_reason_counts": score.get("replacement_reason_counts"),
        "progress_instrumentation_controls": {
            "included_in_mutant_denominator": False,
            "required_count": controls.get("required_count"),
            "detected_count": controls.get("detected_count"),
            "records": controls.get("records"),
        },
    }


def _span(value: Any, label: str, *, limit: int) -> tuple[int, int] | None:
    if value is None:
        return None
    item = _mapping(value, label)
    if frozenset(item) != {"start", "stop"}:
        raise BatchValidationError(f"{label} must contain start and stop")
    start = _integer(item.get("start"), f"{label}.start")
    stop = _integer(item.get("stop"), f"{label}.stop")
    if stop < start or stop > limit:
        raise BatchValidationError(f"{label} lies outside its ledger")
    return start, stop


def _reward_vector(value: Any, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    items = _list(value, label)
    for index, item in enumerate(items):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise BatchValidationError(f"{label}[{index}] must be numeric")
        if not math.isfinite(float(item)):
            raise BatchValidationError(f"{label}[{index}] must be finite")


def _boolean(value: Any, label: str) -> None:
    if not isinstance(value, bool):
        raise BatchValidationError(f"{label} must be boolean")


def _validate_source_event(value: Any, label: str) -> Mapping[str, Any]:
    event = _mapping(value, label)
    _exact_keys(event, SOURCE_EVENT_KEYS, label)
    _integer(event.get("progress"), f"{label}.progress")
    _reward_vector(event.get("rewards"), f"{label}.rewards")
    _boolean(event.get("terminated"), f"{label}.terminated")
    _boolean(event.get("truncated"), f"{label}.truncated")
    _mapping(event.get("metadata"), f"{label}.metadata")
    return event


def _validate_destination_event(
    value: Any,
    label: str,
    *,
    previous_progress: int,
) -> Mapping[str, Any]:
    event = _mapping(value, label)
    _exact_keys(event, DESTINATION_EVENT_KEYS, label)
    _integer(event.get("source_progress"), f"{label}.source_progress")
    _reward_vector(event.get("rewards"), f"{label}.rewards")
    _reward_vector(
        event.get("delivered_rewards"),
        f"{label}.delivered_rewards",
        optional=True,
    )
    _boolean(event.get("terminated"), f"{label}.terminated")
    _boolean(event.get("truncated"), f"{label}.truncated")
    _boolean(event.get("cleanup"), f"{label}.cleanup")
    metadata = _mapping(event.get("metadata"), f"{label}.metadata")
    instrumentation = _mapping(
        metadata.get("progress_instrumentation"),
        f"{label}.metadata.progress_instrumentation",
    )
    _exact_keys(
        instrumentation,
        PROGRESS_INSTRUMENTATION_KEYS,
        f"{label}.metadata.progress_instrumentation",
    )
    if instrumentation.get("method_id") != PROGRESS_ANNOTATION_METHOD_ID:
        raise BatchValidationError(f"{label} progress method differs")
    source_progress = int(event["source_progress"])
    expected_progresses = list(range(previous_progress + 1, source_progress + 1))
    expected_anchor = {
        "progress_before": previous_progress,
        "progress_after": source_progress,
        "annotated_progress_after": source_progress,
        "replayed_source_event_count": len(expected_progresses),
        "source_event_progresses": expected_progresses,
    }
    for field, expected in expected_anchor.items():
        _strict_json_equal(
            instrumentation.get(field),
            expected,
            f"{label} progress instrumentation {field}",
        )
    history_delta = _list(
        instrumentation.get("wrapped_history_delta"),
        f"{label}.metadata.progress_instrumentation.wrapped_history_delta",
    )
    for index, action in enumerate(history_delta):
        _integer(
            action,
            f"{label}.metadata.progress_instrumentation."
            f"wrapped_history_delta[{index}]",
        )
    return event


def _validate_violation(
    value: Any,
    label: str,
    *,
    source_length: int,
    destination_length: int,
    segment_count: int,
) -> Mapping[str, Any]:
    violation = _mapping(value, label)
    _exact_keys(violation, VIOLATION_KEYS, label)
    _string(violation.get("obligation"), f"{label}.obligation")
    _string(violation.get("code"), f"{label}.code")
    _string(violation.get("message"), f"{label}.message")
    segment_index = violation.get("segment_index")
    if segment_index is not None:
        index = _integer(segment_index, f"{label}.segment_index")
        if index >= segment_count:
            raise BatchValidationError(f"{label}.segment_index is out of range")
    _span(violation.get("source_span"), f"{label}.source_span", limit=source_length)
    _span(
        violation.get("destination_span"),
        f"{label}.destination_span",
        limit=destination_length,
    )
    return violation


def _validate_alignment(
    alignment_value: Any,
    source_events: list[Any],
    destination_events: list[Any],
    label: str,
) -> Mapping[str, Any]:
    alignment = _mapping(alignment_value, label)
    _exact_keys(alignment, ALIGNMENT_KEYS, label)
    if alignment.get("source_events") != source_events:
        raise BatchValidationError(f"{label} duplicates different source events")
    if alignment.get("destination_events") != destination_events:
        raise BatchValidationError(
            f"{label} duplicates different destination events"
        )
    _integer(alignment.get("initial_progress"), f"{label}.initial_progress")
    for event_index, event in enumerate(source_events):
        _validate_source_event(event, f"{label}.source_events[{event_index}]")
    previous_progress = int(alignment["initial_progress"])
    for event_index, event in enumerate(destination_events):
        _validate_destination_event(
            event,
            f"{label}.destination_events[{event_index}]",
            previous_progress=previous_progress,
        )
        previous_progress = int(event["source_progress"])
    segments = _list(alignment.get("segments"), f"{label}.segments")
    source_cursor = 0
    destination_cursor = 0
    for segment_index, segment_value in enumerate(segments):
        segment = _mapping(segment_value, f"{label}.segments[{segment_index}]")
        segment_label = f"{label}.segments[{segment_index}]"
        _exact_keys(segment, SEGMENT_KEYS, segment_label)
        kind = segment.get("kind")
        if kind not in {"transition", "stutter", "terminal_tail"}:
            raise BatchValidationError(f"{segment_label}.kind differs")
        before = _integer(
            segment.get("source_before"),
            f"{segment_label}.source_before",
        )
        after = _integer(
            segment.get("source_after"),
            f"{segment_label}.source_after",
        )
        if after < before or (kind == "transition") != (after > before):
            raise BatchValidationError(f"{segment_label} progress/kind differs")
        source_span = _span(
            segment.get("source_span"),
            f"{segment_label}.source_span",
            limit=len(source_events),
        )
        destination_span = _span(
            segment.get("destination_span"),
            f"{segment_label}.destination_span",
            limit=len(destination_events),
        )
        if source_span is None or destination_span is None:
            raise BatchValidationError("alignment segment spans cannot be null")
        if source_span[0] != source_cursor or destination_span[0] != destination_cursor:
            raise BatchValidationError(
                "alignment segments do not form contiguous covers"
            )
        if segment.get("source_events") != source_events[slice(*source_span)]:
            raise BatchValidationError("alignment segment source events differ")
        if segment.get("destination_events") != destination_events[
            slice(*destination_span)
        ]:
            raise BatchValidationError("alignment segment destination events differ")
        if destination_span[0] == destination_span[1]:
            raise BatchValidationError("alignment segment has no destination event")
        if kind != "transition" and source_span[0] != source_span[1]:
            raise BatchValidationError(
                "non-transition alignment segment consumes source events"
            )
        source_cursor = source_span[1]
        destination_cursor = destination_span[1]
    # Every destination call belongs to exactly one segment. Source events may
    # retain an uncovered suffix when the destination failed to reach the same
    # progress; that suffix is precisely the evidence for completeness checks.
    if destination_cursor != len(destination_events):
        raise BatchValidationError(
            "alignment segments do not cover the destination ledger"
        )
    return alignment


def _validate_baselines(
    values: Any,
    label: str,
    *,
    source_length: int,
    destination_length: int,
    segment_count: int,
) -> tuple[Mapping[str, Any], ...]:
    items = _list(values, label)
    baselines: list[Mapping[str, Any]] = []
    observed_names: list[str] = []
    for index, value in enumerate(items):
        item_label = f"{label}[{index}]"
        baseline = _mapping(value, item_label)
        _exact_keys(baseline, BASELINE_KEYS, item_label)
        name = _string(baseline.get("baseline"), f"{item_label}.baseline")
        observed_names.append(name)
        applicable = baseline.get("applicable")
        if not isinstance(applicable, bool):
            raise BatchValidationError(f"{item_label}.applicable must be boolean")
        findings = _list(baseline.get("findings"), f"{item_label}.findings")
        for finding_index, finding in enumerate(findings):
            _validate_violation(
                finding,
                f"{item_label}.findings[{finding_index}]",
                source_length=source_length,
                destination_length=destination_length,
                segment_count=segment_count,
            )
        reason = baseline.get("reason")
        if applicable:
            if reason is not None:
                raise BatchValidationError(
                    f"{item_label} is applicable but carries an inapplicability reason"
                )
        else:
            if findings:
                raise BatchValidationError(
                    f"{item_label} is inapplicable but carries detection findings"
                )
            _string(reason, f"{item_label}.reason")
        baselines.append(baseline)
    if tuple(observed_names) != BASELINE_NAMES:
        raise BatchValidationError("case baseline panel or order differs")
    return tuple(baselines)


def _validate_run(
    run_value: Any,
    *,
    game_name: str,
    policy_index: int,
    case_label: str,
) -> Mapping[str, Any]:
    run = _mapping(run_value, f"{case_label}.run")
    _exact_keys(run, RUN_KEYS, f"{case_label}.run")
    policy = TRACE_POLICIES[policy_index]
    if (
        run.get("game_spec") != game_name
        or _integer(run.get("seed"), f"{case_label}.run.seed")
        != policy.environment_seed
    ):
        raise BatchValidationError(f"{case_label} run game or seed differs")
    if not isinstance(run.get("applicable"), bool):
        raise BatchValidationError(f"{case_label}.run.applicable must be boolean")
    source_events = _list(run.get("source_events"), f"{case_label}.source_events")
    destination_events = _list(
        run.get("destination_events"), f"{case_label}.destination_events"
    )
    alignment = _validate_alignment(
        run.get("alignment"),
        source_events,
        destination_events,
        f"{case_label}.alignment",
    )
    segments = _list(alignment.get("segments"), f"{case_label}.alignment.segments")
    violations = _list(run.get("violations"), f"{case_label}.violations")
    for index, violation in enumerate(violations):
        _validate_violation(
            violation,
            f"{case_label}.violations[{index}]",
            source_length=len(source_events),
            destination_length=len(destination_events),
            segment_count=len(segments),
        )
    _validate_baselines(
        run.get("baselines"),
        f"{case_label}.baselines",
        source_length=len(source_events),
        destination_length=len(destination_events),
        segment_count=len(segments),
    )
    summary = _mapping(run.get("summary"), f"{case_label}.summary")
    expected_summary = {
        "trace_policy_name": policy.name,
        "trace_policy_id": policy.policy_id,
        "trace_policy_seed": policy.seed,
        "requested_seed": policy.environment_seed,
        "requested_max_source_decisions": 1000,
    }
    for field, expected in expected_summary.items():
        _strict_json_equal(
            summary.get(field),
            expected,
            f"{case_label}.summary.{field}",
        )
    optional_counts = {
        "source_transitions": len(source_events),
        "destination_calls": len(destination_events),
        "violation_count": len(violations),
    }
    for field, expected in optional_counts.items():
        if field in summary:
            _strict_json_equal(
                summary[field],
                expected,
                f"{case_label}.summary.{field}",
            )
    try:
        validate_serialized_obligation_evaluations(
            run.get("obligation_evaluations"),
            violations=violations,
            alignment=alignment,
            summary=summary,
            caller_supplied_nondefault=summary.get(
                "caller_supplied_nondefault_configuration"
            ),
            label=f"{case_label}.obligation_evaluations",
        )
    except ValueError as exc:
        raise BatchValidationError(str(exc)) from exc
    return run


def _expected_case_metadata(
    ordinal: int,
    game_name: str,
    policy_index: int,
) -> dict[str, Any]:
    policy = TRACE_POLICIES[policy_index]
    return {
        "case_id": f"{game_name}::{policy.name}",
        "ordinal": ordinal,
        "game_name": game_name,
        "trace_policy_name": policy.name,
        "trace_policy_id": policy.policy_id,
        "trace_policy_seed": policy.seed,
        "environment_seed": policy.environment_seed,
    }


def _validate_case(
    record: Mapping[str, Any],
    *,
    ordinal: int,
    game_name: str,
    policy_index: int,
    identities: Mapping[str, str],
    resumed_batch: bool,
) -> Mapping[str, Any] | None:
    label = f"case[{ordinal}]"
    _exact_keys(record, CASE_KEYS, label)
    if (
        _integer(record.get("schema_version"), f"{label}.schema_version")
        != BATCH_SCHEMA_VERSION
        or record.get("artifact_type") != "marlrefine_prospective_case"
        or record.get("classifier_id") != CLASSIFIER_ID
    ):
        raise BatchValidationError(f"{label} schema/type/classifier differs")
    for field, expected in identities.items():
        if record.get(field) != expected:
            raise BatchValidationError(f"{label} {field} differs")
    metadata = _mapping(record.get("case"), f"{label}.case")
    _exact_keys(metadata, CASE_METADATA_KEYS, f"{label}.case")
    expected_metadata = _expected_case_metadata(ordinal, game_name, policy_index)
    try:
        _strict_json_equal(metadata, expected_metadata, f"{label}.case")
    except BatchValidationError as exc:
        raise BatchValidationError(f"{label} identity/order differs") from exc
    attempt = _integer(record.get("attempt"), f"{label}.attempt", minimum=1)
    if attempt not in {1, 2}:
        raise BatchValidationError(f"{label}.attempt exceeds the frozen retry limit")
    prior_hash = _sha(
        record.get("prior_record_sha256"),
        f"{label}.prior_record_sha256",
        optional=True,
    )
    if (attempt == 1 and prior_hash is not None) or (
        attempt == 2 and prior_hash is None
    ):
        raise BatchValidationError(f"{label} attempt/prior identity is inconsistent")
    if not resumed_batch and attempt != 1:
        raise BatchValidationError("an initial batch cannot contain retry attempts")
    _integer(record.get("elapsed_ns"), f"{label}.elapsed_ns")
    if not isinstance(record.get("captured_stdout"), str) or not isinstance(
        record.get("captured_stderr"), str
    ):
        raise BatchValidationError(f"{label} captured streams must be strings")

    run_value = record.get("run")
    infrastructure_error = record.get("infrastructure_error")
    if run_value is None:
        error = _mapping(infrastructure_error, f"{label}.infrastructure_error")
        _string(error.get("exception_type"), f"{label}.exception_type")
        if not isinstance(error.get("message"), str):
            raise BatchValidationError(f"{label}.infrastructure message must be text")
        run = None
    else:
        if infrastructure_error is not None:
            raise BatchValidationError(f"{label} has both a run and runner exception")
        run = _validate_run(
            run_value,
            game_name=game_name,
            policy_index=policy_index,
            case_label=label,
        )
    classified = classify_case_record(record)
    if record.get("status") != classified.value:
        raise BatchValidationError(f"{label} stored status disagrees with classifier")
    return run


def _validate_footer(
    footer: Mapping[str, Any],
    *,
    identities: Mapping[str, str],
    observed_counts: Counter[str],
    resumed_batch: bool,
) -> None:
    _exact_keys(footer, FOOTER_KEYS, "batch footer")
    if (
        _integer(footer.get("schema_version"), "batch footer schema_version")
        != BATCH_SCHEMA_VERSION
        or footer.get("artifact_type") != "marlrefine_prospective_batch_footer"
        or footer.get("classifier_id") != CLASSIFIER_ID
    ):
        raise BatchValidationError("batch footer schema/type/classifier differs")
    for field, expected in identities.items():
        if footer.get(field) != expected:
            raise BatchValidationError(f"footer {field} differs")
    if (
        _integer(footer.get("case_count"), "batch footer case_count")
        != EXPECTED_TRACE_COUNT
    ):
        raise BatchValidationError("footer must report exactly 840 traces")
    expected_counts = dict(sorted(observed_counts.items()))
    _strict_json_equal(
        footer.get("status_counts"),
        expected_counts,
        "batch footer status counts",
    )
    resumed_count = _integer(
        footer.get("resumed_infrastructure_cases"),
        "footer resumed_infrastructure_cases",
    )
    if resumed_count > EXPECTED_TRACE_COUNT:
        raise BatchValidationError("footer retry count exceeds trace count")
    if not resumed_batch and resumed_count != 0:
        raise BatchValidationError("initial batch reports infrastructure retries")


def _rational_summary(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "total": 0,
            "minimum": None,
            "maximum": None,
            "median": None,
            "mean": None,
        }
    ordered = sorted(values)
    length = len(ordered)
    if length % 2:
        median_num, median_den = ordered[length // 2], 1
    else:
        median_num = ordered[length // 2 - 1] + ordered[length // 2]
        median_den = 2
    divisor = math.gcd(sum(values), length)
    return {
        "count": length,
        "total": sum(values),
        "minimum": ordered[0],
        "maximum": ordered[-1],
        "median": {"numerator": median_num, "denominator": median_den},
        "mean": {
            "numerator": sum(values) // divisor,
            "denominator": length // divisor,
        },
    }


def _tolerance_sensitivity_summary(
    ratios: Sequence[float],
    *,
    nonfinite_or_unavailable: int,
) -> dict[str, Any]:
    """Count fixed tolerance-factor classifications without changing verdicts."""
    return {
        "comparable_site_count": len(ratios),
        "nonfinite_or_unavailable_site_count": nonfinite_or_unavailable,
        "within_one_tenth_primary": sum(ratio <= 0.1 for ratio in ratios),
        "within_primary": sum(ratio <= 1.0 for ratio in ratios),
        "within_ten_times_primary": sum(ratio <= 10.0 for ratio in ratios),
        "maximum_primary_tolerance_ratio": max(ratios, default=None),
    }


def _math_isclose_tolerance_ratio(
    expected: float,
    observed: float,
    *,
    atol: float,
    rtol: float,
) -> float:
    """Return residual/threshold for the exact ``math.isclose`` rule."""
    threshold = max(atol, rtol * max(abs(expected), abs(observed)))
    return abs(expected - observed) / threshold


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _exclusive_bucket(statuses: Sequence[str]) -> str:
    observed = set(statuses)
    if OutcomeStatus.INFRASTRUCTURE.value in observed:
        return "infrastructure_present"
    if OutcomeStatus.UNALIGNABLE.value in observed:
        return "unalignable_present_no_infrastructure"
    if OutcomeStatus.INAPPLICABLE.value in observed:
        return "inapplicable_present_no_infrastructure_or_unalignable"
    if OutcomeStatus.FAIL.value in observed:
        return "violation_present_all_traces_semantically_completed"
    if observed == {OutcomeStatus.PASS.value}:
        return "all_traces_no_observed_violation"
    raise BatchValidationError(f"unclassifiable game status profile: {statuses}")


def _status_profile(statuses: Sequence[str]) -> str:
    counts = Counter(statuses)
    return "|".join(
        f"{status}={counts.get(status, 0)}" for status in OutcomeStatus
    )


def _canonical_bytes(value: Any) -> int:
    return len(_canonical_line(value).encode("utf-8"))


def _two_axis_outcome(
    status: str,
    run: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """Preserve semantic evidence independently of execution completeness."""
    if run is None:
        return "no_verdict", "infrastructure"
    if run.get("applicable") is not True:
        return "no_verdict", "inapplicable"

    violations = run.get("violations")
    semantic_failure = isinstance(violations, list) and any(
        isinstance(item, Mapping)
        and item.get("code") not in NON_SEMANTIC_DIAGNOSTIC_CODES
        for item in violations
    )
    if semantic_failure:
        semantic = "observed_failure"
    elif status in {OutcomeStatus.PASS.value, OutcomeStatus.FAIL.value}:
        semantic = "no_observed_failure"
    else:
        semantic = "no_verdict"

    if status == OutcomeStatus.INFRASTRUCTURE.value:
        execution = "infrastructure"
    elif status == OutcomeStatus.UNALIGNABLE.value:
        execution = "unalignable"
    else:
        summary = run.get("summary")
        stop_reason = (
            summary.get("stop_reason") if isinstance(summary, Mapping) else None
        )
        if (
            stop_reason == "destination_episode_end"
            and summary.get("source_terminal") is True
            and summary.get("adapter_agents_remaining") == 0
        ):
            execution = "terminal_complete"
        elif stop_reason == "source_decision_limit":
            execution = "bounded_prefix"
        else:
            execution = "semantic_abort"
    return semantic, execution


def _aggregate(
    cases: Iterable[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    game_names: Sequence[str],
    game_strata: Mapping[str, str],
) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    semantic_evidence_counts: Counter[str] = Counter()
    execution_completeness_counts: Counter[str] = Counter()
    two_axis_counts: dict[str, Counter[str]] = {
        category: Counter() for category in SEMANTIC_EVIDENCE_CATEGORIES
    }
    statuses_by_game: dict[str, list[str]] = defaultdict(list)
    elapsed_ns: list[int] = []
    source_counts: list[int] = []
    destination_counts: list[int] = []
    ledger_counts: list[int] = []
    ledger_bytes: list[int] = []
    case_bytes: list[int] = []
    violation_occurrences: Counter[str] = Counter()
    violation_traces: dict[str, set[str]] = defaultdict(set)
    violation_games: dict[str, set[str]] = defaultdict(set)
    obligation_occurrences: Counter[str] = Counter()
    obligation_traces: dict[str, set[str]] = defaultdict(set)
    obligation_games: dict[str, set[str]] = defaultdict(set)
    obligation_code_occurrences: Counter[tuple[str, str]] = Counter()
    obligation_code_traces: dict[tuple[str, str], set[str]] = defaultdict(set)
    obligation_code_games: dict[tuple[str, str], set[str]] = defaultdict(set)
    finding_inventory: list[dict[str, Any]] = []
    stop_reasons: Counter[str] = Counter()
    obligation_evaluation_counts: dict[str, Counter[str]] = {
        obligation_id: Counter() for obligation_id in OBLIGATION_IDS
    }
    obligation_evaluation_games: dict[str, dict[str, set[str]]] = {
        obligation_id: defaultdict(set) for obligation_id in OBLIGATION_IDS
    }
    obligation_site_checks: Counter[str] = Counter()
    obligation_linked_findings: Counter[str] = Counter()
    unlinked_finding_occurrences: Counter[tuple[str, str]] = Counter()
    unlinked_finding_traces: dict[tuple[str, str], set[str]] = defaultdict(set)
    unlinked_finding_games: dict[tuple[str, str], set[str]] = defaultdict(set)
    unlinked_trace_ids: set[str] = set()
    unlinked_game_names: set[str] = set()
    execution_path_statuses: dict[str, Counter[str]] = {
        path: Counter() for path in EXECUTION_PATH_CATEGORIES
    }
    stop_reason_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    structural_occurrences: Counter[str] = Counter()
    structural_traces: dict[str, set[str]] = defaultdict(set)
    structural_games: dict[str, set[str]] = defaultdict(set)
    source_origin_kind_occurrences: Counter[str] = Counter()
    source_origin_kind_traces: dict[str, set[str]] = defaultdict(set)
    source_origin_kind_games: dict[str, set[str]] = defaultdict(set)
    final_source_kind_traces: dict[str, set[str]] = defaultdict(set)
    final_source_kind_games: dict[str, set[str]] = defaultdict(set)
    baseline_counts: dict[str, Counter[str]] = {
        name: Counter() for name in BASELINE_NAMES
    }
    baseline_games: dict[str, dict[str, set[str]]] = {
        name: defaultdict(set) for name in BASELINE_NAMES
    }
    baseline_finding_occurrences: dict[str, Counter[str]] = {
        name: Counter() for name in BASELINE_NAMES
    }
    witnesses: list[dict[str, Any]] = []
    witness_ratios: list[tuple[int, int]] = []
    witness_destination_prefix_lengths: list[int] = []
    witness_phases: Counter[str] = Counter()
    exact_call_witnesses = 0
    observation_tolerance_ratios: list[float] = []
    observation_residual_unavailable = 0
    reward_tolerance_ratios: list[float] = []
    reward_residual_unavailable = 0
    stratum_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    stratum_findings: dict[str, Counter[str]] = defaultdict(Counter)
    offered_actions_by_policy: dict[int, set[tuple[str, str, int, int]]] = {
        index: set() for index in range(len(TRACE_POLICIES))
    }
    selected_actions_by_policy: dict[int, set[tuple[str, str, int, int]]] = {
        index: set() for index in range(len(TRACE_POLICIES))
    }

    for ordinal, (record, run) in enumerate(cases):
        case = record["case"]
        case_id = str(case["case_id"])
        game_name = str(case["game_name"])
        status = str(record["status"])
        status_counts[status] += 1
        semantic_evidence, execution_completeness = _two_axis_outcome(status, run)
        semantic_evidence_counts[semantic_evidence] += 1
        execution_completeness_counts[execution_completeness] += 1
        two_axis_counts[semantic_evidence][execution_completeness] += 1
        policy_index = ordinal % len(TRACE_POLICIES)
        stratum = game_strata[game_name]
        stratum_statuses[stratum][status] += 1
        statuses_by_game[game_name].append(status)
        elapsed_ns.append(int(record["elapsed_ns"]))
        case_bytes.append(_canonical_bytes(record))
        if run is None:
            source_counts.append(0)
            destination_counts.append(0)
            ledger_counts.append(0)
            ledger_bytes.append(0)
            execution_path_statuses["no_run_infrastructure"][status] += 1
            for obligation_id in OBLIGATION_IDS:
                obligation_evaluation_counts[obligation_id]["not_evaluated"] += 1
                obligation_evaluation_games[obligation_id]["not_evaluated"].add(
                    game_name
                )
            for name in BASELINE_NAMES:
                baseline_counts[name]["not_evaluated_infrastructure_traces"] += 1
                baseline_games[name][
                    "not_evaluated_infrastructure_games"
                ].add(game_name)
            continue

        source_events = run["source_events"]
        destination_events = run["destination_events"]
        source_count = len(source_events)
        destination_count = len(destination_events)
        source_counts.append(source_count)
        destination_counts.append(destination_count)
        ledger_counts.append(source_count + destination_count)
        ledger_bytes.append(
            _canonical_bytes(
                {
                    "source_events": source_events,
                    "destination_events": destination_events,
                }
            )
        )
        summary = run["summary"]
        for event in source_events:
            metadata = event.get("metadata")
            node_kind = (
                metadata.get("node_kind_before")
                if isinstance(metadata, Mapping)
                else None
            )
            if isinstance(node_kind, str) and node_kind:
                source_origin_kind_occurrences[node_kind] += 1
                source_origin_kind_traces[node_kind].add(case_id)
                source_origin_kind_games[node_kind].add(game_name)
        final_source_kind = summary.get("source_node_kind")
        if isinstance(final_source_kind, str) and final_source_kind:
            final_source_kind_traces[final_source_kind].add(case_id)
            final_source_kind_games[final_source_kind].add(game_name)
        stop_reason = summary.get("stop_reason")
        if isinstance(stop_reason, str) and stop_reason:
            stop_reasons[stop_reason] += 1
            stop_reason_statuses[stop_reason][status] += 1

        if (
            stop_reason == "destination_episode_end"
            and summary.get("source_terminal") is True
            and summary.get("adapter_agents_remaining") == 0
        ):
            execution_path = "terminal_complete"
        elif stop_reason == "source_decision_limit":
            execution_path = "bounded_prefix"
        else:
            execution_path = "other_serialized_run"
        execution_path_statuses[execution_path][status] += 1

        feature_counts: Counter[str] = Counter()
        for segment in run["alignment"]["segments"]:
            if segment["kind"] != "transition":
                continue
            feature_counts["aligned_transition_segments"] += 1
            source_span = segment["source_span"]
            destination_span = segment["destination_span"]
            source_width = int(source_span["stop"]) - int(source_span["start"])
            destination_width = int(destination_span["stop"]) - int(
                destination_span["start"]
            )
            if source_width == 1 and destination_width > 1:
                feature_counts["one_to_many_transition_segments"] += 1
            if source_width > 1 and destination_width == 1:
                feature_counts["many_to_one_transition_segments"] += 1
            source_segment_events = segment["source_events"]
            destination_segment_events = segment["destination_events"]
            if source_segment_events and destination_segment_events:
                dimension = len(source_segment_events[0]["rewards"])
                if all(
                    len(event["rewards"]) == dimension
                    for event in (
                        *source_segment_events,
                        *destination_segment_events,
                    )
                ):
                    source_reward = [
                        math.fsum(
                            float(event["rewards"][index])
                            for event in source_segment_events
                        )
                        for index in range(dimension)
                    ]
                    destination_reward = [
                        math.fsum(
                            float(event["rewards"][index])
                            for event in destination_segment_events
                        )
                        for index in range(dimension)
                    ]
                    ratios = [
                        _math_isclose_tolerance_ratio(
                            expected,
                            observed,
                            atol=1e-12,
                            rtol=1e-12,
                        )
                        for expected, observed in zip(
                            source_reward,
                            destination_reward,
                            strict=True,
                        )
                    ]
                    if all(math.isfinite(ratio) for ratio in ratios):
                        reward_tolerance_ratios.append(max(ratios, default=0.0))
                    else:
                        reward_residual_unavailable += 1
                else:
                    reward_residual_unavailable += 1
        for event in destination_events:
            metadata = event.get("metadata")
            if isinstance(metadata, Mapping):
                state_digest = metadata.get("source_state_digest_before")
                player = metadata.get("player")
                action = metadata.get("action")
                legal_actions = metadata.get("source_legal_actions_before")
                if (
                    isinstance(state_digest, str)
                    and isinstance(player, int)
                    and not isinstance(player, bool)
                    and isinstance(action, int)
                    and not isinstance(action, bool)
                    and isinstance(legal_actions, list)
                ):
                    for legal_action in legal_actions:
                        if isinstance(legal_action, int) and not isinstance(
                            legal_action, bool
                        ):
                            offered_actions_by_policy[policy_index].add(
                                (game_name, state_digest, player, legal_action)
                            )
                    selected_actions_by_policy[policy_index].add(
                        (game_name, state_digest, player, action)
                    )
            residual = (
                metadata.get("observation_numeric_residual_before")
                if isinstance(metadata, Mapping)
                else None
            )
            if residual is None:
                continue
            if not isinstance(residual, Mapping) or residual.get("finite") is not True:
                observation_residual_unavailable += 1
                continue
            ratio = residual.get("primary_tolerance_ratio")
            if (
                isinstance(ratio, (int, float))
                and not isinstance(ratio, bool)
                and math.isfinite(float(ratio))
                and float(ratio) >= 0
            ):
                observation_tolerance_ratios.append(float(ratio))
            else:
                observation_residual_unavailable += 1
        for event in destination_events:
            metadata = event["metadata"]
            instrumentation = metadata.get("progress_instrumentation")
            progress_before = (
                instrumentation.get("progress_before")
                if isinstance(instrumentation, Mapping)
                else None
            )
            progress_after = (
                instrumentation.get("progress_after")
                if isinstance(instrumentation, Mapping)
                else None
            )
            advances = (
                isinstance(progress_before, int)
                and not isinstance(progress_before, bool)
                and isinstance(progress_after, int)
                and not isinstance(progress_after, bool)
                and progress_after > progress_before
            )
            if event["cleanup"] is True:
                feature_counts["destination_cleanup_calls"] += 1
            elif advances:
                feature_counts["destination_commit_calls"] += 1
            elif metadata.get("buffer_only") is True:
                feature_counts["destination_buffer_calls"] += 1
            else:
                feature_counts["destination_other_stutter_calls"] += 1
        feature_counts["destination_stutter_calls"] = feature_counts[
            "destination_buffer_calls"
        ]
        feature_counts["terminal_cleanup_calls"] = feature_counts[
            "destination_cleanup_calls"
        ]
        chance_event_count = summary.get("chance_event_count", 0)
        if (
            not isinstance(chance_event_count, int)
            or isinstance(chance_event_count, bool)
            or chance_event_count < 0
        ):
            raise BatchValidationError(
                f"{case_id} summary chance_event_count is invalid"
            )
        feature_counts["source_chance_events"] = chance_event_count
        for feature in STRUCTURAL_COVERAGE_FEATURES:
            count = feature_counts[feature]
            structural_occurrences[feature] += count
            if count:
                structural_traces[feature].add(case_id)
                structural_games[feature].add(game_name)

        linked_obligations_by_finding: dict[int, list[str]] = defaultdict(list)
        for evaluation in run["obligation_evaluations"]:
            obligation_id = str(evaluation["obligation_id"])
            outcome = str(evaluation["outcome"])
            obligation_evaluation_counts[obligation_id][outcome] += 1
            obligation_evaluation_games[obligation_id][outcome].add(game_name)
            obligation_site_checks[obligation_id] += int(
                evaluation["evaluation_count"]
            )
            obligation_linked_findings[obligation_id] += len(
                evaluation["finding_indices"]
            )
            for finding_index in evaluation["finding_indices"]:
                linked_obligations_by_finding[int(finding_index)].append(
                    obligation_id
                )

        for violation_index, violation in enumerate(run["violations"]):
            code = str(violation["code"])
            obligation = str(violation["obligation"])
            finding_inventory.append(
                {
                    "case_id": case_id,
                    "violation_index": violation_index,
                    "finding_sha256": hashlib.sha256(
                        _canonical_line(violation).encode("utf-8")
                    ).hexdigest(),
                    "obligation": obligation,
                    "code": code,
                    "linked_obligation_ids": linked_obligations_by_finding.get(
                        violation_index, []
                    ),
                }
            )
            violation_occurrences[code] += 1
            violation_traces[code].add(case_id)
            violation_games[code].add(game_name)
            obligation_occurrences[obligation] += 1
            obligation_traces[obligation].add(case_id)
            obligation_games[obligation].add(game_name)
            obligation_code_occurrences[(obligation, code)] += 1
            obligation_code_traces[(obligation, code)].add(case_id)
            obligation_code_games[(obligation, code)].add(game_name)
            stratum_findings[stratum][f"{obligation}/{code}"] += 1
            if violation_index not in linked_obligations_by_finding:
                key = (obligation, code)
                unlinked_finding_occurrences[key] += 1
                unlinked_finding_traces[key].add(case_id)
                unlinked_finding_games[key].add(game_name)
                unlinked_trace_ids.add(case_id)
                unlinked_game_names.add(game_name)

        for baseline in run["baselines"]:
            name = str(baseline["baseline"])
            findings = baseline["findings"]
            if baseline["applicable"]:
                baseline_counts[name]["applicable_traces"] += 1
                baseline_games[name]["applicable_games"].add(game_name)
                if findings:
                    baseline_counts[name]["detected_traces"] += 1
                    baseline_games[name]["detected_games"].add(game_name)
                else:
                    baseline_counts[name]["no_detection_traces"] += 1
                    baseline_games[name]["no_detection_games"].add(game_name)
            else:
                baseline_counts[name]["inapplicable_traces"] += 1
                baseline_games[name]["inapplicable_games"].add(game_name)
            for finding in findings:
                baseline_finding_occurrences[name][str(finding["code"])] += 1

        if run["violations"]:
            case_baseline_outcomes = {
                str(baseline["baseline"]): (
                    "detected"
                    if baseline["applicable"] and baseline["findings"]
                    else (
                        "not_detected"
                        if baseline["applicable"]
                        else "inapplicable"
                    )
                )
                for baseline in run["baselines"]
            }
            ablation_localization = {
                name: (
                    ABLATION_LOCALIZATION_RESOLUTION[name]
                    if outcome == "detected"
                    else None
                )
                for name, outcome in case_baseline_outcomes.items()
            }
            localized_findings = localize_all_divergences(run)
            if len(localized_findings) != len(run["violations"]):
                raise BatchValidationError(
                    f"{case_id} localizer did not cover every finding"
                )
            for violation_index, witness in enumerate(localized_findings):
                witnesses.append(
                    {
                        "case_id": case_id,
                        "violation_index": violation_index,
                        "game_name": game_name,
                        "status": status,
                        "localized_witness_sha256": hashlib.sha256(
                            _canonical_line(witness).encode("utf-8")
                        ).hexdigest(),
                        "baseline_outcomes": case_baseline_outcomes,
                        "case_signal_resolution_ceiling": (
                            ablation_localization
                        ),
                        "witness": witness,
                    }
                )
                ratio = witness.get("prefix_to_original_event_ratio")
                if isinstance(ratio, Mapping):
                    witness_ratios.append(
                        (int(ratio["numerator"]), int(ratio["denominator"]))
                    )
                specificity = witness.get("diagnostic_specificity")
                if isinstance(specificity, Mapping):
                    phase = specificity.get("destination_phase")
                    if isinstance(phase, str):
                        witness_phases[phase] += 1
                    prefix_calls = specificity.get(
                        "destination_prefix_call_count"
                    )
                    if isinstance(prefix_calls, int) and not isinstance(
                        prefix_calls, bool
                    ):
                        witness_destination_prefix_lengths.append(prefix_calls)
                    if isinstance(
                        specificity.get("exact_destination_call_index"), int
                    ):
                        exact_call_witnesses += 1

    overlapping = {
        f"games_with_any_{status.value}": sum(
            status.value in statuses for statuses in statuses_by_game.values()
        )
        for status in OutcomeStatus
    }
    overlapping.update(
        {
            "games_semantically_complete_all_eight_traces": sum(
                len(statuses) == 8
                and set(statuses).issubset(
                    {OutcomeStatus.PASS.value, OutcomeStatus.FAIL.value}
                )
                for statuses in statuses_by_game.values()
            ),
            "games_all_eight_no_observed_violation": sum(
                statuses == [OutcomeStatus.PASS.value] * 8
                for statuses in statuses_by_game.values()
            ),
        }
    )
    exclusive = Counter(
        _exclusive_bucket(statuses) for statuses in statuses_by_game.values()
    )
    if sum(exclusive.values()) != len(game_names):
        raise BatchValidationError("exclusive game accounting is not exhaustive")
    profiles = Counter(
        _status_profile(statuses) for statuses in statuses_by_game.values()
    )
    cumulative_offered: set[tuple[str, str, int, int]] = set()
    cumulative_selected: set[tuple[str, str, int, int]] = set()
    policy_saturation: list[dict[str, Any]] = []
    for policy_index, policy in enumerate(TRACE_POLICIES):
        prior_selected = len(cumulative_selected)
        cumulative_offered.update(offered_actions_by_policy[policy_index])
        cumulative_selected.update(selected_actions_by_policy[policy_index])
        policy_saturation.append(
            {
                "policy_index": policy_index,
                "policy_name": policy.name,
                "cumulative_offered_state_player_actions": len(
                    cumulative_offered
                ),
                "cumulative_selected_state_player_actions": len(
                    cumulative_selected
                ),
                "marginal_new_selected_state_player_actions": (
                    len(cumulative_selected) - prior_selected
                ),
                "selected_to_offered_ratio": (
                    None
                    if not cumulative_offered
                    else {
                        "numerator": len(cumulative_selected),
                        "denominator": len(cumulative_offered),
                    }
                ),
            }
        )

    violation_code_summary = {
        code: {
            "occurrence_count": violation_occurrences[code],
            "trace_count": len(violation_traces[code]),
            "distinct_game_count": len(violation_games[code]),
        }
        for code in sorted(violation_occurrences)
    }
    obligation_summary = {
        obligation: {
            "occurrence_count": obligation_occurrences[obligation],
            "trace_count": len(obligation_traces[obligation]),
            "distinct_game_count": len(obligation_games[obligation]),
        }
        for obligation in sorted(obligation_occurrences)
    }
    obligation_code_summary = {
        obligation: {
            code: {
                "occurrence_count": obligation_code_occurrences[
                    (obligation, code)
                ],
                "trace_count": len(obligation_code_traces[(obligation, code)]),
                "distinct_game_count": len(
                    obligation_code_games[(obligation, code)]
                ),
            }
            for code in sorted(
                item_code
                for item_obligation, item_code in obligation_code_occurrences
                if item_obligation == obligation
            )
        }
        for obligation in sorted(
            {item_obligation for item_obligation, _ in obligation_code_occurrences}
        )
    }
    obligation_coverage: dict[str, Any] = {}
    for obligation_id in OBLIGATION_IDS:
        counts = obligation_evaluation_counts[obligation_id]
        trace_outcomes = {
            outcome: counts.get(outcome, 0)
            for outcome in OBLIGATION_EVALUATION_OUTCOMES
        }
        if sum(trace_outcomes.values()) != EXPECTED_TRACE_COUNT:
            raise BatchValidationError(
                f"{obligation_id} evaluation outcomes do not sum to 840"
            )
        obligation_coverage[obligation_id] = {
            "trace_outcomes": trace_outcomes,
            "applicable_trace_count": (
                trace_outcomes["evaluated_pass"]
                + trace_outcomes["evaluated_fail"]
            ),
            "evaluation_count": obligation_site_checks[obligation_id],
            "linked_finding_count": obligation_linked_findings[obligation_id],
            "overlapping_distinct_game_counts": {
                outcome: len(
                    obligation_evaluation_games[obligation_id].get(outcome, set())
                )
                for outcome in OBLIGATION_EVALUATION_OUTCOMES
            },
        }

    unlinked_by_family_and_code = {
        obligation: {
            code: {
                "occurrence_count": unlinked_finding_occurrences[
                    (obligation, code)
                ],
                "trace_count": len(
                    unlinked_finding_traces[(obligation, code)]
                ),
                "distinct_game_count": len(
                    unlinked_finding_games[(obligation, code)]
                ),
            }
            for code in sorted(
                item_code
                for item_obligation, item_code in unlinked_finding_occurrences
                if item_obligation == obligation
            )
        }
        for obligation in sorted(
            {
                item_obligation
                for item_obligation, _ in unlinked_finding_occurrences
            }
        )
    }
    all_statuses = tuple(status.value for status in OutcomeStatus)
    completion_by_status = {
        path: {
            status: execution_path_statuses[path].get(status, 0)
            for status in all_statuses
        }
        for path in EXECUTION_PATH_CATEGORIES
    }
    if sum(
        count
        for path_counts in completion_by_status.values()
        for count in path_counts.values()
    ) != EXPECTED_TRACE_COUNT:
        raise BatchValidationError("execution-path accounting does not sum to 840")
    stop_reason_by_status = {
        reason: {
            status: stop_reason_statuses[reason].get(status, 0)
            for status in all_statuses
        }
        for reason in sorted(stop_reason_statuses)
    }
    structural_coverage = {
        feature: {
            "occurrence_count": structural_occurrences[feature],
            "trace_count": len(structural_traces[feature]),
            "distinct_game_count": len(structural_games[feature]),
        }
        for feature in STRUCTURAL_COVERAGE_FEATURES
    }
    baseline_summary: dict[str, Any] = {}
    for name in BASELINE_NAMES:
        counts = baseline_counts[name]
        trace_outcome_keys = (
            "applicable_traces",
            "detected_traces",
            "no_detection_traces",
            "inapplicable_traces",
            "not_evaluated_infrastructure_traces",
        )
        applicable = counts.get("applicable_traces", 0)
        if applicable != counts.get("detected_traces", 0) + counts.get(
            "no_detection_traces", 0
        ):
            raise BatchValidationError("baseline applicable accounting differs")
        if (
            applicable
            + counts.get("inapplicable_traces", 0)
            + counts.get("not_evaluated_infrastructure_traces", 0)
            != EXPECTED_TRACE_COUNT
        ):
            raise BatchValidationError("baseline trace accounting does not sum to 840")
        baseline_summary[name] = {
            "trace_outcomes": {
                key: counts.get(key, 0) for key in trace_outcome_keys
            },
            "overlapping_distinct_game_counts": {
                key: len(baseline_games[name].get(key, set()))
                for key in (
                    "applicable_games",
                    "detected_games",
                    "no_detection_games",
                    "inapplicable_games",
                    "not_evaluated_infrastructure_games",
                )
            },
            "finding_code_occurrences": _counter_dict(
                baseline_finding_occurrences[name]
            ),
            "interpretation": (
                "descriptive baseline symptoms only; no causal-root detection "
                "credit is inferred"
            ),
        }

    ratio_summary: dict[str, Any]
    if witness_ratios:
        ordered_ratios = sorted(
            witness_ratios,
            key=lambda item: Fraction(item[0], item[1]),
        )
        middle = len(ordered_ratios) // 2
        if len(ordered_ratios) % 2:
            numerator, denominator = ordered_ratios[middle]
        else:
            left = ordered_ratios[middle - 1]
            right = ordered_ratios[middle]
            numerator = left[0] * right[1] + right[0] * left[1]
            denominator = 2 * left[1] * right[1]
        divisor = math.gcd(numerator, denominator)
        ratio_summary = {
            "count": len(witness_ratios),
            "median": {
                "numerator": numerator // divisor,
                "denominator": denominator // divisor,
            },
        }
    else:
        ratio_summary = {"count": 0, "median": None}

    trace_counts = {
        status.value: status_counts.get(status.value, 0) for status in OutcomeStatus
    }
    trace_counts.update(
        {
            "scheduled": EXPECTED_TRACE_COUNT,
            "attempted": EXPECTED_TRACE_COUNT,
            "semantically_completed": status_counts.get("pass", 0)
            + status_counts.get("fail", 0),
            "with_applicable_violation": status_counts.get("fail", 0),
            "no_observed_violation": status_counts.get("pass", 0),
        }
    )
    if sum(semantic_evidence_counts.values()) != EXPECTED_TRACE_COUNT:
        raise BatchValidationError("semantic-evidence accounting does not sum to 840")
    if sum(execution_completeness_counts.values()) != EXPECTED_TRACE_COUNT:
        raise BatchValidationError(
            "execution-completeness accounting does not sum to 840"
        )
    elapsed_summary = _rational_summary(elapsed_ns)
    elapsed_summary["definition"] = (
        "selected final case-record runner durations; orchestration/archive "
        "verification time and superseded retry attempts are not present in "
        f"batch schema v{BATCH_SCHEMA_VERSION}"
    )
    return {
        "trace_level_accounting": {
            "unit": "scheduled game-policy trace",
            "counts": trace_counts,
            "invariant": (
                "the five classifier statuses are mutually exclusive and sum to 840"
            ),
        },
        "two_axis_trace_accounting": {
            "semantic_evidence": {
                category: semantic_evidence_counts.get(category, 0)
                for category in SEMANTIC_EVIDENCE_CATEGORIES
            },
            "execution_completeness": {
                category: execution_completeness_counts.get(category, 0)
                for category in EXECUTION_COMPLETENESS_CATEGORIES
            },
            "cross_tabulation": {
                semantic: {
                    execution: two_axis_counts[semantic].get(execution, 0)
                    for execution in EXECUTION_COMPLETENESS_CATEGORIES
                }
                for semantic in SEMANTIC_EVIDENCE_CATEGORIES
            },
            "invariant": (
                "each axis independently sums to 840; an observed semantic failure "
                "is retained even when later execution becomes unalignable or "
                "infrastructure-limited"
            ),
        },
        "game_level_accounting": {
            "unit": "distinct prospective game type with eight scheduled traces",
            "population": len(game_names),
            "exclusive_reporting_buckets": {
                "precedence": list(EXCLUSIVE_GAME_BUCKETS),
                "counts": {
                    bucket: exclusive.get(bucket, 0)
                    for bucket in EXCLUSIVE_GAME_BUCKETS
                },
                "invariant": "buckets are non-overlapping and sum to 105",
                "interpretation": (
                    "precedence represents completeness of evidence, not causal "
                    "severity; overlapping flags below preserve mixed outcomes"
                ),
            },
            "overlapping_flags": {
                "counts": overlapping,
                "invariant": "flags may overlap and must not be summed",
            },
            "status_profiles": _counter_dict(profiles),
        },
        "violations": {
            "prospective_finding_inventory": finding_inventory,
            "by_code": violation_code_summary,
            "by_obligation": obligation_summary,
            "by_obligation_and_code": obligation_code_summary,
            "unlinked_execution_or_alignment_diagnostics": {
                "occurrence_count": sum(unlinked_finding_occurrences.values()),
                "trace_count": len(unlinked_trace_ids),
                "distinct_game_count": len(unlinked_game_names),
                "by_obligation_and_code": unlinked_by_family_and_code,
            },
            "interpretation": (
                "occurrences, traces, and games are symptoms; none is a "
                "causal-root count"
            ),
        },
        "obligation_evaluation_coverage": {
            "unit": "scheduled game-policy trace",
            "ledger_schema_id": OBLIGATION_LEDGER_SCHEMA_ID,
            "by_obligation": obligation_coverage,
            "invariant": (
                "each obligation's four trace outcomes are mutually exclusive "
                "and sum to 840"
            ),
        },
        "execution_path_coverage": {
            "completion_by_status": completion_by_status,
            "stop_reason_by_status": stop_reason_by_status,
            "observed_structure": structural_coverage,
            "source_event_origin_node_kinds": {
                kind: {
                    "occurrence_count": source_origin_kind_occurrences[kind],
                    "trace_count": len(source_origin_kind_traces[kind]),
                    "distinct_game_count": len(source_origin_kind_games[kind]),
                }
                for kind in sorted(source_origin_kind_occurrences)
            },
            "final_source_boundary_kinds": {
                kind: {
                    "trace_count": len(final_source_kind_traces[kind]),
                    "distinct_game_count": len(final_source_kind_games[kind]),
                }
                for kind in sorted(final_source_kind_traces)
            },
            "action_coverage": {
                "unit": "unique game-state-player-action tuple on visited states",
                "cumulative_by_policy_order": policy_saturation,
            },
            "status_by_registry_stratum": {
                stratum: {
                    status: stratum_statuses[stratum].get(status, 0)
                    for status in all_statuses
                }
                for stratum in sorted(stratum_statuses)
            },
            "finding_occurrences_by_registry_stratum": {
                stratum: _counter_dict(counts)
                for stratum, counts in sorted(stratum_findings.items())
            },
            "interpretation": (
                "coverage describes recorded bounded paths and is not proof over "
                "unvisited behavior"
            ),
        },
        "compatible_baselines": baseline_summary,
        "tolerance_sensitivity": {
            "factors": [0.1, 1.0, 10.0],
            "aligned_reward_values": _tolerance_sensitivity_summary(
                reward_tolerance_ratios,
                nonfinite_or_unavailable=reward_residual_unavailable,
            ),
            "floating_observation_values": _tolerance_sensitivity_summary(
                observation_tolerance_ratios,
                nonfinite_or_unavailable=observation_residual_unavailable,
            ),
            "interpretation": (
                "prespecified secondary reclassification of stored residuals; "
                "primary verdicts retain factor 1.0 and exact signature checks"
            ),
        },
        "cost": {
            "elapsed_ns": elapsed_summary,
            "source_event_count": _rational_summary(source_counts),
            "destination_event_count": _rational_summary(destination_counts),
            "ledger_event_count": _rational_summary(ledger_counts),
            "canonical_ledger_json_bytes": _rational_summary(ledger_bytes),
            "canonical_case_record_json_bytes": _rational_summary(case_bytes),
            "peak_memory_bytes": None,
            "peak_memory_note": (
                f"not recorded by prospective batch schema v{BATCH_SCHEMA_VERSION}"
            ),
        },
        "stop_reasons": _counter_dict(stop_reasons),
        "witness_localization": {
            "localizer_id": LOCALIZER_ID,
            "method": (
                "one unmodified original ledger prefix per recorded finding; "
                "the first finding remains derivable chronologically; no "
                "minimization or delta debugging"
            ),
            "witness_count": len(witnesses),
            "prefix_to_original_event_ratio": ratio_summary,
            "destination_prefix_call_count": _rational_summary(
                witness_destination_prefix_lengths
            ),
            "exact_destination_call_attribution_count": exact_call_witnesses,
            "destination_phase_counts": _counter_dict(witness_phases),
            "ablation_resolution_definitions": dict(
                ABLATION_LOCALIZATION_RESOLUTION
            ),
            "witnesses": witnesses,
        },
    }


def _pending_manual_values() -> dict[str, int | None]:
    return {field: None for field in MANUAL_FIELDS}


def _validate_witness_reference(
    value: Any,
    label: str,
    *,
    provenance: str,
    adjudication_status: str,
    raw_batch_sha256: str,
    prospective_case_ids: frozenset[str],
    prospective_witnesses: Mapping[tuple[str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    witness = _mapping(value, label)
    _exact_keys(witness, WITNESS_REFERENCE_KEYS, label)
    case_id = _nonblank_string(witness.get("case_id"), f"{label}.case_id")
    evidence_sha = _sha(
        witness.get("evidence_artifact_sha256"),
        f"{label}.evidence_artifact_sha256",
    )
    if witness.get("localizer_id") != LOCALIZER_ID:
        raise BatchValidationError(f"{label}.localizer_id differs")
    localized_sha = _sha(
        witness.get("localized_witness_sha256"),
        f"{label}.localized_witness_sha256",
    )
    boundary_value = _mapping(witness.get("boundary"), f"{label}.boundary")
    _exact_keys(boundary_value, BOUNDARY_REFERENCE_KEYS, f"{label}.boundary")
    segment_index_value = boundary_value.get("segment_index")
    segment_index = (
        None
        if segment_index_value is None
        else _integer(segment_index_value, f"{label}.boundary.segment_index")
    )
    boundary = {
        "segment_index": segment_index,
        "source_event_stop": _integer(
            boundary_value.get("source_event_stop"),
            f"{label}.boundary.source_event_stop",
        ),
        "destination_event_stop": _integer(
            boundary_value.get("destination_event_stop"),
            f"{label}.boundary.destination_event_stop",
        ),
        "selected_violation_index": _integer(
            boundary_value.get("selected_violation_index"),
            f"{label}.boundary.selected_violation_index",
        ),
    }

    if provenance == "prospective":
        expected = prospective_witnesses.get(
            (case_id, boundary["selected_violation_index"])
        )
        if expected is None:
            raise BatchValidationError(
                f"{label} does not reference a localized prospective witness"
            )
        if evidence_sha != raw_batch_sha256:
            raise BatchValidationError(
                f"{label} prospective evidence must reference the raw batch"
            )
        if localized_sha != expected["localized_witness_sha256"]:
            raise BatchValidationError(
                f"{label}.localized_witness_sha256 differs from frozen analysis"
            )
        expected_witness = _mapping(expected["witness"], "localized witness")
        expected_boundary = _mapping(
            expected_witness.get("boundary"),
            "localized witness boundary",
        )
        expected_reference = {
            "segment_index": expected_boundary.get("segment_index"),
            "source_event_stop": expected_boundary.get("source_event_stop"),
            "destination_event_stop": expected_boundary.get(
                "destination_event_stop"
            ),
            "selected_violation_index": expected_witness.get(
                "selected_violation_index"
            ),
        }
        if boundary != expected_reference:
            raise BatchValidationError(
                f"{label}.boundary differs from frozen localization"
            )
        if adjudication_status == "confirmed" and expected.get("status") != (
            OutcomeStatus.FAIL.value
        ):
            raise BatchValidationError(
                f"{label} confirmed prospective root must reference a failed trace"
            )
    elif case_id in prospective_case_ids:
        raise BatchValidationError(
            f"{label} labels a prospective case as discovery provenance"
        )

    return {
        "case_id": case_id,
        "evidence_artifact_sha256": evidence_sha,
        "localizer_id": LOCALIZER_ID,
        "localized_witness_sha256": localized_sha,
        "boundary": boundary,
    }


def _validate_contract(
    value: Any,
    label: str,
    *,
    adjudication_status: str,
) -> dict[str, str]:
    contract = _mapping(value, label)
    _exact_keys(contract, CONTRACT_KEYS, label)
    citation = _nonblank_string(contract.get("citation"), f"{label}.citation")
    classification = _nonblank_string(
        contract.get("claim_classification"),
        f"{label}.claim_classification",
    )
    if adjudication_status == "confirmed":
        allowed = CONFIRMED_CLAIM_CLASSIFICATIONS
    elif adjudication_status == "pending":
        allowed = frozenset({"pending"})
    else:
        allowed = frozenset({"oracle_issue", "not_a_defect"})
    if classification not in allowed:
        raise BatchValidationError(
            f"{label}.claim_classification is inconsistent with "
            f"{adjudication_status} adjudication"
        )
    return {"citation": citation, "claim_classification": classification}


def _validate_causal_patch(
    value: Any,
    label: str,
    *,
    adjudication_status: str,
    provenance: str,
    frozen_source_tree_sha256: str,
) -> dict[str, str | None] | None:
    if adjudication_status != "confirmed":
        if value is not None:
            raise BatchValidationError(
                f"{label} is allowed only for a confirmed root"
            )
        return None
    patch = _mapping(value, label)
    _exact_keys(patch, CAUSAL_PATCH_KEYS, label)
    stock_sha = _sha(
        patch.get("stock_source_tree_sha256"),
        f"{label}.stock_source_tree_sha256",
    )
    treatment_sha = _sha(
        patch.get("treatment_source_tree_sha256"),
        f"{label}.treatment_source_tree_sha256",
    )
    patch_sha = _sha(patch.get("patch_sha256"), f"{label}.patch_sha256")
    reference = _nonblank_string(
        patch.get("evidence_reference"),
        f"{label}.evidence_reference",
    )
    if stock_sha == treatment_sha:
        raise BatchValidationError(f"{label} treatment tree equals stock tree")
    if provenance == "prospective" and stock_sha != frozen_source_tree_sha256:
        raise BatchValidationError(
            f"{label} prospective stock tree differs from frozen source"
        )
    return {
        "stock_source_tree_sha256": stock_sha,
        "treatment_source_tree_sha256": treatment_sha,
        "patch_sha256": patch_sha,
        "evidence_reference": reference,
    }


def _validate_root_baselines(
    value: Any,
    label: str,
    *,
    adjudication_status: str,
    prospective_baseline_outcomes: Mapping[str, Any] | None,
    prospective_case_id: str | None,
    raw_batch_sha256: str,
    causal_patch_sha256: str | None,
    external_baseline_sha256: str | None,
    external_stock_outcomes: Mapping[str, str] | None,
) -> dict[str, Any]:
    baselines = _mapping(value, label)
    _exact_keys(baselines, ROOT_BASELINES_KEYS, label)
    trace_rules = {
        "detected",
        "not_detected",
        "inapplicable",
        "not_evaluated",
    }
    external_rules = {"failed", "passed", "inapplicable", "not_evaluated"}
    rules = {
        **{baseline_name: trace_rules for baseline_name in BASELINE_NAMES},
        **{
            baseline_name: external_rules
            for baseline_name in EXTERNAL_BASELINE_NAMES
        },
    }
    normalized: dict[str, Any] = {}
    for baseline_name, outcomes in rules.items():
        item_label = f"{label}.{baseline_name}"
        item = _mapping(baselines.get(baseline_name), item_label)
        _exact_keys(item, BASELINE_CREDIT_KEYS, item_label)
        outcome = _nonblank_string(item.get("outcome"), f"{item_label}.outcome")
        if outcome not in outcomes:
            raise BatchValidationError(f"{item_label}.outcome differs")
        reached_value = item.get("root_witness_reached")
        _boolean(reached_value, f"{item_label}.root_witness_reached")
        root_witness_reached = bool(reached_value)
        if (
            prospective_baseline_outcomes is not None
            and baseline_name in BASELINE_NAMES
            and outcome != prospective_baseline_outcomes.get(baseline_name)
        ):
            raise BatchValidationError(
                f"{item_label}.outcome differs from the first witness trace"
            )
        outcome_evidence_value = item.get("outcome_evidence")
        outcome_evidence: dict[str, str | None] | None
        if outcome == "not_evaluated":
            if outcome_evidence_value is not None:
                raise BatchValidationError(
                    f"{item_label}.not_evaluated cannot carry outcome evidence"
                )
            outcome_evidence = None
        else:
            raw_outcome_evidence = _mapping(
                outcome_evidence_value,
                f"{item_label}.outcome_evidence",
            )
            _exact_keys(
                raw_outcome_evidence,
                BASELINE_EVIDENCE_KEYS,
                f"{item_label}.outcome_evidence",
            )
            outcome_evidence = {
                "artifact_sha256": _sha(
                    raw_outcome_evidence.get("artifact_sha256"),
                    f"{item_label}.outcome_evidence.artifact_sha256",
                ),
                "evidence_reference": _nonblank_string(
                    raw_outcome_evidence.get("evidence_reference"),
                    f"{item_label}.outcome_evidence.evidence_reference",
                ),
            }
        if (
            prospective_baseline_outcomes is not None
            and baseline_name in BASELINE_NAMES
            and (
                outcome_evidence is None
                or outcome_evidence["artifact_sha256"] != raw_batch_sha256
                or outcome_evidence["evidence_reference"] != prospective_case_id
            )
        ):
            raise BatchValidationError(
                f"{item_label} prospective outcome evidence reference differs"
            )
        if (
            prospective_case_id is not None
            and baseline_name == "stock_api"
            and external_baseline_sha256 is not None
            and external_stock_outcomes is not None
        ):
            game_name = prospective_case_id.rsplit("::", maxsplit=1)[0]
            expected_external_outcome = external_stock_outcomes.get(game_name)
            if expected_external_outcome is None:
                raise BatchValidationError(
                    f"{item_label} game is absent from bound external baselines"
                )
            if outcome != expected_external_outcome:
                raise BatchValidationError(
                    f"{item_label}.outcome differs from bound external baseline"
                )
            if (
                outcome_evidence is None
                or outcome_evidence["artifact_sha256"]
                != external_baseline_sha256
                or outcome_evidence["evidence_reference"] != game_name
            ):
                raise BatchValidationError(
                    f"{item_label} evidence differs from bound external baseline"
                )
        is_detection = outcome in {"detected", "failed"}
        is_no_detection = outcome in {"not_detected", "passed"}
        if prospective_baseline_outcomes is not None and baseline_name in (
            BASELINE_NAMES
        ):
            expected_reachability = is_detection or is_no_detection
            if root_witness_reached != expected_reachability:
                raise BatchValidationError(
                    f"{item_label}.root_witness_reached differs from baseline "
                    "applicability on the first witness trace"
                )

        attribution = _nonblank_string(
            item.get("causal_attribution"),
            f"{item_label}.causal_attribution",
        )
        if attribution not in {
            "same_root",
            "different_or_unresolved",
            "not_applicable",
        }:
            raise BatchValidationError(f"{item_label}.causal_attribution differs")
        causal_evidence_value = item.get("causal_evidence")
        causal_evidence: dict[str, Any] | None = None
        if attribution == "same_root":
            if adjudication_status != "confirmed":
                raise BatchValidationError(
                    f"{item_label} cannot attribute an unconfirmed root"
                )
            if not is_detection or not root_witness_reached:
                raise BatchValidationError(
                    f"{item_label}.same_root requires a reached detection"
                )
            raw_causal_evidence = _mapping(
                causal_evidence_value,
                f"{item_label}.causal_evidence",
            )
            _exact_keys(
                raw_causal_evidence,
                BASELINE_CAUSAL_EVIDENCE_KEYS,
                f"{item_label}.causal_evidence",
            )
            causal_evidence = {
                "artifact_sha256": _sha(
                    raw_causal_evidence.get("artifact_sha256"),
                    f"{item_label}.causal_evidence.artifact_sha256",
                ),
                "evidence_reference": _nonblank_string(
                    raw_causal_evidence.get("evidence_reference"),
                    f"{item_label}.causal_evidence.evidence_reference",
                ),
                "patch_sha256": _sha(
                    raw_causal_evidence.get("patch_sha256"),
                    f"{item_label}.causal_evidence.patch_sha256",
                ),
            }
            if causal_evidence["patch_sha256"] != causal_patch_sha256:
                raise BatchValidationError(
                    f"{item_label}.causal_evidence patch identity differs"
                )
            expected_comparison = {
                "isolated_treatment": True,
                "target_root_present_before": True,
                "target_root_absent_after": True,
                "baseline_signal_before": True,
                "baseline_signal_after": False,
            }
            for field, expected in expected_comparison.items():
                observed = raw_causal_evidence.get(field)
                _boolean(observed, f"{item_label}.causal_evidence.{field}")
                if observed is not expected:
                    raise BatchValidationError(
                        f"{item_label}.same_root causal comparison differs"
                    )
                causal_evidence[field] = observed
            expected_credit = "detected"
        else:
            if causal_evidence_value is not None:
                raise BatchValidationError(
                    f"{item_label}.{attribution} cannot carry causal evidence"
                )
            if is_detection and attribution != "different_or_unresolved":
                raise BatchValidationError(
                    f"{item_label} unattributed detection must be "
                    "different_or_unresolved"
                )
            if not is_detection and attribution != "not_applicable":
                raise BatchValidationError(
                    f"{item_label} non-detection must use not_applicable attribution"
                )
            expected_credit = (
                "missed"
                if (
                    adjudication_status == "confirmed"
                    and is_no_detection
                    and root_witness_reached
                )
                else "not_scored"
            )
        if not is_detection and not is_no_detection and root_witness_reached:
            raise BatchValidationError(
                f"{item_label}.{outcome} cannot reach a root witness"
            )
        credit = _nonblank_string(item.get("credit"), f"{item_label}.credit")
        if credit != expected_credit:
            raise BatchValidationError(
                f"{item_label}.credit must be {expected_credit}"
            )
        normalized[baseline_name] = {
            "outcome": outcome,
            "root_witness_reached": root_witness_reached,
            "outcome_evidence": outcome_evidence,
            "causal_attribution": attribution,
            "causal_evidence": causal_evidence,
            "credit": credit,
        }
    return normalized


def _validate_repair(
    value: Any,
    label: str,
    *,
    adjudication_status: str,
    causal_patch_sha256: str | None,
) -> dict[str, Any]:
    repair = _mapping(value, label)
    _exact_keys(repair, REPAIR_KEYS, label)
    status = repair.get("status")
    if status not in {
        "successful",
        "failed",
        "not_attempted",
        "not_applicable",
        "pending",
    }:
        raise BatchValidationError(f"{label}.status differs")
    evidence_value = repair.get("evidence")
    evidence: dict[str, Any] | None
    if status in {"successful", "failed"}:
        raw_evidence = _mapping(evidence_value, f"{label}.evidence")
        _exact_keys(raw_evidence, REPAIR_EVIDENCE_KEYS, f"{label}.evidence")
        evidence = {
            "artifact_sha256": _sha(
                raw_evidence.get("artifact_sha256"),
                f"{label}.evidence.artifact_sha256",
            ),
            "evidence_reference": _nonblank_string(
                raw_evidence.get("evidence_reference"),
                f"{label}.evidence.evidence_reference",
            ),
            "patch_sha256": _sha(
                raw_evidence.get("patch_sha256"),
                f"{label}.evidence.patch_sha256",
            ),
        }
        if evidence["patch_sha256"] != causal_patch_sha256:
            raise BatchValidationError(
                f"{label}.evidence patch identity differs from causal patch"
            )
        criteria = (
            "failing_before",
            "targeted_findings_absent_after",
            "no_new_findings",
            "reachability_preserved",
            "regression_passed",
            "reversion_restores_failure",
        )
        for field in criteria:
            field_value = raw_evidence.get(field)
            _boolean(field_value, f"{label}.evidence.{field}")
            evidence[field] = field_value
        all_satisfied = all(bool(evidence[field]) for field in criteria)
        if status == "successful" and not all_satisfied:
            raise BatchValidationError(
                f"{label} successful repair does not satisfy every criterion"
            )
        if status == "failed" and all_satisfied:
            raise BatchValidationError(
                f"{label} failed repair satisfies every success criterion"
            )
    else:
        if evidence_value is not None:
            raise BatchValidationError(
                f"{label}.{status} repair cannot carry evidence"
            )
        evidence = None
    if adjudication_status != "confirmed" and status == "successful":
        raise BatchValidationError(
            f"{label} cannot credit a repair to an unconfirmed root"
        )
    return {"status": status, "evidence": evidence}


def _validate_upstream(value: Any, label: str) -> dict[str, str | None]:
    upstream = _mapping(value, label)
    _exact_keys(upstream, UPSTREAM_KEYS, label)
    status = upstream.get("status")
    statuses_requiring_reference = {
        "reported",
        "acknowledged",
        "fixed",
        "rejected",
    }
    statuses_without_reference = {"pending", "not_contacted", "not_applicable"}
    if status not in statuses_requiring_reference | statuses_without_reference:
        raise BatchValidationError(f"{label}.status differs")
    reference = _nullable_nonblank_string(
        upstream.get("reference"),
        f"{label}.reference",
    )
    if status in statuses_requiring_reference and reference is None:
        raise BatchValidationError(f"{label}.{status} requires a reference")
    if status in statuses_without_reference and reference is not None:
        raise BatchValidationError(f"{label}.{status} cannot carry a reference")
    return {"status": str(status), "reference": reference}


def _validate_replay(
    value: Any,
    label: str,
    *,
    adjudication_status: str,
    artifact_status: str,
) -> dict[str, Any]:
    replay = _mapping(value, label)
    _exact_keys(replay, REPLAY_KEYS, label)
    status = replay.get("status")
    if status not in {"reproduced", "failed", "pending", "not_applicable"}:
        raise BatchValidationError(f"{label}.status differs")
    evidence_value = replay.get("evidence")
    if status in {"reproduced", "failed"}:
        evidence = _mapping(evidence_value, f"{label}.evidence")
        _exact_keys(evidence, REPLAY_EVIDENCE_KEYS, f"{label}.evidence")
        normalized_evidence = {
            "artifact_sha256": _sha(
                evidence.get("artifact_sha256"),
                f"{label}.evidence.artifact_sha256",
            ),
            "evidence_reference": _nonblank_string(
                evidence.get("evidence_reference"),
                f"{label}.evidence.evidence_reference",
            ),
        }
        for field in (
            "same_case_inputs",
            "finding_reproduced",
            "boundary_reproduced",
        ):
            value_field = evidence.get(field)
            _boolean(value_field, f"{label}.evidence.{field}")
            normalized_evidence[field] = bool(value_field)
        if status == "reproduced" and not all(
            normalized_evidence[field]
            for field in (
                "same_case_inputs",
                "finding_reproduced",
                "boundary_reproduced",
            )
        ):
            raise BatchValidationError(
                f"{label}.reproduced requires all replay criteria"
            )
        if status == "failed" and all(
            normalized_evidence[field]
            for field in (
                "same_case_inputs",
                "finding_reproduced",
                "boundary_reproduced",
            )
        ):
            raise BatchValidationError(
                f"{label}.failed must fail at least one replay criterion"
            )
    else:
        if evidence_value is not None:
            raise BatchValidationError(
                f"{label}.{status} cannot carry replay evidence"
            )
        normalized_evidence = None
    if adjudication_status == "confirmed" and status != "reproduced":
        raise BatchValidationError(
            f"{label} confirmed root requires a reproduced standalone replay"
        )
    if artifact_status == "complete" and status not in {"reproduced", "failed"}:
        raise BatchValidationError(
            f"{label} complete adjudication requires an attempted standalone "
            "replay with bound evidence"
        )
    return {"status": str(status), "evidence": normalized_evidence}


def _validate_root_record(
    value: Any,
    label: str,
    *,
    artifact_status: str,
    raw_batch_sha256: str,
    frozen_source_tree_sha256: str,
    prospective_case_ids: frozenset[str],
    prospective_witnesses: Mapping[tuple[str, int], Mapping[str, Any]],
    external_baseline_sha256: str | None,
    external_stock_outcomes: Mapping[str, str] | None,
) -> dict[str, Any]:
    root = _mapping(value, label)
    _exact_keys(root, ROOT_KEYS, label)
    root_id = _stable_id(root.get("root_id"), f"{label}.root_id")
    provenance = root.get("provenance")
    if provenance not in {"discovery", "prospective"}:
        raise BatchValidationError(f"{label}.provenance differs")
    family = _stable_id(root.get("family"), f"{label}.family")
    adjudication_status = root.get("adjudication_status")
    if adjudication_status not in {"confirmed", "rejected", "pending"}:
        raise BatchValidationError(f"{label}.adjudication_status differs")
    if artifact_status == "complete" and adjudication_status == "pending":
        raise BatchValidationError(
            "complete manual adjudication contains a pending root record"
        )
    first_witness = _validate_witness_reference(
        root.get("first_witness"),
        f"{label}.first_witness",
        provenance=str(provenance),
        adjudication_status=str(adjudication_status),
        raw_batch_sha256=raw_batch_sha256,
        prospective_case_ids=prospective_case_ids,
        prospective_witnesses=prospective_witnesses,
    )
    contract = _validate_contract(
        root.get("contract"),
        f"{label}.contract",
        adjudication_status=str(adjudication_status),
    )
    effect_summary = _nonblank_string(
        root.get("effect_summary"),
        f"{label}.effect_summary",
    )
    replay = _validate_replay(
        root.get("replay"),
        f"{label}.replay",
        adjudication_status=str(adjudication_status),
        artifact_status=artifact_status,
    )
    causal_patch = _validate_causal_patch(
        root.get("causal_patch"),
        f"{label}.causal_patch",
        adjudication_status=str(adjudication_status),
        provenance=str(provenance),
        frozen_source_tree_sha256=frozen_source_tree_sha256,
    )
    causal_patch_sha256 = (
        None if causal_patch is None else str(causal_patch["patch_sha256"])
    )
    prospective_baseline_outcomes = None
    if provenance == "prospective":
        prospective_witness_key = (
            str(first_witness["case_id"]),
            int(first_witness["boundary"]["selected_violation_index"]),
        )
        prospective_baseline_outcomes = _mapping(
            prospective_witnesses[prospective_witness_key]["baseline_outcomes"],
            f"{label}.prospective_baseline_outcomes",
        )
    baselines = _validate_root_baselines(
        root.get("baselines"),
        f"{label}.baselines",
        adjudication_status=str(adjudication_status),
        prospective_baseline_outcomes=prospective_baseline_outcomes,
        prospective_case_id=(
            str(first_witness["case_id"])
            if provenance == "prospective"
            else None
        ),
        raw_batch_sha256=raw_batch_sha256,
        causal_patch_sha256=causal_patch_sha256,
        external_baseline_sha256=external_baseline_sha256,
        external_stock_outcomes=external_stock_outcomes,
    )
    repair = _validate_repair(
        root.get("repair"),
        f"{label}.repair",
        adjudication_status=str(adjudication_status),
        causal_patch_sha256=causal_patch_sha256,
    )
    if (
        adjudication_status == "confirmed"
        and contract["claim_classification"] == "defect"
        and repair["status"] != "successful"
    ):
        raise BatchValidationError(
            f"{label} defect classification requires a successful isolated repair"
        )
    upstream = _validate_upstream(root.get("upstream"), f"{label}.upstream")
    if artifact_status == "complete" and (
        replay["status"] == "pending"
        or repair["status"] == "pending"
        or upstream["status"] == "pending"
    ):
        raise BatchValidationError(
            "complete manual adjudication contains pending "
            "replay/repair/upstream status"
        )
    return {
        "root_id": root_id,
        "provenance": provenance,
        "family": family,
        "adjudication_status": adjudication_status,
        "first_witness": first_witness,
        "contract": contract,
        "effect_summary": effect_summary,
        "replay": replay,
        "baselines": baselines,
        "causal_patch": causal_patch,
        "repair": repair,
        "upstream": upstream,
    }


def _validate_control_record(value: Any, label: str) -> dict[str, Any]:
    control = _mapping(value, label)
    _exact_keys(control, CONTROL_KEYS, label)
    control_id = _stable_id(control.get("control_id"), f"{label}.control_id")
    if control_id not in REQUIRED_CONTROL_IDS:
        raise BatchValidationError(f"{label}.control_id is not a frozen control")
    evidence_sha = _sha(
        control.get("evidence_artifact_sha256"),
        f"{label}.evidence_artifact_sha256",
    )
    outcome = control.get("outcome")
    if outcome not in {"pass", "fail"}:
        raise BatchValidationError(f"{label}.outcome differs")
    observed = _integer(
        control.get("observed_alarm_count"),
        f"{label}.observed_alarm_count",
    )
    unexplained = _integer(
        control.get("unexplained_alarm_count"),
        f"{label}.unexplained_alarm_count",
    )
    if unexplained > observed:
        raise BatchValidationError(f"{label} unexplained alarms exceed observed")
    if (outcome == "pass" and observed != 0) or (
        outcome == "fail" and observed == 0
    ):
        raise BatchValidationError(f"{label} outcome/alarm count differs")
    return {
        "control_id": control_id,
        "evidence_artifact_sha256": evidence_sha,
        "outcome": outcome,
        "observed_alarm_count": observed,
        "unexplained_alarm_count": unexplained,
    }


def _validate_finding_dispositions(
    value: Any,
    *,
    artifact_status: str,
    roots: Sequence[Mapping[str, Any]],
    prospective_finding_inventory: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    expected = {
        (str(item["case_id"]), int(item["violation_index"])): item
        for item in prospective_finding_inventory
    }
    if len(expected) != len(prospective_finding_inventory):
        raise BatchValidationError(
            "prospective finding inventory contains duplicate identifiers"
        )
    root_by_id = {str(root["root_id"]): root for root in roots}
    observed_ids: set[tuple[str, int]] = set()
    memberships: Counter[str] = Counter()
    disposition_counts: Counter[str] = Counter()
    normalized: list[dict[str, Any]] = []
    for index, raw_value in enumerate(
        _list(value, "manual finding_dispositions")
    ):
        label = f"manual finding_dispositions[{index}]"
        item = _mapping(raw_value, label)
        _exact_keys(item, FINDING_DISPOSITION_KEYS, label)
        case_id = _nonblank_string(item.get("case_id"), f"{label}.case_id")
        violation_index = _integer(
            item.get("violation_index"),
            f"{label}.violation_index",
        )
        identifier = (case_id, violation_index)
        if identifier in observed_ids:
            raise BatchValidationError(
                f"duplicate prospective finding disposition: {identifier}"
            )
        expected_item = expected.get(identifier)
        if expected_item is None:
            raise BatchValidationError(
                f"{label} references an unknown prospective finding"
            )
        observed_ids.add(identifier)
        finding_sha = _sha(
            item.get("finding_sha256"),
            f"{label}.finding_sha256",
        )
        if finding_sha != expected_item["finding_sha256"]:
            raise BatchValidationError(f"{label}.finding_sha256 differs")
        disposition = item.get("disposition")
        root_id_value = item.get("root_id")
        rejection_reason_value = item.get("rejection_reason")
        if disposition == "root":
            root_id = _stable_id(root_id_value, f"{label}.root_id")
            if rejection_reason_value is not None:
                raise BatchValidationError(
                    f"{label} root disposition cannot carry rejection_reason"
                )
            root = root_by_id.get(root_id)
            if (
                root is None
                or root["adjudication_status"] != "confirmed"
                or root["provenance"] != "prospective"
            ):
                raise BatchValidationError(
                    f"{label} must reference a confirmed prospective root"
                )
            rejection_reason = None
            memberships[root_id] += 1
        elif disposition == "rejected":
            if root_id_value is not None:
                raise BatchValidationError(
                    f"{label} rejected disposition cannot carry root_id"
                )
            root_id = None
            rejection_reason = _nonblank_string(
                rejection_reason_value,
                f"{label}.rejection_reason",
            )
        else:
            raise BatchValidationError(f"{label}.disposition differs")
        disposition_counts[str(disposition)] += 1
        normalized.append(
            {
                "case_id": case_id,
                "violation_index": violation_index,
                "finding_sha256": finding_sha,
                "disposition": disposition,
                "root_id": root_id,
                "rejection_reason": rejection_reason,
            }
        )

    if artifact_status == "complete" and observed_ids != set(expected):
        missing = sorted(set(expected) - observed_ids)
        raise BatchValidationError(
            f"complete adjudication omits prospective findings: {missing}"
        )
    if artifact_status == "complete":
        for root in roots:
            if (
                root["adjudication_status"] != "confirmed"
                or root["provenance"] != "prospective"
            ):
                continue
            root_id = str(root["root_id"])
            if memberships[root_id] == 0:
                raise BatchValidationError(
                    f"confirmed prospective root {root_id} has no finding membership"
                )
            witness = _mapping(root["first_witness"], "root first_witness")
            boundary = _mapping(witness["boundary"], "root witness boundary")
            first_identifier = (
                str(witness["case_id"]),
                int(boundary["selected_violation_index"]),
            )
            owner = next(
                (
                    item["root_id"]
                    for item in normalized
                    if (item["case_id"], item["violation_index"])
                    == first_identifier
                ),
                None,
            )
            if owner != root_id:
                raise BatchValidationError(
                    f"confirmed root {root_id} does not own its first witness finding"
                )
    return normalized, memberships, disposition_counts


def _validate_optional_measurements(value: Any) -> dict[str, int | None]:
    measurements = _mapping(value, "manual optional_measurements")
    _exact_keys(
        measurements,
        OPTIONAL_MEASUREMENT_KEYS,
        "manual optional_measurements",
    )
    normalized: dict[str, int | None] = {}
    for field in OPTIONAL_MANUAL_FIELDS:
        field_value = measurements[field]
        normalized[field] = (
            None
            if field_value is None
            else _integer(field_value, f"manual optional_measurements.{field}")
        )
    killed = normalized["held_out_mutants_killed"]
    total = normalized["held_out_mutants_total"]
    if (killed is None) != (total is None):
        raise BatchValidationError(
            "held-out mutant killed/total measurements must both be set or null"
        )
    if killed is not None and total is not None and killed > total:
        raise BatchValidationError("held-out mutants killed exceeds total")
    return normalized


def _structured_manual_adjudication(
    value: Mapping[str, Any],
    *,
    path: Path,
    source_sha256: str,
    raw_batch_sha256: str,
    frozen_source_tree_sha256: str,
    prospective_case_ids: frozenset[str],
    prospective_witnesses: Mapping[tuple[str, int], Mapping[str, Any]],
    prospective_finding_inventory: Sequence[Mapping[str, Any]],
    external_baseline_sha256: str | None,
    external_stock_outcomes: Mapping[str, str] | None,
) -> tuple[dict[str, Any], str]:
    _exact_keys(value, STRUCTURED_MANUAL_ARTIFACT_KEYS, "manual adjudication")
    if (
        _integer(value.get("schema_version"), "manual schema_version")
        != STRUCTURED_MANUAL_SCHEMA_VERSION
        or value.get("artifact_type") != "marlrefine_manual_adjudication"
        or value.get("raw_batch_sha256") != raw_batch_sha256
    ):
        raise BatchValidationError(
            "manual adjudication schema or batch identity differs"
        )
    status = value.get("status")
    if status not in {"pending", "complete"}:
        raise BatchValidationError(
            "manual adjudication status must be pending/complete"
        )
    roots = [
        _validate_root_record(
            root,
            f"manual roots[{index}]",
            artifact_status=str(status),
            raw_batch_sha256=raw_batch_sha256,
            frozen_source_tree_sha256=frozen_source_tree_sha256,
            prospective_case_ids=prospective_case_ids,
            prospective_witnesses=prospective_witnesses,
            external_baseline_sha256=external_baseline_sha256,
            external_stock_outcomes=external_stock_outcomes,
        )
        for index, root in enumerate(_list(value.get("roots"), "manual roots"))
    ]
    root_ids = [str(root["root_id"]) for root in roots]
    if len(root_ids) != len(set(root_ids)):
        raise BatchValidationError("manual adjudication root IDs are not unique")
    finding_dispositions, finding_memberships, finding_disposition_counts = (
        _validate_finding_dispositions(
            value.get("finding_dispositions"),
            artifact_status=str(status),
            roots=roots,
            prospective_finding_inventory=prospective_finding_inventory,
        )
    )
    controls = [
        _validate_control_record(control, f"manual controls[{index}]")
        for index, control in enumerate(
            _list(value.get("controls"), "manual controls")
        )
    ]
    control_ids = [str(control["control_id"]) for control in controls]
    if len(control_ids) != len(set(control_ids)):
        raise BatchValidationError("manual control IDs are not unique")
    if status == "complete" and frozenset(control_ids) != REQUIRED_CONTROL_IDS:
        raise BatchValidationError(
            "complete manual adjudication must include the three frozen controls"
        )
    optional = _validate_optional_measurements(value.get("optional_measurements"))
    if status == "complete" and optional["held_out_mutants_killed"] is None:
        raise BatchValidationError(
            "complete manual adjudication must include the sealed "
            "contract-derived mutant killed/total measurements"
        )

    confirmed = [
        root for root in roots if root["adjudication_status"] == "confirmed"
    ]
    discovery_confirmed = [
        root for root in confirmed if root["provenance"] == "discovery"
    ]
    prospective_confirmed = [
        root for root in confirmed if root["provenance"] == "prospective"
    ]
    adjudication_counts = Counter(str(root["adjudication_status"]) for root in roots)
    provenance_counts = Counter(str(root["provenance"]) for root in confirmed)
    family_counts = Counter(str(root["family"]) for root in confirmed)
    upstream_counts = Counter(str(root["upstream"]["status"]) for root in confirmed)
    repair_status_counts = Counter(
        str(root["repair"]["status"]) for root in confirmed
    )
    replay_status_counts = Counter(
        str(root["replay"]["status"]) for root in confirmed
    )
    baseline_credit_counts = {
        baseline_name: _counter_dict(
            Counter(
                str(root["baselines"][baseline_name]["credit"])
                for root in confirmed
            )
        )
        for baseline_name in (*BASELINE_NAMES, *EXTERNAL_BASELINE_NAMES)
    }
    observed_control_alarms = sum(
        int(control["observed_alarm_count"]) for control in controls
    )
    unexplained_control_alarms = sum(
        int(control["unexplained_alarm_count"]) for control in controls
    )
    values = _pending_manual_values()
    if status == "complete":
        repair_attempts = repair_status_counts["successful"] + (
            repair_status_counts["failed"]
        )
        repair_accounted = (
            repair_attempts
            + repair_status_counts["not_attempted"]
            + repair_status_counts["not_applicable"]
        )
        if repair_accounted != len(confirmed):
            raise BatchValidationError(
                "confirmed-root repair dispositions are not exhaustive"
            )
        values.update(
            {
                "confirmed_roots": len(confirmed),
                "discovery_confirmed_roots": len(discovery_confirmed),
                "prospective_confirmed_roots": len(prospective_confirmed),
                "root_families": len(family_counts),
                "macro_baseline_misses": sum(
                    root["baselines"]["macro_aggregate"]["credit"] == "missed"
                    for root in confirmed
                ),
                "api_baseline_misses": sum(
                    root["baselines"]["stock_api"]["credit"] == "missed"
                    for root in confirmed
                ),
                "repair_attempts": repair_attempts,
                "repair_successes": repair_status_counts["successful"],
                "repair_failures": repair_status_counts["failed"],
                "repair_non_attempts": repair_status_counts["not_attempted"],
                "repair_not_applicable": repair_status_counts[
                    "not_applicable"
                ],
                "control_alarms": unexplained_control_alarms,
                "upstream_confirmed_roots": sum(
                    root["upstream"]["status"] in UPSTREAM_CONFIRMED_STATUSES
                    for root in confirmed
                ),
            }
        )
    values.update(optional)
    manual_sha256 = source_sha256
    return (
        {
            "schema_version": STRUCTURED_MANUAL_SCHEMA_VERSION,
            "status": status,
            "source": {"path_name": path.name, "sha256": manual_sha256},
            "roots": roots,
            "finding_dispositions": finding_dispositions,
            "controls": controls,
            "optional_measurements": optional,
            "derived_breakdown": {
                "root_record_count": len(roots),
                "adjudication_status_counts": _counter_dict(adjudication_counts),
                "confirmed_root_provenance_counts": _counter_dict(
                    provenance_counts
                ),
                "confirmed_root_family_counts": _counter_dict(family_counts),
                "confirmed_root_upstream_status_counts": _counter_dict(
                    upstream_counts
                ),
                "confirmed_root_repair_status_counts": {
                    repair_status: repair_status_counts.get(repair_status, 0)
                    for repair_status in (
                        "successful",
                        "failed",
                        "not_attempted",
                        "not_applicable",
                        "pending",
                    )
                },
                "confirmed_root_replay_status_counts": {
                    replay_status: replay_status_counts.get(replay_status, 0)
                    for replay_status in (
                        "reproduced",
                        "failed",
                        "pending",
                        "not_applicable",
                    )
                },
                "confirmed_root_baseline_credit_counts": baseline_credit_counts,
                "prospective_finding_disposition_counts": _counter_dict(
                    finding_disposition_counts
                ),
                "confirmed_prospective_root_finding_counts": _counter_dict(
                    finding_memberships
                ),
                "confirmed_root_macro_boundary_miss_count": sum(
                    root["baselines"]["macro_boundary"]["credit"] == "missed"
                    for root in confirmed
                ),
                "confirmed_root_macro_aggregate_miss_count": sum(
                    root["baselines"]["macro_aggregate"]["credit"] == "missed"
                    for root in confirmed
                ),
                "control_record_count": len(controls),
                "observed_control_alarm_count": observed_control_alarms,
                "unexplained_control_alarm_count": unexplained_control_alarms,
            },
            "values": values,
            "rule": (
                "root, baseline, repair, control, and upstream counts are derived "
                "from validated structured records; the mandatory sealed-mutation "
                "measurement and optional peak-memory measurement remain explicit "
                "batch-bound scalars; schema "
                "and hash validation do not establish substantive truth, so the "
                "underlying adjudication evidence remains manually reviewable"
            ),
        },
        manual_sha256,
    )


def _legacy_pending_manual_adjudication(
    value: Mapping[str, Any],
    *,
    path: Path,
    source_sha256: str,
    raw_batch_sha256: str,
) -> tuple[dict[str, Any], str]:
    _exact_keys(value, LEGACY_MANUAL_ARTIFACT_KEYS, "manual adjudication")
    if (
        _integer(value.get("schema_version"), "manual schema_version") != 1
        or value.get("artifact_type") != "marlrefine_manual_adjudication"
        or value.get("raw_batch_sha256") != raw_batch_sha256
    ):
        raise BatchValidationError(
            "manual adjudication schema or batch identity differs"
        )
    if value.get("status") != "pending":
        raise BatchValidationError(
            "complete root claims require structured manual schema_version 5"
        )
    raw_values = _mapping(value.get("values"), "manual adjudication values")
    if set(raw_values) != set(LEGACY_MANUAL_FIELDS):
        raise BatchValidationError("manual adjudication fields differ")
    if any(raw_values[field] is not None for field in LEGACY_MANUAL_FIELDS):
        raise BatchValidationError(
            "legacy pending manual adjudication values must all be null"
        )
    manual_sha256 = source_sha256
    return (
        {
            "schema_version": 1,
            "status": "pending",
            "source": {"path_name": path.name, "sha256": manual_sha256},
            "roots": [],
            "finding_dispositions": [],
            "controls": [],
            "optional_measurements": {
                field: None for field in OPTIONAL_MANUAL_FIELDS
            },
            "derived_breakdown": None,
            "values": _pending_manual_values(),
            "rule": (
                "legacy schema_version 1 is accepted only as an all-null pending "
                "artifact; complete claims require structured schema_version 5"
            ),
        },
        manual_sha256,
    )


def _manual_adjudication(
    path: Path | None,
    *,
    raw_batch_sha256: str,
    frozen_source_tree_sha256: str,
    prospective_case_ids: frozenset[str],
    prospective_witnesses: Mapping[tuple[str, int], Mapping[str, Any]],
    prospective_finding_inventory: Sequence[Mapping[str, Any]],
    external_baseline_sha256: str | None,
    external_stock_outcomes: Mapping[str, str] | None,
) -> tuple[dict[str, Any], str | None]:
    if path is None:
        return (
            {
                "schema_version": STRUCTURED_MANUAL_SCHEMA_VERSION,
                "status": "pending",
                "source": None,
                "roots": [],
                "finding_dispositions": [],
                "controls": [],
                "optional_measurements": {
                    field: None for field in OPTIONAL_MANUAL_FIELDS
                },
                "derived_breakdown": None,
                "values": _pending_manual_values(),
                "rule": (
                    "root clustering, baseline/repair credit, controls, and upstream "
                    "confirmation require a structured batch-bound adjudication"
                ),
            },
            None,
        )
    value, source_sha256 = _read_json_object_with_sha256(
        path,
        "manual adjudication",
    )
    schema_version = _integer(value.get("schema_version"), "manual schema_version")
    if schema_version == 1:
        return _legacy_pending_manual_adjudication(
            value,
            path=path,
            source_sha256=source_sha256,
            raw_batch_sha256=raw_batch_sha256,
        )
    return _structured_manual_adjudication(
        value,
        path=path,
        source_sha256=source_sha256,
        raw_batch_sha256=raw_batch_sha256,
        frozen_source_tree_sha256=frozen_source_tree_sha256,
        prospective_case_ids=prospective_case_ids,
        prospective_witnesses=prospective_witnesses,
        prospective_finding_inventory=prospective_finding_inventory,
        external_baseline_sha256=external_baseline_sha256,
        external_stock_outcomes=external_stock_outcomes,
    )


def analyze_prospective_batch(
    batch_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    *,
    manual_adjudication_path: Path | None = None,
    external_baseline_path: Path | None = None,
    mutation_batch_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and analyze exactly 105 games x 8 traces without execution.

    Preliminary analysis may omit secondary evidence. A complete manual
    adjudication requires both exact, identity-bound secondary artifacts.
    """
    (
        manifest,
        receipt,
        game_names,
        manifest_sha256,
        receipt_sha256,
    ) = _validate_manifest_and_receipt(
        manifest_path,
        receipt_path,
    )
    raw_batch_digest = hashlib.sha256()
    records = iter(_iter_canonical_jsonl(batch_path, digest=raw_batch_digest))
    try:
        header = next(records)
    except StopIteration as exc:
        raise BatchValidationError("sealed batch header is missing") from exc
    identities = _validate_header(
        header,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        receipt=receipt,
        receipt_sha256=receipt_sha256,
    )
    frozen_source_git_revision = _frozen_source_git_revision(manifest)
    resumed_batch = header["resume_infrastructure_from_sha256"] is not None
    counts: Counter[str] = Counter()

    def validated_cases() -> Iterator[
        tuple[Mapping[str, Any], Mapping[str, Any] | None]
    ]:
        for ordinal in range(EXPECTED_TRACE_COUNT):
            try:
                record = next(records)
            except StopIteration as exc:
                raise BatchValidationError(
                    "sealed batch ended before all 840 cases"
                ) from exc
            game_index, policy_index = divmod(ordinal, len(TRACE_POLICIES))
            run = _validate_case(
                record,
                ordinal=ordinal,
                game_name=game_names[game_index],
                policy_index=policy_index,
                identities=identities,
                resumed_batch=resumed_batch,
            )
            counts[str(record["status"])] += 1
            yield record, run
        try:
            footer = next(records)
        except StopIteration as exc:
            raise BatchValidationError("sealed batch footer is missing") from exc
        _validate_footer(
            footer,
            identities=identities,
            observed_counts=counts,
            resumed_batch=resumed_batch,
        )
        try:
            next(records)
        except StopIteration:
            pass
        else:
            raise BatchValidationError("sealed batch has records after its footer")

    aggregate = _aggregate(
        validated_cases(),
        game_names,
        _manifest_game_strata(manifest, game_names),
    )
    raw_batch_sha256 = raw_batch_digest.hexdigest()
    if sum(counts.values()) != EXPECTED_TRACE_COUNT:
        raise BatchValidationError("classifier counts do not sum to 840")
    prospective_case_ids = frozenset(
        f"{game_name}::{policy.name}"
        for game_name in game_names
        for policy in TRACE_POLICIES
    )
    prospective_witnesses = {
        (str(item["case_id"]), int(item["violation_index"])): item
        for item in aggregate["witness_localization"]["witnesses"]
    }
    batch_runtime = _mapping(header["runtime"], "batch execution runtime")
    external_baselines = (
        None
        if external_baseline_path is None
        else _validate_external_baseline_artifact(
            external_baseline_path,
            manifest_sha256=manifest_sha256,
            receipt_sha256=receipt_sha256,
            identities=identities,
            archive_identifier=str(header["archive_identifier"]),
            archive_published_at_utc=str(header["archive_published_at_utc"]),
            batch_runtime=batch_runtime,
            game_names=game_names,
        )
    )
    mutation_evaluation = (
        None
        if mutation_batch_path is None
        else _validate_mutation_batch_artifact(
            mutation_batch_path,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            receipt_sha256=receipt_sha256,
            identities=identities,
            archive_identifier=str(header["archive_identifier"]),
            archive_published_at_utc=str(header["archive_published_at_utc"]),
            batch_runtime=batch_runtime,
        )
    )
    manual, manual_sha256 = _manual_adjudication(
        manual_adjudication_path,
        raw_batch_sha256=raw_batch_sha256,
        frozen_source_tree_sha256=identities["source_tree_sha256"],
        prospective_case_ids=prospective_case_ids,
        prospective_witnesses=prospective_witnesses,
        prospective_finding_inventory=aggregate["violations"][
            "prospective_finding_inventory"
        ],
        external_baseline_sha256=(
            None
            if external_baselines is None
            else str(external_baselines["source"]["sha256"])
        ),
        external_stock_outcomes=(
            None
            if external_baselines is None
            else _mapping(
                external_baselines["stock_pettingzoo_api_test"][
                    "outcomes_by_game"
                ],
                "validated external stock outcomes",
            )
        ),
    )
    if manual["status"] == "complete":
        if external_baselines is None or mutation_evaluation is None:
            raise BatchValidationError(
                "complete manual adjudication requires exact external-baseline "
                "and mutation-batch artifacts"
            )
        manual_values = _mapping(manual["values"], "manual adjudication values")
        mutation_overall = _mapping(
            mutation_evaluation["overall"],
            "validated mutation totals",
        )
        mutation_validation = _mapping(
            mutation_evaluation["validation"],
            "validated mutation report",
        )
        if mutation_validation.get("rq6_reportable") is not True:
            raise BatchValidationError(
                "complete manual adjudication requires an RQ6-reportable "
                "mutation attempt ledger and named progress controls"
            )
        if (
            manual_values["held_out_mutants_killed"]
            != mutation_overall["semantic_kills"]
            or manual_values["held_out_mutants_total"]
            != mutation_overall["selected_total"]
        ):
            raise BatchValidationError(
                "manual mutation measurements differ from the bound mutation batch"
            )
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "artifact_type": "marlrefine_frozen_prospective_analysis",
        "analysis_id": ANALYSIS_ID,
        "input_identities": {
            "raw_batch": {"filename": batch_path.name, "sha256": raw_batch_sha256},
            "manifest": {
                "filename": manifest_path.name,
                "sha256": identities["manifest_sha256"],
            },
            "archive_receipt": {
                "filename": receipt_path.name,
                "sha256": receipt_sha256,
            },
            "manual_adjudication_sha256": manual_sha256,
            "external_baselines": (
                None if external_baselines is None else external_baselines["source"]
            ),
            "mutation_batch": (
                None
                if mutation_evaluation is None
                else mutation_evaluation["source"]
            ),
            "source_tree_sha256": identities["source_tree_sha256"],
            "frozen_source_git_revision": frozen_source_git_revision,
            "uv_lock_sha256": identities["uv_lock_sha256"],
            "classifier_id": CLASSIFIER_ID,
            "obligation_ledger_schema_id": OBLIGATION_LEDGER_SCHEMA_ID,
            "archive_identifier": header["archive_identifier"],
        },
        "design": {
            "distinct_prospective_game_types": EXPECTED_SEMANTIC_COHORT_SIZE,
            "policies_per_game": len(TRACE_POLICIES),
            "scheduled_trace_cases": EXPECTED_TRACE_COUNT,
            "semantic_game_names_sha256": hashlib.sha256(
                "\n".join(game_names).encode("utf-8")
            ).hexdigest(),
        },
        "runtime": dict(_mapping(header["runtime"], "batch header runtime")),
        **aggregate,
        "external_baselines": external_baselines,
        "mutation_evaluation": mutation_evaluation,
        "manual_adjudication": manual,
    }


def _fraction_value(value: Mapping[str, Any] | None) -> float | None:
    if value is None:
        return None
    return int(value["numerator"]) / int(value["denominator"])


def _manual_macro(value: int | None, label: str) -> str:
    return str(value) if value is not None else rf"\ResultPending{{manual {label}}}"


_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _latex_escape(value: Any) -> str:
    """Escape untrusted adjudication text for a LaTeX text-mode cell."""
    compact = " ".join(str(value).split())
    return "".join(_LATEX_ESCAPES.get(character, character) for character in compact)


def _confirmed_root_table_rows(adjudication: Mapping[str, Any]) -> str:
    """Render deterministic table rows from validated confirmed root records."""
    if adjudication.get("status") != "complete":
        return (
            r"\multicolumn{4}{c}{\ResultPending{root rows from batch-bound "
            r"adjudication}} \\"
        )
    if adjudication.get("schema_version") != STRUCTURED_MANUAL_SCHEMA_VERSION:
        raise BatchValidationError(
            "complete root table requires structured manual schema_version 5"
        )
    roots = sorted(
        (
            _mapping(root, "manual adjudication root")
            for root in _list(adjudication.get("roots"), "manual roots")
            if _mapping(root, "manual adjudication root").get(
                "adjudication_status"
            )
            == "confirmed"
        ),
        key=lambda root: str(root["root_id"]),
    )
    if not roots:
        return (
            r"\multicolumn{4}{c}{No confirmed roots in the completed "
            r"adjudication.} \\"
        )

    baseline_labels = (
        ("strict_lockstep", "SL"),
        ("macro_boundary", "MB"),
        ("macro_aggregate", "MA"),
        ("endpoint", "EP"),
        ("return_only", "RO"),
        ("stock_api", "API"),
    )
    rows: list[str] = []
    for root in roots:
        witness = _mapping(root["first_witness"], "root first_witness")
        boundary = _mapping(witness["boundary"], "root witness boundary")
        baselines = _mapping(root["baselines"], "root baselines")
        baseline_parts: list[str] = []
        for baseline_name, short_label in baseline_labels:
            baseline = _mapping(baselines[baseline_name], baseline_name)
            reachability = (
                "reached" if baseline["root_witness_reached"] else "not-reached"
            )
            baseline_parts.append(
                f"{short_label} ["
                f"{_latex_escape(baseline['outcome'])}; "
                f"{_latex_escape(baseline['credit'])}; "
                f"{_latex_escape(baseline['causal_attribution'])}; "
                f"{reachability}]"
            )
        segment_index = boundary["segment_index"]
        boundary_text = (
            f"seg={segment_index if segment_index is not None else 'none'}, "
            f"src={boundary['source_event_stop']}, "
            f"dst={boundary['destination_event_stop']}, "
            f"v={boundary['selected_violation_index']}"
        )
        contract = _mapping(root["contract"], "root contract")
        causal_patch = _mapping(root["causal_patch"], "root causal_patch")
        repair = _mapping(root["repair"], "root repair")
        replay = _mapping(root["replay"], "root replay")
        upstream = _mapping(root["upstream"], "root upstream")
        root_cell = (
            rf"\texttt{{{_latex_escape(root['root_id'])}}} / "
            rf"\texttt{{{_latex_escape(root['family'])}}}"
        )
        provenance_cell = (
            f"{_latex_escape(root['provenance'])}; "
            rf"\texttt{{{_latex_escape(witness['case_id'])}}}; "
            f"{_latex_escape(boundary_text)}; "
            f"{_latex_escape(root['effect_summary'])}"
        )
        baseline_cell = "; ".join(baseline_parts)
        disposition_cell = (
            f"replay {_latex_escape(replay['status'])}; "
            f"repair {_latex_escape(repair['status'])}; "
            rf"patch \texttt{{"
            f"{_latex_escape(str(causal_patch['patch_sha256'])[:12])}"
            r"}; "
            f"claim {_latex_escape(contract['claim_classification'])}; "
            f"upstream {_latex_escape(upstream['status'])}"
        )
        rows.append(
            f"{root_cell} & {provenance_cell} & {baseline_cell} & "
            f"{disposition_cell} \\\\"
        )
    return "\n".join(rows)


def _confirmed_localization_table_rows(
    analysis: Mapping[str, Any],
    adjudication: Mapping[str, Any],
) -> str:
    """Render confirmed-root phase and prefix evidence."""
    if adjudication.get("status") != "complete":
        return (
            r"\multicolumn{5}{c}{\ResultPending{root localization rows from "
            r"batch-bound adjudication}} \\"
        )
    witness_items = _mapping(
        analysis.get("witness_localization"),
        "analysis witness localization",
    ).get("witnesses")
    witnesses_by_finding = {
        (str(item["case_id"]), int(item["violation_index"])): _mapping(
            item, "localized witness item"
        )
        for item in _list(witness_items, "localized witnesses")
    }
    roots = sorted(
        (
            _mapping(root, "manual adjudication root")
            for root in _list(adjudication.get("roots"), "manual roots")
            if _mapping(root, "manual adjudication root").get(
                "adjudication_status"
            )
            == "confirmed"
        ),
        key=lambda root: str(root["root_id"]),
    )
    if not roots:
        return r"\multicolumn{5}{c}{No confirmed roots.} \\"

    resolution_labels = {
        "destination_call_when_schedule_is_one_to_one": "call",
        "advancing_commit_boundary": "commit",
        "aligned_transition_block": "block",
        "trace_endpoint": "endpoint",
        "episode_return_only": "return",
    }
    rows: list[str] = []
    for root in roots:
        witness_reference = _mapping(root["first_witness"], "root witness")
        witness_boundary = _mapping(
            witness_reference["boundary"], "root witness boundary"
        )
        localized_item = witnesses_by_finding.get(
            (
                str(witness_reference["case_id"]),
                int(witness_boundary["selected_violation_index"]),
            )
        )
        if localized_item is None:
            phase_call = "external discovery witness"
            prefix = "external"
            checker = "external"
            resolutions = "external"
        else:
            witness = _mapping(localized_item["witness"], "localized witness")
            specificity = _mapping(
                witness["diagnostic_specificity"],
                "diagnostic specificity",
            )
            call_index = specificity["exact_destination_call_index"]
            phase_call = (
                f"{specificity['destination_phase']}; "
                f"call {call_index if call_index is not None else 'n/a'}"
            )
            original_lengths = _mapping(
                witness["original_lengths"],
                "localized original lengths",
            )
            prefix = (
                f"{specificity['destination_prefix_call_count']}/"
                f"{original_lengths['destination_events']} calls"
            )
            checker = (
                f"{specificity['obligation_family']} / "
                f"{specificity['violation_code']}"
            )
            detected_resolutions = _mapping(
                localized_item["case_signal_resolution_ceiling"],
                "case signal resolution ceiling",
            )
            root_baselines = _mapping(root["baselines"], "root baselines")
            parts = [
                f"{name}={resolution_labels.get(str(resolution), str(resolution))}"
                for name, resolution in detected_resolutions.items()
                if resolution is not None
                and _mapping(root_baselines[name], f"root baseline {name}").get(
                    "credit"
                )
                == "detected"
            ]
            resolutions = ", ".join(parts) if parts else "none detected"
        replay = _mapping(root["replay"], "root replay")
        rows.append(
            rf"\texttt{{{_latex_escape(root['root_id'])}}} & "
            f"{_latex_escape(phase_call)} & {_latex_escape(prefix)} & "
            f"{_latex_escape(checker)} & "
            f"replay {_latex_escape(replay['status'])}; "
            f"ablations {_latex_escape(resolutions)} \\\\"
        )
    return "\n".join(rows)


def _prospective_violation_table_rows(analysis: Mapping[str, Any]) -> str:
    """Render deterministic finding-incidence rows without inventing passes."""
    violations = _mapping(analysis.get("violations"), "analysis violations")
    by_obligation = _mapping(
        violations.get("by_obligation_and_code"),
        "analysis violations by_obligation_and_code",
    )
    rows: list[str] = []
    for obligation in sorted(by_obligation):
        by_code = _mapping(
            by_obligation[obligation],
            f"analysis violations for {obligation}",
        )
        for code in sorted(by_code):
            counts = _mapping(by_code[code], f"analysis violation {obligation}/{code}")
            rows.append(
                f"{_latex_escape(obligation)} & "
                rf"\texttt{{{_latex_escape(code)}}} & "
                f"{int(counts['occurrence_count'])} & "
                f"{int(counts['trace_count'])} & "
                f"{int(counts['distinct_game_count'])} \\\\"
            )
    if not rows:
        return r"\multicolumn{5}{c}{No prospective violation observed.} \\"
    return "\n".join(rows)


def _prospective_obligation_coverage_rows(analysis: Mapping[str, Any]) -> str:
    """Render the frozen O1--O8 trace outcomes and exercised-site totals."""
    coverage = _mapping(
        analysis.get("obligation_evaluation_coverage"),
        "analysis obligation_evaluation_coverage",
    )
    by_obligation = _mapping(
        coverage.get("by_obligation"),
        "analysis obligation coverage by_obligation",
    )
    rows: list[str] = []
    for obligation_id in OBLIGATION_IDS:
        item = _mapping(
            by_obligation.get(obligation_id),
            f"analysis obligation coverage {obligation_id}",
        )
        outcomes = _mapping(
            item.get("trace_outcomes"),
            f"analysis obligation outcomes {obligation_id}",
        )
        rows.append(
            f"{obligation_id} & {int(outcomes['evaluated_pass'])} & "
            f"{int(outcomes['evaluated_fail'])} & "
            f"{int(outcomes['not_applicable'])} & "
            f"{int(outcomes['not_evaluated'])} & "
            f"{int(item['evaluation_count'])} \\\\"
        )
    return "\n".join(rows)


def _prospective_coverage_table_rows(analysis: Mapping[str, Any]) -> str:
    """Render compact structural, action, stratum, and tolerance coverage."""
    paths = _mapping(
        analysis.get("execution_path_coverage"),
        "analysis execution_path_coverage",
    )
    strata = _mapping(
        paths.get("status_by_registry_stratum"),
        "analysis status_by_registry_stratum",
    )
    stratum_parts = [
        f"{name}: {sum(int(value) for value in _mapping(counts, name).values())}"
        for name, counts in sorted(strata.items())
    ]

    origin_kinds = _mapping(
        paths.get("source_event_origin_node_kinds"),
        "analysis source_event_origin_node_kinds",
    )
    kind_parts = [
        f"{kind}: {int(_mapping(counts, kind)['occurrence_count'])}"
        for kind, counts in sorted(origin_kinds.items())
    ]
    structure = _mapping(
        paths.get("observed_structure"),
        "analysis observed_structure",
    )
    phase_parts = []
    for label, key in (
        ("buffer", "destination_buffer_calls"),
        ("commit", "destination_commit_calls"),
        ("other stutter", "destination_other_stutter_calls"),
        ("cleanup", "destination_cleanup_calls"),
    ):
        counts = _mapping(structure.get(key), f"analysis structure {key}")
        phase_parts.append(f"{label}: {int(counts['occurrence_count'])}")

    action_coverage = _mapping(
        paths.get("action_coverage"),
        "analysis action_coverage",
    )
    saturation = action_coverage.get("cumulative_by_policy_order")
    if not isinstance(saturation, list) or not saturation:
        raise BatchValidationError("analysis policy saturation is empty")
    final_saturation = _mapping(saturation[-1], "analysis final policy saturation")
    ratio_value = final_saturation.get("selected_to_offered_ratio")
    ratio = (
        None
        if ratio_value is None
        else _mapping(ratio_value, "analysis final selected_to_offered_ratio")
    )
    marginal_parts = [
        str(
            int(
                _mapping(item, "analysis policy saturation item")[
                    "marginal_new_selected_state_player_actions"
                ]
            )
        )
        for item in saturation
    ]

    tolerance = _mapping(
        analysis.get("tolerance_sensitivity"),
        "analysis tolerance_sensitivity",
    )

    def tolerance_text(key: str) -> str:
        item = _mapping(tolerance.get(key), f"analysis tolerance {key}")
        total = int(item["comparable_site_count"])
        return (
            f"0.1x={int(item['within_one_tenth_primary'])}, "
            f"1x={int(item['within_primary'])}, "
            f"10x={int(item['within_ten_times_primary'])} of {total}"
        )

    row_end = r" \\"
    return "\n".join(
        (
            "Registry strata & scheduled traces & "
            + _latex_escape("; ".join(stratum_parts))
            + row_end,
            "Source event origins & atomic events by native pre-event kind & "
            + _latex_escape("; ".join(kind_parts) or "none")
            + row_end,
            "Destination phases & calls retained by phase & "
            + _latex_escape("; ".join(phase_parts))
            + row_end,
            "Action coverage & unique selected/offered visited-state tuples & "
            + (
                "not observed; "
                if ratio is None
                else f"{int(ratio['numerator'])}/{int(ratio['denominator'])}; "
            )
            + "marginal gains by policy: "
            + _latex_escape(", ".join(marginal_parts))
            + row_end,
            "Tolerance sensitivity & sites within 0.1x/1x/10x threshold & "
            + f"reward {_latex_escape(tolerance_text('aligned_reward_values'))}; "
            + "observation "
            + _latex_escape(tolerance_text("floating_observation_values"))
            + row_end,
        )
    )


def _mutation_family_table_rows(analysis: Mapping[str, Any]) -> str:
    mutation = analysis.get("mutation_evaluation")
    if mutation is None:
        return (
            r"\multicolumn{6}{c}{\ResultPending{validated mutation-family rows}} \\"
        )
    rows: list[str] = []
    for raw_row in _list(
        _mapping(mutation, "analysis mutation evaluation").get("by_family"),
        "analysis mutation family rows",
    ):
        row = _mapping(raw_row, "analysis mutation family row")
        rows.append(
            " & ".join(
                (
                    rf"\texttt{{{_latex_escape(row['family'])}}}",
                    str(row["selected"]),
                    str(row["semantic_kills"]),
                    str(row["crash_only_kills"]),
                    str(row["survived"]),
                    str(row["selected_clean_reference_alarms"]),
                )
            )
            + r" \\"
        )
    return "\n".join(rows)


def _mutation_counter_rows(
    analysis: Mapping[str, Any],
    field: str,
    pending_label: str,
) -> str:
    mutation = analysis.get("mutation_evaluation")
    if mutation is None:
        return rf"\multicolumn{{2}}{{c}}{{\ResultPending{{{pending_label}}}}} \\"
    counts = _mapping(
        _mapping(mutation, "analysis mutation evaluation").get(field),
        f"analysis mutation {field}",
    )
    if not counts:
        return r"\multicolumn{2}{c}{none observed} \\"
    return "\n".join(
        rf"\texttt{{{_latex_escape(name)}}} & {count} \\"
        for name, count in sorted(counts.items())
    )


def _mutation_paired_comparator_rows(analysis: Mapping[str, Any]) -> str:
    mutation = analysis.get("mutation_evaluation")
    if mutation is None:
        return (
            r"\multicolumn{2}{c}{\ResultPending{paired mutation comparator rows}} \\"
        )
    paired = _mapping(
        _mapping(mutation, "analysis mutation evaluation").get(
            "paired_comparator_signal_counts"
        ),
        "analysis mutation paired comparator counts",
    )
    project = _mapping(
        paired.get("project_baselines"),
        "analysis mutation project-baseline counts",
    )
    rows = [
        rf"\texttt{{{_latex_escape(name)}}} & {project[name]} \\"
        for name in BASELINE_NAMES
    ]
    rows.append(
        r"\texttt{stock\_pettingzoo\_api\_test} & "
        + str(paired["stock_pettingzoo_api_test"])
        + r" \\"
    )
    return "\n".join(rows)


def latex_result_macros(analysis: Mapping[str, Any]) -> str:
    """Render deterministic LaTeX macros, preserving manual pending states."""
    traces = analysis["trace_level_accounting"]["counts"]
    two_axis = analysis["two_axis_trace_accounting"]
    semantic_evidence = two_axis["semantic_evidence"]
    execution_completeness = two_axis["execution_completeness"]
    games = analysis["game_level_accounting"]
    overlapping = games["overlapping_flags"]["counts"]
    exclusive = games["exclusive_reporting_buckets"]["counts"]
    elapsed = analysis["cost"]["elapsed_ns"]
    ledger = analysis["cost"]["ledger_event_count"]
    ratios = analysis["witness_localization"]["prefix_to_original_event_ratio"]
    adjudication = _mapping(
        analysis["manual_adjudication"],
        "analysis manual_adjudication",
    )
    manual = _mapping(adjudication["values"], "manual adjudication values")
    archive_identifier = analysis["input_identities"]["archive_identifier"]
    preregistration_text = (
        "not preregistered (local Git freeze)"
        if str(archive_identifier).startswith("local-unregistered:")
        else rf"\url{{https://doi.org/{archive_identifier}}}"
    )
    completion = analysis["execution_path_coverage"]["completion_by_status"]
    terminal_complete = sum(completion["terminal_complete"].values())
    bounded_prefix_passes = completion["bounded_prefix"][
        OutcomeStatus.PASS.value
    ]

    median_elapsed = _fraction_value(elapsed["median"])
    median_ledger = _fraction_value(ledger["median"])
    median_ratio = _fraction_value(ratios["median"])
    total_seconds = int(elapsed["total"]) / 1_000_000_000
    runtime = (
        rf"{median_elapsed / 1_000_000_000:.6g}~s"
        if median_elapsed is not None
        else r"\ResultPending{median runtime}"
    )
    ledger_text = (
        f"{median_ledger:.6g} events"
        if median_ledger is not None
        else r"\ResultPending{median ledger size}"
    )
    ratio_text = (
        f"{median_ratio:.6g}"
        if median_ratio is not None
        else r"\ResultPending{no divergent witness prefixes}"
    )
    mutation_value = analysis.get("mutation_evaluation")
    mutation = (
        None
        if mutation_value is None
        else _mapping(mutation_value, "analysis mutation evaluation")
    )
    if mutation is None:
        mutation_overall = None
        mutation_validation = None
        mutation_clean_alarms = None
        mutation_controls = None
        mutation_paired = None
        mutation_killed = None
        mutation_total = None
        mutation_score = r"\ResultPending{validated sealed mutation evaluation}"
    else:
        mutation_overall = _mapping(
            mutation.get("overall"),
            "analysis mutation overall",
        )
        mutation_validation = _mapping(
            mutation.get("validation"),
            "analysis mutation validation",
        )
        mutation_clean_alarms = _mapping(
            mutation.get("clean_reference_alarms"),
            "analysis mutation clean-reference alarms",
        )
        mutation_controls = _mapping(
            mutation.get("progress_instrumentation_controls"),
            "analysis mutation progress controls",
        )
        mutation_paired = _mapping(
            mutation.get("paired_comparator_signal_counts"),
            "analysis mutation paired comparator counts",
        )
        mutation_killed = int(mutation_overall["semantic_kills"])
        mutation_total = int(mutation_overall["selected_total"])
        mutation_score = (
            "not applicable (no evaluable sealed mutants)"
            if mutation_total == 0
            else f"{mutation_killed / mutation_total:.3f}"
        )

    external_value = analysis.get("external_baselines")
    external = (
        None
        if external_value is None
        else _mapping(external_value, "analysis external baselines")
    )
    if external is None:
        external_stock_passes: int | str = (
            r"\ResultPending{external stock-API passes}"
        )
        external_stock_failures: int | str = (
            r"\ResultPending{external stock-API failures}"
        )
        external_suite_status = r"\ResultPending{released Shimmy suite status}"
        external_runtime = r"\ResultPending{external-baseline runtime}"
    else:
        external_stock = _mapping(
            external.get("stock_pettingzoo_api_test"),
            "analysis external stock API panel",
        )
        external_stock_counts = _mapping(
            external_stock.get("status_counts"),
            "analysis external stock API status counts",
        )
        external_stock_passes = int(external_stock_counts.get("pass", 0))
        external_stock_failures = int(external_stock_counts.get("fail", 0))
        external_suite = _mapping(
            external.get("released_shimmy_openspiel_suite"),
            "analysis external Shimmy suite",
        )
        external_suite_status = rf"\texttt{{{_latex_escape(external_suite['status'])}}}"
        external_runtime = (
            f"{int(external['elapsed_ns']) / 1_000_000_000:.6g}~s"
        )

    mutation_runtime = (
        r"\ResultPending{mutation-batch runtime}"
        if mutation is None
        else f"{int(mutation['elapsed_ns']) / 1_000_000_000:.6g}~s"
    )

    input_identities = _mapping(
        analysis["input_identities"], "analysis input_identities"
    )
    revision = input_identities.get("frozen_source_git_revision")
    if not isinstance(revision, str) or not GIT_REVISION_PATTERN.fullmatch(
        revision
    ):
        raise BatchValidationError(
            "analysis frozen source Git revision is not a full lowercase SHA"
        )
    raw_batch_identity = _mapping(
        input_identities.get("raw_batch"), "analysis raw-batch identity"
    )
    raw_batch_revision = _sha(
        raw_batch_identity.get("sha256"), "analysis raw-batch SHA-256"
    )
    assert raw_batch_revision is not None
    revision_macro = rf"\texttt{{{revision}}}"
    raw_batch_revision_macro = rf"\texttt{{{raw_batch_revision}}}"

    def evidence_revision(identity_name: str, label: str) -> str:
        value = input_identities.get(identity_name)
        if value is None:
            return rf"\ResultPending{{{label}}}"
        identity = _mapping(value, f"analysis {identity_name} identity")
        digest = _sha(identity.get("sha256"), f"analysis {identity_name} SHA-256")
        return rf"\texttt{{{digest}}}"

    external_revision_macro = evidence_revision(
        "external_baselines",
        "external-baseline artifact revision",
    )
    mutation_revision_macro = evidence_revision(
        "mutation_batch",
        "mutation-batch artifact revision",
    )

    def validation_flag_text(
        flag: str,
        reasons: str,
        pending_label: str,
    ) -> str:
        if mutation_validation is None:
            return rf"\ResultPending{{{pending_label}}}"
        if mutation_validation[flag]:
            return "yes"
        reason_text = ", ".join(mutation_validation[reasons]) or "unspecified"
        return "no (" + _latex_escape(reason_text) + ")"

    def validation_boolean_flag_text(flag: str, pending_label: str) -> str:
        if mutation_validation is None:
            return rf"\ResultPending{{{pending_label}}}"
        return "yes" if mutation_validation[flag] else "no"

    def macro(name: str, value: Any) -> str:
        return rf"\newcommand{{\{name}}}{{{value}}}"

    lines = [
        "% Generated by marlrefine frozen analysis; do not hand-edit.",
        "% Trace counts use 840 game-policy traces; game counts use 105 names.",
        (
            r"\newcommand{\ResultPending}[1]{\textcolor{PendingRed}"
            r"{\textsf{[PENDING: #1]}}}"
        ),
        macro("ProspectiveGameCount", games["population"]),
        macro("ProspectiveTraceCount", traces["scheduled"]),
        macro("ProspectiveTraceCompleted", traces["semantically_completed"]),
        macro("ProspectiveTracePasses", traces["pass"]),
        macro("ProspectiveTerminalCompleteTraces", terminal_complete),
        macro("ProspectiveBoundedPrefixPasses", bounded_prefix_passes),
        macro("ProspectiveTraceFailures", traces["fail"]),
        macro(
            "ProspectiveSemanticEvidenceFailures",
            semantic_evidence["observed_failure"],
        ),
        macro(
            "ProspectiveSemanticNoFailure",
            semantic_evidence["no_observed_failure"],
        ),
        macro("ProspectiveSemanticNoVerdict", semantic_evidence["no_verdict"]),
        macro(
            "ProspectiveExecutionSemanticAborts",
            execution_completeness["semantic_abort"],
        ),
        macro("ProspectiveTraceInapplicable", traces["inapplicable"]),
        macro("ProspectiveTraceUnalignable", traces["unalignable"]),
        macro(
            "ProspectiveTraceInfrastructureFailures",
            traces["infrastructure"],
        ),
        "% Backward-compatible paper macros below are explicitly game-level.",
        macro(
            "ProspectiveCompleted",
            overlapping["games_semantically_complete_all_eight_traces"],
        ),
        macro(
            "ProspectiveInapplicable",
            overlapping["games_with_any_inapplicable"],
        ),
        macro(
            "ProspectiveUnalignable",
            overlapping["games_with_any_unalignable"],
        ),
        macro(
            "ProspectiveInfrastructureFailures",
            overlapping["games_with_any_infrastructure"],
        ),
        macro(
            "ProspectiveCasesWithViolation", overlapping["games_with_any_fail"]
        ),
        macro(
            "ProspectiveCasesWithoutViolation",
            overlapping["games_all_eight_no_observed_violation"],
        ),
        macro(
            "ProspectiveGamesExclusiveInfrastructure",
            exclusive["infrastructure_present"],
        ),
        macro(
            "ProspectiveGamesExclusiveUnalignable",
            exclusive["unalignable_present_no_infrastructure"],
        ),
        macro(
            "ProspectiveGamesExclusiveInapplicable",
            exclusive[
                "inapplicable_present_no_infrastructure_or_unalignable"
            ],
        ),
        macro(
            "ProspectiveGamesExclusiveViolation",
            exclusive["violation_present_all_traces_semantically_completed"],
        ),
        macro(
            "ProspectiveGamesExclusiveAllPass",
            exclusive["all_traces_no_observed_violation"],
        ),
        macro(
            "ConfirmedRootsTotal",
            _manual_macro(manual["confirmed_roots"], "total confirmed roots"),
        ),
        macro(
            "DiscoveryConfirmedRoots",
            _manual_macro(
                manual["discovery_confirmed_roots"],
                "discovery confirmed roots",
            ),
        ),
        macro(
            "ProspectiveConfirmedRoots",
            _manual_macro(
                manual["prospective_confirmed_roots"],
                "prospective confirmed roots",
            ),
        ),
        macro(
            "ConfirmedRootFamilies",
            _manual_macro(manual["root_families"], "root families"),
        ),
        macro(
            "ConfirmedMacroBaselineMisses",
            _manual_macro(
                manual["macro_baseline_misses"], "macro-baseline root misses"
            ),
        ),
        macro(
            "ConfirmedAPIBaselineMisses",
            _manual_macro(
                manual["api_baseline_misses"], "API-baseline root misses"
            ),
        ),
        macro(
            "ConfirmedRepairAttempts",
            _manual_macro(manual["repair_attempts"], "repair attempts"),
        ),
        macro(
            "ConfirmedRepairSuccesses",
            _manual_macro(manual["repair_successes"], "repair successes"),
        ),
        macro(
            "ConfirmedRepairFailures",
            _manual_macro(manual["repair_failures"], "repair failures"),
        ),
        macro(
            "ConfirmedRepairNonAttempts",
            _manual_macro(manual["repair_non_attempts"], "repair non-attempts"),
        ),
        macro(
            "ConfirmedRepairNotApplicable",
            _manual_macro(
                manual["repair_not_applicable"],
                "repairs not applicable",
            ),
        ),
        macro(
            "ConfirmedControlAlarms",
            _manual_macro(manual["control_alarms"], "control alarms"),
        ),
        macro(
            "ConfirmedRootTableRows",
            _confirmed_root_table_rows(adjudication),
        ),
        macro(
            "ConfirmedLocalizationTableRows",
            _confirmed_localization_table_rows(analysis, adjudication),
        ),
        macro(
            "ProspectiveViolationTableRows",
            _prospective_violation_table_rows(analysis),
        ),
        macro(
            "ProspectiveObligationCoverageRows",
            _prospective_obligation_coverage_rows(analysis),
        ),
        macro(
            "ProspectiveCoverageTableRows",
            _prospective_coverage_table_rows(analysis),
        ),
        macro("ExternalStockAPIPasses", external_stock_passes),
        macro("ExternalStockAPIFailures", external_stock_failures),
        macro("ExternalShimmySuiteStatus", external_suite_status),
        macro(
            "MutationAttemptedCandidates",
            (
                mutation_overall["attempted_candidates"]
                if mutation_overall is not None
                else r"\ResultPending{attempted mutation candidates}"
            ),
        ),
        macro(
            "MutationSelectedTotal",
            (
                mutation_total
                if mutation_total is not None
                else r"\ResultPending{selected mutation denominator}"
            ),
        ),
        macro(
            "MutationSemanticKills",
            (
                mutation_killed
                if mutation_killed is not None
                else r"\ResultPending{semantic mutation kills}"
            ),
        ),
        macro(
            "MutationCrashOnlyKills",
            (
                mutation_overall["crash_only_kills"]
                if mutation_overall is not None
                else r"\ResultPending{crash-only mutation detections}"
            ),
        ),
        macro(
            "MutationSurvivors",
            (
                mutation_overall["survived"]
                if mutation_overall is not None
                else r"\ResultPending{surviving selected mutants}"
            ),
        ),
        macro(
            "MutationSelectedCleanReferenceAlarms",
            (
                mutation_clean_alarms["selected_alarm_count"]
                if mutation_clean_alarms is not None
                else r"\ResultPending{selected clean-reference alarms}"
            ),
        ),
        macro(
            "MutationPoolCleanReferenceAlarms",
            (
                mutation_clean_alarms["attempted_pool_alarm_count"]
                if mutation_clean_alarms is not None
                else r"\ResultPending{pool clean-reference alarms}"
            ),
        ),
        macro(
            "MutationProgressControlsDetected",
            (
                mutation_controls["detected_count"]
                if mutation_controls is not None
                else r"\ResultPending{detected progress controls}"
            ),
        ),
        macro(
            "MutationProgressControlsRequired",
            (
                mutation_controls["required_count"]
                if mutation_controls is not None
                else r"\ResultPending{required progress controls}"
            ),
        ),
        macro(
            "MutationStockAPIPairedSignals",
            (
                mutation_paired["stock_pettingzoo_api_test"]
                if mutation_paired is not None
                else r"\ResultPending{paired stock-API mutation signals}"
            ),
        ),
        macro("MutationFamilyTableRows", _mutation_family_table_rows(analysis)),
        macro(
            "MutationPairedComparatorRows",
            _mutation_paired_comparator_rows(analysis),
        ),
        macro(
            "MutationFirstObligationRows",
            _mutation_counter_rows(
                analysis,
                "first_detection_by_obligation",
                "mutation first-obligation rows",
            ),
        ),
        macro(
            "MutationFirstPhaseRows",
            _mutation_counter_rows(
                analysis,
                "first_detection_by_phase",
                "mutation first-phase rows",
            ),
        ),
        macro(
            "MutationReplacementReasonRows",
            _mutation_counter_rows(
                analysis,
                "replacement_reason_counts",
                "mutation replacement-reason rows",
            ),
        ),
        macro(
            "MutationCohortComplete",
            validation_boolean_flag_text(
                "cohort_complete_24",
                "24-mutant cohort completeness",
            ),
        ),
        macro(
            "MutationProgressControlsSatisfied",
            validation_boolean_flag_text(
                "progress_controls_satisfied",
                "named progress-control validation",
            ),
        ),
        macro(
            "MutationRQSixReportable",
            validation_flag_text(
                "rq6_reportable",
                "rq6_reporting_reasons",
                "RQ6 reporting readiness",
            ),
        ),
        macro(
            "MutationStrongPerformanceThresholdMet",
            validation_flag_text(
                "strong_performance_threshold_met",
                "strong_performance_threshold_reasons",
                "strong mutation-performance threshold",
            ),
        ),
        macro(
            "MutationSensitivityClaimReady",
            validation_flag_text(
                "strong_sensitivity_claim_ready",
                "strong_sensitivity_claim_reasons",
                "strong mutation-sensitivity claim readiness",
            ),
        ),
        macro("MutationReportingComplete", r"\MutationRQSixReportable"),
        macro(
            "MutationStrongSensitivityThresholdMet",
            r"\MutationStrongPerformanceThresholdMet",
        ),
        macro(
            "HeldOutMutantsKilled",
            (
                mutation_killed
                if mutation_killed is not None
                else r"\ResultPending{sealed contract-derived semantic kills}"
            ),
        ),
        macro(
            "HeldOutMutantsTotal",
            (
                mutation_total
                if mutation_total is not None
                else r"\ResultPending{sealed contract-derived mutant total}"
            ),
        ),
        macro("HeldOutMutationScore", mutation_score),
        macro("MedianRuntime", runtime),
        macro("TotalRuntime", f"{total_seconds:.6g}~s"),
        macro("ExternalBaselineRuntime", external_runtime),
        macro("MutationRuntime", mutation_runtime),
        macro("MutationPeakMemory", "not measured (not instrumented)"),
        macro(
            "PeakMemory",
            (
                "not measured (not instrumented)"
                if adjudication["status"] == "complete"
                and manual["peak_memory_bytes"] is None
                else _manual_macro(
                    manual["peak_memory_bytes"],
                    "peak memory bytes",
                )
            ),
        ),
        macro("MedianLedgerSize", ledger_text),
        macro(
            "MedianReductionRatio",
            "not applicable (no minimizer)",
        ),
        macro("MedianWitnessPrefixRatio", ratio_text),
        macro(
            "PreregistrationURL",
            preregistration_text,
        ),
        macro("FrozenSourceRevision", revision_macro),
        macro("RawBatchRevision", raw_batch_revision_macro),
        macro("ExternalBaselineRevision", external_revision_macro),
        macro("MutationBatchRevision", mutation_revision_macro),
        macro(
            "ReviewerPackageURL",
            r"\ResultPending{private reviewer-package URL}",
        ),
        macro(
            "ReviewerPackageRevision",
            r"\ResultPending{sealed reviewer-package revision}",
        ),
        # Honest compatibility aliases: these remain pending until packaging.
        macro("ArtifactURL", r"\ReviewerPackageURL"),
        macro("ArtifactRevision", r"\ReviewerPackageRevision"),
        # Backward-compatible source-revision alias for older templates.
        macro("RepositoryRevision", revision_macro),
        "",
    ]
    return "\n".join(lines)


def write_analysis_artifacts(
    batch_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    output_path: Path,
    latex_output_path: Path,
    *,
    manual_adjudication_path: Path | None = None,
    external_baseline_path: Path | None = None,
    mutation_batch_path: Path | None = None,
) -> dict[str, Any]:
    """Validate inputs and the live analyzer, then atomically write artifacts."""
    if output_path.resolve() == latex_output_path.resolve():
        raise ValueError("JSON and LaTeX analysis outputs must use different paths")
    if output_path.exists() or latex_output_path.exists():
        raise FileExistsError("refusing to overwrite a frozen analysis artifact")
    analysis = analyze_prospective_batch(
        batch_path,
        manifest_path,
        receipt_path,
        manual_adjudication_path=manual_adjudication_path,
        external_baseline_path=external_baseline_path,
        mutation_batch_path=mutation_batch_path,
    )
    batch_runtime = _mapping(analysis["runtime"], "batch execution runtime")
    analysis_runtime = runtime_provenance()
    for field in (
        "source_tree_sha256",
        "uv_lock_sha256",
        "source_identity_scope",
        "packages",
        "installed_distribution_sha256",
        "git_revision",
        "git_dirty",
    ):
        if analysis_runtime.get(field) != batch_runtime.get(field):
            raise BatchValidationError(
                f"analysis runtime {field} differs from batch execution runtime"
            )
    for field in ("implementation", "version", "executable_name"):
        if _mapping(
            analysis_runtime.get("python"), "analysis runtime Python"
        ).get(field) != _mapping(
            batch_runtime.get("python"), "batch runtime Python"
        ).get(field):
            raise BatchValidationError(
                f"analysis runtime Python {field} differs from batch execution runtime"
            )
    for field in ("system", "machine"):
        if _mapping(
            analysis_runtime.get("platform"), "analysis runtime platform"
        ).get(field) != _mapping(
            batch_runtime.get("platform"), "batch runtime platform"
        ).get(field):
            raise BatchValidationError(
                "analysis runtime platform "
                f"{field} differs from batch execution runtime"
            )
    analysis["analysis_runtime"] = analysis_runtime
    write_json(output_path, analysis)
    latex_output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=latex_output_path.parent,
            prefix=f".{latex_output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(latex_result_macros(analysis))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, latex_output_path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise
    return analysis
