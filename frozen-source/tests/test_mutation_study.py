from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from marlrefine import mutation_study
from marlrefine.adapters.openspiel_shimmy import run_trace
from marlrefine.alignment import align_traces
from marlrefine.baselines import BASELINE_NAMES
from marlrefine.evaluation import (
    build_obligation_evaluations,
    validate_serialized_obligation_evaluations,
)
from marlrefine.model import DestinationEvent, SourceEvent, Violation
from marlrefine.mutation_study import (
    MUTATION_BATCH_ARTIFACT_TYPE,
    MUTATION_BATCH_SCHEMA_VERSION,
    MutationBatchValidationError,
    MutationGateError,
    _apply_outcome_blind_selection,
    _canonical_digest,
    _finding_phase,
    _new_finding_signatures,
    _reference_acceptable,
    build_mutation_manifest,
    execute_mutation_study,
    validate_mutation_batch,
    verify_mutation_manifest,
)
from marlrefine.mutations import (
    CANDIDATE_POOL,
    MUTANTS_PER_FAMILY,
    MUTATION_FAMILIES,
    MUTATION_PROTOCOL_ID,
    PROGRESS_INSTRUMENTATION_CONTROLS,
    adapter_class_for,
    candidate_manifest_records,
)
from marlrefine.policies import get_trace_policy
from marlrefine.serialization import to_jsonable


def _environment() -> dict[str, object]:
    return {
        "python": {"implementation": "CPython", "version": "3.13.2"},
        "packages": {"marlrefine": "0.1.0"},
        "installed_distribution_sha256": {"shimmy": "a" * 64},
        "uv_lock_sha256": "b" * 64,
        "source_tree_sha256": "c" * 64,
        "git_revision": None,
        "git_dirty": True,
    }


def test_manifest_generation_is_declarative_and_executes_no_trace(monkeypatch) -> None:
    monkeypatch.setattr(mutation_study, "runtime_provenance", _environment)
    monkeypatch.setattr(
        mutation_study,
        "run_trace",
        lambda *args, **kwargs: pytest.fail("manifest generation executed a trace"),
    )

    payload = build_mutation_manifest(
        manifest_status="frozen_pending_archive",
        source_git_revision="d" * 40,
    )

    assert payload["prearchive_activity"]["candidate_or_control_outcomes_executed"] == 0
    assert payload["selection"]["candidate_pool_total"] == 48
    assert payload["selection"]["required_total"] == 24
    assert len(payload["candidates"]) == 48
    assert len(payload["progress_instrumentation_controls"]) == 2
    assert "full finding signature" in payload["scoring"]["kill_definition"]
    assert payload["environment"]["git_revision"] == "d" * 40
    assert payload["environment"]["git_dirty"] is False


def test_study_manifest_binds_exact_mutation_manifest_bytes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(mutation_study, "runtime_provenance", _environment)
    path = tmp_path / "mutation.json"
    payload = build_mutation_manifest(
        manifest_status="frozen_pending_archive",
        source_git_revision="d" * 40,
    )
    path.write_text(
        json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    study_manifest = {"mutation_evaluation": {"mutation_manifest_sha256": digest}}

    observed = verify_mutation_manifest(
        path,
        study_manifest,
        source_tree_sha256="c" * 64,
    )

    assert observed == payload
    study_manifest["mutation_evaluation"]["mutation_manifest_sha256"] = "e" * 64
    with pytest.raises(MutationGateError, match="does not bind"):
        verify_mutation_manifest(
            path,
            study_manifest,
            source_tree_sha256="c" * 64,
        )


def test_execution_checks_public_archive_gate_before_any_trace(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        mutation_study,
        "build_prospective_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            MutationGateError("synthetic closed gate")
        ),
    )
    monkeypatch.setattr(
        mutation_study,
        "run_trace",
        lambda *args, **kwargs: pytest.fail("closed archive gate executed a trace"),
    )

    with pytest.raises(MutationGateError, match="closed gate"):
        execute_mutation_study(
            tmp_path / "study.json",
            tmp_path / "mutation.json",
            tmp_path / "receipt.json",
            tmp_path / "output.json",
        )


