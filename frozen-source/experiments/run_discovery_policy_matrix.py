#!/usr/bin/env python3
"""Run the frozen policy matrix only on the seven discovery game names.

This is an exploratory convenience runner, not part of the prospective batch.
The complete selection is validated against a closed allowlist before the
trace runner is called, so a mixed discovery/prospective request fails without
constructing any game.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from marlrefine.adapters.openspiel_shimmy import TraceRun, run_trace
from marlrefine.policies import TRACE_POLICIES, TRACE_POLICY_NAMES, TracePolicy
from marlrefine.prospective import OutcomeStatus, classify_run_payload
from marlrefine.provenance import project_file_identity, runtime_provenance
from marlrefine.serialization import write_json
from marlrefine.study import DISCOVERY_GAME_NAMES

SCHEMA_VERSION = 1
ARTIFACT_TYPE = "marlrefine_discovery_policy_matrix"
DEFAULT_MAX_SOURCE_DECISIONS = 10
DEFAULT_MAX_DESTINATION_CALLS = 200
DEFAULT_OUTPUT = Path("tmp/discovery_policy_matrix.json")

# Fail closed if either supposedly frozen schedule drifts. Keeping these local
# guards prevents an accidental study-constant edit from broadening this
# exploratory runner's execution scope.
EXPECTED_DISCOVERY_GAME_NAMES = (
    "coop_box_pushing",
    "go",
    "kuhn_poker",
    "matrix_rps",
    "mfg_crowd_modelling",
    "nim",
    "tic_tac_toe",
)
EXPECTED_POLICY_NAMES = (
    "smallest_legal",
    "largest_legal",
    "pseudo_random_seed_0",
    "pseudo_random_seed_1",
    "pseudo_random_seed_2",
    "pseudo_random_seed_3",
    "pseudo_random_seed_4",
    "pseudo_random_seed_5",
)


class DiscoveryScopeError(ValueError):
    """A requested matrix would leave the frozen discovery-only scope."""


class TraceResult(Protocol):
    """Minimal runner result used to permit game-free unit-test doubles."""

    def to_dict(self) -> dict[str, Any]: ...


TraceRunner = Callable[..., TraceRun | TraceResult]


def _positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def validate_discovery_selection(selected_names: Sequence[str]) -> tuple[str, ...]:
    """Validate the whole selection before any game-facing function is called."""
    if tuple(DISCOVERY_GAME_NAMES) != EXPECTED_DISCOVERY_GAME_NAMES:
        raise DiscoveryScopeError("the frozen discovery allowlist has drifted")
    if tuple(TRACE_POLICY_NAMES) != EXPECTED_POLICY_NAMES or len(TRACE_POLICIES) != 8:
        raise DiscoveryScopeError("the frozen eight-policy schedule has drifted")

    names = tuple(selected_names)
    if not names:
        raise DiscoveryScopeError("at least one discovery game must be selected")
    if any(not isinstance(name, str) or not name for name in names):
        raise DiscoveryScopeError("every selected discovery game must be named")
    if len(names) != len(set(names)):
        raise DiscoveryScopeError("discovery selection contains duplicate names")
    if not set(names).issubset(EXPECTED_DISCOVERY_GAME_NAMES):
        raise DiscoveryScopeError(
            "selection contains a name outside the discovery-only allowlist"
        )

    # Canonical study order makes output independent of caller ordering.
    selected = set(names)
    return tuple(name for name in EXPECTED_DISCOVERY_GAME_NAMES if name in selected)


def _case_metadata(
    ordinal: int,
    game_name: str,
    policy: TracePolicy,
) -> dict[str, Any]:
    return {
        "case_id": f"{game_name}::{policy.name}",
        "ordinal": ordinal,
        "game_name": game_name,
        "trace_policy_name": policy.name,
        "trace_policy_id": policy.policy_id,
        "trace_policy_seed": policy.seed,
        "environment_seed": policy.environment_seed,
    }


def _execute_case(
    game_name: str,
    policy: TracePolicy,
    *,
    ordinal: int,
    max_source_decisions: int,
    max_destination_calls: int,
    runner: TraceRunner,
) -> dict[str, Any]:
    case = _case_metadata(ordinal, game_name, policy)
    try:
        result = runner(
            game_name,
            seed=policy.environment_seed,
            trace_policy=policy,
            max_source_decisions=max_source_decisions,
            max_destination_calls=max_destination_calls,
        )
        run_payload = result.to_dict()
        if not isinstance(run_payload, dict):
            raise TypeError("trace result to_dict() must return a dictionary")
    except Exception as exc:
        return {
            "case": case,
            "status": OutcomeStatus.INFRASTRUCTURE.value,
            "run": None,
            "infrastructure_error": {
                "exception_type": type(exc).__name__,
                "message": str(exc),
            },
        }
    return {
        "case": case,
        "status": classify_run_payload(run_payload).value,
        "run": run_payload,
        "infrastructure_error": None,
    }


def _aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(record["status"]) for record in records)
    finding_occurrences: Counter[str] = Counter()
    finding_traces: dict[str, set[str]] = defaultdict(set)
    finding_games: dict[str, set[str]] = defaultdict(set)
    obligation_occurrences: Counter[str] = Counter()
    obligation_traces: dict[str, set[str]] = defaultdict(set)
    obligation_games: dict[str, set[str]] = defaultdict(set)
    exception_counts: Counter[str] = Counter()
    baseline_records: Counter[str] = Counter()
    baseline_applicable: Counter[str] = Counter()
    baseline_signal_traces: Counter[str] = Counter()
    baseline_finding_occurrences: Counter[str] = Counter()

    for record in records:
        case = record["case"]
        assert isinstance(case, Mapping)
        case_id = str(case["case_id"])
        game_name = str(case["game_name"])
        error = record.get("infrastructure_error")
        if isinstance(error, Mapping):
            exception_counts[str(error.get("exception_type", "unknown"))] += 1
        run = record.get("run")
        if not isinstance(run, Mapping):
            continue
        violations = run.get("violations", [])
        if not isinstance(violations, list):
            continue
        for finding in violations:
            if not isinstance(finding, Mapping):
                continue
            code = finding.get("code")
            obligation = finding.get("obligation")
            if isinstance(code, str):
                finding_occurrences[code] += 1
                finding_traces[code].add(case_id)
                finding_games[code].add(game_name)
            if isinstance(obligation, str):
                obligation_occurrences[obligation] += 1
                obligation_traces[obligation].add(case_id)
                obligation_games[obligation].add(game_name)
        baselines = run.get("baselines", [])
        if not isinstance(baselines, list):
            continue
        for baseline in baselines:
            if not isinstance(baseline, Mapping):
                continue
            name = baseline.get("baseline")
            findings = baseline.get("findings", [])
            if not isinstance(name, str) or not isinstance(findings, list):
                continue
            baseline_records[name] += 1
            if baseline.get("applicable") is True:
                baseline_applicable[name] += 1
            if findings:
                baseline_signal_traces[name] += 1
                baseline_finding_occurrences[name] += len(findings)

    return {
        "case_count": len(records),
        "status_counts": {
            status.value: status_counts.get(status.value, 0)
            for status in OutcomeStatus
        },
        "finding_codes": {
            code: {
                "occurrence_count": finding_occurrences[code],
                "trace_count": len(finding_traces[code]),
                "distinct_game_count": len(finding_games[code]),
            }
            for code in sorted(finding_occurrences)
        },
        "finding_obligations": {
            obligation: {
                "occurrence_count": obligation_occurrences[obligation],
                "trace_count": len(obligation_traces[obligation]),
                "distinct_game_count": len(obligation_games[obligation]),
            }
            for obligation in sorted(obligation_occurrences)
        },
        "raw_baseline_signals": {
            "interpretation_warning": (
                "These are unadjudicated trace-level signals, not same-root "
                "causal detection credit or independent defect counts."
            ),
            "baselines": {
                name: {
                    "record_count": baseline_records[name],
                    "applicable_trace_count": baseline_applicable[name],
                    "signal_trace_count": baseline_signal_traces[name],
                    "finding_occurrence_count": baseline_finding_occurrences[name],
                }
                for name in sorted(baseline_records)
            },
        },
        "runner_exception_counts": dict(sorted(exception_counts.items())),
    }


def build_discovery_policy_matrix(
    *,
    selected_names: Sequence[str] = DISCOVERY_GAME_NAMES,
    max_source_decisions: int = DEFAULT_MAX_SOURCE_DECISIONS,
    max_destination_calls: int = DEFAULT_MAX_DESTINATION_CALLS,
    runner: TraceRunner = run_trace,
) -> dict[str, Any]:
    """Execute a validated discovery-only game/policy product in fixed order."""
    names = validate_discovery_selection(selected_names)
    source_cap = _positive_integer(max_source_decisions, "max_source_decisions")
    destination_cap = _positive_integer(
        max_destination_calls,
        "max_destination_calls",
    )

    records = tuple(
        _execute_case(
            game_name,
            policy,
            ordinal=ordinal,
            max_source_decisions=source_cap,
            max_destination_calls=destination_cap,
            runner=runner,
        )
        for ordinal, (game_name, policy) in enumerate(
            (game_name, policy)
            for game_name in names
            for policy in TRACE_POLICIES
        )
    )
    runtime = runtime_provenance()
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "population_role": "discovery_only_exploratory",
        "scope_warning": (
            "All names are pre-freeze discovery cases. This matrix is neither "
            "prospective nor held-out evidence."
        ),
        "runtime": runtime,
        "source_identity": {
            key: runtime.get(key)
            for key in (
                "source_identity_scope",
                "source_tree_sha256",
                "uv_lock_sha256",
                "git_revision",
                "git_dirty",
            )
        },
        "study_manifest": project_file_identity("manifests/study_v1_draft.json"),
        "configuration": {
            "game_names": names,
            "game_count": len(names),
            "trace_policy_names": TRACE_POLICY_NAMES,
            "trace_policy_count": len(TRACE_POLICIES),
            "expected_case_count": len(names) * len(TRACE_POLICIES),
            "max_source_decisions": source_cap,
            "max_destination_calls": destination_cap,
        },
        "aggregate": _aggregate(records),
        "records": records,
    }


def main() -> None:
    payload = build_discovery_policy_matrix()
    write_json(DEFAULT_OUTPUT, payload)
    counts = payload["aggregate"]["status_counts"]
    rendered = ", ".join(f"{key}={value}" for key, value in counts.items())
    print(f"wrote {payload['aggregate']['case_count']} discovery traces: {rendered}")
    print(DEFAULT_OUTPUT)


if __name__ == "__main__":
    main()
