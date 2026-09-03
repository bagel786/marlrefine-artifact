from __future__ import annotations

import pyspiel
import pytest
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

from marlrefine.adapters.openspiel_shimmy import run_trace
from marlrefine.repair_evidence import (
    RepairCase,
    treatment_outcome_valid,
    unexpected_treatment_codes,
)
from marlrefine.repairs import (
    CombinedRepairV0,
    ConfigurationRepairV0,
    DecisionClockRepairV0,
    MeanFieldFailFastRepairV0,
    RewardAccountingRepairV0,
)


def _codes(run) -> set[str]:
    return {violation.code for violation in run.violations}


@pytest.mark.integration
def test_reward_repair_removes_buffer_replay_without_clock_changes() -> None:
    run = run_trace(
        "coop_box_pushing",
        seed=7,
        max_source_decisions=10,
        adapter_class=RewardAccountingRepairV0,
    )
    assert "nonzero_stutter_reward" not in _codes(run)
    assert "segment_reward_mismatch" not in _codes(run)
    assert run.summary["source_decisions"] == 10


@pytest.mark.integration
def test_reward_repair_restores_consumer_terminal_return() -> None:
    run = run_trace("nim", seed=0, adapter_class=RewardAccountingRepairV0)
    assert "nonzero_terminal_cleanup_reward" not in _codes(run)
    assert "consumer_return_mismatch" not in _codes(run)
    assert "consumer_delivery_mismatch" not in _codes(run)
    assert run.summary["destination_delivered_reward_sum"] == (1.0, -1.0)


@pytest.mark.integration
def test_clock_repair_reaches_native_horizon() -> None:
    run = run_trace(
        "coop_box_pushing",
        seed=0,
        adapter_class=DecisionClockRepairV0,
    )
    assert "premature_adapter_truncation" not in _codes(run)
    assert run.summary["source_decisions"] == 100
    assert run.summary["source_terminal"] is True


@pytest.mark.integration
def test_clock_treatment_isolates_premature_truncation_before_shared_cap() -> None:
    case = RepairCase(
        case_id="chance_decision_clock",
        game_spec="coop_box_pushing",
        seed=0,
        max_source_decisions=30,
        treatment=DecisionClockRepairV0,
        targeted_codes=("premature_adapter_truncation",),
        success_mode="successful_semantic_execution",
        interpretation="shared-cap clock isolation",
        required_treatment_summary=(
            ("stop_reason", "source_decision_limit"),
            ("source_decisions", 30),
            ("source_terminal", False),
            ("source_node_kind", "simultaneous"),
            ("adapter_agents_remaining", 2),
        ),
    )
    stock = run_trace(
        case.game_spec,
        seed=case.seed,
        max_source_decisions=case.max_source_decisions,
    )
    treatment = run_trace(
        case.game_spec,
        seed=case.seed,
        max_source_decisions=case.max_source_decisions,
        adapter_class=case.treatment,
    )

    assert "premature_adapter_truncation" in _codes(stock)
    assert "premature_adapter_truncation" not in _codes(treatment)
    assert unexpected_treatment_codes(case, stock, treatment) == ()
    assert treatment_outcome_valid(case, stock, treatment) is True


@pytest.mark.integration
def test_clock_repair_removes_terminal_truncation_conflation() -> None:
    run = run_trace(
        "matrix_rps",
        seed=0,
        adapter_class=DecisionClockRepairV0,
    )
    assert "boundary_lifecycle_mismatch" not in _codes(run)
    assert "pre_cleanup_lifecycle_mismatch" not in _codes(run)


@pytest.mark.integration
def test_configuration_repair_preserves_nondefault_go() -> None:
    run = run_trace(
        "go(board_size=5)",
        seed=3,
        max_source_decisions=1,
        adapter_class=ConfigurationRepairV0,
    )
    assert "parameters_changed_on_reset" not in _codes(run)
    assert run.summary["source_parameters"]["board_size"] == 5
    assert run.summary["adapter_parameters"]["board_size"] == 5


@pytest.mark.integration
def test_mean_field_repair_fails_fast_explicitly() -> None:
    with pytest.raises(NotImplementedError, match="mean-field distribution protocol"):
        MeanFieldFailFastRepairV0(env=pyspiel.load_game("mfg_crowd_modelling"))


