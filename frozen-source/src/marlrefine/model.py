"""Typed, framework-independent trace and alignment records.

The core model deliberately contains no OpenSpiel, PettingZoo, or NumPy types.
Integrations translate framework events into these immutable records and may put
additional evidence (states, masks, observations, provenance) in ``metadata``.

``progress`` is a cumulative source-transition count.  A destination event may
leave it unchanged (a stutter), advance it by one, or advance it by more than
one (a destination call that commits several source transitions).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from numbers import Integral

type RewardVector = tuple[float, ...]
type Metadata = Mapping[str, object]


def _progress(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{field_name} must be an integer")
    result = int(value)
    if result < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return result


def _rewards(values: RewardVector) -> RewardVector:
    """Make reward inputs immutable and numerically comparable."""
    return tuple(float(value) for value in values)


class SegmentKind(StrEnum):
    """The relationship represented by one aligned segment."""

    TRANSITION = "transition"
    STUTTER = "stutter"
    TERMINAL_TAIL = "terminal_tail"


class RewardChannel(StrEnum):
    """Destination reward observations kept distinct by the event ledger."""

    INSTANTANEOUS = "instantaneous"
    DELIVERED = "delivered"


class EvaluationOutcome(StrEnum):
    """Trace-local result for one frozen study obligation."""

    EVALUATED_PASS = "evaluated_pass"
    EVALUATED_FAIL = "evaluated_fail"
    NOT_APPLICABLE = "not_applicable"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True, slots=True)
class SourceEvent:
    """One independently observed source transition.

    ``progress`` is the cumulative transition count *after* this event.
    ``rewards`` is the source's immediate reward vector.  Termination and
    truncation are separate because conflating them changes bootstrapping
    semantics in reinforcement-learning consumers.
    """

    progress: int
    rewards: RewardVector = ()
    terminated: bool = False
    truncated: bool = False
    metadata: Metadata = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "progress", _progress(self.progress, "progress"))
        object.__setattr__(self, "rewards", _rewards(self.rewards))

    @property
    def terminal(self) -> bool:
        """Whether this event ends the source episode for either reason."""
        return self.terminated or self.truncated


@dataclass(frozen=True, slots=True)
class DestinationEvent:
    """One destination API call annotated with observed source progress.

    ``rewards`` is the destination's instantaneous reward vector after the
    call.  ``delivered_rewards`` optionally records what a consumer receives
    (for example through PettingZoo's ``last()``); it is intentionally a
    separate channel and ``None`` means that the channel was not captured.

    ``cleanup`` marks a known terminal/dead-agent cleanup call.  Alignment can
    also infer a terminal tail from position and termination metadata.
    """

    source_progress: int
    rewards: RewardVector = ()
    delivered_rewards: RewardVector | None = None
    terminated: bool = False
    truncated: bool = False
    cleanup: bool = False
    metadata: Metadata = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_progress",
            _progress(self.source_progress, "source_progress"),
        )
        object.__setattr__(self, "rewards", _rewards(self.rewards))
        if self.delivered_rewards is not None:
            object.__setattr__(
                self,
                "delivered_rewards",
                _rewards(self.delivered_rewards),
            )

    @property
    def terminal(self) -> bool:
        """Whether the destination reports either form of episode ending."""
        return self.terminated or self.truncated

    def reward_for(self, channel: RewardChannel) -> RewardVector | None:
        """Return one reward channel without silently substituting the other."""
        if channel is RewardChannel.INSTANTANEOUS:
            return self.rewards
        if channel is RewardChannel.DELIVERED:
            return self.delivered_rewards
        raise ValueError(f"unsupported reward channel: {channel!r}")


@dataclass(frozen=True, slots=True, order=True)
class Span:
    """A half-open span into an input trace."""

    start: int
    stop: int

    def __post_init__(self) -> None:
        start = _progress(self.start, "start")
        stop = _progress(self.stop, "stop")
        if stop < start:
            raise ValueError("span stop must not precede span start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "stop", stop)

    def __len__(self) -> int:
        return self.stop - self.start


@dataclass(frozen=True, slots=True)
class AlignedSegment:
    """A variable-length source/destination trace segment.

    A transition segment may be 1:n (buffers followed by a commit) or n:1
    (one destination commit skips across several source events).  Stutter and
    terminal-tail segments have a zero-width source side.
    """

    kind: SegmentKind
    source_before: int
    source_after: int
    source_span: Span
    destination_span: Span
    source_events: tuple[SourceEvent, ...]
    destination_events: tuple[DestinationEvent, ...]

    def __post_init__(self) -> None:
        before = _progress(self.source_before, "source_before")
        after = _progress(self.source_after, "source_after")
        if after < before:
            raise ValueError("a segment cannot move source progress backwards")
        if len(self.source_span) != len(self.source_events):
            raise ValueError("source span length does not match source events")
        if len(self.destination_span) != len(self.destination_events):
            raise ValueError(
                "destination span length does not match destination events"
            )
        if not self.destination_events:
            raise ValueError("an aligned segment must contain a destination event")
        if self.kind is SegmentKind.TRANSITION and after == before:
            raise ValueError("a transition segment must advance source progress")
        if self.kind is not SegmentKind.TRANSITION:
            if after != before:
                raise ValueError("a non-transition segment cannot advance progress")
            if self.source_events:
                raise ValueError(
                    "a non-transition segment cannot consume source events"
                )
        object.__setattr__(self, "source_before", before)
        object.__setattr__(self, "source_after", after)

    @property
    def source_count(self) -> int:
        return len(self.source_events)

    @property
    def destination_count(self) -> int:
        return len(self.destination_events)

    @property
    def source_advance(self) -> int:
        return self.source_after - self.source_before

    @property
    def commit_event(self) -> DestinationEvent | None:
        """The advancing destination event, if this is a transition segment."""
        if self.kind is SegmentKind.TRANSITION:
            return self.destination_events[-1]
        return None

    @property
    def buffer_events(self) -> tuple[DestinationEvent, ...]:
        """Non-advancing calls grouped with this segment's commit."""
        if self.kind is SegmentKind.TRANSITION:
            return self.destination_events[:-1]
        return self.destination_events


@dataclass(frozen=True, slots=True)
class Alignment:
    """The complete, lossless result of aligning two input traces."""

    source_events: tuple[SourceEvent, ...]
    destination_events: tuple[DestinationEvent, ...]
    segments: tuple[AlignedSegment, ...]
    initial_progress: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "initial_progress",
            _progress(self.initial_progress, "initial_progress"),
        )

    @property
    def maximum_source_progress(self) -> int:
        return max(
            (event.progress for event in self.source_events),
            default=self.initial_progress,
        )

    @property
    def final_source_progress(self) -> int:
        if not self.source_events:
            return self.initial_progress
        return self.source_events[-1].progress

    @property
    def maximum_destination_progress(self) -> int:
        return max(
            (event.source_progress for event in self.destination_events),
            default=self.initial_progress,
        )

    @property
    def final_destination_progress(self) -> int:
        if not self.destination_events:
            return self.initial_progress
        return self.destination_events[-1].source_progress

    @property
    def transition_segments(self) -> tuple[AlignedSegment, ...]:
        return tuple(
            segment
            for segment in self.segments
            if segment.kind is SegmentKind.TRANSITION
        )

    @property
    def terminal_tail(self) -> AlignedSegment | None:
        for segment in reversed(self.segments):
            if segment.kind is SegmentKind.TERMINAL_TAIL:
                return segment
        return None


