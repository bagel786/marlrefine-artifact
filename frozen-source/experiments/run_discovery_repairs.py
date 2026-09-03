#!/usr/bin/env python3
"""Generate discovery-only causal-treatment evidence for diagnosed mechanisms."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marlrefine.adapters.openspiel_shimmy import run_trace
from marlrefine.provenance import project_file_identity, runtime_provenance
from marlrefine.repair_evidence import (
    RepairCase,
    treatment_outcome_valid,
    unexpected_treatment_codes,
    violation_codes,
)
from marlrefine.repairs import (
    CombinedRepairV0,
    ConfigurationRepairV0,
    DecisionClockRepairV0,
    MeanFieldFailFastRepairV0,
    RewardAccountingRepairV0,
)
from marlrefine.serialization import write_json

MATCH_STOCK_TRAJECTORY_FIELDS = (
    "stop_reason",
    "destination_calls",
    "source_transitions",
    "source_decisions",
    "source_terminal",
    "source_node_kind",
    "source_return",
    "adapter_agents_remaining",
    "post_reset_source_state_digest",
    "final_source_state_digest",
    "chance_tape_sha256",
)

CASES = (
    RepairCase(
        case_id="reward_buffer_accounting",
        game_spec="coop_box_pushing",
        seed=7,
        max_source_decisions=10,
        treatment=RewardAccountingRepairV0,
        targeted_codes=("nonzero_stutter_reward", "segment_reward_mismatch"),
        success_mode="successful_semantic_execution",
        interpretation="isolated semantic repair",
        match_stock_summary_fields=MATCH_STOCK_TRAJECTORY_FIELDS,
    ),
    RepairCase(
        case_id="reward_terminal_cleanup",
        game_spec="nim",
        seed=0,
        max_source_decisions=None,
        treatment=RewardAccountingRepairV0,
        targeted_codes=(
            "nonzero_terminal_cleanup_reward",
            "consumer_return_mismatch",
        ),
        success_mode="successful_semantic_execution",
        interpretation="isolated semantic repair",
        match_stock_summary_fields=MATCH_STOCK_TRAJECTORY_FIELDS,
    ),
    RepairCase(
        case_id="chance_decision_clock",
        game_spec="coop_box_pushing",
        seed=0,
        max_source_decisions=30,
        treatment=DecisionClockRepairV0,
        targeted_codes=("premature_adapter_truncation",),
        success_mode="successful_semantic_execution",
        interpretation=(
            "isolated clock treatment: stock exhausts its destination schedule "
            "at 25 native decisions, while the treatment remains live to the "
            "shared 30-decision harness cap"
        ),
        required_treatment_summary=(
            ("stop_reason", "source_decision_limit"),
            ("source_decisions", 30),
            ("source_terminal", False),
            ("source_node_kind", "simultaneous"),
            ("adapter_agents_remaining", 2),
        ),
    ),
    RepairCase(
        case_id="terminal_lifecycle_clock_offset",
        game_spec="matrix_rps",
        seed=0,
        max_source_decisions=None,
        treatment=DecisionClockRepairV0,
        targeted_codes=(
            "boundary_lifecycle_mismatch",
            "pre_cleanup_lifecycle_mismatch",
        ),
        success_mode="successful_semantic_execution",
        interpretation=(
            "causal discovery evidence; contract classification remains pending"
        ),
        match_stock_summary_fields=MATCH_STOCK_TRAJECTORY_FIELDS,
    ),
    RepairCase(
        case_id="prebuilt_configuration",
        game_spec="go(board_size=5)",
        seed=3,
        max_source_decisions=1,
        treatment=ConfigurationRepairV0,
        targeted_codes=("parameters_changed_on_reset",),
        success_mode="successful_semantic_execution",
        interpretation="isolated semantic repair",
        required_treatment_summary=(
            ("stop_reason", "source_decision_limit"),
            ("destination_calls", 1),
            ("source_transitions", 1),
            ("source_decisions", 1),
            ("source_terminal", False),
        ),
    ),
    RepairCase(
        case_id="mean_field_capability",
        game_spec="mfg_crowd_modelling",
        seed=0,
        max_source_decisions=None,
        treatment=MeanFieldFailFastRepairV0,
        targeted_codes=("mean_field_node_silently_terminated",),
        success_mode="explicit_mean_field_rejection",
        interpretation=(
            "capability repair: silent behavior becomes explicit unsupported-game "
            "rejection, not semantic support"
        ),
    ),
)

COMBINED_REGRESSION_SPECS = (
    "coop_box_pushing",
    "nim",
    "matrix_rps",
    "go(board_size=5)",
)


def _execute(case: RepairCase) -> dict[str, Any]:
    kwargs = {
        "seed": case.seed,
        "max_source_decisions": case.max_source_decisions,
    }
    stock = run_trace(case.game_spec, **kwargs)
    treatment = run_trace(case.game_spec, adapter_class=case.treatment, **kwargs)
    stock_codes = violation_codes(stock)
    treatment_codes = violation_codes(treatment)
    target_present_before = all(code in stock_codes for code in case.targeted_codes)
    target_absent_after = all(
        code not in treatment_codes for code in case.targeted_codes
    )
    outcome_valid = treatment_outcome_valid(case, stock, treatment)
    unexpected_codes_after = unexpected_treatment_codes(case, stock, treatment)
    return {
        "case_id": case.case_id,
        "game_spec": case.game_spec,
        "population_role": "discovery_only",
        "treatment_class": case.treatment.__name__,
        "success_mode": case.success_mode,
        "match_stock_summary_fields": case.match_stock_summary_fields,
        "required_treatment_summary": tuple(
            {"field": field, "expected": expected}
            for field, expected in case.required_treatment_summary
        ),
        "interpretation": case.interpretation,
        "targeted_codes": case.targeted_codes,
        "targeted_codes_present_before": tuple(
            code for code in case.targeted_codes if code in stock_codes
        ),
        "targeted_codes_present_after": tuple(
            code for code in case.targeted_codes if code in treatment_codes
        ),
        "target_present_before": target_present_before,
        "target_absent_after": target_absent_after,
        "unexpected_codes_after": unexpected_codes_after,
        "treatment_outcome_valid": outcome_valid,
        "targeted_mechanism_removed": (
            target_present_before and target_absent_after and outcome_valid
        ),
        "stock": stock,
        "treatment": treatment,
    }


def main() -> None:
    results = tuple(_execute(case) for case in CASES)
    combined_regression = tuple(
        run_trace(game_spec, seed=0, adapter_class=CombinedRepairV0)
        for game_spec in COMBINED_REGRESSION_SPECS
    )
    payload = {
        "schema_version": 1,
        "artifact_type": "marlrefine_discovery_causal_treatments",
        "environment": runtime_provenance(),
        "study_manifest": project_file_identity("manifests/study_v1_draft.json"),
        "scope_warning": (
            "All cases were selected and inspected during development. These are "
            "causal discovery checks, not prospective validation results."
        ),
        "results": results,
        "combined_regression": {
            "population_role": "discovery_only",
            "adapter_class": CombinedRepairV0.__name__,
            "runs": combined_regression,
            "all_passed": all(run.passed for run in combined_regression),
        },
    }
    output = Path("artifacts/discovery_repairs.json")
    write_json(output, payload)
    removed = sum(result["targeted_mechanism_removed"] for result in results)
    print(f"targeted mechanism removed: {removed}/{len(results)} discovery cases")
    for result in results:
        status = "REMOVED" if result["targeted_mechanism_removed"] else "PRESENT"
        print(f"  {result['case_id']}: {status}")
    print(
        "combined repair: "
        f"{sum(run.passed for run in combined_regression)}/"
        f"{len(combined_regression)} discovery witnesses passed"
    )


if __name__ == "__main__":
    main()
