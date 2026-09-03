from __future__ import annotations

from marlrefine.alignment import align_traces, segment_for_destination
from marlrefine.model import DestinationEvent, SegmentKind, SourceEvent, Span


def test_buffers_are_grouped_with_their_commit() -> None:
    source = (
        SourceEvent(progress=1, rewards=(1.0, -1.0)),
        SourceEvent(progress=2, rewards=(0.5, -0.5), terminated=True),
    )
    destination = (
        DestinationEvent(source_progress=0, rewards=(0.0, 0.0)),
        DestinationEvent(source_progress=1, rewards=(1.0, -1.0)),
        DestinationEvent(source_progress=1, rewards=(0.0, 0.0)),
        DestinationEvent(
            source_progress=2,
            rewards=(0.5, -0.5),
            terminated=True,
        ),
    )

    alignment = align_traces(source, destination)

    assert len(alignment.segments) == 2
    first, second = alignment.segments
    assert first.kind is SegmentKind.TRANSITION
    assert first.source_span == Span(0, 1)
    assert first.destination_span == Span(0, 2)
    assert first.buffer_events == (destination[0],)
    assert first.commit_event == destination[1]
    assert second.source_span == Span(1, 2)
    assert second.destination_span == Span(2, 4)
    assert second.buffer_events == (destination[2],)
    assert second.commit_event == destination[3]


def test_one_destination_commit_can_cover_multiple_source_events() -> None:
    source = (
        SourceEvent(progress=1, rewards=(0.25, -0.25)),
        SourceEvent(progress=2, rewards=(0.75, -0.75)),
    )
    destination = (DestinationEvent(source_progress=2, rewards=(1.0, -1.0)),)

    alignment = align_traces(source, destination)

    assert len(alignment.segments) == 1
    segment = alignment.segments[0]
    assert segment.kind is SegmentKind.TRANSITION
    assert segment.source_count == 2
    assert segment.destination_count == 1
    assert segment.source_advance == 2
    assert segment.source_events == source
    assert segment.commit_event == destination[0]


def test_terminal_destination_only_calls_form_a_tail() -> None:
    source = (SourceEvent(progress=1, rewards=(1.0, -1.0), terminated=True),)
    destination = (
        DestinationEvent(
            source_progress=1,
            rewards=(1.0, -1.0),
            terminated=True,
        ),
        DestinationEvent(
            source_progress=1,
            rewards=(0.0, 0.0),
            delivered_rewards=(1.0, 0.0),
            terminated=True,
            cleanup=True,
        ),
        DestinationEvent(
            source_progress=1,
            rewards=(0.0, 0.0),
            delivered_rewards=(0.0, -1.0),
            terminated=True,
            cleanup=True,
        ),
    )

    alignment = align_traces(source, destination)

    assert [segment.kind for segment in alignment.segments] == [
        SegmentKind.TRANSITION,
        SegmentKind.TERMINAL_TAIL,
    ]
    tail = alignment.terminal_tail
    assert tail is not None
    assert tail.source_count == 0
    assert tail.source_before == tail.source_after == 1
    assert tail.destination_span == Span(1, 3)
    assert tail.destination_events == destination[1:]


def test_uncommitted_nonterminal_calls_are_a_zero_source_stutter_segment() -> None:
    source = (SourceEvent(progress=1),)
    destination = (
        DestinationEvent(source_progress=0),
        DestinationEvent(source_progress=0),
    )

    alignment = align_traces(source, destination)

    assert len(alignment.segments) == 1
    segment = alignment.segments[0]
    assert segment.kind is SegmentKind.STUTTER
    assert segment.source_count == 0
    assert segment.destination_count == 2
    assert segment.source_advance == 0


def test_destination_terminal_commit_also_identifies_an_unmarked_tail() -> None:
    alignment = align_traces(
        (SourceEvent(progress=1),),
        (
            DestinationEvent(source_progress=1, terminated=True),
            DestinationEvent(source_progress=1),
        ),
    )

    assert alignment.segments[-1].kind is SegmentKind.TERMINAL_TAIL


def test_alignment_preserves_a_regression_for_later_diagnosis() -> None:
    source = (SourceEvent(progress=1), SourceEvent(progress=2))
    destination = (
        DestinationEvent(source_progress=1),
        DestinationEvent(source_progress=0),
        DestinationEvent(source_progress=2),
    )

    alignment = align_traces(source, destination)

    assert len(alignment.segments) == 2
    assert alignment.segments[1].destination_events == destination[1:]
    assert segment_for_destination(alignment, 1) == (1, alignment.segments[1])
    assert segment_for_destination(alignment, -1) is None
    assert segment_for_destination(alignment, 3) is None


def test_termination_and_truncation_remain_distinct() -> None:
    terminated = DestinationEvent(source_progress=1, terminated=True)
    truncated = DestinationEvent(source_progress=1, truncated=True)

    assert terminated.terminal
    assert terminated.terminated and not terminated.truncated
    assert truncated.terminal
    assert truncated.truncated and not truncated.terminated