@dataclass(frozen=True, slots=True)
class Violation:
    """Structured evidence that one named semantic obligation failed."""

    obligation: str
    code: str
    message: str
    segment_index: int | None = None
    source_span: Span | None = None
    destination_span: Span | None = None
    expected: object | None = None
    observed: object | None = None

    def __post_init__(self) -> None:
        if not self.obligation:
            raise ValueError("violation obligation must be non-empty")
        if not self.code:
            raise ValueError("violation code must be non-empty")
        if not self.message:
            raise ValueError("violation message must be non-empty")


@dataclass(frozen=True, slots=True)
class ObligationEvaluation:
    """Applicability and exercised-site accounting for one O1--O8 obligation.

    ``finding_indices`` refers to positions in the enclosing trace run's stable
    violation tuple.  A finding may support more than one study obligation, and
    execution/alignment diagnostics need not be linked to any O1--O8 row.
    """

    obligation_id: str
    applicable: bool | None
    evaluated: bool
    outcome: EvaluationOutcome
    reason_code: str
    evaluation_count: int
    finding_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.obligation_id:
            raise ValueError("obligation_id must be non-empty")
        if self.applicable is not None and not isinstance(self.applicable, bool):
            raise TypeError("applicable must be boolean or None")
        if not isinstance(self.evaluated, bool):
            raise TypeError("evaluated must be boolean")
        if not isinstance(self.outcome, EvaluationOutcome):
            try:
                outcome = EvaluationOutcome(self.outcome)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"unsupported evaluation outcome: {self.outcome!r}"
                ) from exc
            object.__setattr__(self, "outcome", outcome)
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise ValueError("reason_code must be non-empty")
        count = _progress(self.evaluation_count, "evaluation_count")
        indices = tuple(
            _progress(index, "finding index") for index in self.finding_indices
        )
        if indices != tuple(sorted(set(indices))):
            raise ValueError("finding_indices must be sorted and unique")
        object.__setattr__(self, "evaluation_count", count)
        object.__setattr__(self, "finding_indices", indices)

        if self.outcome is EvaluationOutcome.NOT_APPLICABLE:
            if self.applicable is not False or self.evaluated or count or indices:
                raise ValueError("not-applicable evaluation fields are inconsistent")
        elif self.outcome is EvaluationOutcome.NOT_EVALUATED:
            if self.applicable is False or self.evaluated or count or indices:
                raise ValueError("not-evaluated evaluation fields are inconsistent")
        elif self.outcome is EvaluationOutcome.EVALUATED_PASS:
            if (
                self.applicable is not True
                or not self.evaluated
                or count <= 0
                or indices
            ):
                raise ValueError("evaluated-pass fields are inconsistent")
        elif (
            self.applicable is not True
            or not self.evaluated
            or count <= 0
            or not indices
        ):
            raise ValueError("evaluated-fail fields are inconsistent")
