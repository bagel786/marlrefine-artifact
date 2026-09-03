"""Archive-gated execution and scoring of the sealed mutation cohort."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from marlrefine.adapters.openspiel_shimmy import (
    CHANCE_POLICY_ID,
    PROTOCOL_VERSION,
    TraceRun,
    run_trace,
)
from marlrefine.baselines import BASELINE_NAMES
from marlrefine.evaluation import validate_serialized_obligation_evaluations
from marlrefine.mutations import (
    CANDIDATE_POOL,
    MUTANTS_PER_FAMILY,
    MUTATION_ENGINE_ID,
    MUTATION_FAMILIES,
    MUTATION_MAX_DESTINATION_CALLS,
    MUTATION_PROTOCOL_ID,
    MUTATION_SELECTION_SEED,
    POOL_PER_FAMILY,
    PROGRESS_INSTRUMENTATION_CONTROLS,
    MutationCandidate,
    adapter_class_for,
    candidate_manifest_records,
    mutation_engine_source_sha256,
    paired_reference_class_for,
    progress_transform_for,
)
from marlrefine.policies import POLICY_ENGINE_ID, get_trace_policy
from marlrefine.prospective import build_prospective_plan, classify_run_payload
from marlrefine.provenance import runtime_provenance
from marlrefine.repairs import CombinedRepairV0
from marlrefine.serialization import to_jsonable, write_json
from marlrefine.stock_tests import StockApiResult, run_stock_api_test

MUTATION_MANIFEST_SCHEMA_VERSION = 1
MUTATION_BATCH_SCHEMA_VERSION = 3
MUTATION_MANIFEST_PATH = "manifests/mutation_v1.json"
MUTATION_BATCH_ARTIFACT_TYPE = "marlrefine_sealed_mutation_batch"
MUTATION_STOCK_API_CYCLES = 100
PROGRESS_CONTROL_REQUIRED_FINDING = (
    "monotone_progress_and_completeness",
    "progress_instrumentation_inconsistent",
)
MUTATION_REPLACEMENT_RULE = (
    "within each family select the first four candidates in frozen priority "
    "order whose clean reference is acceptable, whose hook fires, whose "
    "adapter-facing execution differs, and whose paired behavior-delta hash "
    "is distinct from prior selected candidates in that family; MARLRefine "
    "findings, baseline findings, and stock api_test outcomes are ignored by "
    "selection"
)

_MUTATION_BATCH_KEYS = frozenset(
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
    }
)
_MUTATION_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "manifest_status",
        "protocol_id",
        "environment",
        "selection",
        "reference_adapter",
        "execution",
        "scoring",
        "mutation_engine",
        "candidates",
        "progress_instrumentation_controls",
        "prearchive_activity",
    }
)
_CANDIDATE_RECORD_KEYS = frozenset(
    {
        "candidate",
        "selection_inputs",
        "mutation_evidence",
        "reference",
        "mutant",
        "kill",
        "ablation_signals",
        "selected",
        "replacement_attempt",
    }
)
_SELECTION_INPUT_KEYS = frozenset(
    {
        "reference_acceptable",
        "hook_reached",
        "adapter_behavior_changed",
        "behavior_delta_sha256",
        "base_eligible_before_uniqueness",
        "unique_behavior_among_prior_selected",
        "duplicate_behavior_of_candidate_id",
        "eligible",
    }
)
_MUTATION_EVIDENCE_KEYS = frozenset(
    {"candidate_id", "operator", "trigger_count", "trigger_contexts"}
)
_TRACE_RUN_KEYS = frozenset(
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
_REFERENCE_RESULT_KEYS = frozenset(
    {
        "status",
        "finding_codes",
        "finding_signatures",
        "clean_reference_alarm",
        "adapter_projection_sha256",
        "elapsed_ns",
        "run",
    }
)
_MUTANT_RESULT_KEYS = frozenset(
    {
        "status",
        "finding_codes",
        "finding_signatures",
        "new_full_finding_signatures",
        "new_semantic_finding_signatures",
        "new_execution_finding_signatures",
        "adapter_projection_sha256",
        "elapsed_ns",
        "run",
    }
)
_CLEAN_REFERENCE_ALARM_KEYS = frozenset(
    {
        "semantic_finding_count",
        "execution_finding_count",
        "semantic_alarm",
        "unexpected_execution_alarm",
        "accepted_typed_unsupported_capability",
        "any_unexpected_alarm",
    }
)
_REPLACEMENT_ATTEMPT_KEYS = frozenset({"attempted", "reason_codes"})
_KILL_KEYS = frozenset(
    {
        "new_full_signature_count",
        "new_semantic_signature_count",
        "new_execution_signature_count",
        "killed",
        "semantic_kill",
        "crash_only_kill",
        "status",
        "first_detecting_signature_sha256",
        "first_detecting_obligation",
        "first_detecting_phase",
        "first_detection_order",
    }
)
_ABLATION_SIGNAL_KEYS = frozenset(
    {"project_baselines", "stock_pettingzoo_api_test"}
)
_BASELINE_SIGNAL_KEYS = frozenset(
    {
        "reference_applicable",
        "mutant_applicable",
        "reference_finding_signatures",
        "mutant_finding_signatures",
        "added_finding_signatures",
        "paired_signal",
    }
)
_BASELINE_RESULT_KEYS = frozenset(
    {"baseline", "applicable", "findings", "reason"}
)
_STOCK_SIGNAL_KEYS = frozenset(
    {
        "reference_adapter_class",
        "mutant_adapter_class",
        "reference",
        "mutant",
        "paired_signal",
        "interpretation",
    }
)
_STOCK_RESULT_KEYS = frozenset(
    {"game_spec", "cycles", "passed", "exception", "warnings", "captured_output"}
)
_VIOLATION_KEYS = frozenset(
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
_ALIGNMENT_KEYS = frozenset(
    {"source_events", "destination_events", "segments", "initial_progress"}
)
_SOURCE_EVENT_KEYS = frozenset(
    {"progress", "rewards", "terminated", "truncated", "metadata"}
)
_DESTINATION_EVENT_KEYS = frozenset(
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
_SEGMENT_KEYS = frozenset(
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
_SPAN_KEYS = frozenset({"start", "stop"})
_PROGRESS_INSTRUMENTATION_KEYS = frozenset(
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
_PROGRESS_ANNOTATION_METHOD_ID = "independent_native_replay_event_count_v1"
_SUMMARY_REQUIRED_KEYS = frozenset(
    {
        "protocol_version",
        "trace_policy_name",
        "trace_policy_id",
        "trace_policy_engine_id",
        "trace_policy_seed",
        "chance_policy_id",
        "progress_annotation_method_id",
        "requested_seed",
        "requested_max_destination_calls",
        "requested_max_source_decisions",
        "progress_annotation_control_id",
        "setup_status",
        "adapter_class",
        "stop_reason",
        "destination_calls",
        "source_transitions",
        "source_decisions",
        "source_terminal",
        "adapter_agents_remaining",
        "chance_event_count",
    }
)
_SUMMARY_ALLOWED_KEYS = _SUMMARY_REQUIRED_KEYS | frozenset(
    {
        "canonical_game_identity_sha256",
        "trace_policy_rng_namespace_sha256",
        "caller_supplied_nondefault_configuration",
        "source_dynamics",
        "source_chance_mode",
        "source_num_players",
        "declared_max_game_length",
        "effective_max_source_decisions",
        "unsupported_capability",
        "source_parameters",
        "adapter_parameters",
        "reason",
        "reset_history",
        "reset_chance_transcript",
        "post_reset_source_state_digest",
        "post_reset_source_state_digest_method",
        "post_reset_adapter_state_digest",
        "post_reset_adapter_state_digest_method",
        "post_reset_state_oracle_strength",
        "adapter_game_length_at_reset",
        "adapter_game_length_final",
        "adapter_decision_clock_elapsed_final",
        "decision_clock_mismatch_reported",
        "final_source_state_digest",
        "final_source_state_digest_method",
        "chance_tape_sha256",
        "source_node_kind",
        "source_return",
        "destination_instantaneous_reward_sum",
        "destination_delivered_reward_sum",
        "delivery_comparison_count",
        "delivery_mismatch_count",
        "violation_count",
    }
)

_PRIMARY_FINDING_CODES: dict[str, frozenset[str]] = {
    "stutter_reward_neutrality": frozenset({"nonzero_stutter_reward"}),
    "segment_reward_conservation": frozenset(
        {
            "reward_channel_unavailable",
            "reward_dimension_mismatch",
            "segment_reward_mismatch",
        }
    ),
    "monotone_progress_and_completeness": frozenset(
        {
            "source_progress_not_strictly_monotone",
            "source_progress_gap",
            "progress_instrumentation_inconsistent",
            "destination_progress_regression",
            "destination_progress_beyond_source",
            "destination_progress_incomplete",
            "destination_advance_without_source",
            "segment_source_progress_out_of_bounds",
            "destination_commit_without_source_boundary",
            "unmatched_source_events",
            "unmatched_destination_events",
        }
    ),
    "terminal_cleanup_reward_neutrality": frozenset(
        {"nonzero_terminal_cleanup_reward"}
    ),
    "boundary_lifecycle_preservation": frozenset(
        {"boundary_lifecycle_mismatch"}
    ),
    "configuration_provenance": frozenset(
        {"parameters_changed_on_reset", "player_count_changed_on_reset"}
    ),
    "interface_projection": frozenset(
        {
            "agent_identity_mismatch",
            "selected_agent_identity_mismatch",
            "agent_index_mapping_mismatch",
            "acting_player_mismatch",
            "action_space_malformed",
            "action_space_size_mismatch",
            "action_mask_missing",
            "action_mask_malformed",
            "legal_action_mismatch",
            "no_legal_action_for_live_agent",
            "observation_mismatch",
            "duplicate_simultaneous_player",
        }
    ),
    "state_projection": frozenset(
        {
            "reset_state_mismatch",
            "instrumentation_history_not_prefix_monotone",
            "submitted_action_mismatch",
            "instrumentation_replay_failed",
            "aligned_state_mismatch",
            "unalignable_chance",
        }
    ),
    "lifecycle_preservation": frozenset(
        {
            "pre_cleanup_lifecycle_mismatch",
            "adapter_requests_action_at_nondecision_node",
            "terminality_mismatch",
            "unfinished_joint_action_buffer",
            "agents_exhausted_before_source_terminal",
        }
    ),
    "decision_clock_preservation": frozenset(
        {"source_decision_clock_mismatch", "premature_adapter_truncation"}
    ),
    "state_kind_soundness": frozenset(
        {"mean_field_protocol_missing", "mean_field_node_silently_terminated"}
    ),
    "delivered_reward_conservation": frozenset(
        {"consumer_delivery_mismatch", "consumer_return_mismatch"}
    ),
    "trace_execution": frozenset(
        {
            "source_setup_failed",
            "adapter_setup_failed",
            "unalignable_chance",
            "adapter_step_failed",
            "destination_call_budget_exhausted",
        }
    ),
}
_BASELINE_FINDING_CODES: dict[str, frozenset[str]] = {
    "strict_lockstep": frozenset(
        {
            "lockstep_reward_mismatch",
            "lockstep_lifecycle_mismatch",
            "lockstep_state_mismatch",
        }
    ),
    "macro_boundary": frozenset(
        {
            "boundary_reward_mismatch",
            "boundary_lifecycle_mismatch",
            "boundary_state_mismatch",
        }
    ),
    "macro_aggregate": frozenset(
        {
            "aggregate_boundary_reward_mismatch",
            "aggregate_boundary_lifecycle_mismatch",
            "aggregate_boundary_state_mismatch",
        }
    ),
    "endpoint": frozenset({"endpoint_lifecycle_mismatch", "endpoint_state_mismatch"}),
    "return_only": frozenset({"final_return_mismatch"}),
}
_SELECTION_KEYS = frozenset(
    {
        "rule",
        "selected_ids_by_family",
        "selected_count_by_family",
        "incomplete_families",
        "complete",
    }
)
_SCORE_KEYS = frozenset(
    {
        "selected_total",
        "selected_count_by_family",
        "killed_total",
        "semantic_killed_total",
        "crash_only_killed_total",
        "killed_by_family",
        "semantic_killed_by_family",
        "crash_only_killed_by_family",
        "paired_ablation_signal_counts",
        "clean_reference_alarms",
        "first_detection_counts",
        "replacement_reason_counts",
        "interpretation",
    }
)
_CONTROL_PANEL_KEYS = frozenset(
    {"included_in_24_mutant_denominator", "required_count", "detected_count", "records"}
)
_CONTROL_RECORD_KEYS = frozenset(
    {
        "control",
        "reference",
        "corrupted",
        "new_full_finding_signatures",
        "detected",
        "included_in_24_mutant_denominator",
    }
)
_ADAPTER_PROJECTION_SUMMARY_FIELDS = (
    "setup_status",
    "stop_reason",
    "adapter_parameters",
    "adapter_game_length_at_reset",
    "adapter_game_length_final",
    "adapter_decision_clock_elapsed_final",
    "adapter_agents_remaining",
)
_MUTATION_SCORE_INTERPRETATION = (
    "synthetic sensitivity to the sealed mutation model; not a real defect "
    "count or prevalence estimate"
)


class MutationGateError(RuntimeError):
    """The mutation cohort is not identical to the public pre-run freeze."""


class MutationBatchValidationError(ValueError):
    """A mutation batch is internally inconsistent or not paper-ready."""


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        to_jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_mutation_manifest(
    *,
    manifest_status: str,
    source_git_revision: str | None = None,
) -> dict[str, Any]:
    """Build the candidate pool without importing a game implementation."""
    if manifest_status not in {
        "draft_not_timestamp_archived",
        "frozen_pending_archive",
    }:
        raise ValueError(f"unsupported mutation manifest status {manifest_status!r}")
    if manifest_status == "frozen_pending_archive" and not source_git_revision:
        raise ValueError("frozen mutation manifests require source commit A")
    environment = runtime_provenance()
    if source_git_revision is not None:
        environment["git_revision"] = source_git_revision
        environment["git_dirty"] = False
    return {
        "schema_version": MUTATION_MANIFEST_SCHEMA_VERSION,
        "artifact_type": "marlrefine_sealed_mutation_manifest",
        "manifest_status": manifest_status,
        "protocol_id": MUTATION_PROTOCOL_ID,
        "environment": environment,
        "selection": {
            "selection_seed": MUTATION_SELECTION_SEED,
            "families": list(MUTATION_FAMILIES),
            "required_eligible_per_family": MUTANTS_PER_FAMILY,
            "candidate_pool_per_family": POOL_PER_FAMILY,
            "required_total": len(MUTATION_FAMILIES) * MUTANTS_PER_FAMILY,
            "candidate_pool_total": len(CANDIDATE_POOL),
            "replacement_rule": MUTATION_REPLACEMENT_RULE,
        },
        "reference_adapter": {
            "base_class": "marlrefine.repairs.CombinedRepairV0",
            "factory": "marlrefine.mutations.paired_reference_class_for",
            "role": "candidate_bound_paired_composite_repaired_reference",
            "mean_field_acceptance": (
                "typed explicit unsupported rejection is an acceptable clean "
                "reference outcome for mean-field bypass candidates"
            ),
        },
        "execution": {
            "max_destination_calls": MUTATION_MAX_DESTINATION_CALLS,
            "one_reference_and_one_mutant_trace_per_candidate": True,
            "execution_gate": (
                "same_verified_public_receipt_or_local_unregistered_authorization_"
                "as_primary_study"
            ),
            "outcome_blinding": (
                "eligibility and replacement use reference acceptability, hook "
                "reachability, adapter-facing difference, and behavior-delta "
                "uniqueness only"
            ),
            "stock_api_cycles_per_paired_treatment": MUTATION_STOCK_API_CYCLES,
        },
        "scoring": {
            "finding_signature": (
                "sha256 of canonical JSON for the complete Violation record, "
                "including obligation, code, message, location, expected, and "
                "observed fields"
            ),
            "kill_definition": (
                "a selected mutant is killed only when its run contains at "
                "least one full finding signature absent from its paired clean "
                "reference run"
            ),
            "semantic_kill": (
                "at least one new full signature whose obligation is not "
                "trace_execution"
            ),
            "crash_only_kill": (
                "one or more new trace_execution signatures and no new semantic "
                "signature"
            ),
            "paired_ablation_rule": (
                "report only findings introduced relative to the paired clean "
                "reference; stock api_test signals only on reference-pass to "
                "mutant-fail"
            ),
            "progress_controls": (
                "report separately and exclude both controls from every "
                "24-mutant denominator"
            ),
            "first_detection": (
                "report the first new full signature in stable mutant-finding "
                "order with its obligation and deterministic detector phase"
            ),
            "replacement_accounting": (
                "retain a reason code for every attempted candidate, including "
                "ineligibility, duplicate behavior, and exhausted family quota"
            ),
        },
        "mutation_engine": {
            "engine_id": MUTATION_ENGINE_ID,
            "source_module": "src/marlrefine/mutations.py",
            "source_sha256": mutation_engine_source_sha256(),
        },
        "candidates": list(candidate_manifest_records()),
        "progress_instrumentation_controls": [
            control.to_manifest_record()
            for control in PROGRESS_INSTRUMENTATION_CONTROLS
        ],
        "prearchive_activity": {
            "permitted": (
                "declarative candidate/control definitions, operator source, "
                "canonical patches, and deterministic hashes only"
            ),
            "forbidden_until_execution_authorization": (
                "game construction, trace execution, eligibility evaluation, "
                "replacement selection, checker scoring, and baseline scoring"
            ),
            "candidate_or_control_outcomes_executed": 0,
        },
    }


def _read_object_with_sha256(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], str]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MutationGateError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise MutationGateError(f"non-finite JSON number in {label}: {value}")

    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MutationGateError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise MutationGateError(f"{label} must contain one object")
    return value, hashlib.sha256(raw).hexdigest()


def _verify_mutation_manifest_with_sha256(
    mutation_manifest_path: Path,
    study_manifest: Mapping[str, Any],
    *,
    source_tree_sha256: str,
) -> tuple[dict[str, Any], str]:
    """Verify the separately generated manifest bound by the study manifest."""
    payload, observed_hash = _read_object_with_sha256(
        mutation_manifest_path,
        "mutation manifest",
    )
    expected_hash = study_manifest.get("mutation_evaluation", {}).get(
        "mutation_manifest_sha256"
    )
    if expected_hash != observed_hash:
        raise MutationGateError("study manifest does not bind mutation manifest bytes")
    if frozenset(payload) != _MUTATION_MANIFEST_KEYS:
        raise MutationGateError("mutation manifest keys differ")
    if (
        isinstance(payload.get("schema_version"), bool)
        or not isinstance(payload.get("schema_version"), int)
        or payload.get("schema_version") != MUTATION_MANIFEST_SCHEMA_VERSION
        or payload.get("artifact_type") != "marlrefine_sealed_mutation_manifest"
        or payload.get("manifest_status") != "frozen_pending_archive"
        or payload.get("protocol_id") != MUTATION_PROTOCOL_ID
    ):
        raise MutationGateError("mutation manifest identity is invalid")
    environment = payload.get("environment")
    if (
        not isinstance(environment, Mapping)
        or environment.get("source_tree_sha256") != source_tree_sha256
    ):
        raise MutationGateError("mutation manifest source identity is stale")
    try:
        _batch_strict_equal(
            payload.get("candidates"),
            list(candidate_manifest_records()),
            "mutation manifest candidates",
        )
    except MutationBatchValidationError as exc:
        raise MutationGateError(str(exc)) from exc
    expected_controls = [
        control.to_manifest_record() for control in PROGRESS_INSTRUMENTATION_CONTROLS
    ]
    try:
        _batch_strict_equal(
            payload.get("progress_instrumentation_controls"),
            expected_controls,
            "mutation manifest progress controls",
        )
    except MutationBatchValidationError as exc:
        raise MutationGateError(str(exc)) from exc
    if payload.get("mutation_engine") != {
        "engine_id": MUTATION_ENGINE_ID,
        "source_module": "src/marlrefine/mutations.py",
        "source_sha256": mutation_engine_source_sha256(),
    }:
        raise MutationGateError("mutation engine identity differs from code")
    expected_protocol = build_mutation_manifest(
        manifest_status="frozen_pending_archive",
        source_git_revision="verification-placeholder",
    )
    for key in (
        "selection",
        "reference_adapter",
        "execution",
        "scoring",
        "mutation_engine",
        "candidates",
        "progress_instrumentation_controls",
        "prearchive_activity",
    ):
        try:
            _batch_strict_equal(
                payload.get(key),
                expected_protocol[key],
                f"mutation manifest {key}",
            )
        except MutationBatchValidationError as exc:
            raise MutationGateError(str(exc)) from exc
    selection = payload.get("selection")
    if (
        not isinstance(selection, Mapping)
        or isinstance(selection.get("required_total"), bool)
        or not isinstance(selection.get("required_total"), int)
        or selection.get("required_total") != 24
    ):
        raise MutationGateError("mutation manifest does not require 24 mutants")
    return payload, observed_hash


def verify_mutation_manifest(
    mutation_manifest_path: Path,
    study_manifest: Mapping[str, Any],
    *,
    source_tree_sha256: str,
) -> dict[str, Any]:
    """Verify the separately generated manifest bound by the study manifest."""
    payload, _ = _verify_mutation_manifest_with_sha256(
        mutation_manifest_path,
        study_manifest,
        source_tree_sha256=source_tree_sha256,
    )
    return payload


def _finding_codes(run: TraceRun) -> tuple[str, ...]:
    return tuple(sorted({finding.code for finding in run.violations}))


def _accepted_mean_field_rejection(run: TraceRun) -> bool:
    capability = run.summary.get("unsupported_capability")
    return (
        isinstance(capability, Mapping)
        and capability.get("capability_id")
        == "openspiel_mean_field_distribution_update_v1"
        and run.summary.get("stop_reason") == "adapter_setup_failed"
    )


_REFERENCE_EXECUTION_FAILURES = frozenset(
    {
        "source_setup_failed",
        "reset_replay_failed",
        "adapter_step_error",
        "source_replay_error",
        "destination_call_budget",
        "unknown",
    }
)


def _reference_acceptable(candidate: MutationCandidate, run: TraceRun) -> bool:
    """Classify clean execution without consulting either verdict system."""
    if (
        candidate.operator == "mean_field_bypass_rejection"
        and _accepted_mean_field_rejection(run)
    ):
        return True
    return (
        run.applicable
        and run.summary.get("setup_status") == "pass"
        and run.summary.get("stop_reason") not in _REFERENCE_EXECUTION_FAILURES
    )


def _adapter_projection(run: TraceRun) -> dict[str, Any]:
    summary_fields = (
        "setup_status",
        "stop_reason",
        "adapter_parameters",
        "adapter_game_length_at_reset",
        "adapter_game_length_final",
        "adapter_decision_clock_elapsed_final",
        "adapter_agents_remaining",
    )
    return {
        "applicable": run.applicable,
        "destination_events": run.destination_events,
        "summary": {key: run.summary.get(key) for key in summary_fields},
    }


def _finding_signature_records(run: TraceRun) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for finding in run.violations:
        payload = to_jsonable(finding)
        records.append(
            {
                "signature_sha256": _canonical_digest(payload),
                "category": (
                    "execution"
                    if finding.obligation == "trace_execution"
                    else "semantic"
                ),
                "detector_phase": _finding_phase(payload),
                "finding": payload,
            }
        )
    return tuple(records)


_POST_TRACE_ALIGNMENT_OBLIGATIONS = frozenset(
    {
        "stutter_reward_neutrality",
        "segment_reward_conservation",
        "monotone_progress_and_completeness",
        "terminal_cleanup_reward_neutrality",
        "boundary_lifecycle_preservation",
    }
)


def _finding_phase(finding: Mapping[str, Any]) -> str:
    """Map a full finding to its deterministic checker phase when knowable."""
    obligation = str(finding.get("obligation", ""))
    code = str(finding.get("code", ""))
    if code in {
        "source_setup_failed",
        "adapter_setup_failed",
        "parameters_changed_on_reset",
        "player_count_changed_on_reset",
        "agent_identity_mismatch",
        "reset_replay_failed",
        "reset_state_mismatch",
    }:
        return "setup_or_reset"
    if obligation in _POST_TRACE_ALIGNMENT_OBLIGATIONS:
        return "post_trace_alignment"
    if code in {"consumer_return_mismatch", "unfinished_joint_action_buffer"}:
        return "post_episode"
    if finding.get("destination_span") is not None:
        return "online_destination_boundary"
    if obligation == "trace_execution":
        return "execution_control"
    return "post_trace_or_unknown"


def _new_finding_signatures(
    reference: TraceRun,
    mutant: TraceRun,
) -> tuple[dict[str, Any], ...]:
    reference_signatures = {
        record["signature_sha256"] for record in _finding_signature_records(reference)
    }
    return tuple(
        record
        for record in _finding_signature_records(mutant)
        if record["signature_sha256"] not in reference_signatures
    )


def _baseline_finding_signatures(findings: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple(sorted({_canonical_digest(to_jsonable(item)) for item in findings}))


def _paired_baseline_signals(
    reference: TraceRun,
    mutant: TraceRun,
) -> dict[str, dict[str, Any]]:
    reference_by_name = {item.baseline: item for item in reference.baselines}
    mutant_by_name = {item.baseline: item for item in mutant.baselines}
    result: dict[str, dict[str, Any]] = {}
    for name in BASELINE_NAMES:
        clean = reference_by_name[name]
        changed = mutant_by_name[name]
        clean_signatures = set(_baseline_finding_signatures(clean.findings))
        changed_signatures = set(_baseline_finding_signatures(changed.findings))
        added = tuple(sorted(changed_signatures.difference(clean_signatures)))
        result[name] = {
            "reference_applicable": clean.applicable,
            "mutant_applicable": changed.applicable,
            "reference_finding_signatures": tuple(sorted(clean_signatures)),
            "mutant_finding_signatures": tuple(sorted(changed_signatures)),
            "added_finding_signatures": added,
            "paired_signal": bool(added),
        }
    return result


def _paired_stock_signal(
    reference: StockApiResult,
    mutant: StockApiResult,
    *,
    reference_adapter_class: str,
    mutant_adapter_class: str,
) -> dict[str, Any]:
    return {
        "reference_adapter_class": reference_adapter_class,
        "mutant_adapter_class": mutant_adapter_class,
        "reference": reference,
        "mutant": mutant,
        "paired_signal": reference.passed and not mutant.passed,
        "interpretation": (
            "destination API-test failure introduced relative to the paired "
            "composite repaired reference"
        ),
    }


def _candidate_record(candidate: MutationCandidate) -> dict[str, Any]:
    reference_class = paired_reference_class_for(candidate)
    reference_started = perf_counter_ns()
    reference = run_trace(
        candidate.game_spec,
        seed=candidate.environment_seed,
        trace_policy=candidate.trace_policy_name,
        max_destination_calls=MUTATION_MAX_DESTINATION_CALLS,
        max_source_decisions=candidate.max_source_decisions,
        adapter_class=reference_class,
    )
    reference_elapsed = perf_counter_ns() - reference_started

    mutant_class = adapter_class_for(candidate)
    mutant_started = perf_counter_ns()
    mutant = run_trace(
        candidate.game_spec,
        seed=candidate.environment_seed,
        trace_policy=candidate.trace_policy_name,
        max_destination_calls=MUTATION_MAX_DESTINATION_CALLS,
        max_source_decisions=candidate.max_source_decisions,
        adapter_class=mutant_class,
    )
    mutant_elapsed = perf_counter_ns() - mutant_started
    evidence = mutant_class.mutation_evidence()

    reference_stock = run_stock_api_test(
        candidate.game_spec,
        cycles=MUTATION_STOCK_API_CYCLES,
        seed=candidate.environment_seed,
        adapter_class=reference_class,
    )
    mutant_stock = run_stock_api_test(
        candidate.game_spec,
        cycles=MUTATION_STOCK_API_CYCLES,
        seed=candidate.environment_seed,
        adapter_class=mutant_class,
    )

    reference_projection = _adapter_projection(reference)
    mutant_projection = _adapter_projection(mutant)
    reference_acceptable = _reference_acceptable(candidate, reference)
    hook_reached = int(evidence["trigger_count"]) > 0
    behavior_changed = _canonical_digest(reference_projection) != _canonical_digest(
        mutant_projection
    )
    behavior_delta_sha256 = _canonical_digest(
        {"reference": reference_projection, "mutant": mutant_projection}
    )
    base_eligible = reference_acceptable and hook_reached and behavior_changed

    new_signatures = _new_finding_signatures(reference, mutant)
    new_semantic = tuple(
        item for item in new_signatures if item["category"] == "semantic"
    )
    new_execution = tuple(
        item for item in new_signatures if item["category"] == "execution"
    )
    reference_signatures = _finding_signature_records(reference)
    reference_semantic_count = sum(
        item["category"] == "semantic" for item in reference_signatures
    )
    reference_execution_count = sum(
        item["category"] == "execution" for item in reference_signatures
    )
    accepted_unsupported = _accepted_mean_field_rejection(reference)
    first_detection = new_signatures[0] if new_signatures else None

    return {
        "candidate": candidate.to_manifest_record(),
        "selection_inputs": {
            "reference_acceptable": reference_acceptable,
            "hook_reached": hook_reached,
            "adapter_behavior_changed": behavior_changed,
            "behavior_delta_sha256": behavior_delta_sha256,
            "base_eligible_before_uniqueness": base_eligible,
            "unique_behavior_among_prior_selected": None,
            "eligible": None,
        },
        "mutation_evidence": evidence,
        "reference": {
            "status": classify_run_payload(reference.to_dict()).value,
            "finding_codes": _finding_codes(reference),
            "finding_signatures": reference_signatures,
            "clean_reference_alarm": {
                "semantic_finding_count": reference_semantic_count,
                "execution_finding_count": reference_execution_count,
                "semantic_alarm": reference_semantic_count > 0,
                "unexpected_execution_alarm": (
                    reference_execution_count > 0 and not accepted_unsupported
                ),
                "accepted_typed_unsupported_capability": accepted_unsupported,
                "any_unexpected_alarm": (
                    reference_semantic_count > 0
                    or (reference_execution_count > 0 and not accepted_unsupported)
                ),
            },
            "adapter_projection_sha256": _canonical_digest(reference_projection),
            "elapsed_ns": reference_elapsed,
            "run": reference,
        },
        "mutant": {
            "status": classify_run_payload(mutant.to_dict()).value,
            "finding_codes": _finding_codes(mutant),
            "finding_signatures": _finding_signature_records(mutant),
            "new_full_finding_signatures": new_signatures,
            "new_semantic_finding_signatures": new_semantic,
            "new_execution_finding_signatures": new_execution,
            "adapter_projection_sha256": _canonical_digest(mutant_projection),
            "elapsed_ns": mutant_elapsed,
            "run": mutant,
        },
        "kill": {
            "new_full_signature_count": len(new_signatures),
            "new_semantic_signature_count": len(new_semantic),
            "new_execution_signature_count": len(new_execution),
            "killed": None,
            "semantic_kill": None,
            "crash_only_kill": None,
            "status": "pending_outcome_blind_selection",
            "first_detecting_signature_sha256": (
                None if first_detection is None else first_detection["signature_sha256"]
            ),
            "first_detecting_obligation": (
                None
                if first_detection is None
                else first_detection["finding"]["obligation"]
            ),
            "first_detecting_phase": (
                None if first_detection is None else first_detection["detector_phase"]
            ),
            "first_detection_order": "stable_mutant_finding_order",
        },
        "ablation_signals": {
            "project_baselines": _paired_baseline_signals(reference, mutant),
            "stock_pettingzoo_api_test": _paired_stock_signal(
                reference_stock,
                mutant_stock,
                reference_adapter_class=reference_class.__name__,
                mutant_adapter_class=mutant_class.__name__,
            ),
        },
    }


def _apply_outcome_blind_selection(
    records: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Apply frozen replacement using only non-verdict execution attributes."""
    selected_ids: dict[str, list[str]] = defaultdict(list)
    selected_behavior: dict[str, set[str]] = defaultdict(set)
    selected_behavior_origin: dict[str, dict[str, str]] = defaultdict(dict)
    for record in records:
        candidate = record["candidate"]
        family = str(candidate["family"])
        inputs = record["selection_inputs"]
        behavior = str(inputs["behavior_delta_sha256"])
        unique = behavior not in selected_behavior[family]
        eligible = bool(inputs["base_eligible_before_uniqueness"]) and unique
        inputs["unique_behavior_among_prior_selected"] = unique
        inputs["duplicate_behavior_of_candidate_id"] = (
            None if unique else selected_behavior_origin[family].get(behavior)
        )
        inputs["eligible"] = eligible
        if eligible and len(selected_ids[family]) < MUTANTS_PER_FAMILY:
            selected_ids[family].append(str(candidate["candidate_id"]))
            selected_behavior[family].add(behavior)
            selected_behavior_origin[family][behavior] = str(candidate["candidate_id"])
            record["selected"] = True
        else:
            record["selected"] = False

        reasons: list[str] = []
        if record["selected"] is True:
            reasons.append("selected")
        else:
            if not bool(inputs["base_eligible_before_uniqueness"]):
                if inputs.get("reference_acceptable") is False:
                    reasons.append("clean_reference_unacceptable")
                if inputs.get("hook_reached") is False:
                    reasons.append("mutation_hook_not_reached")
                if inputs.get("adapter_behavior_changed") is False:
                    reasons.append("adapter_behavior_unchanged")
                if not reasons:
                    reasons.append("base_eligibility_failed_unspecified")
            if not unique:
                reasons.append("duplicate_behavior_delta")
            if eligible and len(selected_ids[family]) >= MUTANTS_PER_FAMILY:
                reasons.append("family_quota_already_met")
        record["replacement_attempt"] = {
            "attempted": True,
            "reason_codes": tuple(reasons),
        }

        kill = record["kill"]
        has_semantic = bool(kill["new_semantic_signature_count"])
        has_execution = bool(kill["new_execution_signature_count"])
        selected = record["selected"] is True
        kill["killed"] = selected and (has_semantic or has_execution)
        kill["semantic_kill"] = selected and has_semantic
        kill["crash_only_kill"] = selected and not has_semantic and has_execution
        if not selected:
            kill["status"] = "not_selected"
        elif has_semantic:
            kill["status"] = "semantic_kill"
        elif has_execution:
            kill["status"] = "crash_only_kill"
        else:
            kill["status"] = "survived"
    selected_by_family = {
        family: len(selected_ids[family]) for family in MUTATION_FAMILIES
    }
    return dict(selected_ids), selected_by_family