def test_special_node_early_stop_obligation_ledger_revalidates() -> None:
    candidate = next(
        item
        for item in CANDIDATE_POOL
        if item.candidate_id == "mut-special-node-kind-02"
    )
    run = to_jsonable(
        run_trace(
            candidate.game_spec,
            seed=candidate.environment_seed,
            trace_policy=candidate.trace_policy_name,
            max_destination_calls=2000,
            max_source_decisions=candidate.max_source_decisions,
            adapter_class=adapter_class_for(candidate),
        )
    )

    validate_serialized_obligation_evaluations(
        run["obligation_evaluations"],
        violations=run["violations"],
        alignment=run["alignment"],
        summary=run["summary"],
        caller_supplied_nondefault=run["summary"][
            "caller_supplied_nondefault_configuration"
        ],
        label="special-node early-stop ledger",
    )
    row = next(
        item for item in run["obligation_evaluations"] if item["obligation_id"] == "O8"
    )
    assert row["evaluation_count"] == 2


def test_manifest_verification_rejects_stale_source_without_game_use(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(mutation_study, "runtime_provenance", _environment)
    payload = build_mutation_manifest(
        manifest_status="frozen_pending_archive",
        source_git_revision="d" * 40,
    )
    path = tmp_path / "mutation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    study_manifest = {"mutation_evaluation": {"mutation_manifest_sha256": digest}}

    with pytest.raises(MutationGateError, match="source identity is stale"):
        verify_mutation_manifest(
            path,
            study_manifest,
            source_tree_sha256="f" * 64,
        )


def _selection_record(
    candidate_id: str,
    behavior: str,
    *,
    base_eligible: bool = True,
    semantic: int = 0,
    execution: int = 0,
) -> dict[str, object]:
    return {
        "candidate": {
            "candidate_id": candidate_id,
            "family": "reward_accounting",
        },
        "selection_inputs": {
            "behavior_delta_sha256": behavior,
            "base_eligible_before_uniqueness": base_eligible,
        },
        "kill": {
            "new_semantic_signature_count": semantic,
            "new_execution_signature_count": execution,
        },
        # Deliberately contradictory verdict-like fields prove they are unused.
        "mutant": {"status": "pass" if semantic else "fail"},
        "ablation_signals": {"synthetic": not semantic},
    }


def test_selection_is_unique_and_does_not_consume_verdicts() -> None:
    records = [
        _selection_record("a", "delta-a", semantic=1),
        _selection_record("a-duplicate", "delta-a", execution=1),
        _selection_record("b", "delta-b"),
        _selection_record("c", "delta-c", semantic=2),
        _selection_record("d", "delta-d", execution=1),
        _selection_record("e", "delta-e", semantic=1),
    ]

    selected, counts = _apply_outcome_blind_selection(records)

    assert selected["reward_accounting"] == ["a", "b", "c", "d"]
    assert counts["reward_accounting"] == 4
    assert (
        records[1]["selection_inputs"]["unique_behavior_among_prior_selected"] is False
    )
    assert records[0]["kill"]["status"] == "semantic_kill"
    assert records[3]["kill"]["status"] == "semantic_kill"
    assert records[4]["kill"]["status"] == "crash_only_kill"
    assert records[5]["kill"]["status"] == "not_selected"


def test_kill_delta_uses_full_signatures_and_separates_execution() -> None:
    shared = Violation("semantic", "same", "same", expected=1, observed=2)
    changed_location = Violation(
        "semantic",
        "same",
        "same",
        segment_index=1,
        expected=1,
        observed=2,
    )
    crash = Violation("trace_execution", "adapter_step_failed", "boom")
    reference = SimpleNamespace(violations=(shared,))
    mutant = SimpleNamespace(violations=(shared, changed_location, crash))

    delta = _new_finding_signatures(reference, mutant)

    assert len(delta) == 2
    assert {item["category"] for item in delta} == {"semantic", "execution"}
    assert all(len(item["signature_sha256"]) == 64 for item in delta)


def test_typed_mean_field_refusal_is_acceptable_without_checker_verdict() -> None:
    candidate = next(
        item
        for item in CANDIDATE_POOL
        if item.operator == "mean_field_bypass_rejection"
    )
    run = SimpleNamespace(
        applicable=True,
        violations=(Violation("trace_execution", "adapter_setup_failed", "x"),),
        summary={
            "setup_status": "error:adapter_setup:NotImplementedError:legacy text",
            "stop_reason": "adapter_setup_failed",
            "unsupported_capability": {
                "capability_id": "openspiel_mean_field_distribution_update_v1",
            },
        },
    )

    assert _reference_acceptable(candidate, run) is True


def _empty_serialized_run(
    *,
    game_spec: str = "synthetic",
    seed: int = 0,
    projection_marker: str = "reference",
    progress_control_id: str | None = None,
    trace_policy_name: str = "smallest_legal",
    max_source_decisions: int = 40,
    adapter_class: str = "CombinedRepairV0",
    rewards: tuple[float, ...] = (1.0, 2.0),
    annotated_progress: int = 1,
    violations: tuple[Violation, ...] = (),
) -> dict[str, object]:
    policy = get_trace_policy(trace_policy_name)
    source = (
        SourceEvent(
            progress=1,
            rewards=(1.0, 2.0),
            terminated=True,
            metadata={},
        ),
    )
    destination = (
        DestinationEvent(
            source_progress=annotated_progress,
            rewards=rewards,
            delivered_rewards=rewards,
            terminated=True,
            metadata={
                "progress_instrumentation": {
                    "method_id": "independent_native_replay_event_count_v1",
                    "progress_before": 0,
                    "progress_after": 1,
                    "annotated_progress_after": annotated_progress,
                    "replayed_source_event_count": 1,
                    "source_event_progresses": (1,),
                    "wrapped_history_delta": (0,),
                }
            },
        ),
    )
    alignment = align_traces(source, destination)
    summary = {
        "protocol_version": mutation_study.PROTOCOL_VERSION,
        "trace_policy_name": policy.name,
        "trace_policy_id": policy.policy_id,
        "trace_policy_engine_id": mutation_study.POLICY_ENGINE_ID,
        "trace_policy_seed": policy.seed,
        "chance_policy_id": mutation_study.CHANCE_POLICY_ID,
        "progress_annotation_method_id": (
            "independent_native_replay_event_count_v1"
        ),
        "requested_seed": seed,
        "requested_max_destination_calls": (
            mutation_study.MUTATION_MAX_DESTINATION_CALLS
        ),
        "requested_max_source_decisions": max_source_decisions,
        "progress_annotation_control_id": progress_control_id,
        "setup_status": "pass",
        "adapter_class": adapter_class,
        "stop_reason": "destination_episode_end",
        "destination_calls": 1,
        "source_transitions": 1,
        "source_decisions": 1,
        "source_terminal": True,
        "adapter_agents_remaining": 0,
        "chance_event_count": 0,
        "adapter_parameters": {"projection_marker": projection_marker},
        "adapter_game_length_at_reset": 0,
        "adapter_game_length_final": 1,
        "adapter_decision_clock_elapsed_final": 1,
        "caller_supplied_nondefault_configuration": False,
        "violation_count": len(violations),
    }
    return {
        "game_spec": game_spec,
        "seed": seed,
        "applicable": True,
        "source_events": to_jsonable(source),
        "destination_events": to_jsonable(destination),
        "alignment": to_jsonable(alignment),
        "violations": to_jsonable(violations),
        "baselines": [
            {
                "baseline": name,
                "applicable": True,
                "findings": [],
                "reason": None,
            }
            for name in BASELINE_NAMES
        ],
        "obligation_evaluations": to_jsonable(
            build_obligation_evaluations(
                alignment,
                violations,
                caller_supplied_nondefault=False,
                configuration_evaluated=True,
                state_kind_evaluation_count=0,
                interface_evaluation_count=0,
                complete_episode=True,
                unresolved="no_applicable_site",
            )
        ),
        "summary": summary,
    }


def _synthetic_mutation_context(candidate: dict[str, object]) -> dict[str, object]:
    operator = str(candidate["operator"])
    parameters = candidate["parameters"]
    assert isinstance(parameters, dict)
    if operator.startswith("reward_"):
        before = [1.0, 2.0]
        if operator == "reward_scale":
            after = [value * float(parameters["factor"]) for value in before]
        elif operator == "reward_negate":
            after = [-value for value in before]
        elif operator == "reward_rotate":
            after = [2.0, 1.0]
        elif operator == "reward_drop":
            after = [0.0, 0.0]
        elif operator == "reward_offset":
            after = [1.0 + float(parameters["offset"]), 2.0]
        else:
            after = [1.0, 0.0]
        return {"before": before, "after": after}
    if operator in {"history_lag_one", "history_duplicate_last"}:
        return {"history_length": 1}
    if operator in {"action_mask_remove", "action_mask_add"}:
        return {"agent": "player_0", "action": 0}
    if operator == "observation_swap_agents":
        return {"agents": ["player_0", "player_1"]}
    if operator in {
        "observation_dtype_float32",
        "observation_list_container",
        "observation_value_offset",
    }:
        return {"agent": "player_0", "shape": [2]}
    if operator == "clock_reset_offset":
        return {"offset": parameters["offset"]}
    if operator in {"clock_extra_on_advance", "clock_cancel_on_advance"}:
        return {"offset": parameters["offset"], "advanced": True}
    if operator == "clock_buffer_increment":
        return {"buffer_only": True}
    if operator == "clock_chance_increment":
        return {"chance_events": 1}
    if operator in {
        "terminal_as_truncation",
        "suppress_terminal",
        "partial_terminal_flags",
    }:
        return {"source_terminal": True}
    if operator in {"premature_termination", "premature_truncation"}:
        return {"source_terminal": False}
    if operator == "clear_agents_at_terminal":
        return {"cleared_agents": True}
    if operator in {"config_replace", "config_drop"}:
        return {
            "key": parameters["key"],
            "before": None,
            "after": parameters.get("value"),
        }
    if operator == "mean_field_bypass_rejection":
        return {"dynamics": "mean_field"}
    if operator == "chance_unresolved":
        return {"chance_left_unresolved": True}
    if operator == "chance_one_only":
        return {"resolved_action": 0}
    if operator == "simultaneous_forget_buffer":
        return {"forgot_buffer": True}
    if operator == "simultaneous_prefill_next":
        return {"prefilled_agent": "player_0", "action": 0}
    raise AssertionError(operator)


def _synthetic_valid_batch() -> dict[str, object]:
    records: list[dict[str, object]] = []
    selected_ids = {family: [] for family in MUTATION_FAMILIES}
    for candidate in candidate_manifest_records():
        family = candidate["family"]
        selected = len(selected_ids[family]) < MUTANTS_PER_FAMILY
        if selected:
            selected_ids[family].append(candidate["candidate_id"])
        reference_run = _empty_serialized_run(
            game_spec=candidate["game_spec"],
            seed=candidate["environment_seed"],
            trace_policy_name=candidate["trace_policy_name"],
            max_source_decisions=candidate["max_source_decisions"],
            adapter_class=(
                "PairedReference_"
                + str(candidate["candidate_id"]).replace("-", "_")
            ),
        )
        context = _synthetic_mutation_context(candidate)
        mutant_rewards = (
            tuple(context["after"])
            if str(candidate["operator"]).startswith("reward_")
            else (1.0, 2.0)
        )
        mutant_run = _empty_serialized_run(
            game_spec=candidate["game_spec"],
            seed=candidate["environment_seed"],
            projection_marker=candidate["candidate_id"],
            trace_policy_name=candidate["trace_policy_name"],
            max_source_decisions=candidate["max_source_decisions"],
            adapter_class=(
                "SealedMutation_"
                + str(candidate["candidate_id"]).replace("-", "_")
            ),
            rewards=mutant_rewards,
        )
        reference_projection = mutation_study._serialized_adapter_projection(
            reference_run,
            "synthetic reference",
        )
        mutant_projection = mutation_study._serialized_adapter_projection(
            mutant_run,
            "synthetic mutant",
        )
        behavior = _canonical_digest(
            {"reference": reference_projection, "mutant": mutant_projection}
        )
        baseline_signals = {
            name: {
                "reference_applicable": True,
                "mutant_applicable": True,
                "reference_finding_signatures": [],
                "mutant_finding_signatures": [],
                "added_finding_signatures": [],
                "paired_signal": False,
            }
            for name in BASELINE_NAMES
        }
        records.append(
            {
                "candidate": candidate,
                "selection_inputs": {
                    "reference_acceptable": True,
                    "hook_reached": True,
                    "adapter_behavior_changed": True,
                    "behavior_delta_sha256": behavior,
                    "base_eligible_before_uniqueness": True,
                    "unique_behavior_among_prior_selected": True,
                    "duplicate_behavior_of_candidate_id": None,
                    "eligible": True,
                },
                "mutation_evidence": {
                    "candidate_id": candidate["candidate_id"],
                    "operator": candidate["operator"],
                    "trigger_count": 1,
                    "trigger_contexts": [context],
                },
                "selected": selected,
                "replacement_attempt": {
                    "attempted": True,
                    "reason_codes": [
                        "selected" if selected else "family_quota_already_met"
                    ],
                },
                "reference": {
                    "status": "pass",
                    "finding_codes": [],
                    "run": reference_run,
                    "finding_signatures": [],
                    "clean_reference_alarm": {
                        "semantic_finding_count": 0,
                        "execution_finding_count": 0,
                        "semantic_alarm": False,
                        "unexpected_execution_alarm": False,
                        "accepted_typed_unsupported_capability": False,
                        "any_unexpected_alarm": False,
                    },
                    "adapter_projection_sha256": _canonical_digest(
                        reference_projection
                    ),
                    "elapsed_ns": 0,
                },
                "mutant": {
                    "status": "pass",
                    "finding_codes": [],
                    "run": mutant_run,
                    "finding_signatures": [],
                    "new_full_finding_signatures": [],
                    "new_semantic_finding_signatures": [],
                    "new_execution_finding_signatures": [],
                    "adapter_projection_sha256": _canonical_digest(
                        mutant_projection
                    ),
                    "elapsed_ns": 0,
                },
                "kill": {
                    "new_full_signature_count": 0,
                    "new_semantic_signature_count": 0,
                    "new_execution_signature_count": 0,
                    "killed": False,
                    "semantic_kill": False,
                    "crash_only_kill": False,
                    "status": "survived" if selected else "not_selected",
                    "first_detecting_signature_sha256": None,
                    "first_detecting_obligation": None,
                    "first_detecting_phase": None,
                    "first_detection_order": "stable_mutant_finding_order",
                },
                "ablation_signals": {
                    "project_baselines": baseline_signals,
                    "stock_pettingzoo_api_test": {
                        "reference_adapter_class": (
                            "PairedReference_"
                            + candidate["candidate_id"].replace("-", "_")
                        ),
                        "mutant_adapter_class": (
                            "SealedMutation_"
                            + candidate["candidate_id"].replace("-", "_")
                        ),
                        "reference": {
                            "game_spec": candidate["game_spec"],
                            "cycles": mutation_study.MUTATION_STOCK_API_CYCLES,
                            "passed": True,
                            "exception": None,
                            "warnings": [],
                            "captured_output": "",
                        },
                        "mutant": {
                            "game_spec": candidate["game_spec"],
                            "cycles": mutation_study.MUTATION_STOCK_API_CYCLES,
                            "passed": True,
                            "exception": None,
                            "warnings": [],
                            "captured_output": "",
                        },
                        "paired_signal": False,
                        "interpretation": (
                            "destination API-test failure introduced relative to "
                            "the paired composite repaired reference"
                        ),
                    },
                },
            }
        )

    control_finding_object = Violation(
        obligation="monotone_progress_and_completeness",
        code="progress_instrumentation_inconsistent",
        message="negative control",
    )
    control_finding = to_jsonable(control_finding_object)
    control_signature = {
        "signature_sha256": _canonical_digest(control_finding),
        "category": "semantic",
        "detector_phase": _finding_phase(control_finding),
        "finding": control_finding,
    }
    control_records = []
    for control in PROGRESS_INSTRUMENTATION_CONTROLS:
        annotated_progress = 2 if control.operator == "offset_plus_one" else 0
        control_records.append(
            {
                "control": control.to_manifest_record(),
                "reference": _empty_serialized_run(
                    game_spec=control.game_spec,
                    seed=control.environment_seed,
                    trace_policy_name=control.trace_policy_name,
                    max_source_decisions=control.max_source_decisions,
                    adapter_class="CombinedRepairV0",
                ),
                "corrupted": _empty_serialized_run(
                    game_spec=control.game_spec,
                    seed=control.environment_seed,
                    progress_control_id=control.control_id,
                    trace_policy_name=control.trace_policy_name,
                    max_source_decisions=control.max_source_decisions,
                    adapter_class="CombinedRepairV0",
                    annotated_progress=annotated_progress,
                    violations=(control_finding_object,),
                ),
                "new_full_finding_signatures": [control_signature],
                "detected": True,
                "included_in_24_mutant_denominator": False,
            }
        )
    zeros = {family: 0 for family in MUTATION_FAMILIES}
    selected_counts = {family: MUTANTS_PER_FAMILY for family in MUTATION_FAMILIES}
    return {
        "schema_version": MUTATION_BATCH_SCHEMA_VERSION,
        "artifact_type": MUTATION_BATCH_ARTIFACT_TYPE,
        "protocol_id": MUTATION_PROTOCOL_ID,
        "study_manifest_sha256": "c" * 64,
        "mutation_manifest_sha256": "d" * 64,
        "archive_identifier": "10.5281/zenodo.1234567",
        "archive_published_at_utc": "2026-09-01T00:00:00+00:00",
        "source_tree_sha256": "e" * 64,
        "uv_lock_sha256": "a" * 64,
        "receipt_sha256": "b" * 64,
        "runtime": {},
        "selection": {
            "rule": mutation_study.MUTATION_REPLACEMENT_RULE,
            "selected_ids_by_family": selected_ids,
            "selected_count_by_family": selected_counts,
            "incomplete_families": {},
            "complete": True,
        },
        "score": {
            "selected_total": 24,
            "selected_count_by_family": selected_counts,
            "killed_total": 0,
            "semantic_killed_total": 0,
            "crash_only_killed_total": 0,
            "killed_by_family": zeros,
            "semantic_killed_by_family": zeros,
            "crash_only_killed_by_family": zeros,
            "paired_ablation_signal_counts": {
                "project_baselines": {name: 0 for name in BASELINE_NAMES},
                "stock_pettingzoo_api_test": 0,
            },
            "clean_reference_alarms": {
                "selected_alarm_count": 0,
                "selected_candidate_ids": [],
                "attempted_pool_alarm_count": 0,
                "attempted_pool_candidate_ids": [],
            },
            "first_detection_counts": {"by_obligation": {}, "by_phase": {}},
            "replacement_reason_counts": {
                "family_quota_already_met": 24,
                "selected": 24,
            },
            "interpretation": mutation_study._MUTATION_SCORE_INTERPRETATION,
        },
        "candidate_records": records,
        "progress_instrumentation_controls": {
            "included_in_24_mutant_denominator": False,
            "required_count": 2,
            "detected_count": 2,
            "records": control_records,
        },
        "elapsed_ns": 0,
    }


def test_pure_batch_validator_recomputes_paper_facing_totals() -> None:
    payload = _synthetic_valid_batch()

    report = validate_mutation_batch(payload, require_reporting_complete=True)

    assert report["structurally_valid"] is True
    assert report["candidate_attempt_ledger_complete"] is True
    assert report["cohort_complete_24"] is True
    assert report["progress_controls_satisfied"] is True
    assert report["rq6_reportable"] is True
    assert report["reporting_complete"] is True
    assert report["strong_performance_threshold_met"] is False
    assert report["strong_sensitivity_claim_ready"] is False
    assert report["strong_sensitivity_threshold_met"] is False
    assert "semantic_kills_below_20_of_24" in report[
        "strong_sensitivity_threshold_reasons"
    ]
    assert report["selected_total"] == 24

    tampered = deepcopy(payload)
    tampered["score"]["selected_total"] = 23
    with pytest.raises(MutationBatchValidationError, match="selected_total"):
        validate_mutation_batch(tampered)


def test_batch_validator_derives_selection_inputs_from_recorded_evidence() -> None:
    payload = _synthetic_valid_batch()
    record = payload["candidate_records"][0]
    reference = record["reference"]
    reference_run = reference["run"]
    reference_run["applicable"] = False
    reference["status"] = "inapplicable"
    reference_projection = mutation_study._serialized_adapter_projection(
        reference_run,
        "tampered reference",
    )
    mutant_projection = mutation_study._serialized_adapter_projection(
        record["mutant"]["run"],
        "synthetic mutant",
    )
    reference["adapter_projection_sha256"] = _canonical_digest(
        reference_projection
    )
    record["selection_inputs"]["behavior_delta_sha256"] = _canonical_digest(
        {"reference": reference_projection, "mutant": mutant_projection}
    )

    with pytest.raises(
        MutationBatchValidationError,
        match="selection input reference_acceptable",
    ):
        validate_mutation_batch(payload)


def test_batch_validator_requires_exact_mutation_evidence_and_hashes() -> None:
    missing_evidence = _synthetic_valid_batch()
    missing_evidence["candidate_records"][0].pop("mutation_evidence")
    with pytest.raises(MutationBatchValidationError, match="keys differ"):
        validate_mutation_batch(missing_evidence)

    nonhex_behavior = _synthetic_valid_batch()
    nonhex_behavior["candidate_records"][0]["selection_inputs"][
        "behavior_delta_sha256"
    ] = "z" * 64
    with pytest.raises(MutationBatchValidationError, match="lowercase SHA-256"):
        validate_mutation_batch(nonhex_behavior)

    nested_extra = _synthetic_valid_batch()
    nested_extra["candidate_records"][0]["mutation_evidence"]["extra"] = True
    with pytest.raises(MutationBatchValidationError, match="keys differ"):
        validate_mutation_batch(nested_extra)


@pytest.mark.parametrize("field", ["receipt_sha256", "uv_lock_sha256"])
def test_batch_validator_rejects_invalid_bound_identity_hashes(field: str) -> None:
    payload = _synthetic_valid_batch()
    payload[field] = "not-a-sha256"

    with pytest.raises(MutationBatchValidationError, match=field):
        validate_mutation_batch(payload)


def test_unrelated_control_finding_does_not_satisfy_progress_control() -> None:
    payload = _synthetic_valid_batch()
    control = payload["progress_instrumentation_controls"]["records"][0]
    finding = deepcopy(control["corrupted"]["violations"][0])
    finding["code"] = "unrelated_semantic_finding"
    signature = {
        "signature_sha256": _canonical_digest(finding),
        "category": "semantic",
        "detector_phase": _finding_phase(finding),
        "finding": finding,
    }
    control["corrupted"]["violations"] = [finding]
    control["new_full_finding_signatures"] = [signature]

    with pytest.raises(
        MutationBatchValidationError,
        match="registered finding|progress control sensitivity accounting",
    ):
        validate_mutation_batch(payload)


def test_short_selected_denominator_remains_rq6_reportable() -> None:
    payload = _synthetic_valid_batch()
    family = MUTATION_FAMILIES[0]
    family_records = [
        record
        for record in payload["candidate_records"]
        if record["candidate"]["family"] == family
    ]
    for record in family_records[3:]:
        record["mutation_evidence"]["trigger_count"] = 0
        record["mutation_evidence"]["trigger_contexts"] = []
        record["selection_inputs"]["hook_reached"] = False
        record["selection_inputs"]["base_eligible_before_uniqueness"] = False
        record["selection_inputs"]["eligible"] = False
        record["selected"] = False
        record["replacement_attempt"]["reason_codes"] = [
            "mutation_hook_not_reached"
        ]
        record["kill"]["status"] = "not_selected"

    selected_counts = dict(payload["selection"]["selected_count_by_family"])
    selected_counts[family] = 3
    payload["selection"]["selected_ids_by_family"][family] = [
        record["candidate"]["candidate_id"] for record in family_records[:3]
    ]
    payload["selection"]["selected_count_by_family"] = selected_counts
    payload["selection"]["incomplete_families"] = {family: 3}
    payload["selection"]["complete"] = False
    payload["score"]["selected_total"] = 23
    payload["score"]["selected_count_by_family"] = selected_counts
    payload["score"]["replacement_reason_counts"] = {
        "family_quota_already_met": 20,
        "mutation_hook_not_reached": 5,
        "selected": 23,
    }

    report = validate_mutation_batch(payload, require_reporting_complete=True)

    assert report["cohort_complete_24"] is False
    assert report["rq6_reportable"] is True
    assert report["strong_performance_threshold_met"] is False
    assert report["strong_sensitivity_claim_ready"] is False
    assert "short_selected_mutation_denominator" in report["reporting_warnings"]


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("schema_version",), 3.0),
        (("candidate_records", 0, "selection_inputs", "hook_reached"), 1),
        (
            (
                "candidate_records",
                0,
                "reference",
                "clean_reference_alarm",
                "semantic_alarm",
            ),
            0,
        ),
        (
            (
                "candidate_records",
                0,
                "reference",
                "clean_reference_alarm",
                "semantic_finding_count",
            ),
            0.0,
        ),
        (
            (
                "candidate_records",
                0,
                "ablation_signals",
                "stock_pettingzoo_api_test",
                "reference",
                "cycles",
            ),
            100.0,
        ),
        (("selection", "complete"), 1),
        (("score", "selected_total"), 24.0),
        (("progress_instrumentation_controls", "required_count"), 2.0),
    ),
)
def test_batch_validator_rejects_json_scalar_aliases(
    path: tuple[object, ...],
    replacement: object,
) -> None:
    payload = _synthetic_valid_batch()
    target: object = payload
    for component in path[:-1]:
        target = target[component]  # type: ignore[index]
    target[path[-1]] = replacement  # type: ignore[index]

    with pytest.raises(MutationBatchValidationError):
        validate_mutation_batch(payload)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("obligation", "invented_unknown_obligation"),
        ("segment_index", "0"),
        ("source_span", {"start": 0.0, "stop": 1}),
        ("destination_span", True),
    ),
)
def test_batch_validator_rejects_malformed_nested_findings(
    field: str,
    replacement: object,
) -> None:
    payload = _synthetic_valid_batch()
    control = payload["progress_instrumentation_controls"]["records"][0]
    control["corrupted"]["violations"][0][field] = replacement

    with pytest.raises(MutationBatchValidationError):
        validate_mutation_batch(payload)


