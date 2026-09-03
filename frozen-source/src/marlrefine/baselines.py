"""Project-defined comparison baselines used by the empirical study.

These executable comparators are intentionally weaker than the
obligation-aware checker.  Their names describe the schedule at which this
project compares rewards, lifecycle signals, and (when both sides expose one)
serialized-state digests.  They are not reimplementations of, or claims about,
Gymnasium's separate ``env_match`` utility.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from marlrefine.model import Alignment, SegmentKind, Span, Violation

STRICT_LOCKSTEP = "strict_lockstep"
MACRO_BOUNDARY = "macro_boundary"
MACRO_AGGREGATE = "macro_aggregate"
ENDPOINT = "endpoint"
RETURN_ONLY = "return_only"
BASELINE_NAMES = (
    STRICT_LOCKSTEP,
    MACRO_BOUNDARY,
    MACRO_AGGREGATE,
    ENDPOINT,
    RETURN_ONLY,
)


@dataclass(frozen=True, slots=True)
class BaselineResult:
    """One baseline verdict with explicit applicability."""

    baseline: str
    applicable: bool
    findings: tuple[Violation, ...] = ()
    reason: str | None = None

    @property
    def detected(self) -> bool:
        return bool(self.findings)


def inapplicable_baselines(reason: str) -> tuple[BaselineResult, ...]:
    """Return a complete baseline panel without inventing empty-trace passes."""
    return tuple(
        BaselineResult(baseline=name, applicable=False, reason=reason)
        for name in BASELINE_NAMES
    )


def _close(
    expected: tuple[float, ...],
    observed: tuple[float, ...],
    *,
    atol: float,
    rtol: float,
) -> bool:
    return len(expected) == len(observed) and all(
        math.isclose(left, right, abs_tol=atol, rel_tol=rtol)
        for left, right in zip(expected, observed, strict=True)
    )


def _sum(vectors: tuple[tuple[float, ...], ...]) -> tuple[float, ...]:
    if not vectors:
        return ()
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1:
        return ()
    dimension = next(iter(dimensions))
    return tuple(
        math.fsum(vector[index] for vector in vectors) for index in range(dimension)
    )


def _source_state_digest(event: object) -> object | None:
    metadata = getattr(event, "metadata", {})
    return metadata.get("state_digest_after")


def _destination_state_digest(event: object) -> object | None:
    metadata = getattr(event, "metadata", {})
    return metadata.get("adapter_state_digest_after")


def strict_lockstep(
    alignment: Alignment,
    *,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> BaselineResult:
    """Compare rewards, lifecycle, and available state at a 1:1 call schedule."""
    if not alignment.source_events or not alignment.destination_events:
        return BaselineResult(
            baseline=STRICT_LOCKSTEP,
            applicable=False,
            reason="trace has no source/destination event pair",
        )
    one_to_one = (
        len(alignment.source_events) == len(alignment.destination_events)
        and all(
            destination.source_progress == source.progress
            for source, destination in zip(
                alignment.source_events,
                alignment.destination_events,
                strict=True,
            )
        )
        and not any(event.cleanup for event in alignment.destination_events)
    )
    if not one_to_one:
        return BaselineResult(
            baseline=STRICT_LOCKSTEP,
            applicable=False,
            reason="source and destination do not expose a one-to-one schedule",
        )

    findings: list[Violation] = []
    for index, (source, destination) in enumerate(
        zip(alignment.source_events, alignment.destination_events, strict=True)
    ):
        if not _close(source.rewards, destination.rewards, atol=atol, rtol=rtol):
            findings.append(
                Violation(
                    obligation=STRICT_LOCKSTEP,
                    code="lockstep_reward_mismatch",
                    message="one-to-one source and destination rewards differ",
                    source_span=Span(index, index + 1),
                    destination_span=Span(index, index + 1),
                    expected=source.rewards,
                    observed=destination.rewards,
                )
            )
        if (source.terminated, source.truncated) != (
            destination.terminated,
            destination.truncated,
        ):
            findings.append(
                Violation(
                    obligation=STRICT_LOCKSTEP,
                    code="lockstep_lifecycle_mismatch",
                    message="one-to-one lifecycle signals differ",
                    source_span=Span(index, index + 1),
                    destination_span=Span(index, index + 1),
                    expected=(source.terminated, source.truncated),
                    observed=(destination.terminated, destination.truncated),
                )
            )
        source_digest = _source_state_digest(source)
        destination_digest = _destination_state_digest(destination)
        if (
            source_digest is not None
            and destination_digest is not None
            and source_digest != destination_digest
        ):
            findings.append(
                Violation(
                    obligation=STRICT_LOCKSTEP,
                    code="lockstep_state_mismatch",
                    message=(
                        "one-to-one source and destination serialized-state "
                        "digests differ"
                    ),
                    source_span=Span(index, index + 1),
                    destination_span=Span(index, index + 1),
                    expected=source_digest,
                    observed=destination_digest,
                )
            )
    return BaselineResult(STRICT_LOCKSTEP, True, tuple(findings))


def macro_boundary(
    alignment: Alignment,
    *,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> BaselineResult:
    """Compare each source boundary with its advancing destination commit."""
    if not alignment.transition_segments:
        return BaselineResult(
            baseline=MACRO_BOUNDARY,
            applicable=False,
            reason="trace has no aligned transition boundary",
        )
    findings: list[Violation] = []
    for segment_index, segment in enumerate(alignment.segments):
        if segment.kind is not SegmentKind.TRANSITION:
            continue
        commit = segment.commit_event
        if commit is None or not segment.source_events:
            continue
        expected_reward = _sum(tuple(event.rewards for event in segment.source_events))
        if not _close(expected_reward, commit.rewards, atol=atol, rtol=rtol):
            findings.append(
                Violation(
                    obligation=MACRO_BOUNDARY,
                    code="boundary_reward_mismatch",
                    message="commit reward differs from the compressed source segment",
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=Span(
                        segment.destination_span.stop - 1,
                        segment.destination_span.stop,
                    ),
                    expected=expected_reward,
                    observed=commit.rewards,
                )
            )
        source_end = segment.source_events[-1]
        if (source_end.terminated, source_end.truncated) != (
            commit.terminated,
            commit.truncated,
        ):
            findings.append(
                Violation(
                    obligation=MACRO_BOUNDARY,
                    code="boundary_lifecycle_mismatch",
                    message="commit lifecycle differs from the source boundary",
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=Span(
                        segment.destination_span.stop - 1,
                        segment.destination_span.stop,
                    ),
                    expected=(source_end.terminated, source_end.truncated),
                    observed=(commit.terminated, commit.truncated),
                )
            )
        source_digest = _source_state_digest(source_end)
        destination_digest = _destination_state_digest(commit)
        if (
            source_digest is not None
            and destination_digest is not None
            and source_digest != destination_digest
        ):
            findings.append(
                Violation(
                    obligation=MACRO_BOUNDARY,
                    code="boundary_state_mismatch",
                    message=(
                        "destination commit serialized-state digest differs "
                        "from the compressed source boundary"
                    ),
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=Span(
                        segment.destination_span.stop - 1,
                        segment.destination_span.stop,
                    ),
                    expected=source_digest,
                    observed=destination_digest,
                )
            )
    return BaselineResult(MACRO_BOUNDARY, True, tuple(findings))


def macro_aggregate(
    alignment: Alignment,
    *,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> BaselineResult:
    """Aggregate every microstep reward before comparing a source boundary.

    Unlike :func:`macro_boundary`, which samples only the advancing commit,
    this stronger compression baseline sums the complete destination side of
    each aligned transition block.  It therefore detects duplicated or shifted
    microstep reward even though it does not diagnose which target-only phase
    caused the discrepancy.
    """
    if not alignment.transition_segments:
        return BaselineResult(
            baseline=MACRO_AGGREGATE,
            applicable=False,
            reason="trace has no aligned transition boundary",
        )
    findings: list[Violation] = []
    for segment_index, segment in enumerate(alignment.segments):
        if segment.kind is not SegmentKind.TRANSITION:
            continue
        commit = segment.commit_event
        if commit is None or not segment.source_events:
            continue
        expected_reward = _sum(tuple(event.rewards for event in segment.source_events))
        observed_reward = _sum(
            tuple(event.rewards for event in segment.destination_events)
        )
        if not _close(expected_reward, observed_reward, atol=atol, rtol=rtol):
            findings.append(
                Violation(
                    obligation=MACRO_AGGREGATE,
                    code="aggregate_boundary_reward_mismatch",
                    message=(
                        "summed destination microstep reward differs from the "
                        "compressed source segment"
                    ),
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=segment.destination_span,
                    expected=expected_reward,
                    observed=observed_reward,
                )
            )
        source_end = segment.source_events[-1]
        if (source_end.terminated, source_end.truncated) != (
            commit.terminated,
            commit.truncated,
        ):
            findings.append(
                Violation(
                    obligation=MACRO_AGGREGATE,
                    code="aggregate_boundary_lifecycle_mismatch",
                    message="commit lifecycle differs from the source boundary",
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=Span(
                        segment.destination_span.stop - 1,
                        segment.destination_span.stop,
                    ),
                    expected=(source_end.terminated, source_end.truncated),
                    observed=(commit.terminated, commit.truncated),
                )
            )
        source_digest = _source_state_digest(source_end)
        destination_digest = _destination_state_digest(commit)
        if (
            source_digest is not None
            and destination_digest is not None
            and source_digest != destination_digest
        ):
            findings.append(
                Violation(
                    obligation=MACRO_AGGREGATE,
                    code="aggregate_boundary_state_mismatch",
                    message=(
                        "destination commit serialized-state digest differs "
                        "from the compressed source boundary"
                    ),
                    segment_index=segment_index,
                    source_span=segment.source_span,
                    destination_span=Span(
                        segment.destination_span.stop - 1,
                        segment.destination_span.stop,
                    ),
                    expected=source_digest,
                    observed=destination_digest,
                )
            )
    return BaselineResult(MACRO_AGGREGATE, True, tuple(findings))


def endpoint(alignment: Alignment) -> BaselineResult:
    """Compare final lifecycle and available serialized-state digests only."""
    if not alignment.source_events or not alignment.transition_segments:
        return BaselineResult(
            baseline=ENDPOINT,
            applicable=False,
            reason="trace has no aligned transition endpoint",
        )
    source = alignment.source_events[-1]
    destination = alignment.transition_segments[-1].commit_event
    assert destination is not None
    expected = (source.terminated, source.truncated)
    observed = (destination.terminated, destination.truncated)
    findings: list[Violation] = []
    if expected != observed:
        findings.append(
            Violation(
                obligation=ENDPOINT,
                code="endpoint_lifecycle_mismatch",
                message="final source and destination lifecycle signals differ",
                expected=expected,
                observed=observed,
            )
        )
    source_digest = _source_state_digest(source)
    destination_digest = _destination_state_digest(destination)
    if (
        source_digest is not None
        and destination_digest is not None
        and source_digest != destination_digest
    ):
        findings.append(
            Violation(
                obligation=ENDPOINT,
                code="endpoint_state_mismatch",
                message="final source and destination serialized-state digests differ",
                expected=source_digest,
                observed=destination_digest,
            )
        )
    return BaselineResult(ENDPOINT, True, tuple(findings))


def return_only(
    source_return: tuple[float, ...],
    delivered_return: tuple[float, ...],
    *,
    complete_episode: bool,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> BaselineResult:
    """Compare only final consumer-visible return on complete episodes."""
    if not complete_episode:
        return BaselineResult(
            baseline=RETURN_ONLY,
            applicable=False,
            reason="return-only comparison requires a complete destination episode",
        )
    findings: tuple[Violation, ...] = ()
    if not _close(source_return, delivered_return, atol=atol, rtol=rtol):
        findings = (
            Violation(
                obligation=RETURN_ONLY,
                code="final_return_mismatch",
                message="final consumer-visible return differs from the source",
                expected=source_return,
                observed=delivered_return,
            ),
        )
    return BaselineResult(RETURN_ONLY, True, findings)
