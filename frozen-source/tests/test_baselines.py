from __future__ import annotations

from marlrefine.alignment import align_traces
from marlrefine.baselines import (
    endpoint,
    inapplicable_baselines,
    macro_aggregate,
    macro_boundary,
    return_only,
    strict_lockstep,
)
from marlrefine.model import DestinationEvent, SourceEvent


def test_boundary_baseline_discards_nonzero_buffer_reward() -> None:
    alignment = align_traces(
        [SourceEvent(progress=1, rewards=(1.0, -1.0), terminated=True)],
        [
            DestinationEvent(source_progress=0, rewards=(1.0, -1.0)),
            DestinationEvent(
                source_progress=1,
                rewards=(1.0, -1.0),
                terminated=True,
            ),
        ],
    )
    assert strict_lockstep(alignment).applicable is False
    assert macro_boundary(alignment).detected is False
    aggregate = macro_aggregate(alignment)
    assert aggregate.detected is True
    assert aggregate.findings[0].code == "aggregate_boundary_reward_mismatch"
    assert endpoint(alignment).detected is False


def test_macro_boundary_handles_skipping_when_aggregate_matches() -> None:
    alignment = align_traces(
        [
            SourceEvent(progress=1, rewards=(0.25,)),
            SourceEvent(progress=2, rewards=(0.75,)),
        ],
        [DestinationEvent(source_progress=2, rewards=(1.0,))],
    )
    result = macro_boundary(alignment)
    assert result.applicable is True
    assert result.detected is False
    assert macro_aggregate(alignment).detected is False


def test_return_only_detects_cleanup_consequence_but_not_location() -> None:
    mismatch = return_only((1.0, -1.0), (1.0, -2.0), complete_episode=True)
    assert mismatch.detected is True
    assert mismatch.findings[0].code == "final_return_mismatch"


def test_return_only_is_inapplicable_to_prefixes() -> None:
    result = return_only((0.0,), (0.0,), complete_episode=False)
    assert result.applicable is False
    assert result.detected is False


def test_empty_trace_cannot_create_a_baseline_pass() -> None:
    alignment = align_traces([], [])
    assert strict_lockstep(alignment).applicable is False
    assert macro_boundary(alignment).applicable is False
    assert macro_aggregate(alignment).applicable is False
    results = inapplicable_baselines("trace unavailable")
    assert len(results) == 5
    assert all(not result.applicable and not result.detected for result in results)


def test_schedule_baselines_compare_available_state_digests() -> None:
    alignment = align_traces(
        [
            SourceEvent(
                progress=1,
                rewards=(0.0,),
                terminated=True,
                metadata={"state_digest_after": "native-final"},
            )
        ],
        [
            DestinationEvent(
                source_progress=1,
                rewards=(0.0,),
                terminated=True,
                metadata={"adapter_state_digest_after": "adapter-final"},
            )
        ],
    )

    assert {finding.code for finding in strict_lockstep(alignment).findings} == {
        "lockstep_state_mismatch"
    }
    assert {finding.code for finding in macro_boundary(alignment).findings} == {
        "boundary_state_mismatch"
    }
    assert {finding.code for finding in macro_aggregate(alignment).findings} == {
        "aggregate_boundary_state_mismatch"
    }
    assert {finding.code for finding in endpoint(alignment).findings} == {
        "endpoint_state_mismatch"
    }


def test_state_digest_is_optional_for_project_baselines() -> None:
    alignment = align_traces(
        [SourceEvent(progress=1, rewards=(0.0,), terminated=True)],
        [DestinationEvent(source_progress=1, rewards=(0.0,), terminated=True)],
    )

    assert strict_lockstep(alignment).detected is False
    assert macro_boundary(alignment).detected is False
    assert macro_aggregate(alignment).detected is False
    assert endpoint(alignment).detected is False