@pytest.mark.integration
def test_mean_field_rejection_scores_through_the_trace_runner() -> None:
    case = RepairCase(
        case_id="mean_field_capability",
        game_spec="mfg_crowd_modelling",
        seed=0,
        max_source_decisions=None,
        treatment=MeanFieldFailFastRepairV0,
        targeted_codes=("mean_field_node_silently_terminated",),
        success_mode="explicit_mean_field_rejection",
        interpretation="explicit unsupported capability outcome",
    )
    stock = run_trace(case.game_spec, seed=case.seed)
    treatment = run_trace(
        case.game_spec,
        seed=case.seed,
        adapter_class=case.treatment,
    )

    assert treatment.applicable is True
    assert _codes(treatment) == {"adapter_setup_failed"}
    assert treatment_outcome_valid(case, stock, treatment) is True


@pytest.mark.integration
def test_generic_mean_field_constructor_crash_does_not_score() -> None:
    class CrashingTreatment(OpenSpielCompatibilityV0):
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("mean-field distribution protocol crashed")

    case = RepairCase(
        case_id="mean_field_crash_control",
        game_spec="mfg_crowd_modelling",
        seed=0,
        max_source_decisions=None,
        treatment=CrashingTreatment,
        targeted_codes=("mean_field_node_silently_terminated",),
        success_mode="explicit_mean_field_rejection",
        interpretation="negative control",
    )
    stock = run_trace(case.game_spec, seed=case.seed)
    treatment = run_trace(
        case.game_spec,
        seed=case.seed,
        adapter_class=case.treatment,
    )

    assert treatment_outcome_valid(case, stock, treatment) is False


@pytest.mark.integration
@pytest.mark.parametrize(
    "game_spec",
    ("coop_box_pushing", "nim", "matrix_rps", "go(board_size=5)"),
)
def test_combined_repair_passes_non_mean_field_discovery_witnesses(
    game_spec: str,
) -> None:
    run = run_trace(game_spec, seed=0, adapter_class=CombinedRepairV0)
    assert run.passed is True


@pytest.mark.integration
def test_arbitrary_constructor_crash_cannot_score_as_a_repair() -> None:
    class CrashingTreatment(OpenSpielCompatibilityV0):
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("unrelated constructor crash")

    case = RepairCase(
        case_id="crash_control",
        game_spec="matrix_rps",
        seed=0,
        max_source_decisions=1,
        treatment=CrashingTreatment,
        targeted_codes=("boundary_lifecycle_mismatch",),
        success_mode="successful_semantic_execution",
        interpretation="negative control",
    )
    treatment = run_trace(
        case.game_spec,
        seed=case.seed,
        max_source_decisions=case.max_source_decisions,
        adapter_class=case.treatment,
    )
    stock = run_trace(
        case.game_spec,
        seed=case.seed,
        max_source_decisions=case.max_source_decisions,
    )
    assert treatment_outcome_valid(case, stock, treatment) is False


@pytest.mark.integration
def test_path_avoidance_cannot_score_as_a_repair() -> None:
    class PathAvoidingTreatment(OpenSpielCompatibilityV0):
        def step(self, action) -> None:
            super().step(action)
            self.agents.clear()

    trajectory_fields = (
        "stop_reason",
        "destination_calls",
        "source_transitions",
        "source_decisions",
        "final_source_state_digest",
    )
    case = RepairCase(
        case_id="path_avoidance_control",
        game_spec="coop_box_pushing",
        seed=7,
        max_source_decisions=10,
        treatment=PathAvoidingTreatment,
        targeted_codes=("nonzero_stutter_reward", "segment_reward_mismatch"),
        success_mode="successful_semantic_execution",
        interpretation="negative control",
        match_stock_summary_fields=trajectory_fields,
    )
    stock = run_trace(
        case.game_spec,
        seed=case.seed,
        max_source_decisions=case.max_source_decisions,
    )
    treatment = run_trace(
        case.game_spec,
        seed=case.seed,
        max_source_decisions=case.max_source_decisions,
        adapter_class=case.treatment,
    )
    assert treatment_outcome_valid(case, stock, treatment) is False
