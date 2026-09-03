"""Contract-derived, pure obligation checks over an :class:`Alignment`."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from .model import (
    AlignedSegment,
    Alignment,
    RewardChannel,
    RewardVector,
    SegmentKind,
    Span,
    Violation,
)

STUTTER_REWARD_NEUTRALITY = "stutter_reward_neutrality"
SEGMENT_REWARD_CONSERVATION = "segment_reward_conservation"
PROGRESS_COMPLETENESS = "monotone_progress_and_completeness"
TERMINAL_CLEANUP_REWARD_NEUTRALITY = "terminal_cleanup_reward_neutrality"
BOUNDARY_LIFECYCLE_PRESERVATION = "boundary_lifecycle_preservation"
PROGRESS_ANNOTATION_METHOD_ID = "independent_native_replay_event_count_v1"


def _validate_tolerances(atol: float, rtol: float = 0.0) -> None:
    if not math.isfinite(atol) or atol < 0:
        raise ValueError("atol must be finite and non-negative")
    if not math.isfinite(rtol) or rtol < 0:
        raise ValueError("rtol must be finite and non-negative")


def _is_zero(rewards: RewardVector, *, atol: float) -> bool:
    return all(
        math.isfinite(value) and math.isclose(value, 0.0, abs_tol=atol, rel_tol=0.0)
        for value in rewards
    )


def _sum_vectors(vectors: Sequence[RewardVector], dimension: int) -> RewardVector:
    return tuple(
        math.fsum(vector[index] for vector in vectors) for index in range(dimension)
    )


def _segment_index_by_destination(alignment: Alignment) -> dict[int, int]:
    result: dict[int, int] = {}
    for segment_index, segment in enumerate(alignment.segments):
        for destination_index in range(
            segment.destination_span.start,
            segment.destination_span.stop,
        ):
            result[destination_index] = segment_index
    return result


def check_stutter_reward_neutrality(
    alignment: Alignment,
    *,
    atol: float = 1e-12,
) -> tuple[Violation, ...]:
    """Require zero instantaneous reward on every non-advancing buffer call.

    Consumer-delivered rewards are deliberately not inspected here: an AEC
    consumer may legitimately receive accumulated reward immediately before
    submitting a buffered action.  The obligation concerns the destination's
    instantaneous post-call reward channel.
    """

    _validate_tolerances(atol)
    violations: list[Violation] = []

    for segment_index, segment in enumerate(alignment.segments):
        if segment.kind is SegmentKind.TERMINAL_TAIL:
            continue
        buffer_count = (
            segment.destination_count - 1
            if segment.kind is SegmentKind.TRANSITION
            else segment.destination_count
        )
        for offset, event in enumerate(segment.destination_events[:buffer_count]):
            if event.cleanup:
                # Cleanup is checked under its more specific obligation.
                continue
            if _is_zero(event.rewards, atol=atol):
                continue
            destination_index = segment.destination_span.start + offset
            violations.append(
                Violation(
                    obligation=STUTTER_REWARD_NEUTRALITY,
                    code="nonzero_stutter_reward",
                    message=(
                        "a destination call emitted instantaneous reward without "
                        "advancing source progress"
                    ),
                    segment_index=segment_index,
                    source_span=Span(
                        segment.source_span.start,
                        segment.source_span.start,
                    ),
                    destination_span=Span(
                        destination_index,
                        destination_index + 1,
                    ),
                    expected=tuple(0.0 for _ in event.rewards),
                    observed=event.rewards,
                )
            )

    return tuple(violations)


def check_segment_reward_conservation(
    alignment: Alignment,
    *,
    channel: RewardChannel = RewardChannel.INSTANTANEOUS,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> tuple[Violation, ...]:
    """Compare summed source and destination reward on every commit segment.

    Variable-length segments are reduced before comparison, so the same check
    handles buffered 1:n mappings and n:1 destination skipping.  The caller
    must explicitly choose the consumer-delivered channel; missing delivered
    observations are violations rather than silently falling back to
    instantaneous rewards.
    """

    _validate_tolerances(atol, rtol)
    if not isinstance(channel, RewardChannel):
        try:
            channel = RewardChannel(channel)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported reward channel: {channel!r}") from exc

    violations: list[Violation] = []
    for segment_index, segment in enumerate(alignment.segments):
        if segment.kind is not SegmentKind.TRANSITION:
            continue

        source_vectors = [event.rewards for event in segment.source_events]
        destination_vectors: list[RewardVector] = []
        missing_channel_indices: list[int] = []
        for offset, event in enumerate(segment.destination_events):
            reward = event.reward_for(channel)
            if reward is None:
                missing_channel_indices.append(segment.destination_span.start + offset)
            else:
                destination_vectors.append(reward)

        if missing_channel_indices:
            violations.append(
                Violation(
                    obligation=SEGMENT_REWARD_CONSERVATION,
                    code="reward_channel_unavailable",
                    message=f"destination {channel.value} reward was not captured",
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=segment.destination_span,
                    expected={"channel": channel.value},
                    observed={"missing_event_indices": tuple(missing_channel_indices)},
                )
            )
            continue

        dimensions = {len(vector) for vector in [*source_vectors, *destination_vectors]}
        if len(dimensions) > 1:
            violations.append(
                Violation(
                    obligation=SEGMENT_REWARD_CONSERVATION,
                    code="reward_dimension_mismatch",
                    message="source and destination reward vector dimensions differ",
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=segment.destination_span,
                    expected=tuple(len(vector) for vector in source_vectors),
                    observed=tuple(len(vector) for vector in destination_vectors),
                )
            )
            continue

        dimension = next(iter(dimensions), 0)
        source_sum = _sum_vectors(source_vectors, dimension)
        destination_sum = _sum_vectors(destination_vectors, dimension)
        conserved = all(
            math.isfinite(expected)
            and math.isfinite(observed)
            and math.isclose(expected, observed, abs_tol=atol, rel_tol=rtol)
            for expected, observed in zip(source_sum, destination_sum, strict=True)
        )
        if conserved:
            continue
        violations.append(
            Violation(
                obligation=SEGMENT_REWARD_CONSERVATION,
                code="segment_reward_mismatch",
                message=(
                    "summed destination reward does not equal summed source "
                    "reward on the aligned transition"
                ),
                segment_index=segment_index,
                source_span=segment.source_span,
                destination_span=segment.destination_span,
                expected=source_sum,
                observed=destination_sum,
            )
        )

    return tuple(violations)


def check_progress_completeness(alignment: Alignment) -> tuple[Violation, ...]:
    """Check source ordering, destination monotonicity, bounds, and coverage."""

    violations: list[Violation] = []
    highest_source = alignment.initial_progress

    for source_index, event in enumerate(alignment.source_events):
        if event.progress <= highest_source:
            violations.append(
                Violation(
                    obligation=PROGRESS_COMPLETENESS,
                    code="source_progress_not_strictly_monotone",
                    message="source progress must increase once per source event",
                    source_span=Span(source_index, source_index + 1),
                    expected={"greater_than": highest_source},
                    observed=event.progress,
                )
            )
        elif event.progress > highest_source + 1:
            violations.append(
                Violation(
                    obligation=PROGRESS_COMPLETENESS,
                    code="source_progress_gap",
                    message="the source trace omits one or more progress boundaries",
                    source_span=Span(source_index, source_index + 1),
                    expected=highest_source + 1,
                    observed=event.progress,
                )
            )
        highest_source = max(highest_source, event.progress)

    previous_destination = alignment.initial_progress
    for destination_index, event in enumerate(alignment.destination_events):
        instrumentation = event.metadata.get("progress_instrumentation")
        if instrumentation is not None:
            expected_progresses = tuple(
                range(previous_destination + 1, event.source_progress + 1)
            )
            expected_instrumentation = {
                "method_id": PROGRESS_ANNOTATION_METHOD_ID,
                "progress_before": previous_destination,
                "progress_after": event.source_progress,
                "replayed_source_event_count": len(expected_progresses),
                "source_event_progresses": expected_progresses,
            }
            observed_instrumentation = (
                {
                    key: instrumentation.get(key)
                    for key in expected_instrumentation
                }
                if isinstance(instrumentation, Mapping)
                else instrumentation
            )
            if observed_instrumentation != expected_instrumentation:
                violations.append(
                    Violation(
                        obligation=PROGRESS_COMPLETENESS,
                        code="progress_instrumentation_inconsistent",
                        message=(
                            "destination progress tag disagrees with its native-"
                            "replay anchor"
                        ),
                        destination_span=Span(
                            destination_index,
                            destination_index + 1,
                        ),
                        expected=expected_instrumentation,
                        observed=observed_instrumentation,
                    )
                )
        if event.source_progress < previous_destination:
            violations.append(
                Violation(
                    obligation=PROGRESS_COMPLETENESS,
                    code="destination_progress_regression",
                    message="destination-observed source progress moved backwards",
                    destination_span=Span(
                        destination_index,
                        destination_index + 1,
                    ),
                    expected={"at_least": previous_destination},
                    observed=event.source_progress,
                )
            )
        if event.source_progress > highest_source:
            violations.append(
                Violation(
                    obligation=PROGRESS_COMPLETENESS,
                    code="destination_progress_beyond_source",
                    message="destination progress exceeds the available source trace",
                    destination_span=Span(
                        destination_index,
                        destination_index + 1,
                    ),
                    expected={"at_most": highest_source},
                    observed=event.source_progress,
                )
            )
        previous_destination = event.source_progress

    maximum_destination = alignment.maximum_destination_progress
    if maximum_destination < highest_source:
        violations.append(
            Violation(
                obligation=PROGRESS_COMPLETENESS,
                code="destination_progress_incomplete",
                message="destination trace never reaches the final source boundary",
                expected=highest_source,
                observed=maximum_destination,
            )
        )

    covered_source_indices: set[int] = set()
    covered_destination_indices: set[int] = set()
    for segment_index, segment in enumerate(alignment.segments):
        covered_destination_indices.update(
            range(segment.destination_span.start, segment.destination_span.stop)
        )
        if segment.kind is not SegmentKind.TRANSITION:
            continue
        covered_source_indices.update(
            range(segment.source_span.start, segment.source_span.stop)
        )
        if not segment.source_events:
            violations.append(
                Violation(
                    obligation=PROGRESS_COMPLETENESS,
                    code="destination_advance_without_source",
                    message="a destination commit has no corresponding source event",
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=segment.destination_span,
                    expected="one or more source events",
                    observed=0,
                )
            )
            continue

        event_progress = tuple(event.progress for event in segment.source_events)
        if any(
            progress <= segment.source_before or progress > segment.source_after
            for progress in event_progress
        ):
            violations.append(
                Violation(
                    obligation=PROGRESS_COMPLETENESS,
                    code="segment_source_progress_out_of_bounds",
                    message=(
                        "a consumed source event falls outside its segment boundary"
                    ),
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=segment.destination_span,
                    expected={
                        "greater_than": segment.source_before,
                        "at_most": segment.source_after,
                    },
                    observed=event_progress,
                )
            )
        if max(event_progress) != segment.source_after:
            violations.append(
                Violation(
                    obligation=PROGRESS_COMPLETENESS,
                    code="destination_commit_without_source_boundary",
                    message=(
                        "destination commit progress does not name a captured "
                        "source boundary"
                    ),
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=segment.destination_span,
                    expected=segment.source_after,
                    observed=max(event_progress),
                )
            )

    unmatched_source = tuple(
        index
        for index in range(len(alignment.source_events))
        if index not in covered_source_indices
    )
    if unmatched_source:
        violations.append(
            Violation(
                obligation=PROGRESS_COMPLETENESS,
                code="unmatched_source_events",
                message="one or more source events were not consumed by any commit",
                expected="all source event indices covered exactly once",
                observed=unmatched_source,
            )
        )

    unmatched_destination = tuple(
        index
        for index in range(len(alignment.destination_events))
        if index not in covered_destination_indices
    )
    if unmatched_destination:
        violations.append(
            Violation(
                obligation=PROGRESS_COMPLETENESS,
                code="unmatched_destination_events",
                message="one or more destination calls were not aligned",
                expected="all destination event indices covered exactly once",
                observed=unmatched_destination,
            )
        )

    return tuple(violations)


def check_terminal_cleanup_reward_neutrality(
    alignment: Alignment,
    *,
    atol: float = 1e-12,
) -> tuple[Violation, ...]:
    """Require terminal destination-only calls to emit no new reward."""

    _validate_tolerances(atol)
    segment_by_destination = _segment_index_by_destination(alignment)
    cleanup_indices = {
        index
        for index, event in enumerate(alignment.destination_events)
        if event.cleanup
    }
    for segment in alignment.segments:
        if segment.kind is SegmentKind.TERMINAL_TAIL:
            cleanup_indices.update(
                range(segment.destination_span.start, segment.destination_span.stop)
            )

    violations: list[Violation] = []
    for destination_index in sorted(cleanup_indices):
        event = alignment.destination_events[destination_index]
        if _is_zero(event.rewards, atol=atol):
            continue
        segment_index = segment_by_destination.get(destination_index)
        segment: AlignedSegment | None = (
            alignment.segments[segment_index] if segment_index is not None else None
        )
        violations.append(
            Violation(
                obligation=TERMINAL_CLEANUP_REWARD_NEUTRALITY,
                code="nonzero_terminal_cleanup_reward",
                message="terminal cleanup emitted a new instantaneous reward",
                segment_index=segment_index,
                source_span=segment.source_span if segment is not None else None,
                destination_span=Span(destination_index, destination_index + 1),
                expected=tuple(0.0 for _ in event.rewards),
                observed=event.rewards,
            )
        )

    return tuple(violations)


def check_boundary_lifecycle_preservation(
    alignment: Alignment,
) -> tuple[Violation, ...]:
    """Require termination and truncation to agree at advancing boundaries."""
    violations: list[Violation] = []
    for segment_index, segment in enumerate(alignment.segments):
        if segment.kind is not SegmentKind.TRANSITION or not segment.source_events:
            continue
        commit = segment.commit_event
        assert commit is not None
        source_boundary = segment.source_events[-1]
        expected = (source_boundary.terminated, source_boundary.truncated)
        observed = (commit.terminated, commit.truncated)
        if expected == observed:
            continue
        violations.append(
            Violation(
                obligation=BOUNDARY_LIFECYCLE_PRESERVATION,
                code="boundary_lifecycle_mismatch",
                message=(
                    "destination termination or truncation disagrees with the "
                    "aligned source boundary"
                ),
                segment_index=segment_index,
                source_span=segment.source_span,
                destination_span=Span(
                    segment.destination_span.stop - 1,
                    segment.destination_span.stop,
                ),
                expected=expected,
                observed=observed,
            )
        )
    return tuple(violations)


def evaluate_obligations(
    alignment: Alignment,
    *,
    reward_channel: RewardChannel = RewardChannel.INSTANTANEOUS,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> tuple[Violation, ...]:
    """Run the generic core obligations in stable diagnostic order."""

    groups: Iterable[tuple[Violation, ...]] = (
        check_progress_completeness(alignment),
        check_stutter_reward_neutrality(alignment, atol=atol),
        check_segment_reward_conservation(
            alignment,
            channel=reward_channel,
            atol=atol,
            rtol=rtol,
        ),
        check_boundary_lifecycle_preservation(alignment),
        check_terminal_cleanup_reward_neutrality(alignment, atol=atol),
    )
    return tuple(violation for group in groups for violation in group)


def check_all(
    alignment: Alignment,
    *,
    reward_channel: RewardChannel = RewardChannel.INSTANTANEOUS,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> tuple[Violation, ...]:
    """Alias with a concise name for callers and test suites."""

    return evaluate_obligations(
        alignment,
        reward_channel=reward_channel,
        atol=atol,
        rtol=rtol,
    )
