from __future__ import annotations

import pytest

from marlrefine.controls import (
    run_discovery_semantic_controls,
    run_native_clone_control,
    run_parallel_to_aec_control,
    run_turn_based_simultaneous_control,
)
from marlrefine.model import SegmentKind


@pytest.mark.integration
def test_native_clone_replay_is_a_clean_one_to_one_control() -> None:
    run = run_native_clone_control()
    assert run.passed is True
    assert run.summary["source_terminal"] is True
    assert run.summary["source_transitions"] == run.summary["destination_calls"]
    assert all(
        segment.source_count == segment.destination_count == 1
        for segment in run.alignment.segments
    )


@pytest.mark.integration
def test_official_turn_based_transform_is_a_clean_stutter_control() -> None:
    run = run_turn_based_simultaneous_control()
    assert run.passed is True
    assert run.summary["source_terminal"] is True
    assert run.summary["source_transitions"] == 1
    assert run.summary["destination_calls"] == 2
    segment = run.alignment.transition_segments[0]
    assert segment.source_count == 1
    assert segment.destination_count == 2
    assert segment.buffer_events[0].rewards == (0.0, 0.0)
    assert segment.commit_event is not None
    assert segment.commit_event.rewards != (0.0, 0.0)


@pytest.mark.integration
def test_canonical_parallel_to_aec_is_clean_through_terminal_cleanup() -> None:
    run = run_parallel_to_aec_control()
    assert run.passed is True
    assert run.summary["source_terminal"] is True
    assert run.summary["aec_agents_remaining"] == 0
    assert run.summary["source_return"] == run.summary["delivered_return"]
    assert run.alignment.terminal_tail is not None
    assert run.alignment.terminal_tail.kind is SegmentKind.TERMINAL_TAIL
    assert all(
        event.cleanup for event in run.alignment.terminal_tail.destination_events
    )


def test_control_guard_rejects_non_discovery_specs_before_loading() -> None:
    with pytest.raises(ValueError, match="only predeclared discovery games"):
        run_native_clone_control("synthetic_not_discovery")


@pytest.mark.integration
def test_full_clean_control_panel_fails_on_no_obligation() -> None:
    runs = run_discovery_semantic_controls()
    assert len(runs) == 3
    assert all(run.passed for run in runs)
    assert all(run.validation_scope for run in runs)