def _instrumentation_control_record(control: Any) -> dict[str, Any]:
    clean = run_trace(
        control.game_spec,
        seed=control.environment_seed,
        trace_policy=control.trace_policy_name,
        max_destination_calls=MUTATION_MAX_DESTINATION_CALLS,
        max_source_decisions=control.max_source_decisions,
        adapter_class=CombinedRepairV0,
    )
    corrupted = run_trace(
        control.game_spec,
        seed=control.environment_seed,
        trace_policy=control.trace_policy_name,
        max_destination_calls=MUTATION_MAX_DESTINATION_CALLS,
        max_source_decisions=control.max_source_decisions,
        adapter_class=CombinedRepairV0,
        progress_annotation_transform=progress_transform_for(control),
        progress_annotation_control_id=control.control_id,
    )
    new_signatures = _new_finding_signatures(clean, corrupted)
    named_instrumentation_failures = tuple(
        item
        for item in new_signatures
        if (
            item["finding"]["obligation"],
            item["finding"]["code"],
        )
        == PROGRESS_CONTROL_REQUIRED_FINDING
    )
    return {
        "control": control.to_manifest_record(),
        "reference": clean,
        "corrupted": corrupted,
        "new_full_finding_signatures": new_signatures,
        "detected": bool(named_instrumentation_failures),
        "included_in_24_mutant_denominator": False,
    }


