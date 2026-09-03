from __future__ import annotations

import pytest

from marlrefine.alignment import align_traces
from marlrefine.localization import (
    LocalizationError,
    localize_all_divergences,
    localize_divergence,
    localize_first_divergence,
    materialize_replayable_prefix,
)
from marlrefine.model import DestinationEvent, SourceEvent, Span, Violation
from marlrefine.serialization import to_jsonable


def test_first_divergence_preserves_original_prefix_without_minimizing() -> None:
    source = (
        SourceEvent(1, metadata={"action": 2}),
        SourceEvent(2, metadata={"action": 4}),
    )
    destination = (
        DestinationEvent(
            0,
            metadata={"submitted_action": 2, "buffer_only": True},
        ),
        DestinationEvent(1, metadata={"submitted_action": 2}),
        DestinationEvent(2, metadata={"submitted_action": 4}),
    )
    alignment = align_traces(source, destination)
    late = Violation(
        obligation="lifecycle",
        code="late_divergence",
        message="synthetic late mismatch",
        segment_index=1,
        source_span=Span(1, 2),
        destination_span=Span(2, 3),
    )
    early = Violation(
        obligation="reward",
        code="early_divergence",
        message="synthetic buffer mismatch",
        segment_index=0,
        source_span=Span(0, 0),
        destination_span=Span(0, 1),
    )
    run = {
        "game_spec": "synthetic",
        "seed": 3,
        "source_events": to_jsonable(source),
        "destination_events": to_jsonable(destination),
        "alignment": to_jsonable(alignment),
        "violations": to_jsonable((late, early)),
        "summary": {
            "trace_policy_name": "synthetic",
            "reset_history": [],
        },
    }

    witness = localize_first_divergence(run)

    assert witness["selected_violation"]["code"] == "early_divergence"
    assert witness["boundary"]["segment_index"] == 0
    assert witness["boundary"]["recorded_segment_index"] == 0
    assert witness["boundary"]["source_event_stop"] == 0
    assert witness["boundary"]["destination_event_stop"] == 1
    prefix = witness["replayable_original_prefix"]
    assert "source_events" not in prefix
    assert prefix["ledger_prefixes"]["source_events"]["stop"] == 0
    assert prefix["ledger_prefixes"]["destination_events"]["stop"] == 1
    materialized = materialize_replayable_prefix(run, witness)
    assert materialized["source_events"] == []
    assert materialized["destination_events"] == to_jsonable(destination[:1])
    assert witness["prefix_to_original_event_ratio"] == {
        "numerator": 1,
        "denominator": 5,
    }
    assert witness["diagnostic_specificity"] == {
        "destination_phase": "buffer",
        "phase_attribution_basis": "destination_buffer_only_flag",
        "exact_destination_call_index": 0,
        "implicated_destination_call_count": 1,
        "destination_prefix_call_count": 1,
        "obligation_family": "reward",
        "violation_code": "early_divergence",
    }

    later_witness = localize_divergence(run, 0)
    assert later_witness["artifact_type"] == "marlrefine_divergence_witness"
    assert later_witness["selected_violation_index"] == 0
    assert later_witness["selected_violation"]["code"] == "late_divergence"
    assert later_witness["boundary"]["source_event_stop"] == 2
    assert later_witness["boundary"]["destination_event_stop"] == 3


