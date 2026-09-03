"""Pure first-divergence localization over serialized trace evidence.

The localizer never executes a game and never mutates or shortens the observed
trace.  It selects the earliest boundary named by an existing violation and
exports compact, hash-bound references to the *original* source/destination
ledger prefixes through that boundary, together with the frozen invocation
context needed to replay it.  The event arrays remain in the sealed raw batch
and can be materialized with :func:`materialize_replayable_prefix`.  This is
witness localization, not delta debugging or semantic minimization.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from marlrefine.serialization import to_jsonable

LOCALIZER_ID = "marlrefine_original_prefix_v3"


class LocalizationError(ValueError):
    """A serialized run cannot be localized without inventing evidence."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LocalizationError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise LocalizationError(f"{label} must be an array")
    return value


def _optional_index(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LocalizationError(f"{label} must be a non-negative integer or null")
    return value


def _span_stop(value: Any, label: str, limit: int) -> int | None:
    if value is None:
        return None
    span = _mapping(value, label)
    start = _optional_index(span.get("start"), f"{label}.start")
    stop = _optional_index(span.get("stop"), f"{label}.stop")
    if start is None or stop is None or stop < start or stop > limit:
        raise LocalizationError(f"{label} is outside its original ledger")
    return stop


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        to_jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_prefix_hashes(
    values: Sequence[Any], stops: set[int]
) -> dict[int, str]:
    """Hash requested canonical JSON array prefixes in one forward pass."""
    if any(stop < 0 or stop > len(values) for stop in stops):
        raise LocalizationError("requested prefix lies outside its original ledger")
    requested = set(stops)
    digests: dict[int, str] = {}
    running = hashlib.sha256()
    running.update(b"[")
    if 0 in requested:
        empty = running.copy()
        empty.update(b"]")
        digests[0] = empty.hexdigest()
    for index, value in enumerate(values, start=1):
        if index > 1:
            running.update(b",")
        running.update(
            json.dumps(
                to_jsonable(value),
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if index in requested:
            prefix = running.copy()
            prefix.update(b"]")
            digests[index] = prefix.hexdigest()
    return digests


def _ledger_prefix_reference(
    field: str,
    stop: int,
    digest: str,
) -> dict[str, Any]:
    return {
        "raw_run_field": field,
        "start": 0,
        "stop": stop,
        "canonical_json_sha256": digest,
    }


def _boundary_for_violation(
    violation: Mapping[str, Any],
    *,
    violation_index: int,
    segments: Sequence[Any],
    source_events: Sequence[Any],
    destination_events: Sequence[Any],
    source_length: int,
    destination_length: int,
) -> dict[str, Any]:
    recorded_segment_index = _optional_index(
        violation.get("segment_index"),
        f"violations[{violation_index}].segment_index",
    )
    source_stop = _span_stop(
        violation.get("source_span"),
        f"violations[{violation_index}].source_span",
        source_length,
    )
    destination_stop = _span_stop(
        violation.get("destination_span"),
        f"violations[{violation_index}].destination_span",
        destination_length,
    )
    evidence: list[str] = []
    if source_stop is not None:
        evidence.append("source_span")
    if destination_stop is not None:
        evidence.append("destination_span")

    segment_index = recorded_segment_index
    if segment_index is not None:
        evidence.append("recorded_segment_index")
    elif destination_stop is not None and destination_stop > 0:
        destination_index = destination_stop - 1
        for candidate_index, candidate_value in enumerate(segments):
            candidate = _mapping(candidate_value, f"segments[{candidate_index}]")
            span = _mapping(
                candidate.get("destination_span"),
                f"segments[{candidate_index}].destination_span",
            )
            if int(span["start"]) <= destination_index < int(span["stop"]):
                segment_index = candidate_index
                evidence.append("segment_inferred_from_destination_span")
                break
    elif source_stop is not None and source_stop > 0:
        source_index = source_stop - 1
        for candidate_index, candidate_value in enumerate(segments):
            candidate = _mapping(candidate_value, f"segments[{candidate_index}]")
            span = _mapping(
                candidate.get("source_span"),
                f"segments[{candidate_index}].source_span",
            )
            if int(span["start"]) <= source_index < int(span["stop"]):
                segment_index = candidate_index
                evidence.append("segment_inferred_from_source_span")
                break

    if segment_index is not None:
        if segment_index >= len(segments):
            raise LocalizationError(
                f"violations[{violation_index}].segment_index is out of range"
            )
        segment = _mapping(segments[segment_index], f"segments[{segment_index}]")
        if destination_stop is None:
            destination_stop = _span_stop(
                segment.get("destination_span"),
                f"segments[{segment_index}].destination_span",
                destination_length,
            )
            evidence.append("destination_stop_from_segment")

    # When a destination call is explicitly located, its observed source
    # progress gives the exact source prefix at that call. This avoids pulling
    # a later commit event into an early buffer-only witness merely because the
    # aligner groups both calls in one segment.
    if source_stop is None and destination_stop is not None:
        if destination_stop == 0:
            source_stop = 0
        else:
            destination_event = _mapping(
                destination_events[destination_stop - 1],
                f"destination_events[{destination_stop - 1}]",
            )
            observed_progress = destination_event.get("source_progress")
            if isinstance(observed_progress, bool) or not isinstance(
                observed_progress, int
            ):
                raise LocalizationError(
                    "destination source_progress must be an integer"
                )
            source_stop = 0
            for source_event_index, source_event_value in enumerate(source_events):
                source_event = _mapping(
                    source_event_value,
                    f"source_events[{source_event_index}]",
                )
                progress = source_event.get("progress")
                if isinstance(progress, bool) or not isinstance(progress, int):
                    raise LocalizationError("source progress must be an integer")
                if progress > observed_progress:
                    break
                source_stop = source_event_index + 1
        evidence.append("source_stop_from_destination_progress")

    if source_stop is None and segment_index is not None:
        segment = _mapping(segments[segment_index], f"segments[{segment_index}]")
        source_stop = _span_stop(
            segment.get("source_span"),
            f"segments[{segment_index}].source_span",
            source_length,
        )
        evidence.append("source_stop_from_segment")

    # A violation without coordinates is necessarily a setup boundary when
    # both ledgers are empty, and otherwise a whole-trace/end-point finding.
    if source_stop is None:
        source_stop = source_length
        evidence.append(
            "empty_setup_boundary" if source_length == 0 else "full_source_fallback"
        )
    if destination_stop is None:
        destination_stop = destination_length
        evidence.append(
            "empty_setup_boundary"
            if destination_length == 0
            else "full_destination_fallback"
        )
    return {
        "segment_index": segment_index,
        "recorded_segment_index": recorded_segment_index,
        "source_event_stop": source_stop,
        "destination_event_stop": destination_stop,
        "localization_evidence": evidence,
    }


def _boundary_key(
    boundary: Mapping[str, Any], violation_index: int
) -> tuple[int, int, int, int]:
    segment_index = boundary["segment_index"]
    # Destination/source prefix stops put explicitly spanned integration
    # findings and segment-indexed generic findings on one chronology.
    segment_order = segment_index if isinstance(segment_index, int) else 2**63 - 1
    return (
        int(boundary["destination_event_stop"] or 0),
        int(boundary["source_event_stop"] or 0),
        segment_order,
        violation_index,
    )


def _same_boundary(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        left[field] == right[field]
        for field in (
            "segment_index",
            "source_event_stop",
            "destination_event_stop",
        )
    )


def _diagnostic_specificity(
    violation: Mapping[str, Any],
    boundary: Mapping[str, Any],
    *,
    destination_events: Sequence[Any],
    initial_progress: int,
) -> dict[str, Any]:
    """Describe what the recorded finding localizes without claiming causality."""
    destination_span = violation.get("destination_span")
    start: int | None = None
    stop: int | None = None
    if isinstance(destination_span, Mapping):
        start = _optional_index(destination_span.get("start"), "destination_span.start")
        stop = _optional_index(destination_span.get("stop"), "destination_span.stop")

    exact_index: int | None = None
    implicated_calls: int | None = None
    if not destination_events and int(boundary.get("source_event_stop") or 0) == 0:
        phase = "setup"
        basis = "empty_ledgers_without_destination_span"
    elif int(boundary.get("destination_event_stop") or 0) == len(
        destination_events
    ):
        phase = "endpoint_or_whole_trace"
        basis = "coordinate_free_finding_at_full_destination_prefix"
    else:
        phase = "boundary_only"
        basis = "coordinate_free_finding_before_destination_endpoint"
    if start is not None and stop is not None:
        implicated_calls = stop - start
        if implicated_calls == 0:
            phase = "pre_call_boundary"
            basis = "zero_width_destination_span"
        elif implicated_calls == 1 and start < len(destination_events):
            exact_index = start
            event = _mapping(destination_events[start], f"destination_events[{start}]")
            metadata = _mapping(
                event.get("metadata"),
                f"destination_events[{start}].metadata",
            )
            if event.get("cleanup") is True:
                phase = "cleanup"
                basis = "destination_cleanup_flag"
            elif metadata.get("buffer_only") is True:
                phase = "buffer"
                basis = "destination_buffer_only_flag"
            else:
                previous_progress = (
                    initial_progress
                    if start == 0
                    else int(
                        _mapping(
                            destination_events[start - 1],
                            f"destination_events[{start - 1}]",
                        ).get("source_progress")
                    )
                )
                observed_progress = event.get("source_progress")
                if isinstance(observed_progress, int) and (
                    observed_progress > previous_progress
                ):
                    phase = "commit"
                    basis = "source_progress_advance"
                else:
                    phase = "other_stutter"
                    basis = "nonadvancing_destination_call"
        elif implicated_calls > 1:
            phase = "multi_call_span"
            basis = "multi_call_destination_span"

    return {
        "destination_phase": phase,
        "phase_attribution_basis": basis,
        "exact_destination_call_index": exact_index,
        "implicated_destination_call_count": implicated_calls,
        "destination_prefix_call_count": int(
            boundary.get("destination_event_stop") or 0
        ),
        "obligation_family": violation.get("obligation"),
        "violation_code": violation.get("code"),
    }


def _localization_inputs(
    run: Mapping[str, Any],
) -> tuple[
    Sequence[Any],
    Sequence[Any],
    Sequence[Any],
    Mapping[str, Any],
    list[Mapping[str, Any]],
    list[dict[str, Any]],
]:
    """Validate one run and derive every recorded finding boundary once."""
    source_events = _sequence(run.get("source_events"), "run.source_events")
    destination_events = _sequence(
        run.get("destination_events"), "run.destination_events"
    )
    violations = _sequence(run.get("violations"), "run.violations")
    alignment = _mapping(run.get("alignment"), "run.alignment")
    segments = _sequence(alignment.get("segments"), "run.alignment.segments")

    boundaries: list[dict[str, Any]] = []
    checked_violations: list[Mapping[str, Any]] = []
    for index, value in enumerate(violations):
        violation = _mapping(value, f"violations[{index}]")
        checked_violations.append(violation)
        boundaries.append(
            _boundary_for_violation(
                violation,
                violation_index=index,
                segments=segments,
                source_events=source_events,
                destination_events=destination_events,
                source_length=len(source_events),
                destination_length=len(destination_events),
            )
        )
    return (
        source_events,
        destination_events,
        violations,
        alignment,
        checked_violations,
        boundaries,
    )


def _no_divergence_witness(
    source_events: Sequence[Any], destination_events: Sequence[Any]
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "artifact_type": "marlrefine_first_divergence_witness",
        "localizer_id": LOCALIZER_ID,
        "status": "no_recorded_divergence",
        "original_lengths": {
            "source_events": len(source_events),
            "destination_events": len(destination_events),
            "ledger_events": len(source_events) + len(destination_events),
        },
        "boundary": None,
        "selected_violation": None,
        "coincident_violation_indices": [],
        "diagnostic_specificity": None,
        "replayable_original_prefix": None,
    }


def _build_witness(
    run: Mapping[str, Any],
    *,
    source_events: Sequence[Any],
    destination_events: Sequence[Any],
    alignment: Mapping[str, Any],
    checked_violations: Sequence[Mapping[str, Any]],
    boundaries: Sequence[Mapping[str, Any]],
    selected_index: int,
    artifact_type: str,
    source_prefix_hashes: Mapping[int, str],
    destination_prefix_hashes: Mapping[int, str],
    raw_run_sha256: str,
) -> dict[str, Any]:
    """Build one compact reference without copying either ledger prefix."""
    selected_boundary = boundaries[selected_index]
    same_boundary = [
        index
        for index, boundary in enumerate(boundaries)
        if _same_boundary(boundary, selected_boundary)
    ]
    source_stop = int(selected_boundary["source_event_stop"] or 0)
    destination_stop = int(selected_boundary["destination_event_stop"] or 0)
    replay_context = dict(_mapping(run.get("summary"), "run.summary"))
    prefix_reference = {
        "game_spec": run.get("game_spec"),
        "seed": run.get("seed"),
        "initial_source_progress": alignment.get("initial_progress"),
        "raw_run_sha256": raw_run_sha256,
        "ledger_prefixes": {
            "source_events": _ledger_prefix_reference(
                "source_events",
                source_stop,
                source_prefix_hashes[source_stop],
            ),
            "destination_events": _ledger_prefix_reference(
                "destination_events",
                destination_stop,
                destination_prefix_hashes[destination_stop],
            ),
        },
        # Policy, parameters, reset transcript, chance tape, and decision-cap
        # inputs remain in the raw run and are hash-bound here. They are
        # replay evidence, never an oracle, and are not duplicated per finding.
        "replay_context": {
            "raw_run_field": "summary",
            "canonical_json_sha256": _canonical_sha256(replay_context),
        },
    }
    prefix_reference["sha256"] = _canonical_sha256(prefix_reference)
    prefix_length = source_stop + destination_stop
    original_length = len(source_events) + len(destination_events)
    ratio = (
        None
        if original_length == 0
        else {"numerator": prefix_length, "denominator": original_length}
    )
    return {
        "schema_version": 3,
        "artifact_type": artifact_type,
        "localizer_id": LOCALIZER_ID,
        "status": "localized_original_prefix_reference",
        "original_lengths": {
            "source_events": len(source_events),
            "destination_events": len(destination_events),
            "ledger_events": original_length,
        },
        "prefix_lengths": {
            "source_events": source_stop,
            "destination_events": destination_stop,
            "ledger_events": prefix_length,
        },
        "prefix_to_original_event_ratio": ratio,
        "boundary": dict(selected_boundary),
        "selected_violation_index": selected_index,
        "selected_violation": dict(checked_violations[selected_index]),
        "coincident_violation_indices": same_boundary,
        "diagnostic_specificity": _diagnostic_specificity(
            checked_violations[selected_index],
            selected_boundary,
            destination_events=destination_events,
            initial_progress=int(alignment.get("initial_progress") or 0),
        ),
        "replayable_original_prefix": prefix_reference,
    }


def _selected_witnesses(
    run: Mapping[str, Any],
    selected_indices: Sequence[int],
    *,
    first_divergence_index: int | None = None,
) -> tuple[dict[str, Any], ...]:
    (
        source_events,
        destination_events,
        violations,
        alignment,
        checked_violations,
        boundaries,
    ) = _localization_inputs(run)
    if not violations:
        if selected_indices:
            raise LocalizationError("cannot select a violation from an empty run")
        return (_no_divergence_witness(source_events, destination_events),)

    for selected_index in selected_indices:
        if (
            isinstance(selected_index, bool)
            or not isinstance(selected_index, int)
            or selected_index < 0
            or selected_index >= len(boundaries)
        ):
            raise LocalizationError("violation_index is out of range")

    source_stops = {
        int(boundaries[index]["source_event_stop"] or 0)
        for index in selected_indices
    }
    destination_stops = {
        int(boundaries[index]["destination_event_stop"] or 0)
        for index in selected_indices
    }
    source_prefix_hashes = _canonical_prefix_hashes(source_events, source_stops)
    destination_prefix_hashes = _canonical_prefix_hashes(
        destination_events, destination_stops
    )
    raw_run_sha256 = _canonical_sha256(run)
    return tuple(
        _build_witness(
            run,
            source_events=source_events,
            destination_events=destination_events,
            alignment=alignment,
            checked_violations=checked_violations,
            boundaries=boundaries,
            selected_index=index,
            artifact_type=(
                "marlrefine_first_divergence_witness"
                if first_divergence_index == index
                else "marlrefine_divergence_witness"
            ),
            source_prefix_hashes=source_prefix_hashes,
            destination_prefix_hashes=destination_prefix_hashes,
            raw_run_sha256=raw_run_sha256,
        )
        for index in selected_indices
    )


def materialize_replayable_prefix(
    run: Mapping[str, Any], witness: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve and verify one compact witness against its sealed raw run."""
    prefix = _mapping(
        witness.get("replayable_original_prefix"),
        "witness.replayable_original_prefix",
    )
    reference_without_digest = {
        key: value for key, value in prefix.items() if key != "sha256"
    }
    if prefix.get("sha256") != _canonical_sha256(reference_without_digest):
        raise LocalizationError("witness prefix-reference hash differs")
    if prefix.get("raw_run_sha256") != _canonical_sha256(run):
        raise LocalizationError("witness raw-run hash differs from supplied run")
    alignment = _mapping(run.get("alignment"), "run.alignment")
    if (
        prefix.get("game_spec") != run.get("game_spec")
        or prefix.get("seed") != run.get("seed")
        or prefix.get("initial_source_progress")
        != alignment.get("initial_progress")
    ):
        raise LocalizationError("witness replay invocation differs from supplied run")
    ledger_prefixes = _mapping(
        prefix.get("ledger_prefixes"),
        "witness replay ledger_prefixes",
    )
    materialized: dict[str, Any] = {
        "game_spec": prefix.get("game_spec"),
        "seed": prefix.get("seed"),
        "initial_source_progress": prefix.get("initial_source_progress"),
    }
    for field in ("source_events", "destination_events"):
        reference = _mapping(
            ledger_prefixes.get(field),
            f"witness replay ledger_prefixes.{field}",
        )
        if reference.get("raw_run_field") != field or reference.get("start") != 0:
            raise LocalizationError(f"witness {field} reference is invalid")
        stop = _optional_index(reference.get("stop"), f"witness {field}.stop")
        values = _sequence(run.get(field), f"run.{field}")
        if stop is None or stop > len(values):
            raise LocalizationError(f"witness {field} reference is out of range")
        selected = list(values[:stop])
        if reference.get("canonical_json_sha256") != _canonical_sha256(selected):
            raise LocalizationError(f"witness {field} prefix hash differs")
        materialized[field] = selected
    replay_reference = _mapping(
        prefix.get("replay_context"), "witness replay_context"
    )
    if replay_reference.get("raw_run_field") != "summary":
        raise LocalizationError("witness replay-context reference is invalid")
    replay_context = dict(_mapping(run.get("summary"), "run.summary"))
    if replay_reference.get("canonical_json_sha256") != _canonical_sha256(
        replay_context
    ):
        raise LocalizationError("witness replay-context hash differs")
    materialized["replay_context"] = replay_context
    materialized["sha256"] = _canonical_sha256(materialized)
    return materialized


def localize_all_divergences(run: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Localize every recorded finding with one boundary/hash pass per run."""
    violations = _sequence(run.get("violations"), "run.violations")
    if not violations:
        return ()
    return _selected_witnesses(run, tuple(range(len(violations))))


def _localize_divergence(
    run: Mapping[str, Any],
    violation_index: int | None,
) -> dict[str, Any]:
    """Return one recorded divergent boundary as a compact raw-run reference."""
    violations = _sequence(run.get("violations"), "run.violations")
    if not violations:
        if violation_index is not None:
            raise LocalizationError("cannot select a violation from an empty run")
        source_events = _sequence(run.get("source_events"), "run.source_events")
        destination_events = _sequence(
            run.get("destination_events"), "run.destination_events"
        )
        return _no_divergence_witness(source_events, destination_events)

    if violation_index is None:
        (
            _source_events,
            _destination_events,
            _violations,
            _alignment,
            _checked_violations,
            boundaries,
        ) = _localization_inputs(run)
        selected_index = min(
            range(len(boundaries)),
            key=lambda index: _boundary_key(boundaries[index], index),
        )
        return _selected_witnesses(
            run,
            (selected_index,),
            first_divergence_index=selected_index,
        )[0]
    else:
        return _selected_witnesses(run, (violation_index,))[0]


def localize_divergence(
    run: Mapping[str, Any], violation_index: int
) -> dict[str, Any]:
    """Localize a specified recorded finding without requiring it to be first."""
    return _localize_divergence(run, violation_index)


def localize_first_divergence(run: Mapping[str, Any]) -> dict[str, Any]:
    """Return the chronologically first recorded divergence and original prefix."""
    return _localize_divergence(run, None)
