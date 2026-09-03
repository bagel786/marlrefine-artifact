"""Pure alignment of source transitions and destination API calls."""

from __future__ import annotations

from collections.abc import Iterable
from numbers import Integral

from .model import (
    AlignedSegment,
    Alignment,
    DestinationEvent,
    SegmentKind,
    SourceEvent,
    Span,
)


def _initial_progress(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError("initial_progress must be an integer")
    result = int(value)
    if result < 0:
        raise ValueError("initial_progress must be non-negative")
    return result


def align_traces(
    source_events: Iterable[SourceEvent],
    destination_events: Iterable[DestinationEvent],
    *,
    initial_progress: int = 0,
) -> Alignment:
    """Align two traces using destination-observed monotone source progress.

    The algorithm is deterministic and lossless:

    * destination calls that leave progress unchanged are held as buffers;
    * the next advancing call commits those buffers in the same segment;
    * a jump over several source events creates an n:1 transition segment;
    * uncommitted calls form a zero-source stutter segment; and
    * calls after a terminal source boundary form a terminal-only tail.

    Semantic defects such as regressions, overrun, and incomplete progress are
    intentionally not raised here.  The aligner preserves those events so the
    obligation layer can return structured counterexamples.
    """

    initial = _initial_progress(initial_progress)
    source = tuple(source_events)
    destination = tuple(destination_events)

    segments: list[AlignedSegment] = []
    source_cursor = 0
    destination_segment_start = 0
    reached_progress = initial

    for destination_index, event in enumerate(destination):
        if event.source_progress <= reached_progress:
            # Equal progress is a normal buffer.  A regression is retained in
            # the same pending group and diagnosed by the obligation layer.
            continue

        source_segment_start = source_cursor
        while (
            source_cursor < len(source)
            and source[source_cursor].progress <= event.source_progress
        ):
            source_cursor += 1

        destination_stop = destination_index + 1
        segments.append(
            AlignedSegment(
                kind=SegmentKind.TRANSITION,
                source_before=reached_progress,
                source_after=event.source_progress,
                source_span=Span(source_segment_start, source_cursor),
                destination_span=Span(
                    destination_segment_start,
                    destination_stop,
                ),
                source_events=source[source_segment_start:source_cursor],
                destination_events=destination[
                    destination_segment_start:destination_stop
                ],
            )
        )
        reached_progress = event.source_progress
        destination_segment_start = destination_stop

    if destination_segment_start < len(destination):
        pending = destination[destination_segment_start:]
        source_limit = max(
            (event.progress for event in source),
            default=initial,
        )
        source_ended = any(
            event.progress == source_limit and event.terminal for event in source
        )
        destination_reports_end = any(event.terminal for event in pending)
        preceding_commit_ended = (
            destination_segment_start > 0
            and destination[destination_segment_start - 1].terminal
        )
        explicit_cleanup = any(event.cleanup for event in pending)
        is_terminal_tail = reached_progress >= source_limit and (
            source_ended
            or destination_reports_end
            or preceding_commit_ended
            or explicit_cleanup
        )
        kind = SegmentKind.TERMINAL_TAIL if is_terminal_tail else SegmentKind.STUTTER
        segments.append(
            AlignedSegment(
                kind=kind,
                source_before=reached_progress,
                source_after=reached_progress,
                source_span=Span(source_cursor, source_cursor),
                destination_span=Span(destination_segment_start, len(destination)),
                source_events=(),
                destination_events=pending,
            )
        )

    return Alignment(
        source_events=source,
        destination_events=destination,
        segments=tuple(segments),
        initial_progress=initial,
    )


def segment_for_destination(
    alignment: Alignment,
    destination_index: int,
) -> tuple[int, AlignedSegment] | None:
    """Return ``(segment_index, segment)`` containing a destination event."""
    if destination_index < 0:
        return None
    for segment_index, segment in enumerate(alignment.segments):
        if (
            segment.destination_span.start
            <= destination_index
            < segment.destination_span.stop
        ):
            return segment_index, segment
    return None