def test_unsegmented_early_destination_span_precedes_later_segment_finding() -> None:
    source = (
        SourceEvent(1, metadata={"action": 2}),
        SourceEvent(2, metadata={"action": 4}),
    )
    destination = (
        DestinationEvent(0, metadata={"submitted_action": 2}),
        DestinationEvent(1, metadata={"submitted_action": 2}),
        DestinationEvent(2, metadata={"submitted_action": 4}),
    )
    alignment = align_traces(source, destination)
    late_segment_finding = Violation(
        obligation="lifecycle",
        code="late_segment_finding",
        message="synthetic late mismatch",
        segment_index=1,
        source_span=Span(1, 2),
        destination_span=Span(2, 3),
    )
    early_integration_finding = Violation(
        obligation="legal_actions",
        code="early_unsegmented_mask_mismatch",
        message="synthetic early integration mismatch",
        destination_span=Span(0, 1),
    )
    witness = localize_first_divergence(
        {
            "game_spec": "synthetic",
            "seed": 3,
            "source_events": to_jsonable(source),
            "destination_events": to_jsonable(destination),
            "alignment": to_jsonable(alignment),
            "violations": to_jsonable(
                (late_segment_finding, early_integration_finding)
            ),
            "summary": {"trace_policy_name": "synthetic"},
        }
    )

    assert witness["selected_violation"]["code"] == (
        "early_unsegmented_mask_mismatch"
    )
    assert witness["boundary"]["recorded_segment_index"] is None
    assert witness["boundary"]["segment_index"] == 0
    assert witness["boundary"]["destination_event_stop"] == 1
    assert witness["boundary"]["source_event_stop"] == 0
    assert "segment_inferred_from_destination_span" in witness["boundary"][
        "localization_evidence"
    ]


def test_localizer_reports_no_divergence_without_synthesizing_prefix() -> None:
    empty = align_traces((), ())
    witness = localize_first_divergence(
        {
            "source_events": [],
            "destination_events": [],
            "alignment": to_jsonable(empty),
            "violations": [],
        }
    )
    assert witness["status"] == "no_recorded_divergence"
    assert witness["schema_version"] == 3
    assert witness["diagnostic_specificity"] is None
    assert witness["replayable_original_prefix"] is None


def test_coordinate_free_findings_distinguish_setup_from_endpoint() -> None:
    setup_alignment = align_traces((), ())
    setup = localize_divergence(
        {
            "source_events": [],
            "destination_events": [],
            "alignment": to_jsonable(setup_alignment),
            "violations": to_jsonable(
                (
                    Violation(
                        obligation="setup",
                        code="construction_failed",
                        message="synthetic setup finding",
                    ),
                )
            ),
            "summary": {},
        },
        0,
    )
    assert setup["diagnostic_specificity"]["destination_phase"] == "setup"

    source = (SourceEvent(1),)
    destination = (DestinationEvent(1),)
    endpoint_alignment = align_traces(source, destination)
    endpoint = localize_divergence(
        {
            "source_events": to_jsonable(source),
            "destination_events": to_jsonable(destination),
            "alignment": to_jsonable(endpoint_alignment),
            "violations": to_jsonable(
                (
                    Violation(
                        obligation="return",
                        code="consumer_return_mismatch",
                        message="synthetic endpoint finding",
                    ),
                )
            ),
            "summary": {},
        },
        0,
    )
    assert endpoint["diagnostic_specificity"]["destination_phase"] == (
        "endpoint_or_whole_trace"
    )


def test_all_finding_localization_is_compact_and_hash_bound() -> None:
    source = (SourceEvent(1), SourceEvent(2))
    destination = (DestinationEvent(1), DestinationEvent(2))
    run = {
        "game_spec": "synthetic",
        "seed": 8,
        "source_events": to_jsonable(source),
        "destination_events": to_jsonable(destination),
        "alignment": to_jsonable(align_traces(source, destination)),
        "violations": to_jsonable(
            (
                Violation(
                    obligation="reward",
                    code="first",
                    message="first",
                    source_span=Span(0, 1),
                    destination_span=Span(0, 1),
                ),
                Violation(
                    obligation="lifecycle",
                    code="second",
                    message="second",
                    source_span=Span(1, 2),
                    destination_span=Span(1, 2),
                ),
            )
        ),
        "summary": {"trace_policy_name": "synthetic", "chance_tape": [3]},
    }

    witnesses = localize_all_divergences(run)

    assert [item["selected_violation_index"] for item in witnesses] == [0, 1]
    assert all(
        isinstance(item["replayable_original_prefix"]["ledger_prefixes"], dict)
        for item in witnesses
    )
    assert materialize_replayable_prefix(run, witnesses[1])["source_events"] == (
        to_jsonable(source)
    )

    tampered = {**run, "source_events": to_jsonable((SourceEvent(9), *source[1:]))}
    with pytest.raises(LocalizationError, match="raw-run hash"):
        materialize_replayable_prefix(tampered, witnesses[1])
