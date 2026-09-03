from __future__ import annotations

from marlrefine.alignment import align_traces
from marlrefine.model import (
    DestinationEvent,
    RewardChannel,
    SourceEvent,
    Span,
    Violation,
)
from marlrefine.obligations import (
    check_all,
    check_boundary_lifecycle_preservation,
    check_progress_completeness,
    check_segment_reward_conservation,
    check_stutter_reward_neutrality,
    check_terminal_cleanup_reward_neutrality,
)


def _codes(violations: tuple[Violation, ...]) -> set[str]:
    return {violation.code for violation in violations}


def test_clean_buffer_commit_and_cleanup_trace_satisfies_all_obligations() -> None:
    source = (SourceEvent(progress=1, rewards=(1.0, -1.0), terminated=True),)
    destination = (
        DestinationEvent(source_progress=0, rewards=(0.0, 0.0)),
        DestinationEvent(
            source_progress=1,
            rewards=(1.0, -1.0),
            terminated=True,
        ),
        DestinationEvent(
            source_progress=1,
            rewards=(0.0, 0.0),
            delivered_rewards=(1.0, -1.0),
            terminated=True,
            cleanup=True,
        ),
    )

    assert check_all(align_traces(source, destination)) == ()


def test_stutter_reward_violation_identifies_the_exact_call() -> None:
    source = (SourceEvent(progress=1, rewards=(1.0, -1.0)),)
    destination = (
        DestinationEvent(source_progress=0, rewards=(0.25, -0.25)),
        DestinationEvent(source_progress=1, rewards=(1.0, -1.0)),
    )

    violations = check_stutter_reward_neutrality(align_traces(source, destination))

    assert len(violations) == 1
    violation = violations[0]
    assert violation.code == "nonzero_stutter_reward"
    assert violation.segment_index == 0
    assert violation.destination_span == Span(0, 1)
    assert violation.expected == (0.0, 0.0)
    assert violation.observed == (0.25, -0.25)


def test_reward_conservation_supports_n_to_one_skipping() -> None:
    source = (
        SourceEvent(progress=1, rewards=(0.25, -0.25)),
        SourceEvent(progress=2, rewards=(0.75, -0.75)),
    )
    conserved = align_traces(
        source,
        (DestinationEvent(source_progress=2, rewards=(1.0, -1.0)),),
    )
    changed = align_traces(
        source,
        (DestinationEvent(source_progress=2, rewards=(0.9, -1.0)),),
    )

    assert check_segment_reward_conservation(conserved) == ()
    violations = check_segment_reward_conservation(changed)
    assert len(violations) == 1
    assert violations[0].code == "segment_reward_mismatch"
    assert violations[0].expected == (1.0, -1.0)
    assert violations[0].observed == (0.9, -1.0)


def test_delivered_reward_channel_is_never_silently_substituted() -> None:
    alignment = align_traces(
        (SourceEvent(progress=1, rewards=(1.0,)),),
        (DestinationEvent(source_progress=1, rewards=(1.0,)),),
    )

    violations = check_segment_reward_conservation(
        alignment,
        channel=RewardChannel.DELIVERED,
    )

    assert len(violations) == 1
    assert violations[0].code == "reward_channel_unavailable"
    assert violations[0].observed == {"missing_event_indices": (0,)}


def test_progress_check_reports_regression_gap_and_incompleteness() -> None:
    source = (
        SourceEvent(progress=1),
        SourceEvent(progress=3),
    )
    destination = (
        DestinationEvent(source_progress=1),
        DestinationEvent(source_progress=0),
    )

    violations = check_progress_completeness(align_traces(source, destination))

    assert {
        "source_progress_gap",
        "destination_progress_regression",
        "destination_progress_incomplete",
        "unmatched_source_events",
    }.issubset(_codes(violations))


def test_progress_check_allows_destination_jump_when_boundaries_exist() -> None:
    alignment = align_traces(
        (SourceEvent(progress=1), SourceEvent(progress=2)),
        (DestinationEvent(source_progress=2),),
    )

    assert check_progress_completeness(alignment) == ()


def test_progress_replay_anchor_accepts_a_consistent_many_to_one_commit() -> None:
    destination = DestinationEvent(
        source_progress=2,
        metadata={
            "progress_instrumentation": {
                "method_id": "independent_native_replay_event_count_v1",
                "progress_before": 0,
                "progress_after": 2,
                "replayed_source_event_count": 2,
                "source_event_progresses": (1, 2),
                "wrapped_history_delta": (4, 7),
            }
        },
    )
    alignment = align_traces(
        (SourceEvent(progress=1), SourceEvent(progress=2)),
        (destination,),
    )

    assert check_progress_completeness(alignment) == ()


def test_progress_replay_anchor_detects_plausible_monotone_tag_corruption() -> None:
    destination = DestinationEvent(
        source_progress=0,
        metadata={
            "progress_instrumentation": {
                "method_id": "independent_native_replay_event_count_v1",
                "progress_before": 0,
                "progress_after": 1,
                "replayed_source_event_count": 1,
                "source_event_progresses": (1,),
                "wrapped_history_delta": (0,),
            }
        },
    )
    alignment = align_traces((SourceEvent(progress=1),), (destination,))

    assert "progress_instrumentation_inconsistent" in _codes(
        check_progress_completeness(alignment)
    )


def test_progress_check_reports_destination_overrun() -> None:
    alignment = align_traces(
        (SourceEvent(progress=1),),
        (DestinationEvent(source_progress=2),),
    )

    assert {
        "destination_progress_beyond_source",
        "destination_commit_without_source_boundary",
    }.issubset(_codes(check_progress_completeness(alignment)))


def test_terminal_cleanup_checks_instantaneous_not_delivered_reward() -> None:
    source = (SourceEvent(progress=1, rewards=(1.0, -1.0), terminated=True),)
    destination = (
        DestinationEvent(
            source_progress=1,
            rewards=(1.0, -1.0),
            terminated=True,
        ),
        DestinationEvent(
            source_progress=1,
            rewards=(0.0, -1.0),
            delivered_rewards=(0.0, -1.0),
            terminated=True,
            cleanup=True,
        ),
    )

    violations = check_terminal_cleanup_reward_neutrality(
        align_traces(source, destination)
    )

    assert len(violations) == 1
    violation = violations[0]
    assert violation.code == "nonzero_terminal_cleanup_reward"
    assert violation.destination_span == Span(1, 2)
    assert violation.expected == (0.0, 0.0)
    assert violation.observed == (0.0, -1.0)


def test_boundary_lifecycle_keeps_termination_and_truncation_distinct() -> None:
    alignment = align_traces(
        (SourceEvent(progress=1, terminated=True, truncated=False),),
        (
            DestinationEvent(
                source_progress=1,
                terminated=True,
                truncated=True,
            ),
        ),
    )

    violations = check_boundary_lifecycle_preservation(alignment)

    assert len(violations) == 1
    assert violations[0].code == "boundary_lifecycle_mismatch"
    assert violations[0].expected == (True, False)
    assert violations[0].observed == (True, True)


def test_reward_dimension_mismatch_is_structured() -> None:
    alignment = align_traces(
        (SourceEvent(progress=1, rewards=(1.0, -1.0)),),
        (DestinationEvent(source_progress=1, rewards=(1.0,)),),
    )

    violations = check_segment_reward_conservation(alignment)

    assert len(violations) == 1
    assert violations[0].code == "reward_dimension_mismatch"
    assert violations[0].source_span == Span(0, 1)
    assert violations[0].destination_span == Span(0, 1)
