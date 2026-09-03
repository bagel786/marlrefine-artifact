from __future__ import annotations

import copy

import pytest

from marlrefine.alignment import align_traces
from marlrefine.evaluation import (
    OBLIGATION_IDS,
    build_obligation_evaluations,
    validate_serialized_obligation_evaluations,
)
from marlrefine.model import DestinationEvent, SourceEvent, Span, Violation
from marlrefine.serialization import to_jsonable


def _fully_exercised_fixture():
    source = (
        SourceEvent(
            1,
            rewards=(1.0,),
            terminated=True,
            metadata={"node_kind_before": "simultaneous"},
        ),
    )
    destination = (
        DestinationEvent(
            0,
            rewards=(0.0,),
            metadata={"buffer_only": True},
        ),
        DestinationEvent(
            1,
            rewards=(1.0,),
            terminated=True,
            metadata={"buffer_only": False, "declared_action_space_n": 3},
        ),
        DestinationEvent(
            1,
            rewards=(0.0,),
            terminated=True,
            cleanup=True,
            metadata={"buffer_only": False},
        ),
    )
    alignment = align_traces(source, destination)
    violations = (
        Violation(
            obligation="state_projection",
            code="aligned_state_mismatch",
            message="buffer changed wrapped source state",
            destination_span=Span(0, 1),
        ),
        Violation(
            obligation="stutter_reward_neutrality",
            code="nonzero_stutter_reward",
            message="buffer emitted reward",
            destination_span=Span(0, 1),
        ),
        Violation(
            obligation="terminal_cleanup_reward_neutrality",
            code="nonzero_terminal_cleanup_reward",
            message="cleanup emitted reward",
            destination_span=Span(2, 3),
        ),
        Violation(
            obligation="segment_reward_conservation",
            code="segment_reward_mismatch",
            message="segment reward differs",
            destination_span=Span(0, 2),
        ),
        Violation(
            obligation="decision_clock_preservation",
            code="source_decision_clock_mismatch",
            message="clock differs",
            destination_span=Span(1, 2),
        ),
        Violation(
            obligation="decision_clock_preservation",
            code="premature_adapter_truncation",
            message="adapter truncated early",
            destination_span=Span(1, 2),
        ),
        Violation(
            obligation="configuration_provenance",
            code="parameters_changed_on_reset",
            message="parameters differ",
        ),
        Violation(
            obligation="state_kind_soundness",
            code="mean_field_node_silently_terminated",
            message="state kind differs",
            destination_span=Span(1, 2),
        ),
        Violation(
            obligation="interface_projection",
            code="legal_action_mismatch",
            message="legal actions differ",
            destination_span=Span(1, 2),
        ),
        Violation(
            obligation="trace_execution",
            code="adapter_step_failed",
            message="unlinked execution diagnostic",
            destination_span=Span(3, 3),
        ),
    )
    evaluations = build_obligation_evaluations(
        alignment,
        violations,
        caller_supplied_nondefault=True,
        configuration_evaluated=True,
        state_kind_evaluation_count=2,
        interface_evaluation_count=1,
        complete_episode=True,
        unresolved="no_applicable_site",
    )
    return alignment, violations, evaluations


def test_ledger_maps_findings_and_counts_sites_without_changing_findings() -> None:
    alignment, violations, evaluations = _fully_exercised_fixture()

    assert tuple(item.obligation_id for item in evaluations) == OBLIGATION_IDS
    assert tuple(item.outcome.value for item in evaluations) == (
        "evaluated_fail",
    ) * 8
    assert tuple(item.evaluation_count for item in evaluations) == (
        1,
        2,
        2,
        3,
        2,
        1,
        2,
        1,
    )
    assert evaluations[1].finding_indices == (1, 2)
    assert evaluations[4].finding_indices == (2, 5)
    assert evaluations[3].finding_indices == (4,)
    assert evaluations[7].finding_indices == (8,)
    assert 9 not in {
        index
        for evaluation in evaluations
        for index in evaluation.finding_indices
    }

    validate_serialized_obligation_evaluations(
        to_jsonable(evaluations),
        violations=to_jsonable(violations),
        alignment=to_jsonable(alignment),
        summary={
            "stop_reason": "destination_episode_end",
            "source_terminal": True,
            "adapter_agents_remaining": 0,
            "source_node_kind": "terminal",
        },
        caller_supplied_nondefault=True,
        label="fixture ledger",
    )