def test_batch_validator_rejects_unknown_destination_event_schema() -> None:
    payload = _synthetic_valid_batch()
    run = payload["candidate_records"][0]["mutant"]["run"]
    run["destination_events"] = [{"bogus": True}]
    run["alignment"]["destination_events"] = [{"bogus": True}]

    with pytest.raises(MutationBatchValidationError, match="keys differ"):
        validate_mutation_batch(payload)


def test_batch_validator_seals_trace_schedule_and_evidence_context() -> None:
    wrong_schedule = _synthetic_valid_batch()
    wrong_schedule["candidate_records"][0]["mutant"]["run"]["summary"][
        "trace_policy_id"
    ] = "invented_policy"
    with pytest.raises(MutationBatchValidationError, match="trace_policy_id"):
        validate_mutation_batch(wrong_schedule)

    wrong_context = _synthetic_valid_batch()
    wrong_context["candidate_records"][0]["mutation_evidence"][
        "trigger_contexts"
    ] = [{"fabricated": True}]
    with pytest.raises(MutationBatchValidationError, match="keys differ"):
        validate_mutation_batch(wrong_context)


def test_progress_controls_require_exact_nonempty_transform() -> None:
    payload = _synthetic_valid_batch()
    control = payload["progress_instrumentation_controls"]["records"][0]
    corrupt_run = control["corrupted"]
    corrupt_event = corrupt_run["destination_events"][0]
    corrupt_event["source_progress"] = 1
    corrupt_event["metadata"]["progress_instrumentation"][
        "annotated_progress_after"
    ] = 1
    corrupt_run["alignment"]["destination_events"][0] = deepcopy(corrupt_event)
    corrupt_run["alignment"]["segments"][0]["destination_events"][0] = deepcopy(
        corrupt_event
    )

    with pytest.raises(
        MutationBatchValidationError,
        match="source_progress|progress transform",
    ):
        validate_mutation_batch(payload)
