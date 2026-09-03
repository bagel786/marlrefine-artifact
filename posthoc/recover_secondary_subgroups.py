#!/usr/bin/env python3
# ruff: noqa: E501 -- long report prose is kept as readable literal text
"""Recover the protocol-prespecified secondary-tag report from frozen inputs.

This is deliberately a descriptive audit, not a new endpoint or an inferential
analysis.  It streams the large prospective JSONL file, verifies its accounting
against the frozen manifest and census, and joins the separately
adjudicated causal-root labels by exact (case_id, violation_index) keys.

Example:

    python paper/analysis/recover_secondary_subgroups.py \
      --ledger /path/to/output/prospective_raw.jsonl \
      --adjudication /path/to/output/manual_adjudication.json \
      --analysis /path/to/output/frozen_analysis.json \
      --json-out paper/analysis/secondary_subgroup_report.json \
      --markdown-out paper/analysis/secondary_subgroup_report.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

STATUS_CATEGORIES = ("pass", "fail", "inapplicable", "unalignable", "infrastructure")
SEMANTIC_CATEGORIES = ("observed_failure", "no_observed_failure", "no_verdict")
EXECUTION_CATEGORIES = (
    "terminal_complete",
    "bounded_prefix",
    "semantic_abort",
    "unalignable",
    "infrastructure",
    "inapplicable",
)
OBLIGATION_OUTCOMES = (
    "evaluated_pass",
    "evaluated_fail",
    "not_applicable",
    "not_evaluated",
)
NON_SEMANTIC_DIAGNOSTIC_CODES = frozenset(
    {
        "source_setup_failed",
        "destination_call_budget_exhausted",
        "instrumentation_history_not_prefix_monotone",
        "instrumentation_replay_failed",
        "progress_instrumentation_inconsistent",
        "unalignable_chance",
    }
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fixed_counts(counter: Counter[str], categories: Iterable[str]) -> dict[str, int]:
    return {category: counter.get(category, 0) for category in categories}


def two_axis_outcome(status: str, run: Mapping[str, Any] | None) -> tuple[str, str]:
    """Mirror marlrefine.analysis._two_axis_outcome for the frozen schema."""
    if run is None:
        return "no_verdict", "infrastructure"
    if run.get("applicable") is not True:
        return "no_verdict", "inapplicable"

    violations = run.get("violations")
    semantic_failure = isinstance(violations, list) and any(
        isinstance(item, Mapping)
        and item.get("code") not in NON_SEMANTIC_DIAGNOSTIC_CODES
        for item in violations
    )
    if semantic_failure:
        semantic = "observed_failure"
    elif status in {"pass", "fail"}:
        semantic = "no_observed_failure"
    else:
        semantic = "no_verdict"

    if status == "infrastructure":
        execution = "infrastructure"
    elif status == "unalignable":
        execution = "unalignable"
    else:
        summary = run.get("summary")
        summary = summary if isinstance(summary, Mapping) else {}
        stop_reason = summary.get("stop_reason")
        if (
            stop_reason == "destination_episode_end"
            and summary.get("source_terminal") is True
            and summary.get("adapter_agents_remaining") == 0
        ):
            execution = "terminal_complete"
        elif stop_reason == "source_decision_limit":
            execution = "bounded_prefix"
        else:
            execution = "semantic_abort"
    return semantic, execution


def read_ledger(
    path: Path,
    *,
    manifest_sha256: str,
    cohort_names: list[str],
    policies: list[str],
    census_by_name: Mapping[str, Mapping[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    digest = hashlib.sha256()
    header: dict[str, Any] | None = None
    footer: dict[str, Any] | None = None
    cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()

    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            digest.update(raw_line)
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON at ledger line {line_number}: {exc}"
                ) from exc
            artifact_type = item.get("artifact_type")
            if artifact_type == "marlrefine_prospective_batch_header":
                require(
                    line_number == 1 and header is None, "ledger header must be line 1"
                )
                header = item
                continue
            if artifact_type == "marlrefine_prospective_batch_footer":
                require(footer is None, "ledger has more than one footer")
                footer = item
                continue
            require(
                artifact_type == "marlrefine_prospective_case",
                f"unexpected artifact_type at ledger line {line_number}: {artifact_type!r}",
            )
            require(footer is None, "case record appears after ledger footer")

            case = item.get("case")
            require(
                isinstance(case, Mapping), f"case object missing at line {line_number}"
            )
            ordinal = case.get("ordinal")
            require(
                ordinal == len(cases),
                f"nonconsecutive case ordinal at line {line_number}",
            )
            expected_game = cohort_names[ordinal // len(policies)]
            expected_policy = policies[ordinal % len(policies)]
            game_name = case.get("game_name")
            policy_name = case.get("trace_policy_name")
            require(
                game_name == expected_game, f"game order mismatch at ordinal {ordinal}"
            )
            require(
                policy_name == expected_policy,
                f"policy order mismatch at ordinal {ordinal}",
            )
            case_id = case.get("case_id")
            require(
                case_id == f"{game_name}::{policy_name}",
                f"case_id mismatch at ordinal {ordinal}",
            )
            require(case_id not in seen_case_ids, f"duplicate case_id: {case_id}")
            seen_case_ids.add(str(case_id))

            status = item.get("status")
            require(
                status in STATUS_CATEGORIES, f"unknown status {status!r} for {case_id}"
            )
            run = item.get("run")
            require(
                run is None or isinstance(run, Mapping), f"invalid run for {case_id}"
            )
            semantic, execution = two_axis_outcome(str(status), run)

            violations: list[dict[str, Any]] = []
            obligation_evaluations: list[dict[str, Any]] = []
            final_source_boundary_kind: str | None = None
            if run is not None:
                require(
                    run.get("game_spec") == game_name,
                    f"run game mismatch for {case_id}",
                )
                summary = run.get("summary")
                require(
                    isinstance(summary, Mapping), f"run summary missing for {case_id}"
                )
                source_num_players = summary.get("source_num_players")
                raw_final_kind = summary.get("source_node_kind")
                if raw_final_kind is not None:
                    require(
                        isinstance(raw_final_kind, str) and bool(raw_final_kind),
                        f"invalid final source boundary kind for {case_id}",
                    )
                    final_source_boundary_kind = raw_final_kind
                census_num_players = census_by_name[str(game_name)].get("num_players")
                require(
                    source_num_players == census_num_players,
                    f"runtime/census num_players mismatch for {case_id}: "
                    f"{source_num_players!r} != {census_num_players!r}",
                )
                raw_violations = run.get("violations")
                require(
                    isinstance(raw_violations, list),
                    f"violations missing for {case_id}",
                )
                for violation_index, violation in enumerate(raw_violations):
                    require(
                        isinstance(violation, Mapping),
                        f"invalid violation for {case_id}",
                    )
                    code = violation.get("code")
                    obligation = violation.get("obligation")
                    require(
                        isinstance(code, str) and code,
                        f"violation code missing for {case_id}",
                    )
                    require(
                        isinstance(obligation, str) and obligation,
                        f"violation obligation missing for {case_id}",
                    )
                    violations.append(
                        {
                            "violation_index": violation_index,
                            "code": code,
                            "obligation": obligation,
                            "semantic": code not in NON_SEMANTIC_DIAGNOSTIC_CODES,
                        }
                    )
                raw_evaluations = run.get("obligation_evaluations")
                require(
                    isinstance(raw_evaluations, list),
                    f"obligation evaluations missing for {case_id}",
                )
                for evaluation in raw_evaluations:
                    require(
                        isinstance(evaluation, Mapping),
                        f"invalid obligation evaluation for {case_id}",
                    )
                    obligation_id = evaluation.get("obligation_id")
                    outcome = evaluation.get("outcome")
                    evaluation_count = evaluation.get("evaluation_count")
                    finding_indices = evaluation.get("finding_indices")
                    require(
                        isinstance(obligation_id, str),
                        f"obligation id missing for {case_id}",
                    )
                    require(
                        outcome in OBLIGATION_OUTCOMES,
                        f"unknown obligation outcome for {case_id}",
                    )
                    require(
                        isinstance(evaluation_count, int) and evaluation_count >= 0,
                        f"invalid evaluation_count for {case_id}/{obligation_id}",
                    )
                    require(
                        isinstance(finding_indices, list),
                        f"finding_indices missing for {case_id}",
                    )
                    require(
                        all(
                            isinstance(index, int) and 0 <= index < len(violations)
                            for index in finding_indices
                        ),
                        f"invalid finding index for {case_id}/{obligation_id}",
                    )
                    obligation_evaluations.append(
                        {
                            "obligation_id": obligation_id,
                            "outcome": outcome,
                            "evaluation_count": evaluation_count,
                            "finding_indices": list(finding_indices),
                        }
                    )

            cases.append(
                {
                    "case_id": str(case_id),
                    "game_name": str(game_name),
                    "status": str(status),
                    "semantic_evidence": semantic,
                    "execution_completeness": execution,
                    "final_source_boundary_kind": final_source_boundary_kind,
                    "violations": violations,
                    "obligation_evaluations": obligation_evaluations,
                }
            )

    require(header is not None, "ledger header missing")
    require(footer is not None, "ledger footer missing")
    expected_case_count = len(cohort_names) * len(policies)
    require(
        len(cases) == expected_case_count,
        "ledger case count does not match manifest schedule",
    )
    for boundary_name, boundary in (("header", header), ("footer", footer)):
        require(
            boundary.get("manifest_sha256") == manifest_sha256,
            f"ledger {boundary_name} manifest hash mismatch",
        )
        require(
            boundary.get("case_count") == expected_case_count,
            f"ledger {boundary_name} case count mismatch",
        )
    actual_status_counts = Counter(case["status"] for case in cases)
    require(
        dict(actual_status_counts) == footer.get("status_counts"),
        "ledger footer status counts do not match case records",
    )
    return digest.hexdigest(), header, footer, cases


def attach_root_adjudication(
    path: Path,
    *,
    ledger_sha256: str,
    cases: list[dict[str, Any]],
) -> tuple[str, list[str], dict[str, Any]]:
    adjudication = load_json(path)
    require(isinstance(adjudication, Mapping), "manual adjudication must be an object")
    require(
        adjudication.get("raw_batch_sha256") == ledger_sha256,
        "manual adjudication does not identify the supplied raw ledger",
    )
    roots = adjudication.get("roots")
    require(isinstance(roots, list), "manual adjudication roots missing")
    root_ids = sorted(str(root["root_id"]) for root in roots)
    require(len(root_ids) == len(set(root_ids)), "duplicate causal root id")

    violation_lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for case in cases:
        for violation in case["violations"]:
            key = (case["case_id"], violation["violation_index"])
            violation_lookup[key] = violation

    dispositions = adjudication.get("finding_dispositions")
    require(isinstance(dispositions, list), "manual adjudication dispositions missing")
    seen: set[tuple[str, int]] = set()
    disposition_counts: Counter[str] = Counter()
    for disposition in dispositions:
        require(isinstance(disposition, Mapping), "invalid finding disposition")
        key = (str(disposition.get("case_id")), disposition.get("violation_index"))
        require(isinstance(key[1], int), f"invalid disposition key: {key!r}")
        require(
            key in violation_lookup,
            f"adjudication points to missing violation: {key!r}",
        )
        require(key not in seen, f"duplicate adjudication disposition: {key!r}")
        seen.add(key)
        disposition_name = disposition.get("disposition")
        require(isinstance(disposition_name, str), f"invalid disposition for {key!r}")
        disposition_counts[disposition_name] += 1
        root_id = disposition.get("root_id")
        if disposition_name == "root":
            require(
                root_id in root_ids, f"unknown causal root for {key!r}: {root_id!r}"
            )
            violation_lookup[key]["root_id"] = root_id
        else:
            violation_lookup[key]["root_id"] = None

    unmapped = set(violation_lookup) - seen
    require(not unmapped, f"{len(unmapped)} raw violations lack a manual disposition")
    return (
        sha256_file(path),
        root_ids,
        {
            "disposition_counts": dict(sorted(disposition_counts.items())),
            "mapped_violation_count": len(seen),
            "unmapped_violation_count": 0,
        },
    )


def incidence(items: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    occurrences: Counter[str] = Counter()
    traces: dict[str, set[str]] = defaultdict(set)
    games: dict[str, set[str]] = defaultdict(set)
    for case in items:
        for violation in case["violations"]:
            if not violation["semantic"]:
                continue
            value = violation.get(key)
            if not isinstance(value, str):
                continue
            occurrences[value] += 1
            traces[value].add(case["case_id"])
            games[value].add(case["game_name"])
    return {
        value: {
            "finding_occurrence_count": occurrences[value],
            "trace_count": len(traces[value]),
            "distinct_game_count": len(games[value]),
        }
        for value in sorted(occurrences)
    }


def aggregate(
    game_names: Iterable[str],
    *,
    cases_by_game: Mapping[str, list[dict[str, Any]]],
    root_ids: list[str],
    detailed: bool = False,
) -> dict[str, Any]:
    names = sorted(game_names)
    selected = [case for name in names for case in cases_by_game[name]]
    require(
        len(selected) == len(names) * 8, "subgroup does not have eight traces per game"
    )
    status_counts = Counter(case["status"] for case in selected)
    semantic_counts = Counter(case["semantic_evidence"] for case in selected)
    execution_counts = Counter(case["execution_completeness"] for case in selected)
    finding_cases = [
        case
        for case in selected
        if any(violation["semantic"] for violation in case["violations"])
    ]
    finding_occurrence_count = sum(
        1
        for case in selected
        for violation in case["violations"]
        if violation["semantic"]
    )
    by_root = incidence(selected, "root_id")
    for root_id in root_ids:
        by_root.setdefault(
            root_id,
            {"finding_occurrence_count": 0, "trace_count": 0, "distinct_game_count": 0},
        )
    result: dict[str, Any] = {
        "game_names": names,
        "game_count": len(names),
        "scheduled_trace_count": len(selected),
        "primary_status_counts": fixed_counts(status_counts, STATUS_CATEGORIES),
        "semantic_evidence_counts": fixed_counts(semantic_counts, SEMANTIC_CATEGORIES),
        "execution_completeness_counts": fixed_counts(
            execution_counts, EXECUTION_CATEGORIES
        ),
        "observed_semantic_finding_incidence": {
            "finding_occurrence_count": finding_occurrence_count,
            "trace_count": len(finding_cases),
            "distinct_game_count": len({case["game_name"] for case in finding_cases}),
            "trace_denominator": len(selected),
            "game_denominator": len(names),
        },
        "adjudicated_causal_root_incidence": dict(sorted(by_root.items())),
    }
    if not detailed:
        return result

    result["semantic_violations_by_code"] = incidence(selected, "code")
    result["semantic_violations_by_protocol_obligation"] = incidence(
        selected, "obligation"
    )
    result["per_game"] = {
        name: aggregate(
            [name], cases_by_game=cases_by_game, root_ids=root_ids, detailed=False
        )
        for name in names
    }

    obligation_ids = sorted(
        {
            evaluation["obligation_id"]
            for case in selected
            for evaluation in case["obligation_evaluations"]
        }
    )
    obligation_report: dict[str, Any] = {}
    for obligation_id in obligation_ids:
        outcome_counts: Counter[str] = Counter()
        evaluation_site_count = 0
        linked_occurrences = 0
        linked_traces: set[str] = set()
        linked_games: set[str] = set()
        for case in selected:
            evaluations = [
                evaluation
                for evaluation in case["obligation_evaluations"]
                if evaluation["obligation_id"] == obligation_id
            ]
            require(len(evaluations) == 1, f"missing or duplicate {obligation_id} row")
            evaluation = evaluations[0]
            outcome_counts[evaluation["outcome"]] += 1
            evaluation_site_count += evaluation["evaluation_count"]
            semantic_indices = {
                violation["violation_index"]
                for violation in case["violations"]
                if violation["semantic"]
            }
            linked = [
                index
                for index in evaluation["finding_indices"]
                if index in semantic_indices
            ]
            linked_occurrences += len(linked)
            if linked:
                linked_traces.add(case["case_id"])
                linked_games.add(case["game_name"])
        obligation_report[obligation_id] = {
            "trace_outcome_counts": fixed_counts(outcome_counts, OBLIGATION_OUTCOMES),
            "evaluation_site_count": evaluation_site_count,
            "linked_semantic_finding_occurrence_count": linked_occurrences,
            "linked_semantic_finding_trace_count": len(linked_traces),
            "linked_semantic_finding_distinct_game_count": len(linked_games),
        }
    result["obligation_evaluation_coverage"] = obligation_report
    return result


def bool_label(value: Any) -> str:
    require(isinstance(value, bool), f"expected boolean census field, found {value!r}")
    return "true" if value else "false"


def num_players_label(record: Mapping[str, Any]) -> str:
    value = record.get("num_players")
    require(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        f"invalid effective num_players value: {value!r}",
    )
    return str(value)


def required_string_label(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    require(
        isinstance(value, str) and bool(value), f"invalid census {field}: {value!r}"
    )
    return value


def chance_mode_label(record: Mapping[str, Any]) -> str:
    value = required_string_label(record, "chance_mode")
    require(
        value in {"deterministic", "explicit_stochastic", "sampled_stochastic"},
        f"unknown census chance_mode: {value!r}",
    )
    return value


def reward_model_label(record: Mapping[str, Any]) -> str:
    value = record.get("reward_model")
    if value == "terminal":
        return "terminal_only"
    if value == "rewards":
        return "intermediate_rewards_allowed"
    raise ValueError(f"unknown census reward_model: {value!r}")


def finite_length_label(record: Mapping[str, Any]) -> str:
    value = record.get("max_game_length")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return "finite_positive"
    return "not_finite_positive"


def configurable_label(record: Mapping[str, Any]) -> str:
    parameters = record.get("parameters")
    require(
        isinstance(parameters, Mapping), "census parameters field must be an object"
    )
    return "nonempty_parameter_mapping" if parameters else "empty_parameter_mapping"


def capability_pair_label(record: Mapping[str, Any]) -> str:
    return (
        f"observation={bool_label(record.get('provides_observation'))}|"
        f"information_state={bool_label(record.get('provides_information_state'))}"
    )


def registry_stratum_label(record: Mapping[str, Any]) -> str:
    dynamics = required_string_label(record, "dynamics")
    chance_mode = chance_mode_label(record)
    if dynamics == "mean_field":
        return "mean_field"
    require(
        dynamics in {"sequential", "simultaneous"},
        f"unknown census dynamics: {dynamics!r}",
    )
    stochasticity = "deterministic" if chance_mode == "deterministic" else "stochastic"
    return f"{dynamics}__{stochasticity}"


def validate_frozen_analysis(
    analysis: Mapping[str, Any],
    *,
    manifest_sha256: str,
    ledger_sha256: str,
    adjudication_sha256: str,
    cohort_names: list[str],
    census_by_name: Mapping[str, Mapping[str, Any]],
    cases: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate frozen-analysis linkage and independently rederive requested views."""
    identities = analysis.get("input_identities")
    require(isinstance(identities, Mapping), "frozen analysis input identities missing")
    manifest_identity = identities.get("manifest")
    raw_identity = identities.get("raw_batch")
    require(
        isinstance(manifest_identity, Mapping), "analysis manifest identity missing"
    )
    require(isinstance(raw_identity, Mapping), "analysis raw-batch identity missing")
    require(
        manifest_identity.get("sha256") == manifest_sha256,
        "frozen analysis does not identify the supplied manifest",
    )
    require(
        raw_identity.get("sha256") == ledger_sha256,
        "frozen analysis does not identify the supplied raw ledger",
    )
    require(
        identities.get("manual_adjudication_sha256") == adjudication_sha256,
        "frozen analysis does not identify the supplied manual adjudication",
    )
    design = analysis.get("design")
    require(isinstance(design, Mapping), "frozen analysis design missing")
    require(
        design.get("distinct_prospective_game_types") == 105,
        "analysis game count mismatch",
    )
    require(design.get("policies_per_game") == 8, "analysis policy count mismatch")
    require(design.get("scheduled_trace_cases") == 840, "analysis trace count mismatch")

    paths = analysis.get("execution_path_coverage")
    require(isinstance(paths, Mapping), "frozen analysis path coverage missing")
    frozen_statuses = paths.get("status_by_registry_stratum")
    frozen_findings = paths.get("finding_occurrences_by_registry_stratum")
    frozen_final_kinds = paths.get("final_source_boundary_kinds")
    require(isinstance(frozen_statuses, Mapping), "frozen stratum statuses missing")
    require(isinstance(frozen_findings, Mapping), "frozen stratum findings missing")
    require(
        isinstance(frozen_final_kinds, Mapping), "frozen final boundary kinds missing"
    )

    cases_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        cases_by_game[case["game_name"]].append(case)
    stratum_names: dict[str, list[str]] = defaultdict(list)
    for game_name in cohort_names:
        stratum_names[registry_stratum_label(census_by_name[game_name])].append(
            game_name
        )

    primary_strata = {
        stratum: aggregate(
            names,
            cases_by_game=cases_by_game,
            root_ids=[],
            detailed=False,
        )
        for stratum, names in sorted(stratum_names.items())
    }
    derived_statuses = {
        stratum: values["primary_status_counts"]
        for stratum, values in primary_strata.items()
    }
    require(
        derived_statuses == frozen_statuses,
        "rederived stratum statuses differ from frozen analysis",
    )

    derived_finding_pairs: dict[str, dict[str, int]] = {}
    for stratum, names in sorted(stratum_names.items()):
        counts: Counter[str] = Counter()
        for game_name in names:
            for case in cases_by_game[game_name]:
                for violation in case["violations"]:
                    counts[f"{violation['obligation']}/{violation['code']}"] += 1
        derived_finding_pairs[stratum] = dict(sorted(counts.items()))
    require(
        derived_finding_pairs == frozen_findings,
        "rederived stratum finding occurrences differ from frozen analysis",
    )

    final_kind_traces: Counter[str] = Counter()
    final_kind_games: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        kind = case["final_source_boundary_kind"]
        if kind is None:
            continue
        final_kind_traces[kind] += 1
        final_kind_games[kind].add(case["game_name"])
    final_kinds = {
        kind: {
            "trace_count": final_kind_traces[kind],
            "distinct_game_count": len(final_kind_games[kind]),
        }
        for kind in sorted(final_kind_traces)
    }
    require(
        final_kinds == frozen_final_kinds,
        "rederived final source boundary kinds differ from frozen analysis",
    )
    validation = {
        "analysis_manifest_sha256_matches": True,
        "analysis_raw_ledger_sha256_matches": True,
        "analysis_manual_adjudication_sha256_matches": True,
        "primary_stratum_statuses_rederived_exactly": True,
        "primary_stratum_finding_occurrences_rederived_exactly": True,
        "final_source_boundary_kinds_rederived_exactly": True,
    }
    return primary_strata, final_kinds, validation


