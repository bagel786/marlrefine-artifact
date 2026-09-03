"""Frozen O1--O8 applicability and exercised-site accounting.

This module adds measurement only.  It does not participate in the primary
five-status classifier and cannot change a trace violation verdict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from marlrefine.model import (
    Alignment,
    EvaluationOutcome,
    ObligationEvaluation,
    Violation,
)

OBLIGATION_LEDGER_SCHEMA_ID = "marlrefine_obligation_ledger_v1"
OBLIGATION_IDS = ("O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8")
OBLIGATION_EVALUATION_KEYS = frozenset(
    {
        "obligation_id",
        "applicable",
        "evaluated",
        "outcome",
        "reason_code",
        "evaluation_count",
        "finding_indices",
    }
)

_OBSERVED_REASONS = {
    "O1": "buffer_microstep_observed",
    "O2": "buffer_or_cleanup_microstep_observed",
    "O3": "aligned_transition_or_completed_return_observed",
    "O4": "adapter_clock_comparison_observed",
    "O5": "boundary_or_cleanup_lifecycle_observed",
    "O6": "caller_supplied_nondefault_configuration",
    "O7": "source_state_kind_boundary_observed",
    "O8": "adapter_interface_boundary_observed",
}


def _is_buffer_destination_index(alignment: Alignment, index: int) -> bool:
    if index < 0 or index >= len(alignment.destination_events):
        return False
    event = alignment.destination_events[index]
    return not event.cleanup and event.metadata.get("buffer_only") is True


def _is_strong_buffer_destination_index(alignment: Alignment, index: int) -> bool:
    if not _is_buffer_destination_index(alignment, index):
        return False
    strength = alignment.destination_events[index].metadata.get(
        "state_oracle_strength",
        "strong",
    )
    return strength == "strong"


def _linked_obligation_ids(
    violation: Violation,
    *,
    alignment: Alignment,
    caller_supplied_nondefault: bool | None,
) -> tuple[str, ...]:
    """Map one contextual finding to the published O1--O8 study obligations."""
    ids: set[str] = set()
    family = violation.obligation
    code = violation.code

    if family == "stutter_reward_neutrality":
        if (
            violation.destination_span is not None
            and len(violation.destination_span) == 1
            and _is_buffer_destination_index(
                alignment, violation.destination_span.start
            )
        ):
            ids.add("O2")
    elif family == "segment_reward_conservation":
        ids.add("O3")
    elif family == "terminal_cleanup_reward_neutrality":
        ids.update(("O2", "O5"))
    elif family == "boundary_lifecycle_preservation":
        ids.add("O5")
    elif family == "delivered_reward_conservation":
        ids.add("O3")
    elif family == "decision_clock_preservation" and code == (
        "source_decision_clock_mismatch"
    ):
        ids.add("O4")
    elif family == "lifecycle_preservation":
        ids.add("O5")
    elif family == "state_kind_soundness":
        ids.add("O7")
    elif family == "interface_projection":
        ids.add("O8")

    if code == "premature_adapter_truncation":
        ids.add("O5")
    if code == "adapter_requests_action_at_nondecision_node":
        ids.add("O7")
    if code == "submitted_action_mismatch":
        ids.add("O8")
    if code == "player_count_changed_on_reset":
        ids.add("O8")
    if code == "parameters_changed_on_reset" and caller_supplied_nondefault:
        ids.add("O6")
    contextual_values = (violation.expected, violation.observed)
    explicit_buffer_attempt = any(
        isinstance(value, Mapping) and value.get("buffer_only_attempt") is True
        for value in contextual_values
    )
    if family == "state_projection":
        buffered_event = (
            violation.destination_span is not None
            and len(violation.destination_span) == 1
            and _is_buffer_destination_index(
                alignment, violation.destination_span.start
            )
        )
        if buffered_event or explicit_buffer_attempt:
            ids.add("O1")

    return tuple(item for item in OBLIGATION_IDS if item in ids)


def _serialized_buffer_destination_index(
    alignment: Mapping[str, Any],
    index: int,
) -> bool:
    destination_events = alignment.get("destination_events")
    if not isinstance(destination_events, list) or not 0 <= index < len(
        destination_events
    ):
        return False
    event = destination_events[index]
    if not isinstance(event, Mapping) or event.get("cleanup") is not False:
        return False
    metadata = event.get("metadata")
    return isinstance(metadata, Mapping) and metadata.get("buffer_only") is True


def _serialized_strong_buffer_destination_index(
    alignment: Mapping[str, Any],
    index: int,
) -> bool:
    if not _serialized_buffer_destination_index(alignment, index):
        return False
    event = alignment["destination_events"][index]
    metadata = event["metadata"]
    return metadata.get("state_oracle_strength", "strong") == "strong"


def _linked_serialized_obligation_ids(
    violation: Mapping[str, Any],
    *,
    alignment: Mapping[str, Any],
    caller_supplied_nondefault: bool | None,
) -> tuple[str, ...]:
    """Mirror the frozen typed mapping for a decoded artifact."""
    ids: set[str] = set()
    family = violation.get("obligation")
    code = violation.get("code")

    if family == "stutter_reward_neutrality":
        destination_span = violation.get("destination_span")
        if isinstance(destination_span, Mapping):
            start = destination_span.get("start")
            stop = destination_span.get("stop")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(stop, int)
                and not isinstance(stop, bool)
                and stop == start + 1
                and _serialized_buffer_destination_index(alignment, start)
            ):
                ids.add("O2")
    elif family == "segment_reward_conservation":
        ids.add("O3")
    elif family == "terminal_cleanup_reward_neutrality":
        ids.update(("O2", "O5"))
    elif family == "boundary_lifecycle_preservation":
        ids.add("O5")
    elif family == "delivered_reward_conservation":
        ids.add("O3")
    elif family == "decision_clock_preservation" and code == (
        "source_decision_clock_mismatch"
    ):
        ids.add("O4")
    elif family == "lifecycle_preservation":
        ids.add("O5")
    elif family == "state_kind_soundness":
        ids.add("O7")
    elif family == "interface_projection":
        ids.add("O8")

    if code == "premature_adapter_truncation":
        ids.add("O5")
    if code == "adapter_requests_action_at_nondecision_node":
        ids.add("O7")
    if code == "submitted_action_mismatch":
        ids.add("O8")
    if code == "player_count_changed_on_reset":
        ids.add("O8")
    if code == "parameters_changed_on_reset" and caller_supplied_nondefault:
        ids.add("O6")
    destination_span = violation.get("destination_span")
    contextual_values = (violation.get("expected"), violation.get("observed"))
    explicit_buffer_attempt = any(
        isinstance(value, Mapping) and value.get("buffer_only_attempt") is True
        for value in contextual_values
    )
    if family == "state_projection" and isinstance(destination_span, Mapping):
        start = destination_span.get("start")
        stop = destination_span.get("stop")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(stop, int)
            and not isinstance(stop, bool)
            and stop == start + 1
            and _serialized_buffer_destination_index(alignment, start)
        ):
            ids.add("O1")
    if family == "state_projection" and explicit_buffer_attempt:
        ids.add("O1")

    return tuple(item for item in OBLIGATION_IDS if item in ids)


def build_obligation_evaluations(
    alignment: Alignment,
    violations: Sequence[Violation],
    *,
    caller_supplied_nondefault: bool | None,
    configuration_evaluated: bool,
    state_kind_evaluation_count: int,
    interface_evaluation_count: int,
    complete_episode: bool,
    unresolved: str,
) -> tuple[ObligationEvaluation, ...]:
    """Build the exact eight-row ledger without changing semantic verdicts.

    ``unresolved`` controls only zero-site rows and must be ``not_evaluated``,
    ``not_applicable``, or ``no_applicable_site``.
    """
    if unresolved not in {"not_evaluated", "not_applicable", "no_applicable_site"}:
        raise ValueError(f"unsupported unresolved evaluation state: {unresolved!r}")
    if state_kind_evaluation_count < 0 or interface_evaluation_count < 0:
        raise ValueError("evaluation counts must be non-negative")

    buffer_count = sum(
        _is_buffer_destination_index(alignment, index)
        for index in range(len(alignment.destination_events))
    )
    strong_buffer_count = sum(
        _is_strong_buffer_destination_index(alignment, index)
        for index in range(len(alignment.destination_events))
    )
    cleanup_count = sum(event.cleanup for event in alignment.destination_events)
    transition_count = len(alignment.transition_segments)
    counts = {
        "O1": strong_buffer_count,
        "O2": buffer_count + cleanup_count,
        "O3": transition_count + int(complete_episode),
        "O4": len(alignment.destination_events),
        "O5": transition_count + cleanup_count,
        "O6": int(bool(caller_supplied_nondefault and configuration_evaluated)),
        "O7": state_kind_evaluation_count,
        "O8": interface_evaluation_count,
    }
    linked: dict[str, list[int]] = {
        obligation_id: [] for obligation_id in OBLIGATION_IDS
    }
    pre_event_sites: dict[str, set[int]] = {
        obligation_id: set() for obligation_id in ("O1", "O5", "O8")
    }
    for index, violation in enumerate(violations):
        obligation_ids = _linked_obligation_ids(
            violation,
            alignment=alignment,
            caller_supplied_nondefault=caller_supplied_nondefault,
        )
        for obligation_id in obligation_ids:
            linked[obligation_id].append(index)
        span = violation.destination_span
        if (
            span is not None
            and len(span) == 0
            and span.start >= len(alignment.destination_events)
        ):
            interface_already_evaluated = any(
                isinstance(context, Mapping)
                and context.get("interface_boundary_evaluated") is True
                for context in (violation.expected, violation.observed)
            )
            for obligation_id in obligation_ids:
                if obligation_id in pre_event_sites and not (
                    obligation_id == "O8" and interface_already_evaluated
                ):
                    pre_event_sites[obligation_id].add(span.start)

    for obligation_id, sites in pre_event_sites.items():
        counts[obligation_id] += len(sites)

    # A pre-call finding is itself evidence that one applicability site was
    # evaluated even when no destination event could be appended.
    for obligation_id, indices in linked.items():
        if indices and counts[obligation_id] == 0:
            counts[obligation_id] = 1

    evaluations: list[ObligationEvaluation] = []
    for obligation_id in OBLIGATION_IDS:
        count = counts[obligation_id]
        finding_indices = tuple(linked[obligation_id])
        if count:
            outcome = (
                EvaluationOutcome.EVALUATED_FAIL
                if finding_indices
                else EvaluationOutcome.EVALUATED_PASS
            )
            evaluations.append(
                ObligationEvaluation(
                    obligation_id=obligation_id,
                    applicable=True,
                    evaluated=True,
                    outcome=outcome,
                    reason_code=_OBSERVED_REASONS[obligation_id],
                    evaluation_count=count,
                    finding_indices=finding_indices,
                )
            )
            continue

        if obligation_id == "O1" and buffer_count and not strong_buffer_count:
            evaluations.append(
                ObligationEvaluation(
                    obligation_id=obligation_id,
                    applicable=True,
                    evaluated=False,
                    outcome=EvaluationOutcome.NOT_EVALUATED,
                    reason_code="weak_state_identity_only",
                    evaluation_count=0,
                )
            )
        elif obligation_id == "O6" and caller_supplied_nondefault is False:
            evaluations.append(
                ObligationEvaluation(
                    obligation_id=obligation_id,
                    applicable=False,
                    evaluated=False,
                    outcome=EvaluationOutcome.NOT_APPLICABLE,
                    reason_code="default_configuration_only",
                    evaluation_count=0,
                )
            )
        elif unresolved in {"not_applicable", "no_applicable_site"}:
            evaluations.append(
                ObligationEvaluation(
                    obligation_id=obligation_id,
                    applicable=False,
                    evaluated=False,
                    outcome=EvaluationOutcome.NOT_APPLICABLE,
                    reason_code=(
                        "trace_globally_inapplicable"
                        if unresolved == "not_applicable"
                        else "no_applicable_site_observed"
                    ),
                    evaluation_count=0,
                )
            )
        else:
            evaluations.append(
                ObligationEvaluation(
                    obligation_id=obligation_id,
                    applicable=None,
                    evaluated=False,
                    outcome=EvaluationOutcome.NOT_EVALUATED,
                    reason_code="trace_stopped_before_applicability_determined",
                    evaluation_count=0,
                )
            )
    return tuple(evaluations)


def validate_serialized_obligation_evaluations(
    value: Any,
    *,
    violations: Any,
    alignment: Any,
    summary: Any,
    caller_supplied_nondefault: bool | None,
    label: str,
) -> tuple[Mapping[str, Any], ...]:
    """Validate decoded JSON rows and return them in their frozen order."""
    if not isinstance(violations, list) or not all(
        isinstance(item, Mapping) for item in violations
    ):
        raise ValueError(f"{label} cannot be checked against malformed findings")
    if not isinstance(alignment, Mapping):
        raise ValueError(f"{label} cannot be checked against malformed alignment")
    if not isinstance(summary, Mapping):
        raise ValueError(f"{label} cannot be checked against malformed summary")
    if caller_supplied_nondefault is not None and not isinstance(
        caller_supplied_nondefault, bool
    ):
        raise ValueError(f"{label} caller configuration flag is invalid")
    if not isinstance(value, list) or len(value) != len(OBLIGATION_IDS):
        raise ValueError(f"{label} must contain exactly eight rows")
    rows: list[Mapping[str, Any]] = []
    for index, (raw, expected_id) in enumerate(zip(value, OBLIGATION_IDS, strict=True)):
        row_label = f"{label}[{index}]"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{row_label} must be an object")
        if set(raw) != OBLIGATION_EVALUATION_KEYS:
            raise ValueError(f"{row_label} schema differs")
        if raw.get("obligation_id") != expected_id:
            raise ValueError(f"{row_label}.obligation_id is out of order")
        applicable = raw.get("applicable")
        evaluated = raw.get("evaluated")
        reason_code = raw.get("reason_code")
        evaluation_count = raw.get("evaluation_count")
        finding_indices = raw.get("finding_indices")
        if applicable is not None and not isinstance(applicable, bool):
            raise ValueError(f"{row_label}.applicable is invalid")
        if not isinstance(evaluated, bool):
            raise ValueError(f"{row_label}.evaluated is invalid")
        if not isinstance(reason_code, str) or not reason_code:
            raise ValueError(f"{row_label}.reason_code is invalid")
        if (
            not isinstance(evaluation_count, int)
            or isinstance(evaluation_count, bool)
            or evaluation_count < 0
        ):
            raise ValueError(f"{row_label}.evaluation_count is invalid")
        if not isinstance(finding_indices, list) or any(
            not isinstance(item, int)
            or isinstance(item, bool)
            or item < 0
            or item >= len(violations)
            for item in finding_indices
        ):
            raise ValueError(f"{row_label}.finding_indices is invalid")
        if finding_indices != sorted(set(finding_indices)):
            raise ValueError(f"{row_label}.finding_indices must be sorted and unique")
        try:
            evaluation = ObligationEvaluation(
                obligation_id=expected_id,
                applicable=applicable,
                evaluated=evaluated,
                outcome=EvaluationOutcome(raw.get("outcome")),
                reason_code=reason_code,
                evaluation_count=evaluation_count,
                finding_indices=tuple(finding_indices),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{row_label} is inconsistent: {exc}") from exc
        if evaluation.evaluated:
            if reason_code != _OBSERVED_REASONS[expected_id]:
                raise ValueError(f"{row_label}.reason_code is not canonical")
        elif evaluation.outcome is EvaluationOutcome.NOT_EVALUATED:
            allowed_not_evaluated = {"trace_stopped_before_applicability_determined"}
            if expected_id == "O1":
                allowed_not_evaluated.add("weak_state_identity_only")
            if reason_code not in allowed_not_evaluated:
                raise ValueError(f"{row_label}.reason_code is not canonical")
        else:
            allowed_not_applicable = {
                "trace_globally_inapplicable",
                "no_applicable_site_observed",
            }
            if expected_id == "O6":
                allowed_not_applicable.add("default_configuration_only")
            if reason_code not in allowed_not_applicable:
                raise ValueError(f"{row_label}.reason_code is not canonical")
        if evaluation.obligation_id != expected_id:  # pragma: no cover - defensive
            raise ValueError(f"{row_label}.obligation_id differs")
        rows.append(raw)

    expected_indices: dict[str, list[int]] = {
        obligation_id: [] for obligation_id in OBLIGATION_IDS
    }
    for finding_index, violation in enumerate(violations):
        for obligation_id in _linked_serialized_obligation_ids(
            violation,
            alignment=alignment,
            caller_supplied_nondefault=caller_supplied_nondefault,
        ):
            expected_indices[obligation_id].append(finding_index)
    for row in rows:
        obligation_id = str(row["obligation_id"])
        if row["finding_indices"] != expected_indices[obligation_id]:
            raise ValueError(
                f"{label} finding references differ for {obligation_id}"
            )

    source_events = alignment.get("source_events")
    destination_events = alignment.get("destination_events")
    segments = alignment.get("segments")
    if (
        not isinstance(source_events, list)
        or not isinstance(destination_events, list)
        or not isinstance(segments, list)
        or not all(isinstance(item, Mapping) for item in source_events)
        or not all(isinstance(item, Mapping) for item in destination_events)
        or not all(isinstance(item, Mapping) for item in segments)
    ):
        raise ValueError(f"{label} alignment evidence is malformed")
    buffer_count = sum(
        _serialized_buffer_destination_index(alignment, index)
        for index in range(len(destination_events))
    )
    strong_buffer_count = sum(
        _serialized_strong_buffer_destination_index(alignment, index)
        for index in range(len(destination_events))
    )
    cleanup_count = sum(item.get("cleanup") is True for item in destination_events)
    transition_count = sum(item.get("kind") == "transition" for item in segments)
    complete_episode = (
        summary.get("stop_reason") == "destination_episode_end"
        and summary.get("source_terminal") is True
        and summary.get("adapter_agents_remaining") == 0
    )

    pre_event_sites: dict[str, set[int]] = {
        obligation_id: set() for obligation_id in ("O1", "O5", "O8")
    }
    attempted_interface_sites: set[int] = set()
    for finding_index, violation in enumerate(violations):
        destination_span = violation.get("destination_span")
        if not isinstance(destination_span, Mapping):
            continue
        start = destination_span.get("start")
        stop = destination_span.get("stop")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(stop, int)
            or isinstance(stop, bool)
            or start != stop
            or start < len(destination_events)
        ):
            continue
        interface_already_evaluated = any(
            isinstance(context, Mapping)
            and context.get("interface_boundary_evaluated") is True
            for context in (violation.get("expected"), violation.get("observed"))
        )
        for obligation_id, indices in expected_indices.items():
            if (
                finding_index in indices
                and obligation_id in pre_event_sites
                and not (
                    obligation_id == "O8" and interface_already_evaluated
                )
            ):
                pre_event_sites[obligation_id].add(start)
        if interface_already_evaluated:
            attempted_interface_sites.add(start)

    state_kind_boundaries: set[tuple[int, str]] = set()
    for event in source_events:
        progress = event.get("progress")
        metadata = event.get("metadata")
        if (
            isinstance(progress, int)
            and not isinstance(progress, bool)
            and isinstance(metadata, Mapping)
            and isinstance(metadata.get("node_kind_before"), str)
        ):
            state_kind_boundaries.add(
                (progress - 1, str(metadata["node_kind_before"]))
            )
    final_node_kind = summary.get("source_node_kind")
    if isinstance(final_node_kind, str):
        final_progress_value = (
            source_events[-1].get("progress")
            if source_events
            else alignment.get("initial_progress", 0)
        )
        if not isinstance(final_progress_value, int) or isinstance(
            final_progress_value, bool
        ):
            raise ValueError(f"{label} final source progress is invalid")
        final_progress = final_progress_value
        state_kind_boundaries.add((final_progress, final_node_kind))

    successful_interface_sites = sum(
        item.get("cleanup") is False
        and isinstance(item.get("metadata"), Mapping)
        and isinstance(item["metadata"].get("declared_action_space_n"), int)
        and not isinstance(
            item["metadata"].get("declared_action_space_n"), bool
        )
        for item in destination_events
    )
    configuration_evaluated = summary.get("stop_reason") not in {
        "source_setup_failed",
        "adapter_setup_failed",
        "sampled_stochastic_inapplicable",
    }
    expected_counts = {
        "O1": strong_buffer_count + len(pre_event_sites["O1"]),
        "O2": buffer_count + cleanup_count,
        "O3": transition_count + int(complete_episode),
        "O4": len(destination_events),
        "O5": transition_count + cleanup_count + len(pre_event_sites["O5"]),
        "O6": int(
            bool(caller_supplied_nondefault and configuration_evaluated)
        ),
        "O7": len(state_kind_boundaries),
        "O8": (
            successful_interface_sites
            + len(pre_event_sites["O8"])
            + len(attempted_interface_sites.difference(pre_event_sites["O8"]))
        ),
    }
    for obligation_id, indices in expected_indices.items():
        if indices and expected_counts[obligation_id] == 0:
            expected_counts[obligation_id] = 1
    for row in rows:
        obligation_id = str(row["obligation_id"])
        if row["evaluation_count"] != expected_counts[obligation_id]:
            raise ValueError(
                f"{label} evaluation count differs for {obligation_id}"
            )
        if (
            obligation_id == "O1"
            and buffer_count
            and not strong_buffer_count
            and not expected_indices["O1"]
            and (
                row["outcome"] != EvaluationOutcome.NOT_EVALUATED.value
                or row["reason_code"] != "weak_state_identity_only"
            )
        ):
            raise ValueError(f"{label} weak O1 state evidence must be unevaluated")
    return tuple(rows)