def test_nonbuffer_no_progress_event_does_not_inflate_o1_or_o2() -> None:
    alignment = align_traces(
        (),
        (
            DestinationEvent(
                0,
                rewards=(0.0,),
                metadata={"buffer_only": False},
            ),
        ),
    )
    evaluations = build_obligation_evaluations(
        alignment,
        (),
        caller_supplied_nondefault=False,
        configuration_evaluated=True,
        state_kind_evaluation_count=0,
        interface_evaluation_count=0,
        complete_episode=False,
        unresolved="no_applicable_site",
    )

    assert evaluations[0].outcome.value == "not_applicable"
    assert evaluations[1].outcome.value == "not_applicable"
    assert evaluations[5].reason_code == "default_configuration_only"


def test_failed_buffer_attempt_is_linked_without_appending_phantom_event() -> None:
    alignment = align_traces((), ())
    violation = Violation(
        obligation="state_projection",
        code="instrumentation_history_not_prefix_monotone",
        message="buffer rewrote source history",
        destination_span=Span(0, 0),
        expected={"history_prefix": (), "buffer_only_attempt": True},
        observed=(1,),
    )
    evaluations = build_obligation_evaluations(
        alignment,
        (violation,),
        caller_supplied_nondefault=False,
        configuration_evaluated=True,
        state_kind_evaluation_count=0,
        interface_evaluation_count=0,
        complete_episode=False,
        unresolved="no_applicable_site",
    )

    assert evaluations[0].outcome.value == "evaluated_fail"
    assert evaluations[0].evaluation_count == 1
    assert evaluations[0].finding_indices == (0,)
    assert all(
        not item.finding_indices for item in evaluations[1:]
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_row",
        "reordered_rows",
        "boolean_count",
        "duplicate_reference",
        "out_of_range_reference",
        "pass_with_reference",
        "wrong_obligation_reference",
        "unknown_reason",
        "inflated_site_count",
    ),
)
def test_serialized_ledger_validation_fails_closed(mutation: str) -> None:
    alignment, violations, evaluations = _fully_exercised_fixture()
    rows = copy.deepcopy(to_jsonable(evaluations))
    if mutation == "missing_row":
        rows.pop()
    elif mutation == "reordered_rows":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "boolean_count":
        rows[0]["evaluation_count"] = True
    elif mutation == "duplicate_reference":
        rows[1]["finding_indices"] = [1, 1, 2]
    elif mutation == "out_of_range_reference":
        rows[7]["finding_indices"] = [len(violations)]
    elif mutation == "pass_with_reference":
        rows[0]["outcome"] = "evaluated_pass"
    elif mutation == "wrong_obligation_reference":
        rows[7]["finding_indices"] = [0]
    elif mutation == "unknown_reason":
        rows[0]["reason_code"] = "free_text_reason"
    elif mutation == "inflated_site_count":
        rows[3]["evaluation_count"] += 1
    else:  # pragma: no cover - exhaustive parameter guard
        raise AssertionError(mutation)

    with pytest.raises(ValueError):
        validate_serialized_obligation_evaluations(
            rows,
            violations=to_jsonable(violations),
            alignment=to_jsonable(alignment),
            summary={
                "stop_reason": "destination_episode_end",
                "source_terminal": True,
                "adapter_agents_remaining": 0,
                "source_node_kind": "terminal",
            },
            caller_supplied_nondefault=True,
            label="mutated ledger",
        )