def _batch_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MutationBatchValidationError(f"{label} must be an object")
    return value


def _batch_array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise MutationBatchValidationError(f"{label} must be an array")
    return value


def _batch_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    observed = frozenset(value)
    if observed != expected:
        missing = sorted(expected.difference(observed))
        extra = sorted(observed.difference(expected))
        raise MutationBatchValidationError(
            f"{label} keys differ; missing={missing}, extra={extra}"
        )


def _batch_sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MutationBatchValidationError(
            f"{label} is not a lowercase SHA-256"
        )
    return value


def _batch_nonnegative_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MutationBatchValidationError(
            f"{label} must be a nonnegative integer"
        )
    return value


def _batch_boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise MutationBatchValidationError(f"{label} must be boolean")
    return value


def _batch_finite_number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MutationBatchValidationError(f"{label} must be numeric")
    if not math.isfinite(float(value)):
        raise MutationBatchValidationError(f"{label} must be finite")
    return value


def _batch_strict_equal(observed: Any, expected: Any, label: str) -> None:
    if isinstance(expected, Mapping):
        actual = _batch_object(observed, label)
        _batch_exact_keys(actual, frozenset(expected), label)
        for key, expected_value in expected.items():
            _batch_strict_equal(actual[key], expected_value, f"{label}.{key}")
        return
    if isinstance(expected, list | tuple):
        actual = _batch_array(observed, label)
        if len(actual) != len(expected):
            raise MutationBatchValidationError(f"{label} array length differs")
        for index, (actual_value, expected_value) in enumerate(
            zip(actual, expected, strict=True)
        ):
            _batch_strict_equal(actual_value, expected_value, f"{label}[{index}]")
        return
    if expected is None:
        if observed is not None:
            raise MutationBatchValidationError(f"{label} differs")
        return
    if type(observed) is not type(expected) or observed != expected:
        raise MutationBatchValidationError(f"{label} differs")


def _batch_span(value: Any, label: str, *, limit: int) -> tuple[int, int] | None:
    if value is None:
        return None
    span = _batch_object(value, label)
    _batch_exact_keys(span, _SPAN_KEYS, label)
    start = _batch_nonnegative_integer(span.get("start"), f"{label}.start")
    stop = _batch_nonnegative_integer(span.get("stop"), f"{label}.stop")
    if stop < start or stop > limit:
        raise MutationBatchValidationError(f"{label} lies outside its ledger")
    return start, stop