DIMENSIONS: tuple[tuple[str, str, Callable[[Mapping[str, Any]], str]], ...] = (
    (
        "effective_num_players",
        "Effective player count returned by default native construction (census num_players).",
        num_players_label,
    ),
    (
        "declared_chance_mode",
        "Census chance_mode retained as a prespecified secondary tag: deterministic, explicit stochastic, or sampled stochastic.",
        chance_mode_label,
    ),
    (
        "declared_reward_timing",
        "Census reward_model: terminal means terminal-only; rewards permits intermediate rewards but does not prove a nonterminal reward occurred in a sampled trace.",
        reward_model_label,
    ),
    (
        "declared_information_type",
        "OpenSpiel registry information classification recorded in the census.",
        lambda record: required_string_label(record, "information"),
    ),
    (
        "declared_utility_type",
        "OpenSpiel registry utility classification recorded in the census.",
        lambda record: required_string_label(record, "utility"),
    ),
    (
        "provides_observation",
        "Declared observation-tensor capability recorded in the census.",
        lambda record: bool_label(record.get("provides_observation")),
    ),
    (
        "provides_information_state",
        "Declared information-state-tensor capability recorded in the census.",
        lambda record: bool_label(record.get("provides_information_state")),
    ),
    (
        "declared_observation_information_state_pair",
        "Joint view of the two declared tensor capabilities; this is descriptive metadata, not evidence that every trace exercised both interfaces.",
        capability_pair_label,
    ),
    (
        "finite_declared_max_game_length",
        "A finite-length declaration is operationalized as an integer max_game_length greater than zero in the census.",
        finite_length_label,
    ),
    (
        "presence_of_configurable_parameters",
        "Operationalized as a nonempty census parameters mapping; this does not claim every listed default is freely user-tunable.",
        configurable_label,
    ),
)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    single = report["single_player_subgroup"]
    multi = report["two_or_more_player_complement"]
    primary_strata = report["primary_registry_strata"]
    final_kinds = report["final_source_boundary_kind_reachability"]
    finding = single["observed_semantic_finding_incidence"]
    multi_finding = multi["observed_semantic_finding_incidence"]
    lines = [
        "# Recovered prespecified secondary-subgroup report",
        "",
        "## Status and scope",
        "",
        "This is a **post-hoc recovery of a prespecified descriptive report that was omitted from the initial manuscript analysis**. It does not change the frozen cohort, traces, outcomes, endpoint definitions, or causal adjudication. Counts come from the hashed registry census and raw prospective ledger. Causal-root labels are joined from the separately hashed final manual adjudication; they are not inferred from metadata.",
        "",
        "No hypothesis tests were run. These are exact descriptive counts, and overlapping metadata categories must not be treated as independent comparisons.",
        "",
        "## Immutable input identities",
        "",
    ]
    lines += markdown_table(
        ["Input", "Release component", "Extracted path", "SHA-256"],
        [
            [
                name,
                value["release_component"],
                f"`{value['relative_path']}`",
                value["sha256"],
            ]
            for name, value in report["input_identities"].items()
        ],
    )
    lines += [
        "",
        "## Primary registry strata",
        "",
        "The protocol's five mutually exclusive primary strata are rederived below. Finding occurrences are correlated symptom records; finding traces and games count at least one semantic finding. These rows recover reporting that existed in the frozen analysis but was omitted from the initial manuscript table.",
        "",
    ]
    lines += markdown_table(
        [
            "Stratum",
            "Games",
            "Traces",
            "Pass",
            "Fail",
            "Inapplicable",
            "Finding occurrences",
            "Finding traces",
            "Finding games",
        ],
        [
            [
                f"`{stratum}`",
                values["game_count"],
                values["scheduled_trace_count"],
                values["primary_status_counts"]["pass"],
                values["primary_status_counts"]["fail"],
                values["primary_status_counts"]["inapplicable"],
                values["observed_semantic_finding_incidence"][
                    "finding_occurrence_count"
                ],
                values["observed_semantic_finding_incidence"]["trace_count"],
                values["observed_semantic_finding_incidence"]["distinct_game_count"],
            ]
            for stratum, values in primary_strata["strata"].items()
        ],
    )
    lines += [
        "",
        "### Final native source-boundary reachability",
        "",
        "These counts describe the final recorded native boundary for the 808 semantically evaluated traces. Trace categories are mutually exclusive; distinct-game counts can overlap because different policies for one game can stop at different boundary kinds.",
        "",
    ]
    lines += markdown_table(
        ["Final source boundary kind", "Traces", "Distinct games"],
        [
            [f"`{kind}`", values["trace_count"], values["distinct_game_count"]]
            for kind, values in final_kinds["counts"].items()
        ],
    )
    lines += [
        "",
        "## Prespecified single-player subgroup",
        "",
        f"The prospective cohort contains **{single['game_count']} effective single-player game types** and **{single['scheduled_trace_count']} scheduled traces**. An observed semantic finding occurred in **{finding['trace_count']}/{finding['trace_denominator']} traces** across **{finding['distinct_game_count']}/{finding['game_denominator']} games**. The names are: "
        + ", ".join(f"`{name}`" for name in single["game_names"])
        + ".",
        "",
        "These traces can inform generic reward, lifecycle, clock, state-kind, and interface-projection behavior. Under the frozen protocol they are **not evidence for specifically inter-agent buffering or multi-agent scheduling claims**. The default-only prospective panel also supplies no configuration-preservation evidence.",
        "",
        f"For claim narrowing, the arithmetic two-or-more-player complement contains **{multi['game_count']} games** and **{multi['scheduled_trace_count']} traces**. It contains observed semantic findings in **{multi_finding['trace_count']}/{multi_finding['trace_denominator']} traces** across **{multi_finding['distinct_game_count']}/{multi_finding['game_denominator']} games**. This complement was not used to redefine the frozen primary endpoint.",
        "",
        "### Trace outcomes by game",
        "",
    ]
    game_rows: list[list[Any]] = []
    for name, game in single["per_game"].items():
        semantic = game["semantic_evidence_counts"]
        execution = game["execution_completeness_counts"]
        game_rows.append(
            [
                f"`{name}`",
                semantic["observed_failure"],
                semantic["no_observed_failure"],
                semantic["no_verdict"],
                execution["terminal_complete"],
                execution["bounded_prefix"],
                execution["semantic_abort"],
                execution["inapplicable"],
            ]
        )
    lines += markdown_table(
        [
            "Game",
            "Finding",
            "No finding",
            "No verdict",
            "Terminal",
            "Bounded",
            "Abort",
            "Inapplicable",
        ],
        game_rows,
    )
    lines += ["", "### Single-player symptom and root breakdown", ""]
    lines += markdown_table(
        ["Violation code", "Occurrences", "Traces", "Games"],
        [
            [
                f"`{name}`",
                values["finding_occurrence_count"],
                values["trace_count"],
                values["distinct_game_count"],
            ]
            for name, values in single["semantic_violations_by_code"].items()
        ],
    )
    lines += ["", "Protocol-obligation symptoms:", ""]
    lines += markdown_table(
        ["Obligation", "Occurrences", "Traces", "Games"],
        [
            [
                f"`{name}`",
                values["finding_occurrence_count"],
                values["trace_count"],
                values["distinct_game_count"],
            ]
            for name, values in single[
                "semantic_violations_by_protocol_obligation"
            ].items()
        ],
    )
    lines += [
        "",
        "Adjudicated causal roots (occurrences are symptom records attributed to a root, not additional distinct defects):",
        "",
    ]
    lines += markdown_table(
        ["Causal root", "Attributed symptoms", "Traces", "Games"],
        [
            [
                f"`{name}`",
                values["finding_occurrence_count"],
                values["trace_count"],
                values["distinct_game_count"],
            ]
            for name, values in single["adjudicated_causal_root_incidence"].items()
        ],
    )
    lines += ["", "### O1--O8 evaluation coverage within the subgroup", ""]
    lines += markdown_table(
        [
            "ID",
            "Pass",
            "Fail",
            "N/A",
            "Not eval.",
            "Sites",
            "Linked findings",
            "Finding traces",
            "Finding games",
        ],
        [
            [
                obligation_id,
                values["trace_outcome_counts"]["evaluated_pass"],
                values["trace_outcome_counts"]["evaluated_fail"],
                values["trace_outcome_counts"]["not_applicable"],
                values["trace_outcome_counts"]["not_evaluated"],
                values["evaluation_site_count"],
                values["linked_semantic_finding_occurrence_count"],
                values["linked_semantic_finding_trace_count"],
                values["linked_semantic_finding_distinct_game_count"],
            ]
            for obligation_id, values in single[
                "obligation_evaluation_coverage"
            ].items()
        ],
    )
    lines += [
        "",
        "`evaluated_pass` denominators are obligation-specific. They must not be reconstructed from traces lacking a finding.",
        "",
        "## All prespecified secondary tags",
        "",
        "The table below reports every category that occurs in the 105-game prospective cohort. `Finding traces` and `finding games` count at least one semantic finding; they do not count causal defects.",
        "",
    ]
    tag_rows: list[list[Any]] = []
    for dimension, dimension_report in report["secondary_tag_reports"].items():
        for category, values in dimension_report["categories"].items():
            tag_finding = values["observed_semantic_finding_incidence"]
            semantic = values["semantic_evidence_counts"]
            tag_rows.append(
                [
                    f"`{dimension}`",
                    f"`{category}`",
                    values["game_count"],
                    values["scheduled_trace_count"],
                    tag_finding["finding_occurrence_count"],
                    tag_finding["trace_count"],
                    tag_finding["distinct_game_count"],
                    semantic["no_observed_failure"],
                    semantic["no_verdict"],
                ]
            )
    lines += markdown_table(
        [
            "Dimension",
            "Category",
            "Games",
            "Traces",
            "Finding occurrences",
            "Finding traces",
            "Finding games",
            "No-finding traces",
            "No verdict",
        ],
        tag_rows,
    )
    lines += [
        "",
        "## Suggested manuscript language",
        "",
        report["suggested_manuscript_language"]["recovery_disclosure"],
        "",
        report["suggested_manuscript_language"]["single_player_result"],
        "",
        report["suggested_manuscript_language"]["interpretive_boundary"],
        "",
        "## Reproduction",
        "",
        "From the manuscript source directory, after extracting the tagged GitHub source archive and the v1.0.1 results asset into the paths shown below, run:",
        "",
        "```console",
        "python paper/analysis/recover_secondary_subgroups.py \\",
        "  --protocol extracted-source/marlrefine-artifact-study-v1.0.1/frozen-source/docs/protocol.md \\",
        "  --manifest extracted-results/marlrefine-study-results-v1.0.1/manifests/study_v1_draft.json \\",
        "  --census extracted-results/marlrefine-study-results-v1.0.1/artifacts/registry_census.json \\",
        "  --ledger extracted-results/marlrefine-study-results-v1.0.1/output/prospective_raw.jsonl \\",
        "  --adjudication extracted-results/marlrefine-study-results-v1.0.1/output/manual_adjudication.json \\",
        "  --analysis extracted-results/marlrefine-study-results-v1.0.1/output/frozen_analysis.json \\",
        "  --json-out recovered-secondary-subgroups.json \\",
        "  --markdown-out recovered-secondary-subgroups.md",
        "```",
        "",
        "The script records SHA-256 identities for all six supplied inputs. It terminates on internal manifest-to-ledger, ledger-to-adjudication, and frozen-analysis-to-manifest/ledger/adjudication hash-link mismatches, as well as cohort, schedule, player-count, status-total, obligation-ledger, adjudication-key, stratum, or final-boundary inconsistencies. It deliberately does not hardcode a release-version hash: reviewers can compare the recorded identities with the table above and the release checksums.",
        "",
        "## Limitation: no invented nonstandard-game taxonomy",
        "",
        "The frozen census and manifest do not define an exhaustive classifier for `special-node` or `other nonstandard` game types. This recovery therefore does not invent one after seeing results. It reports the prespecified components that are unambiguous: mean-field as a primary stratum, one-shot as a declared information category, chance mode, effective player count, and reached final source boundary kinds. A separate exhaustive residual taxonomy remains unresolved.",
        "",
    ]
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    protocol_text = args.protocol.read_text(encoding="utf-8")
    protocol_markers = (
        "Primary strata are assigned from OpenSpiel registry metadata",
        "with chance mode retained as a secondary tag",
        "Secondary descriptive tags are number of players",
        "reported in a separate single-agent subgroup",
        "reported by node kind, reachability, status, and observed finding incidence",
    )
    for marker in protocol_markers:
        require(
            marker in protocol_text, f"protocol reporting directive not found: {marker}"
        )
    expected_dimensions = {
        "effective_num_players",
        "declared_chance_mode",
        "declared_reward_timing",
        "declared_information_type",
        "declared_utility_type",
        "provides_observation",
        "provides_information_state",
        "declared_observation_information_state_pair",
        "finite_declared_max_game_length",
        "presence_of_configurable_parameters",
    }
    require(
        {name for name, _definition, _classifier in DIMENSIONS} == expected_dimensions,
        "script does not cover every unambiguous prespecified secondary dimension",
    )

    manifest = load_json(args.manifest)
    census = load_json(args.census)
    require(isinstance(manifest, Mapping), "manifest must be an object")
    require(isinstance(census, Mapping), "census must be an object")
    cohort = manifest["validation"]["semantic_cohort"]
    cohort_names = list(cohort["names"])
    require(
        cohort["size"] == 105 == len(cohort_names),
        "semantic cohort must contain 105 names",
    )
    require(
        len(cohort_names) == len(set(cohort_names)),
        "semantic cohort contains duplicate names",
    )
    policies = list(manifest["trace_schedule"]["policies"])
    require(len(policies) == 8, "frozen trace schedule must contain eight policies")

    census_records = census.get("records")
    require(isinstance(census_records, list), "census records missing")
    require(
        census.get("population_size") == 113 == len(census_records),
        "census must contain 113 rows",
    )
    census_by_name = {str(record["short_name"]): record for record in census_records}
    require(len(census_by_name) == 113, "census contains duplicate short_name values")
    require(
        set(cohort_names) <= set(census_by_name),
        "semantic cohort name absent from census",
    )

    manifest_sha256 = sha256_file(args.manifest)
    ledger_sha256, header, footer, cases = read_ledger(
        args.ledger,
        manifest_sha256=manifest_sha256,
        cohort_names=cohort_names,
        policies=policies,
        census_by_name=census_by_name,
    )
    adjudication_sha256, root_ids, adjudication_join = attach_root_adjudication(
        args.adjudication,
        ledger_sha256=ledger_sha256,
        cases=cases,
    )
    frozen_analysis = load_json(args.analysis)
    require(isinstance(frozen_analysis, Mapping), "frozen analysis must be an object")
    primary_strata, final_source_kinds, analysis_validation = validate_frozen_analysis(
        frozen_analysis,
        manifest_sha256=manifest_sha256,
        ledger_sha256=ledger_sha256,
        adjudication_sha256=adjudication_sha256,
        cohort_names=cohort_names,
        census_by_name=census_by_name,
        cases=cases,
    )

    cases_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        cases_by_game[case["game_name"]].append(case)
    require(
        set(cases_by_game) == set(cohort_names),
        "ledger game set differs from manifest cohort",
    )
    require(
        all(len(game_cases) == 8 for game_cases in cases_by_game.values()),
        "not every game has eight traces",
    )

    secondary_tag_reports: dict[str, Any] = {}
    for dimension_name, definition, classifier in DIMENSIONS:
        groups: dict[str, list[str]] = defaultdict(list)
        for game_name in cohort_names:
            groups[classifier(census_by_name[game_name])].append(game_name)
        category_reports = {
            category: aggregate(
                names,
                cases_by_game=cases_by_game,
                root_ids=root_ids,
                detailed=False,
            )
            for category, names in sorted(groups.items())
        }
        require(
            sum(item["game_count"] for item in category_reports.values()) == 105,
            f"{dimension_name} categories do not partition the cohort",
        )
        require(
            sum(item["scheduled_trace_count"] for item in category_reports.values())
            == 840,
            f"{dimension_name} trace categories do not partition the schedule",
        )
        secondary_tag_reports[dimension_name] = {
            "definition": definition,
            "categories": category_reports,
        }

    single_registry_names = sorted(
        name
        for name, record in census_by_name.items()
        if record.get("num_players") == 1
    )
    single_names = sorted(set(single_registry_names) & set(cohort_names))
    require(single_names, "single-player prospective subgroup is empty")
    discovery_names = set(manifest["discovery"]["names"])
    exclusion_names = set(manifest["validation"]["descriptive_exclusions"]["names"])
    omitted_single_names = sorted(set(single_registry_names) - set(single_names))
    omitted_reasons: dict[str, str] = {}
    for name in omitted_single_names:
        if name in discovery_names:
            omitted_reasons[name] = "discovery_or_implementation_control"
        elif name in exclusion_names:
            omitted_reasons[name] = "prespecified_descriptive_exclusion"
        else:
            raise ValueError(
                f"unexplained single-player census name outside cohort: {name}"
            )

    single = aggregate(
        single_names,
        cases_by_game=cases_by_game,
        root_ids=root_ids,
        detailed=True,
    )
    multi_names = sorted(set(cohort_names) - set(single_names))
    multi = aggregate(
        multi_names,
        cases_by_game=cases_by_game,
        root_ids=root_ids,
        detailed=False,
    )
    single["registry_single_player_names"] = single_registry_names
    single["registry_single_player_count"] = len(single_registry_names)
    single["registry_names_outside_prospective_cohort"] = omitted_reasons
    single["claim_boundary"] = {
        "supports": "generic reward, lifecycle, clock, state-kind, and interface-projection observations",
        "does_not_support": "claims specifically about inter-agent buffering or multi-agent schedules",
        "configuration_preservation": "not prospectively evaluated because the primary cohort is default-configuration only",
    }

    finding = single["observed_semantic_finding_incidence"]
    roots_present = [
        root_id
        for root_id, values in single["adjudicated_causal_root_incidence"].items()
        if values["finding_occurrence_count"] > 0
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "marlrefine_recovered_prespecified_secondary_subgroups",
        "analysis_status": "post_hoc_recovery_of_omitted_prespecified_descriptive_report",
        "input_identities": {
            "protocol": {
                "release_component": "tagged GitHub source archive",
                "relative_path": "frozen-source/docs/protocol.md",
                "sha256": sha256_file(args.protocol),
            },
            "study_manifest": {
                "release_component": "marlrefine-study-results-v1.0.1.tar.gz",
                "relative_path": "manifests/study_v1_draft.json",
                "sha256": manifest_sha256,
            },
            "registry_census": {
                "release_component": "marlrefine-study-results-v1.0.1.tar.gz",
                "relative_path": "artifacts/registry_census.json",
                "sha256": sha256_file(args.census),
            },
            "prospective_raw_ledger": {
                "release_component": "marlrefine-study-results-v1.0.1.tar.gz",
                "relative_path": "output/prospective_raw.jsonl",
                "sha256": ledger_sha256,
            },
            "manual_adjudication": {
                "release_component": "marlrefine-study-results-v1.0.1.tar.gz",
                "relative_path": "output/manual_adjudication.json",
                "sha256": adjudication_sha256,
            },
            "frozen_analysis": {
                "release_component": "marlrefine-study-results-v1.0.1.tar.gz",
                "relative_path": "output/frozen_analysis.json",
                "sha256": sha256_file(args.analysis),
            },
        },
        "validation": {
            "registry_population_count": 113,
            "semantic_cohort_game_count": len(cohort_names),
            "trace_policies_per_game": len(policies),
            "scheduled_trace_count": len(cases),
            "ledger_header_case_count": header["case_count"],
            "ledger_footer_case_count": footer["case_count"],
            "ledger_footer_status_counts": footer["status_counts"],
            "runtime_num_players_matches_census_for_all_traces": True,
            "every_secondary_dimension_partitions_105_games_and_840_traces": True,
            "protocol_lines_268_276_reporting_directives_found": True,
            "manual_adjudication_join": adjudication_join,
            "frozen_analysis_cross_checks": analysis_validation,
        },
        "measurement_notes": {
            "finding_incidence": "Counts at least one non-diagnostic semantic violation in a trace or game; it is not a distinct-defect count.",
            "causal_roots": "Joined from the hashed final manual adjudication by exact case_id and violation_index; not inferred from census metadata or violation names.",
            "overlap": "Secondary dimensions overlap and are descriptive; categories within each dimension partition the cohort, but categories across dimensions are not independent.",
            "inference": "No hypothesis tests, confidence intervals, or multiplicity-sensitive subgroup claims are introduced.",
            "unresolved_nonstandard_taxonomy": "No frozen exhaustive classifier defines special-node or other nonstandard game types. The report does not create one after observing results.",
        },
        "primary_registry_strata": {
            "definition": "Five mutually exclusive strata derived from census dynamics and chance_mode exactly as frozen: sequential/simultaneous crossed with deterministic/stochastic, plus mean_field.",
            "strata": primary_strata,
        },
        "final_source_boundary_kind_reachability": {
            "definition": "Final recorded native source node kind among semantically evaluated traces; trace categories are exclusive and game sets may overlap.",
            "counts": final_source_kinds,
        },
        "single_player_subgroup": single,
        "two_or_more_player_complement": multi,
        "secondary_tag_reports": secondary_tag_reports,
        "suggested_manuscript_language": {
            "recovery_disclosure": "The protocol prespecified primary-stratum status and finding incidence, final source-node reachability, and descriptive reporting by player count, chance and reward mode, information and utility type, declared tensor capabilities, finite-length status, and parameterization. The initial manuscript omitted part of this reporting. We recovered it after the primary analysis by a deterministic join of the frozen census, raw ledger, and frozen analysis; no cohort, trace, outcome, or adjudication changed.",
            "single_player_result": (
                f"Twelve of the 105 prospective game types had an effective num_players() of one, contributing 96 of 840 scheduled traces. "
                f"A semantic finding was observed in {finding['trace_count']} of 96 traces across {finding['distinct_game_count']} of 12 games; "
                f"{single['semantic_evidence_counts']['no_observed_failure']} traces had no observed finding and {single['semantic_evidence_counts']['no_verdict']} had no verdict. "
                f"The subgroup contained findings attributed by the final adjudication to {len(roots_present)} causal roots ({', '.join(roots_present)})."
            ),
            "interpretive_boundary": "The single-player traces inform generic reward, lifecycle, clock, state-kind, and projection behavior. They are not evidence for claims specifically about inter-agent buffering or multi-agent schedules, and the default-only prospective cohort does not test preservation of caller-supplied nondefault configuration.",
        },
    }
    return report


def parse_args() -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol", type=Path, default=repository_root / "docs/protocol.md"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repository_root / "manifests/study_v1_draft.json",
    )
    parser.add_argument(
        "--census",
        type=Path,
        default=repository_root / "artifacts/registry_census.json",
    )
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args)
    json_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out is not None:
        args.json_out.write_text(json_text, encoding="utf-8")
    if args.markdown_out is not None:
        args.markdown_out.write_text(render_markdown(report), encoding="utf-8")
    if args.json_out is None and args.markdown_out is None:
        print(json_text, end="")


if __name__ == "__main__":
    main()
