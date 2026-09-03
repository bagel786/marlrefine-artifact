"""Evidence rules for scoring isolated causal repair treatments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

from marlrefine.adapters.openspiel_shimmy import TraceRun


@dataclass(frozen=True, slots=True)
class RepairCase:
    """One discovery treatment and its non-crash success criterion."""

    case_id: str
    game_spec: str
    seed: int
    max_source_decisions: int | None
    treatment: type[OpenSpielCompatibilityV0]
    targeted_codes: tuple[str, ...]
    success_mode: str
    interpretation: str
    match_stock_summary_fields: tuple[str, ...] = ()
    required_treatment_summary: tuple[tuple[str, Any], ...] = ()


def violation_codes(run: TraceRun) -> tuple[str, ...]:
    """Return canonical unique violation codes for one run."""
    return tuple(sorted({violation.code for violation in run.violations}))


def unexpected_treatment_codes(
    case: RepairCase,
    stock: TraceRun,
    treatment: TraceRun,
) -> tuple[str, ...]:
    """Return findings introduced beyond an explicitly expected rejection."""
    allowed_new = (
        {"adapter_setup_failed"}
        if case.success_mode == "explicit_mean_field_rejection"
        else set()
    )
    return tuple(
        sorted(
            set(violation_codes(treatment))
            .difference(violation_codes(stock))
            .difference(allowed_new)
        )
    )


def treatment_outcome_valid(
    case: RepairCase,
    stock: TraceRun,
    treatment: TraceRun,
) -> bool:
    """Require successful execution, frozen reachability, and no new findings."""
    setup_status = str(treatment.summary.get("setup_status", ""))
    if case.success_mode == "successful_semantic_execution":
        if not treatment.applicable or setup_status != "pass":
            return False
        if unexpected_treatment_codes(case, stock, treatment):
            return False
        if any(
            treatment.summary.get(field) != stock.summary.get(field)
            for field in case.match_stock_summary_fields
        ):
            return False
        return all(
            treatment.summary.get(field) == expected
            for field, expected in case.required_treatment_summary
        )
    if case.success_mode == "explicit_mean_field_rejection":
        return (
            treatment.applicable
            and setup_status.startswith("error:adapter_setup:NotImplementedError:")
            and "mean-field distribution protocol" in setup_status
            and violation_codes(treatment) == ("adapter_setup_failed",)
        )
    raise ValueError(f"unknown treatment success mode: {case.success_mode}")