def _batch_reward_vector(value: Any, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    for index, item in enumerate(_batch_array(value, label)):
        _batch_finite_number(item, f"{label}[{index}]")


def _validate_source_event(value: Any, label: str) -> Mapping[str, Any]:
    event = _batch_object(value, label)
    _batch_exact_keys(event, _SOURCE_EVENT_KEYS, label)
    _batch_nonnegative_integer(event.get("progress"), f"{label}.progress")
    _batch_reward_vector(event.get("rewards"), f"{label}.rewards")
    _batch_boolean(event.get("terminated"), f"{label}.terminated")
    _batch_boolean(event.get("truncated"), f"{label}.truncated")
    _batch_object(event.get("metadata"), f"{label}.metadata")
    return event


def _validate_destination_event(
    value: Any,
    label: str,
    *,
    previous_actual_progress: int,
    progress_control_id: str | None,
) -> tuple[Mapping[str, Any], int]:
    event = _batch_object(value, label)
    _batch_exact_keys(event, _DESTINATION_EVENT_KEYS, label)
    annotated_progress = _batch_nonnegative_integer(
        event.get("source_progress"),
        f"{label}.source_progress",
    )
    _batch_reward_vector(event.get("rewards"), f"{label}.rewards")
    _batch_reward_vector(
        event.get("delivered_rewards"),
        f"{label}.delivered_rewards",
        optional=True,
    )
    for field in ("terminated", "truncated", "cleanup"):
        _batch_boolean(event.get(field), f"{label}.{field}")
    metadata = _batch_object(event.get("metadata"), f"{label}.metadata")
    instrumentation = _batch_object(
        metadata.get("progress_instrumentation"),
        f"{label}.metadata.progress_instrumentation",
    )
    _batch_exact_keys(
        instrumentation,
        _PROGRESS_INSTRUMENTATION_KEYS,
        f"{label}.metadata.progress_instrumentation",
    )
    if instrumentation.get("method_id") != _PROGRESS_ANNOTATION_METHOD_ID:
        raise MutationBatchValidationError(f"{label} progress method differs")
    progress_before = _batch_nonnegative_integer(
        instrumentation.get("progress_before"),
        f"{label}.progress_before",
    )
    progress_after = _batch_nonnegative_integer(
        instrumentation.get("progress_after"),
        f"{label}.progress_after",
    )
    instrumented_annotation = _batch_nonnegative_integer(
        instrumentation.get("annotated_progress_after"),
        f"{label}.annotated_progress_after",
    )
    if progress_before != previous_actual_progress:
        raise MutationBatchValidationError(f"{label} progress_before differs")
    expected_progresses = list(range(progress_before + 1, progress_after + 1))
    expected = {
        "replayed_source_event_count": len(expected_progresses),
        "source_event_progresses": expected_progresses,
    }
    for field, expected_value in expected.items():
        _batch_strict_equal(
            instrumentation.get(field),
            expected_value,
            f"{label}.{field}",
        )
    if instrumented_annotation != annotated_progress:
        raise MutationBatchValidationError(f"{label} annotated progress differs")
    if progress_control_id is None and annotated_progress != progress_after:
        raise MutationBatchValidationError(
            f"{label} uncorrupted progress annotation differs"
        )
    for index, action in enumerate(
        _batch_array(
            instrumentation.get("wrapped_history_delta"),
            f"{label}.wrapped_history_delta",
        )
    ):
        _batch_nonnegative_integer(action, f"{label}.wrapped_history_delta[{index}]")
    return event, progress_after


def _validate_violation(
    value: Any,
    label: str,
    *,
    source_length: int,
    destination_length: int,
    segment_count: int,
    allowed_codes: Mapping[str, frozenset[str]],
) -> Mapping[str, Any]:
    finding = _batch_object(value, label)
    _batch_exact_keys(finding, _VIOLATION_KEYS, label)
    obligation = finding.get("obligation")
    code = finding.get("code")
    message = finding.get("message")
    if not isinstance(obligation, str) or not obligation:
        raise MutationBatchValidationError(f"{label}.obligation must be text")
    if not isinstance(code, str) or not code:
        raise MutationBatchValidationError(f"{label}.code must be text")
    if not isinstance(message, str) or not message:
        raise MutationBatchValidationError(f"{label}.message must be text")
    if obligation not in allowed_codes or code not in allowed_codes[obligation]:
        raise MutationBatchValidationError(
            f"{label} obligation/code is not a registered finding"
        )
    segment_index = finding.get("segment_index")
    if segment_index is not None:
        segment_index = _batch_nonnegative_integer(
            segment_index,
            f"{label}.segment_index",
        )
        if segment_index >= segment_count:
            raise MutationBatchValidationError(f"{label}.segment_index is out of range")
    _batch_span(finding.get("source_span"), f"{label}.source_span", limit=source_length)
    _batch_span(
        finding.get("destination_span"),
        f"{label}.destination_span",
        limit=destination_length,
    )
    return finding


def _validate_alignment(
    value: Any,
    *,
    source_events: list[Any],
    destination_events: list[Any],
    progress_control_id: str | None,
    label: str,
) -> Mapping[str, Any]:
    alignment = _batch_object(value, label)
    _batch_exact_keys(alignment, _ALIGNMENT_KEYS, label)
    _batch_strict_equal(
        alignment.get("source_events"),
        source_events,
        f"{label}.source_events",
    )
    _batch_strict_equal(
        alignment.get("destination_events"),
        destination_events,
        f"{label}.destination_events",
    )
    initial_progress = _batch_nonnegative_integer(
        alignment.get("initial_progress"),
        f"{label}.initial_progress",
    )
    for index, event in enumerate(source_events):
        _validate_source_event(event, f"{label}.source_events[{index}]")
    actual_progress = initial_progress
    for index, event in enumerate(destination_events):
        _, actual_progress = _validate_destination_event(
            event,
            f"{label}.destination_events[{index}]",
            previous_actual_progress=actual_progress,
            progress_control_id=progress_control_id,
        )
    segments = _batch_array(alignment.get("segments"), f"{label}.segments")
    source_cursor = 0
    destination_cursor = 0
    for index, raw_segment in enumerate(segments):
        segment_label = f"{label}.segments[{index}]"
        segment = _batch_object(raw_segment, segment_label)
        _batch_exact_keys(segment, _SEGMENT_KEYS, segment_label)
        kind = segment.get("kind")
        if kind not in {"transition", "stutter", "terminal_tail"}:
            raise MutationBatchValidationError(f"{segment_label}.kind differs")
        before = _batch_nonnegative_integer(
            segment.get("source_before"),
            f"{segment_label}.source_before",
        )
        after = _batch_nonnegative_integer(
            segment.get("source_after"),
            f"{segment_label}.source_after",
        )
        if after < before or (kind == "transition") != (after > before):
            raise MutationBatchValidationError(f"{segment_label} progress/kind differs")
        source_span = _batch_span(
            segment.get("source_span"),
            f"{segment_label}.source_span",
            limit=len(source_events),
        )
        destination_span = _batch_span(
            segment.get("destination_span"),
            f"{segment_label}.destination_span",
            limit=len(destination_events),
        )
        if source_span is None or destination_span is None:
            raise MutationBatchValidationError(f"{segment_label} spans cannot be null")
        if source_span[0] != source_cursor or destination_span[0] != destination_cursor:
            raise MutationBatchValidationError(
                f"{segment_label} does not continue the alignment cover"
            )
        _batch_strict_equal(
            segment.get("source_events"),
            source_events[slice(*source_span)],
            f"{segment_label}.source_events",
        )
        _batch_strict_equal(
            segment.get("destination_events"),
            destination_events[slice(*destination_span)],
            f"{segment_label}.destination_events",
        )
        if destination_span[0] == destination_span[1]:
            raise MutationBatchValidationError(
                f"{segment_label} has no destination event"
            )
        if kind != "transition" and source_span[0] != source_span[1]:
            raise MutationBatchValidationError(
                f"{segment_label} non-transition consumes source events"
            )
        source_cursor = source_span[1]
        destination_cursor = destination_span[1]
    if destination_cursor != len(destination_events):
        raise MutationBatchValidationError(
            f"{label} segments do not cover the destination ledger"
        )
    return alignment


def _validate_serialized_run(
    value: Any,
    *,
    candidate: Mapping[str, Any] | None,
    label: str,
) -> Mapping[str, Any]:
    run = _batch_object(value, label)
    _batch_exact_keys(run, _TRACE_RUN_KEYS, label)
    if not isinstance(run.get("game_spec"), str):
        raise MutationBatchValidationError(f"{label}.game_spec must be text")
    seed = _batch_nonnegative_integer(run.get("seed"), f"{label}.seed")
    _batch_boolean(run.get("applicable"), f"{label}.applicable")
    if candidate is not None and (
        run.get("game_spec") != candidate.get("game_spec")
        or seed != candidate.get("environment_seed")
    ):
        raise MutationBatchValidationError(
            f"{label} game or environment seed differs from sealed candidate"
        )
    source_events = _batch_array(run.get("source_events"), f"{label}.source_events")
    destination_events = _batch_array(
        run.get("destination_events"),
        f"{label}.destination_events",
    )
    summary = _batch_object(run.get("summary"), f"{label}.summary")
    observed_summary_keys = frozenset(summary)
    if not _SUMMARY_REQUIRED_KEYS.issubset(observed_summary_keys) or not (
        observed_summary_keys <= _SUMMARY_ALLOWED_KEYS
    ):
        missing = sorted(_SUMMARY_REQUIRED_KEYS - observed_summary_keys)
        extra = sorted(observed_summary_keys - _SUMMARY_ALLOWED_KEYS)
        raise MutationBatchValidationError(
            f"{label}.summary keys differ; missing={missing}, extra={extra}"
        )
    progress_control_id = summary.get("progress_annotation_control_id")
    if progress_control_id is not None and not isinstance(progress_control_id, str):
        raise MutationBatchValidationError(
            f"{label}.summary.progress_annotation_control_id must be text or null"
        )
    alignment = _validate_alignment(
        run.get("alignment"),
        source_events=source_events,
        destination_events=destination_events,
        progress_control_id=progress_control_id,
        label=f"{label}.alignment",
    )
    segments = _batch_array(
        alignment.get("segments"),
        f"{label}.alignment.segments",
    )
    violations = _batch_array(run.get("violations"), f"{label}.violations")
    for index, finding in enumerate(violations):
        _validate_violation(
            finding,
            f"{label}.violations[{index}]",
            source_length=len(source_events),
            destination_length=len(destination_events),
            segment_count=len(segments),
            allowed_codes=_PRIMARY_FINDING_CODES,
        )
    baselines = _batch_array(run.get("baselines"), f"{label}.baselines")
    if len(baselines) != len(BASELINE_NAMES):
        raise MutationBatchValidationError(f"{label}.baselines panel count differs")
    for index, (raw_baseline, expected_name) in enumerate(
        zip(baselines, BASELINE_NAMES, strict=True)
    ):
        baseline_label = f"{label}.baselines[{index}]"
        baseline = _batch_object(raw_baseline, baseline_label)
        _batch_exact_keys(baseline, _BASELINE_RESULT_KEYS, baseline_label)
        if baseline.get("baseline") != expected_name:
            raise MutationBatchValidationError(f"{baseline_label}.baseline differs")
        applicable = _batch_boolean(
            baseline.get("applicable"),
            f"{baseline_label}.applicable",
        )
        findings = _batch_array(
            baseline.get("findings"),
            f"{baseline_label}.findings",
        )
        for finding_index, finding in enumerate(findings):
            _validate_violation(
                finding,
                f"{baseline_label}.findings[{finding_index}]",
                source_length=len(source_events),
                destination_length=len(destination_events),
                segment_count=len(segments),
                allowed_codes=_BASELINE_FINDING_CODES,
            )
        reason = baseline.get("reason")
        if applicable and reason is not None:
            raise MutationBatchValidationError(
                f"{baseline_label} applicable result carries a reason"
            )
        if not applicable and (
            findings or not isinstance(reason, str) or not reason
        ):
            raise MutationBatchValidationError(
                f"{baseline_label} inapplicable result is inconsistent"
            )
    expected_counts = {
        "destination_calls": len(destination_events),
        "source_transitions": len(source_events),
    }
    if "violation_count" in summary:
        expected_counts["violation_count"] = len(violations)
    for field, expected in expected_counts.items():
        actual = _batch_nonnegative_integer(
            summary.get(field),
            f"{label}.summary.{field}",
        )
        if actual != expected:
            raise MutationBatchValidationError(f"{label}.summary.{field} differs")
    for field in ("source_decisions", "chance_event_count"):
        _batch_nonnegative_integer(summary.get(field), f"{label}.summary.{field}")
    for field in ("setup_status", "adapter_class", "stop_reason"):
        if not isinstance(summary.get(field), str) or not summary[field]:
            raise MutationBatchValidationError(f"{label}.summary.{field} must be text")
    for field in ("source_terminal",):
        value_field = summary.get(field)
        if value_field is not None:
            _batch_boolean(value_field, f"{label}.summary.{field}")
    remaining = summary.get("adapter_agents_remaining")
    if remaining is not None:
        _batch_nonnegative_integer(
            remaining,
            f"{label}.summary.adapter_agents_remaining",
        )
    for field in (
        "caller_supplied_nondefault_configuration",
        "decision_clock_mismatch_reported",
    ):
        if field in summary and summary[field] is not None:
            _batch_boolean(summary[field], f"{label}.summary.{field}")
    if candidate is not None:
        policy = get_trace_policy(str(candidate.get("trace_policy_name")))
        expected_schedule = {
            "protocol_version": PROTOCOL_VERSION,
            "trace_policy_name": policy.name,
            "trace_policy_id": policy.policy_id,
            "trace_policy_engine_id": POLICY_ENGINE_ID,
            "trace_policy_seed": policy.seed,
            "chance_policy_id": CHANCE_POLICY_ID,
            "progress_annotation_method_id": _PROGRESS_ANNOTATION_METHOD_ID,
            "requested_seed": candidate.get("environment_seed"),
            "requested_max_destination_calls": MUTATION_MAX_DESTINATION_CALLS,
            "requested_max_source_decisions": candidate.get("max_source_decisions"),
        }
        for field, expected in expected_schedule.items():
            _batch_strict_equal(
                summary.get(field),
                expected,
                f"{label}.summary.{field}",
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
            label=f"{label}.obligation_evaluations",
        )
    except ValueError as exc:
        raise MutationBatchValidationError(str(exc)) from exc
    return run


def _serialized_adapter_projection(
    run: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    if not isinstance(run.get("applicable"), bool):
        raise MutationBatchValidationError(f"{label}.applicable must be boolean")
    destination_events = _batch_array(
        run.get("destination_events"),
        f"{label}.destination_events",
    )
    summary = _batch_object(run.get("summary"), f"{label}.summary")
    return {
        "applicable": run["applicable"],
        "destination_events": destination_events,
        "summary": {
            key: summary.get(key) for key in _ADAPTER_PROJECTION_SUMMARY_FIELDS
        },
    }


def _serialized_reference_acceptable(
    candidate: Mapping[str, Any],
    run: Mapping[str, Any],
) -> bool:
    summary = _batch_object(run.get("summary"), "reference.run.summary")
    capability = summary.get("unsupported_capability")
    accepted_mean_field_rejection = (
        candidate.get("operator") == "mean_field_bypass_rejection"
        and isinstance(capability, Mapping)
        and capability.get("capability_id")
        == "openspiel_mean_field_distribution_update_v1"
        and summary.get("stop_reason") == "adapter_setup_failed"
    )
    if accepted_mean_field_rejection:
        return True
    return (
        run.get("applicable") is True
        and summary.get("setup_status") == "pass"
        and summary.get("stop_reason") not in _REFERENCE_EXECUTION_FAILURES
    )


def _validate_mutation_evidence(
    value: Any,
    *,
    candidate: Mapping[str, Any],
    reference_run: Mapping[str, Any],
    mutant_run: Mapping[str, Any],
    label: str,
) -> bool:
    evidence = _batch_object(value, label)
    _batch_exact_keys(evidence, _MUTATION_EVIDENCE_KEYS, label)
    if (
        evidence.get("candidate_id") != candidate.get("candidate_id")
        or evidence.get("operator") != candidate.get("operator")
    ):
        raise MutationBatchValidationError(
            f"{label} identity differs from sealed candidate"
        )
    trigger_count = _batch_nonnegative_integer(
        evidence.get("trigger_count"),
        f"{label}.trigger_count",
    )
    contexts = _batch_array(
        evidence.get("trigger_contexts"),
        f"{label}.trigger_contexts",
    )
    if len(contexts) != min(trigger_count, 8):
        raise MutationBatchValidationError(
            f"{label}.trigger_contexts does not match the capped trigger count"
        )
    if (trigger_count > 0) != bool(contexts):
        raise MutationBatchValidationError(
            f"{label} hook count/context presence is inconsistent"
        )
    operator = str(candidate["operator"])
    parameters = _batch_object(candidate.get("parameters"), f"{label}.parameters")
    for index, raw_context in enumerate(contexts):
        context_label = f"{label}.trigger_contexts[{index}]"
        context = _batch_object(raw_context, context_label)
        if operator.startswith("reward_"):
            _batch_exact_keys(context, frozenset({"before", "after"}), context_label)
            before = _batch_array(context.get("before"), f"{context_label}.before")
            after = _batch_array(context.get("after"), f"{context_label}.after")
            for vector_name, vector in (("before", before), ("after", after)):
                for item_index, item in enumerate(vector):
                    _batch_finite_number(
                        item,
                        f"{context_label}.{vector_name}[{item_index}]",
                    )
            if len(before) != len(after) or before == after:
                raise MutationBatchValidationError(
                    f"{context_label} reward mutation did not change its vector"
                )
            if not any(
                event.get("rewards") == after
                for event in _batch_array(
                    mutant_run.get("destination_events"),
                    f"{label}.mutant destination events",
                )
            ):
                raise MutationBatchValidationError(
                    f"{context_label} reward evidence is not present in mutant run"
                )
        elif operator in {"history_lag_one", "history_duplicate_last"}:
            _batch_exact_keys(context, frozenset({"history_length"}), context_label)
            if _batch_nonnegative_integer(
                context.get("history_length"),
                f"{context_label}.history_length",
            ) < 1:
                raise MutationBatchValidationError(
                    f"{context_label}.history_length must be positive"
                )
        elif operator in {"action_mask_remove", "action_mask_add"}:
            _batch_exact_keys(context, frozenset({"agent", "action"}), context_label)
            if not isinstance(context.get("agent"), str) or not context["agent"]:
                raise MutationBatchValidationError(
                    f"{context_label}.agent must be text"
                )
            _batch_nonnegative_integer(
                context.get("action"),
                f"{context_label}.action",
            )
        elif operator == "observation_swap_agents":
            _batch_exact_keys(context, frozenset({"agents"}), context_label)
            agents = _batch_array(context.get("agents"), f"{context_label}.agents")
            if len(agents) != 2 or not all(
                isinstance(agent, str) and agent for agent in agents
            ):
                raise MutationBatchValidationError(
                    f"{context_label}.agents must identify two agents"
                )
        elif operator in {
            "observation_dtype_float32",
            "observation_list_container",
            "observation_value_offset",
        }:
            _batch_exact_keys(context, frozenset({"agent", "shape"}), context_label)
            if not isinstance(context.get("agent"), str) or not context["agent"]:
                raise MutationBatchValidationError(
                    f"{context_label}.agent must be text"
                )
            shape = _batch_array(context.get("shape"), f"{context_label}.shape")
            for dimension_index, dimension in enumerate(shape):
                _batch_nonnegative_integer(
                    dimension,
                    f"{context_label}.shape[{dimension_index}]",
                )
        elif operator == "clock_reset_offset":
            _batch_exact_keys(context, frozenset({"offset"}), context_label)
            _batch_strict_equal(
                context.get("offset"),
                parameters.get("offset"),
                f"{context_label}.offset",
            )
        elif operator in {"clock_extra_on_advance", "clock_cancel_on_advance"}:
            _batch_exact_keys(context, frozenset({"offset", "advanced"}), context_label)
            _batch_strict_equal(
                context.get("offset"),
                parameters.get("offset"),
                f"{context_label}.offset",
            )
            if (
                _batch_boolean(
                    context.get("advanced"),
                    f"{context_label}.advanced",
                )
                is not True
            ):
                raise MutationBatchValidationError(
                    f"{context_label}.advanced must be true"
                )
        elif operator == "clock_buffer_increment":
            _batch_exact_keys(context, frozenset({"buffer_only"}), context_label)
            if (
                _batch_boolean(
                    context.get("buffer_only"),
                    f"{context_label}.buffer_only",
                )
                is not True
            ):
                raise MutationBatchValidationError(
                    f"{context_label}.buffer_only must be true"
                )
        elif operator == "clock_chance_increment":
            _batch_exact_keys(context, frozenset({"chance_events"}), context_label)
            if _batch_nonnegative_integer(
                context.get("chance_events"),
                f"{context_label}.chance_events",
            ) < 1:
                raise MutationBatchValidationError(
                    f"{context_label}.chance_events must be positive"
                )
        elif operator in {
            "terminal_as_truncation",
            "suppress_terminal",
            "premature_termination",
            "premature_truncation",
            "partial_terminal_flags",
        }:
            _batch_exact_keys(context, frozenset({"source_terminal"}), context_label)
            terminal = _batch_boolean(
                context.get("source_terminal"),
                f"{context_label}.source_terminal",
            )
            expected_terminal = operator not in {
                "premature_termination",
                "premature_truncation",
            }
            if terminal is not expected_terminal:
                raise MutationBatchValidationError(
                    f"{context_label}.source_terminal differs for operator"
                )
        elif operator == "clear_agents_at_terminal":
            _batch_exact_keys(context, frozenset({"cleared_agents"}), context_label)
            if (
                _batch_boolean(
                    context.get("cleared_agents"),
                    f"{context_label}.cleared_agents",
                )
                is not True
            ):
                raise MutationBatchValidationError(
                    f"{context_label}.cleared_agents must be true"
                )
        elif operator in {"config_replace", "config_drop"}:
            _batch_exact_keys(
                context,
                frozenset({"key", "before", "after"}),
                context_label,
            )
            _batch_strict_equal(
                context.get("key"),
                parameters.get("key"),
                f"{context_label}.key",
            )
            if operator == "config_drop" and context.get("after") is not None:
                raise MutationBatchValidationError(
                    f"{context_label}.after must be null"
                )
            if operator == "config_replace":
                _batch_strict_equal(
                    context.get("after"),
                    parameters.get("value"),
                    f"{context_label}.after",
                )
        elif operator == "mean_field_bypass_rejection":
            _batch_exact_keys(context, frozenset({"dynamics"}), context_label)
            if context.get("dynamics") != "mean_field":
                raise MutationBatchValidationError(f"{context_label}.dynamics differs")
        elif operator == "chance_unresolved":
            _batch_exact_keys(
                context,
                frozenset({"chance_left_unresolved"}),
                context_label,
            )
            if _batch_boolean(
                context.get("chance_left_unresolved"),
                f"{context_label}.chance_left_unresolved",
            ) is not True:
                raise MutationBatchValidationError(
                    f"{context_label}.chance_left_unresolved must be true"
                )
        elif operator == "chance_one_only":
            _batch_exact_keys(context, frozenset({"resolved_action"}), context_label)
            _batch_nonnegative_integer(
                context.get("resolved_action"),
                f"{context_label}.resolved_action",
            )
        elif operator == "simultaneous_forget_buffer":
            _batch_exact_keys(context, frozenset({"forgot_buffer"}), context_label)
            if (
                _batch_boolean(
                    context.get("forgot_buffer"),
                    f"{context_label}.forgot_buffer",
                )
                is not True
            ):
                raise MutationBatchValidationError(
                    f"{context_label}.forgot_buffer must be true"
                )
        elif operator == "simultaneous_prefill_next":
            _batch_exact_keys(
                context,
                frozenset({"prefilled_agent", "action"}),
                context_label,
            )
            if not isinstance(context.get("prefilled_agent"), str) or not context[
                "prefilled_agent"
            ]:
                raise MutationBatchValidationError(
                    f"{context_label}.prefilled_agent must be text"
                )
            _batch_nonnegative_integer(context.get("action"), f"{context_label}.action")
        else:  # pragma: no cover - candidate pool is frozen and exhaustive
            raise MutationBatchValidationError(
                f"{context_label} has an unregistered mutation operator"
            )
    if trigger_count > 0 and reference_run == mutant_run:
        raise MutationBatchValidationError(
            f"{label} claims a hook without a serialized run difference"
        )
    return trigger_count > 0


def _serialized_finding_codes(
    run: Mapping[str, Any],
    label: str,
) -> list[str]:
    codes: set[str] = set()
    for index, value in enumerate(
        _batch_array(run.get("violations"), f"{label}.violations")
    ):
        finding = _batch_object(value, f"{label}.violations[{index}]")
        code = finding.get("code")
        if not isinstance(code, str):
            raise MutationBatchValidationError(
                f"{label}.violations[{index}].code must be text"
            )
        codes.add(code)
    return sorted(codes)


def _validate_progress_control_pair(
    reference_run: Mapping[str, Any],
    corrupt_run: Mapping[str, Any],
    *,
    control: Any,
    label: str,
) -> int:
    """Re-derive the frozen progress-only transform from serialized events."""
    _batch_strict_equal(
        corrupt_run.get("source_events"),
        reference_run.get("source_events"),
        f"{label}.source_events",
    )
    _batch_strict_equal(
        corrupt_run.get("applicable"),
        reference_run.get("applicable"),
        f"{label}.applicable",
    )
    clean_events = _batch_array(
        reference_run.get("destination_events"),
        f"{label}.reference.destination_events",
    )
    corrupt_events = _batch_array(
        corrupt_run.get("destination_events"),
        f"{label}.corrupted.destination_events",
    )
    if not clean_events or len(clean_events) != len(corrupt_events):
        raise MutationBatchValidationError(
            f"{label} requires a nonempty paired destination ledger"
        )
    transform = progress_transform_for(control)
    triggers = 0
    for index, (raw_clean, raw_corrupt) in enumerate(
        zip(clean_events, corrupt_events, strict=True)
    ):
        event_label = f"{label}.destination_events[{index}]"
        clean = _batch_object(raw_clean, f"{event_label}.reference")
        corrupt = _batch_object(raw_corrupt, f"{event_label}.corrupted")
        for field in _DESTINATION_EVENT_KEYS - {"source_progress", "metadata"}:
            _batch_strict_equal(
                corrupt.get(field),
                clean.get(field),
                f"{event_label}.{field}",
            )
        clean_metadata = _batch_object(
            clean.get("metadata"),
            f"{event_label}.reference.metadata",
        )
        corrupt_metadata = _batch_object(
            corrupt.get("metadata"),
            f"{event_label}.corrupted.metadata",
        )
        if frozenset(clean_metadata) != frozenset(corrupt_metadata):
            raise MutationBatchValidationError(
                f"{event_label} metadata keys differ outside the control seam"
            )
        for field in frozenset(clean_metadata) - {"progress_instrumentation"}:
            _batch_strict_equal(
                corrupt_metadata.get(field),
                clean_metadata.get(field),
                f"{event_label}.metadata.{field}",
            )
        clean_instrumentation = _batch_object(
            clean_metadata.get("progress_instrumentation"),
            f"{event_label}.reference.progress_instrumentation",
        )
        corrupt_instrumentation = _batch_object(
            corrupt_metadata.get("progress_instrumentation"),
            f"{event_label}.corrupted.progress_instrumentation",
        )
        for field in _PROGRESS_INSTRUMENTATION_KEYS - {"annotated_progress_after"}:
            _batch_strict_equal(
                corrupt_instrumentation.get(field),
                clean_instrumentation.get(field),
                f"{event_label}.progress_instrumentation.{field}",
            )
        before = _batch_nonnegative_integer(
            clean_instrumentation.get("progress_before"),
            f"{event_label}.progress_before",
        )
        after = _batch_nonnegative_integer(
            clean_instrumentation.get("progress_after"),
            f"{event_label}.progress_after",
        )
        expected_annotation = transform(index, before, after)
        _batch_strict_equal(
            clean.get("source_progress"),
            after,
            f"{event_label}.reference.source_progress",
        )
        _batch_strict_equal(
            corrupt.get("source_progress"),
            expected_annotation,
            f"{event_label}.corrupted.source_progress",
        )
        _batch_strict_equal(
            corrupt_instrumentation.get("annotated_progress_after"),
            expected_annotation,
            f"{event_label}.corrupted.annotated_progress_after",
        )
        if expected_annotation != after:
            triggers += 1
    clean_summary = dict(
        _batch_object(reference_run.get("summary"), f"{label}.reference.summary")
    )
    corrupt_summary = dict(
        _batch_object(corrupt_run.get("summary"), f"{label}.corrupted.summary")
    )
    for summary in (clean_summary, corrupt_summary):
        summary.pop("progress_annotation_control_id", None)
        summary.pop("violation_count", None)
    _batch_strict_equal(
        corrupt_summary,
        clean_summary,
        f"{label}.summary outside control identity",
    )
    if triggers < 1:
        raise MutationBatchValidationError(
            f"{label} progress transform did not trigger on any destination event"
        )
    return triggers


def _validate_stock_result(
    value: Any,
    *,
    candidate: Mapping[str, Any],
    label: str,
) -> Mapping[str, Any]:
    result = _batch_object(value, label)
    _batch_exact_keys(result, _STOCK_RESULT_KEYS, label)
    cycles = _batch_nonnegative_integer(result.get("cycles"), f"{label}.cycles")
    if (
        result.get("game_spec") != candidate.get("game_spec")
        or cycles != MUTATION_STOCK_API_CYCLES
    ):
        raise MutationBatchValidationError(
            f"{label} game or cycle count differs from sealed treatment"
        )
    passed = result.get("passed")
    if not isinstance(passed, bool):
        raise MutationBatchValidationError(f"{label}.passed must be boolean")
    exception = result.get("exception")
    if exception is not None and not isinstance(exception, str):
        raise MutationBatchValidationError(
            f"{label}.exception must be text or null"
        )
    if passed is not (exception is None):
        raise MutationBatchValidationError(
            f"{label} pass/exception state is inconsistent"
        )
    warnings = _batch_array(result.get("warnings"), f"{label}.warnings")
    if not all(isinstance(item, str) for item in warnings):
        raise MutationBatchValidationError(
            f"{label}.warnings must contain text"
        )
    if not isinstance(result.get("captured_output"), str):
        raise MutationBatchValidationError(
            f"{label}.captured_output must be text"
        )
    return result


def _serialized_finding_signatures(
    run: Mapping[str, Any],
    label: str,
) -> list[dict[str, Any]]:
    violations = _batch_array(run.get("violations"), f"{label}.violations")
    records: list[dict[str, Any]] = []
    for index, value in enumerate(violations):
        finding = _batch_object(value, f"{label}.violations[{index}]")
        _batch_exact_keys(
            finding,
            _VIOLATION_KEYS,
            f"{label}.violations[{index}]",
        )
        obligation = finding.get("obligation")
        if (
            not isinstance(obligation, str)
            or not isinstance(finding.get("code"), str)
            or not isinstance(finding.get("message"), str)
        ):
            raise MutationBatchValidationError(
                f"{label}.violations[{index}] identity fields must be text"
            )
        records.append(
            {
                "signature_sha256": _canonical_digest(finding),
                "category": (
                    "execution" if obligation == "trace_execution" else "semantic"
                ),
                "detector_phase": _finding_phase(finding),
                "finding": dict(finding),
            }
        )
    return records


def _expected_replacement_reasons(
    *,
    selected: bool,
    inputs: Mapping[str, Any],
    unique: bool,
    eligible: bool,
    selected_count_after: int,
) -> list[str]:
    if selected:
        return ["selected"]
    reasons: list[str] = []
    if not bool(inputs.get("base_eligible_before_uniqueness")):
        if inputs.get("reference_acceptable") is False:
            reasons.append("clean_reference_unacceptable")
        if inputs.get("hook_reached") is False:
            reasons.append("mutation_hook_not_reached")
        if inputs.get("adapter_behavior_changed") is False:
            reasons.append("adapter_behavior_unchanged")
        if not reasons:
            reasons.append("base_eligibility_failed_unspecified")
    if not unique:
        reasons.append("duplicate_behavior_delta")
    if eligible and selected_count_after >= MUTANTS_PER_FAMILY:
        reasons.append("family_quota_already_met")
    return reasons


def validate_mutation_batch(
    payload: Mapping[str, Any],
    *,
    require_reporting_complete: bool = False,
) -> dict[str, Any]:
    """Purely validate serialized batch evidence without loading any game.

    The function canonicalizes dataclass-bearing in-memory payloads first, so it
    validates both the object immediately before atomic writing and JSON loaded
    later by an analysis or manuscript build.
    """
    root_value = to_jsonable(payload)
    root = _batch_object(root_value, "mutation batch")
    expected_root_keys = _MUTATION_BATCH_KEYS | (
        frozenset({"self_validation"})
        if "self_validation" in root
        else frozenset()
    )
    _batch_exact_keys(root, expected_root_keys, "mutation batch")
    if (
        _batch_nonnegative_integer(
            root.get("schema_version"),
            "mutation batch schema_version",
        )
        != MUTATION_BATCH_SCHEMA_VERSION
        or root.get("artifact_type") != MUTATION_BATCH_ARTIFACT_TYPE
        or root.get("protocol_id") != MUTATION_PROTOCOL_ID
    ):
        raise MutationBatchValidationError("mutation batch identity is invalid")
    for field in (
        "study_manifest_sha256",
        "mutation_manifest_sha256",
        "source_tree_sha256",
        "uv_lock_sha256",
        "receipt_sha256",
    ):
        _batch_sha256(root.get(field), f"mutation batch {field}")
    for field in ("archive_identifier", "archive_published_at_utc"):
        if not isinstance(root.get(field), str) or not root[field]:
            raise MutationBatchValidationError(
                f"mutation batch {field} must be nonempty text"
            )
    _batch_object(root.get("runtime"), "mutation batch runtime")
    _batch_nonnegative_integer(root.get("elapsed_ns"), "mutation batch elapsed_ns")

    records = _batch_array(root.get("candidate_records"), "candidate_records")
    expected_candidates = list(candidate_manifest_records())
    if len(records) != len(expected_candidates):
        raise MutationBatchValidationError("candidate record count is not 48")

    selected_ids: dict[str, list[str]] = defaultdict(list)
    selected_behavior: dict[str, dict[str, str]] = defaultdict(dict)
    semantic_by_family: Counter[str] = Counter()
    crash_by_family: Counter[str] = Counter()
    killed_by_family: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    clean_alarm_pool: list[str] = []
    clean_alarm_selected: list[str] = []
    first_obligation: Counter[str] = Counter()
    first_phase: Counter[str] = Counter()
    ablation_counts: Counter[str] = Counter()
    stock_signals = 0

    for index, raw_record in enumerate(records):
        record = _batch_object(raw_record, f"candidate_records[{index}]")
        _batch_exact_keys(
            record,
            _CANDIDATE_RECORD_KEYS,
            f"candidate_records[{index}]",
        )
        candidate = _batch_object(record.get("candidate"), "candidate")
        _batch_strict_equal(
            candidate,
            expected_candidates[index],
            f"candidate_records[{index}].candidate",
        )
        candidate_id = str(candidate["candidate_id"])
        family = str(candidate["family"])
        reference = _batch_object(record.get("reference"), "reference")
        mutant = _batch_object(record.get("mutant"), "mutant")
        _batch_exact_keys(reference, _REFERENCE_RESULT_KEYS, "reference")
        _batch_exact_keys(mutant, _MUTANT_RESULT_KEYS, "mutant")
        reference_run = _validate_serialized_run(
            reference.get("run"),
            candidate=candidate,
            label=f"{candidate_id}.reference.run",
        )
        mutant_run = _validate_serialized_run(
            mutant.get("run"),
            candidate=candidate,
            label=f"{candidate_id}.mutant.run",
        )
        expected_adapter_classes = {
            "reference": "PairedReference_" + candidate_id.replace("-", "_"),
            "mutant": "SealedMutation_" + candidate_id.replace("-", "_"),
        }
        for run_role, serialized_run in (
            ("reference", reference_run),
            ("mutant", mutant_run),
        ):
            run_summary = _batch_object(
                serialized_run.get("summary"),
                f"{candidate_id}.{run_role}.summary",
            )
            if run_summary.get("adapter_class") != expected_adapter_classes[run_role]:
                raise MutationBatchValidationError(
                    f"{candidate_id} {run_role} adapter class differs"
                )
        hook_reached = _validate_mutation_evidence(
            record.get("mutation_evidence"),
            candidate=candidate,
            reference_run=reference_run,
            mutant_run=mutant_run,
            label=f"{candidate_id}.mutation_evidence",
        )
        reference_projection = _serialized_adapter_projection(
            reference_run,
            f"{candidate_id}.reference.run",
        )
        mutant_projection = _serialized_adapter_projection(
            mutant_run,
            f"{candidate_id}.mutant.run",
        )
        reference_projection_sha256 = _canonical_digest(reference_projection)
        mutant_projection_sha256 = _canonical_digest(mutant_projection)
        if (
            reference.get("adapter_projection_sha256")
            != reference_projection_sha256
            or mutant.get("adapter_projection_sha256")
            != mutant_projection_sha256
        ):
            raise MutationBatchValidationError(
                f"{candidate_id} adapter projection identity is stale"
            )
        behavior = _canonical_digest(
            {"reference": reference_projection, "mutant": mutant_projection}
        )
        behavior_changed = reference_projection_sha256 != mutant_projection_sha256
        reference_acceptable = _serialized_reference_acceptable(
            candidate,
            reference_run,
        )
        inputs = _batch_object(record.get("selection_inputs"), "selection_inputs")
        _batch_exact_keys(inputs, _SELECTION_INPUT_KEYS, "selection_inputs")
        _batch_sha256(
            inputs.get("behavior_delta_sha256"),
            f"{candidate_id} behavior-delta identity",
        )
        expected_inputs = {
            "reference_acceptable": reference_acceptable,
            "hook_reached": hook_reached,
            "adapter_behavior_changed": behavior_changed,
            "behavior_delta_sha256": behavior,
            "base_eligible_before_uniqueness": (
                reference_acceptable and hook_reached and behavior_changed
            ),
        }
        for field, expected in expected_inputs.items():
            _batch_strict_equal(
                inputs.get(field),
                expected,
                f"{candidate_id} selection input {field}",
            )
        expected_base = bool(expected_inputs["base_eligible_before_uniqueness"])
        if inputs.get("base_eligible_before_uniqueness") is not expected_base:
            raise MutationBatchValidationError(
                f"{candidate_id} base eligibility is inconsistent"
            )
        duplicate_origin = selected_behavior[family].get(behavior)
        unique = duplicate_origin is None
        eligible = expected_base and unique
        if (
            inputs.get("unique_behavior_among_prior_selected") is not unique
            or inputs.get("eligible") is not eligible
            or inputs.get("duplicate_behavior_of_candidate_id") != duplicate_origin
        ):
            raise MutationBatchValidationError(
                f"{candidate_id} uniqueness accounting is inconsistent"
            )
        selected = eligible and len(selected_ids[family]) < MUTANTS_PER_FAMILY
        _batch_boolean(record.get("selected"), f"{candidate_id}.selected")
        if record.get("selected") is not selected:
            raise MutationBatchValidationError(
                f"{candidate_id} selection differs from frozen replacement rule"
            )
        if selected:
            selected_ids[family].append(candidate_id)
            selected_behavior[family][behavior] = candidate_id

        attempt = _batch_object(record.get("replacement_attempt"), "replacement")
        _batch_exact_keys(attempt, _REPLACEMENT_ATTEMPT_KEYS, "replacement")
        reasons = _batch_array(attempt.get("reason_codes"), "replacement reasons")
        expected_reasons = _expected_replacement_reasons(
            selected=selected,
            inputs=inputs,
            unique=unique,
            eligible=eligible,
            selected_count_after=len(selected_ids[family]),
        )
        if attempt.get("attempted") is not True or reasons != expected_reasons:
            raise MutationBatchValidationError(
                f"{candidate_id} replacement reasons are incomplete"
        )
        reason_counts.update(str(reason) for reason in reasons)

        if reference.get("status") != classify_run_payload(reference_run).value:
            raise MutationBatchValidationError(
                f"{candidate_id} reference status is stale"
            )
        if mutant.get("status") != classify_run_payload(mutant_run).value:
            raise MutationBatchValidationError(
                f"{candidate_id} mutant status is stale"
            )
        if reference.get("finding_codes") != _serialized_finding_codes(
            reference_run,
            f"{candidate_id}.reference.run",
        ):
            raise MutationBatchValidationError(
                f"{candidate_id} reference finding codes are stale"
            )
        if mutant.get("finding_codes") != _serialized_finding_codes(
            mutant_run,
            f"{candidate_id}.mutant.run",
        ):
            raise MutationBatchValidationError(
                f"{candidate_id} mutant finding codes are stale"
            )
        _batch_nonnegative_integer(
            reference.get("elapsed_ns"),
            f"{candidate_id}.reference.elapsed_ns",
        )
        _batch_nonnegative_integer(
            mutant.get("elapsed_ns"),
            f"{candidate_id}.mutant.elapsed_ns",
        )
        reference_signatures = _serialized_finding_signatures(
            reference_run,
            f"{candidate_id}.reference.run",
        )
        mutant_signatures = _serialized_finding_signatures(
            mutant_run,
            f"{candidate_id}.mutant.run",
        )
        _batch_strict_equal(
            reference.get("finding_signatures"),
            reference_signatures,
            f"{candidate_id} reference finding signatures",
        )
        _batch_strict_equal(
            mutant.get("finding_signatures"),
            mutant_signatures,
            f"{candidate_id} mutant finding signatures",
        )
        reference_hashes = {item["signature_sha256"] for item in reference_signatures}
        new_signatures = [
            item
            for item in mutant_signatures
            if item["signature_sha256"] not in reference_hashes
        ]
        new_semantic = [
            item for item in new_signatures if item["category"] == "semantic"
        ]
        new_execution = [
            item for item in new_signatures if item["category"] == "execution"
        ]
        _batch_strict_equal(
            mutant.get("new_full_finding_signatures"),
            new_signatures,
            f"{candidate_id} new full finding signatures",
        )
        _batch_strict_equal(
            mutant.get("new_semantic_finding_signatures"),
            new_semantic,
            f"{candidate_id} new semantic finding signatures",
        )
        _batch_strict_equal(
            mutant.get("new_execution_finding_signatures"),
            new_execution,
            f"{candidate_id} new execution finding signatures",
        )

        kill = _batch_object(record.get("kill"), "kill")
        _batch_exact_keys(kill, _KILL_KEYS, "kill")
        semantic_kill = selected and bool(new_semantic)
        crash_kill = selected and not new_semantic and bool(new_execution)
        killed = semantic_kill or crash_kill
        expected_status = (
            "not_selected"
            if not selected
            else "semantic_kill"
            if semantic_kill
            else "crash_only_kill"
            if crash_kill
            else "survived"
        )
        first = new_signatures[0] if new_signatures else None
        expected_kill_fields = {
            "new_full_signature_count": len(new_signatures),
            "new_semantic_signature_count": len(new_semantic),
            "new_execution_signature_count": len(new_execution),
            "killed": killed,
            "semantic_kill": semantic_kill,
            "crash_only_kill": crash_kill,
            "status": expected_status,
            "first_detecting_signature_sha256": (
                None if first is None else first["signature_sha256"]
            ),
            "first_detecting_obligation": (
                None if first is None else first["finding"]["obligation"]
            ),
            "first_detecting_phase": (
                None if first is None else first["detector_phase"]
            ),
            "first_detection_order": "stable_mutant_finding_order",
        }
        _batch_strict_equal(
            kill,
            expected_kill_fields,
            f"{candidate_id} kill classification",
        )
        if semantic_kill:
            semantic_by_family[family] += 1
        if crash_kill:
            crash_by_family[family] += 1
        if killed:
            killed_by_family[family] += 1
            first_obligation[str(kill["first_detecting_obligation"])] += 1
            first_phase[str(kill["first_detecting_phase"])] += 1

        alarm = _batch_object(
            reference.get("clean_reference_alarm"),
            "clean_reference_alarm",
        )
        _batch_exact_keys(
            alarm,
            _CLEAN_REFERENCE_ALARM_KEYS,
            "clean_reference_alarm",
        )
        reference_semantic = sum(
            item["category"] == "semantic" for item in reference_signatures
        )
        reference_execution = sum(
            item["category"] == "execution" for item in reference_signatures
        )
        summary = _batch_object(reference_run.get("summary"), "reference.summary")
        capability = summary.get("unsupported_capability")
        accepted_unsupported = (
            isinstance(capability, Mapping)
            and capability.get("capability_id")
            == "openspiel_mean_field_distribution_update_v1"
            and summary.get("stop_reason") == "adapter_setup_failed"
        )
        unexpected_alarm = reference_semantic > 0 or (
            reference_execution > 0 and not accepted_unsupported
        )
        expected_alarm = {
            "semantic_finding_count": reference_semantic,
            "execution_finding_count": reference_execution,
            "semantic_alarm": reference_semantic > 0,
            "unexpected_execution_alarm": (
                reference_execution > 0 and not accepted_unsupported
            ),
            "accepted_typed_unsupported_capability": accepted_unsupported,
            "any_unexpected_alarm": unexpected_alarm,
        }
        _batch_strict_equal(
            alarm,
            expected_alarm,
            f"{candidate_id} clean-reference alarm",
        )
        if unexpected_alarm:
            clean_alarm_pool.append(candidate_id)
            if selected:
                clean_alarm_selected.append(candidate_id)

        ablations = _batch_object(record.get("ablation_signals"), "ablations")
        _batch_exact_keys(ablations, _ABLATION_SIGNAL_KEYS, "ablations")
        baseline_signals = _batch_object(
            ablations.get("project_baselines"),
            "project baselines",
        )
        _batch_exact_keys(
            baseline_signals,
            frozenset(BASELINE_NAMES),
            "project baselines",
        )
        reference_baseline_items = _batch_array(
            reference_run.get("baselines"),
            "reference baselines",
        )
        mutant_baseline_items = _batch_array(
            mutant_run.get("baselines"),
            "mutant baselines",
        )
        if (
            len(reference_baseline_items) != len(BASELINE_NAMES)
            or len(mutant_baseline_items) != len(BASELINE_NAMES)
        ):
            raise MutationBatchValidationError(
                f"{candidate_id} baseline panel count differs"
            )
        reference_baselines: dict[str, Mapping[str, Any]] = {}
        mutant_baselines: dict[str, Mapping[str, Any]] = {}
        for panel_label, items, destination in (
            ("reference", reference_baseline_items, reference_baselines),
            ("mutant", mutant_baseline_items, mutant_baselines),
        ):
            for baseline_index, item in enumerate(items):
                baseline = _batch_object(
                    item,
                    f"{panel_label} baselines[{baseline_index}]",
                )
                _batch_exact_keys(
                    baseline,
                    _BASELINE_RESULT_KEYS,
                    f"{panel_label} baselines[{baseline_index}]",
                )
                expected_name = BASELINE_NAMES[baseline_index]
                if baseline.get("baseline") != expected_name:
                    raise MutationBatchValidationError(
                        f"{candidate_id} {panel_label} baseline order differs"
                    )
                if not isinstance(baseline.get("applicable"), bool):
                    raise MutationBatchValidationError(
                        f"{candidate_id} {panel_label} baseline applicability "
                        "must be boolean"
                    )
                _batch_array(
                    baseline.get("findings"),
                    f"{panel_label} {expected_name} findings",
                )
                reason = baseline.get("reason")
                if reason is not None and not isinstance(reason, str):
                    raise MutationBatchValidationError(
                        f"{candidate_id} {panel_label} baseline reason is invalid"
                    )
                destination[expected_name] = baseline
        for name in BASELINE_NAMES:
            signal = _batch_object(baseline_signals.get(name), f"baseline {name}")
            _batch_exact_keys(signal, _BASELINE_SIGNAL_KEYS, f"baseline {name}")
            clean = _batch_object(reference_baselines.get(name), f"reference {name}")
            changed = _batch_object(mutant_baselines.get(name), f"mutant {name}")
            clean_hashes = sorted(
                {
                    _canonical_digest(item)
                    for item in _batch_array(clean.get("findings"), "findings")
                }
            )
            changed_hashes = sorted(
                {
                    _canonical_digest(item)
                    for item in _batch_array(changed.get("findings"), "findings")
                }
            )
            added = sorted(set(changed_hashes).difference(clean_hashes))
            expected_signal = {
                "reference_applicable": clean.get("applicable"),
                "mutant_applicable": changed.get("applicable"),
                "reference_finding_signatures": clean_hashes,
                "mutant_finding_signatures": changed_hashes,
                "added_finding_signatures": added,
                "paired_signal": bool(added),
            }
            _batch_strict_equal(
                signal,
                expected_signal,
                f"{candidate_id} paired {name} delta",
            )
            if selected and added:
                ablation_counts[name] += 1
        stock = _batch_object(
            ablations.get("stock_pettingzoo_api_test"),
            "stock api_test",
        )
        _batch_exact_keys(stock, _STOCK_SIGNAL_KEYS, "stock api_test")
        expected_stock_identity = {
            "reference_adapter_class": (
                "PairedReference_" + candidate_id.replace("-", "_")
            ),
            "mutant_adapter_class": (
                "SealedMutation_" + candidate_id.replace("-", "_")
            ),
            "interpretation": (
                "destination API-test failure introduced relative to the paired "
                "composite repaired reference"
            ),
        }
        for field, expected in expected_stock_identity.items():
            if stock.get(field) != expected:
                raise MutationBatchValidationError(
                    f"{candidate_id} stock api_test {field} differs"
                )
        stock_reference = _validate_stock_result(
            stock.get("reference"),
            candidate=candidate,
            label=f"{candidate_id}.stock.reference",
        )
        stock_mutant = _validate_stock_result(
            stock.get("mutant"),
            candidate=candidate,
            label=f"{candidate_id}.stock.mutant",
        )
        stock_signal = (
            stock_reference.get("passed") is True
            and stock_mutant.get("passed") is False
        )
        if stock.get("paired_signal") is not stock_signal:
            raise MutationBatchValidationError(
                f"{candidate_id} stock api_test delta is inconsistent"
            )
        if selected and stock_signal:
            stock_signals += 1

    selected_count_by_family = {
        family: len(selected_ids[family]) for family in MUTATION_FAMILIES
    }
    selection = _batch_object(root.get("selection"), "selection")
    _batch_exact_keys(selection, _SELECTION_KEYS, "selection")
    incomplete = {
        family: count
        for family, count in selected_count_by_family.items()
        if count != MUTANTS_PER_FAMILY
    }
    expected_selection = {
        "rule": MUTATION_REPLACEMENT_RULE,
        "selected_ids_by_family": dict(selected_ids),
        "selected_count_by_family": selected_count_by_family,
        "incomplete_families": incomplete,
        "complete": not incomplete,
    }
    _batch_strict_equal(selection, expected_selection, "aggregate selection")

    score = _batch_object(root.get("score"), "score")
    _batch_exact_keys(score, _SCORE_KEYS, "score")
    selected_total = sum(selected_count_by_family.values())
    expected_score_fields = {
        "selected_total": selected_total,
        "selected_count_by_family": selected_count_by_family,
        "killed_total": sum(killed_by_family.values()),
        "semantic_killed_total": sum(semantic_by_family.values()),
        "crash_only_killed_total": sum(crash_by_family.values()),
        "killed_by_family": {
            family: killed_by_family[family] for family in MUTATION_FAMILIES
        },
        "semantic_killed_by_family": {
            family: semantic_by_family[family] for family in MUTATION_FAMILIES
        },
        "crash_only_killed_by_family": {
            family: crash_by_family[family] for family in MUTATION_FAMILIES
        },
        "paired_ablation_signal_counts": {
            "project_baselines": {
                name: ablation_counts[name] for name in BASELINE_NAMES
            },
            "stock_pettingzoo_api_test": stock_signals,
        },
        "clean_reference_alarms": {
            "selected_alarm_count": len(clean_alarm_selected),
            "selected_candidate_ids": clean_alarm_selected,
            "attempted_pool_alarm_count": len(clean_alarm_pool),
            "attempted_pool_candidate_ids": clean_alarm_pool,
        },
        "first_detection_counts": {
            "by_obligation": dict(sorted(first_obligation.items())),
            "by_phase": dict(sorted(first_phase.items())),
        },
        "replacement_reason_counts": dict(sorted(reason_counts.items())),
        "interpretation": _MUTATION_SCORE_INTERPRETATION,
    }
    for key, expected in expected_score_fields.items():
        _batch_strict_equal(score.get(key), expected, f"score.{key}")

    controls = _batch_object(
        root.get("progress_instrumentation_controls"),
        "progress controls",
    )
    _batch_exact_keys(controls, _CONTROL_PANEL_KEYS, "progress controls")
    control_records = _batch_array(controls.get("records"), "control records")
    if len(control_records) != len(PROGRESS_INSTRUMENTATION_CONTROLS):
        raise MutationBatchValidationError("progress control count is invalid")
    detected_count = 0
    for index, raw_control in enumerate(control_records):
        record = _batch_object(raw_control, f"control_records[{index}]")
        _batch_exact_keys(
            record,
            _CONTROL_RECORD_KEYS,
            f"control_records[{index}]",
        )
        expected_control = PROGRESS_INSTRUMENTATION_CONTROLS[index].to_manifest_record()
        _batch_strict_equal(
            record.get("control"),
            expected_control,
            f"control_records[{index}].control",
        )
        control_reference = _validate_serialized_run(
            record.get("reference"),
            candidate=expected_control,
            label=f"control_records[{index}].reference",
        )
        control_corrupt = _validate_serialized_run(
            record.get("corrupted"),
            candidate=expected_control,
            label=f"control_records[{index}].corrupted",
        )
        reference_summary = _batch_object(
            control_reference.get("summary"),
            f"control_records[{index}].reference.summary",
        )
        corrupt_summary = _batch_object(
            control_corrupt.get("summary"),
            f"control_records[{index}].corrupted.summary",
        )
        if (
            reference_summary.get("progress_annotation_control_id") is not None
            or corrupt_summary.get("progress_annotation_control_id")
            != expected_control["control_id"]
            or reference_summary.get("adapter_class") != "CombinedRepairV0"
            or corrupt_summary.get("adapter_class") != "CombinedRepairV0"
        ):
            raise MutationBatchValidationError(
                "progress control run identity is inconsistent"
            )
        _validate_progress_control_pair(
            control_reference,
            control_corrupt,
            control=PROGRESS_INSTRUMENTATION_CONTROLS[index],
            label=f"control_records[{index}]",
        )
        clean_signatures = _serialized_finding_signatures(
            control_reference,
            "control reference",
        )
        corrupt_signatures = _serialized_finding_signatures(
            control_corrupt,
            "control corrupted",
        )
        clean_hashes = {item["signature_sha256"] for item in clean_signatures}
        delta = [
            item
            for item in corrupt_signatures
            if item["signature_sha256"] not in clean_hashes
        ]
        detected = any(
            (
                item["finding"]["obligation"],
                item["finding"]["code"],
            )
            == PROGRESS_CONTROL_REQUIRED_FINDING
            for item in delta
        )
        _batch_strict_equal(
            record.get("new_full_finding_signatures"),
            delta,
            f"control_records[{index}].new_full_finding_signatures",
        )
        _batch_boolean(record.get("detected"), f"control_records[{index}].detected")
        _batch_boolean(
            record.get("included_in_24_mutant_denominator"),
            f"control_records[{index}].included_in_24_mutant_denominator",
        )
        if (
            record.get("detected") is not detected
            or record.get("included_in_24_mutant_denominator") is not False
        ):
            raise MutationBatchValidationError(
                "progress control sensitivity accounting is inconsistent"
            )
        detected_count += detected
    _batch_boolean(
        controls.get("included_in_24_mutant_denominator"),
        "progress controls included_in_24_mutant_denominator",
    )
    required_count = _batch_nonnegative_integer(
        controls.get("required_count"),
        "progress controls required_count",
    )
    stored_detected_count = _batch_nonnegative_integer(
        controls.get("detected_count"),
        "progress controls detected_count",
    )
    if controls.get("included_in_24_mutant_denominator") is not False or (
        required_count != len(PROGRESS_INSTRUMENTATION_CONTROLS)
        or stored_detected_count != detected_count
    ):
        raise MutationBatchValidationError("progress control aggregate is inconsistent")

    cohort_complete_24 = not incomplete and selected_total == (
        len(MUTATION_FAMILIES) * MUTANTS_PER_FAMILY
    )
    progress_controls_satisfied = detected_count == len(
        PROGRESS_INSTRUMENTATION_CONTROLS
    )
    # Reaching this point proves the complete ordered 48-candidate attempt
    # ledger was present and internally derived.  A short selected denominator
    # is reportable under Gate 2, but cannot satisfy Gate 3.
    rq6_reportable = progress_controls_satisfied
    rq6_reporting_reasons = (
        [] if progress_controls_satisfied else ["progress_control_not_detected"]
    )
    strong_performance_reasons = [*rq6_reporting_reasons]
    if not cohort_complete_24:
        strong_performance_reasons.append("incomplete_24_mutant_cohort")
    semantic_total = sum(semantic_by_family.values())
    if semantic_total < 20:
        strong_performance_reasons.append("semantic_kills_below_20_of_24")
    strong_performance_reasons.extend(
        f"{family}_semantic_kills_below_3_of_4"
        for family in MUTATION_FAMILIES
        if semantic_by_family[family] < 3
    )
    strong_performance_threshold_met = not strong_performance_reasons
    strong_claim_reasons = list(strong_performance_reasons)
    strong_sensitivity_claim_ready = not strong_claim_reasons
    reporting_warnings = []
    if not cohort_complete_24:
        reporting_warnings.append("short_selected_mutation_denominator")
    if clean_alarm_selected:
        reporting_warnings.append("selected_clean_reference_alarm")
    report = {
        "validator_id": "marlrefine_mutation_batch_validator_v3",
        "structurally_valid": True,
        "candidate_attempt_ledger_complete": True,
        "selected_total": selected_total,
        "selected_count_by_family": selected_count_by_family,
        "progress_controls_detected": detected_count,
        "selected_clean_reference_alarm_count": len(clean_alarm_selected),
        "cohort_complete_24": cohort_complete_24,
        "progress_controls_satisfied": progress_controls_satisfied,
        "rq6_reportable": rq6_reportable,
        "rq6_reporting_reasons": rq6_reporting_reasons,
        "strong_performance_threshold_met": strong_performance_threshold_met,
        "strong_performance_threshold_reasons": strong_performance_reasons,
        "strong_sensitivity_claim_ready": strong_sensitivity_claim_ready,
        "strong_sensitivity_claim_reasons": strong_claim_reasons,
        # Scientifically unambiguous compatibility aliases introduced with v3.
        "reporting_complete": rq6_reportable,
        "reporting_completeness_reasons": rq6_reporting_reasons,
        "strong_sensitivity_threshold_met": strong_performance_threshold_met,
        "strong_sensitivity_threshold_reasons": strong_performance_reasons,
        "reporting_warnings": reporting_warnings,
    }
    existing_report = root.get("self_validation")
    if existing_report is not None and existing_report != report:
        raise MutationBatchValidationError("embedded self-validation report is stale")
    if require_reporting_complete and not rq6_reportable:
        raise MutationBatchValidationError(
            "mutation batch is structurally valid but reporting-incomplete: "
            + ", ".join(report["reporting_completeness_reasons"])
        )
    return report


def execute_mutation_study(
    study_manifest_path: Path,
    mutation_manifest_path: Path,
    receipt_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Run all sealed candidates, then apply outcome-blind replacement order."""
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite {output_path}")
    primary_plan = build_prospective_plan(study_manifest_path, receipt_path)
    mutation_manifest, mutation_manifest_sha256 = (
        _verify_mutation_manifest_with_sha256(
            mutation_manifest_path,
            primary_plan.gate.manifest,
            source_tree_sha256=primary_plan.gate.source_tree_sha256,
        )
    )

    started = perf_counter_ns()
    records = [_candidate_record(candidate) for candidate in CANDIDATE_POOL]
    selected_ids, selected_by_family = _apply_outcome_blind_selection(records)
    instrumentation_controls = [
        _instrumentation_control_record(control)
        for control in PROGRESS_INSTRUMENTATION_CONTROLS
    ]

    incomplete = {
        family: len(selected_ids[family])
        for family in MUTATION_FAMILIES
        if len(selected_ids[family]) != MUTANTS_PER_FAMILY
    }
    selected_records = [record for record in records if record["selected"]]
    killed_by_family: Counter[str] = Counter(
        str(record["candidate"]["family"])
        for record in selected_records
        if record["kill"]["killed"] is True
    )
    semantic_killed_by_family: Counter[str] = Counter(
        str(record["candidate"]["family"])
        for record in selected_records
        if record["kill"]["semantic_kill"] is True
    )
    crash_only_by_family: Counter[str] = Counter(
        str(record["candidate"]["family"])
        for record in selected_records
        if record["kill"]["crash_only_kill"] is True
    )
    ablation_kills: dict[str, int] = {}
    for name in BASELINE_NAMES:
        ablation_kills[name] = sum(
            record["ablation_signals"]["project_baselines"][name]["paired_signal"]
            is True
            for record in selected_records
        )
    stock_api_signals = sum(
        record["ablation_signals"]["stock_pettingzoo_api_test"]["paired_signal"] is True
        for record in selected_records
    )
    replacement_reason_counts: Counter[str] = Counter(
        reason
        for record in records
        for reason in record["replacement_attempt"]["reason_codes"]
    )
    selected_reference_alarm_ids = tuple(
        str(record["candidate"]["candidate_id"])
        for record in selected_records
        if record["reference"]["clean_reference_alarm"]["any_unexpected_alarm"] is True
    )
    pool_reference_alarm_ids = tuple(
        str(record["candidate"]["candidate_id"])
        for record in records
        if record["reference"]["clean_reference_alarm"]["any_unexpected_alarm"] is True
    )
    first_obligation_counts: Counter[str] = Counter(
        str(record["kill"]["first_detecting_obligation"])
        for record in selected_records
        if record["kill"]["killed"] is True
    )
    first_phase_counts: Counter[str] = Counter(
        str(record["kill"]["first_detecting_phase"])
        for record in selected_records
        if record["kill"]["killed"] is True
    )

    payload = {
        "schema_version": MUTATION_BATCH_SCHEMA_VERSION,
        "artifact_type": MUTATION_BATCH_ARTIFACT_TYPE,
        "protocol_id": MUTATION_PROTOCOL_ID,
        "study_manifest_sha256": primary_plan.gate.manifest_sha256,
        "mutation_manifest_sha256": mutation_manifest_sha256,
        "archive_identifier": primary_plan.gate.archive_identifier,
        "archive_published_at_utc": primary_plan.gate.published_at_utc,
        "source_tree_sha256": primary_plan.gate.source_tree_sha256,
        "uv_lock_sha256": primary_plan.gate.uv_lock_sha256,
        "receipt_sha256": primary_plan.gate.receipt_sha256,
        "runtime": runtime_provenance(),
        "selection": {
            "rule": mutation_manifest["selection"]["replacement_rule"],
            "selected_ids_by_family": dict(selected_ids),
            "selected_count_by_family": selected_by_family,
            "incomplete_families": incomplete,
            "complete": not incomplete,
        },
        "score": {
            "selected_total": len(selected_records),
            "selected_count_by_family": selected_by_family,
            "killed_total": sum(
                record["kill"]["killed"] is True for record in selected_records
            ),
            "semantic_killed_total": sum(
                record["kill"]["semantic_kill"] is True for record in selected_records
            ),
            "crash_only_killed_total": sum(
                record["kill"]["crash_only_kill"] is True for record in selected_records
            ),
            "killed_by_family": {
                family: killed_by_family[family] for family in MUTATION_FAMILIES
            },
            "semantic_killed_by_family": {
                family: semantic_killed_by_family[family]
                for family in MUTATION_FAMILIES
            },
            "crash_only_killed_by_family": {
                family: crash_only_by_family[family] for family in MUTATION_FAMILIES
            },
            "paired_ablation_signal_counts": {
                "project_baselines": ablation_kills,
                "stock_pettingzoo_api_test": stock_api_signals,
            },
            "clean_reference_alarms": {
                "selected_alarm_count": len(selected_reference_alarm_ids),
                "selected_candidate_ids": selected_reference_alarm_ids,
                "attempted_pool_alarm_count": len(pool_reference_alarm_ids),
                "attempted_pool_candidate_ids": pool_reference_alarm_ids,
            },
            "first_detection_counts": {
                "by_obligation": dict(sorted(first_obligation_counts.items())),
                "by_phase": dict(sorted(first_phase_counts.items())),
            },
            "replacement_reason_counts": dict(
                sorted(replacement_reason_counts.items())
            ),
            "interpretation": _MUTATION_SCORE_INTERPRETATION,
        },
        "candidate_records": records,
        "progress_instrumentation_controls": {
            "included_in_24_mutant_denominator": False,
            "required_count": len(PROGRESS_INSTRUMENTATION_CONTROLS),
            "detected_count": sum(
                record["detected"] is True for record in instrumentation_controls
            ),
            "records": instrumentation_controls,
        },
        "elapsed_ns": perf_counter_ns() - started,
    }
    payload["self_validation"] = validate_mutation_batch(payload)
    validate_mutation_batch(payload)
    write_json(output_path, payload)
    return payload
