from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from marlrefine import analysis as analysis_module
from marlrefine.adapters.openspiel_shimmy import TraceRun
from marlrefine.alignment import align_traces
from marlrefine.analysis import (
    BatchValidationError,
    _manifest_game_strata,
    _math_isclose_tolerance_ratio,
    _validate_manifest_and_receipt,
    _validate_replay,
    latex_result_macros,
)
from marlrefine.analysis import (
    analyze_prospective_batch as _production_analyze_prospective_batch,
)
from marlrefine.baselines import (
    BASELINE_NAMES,
    endpoint,
    inapplicable_baselines,
    macro_aggregate,
    macro_boundary,
    return_only,
    strict_lockstep,
)
from marlrefine.evaluation import (
    OBLIGATION_LEDGER_SCHEMA_ID,
    build_obligation_evaluations,
)
from marlrefine.external_baselines import (
    EXTERNAL_BASELINE_CLASSIFIER_ID,
    EXTERNAL_BASELINE_SCHEMA_VERSION,
)
from marlrefine.localization import LOCALIZER_ID
from marlrefine.model import DestinationEvent, SourceEvent, Span, Violation
from marlrefine.mutation_study import validate_mutation_batch
from marlrefine.policies import TRACE_POLICIES, TRACE_POLICY_NAMES
from marlrefine.prospective import (
    BATCH_SCHEMA_VERSION,
    CLASSIFIER_ID,
    classify_case_record,
)
from marlrefine.serialization import write_json, write_jsonl
from marlrefine.study import (
    PROSPECTIVE_DESTINATION_CALL_CAP,
    PROSPECTIVE_MAX_CASE_ATTEMPTS,
    PROSPECTIVE_RETRY_ELIGIBILITY,
    SHIMMY_OPENSPIEL_TEST_MEMBER,
    SHIMMY_OPENSPIEL_TEST_SHA256,
    SHIMMY_SDIST_SHA256,
    SHIMMY_SDIST_URL,
    STOCK_API_ACTION_SPACE_SEED,
    STOCK_API_CYCLES,
    external_baseline_protocol,
    prospective_execution_contract,
)
from tests.test_mutation_study import _synthetic_valid_batch


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reward_sensitivity_uses_primary_math_isclose_threshold() -> None:
    ratio = _math_isclose_tolerance_ratio(
        1.0,
        1.0 + 1.5e-12,
        atol=1e-12,
        rtol=1e-12,
    )
    assert ratio > 1.0


def test_registry_strata_merge_explicit_and_sampled_chance() -> None:
    manifest = {
        "population": {
            "registry_metadata": [
                {
                    "short_name": "explicit",
                    "dynamics": "sequential",
                    "chance_mode": "explicit_stochastic",
                },
                {
                    "short_name": "sampled",
                    "dynamics": "sequential",
                    "chance_mode": "sampled_stochastic",
                },
                {
                    "short_name": "mean_field",
                    "dynamics": "mean_field",
                    "chance_mode": "explicit_stochastic",
                },
            ]
        }
    }
    assert _manifest_game_strata(
        manifest, ("explicit", "sampled", "mean_field")
    ) == {
        "explicit": "sequential__stochastic",
        "sampled": "sequential__stochastic",
        "mean_field": "mean_field",
    }

    manifest["population"]["registry_metadata"][0]["chance_mode"] = "typo"
    with pytest.raises(BatchValidationError, match="chance_mode differs"):
        _manifest_game_strata(manifest, ("explicit", "sampled", "mean_field"))


def _inputs(tmp_path: Path) -> tuple[Path, Path, tuple[str, ...]]:
    names = tuple(f"synthetic_game_{index:03d}" for index in range(105))
    source_hash = "a" * 64
    lock_hash = "b" * 64
    manifest = {
        "schema_version": 2,
        "manifest_status": "frozen_pending_archive",
        "protocol_version": "synthetic",
        "study_scope": {},
        "target_versions": {},
        "population": {
            "registry_metadata": [
                {
                    "short_name": name,
                    "dynamics": "sequential",
                    "chance_mode": "deterministic",
                }
                for name in names
            ]
        },
        "discovery": {},
        "environment": {
            "source_tree_sha256": source_hash,
            "uv_lock_sha256": lock_hash,
            "git_revision": "c" * 40,
            "git_dirty": False,
        },
        "configuration_evaluation": {},
        "case_inclusion": {},
        "validation": {
            "accounting_size": 106,
            "semantic_cohort": {"size": 105, "names": list(names)},
            "descriptive_exclusions": {"names": ["crossword"]},
        },
        "trace_schedule": {
            "policies": list(TRACE_POLICY_NAMES),
            "per_case": 8,
            "decision_cap": 1000,
            "destination_call_cap": PROSPECTIVE_DESTINATION_CALL_CAP,
            "outcome_classifier_id": CLASSIFIER_ID,
            "max_case_attempts": PROSPECTIVE_MAX_CASE_ATTEMPTS,
            "retry_eligibility": PROSPECTIVE_RETRY_ELIGIBILITY,
        },
        "execution_contract": prospective_execution_contract(),
        "external_baselines": external_baseline_protocol(),
        "mean_field_success": {},
        "mutation_evaluation": {
            "mutation_manifest_sha256": "d" * 64,
        },
        "outcome_reporting": {},
        "preregistration_warning": "synthetic",
    }
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest)
    receipt = {
        "schema_version": 1,
        "artifact_type": "marlrefine_protocol_archive_receipt",
        "manifest_sha256": _sha256(manifest_path),
        "source_tree_sha256": source_hash,
        "uv_lock_sha256": lock_hash,
        "published_at_utc": "2026-08-30T12:00:00+00:00",
        "doi": "10.5281/zenodo.1234567",
        "record_id": 1234567,
    }
    receipt_path = tmp_path / "receipt.json"
    write_json(receipt_path, receipt)
    return manifest_path, receipt_path, names


def test_analysis_accepts_disclosed_local_unregistered_authorization(
    tmp_path: Path,
) -> None:
    manifest_path, receipt_path, names = _inputs(tmp_path)
    manifest_sha256 = _sha256(manifest_path)
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "artifact_type": "marlrefine_local_execution_authorization",
            "manifest_sha256": manifest_sha256,
            "source_tree_sha256": "a" * 64,
            "uv_lock_sha256": "b" * 64,
            "authorized_at_utc": "2026-09-02T00:00:00+00:00",
            "authorization_id": f"local-unregistered:{manifest_sha256}",
            "source_git_revision": "c" * 40,
            "preregistered": False,
            "public_archive": False,
        },
    )

    _, authorization, observed_names, _, _ = _validate_manifest_and_receipt(
        manifest_path,
        receipt_path,
    )

    assert observed_names == names
    assert authorization["preregistered"] is False


def _run(game_name: str, policy_index: int, status: str) -> dict:
    policy = TRACE_POLICIES[policy_index]
    summary = {
        "trace_policy_name": policy.name,
        "trace_policy_id": policy.policy_id,
        "trace_policy_seed": policy.seed,
        "requested_seed": policy.environment_seed,
        "requested_max_source_decisions": 1000,
        "setup_status": "pass",
    }
    if status == "inapplicable":
        summary["setup_status"] = "inapplicable:synthetic"
        summary["caller_supplied_nondefault_configuration"] = False
        return TraceRun(
            game_spec=game_name,
            seed=policy.environment_seed,
            applicable=False,
            source_events=(),
            destination_events=(),
            alignment=align_traces((), ()),
            baselines=inapplicable_baselines("synthetic inapplicability"),
            violations=(),
            obligation_evaluations=build_obligation_evaluations(
                align_traces((), ()),
                (),
                caller_supplied_nondefault=False,
                configuration_evaluated=False,
                state_kind_evaluation_count=0,
                interface_evaluation_count=0,
                complete_episode=False,
                unresolved="not_applicable",
            ),
            summary=summary,
        ).to_dict()

    source = (
        SourceEvent(
            1,
            rewards=(1.0,),
            terminated=True,
            metadata={"action": 0, "node_kind_before": "decision"},
        ),
    )
    destination_reward = 0.0 if status == "fail" else 1.0
    destination = (
        DestinationEvent(
            1,
            rewards=(destination_reward,),
            delivered_rewards=(destination_reward,),
            terminated=True,
            metadata={
                "submitted_action": 0,
                "declared_action_space_n": 1,
                "player": 0,
                "action": 0,
                "source_state_digest_before": "d" * 64,
                "source_legal_actions_before": (0, 1),
                "progress_instrumentation": {
                    "method_id": "independent_native_replay_event_count_v1",
                    "progress_before": 0,
                    "progress_after": 1,
                    "annotated_progress_after": 1,
                    "replayed_source_event_count": 1,
                    "source_event_progresses": (1,),
                    "wrapped_history_delta": (0,),
                },
            },
        ),
    )
    alignment = align_traces(source, destination)
    baselines = (
        strict_lockstep(alignment),
        macro_boundary(alignment),
        macro_aggregate(alignment),
        endpoint(alignment),
        return_only(
            (1.0,),
            (destination_reward,),
            complete_episode=True,
        ),
    )
    if status == "fail":
        violations = (
            Violation(
                obligation="segment_reward_conservation",
                code="synthetic_reward_mismatch",
                message="synthetic reward differs",
                segment_index=0,
                source_span=Span(0, 1),
                destination_span=Span(0, 1),
                expected=(1.0,),
                observed=(0.0,),
            ),
        )
    elif status == "unalignable":
        violations = (
            Violation(
                obligation="trace_execution",
                code="unalignable_chance",
                message="synthetic chance transcript cannot align",
                segment_index=0,
                source_span=Span(0, 1),
                destination_span=Span(0, 1),
            ),
        )
    else:
        violations = ()
    summary.update(
        {
            "stop_reason": "destination_episode_end",
            "source_transitions": 1,
            "destination_calls": 1,
            "violation_count": len(violations),
            "caller_supplied_nondefault_configuration": False,
            "chance_event_count": 0,
            "source_terminal": True,
            "source_node_kind": "terminal",
            "adapter_agents_remaining": 0,
        }
    )
    return TraceRun(
        game_spec=game_name,
        seed=policy.environment_seed,
        applicable=True,
        source_events=source,
        destination_events=destination,
        alignment=alignment,
        baselines=baselines,
        violations=violations,
        obligation_evaluations=build_obligation_evaluations(
            alignment,
            violations,
            caller_supplied_nondefault=False,
            configuration_evaluated=True,
            state_kind_evaluation_count=2,
            interface_evaluation_count=1,
            complete_episode=True,
            unresolved="no_applicable_site",
        ),
        summary=summary,
    ).to_dict()


def _case(
    ordinal: int,
    game_name: str,
    policy_index: int,
    status: str,
    identities: dict[str, str],
) -> dict:
    policy = TRACE_POLICIES[policy_index]
    record = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_case",
        "classifier_id": CLASSIFIER_ID,
        **identities,
        "case": {
            "case_id": f"{game_name}::{policy.name}",
            "ordinal": ordinal,
            "game_name": game_name,
            "trace_policy_name": policy.name,
            "trace_policy_id": policy.policy_id,
            "trace_policy_seed": policy.seed,
            "environment_seed": policy.environment_seed,
        },
        "attempt": 1,
        "prior_record_sha256": None,
        "run": None if status == "infrastructure" else _run(
            game_name, policy_index, status
        ),
        "infrastructure_error": (
            {"exception_type": "SyntheticWorkerLoss", "message": "synthetic"}
            if status == "infrastructure"
            else None
        ),
        "captured_stdout": "",
        "captured_stderr": "",
        "elapsed_ns": ordinal + 1,
    }
    record["status"] = classify_case_record(record).value
    assert record["status"] == status
    return record


def _batch(
    tmp_path: Path,
) -> tuple[Path, Path, Path, list[dict]]:
    manifest_path, receipt_path, names = _inputs(tmp_path)
    identities = {
        "manifest_sha256": _sha256(manifest_path),
        "source_tree_sha256": "a" * 64,
        "uv_lock_sha256": "b" * 64,
    }
    header = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_batch_header",
        "classifier_id": CLASSIFIER_ID,
        "obligation_ledger_schema_id": OBLIGATION_LEDGER_SCHEMA_ID,
        **identities,
        "receipt_sha256": _sha256(receipt_path),
        "archive_identifier": "10.5281/zenodo.1234567",
        "archive_published_at_utc": "2026-08-30T12:00:00+00:00",
        "case_count": 840,
        "decision_cap": 1000,
        "destination_call_cap": PROSPECTIVE_DESTINATION_CALL_CAP,
        "max_case_attempts": PROSPECTIVE_MAX_CASE_ATTEMPTS,
        "retry_eligibility": PROSPECTIVE_RETRY_ELIGIBILITY,
        "known_descriptive_exclusions": ["crossword"],
        "resume_infrastructure_from_sha256": None,
        "runtime": {
            "source_tree_sha256": "a" * 64,
            "uv_lock_sha256": "b" * 64,
            "git_revision": "c" * 40,
        },
    }
    special = {
        0: "fail",
        8: "inapplicable",
        16: "unalignable",
        24: "infrastructure",
    }
    cases = []
    for ordinal in range(840):
        game_index, policy_index = divmod(ordinal, 8)
        cases.append(
            _case(
                ordinal,
                names[game_index],
                policy_index,
                special.get(ordinal, "pass"),
                identities,
            )
        )
    counts = Counter(str(record["status"]) for record in cases)
    footer = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_batch_footer",
        "classifier_id": CLASSIFIER_ID,
        **identities,
        "case_count": 840,
        "status_counts": dict(sorted(counts.items())),
        "resumed_infrastructure_cases": 0,
    }
    records = [header, *cases, footer]
    batch_path = tmp_path / "raw.jsonl"
    write_jsonl(batch_path, records)
    return batch_path, manifest_path, receipt_path, records


def _secondary_evidence(
    batch: Path,
    manifest: Path,
    receipt: Path,
) -> tuple[Path, Path]:
    external_path = batch.parent / "external-baselines.json"
    mutation_path = batch.parent / "mutation-batch.json"
    manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    with batch.open("r", encoding="utf-8") as handle:
        header = json.loads(handle.readline())
    runtime = header["runtime"]
    names = manifest_value["validation"]["semantic_cohort"]["names"]

    if not external_path.exists():
        stock_results = [
            {
                "game_spec": name,
                "cycles": STOCK_API_CYCLES,
                "passed": True,
                "exception": None,
                "warnings": [],
                "captured_output": "",
            }
            for name in names
        ]
        write_json(
            external_path,
            {
                "schema_version": EXTERNAL_BASELINE_SCHEMA_VERSION,
                "artifact_type": "marlrefine_prospective_external_baselines",
                "classifier_id": EXTERNAL_BASELINE_CLASSIFIER_ID,
                "manifest_sha256": _sha256(manifest),
                "source_tree_sha256": receipt_value["source_tree_sha256"],
                "uv_lock_sha256": receipt_value["uv_lock_sha256"],
                "receipt_sha256": _sha256(receipt),
                "archive_identifier": receipt_value["doi"],
                "archive_published_at_utc": receipt_value["published_at_utc"],
                "runtime": runtime,
                "stock_pettingzoo_api_test": {
                    "cycles": STOCK_API_CYCLES,
                    "action_space_seed": STOCK_API_ACTION_SPACE_SEED,
                    "case_count": len(names),
                    "status_counts": {"pass": len(names)},
                    "results": stock_results,
                },
                "released_shimmy_openspiel_suite": {
                    "role": (
                        "contextual_upstream_suite_evidence_not_cohort_comparator"
                    ),
                    "sdist_url": SHIMMY_SDIST_URL,
                    "sdist_sha256": SHIMMY_SDIST_SHA256,
                    "test_member": SHIMMY_OPENSPIEL_TEST_MEMBER,
                    "test_member_sha256": SHIMMY_OPENSPIEL_TEST_SHA256,
                    "pytest_args": ["-q", "--disable-warnings"],
                    "pythonhashseed": "0",
                    "result_classifier": (
                        "pytest_exit_0_pass_1_fail_else_infrastructure_v1"
                    ),
                    "limitations": external_baseline_protocol()[
                        "released_shimmy_openspiel_suite"
                    ]["limitations"],
                    "result": {
                        "status": "pass",
                        "returncode": 0,
                        "exception": None,
                        "stdout": "synthetic pass",
                        "stderr": "",
                        "elapsed_ns": 1,
                    },
                },
                "elapsed_ns": 2,
            },
        )

    if not mutation_path.exists():
        mutation = _synthetic_valid_batch()
        mutation.update(
            {
                "study_manifest_sha256": _sha256(manifest),
                "mutation_manifest_sha256": manifest_value[
                    "mutation_evaluation"
                ]["mutation_manifest_sha256"],
                "archive_identifier": receipt_value["doi"],
                "archive_published_at_utc": receipt_value["published_at_utc"],
                "source_tree_sha256": receipt_value["source_tree_sha256"],
                "uv_lock_sha256": receipt_value["uv_lock_sha256"],
                "receipt_sha256": _sha256(receipt),
                "runtime": runtime,
                "elapsed_ns": 3,
            }
        )
        mutation["self_validation"] = validate_mutation_batch(mutation)
        write_json(mutation_path, mutation)
    return external_path, mutation_path


def analyze_prospective_batch(
    batch_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    *,
    manual_adjudication_path: Path | None = None,
    external_baseline_path: Path | None = None,
    mutation_batch_path: Path | None = None,
) -> dict:
    default_external, default_mutation = _secondary_evidence(
        batch_path,
        manifest_path,
        receipt_path,
    )
    return _production_analyze_prospective_batch(
        batch_path,
        manifest_path,
        receipt_path,
        manual_adjudication_path=manual_adjudication_path,
        external_baseline_path=(external_baseline_path or default_external),
        mutation_batch_path=(mutation_batch_path or default_mutation),
    )


def _structured_manual(
    batch: Path,
    analysis: dict,
    *,
    status: str = "complete",
) -> dict:
    localized = analysis["witness_localization"]["witnesses"][0]
    witness = localized["witness"]
    boundary = witness["boundary"]
    causal_patch_sha256 = "7" * 64

    def trace_baseline_record(baseline_name: str) -> dict:
        outcome = localized["baseline_outcomes"][baseline_name]
        reached = outcome in {"detected", "not_detected"}
        same_root = outcome == "detected"
        return {
            "outcome": outcome,
            "root_witness_reached": reached,
            "outcome_evidence": {
                "artifact_sha256": _sha256(batch),
                "evidence_reference": localized["case_id"],
            },
            "causal_attribution": (
                "same_root" if same_root else "not_applicable"
            ),
            "causal_evidence": (
                {
                    "artifact_sha256": "9" * 64,
                    "evidence_reference": (
                        f"{localized['case_id']}::{baseline_name}::ablation"
                    ),
                    "patch_sha256": causal_patch_sha256,
                    "isolated_treatment": True,
                    "target_root_present_before": True,
                    "target_root_absent_after": True,
                    "baseline_signal_before": True,
                    "baseline_signal_after": False,
                }
                if same_root
                else None
            ),
            "credit": (
                "detected"
                if same_root
                else ("missed" if reached else "not_scored")
            ),
        }

    trace_baselines = {
        baseline_name: trace_baseline_record(baseline_name)
        for baseline_name in BASELINE_NAMES
    }
    return {
        "schema_version": 5,
        "artifact_type": "marlrefine_manual_adjudication",
        "raw_batch_sha256": _sha256(batch),
        "status": status,
        "roots": [
            {
                "root_id": "synthetic-reward-root",
                "provenance": "prospective",
                "family": "reward-conservation",
                "adjudication_status": "confirmed",
                "first_witness": {
                    "case_id": localized["case_id"],
                    "evidence_artifact_sha256": _sha256(batch),
                    "localizer_id": LOCALIZER_ID,
                    "localized_witness_sha256": localized[
                        "localized_witness_sha256"
                    ],
                    "boundary": {
                        "segment_index": boundary["segment_index"],
                        "source_event_stop": boundary["source_event_stop"],
                        "destination_event_stop": boundary[
                            "destination_event_stop"
                        ],
                        "selected_violation_index": witness[
                            "selected_violation_index"
                        ],
                    },
                },
                "contract": {
                    "citation": "Synthetic contract section 1",
                    "claim_classification": "defect",
                },
                "effect_summary": "The destination loses one reward unit.",
                "replay": {
                    "status": "reproduced",
                    "evidence": {
                        "artifact_sha256": "8" * 64,
                        "evidence_reference": "synthetic standalone replay",
                        "same_case_inputs": True,
                        "finding_reproduced": True,
                        "boundary_reproduced": True,
                    },
                },
                "baselines": {
                    **trace_baselines,
                    "stock_api": {
                        "outcome": "passed",
                        "root_witness_reached": True,
                        "outcome_evidence": {
                            "artifact_sha256": _sha256(
                                batch.parent / "external-baselines.json"
                            ),
                            "evidence_reference": localized["case_id"].rsplit(
                                "::", maxsplit=1
                            )[0],
                        },
                        "causal_attribution": "not_applicable",
                        "causal_evidence": None,
                        "credit": "missed",
                    },
                },
                "causal_patch": {
                    "stock_source_tree_sha256": "a" * 64,
                    "treatment_source_tree_sha256": "8" * 64,
                    "patch_sha256": causal_patch_sha256,
                    "evidence_reference": "synthetic isolated patch",
                },
                "repair": {
                    "status": "successful",
                    "evidence": {
                        "artifact_sha256": "d" * 64,
                        "evidence_reference": "synthetic repair case",
                        "failing_before": True,
                        "targeted_findings_absent_after": True,
                        "no_new_findings": True,
                        "reachability_preserved": True,
                        "regression_passed": True,
                        "reversion_restores_failure": True,
                        "patch_sha256": causal_patch_sha256,
                    },
                },
                "upstream": {"status": "not_contacted", "reference": None},
            }
        ],
        "finding_dispositions": [
            {
                "case_id": finding["case_id"],
                "violation_index": finding["violation_index"],
                "finding_sha256": finding["finding_sha256"],
                "disposition": (
                    "root"
                    if (
                        finding["case_id"] == localized["case_id"]
                        and finding["violation_index"]
                        == witness["selected_violation_index"]
                    )
                    else "rejected"
                ),
                "root_id": (
                    "synthetic-reward-root"
                    if (
                        finding["case_id"] == localized["case_id"]
                        and finding["violation_index"]
                        == witness["selected_violation_index"]
                    )
                    else None
                ),
                "rejection_reason": (
                    None
                    if (
                        finding["case_id"] == localized["case_id"]
                        and finding["violation_index"]
                        == witness["selected_violation_index"]
                    )
                    else "Synthetic non-root finding."
                ),
            }
            for finding in analysis["violations"][
                "prospective_finding_inventory"
            ]
        ],
        "controls": [
            {
                "control_id": control_id,
                "evidence_artifact_sha256": "e" * 64,
                "outcome": "pass",
                "observed_alarm_count": 0,
                "unexplained_alarm_count": 0,
            }
            for control_id in (
                "native_clone_replay_v1",
                "openspiel_turn_based_simultaneous_v1",
                "pettingzoo_parallel_to_aec_v1",
            )
        ],
        "optional_measurements": {
            "held_out_mutants_killed": 0 if status == "complete" else None,
            "held_out_mutants_total": 24 if status == "complete" else None,
            "peak_memory_bytes": 4096,
        },
    }


def test_frozen_analysis_separates_840_traces_from_105_games(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)

    analysis = analyze_prospective_batch(batch, manifest, receipt)

    traces = analysis["trace_level_accounting"]["counts"]
    assert traces == {
        "pass": 836,
        "fail": 1,
        "inapplicable": 1,
        "infrastructure": 1,
        "unalignable": 1,
        "scheduled": 840,
        "attempted": 840,
        "semantically_completed": 837,
        "with_applicable_violation": 1,
        "no_observed_violation": 836,
    }
    games = analysis["game_level_accounting"]
    assert games["population"] == 105
    assert games["exclusive_reporting_buckets"]["counts"] == {
        "infrastructure_present": 1,
        "unalignable_present_no_infrastructure": 1,
        "inapplicable_present_no_infrastructure_or_unalignable": 1,
        "violation_present_all_traces_semantically_completed": 1,
        "all_traces_no_observed_violation": 101,
    }
    flags = games["overlapping_flags"]["counts"]
    assert flags["games_with_any_fail"] == 1
    assert flags["games_all_eight_no_observed_violation"] == 101
    assert flags["games_semantically_complete_all_eight_traces"] == 102
    assert analysis["violations"]["by_code"]["synthetic_reward_mismatch"] == {
        "occurrence_count": 1,
        "trace_count": 1,
        "distinct_game_count": 1,
    }
    coverage = analysis["obligation_evaluation_coverage"]["by_obligation"]
    assert coverage["O3"]["trace_outcomes"] == {
        "evaluated_pass": 837,
        "evaluated_fail": 1,
        "not_applicable": 1,
        "not_evaluated": 1,
    }
    assert coverage["O3"]["evaluation_count"] == 1676
    assert coverage["O6"]["trace_outcomes"] == {
        "evaluated_pass": 0,
        "evaluated_fail": 0,
        "not_applicable": 839,
        "not_evaluated": 1,
    }
    paths = analysis["execution_path_coverage"]
    assert sum(paths["completion_by_status"]["terminal_complete"].values()) == 838
    assert sum(paths["completion_by_status"]["other_serialized_run"].values()) == 1
    assert paths["completion_by_status"]["no_run_infrastructure"] == {
        "pass": 0,
        "fail": 0,
        "inapplicable": 0,
        "infrastructure": 1,
        "unalignable": 0,
    }
    assert paths["observed_structure"]["aligned_transition_segments"] == {
        "occurrence_count": 838,
        "trace_count": 838,
        "distinct_game_count": 105,
    }
    assert analysis["violations"][
        "unlinked_execution_or_alignment_diagnostics"
    ]["occurrence_count"] == 1
    macro = analysis["compatible_baselines"]["macro_boundary"]
    assert macro["trace_outcomes"]["detected_traces"] == 1
    assert analysis["manual_adjudication"]["values"]["confirmed_roots"] is None
    assert analysis["witness_localization"]["witness_count"] == 2
    reward_sensitivity = analysis["tolerance_sensitivity"][
        "aligned_reward_values"
    ]
    assert reward_sensitivity["comparable_site_count"] == 838
    assert reward_sensitivity["within_primary"] == 837
    assert analysis["witness_localization"]["destination_phase_counts"] == {
        "commit": 2
    }
    assert analysis["schema_version"] == 9
    assert analysis["two_axis_trace_accounting"]["semantic_evidence"] == {
        "observed_failure": 1,
        "no_observed_failure": 836,
        "no_verdict": 3,
    }
    assert analysis["two_axis_trace_accounting"]["execution_completeness"] == {
        "terminal_complete": 837,
        "bounded_prefix": 0,
        "semantic_abort": 0,
        "unalignable": 1,
        "infrastructure": 1,
        "inapplicable": 1,
    }
    assert analysis["input_identities"]["frozen_source_git_revision"] == (
        "c" * 40
    )
    compact_prefix = analysis["witness_localization"]["witnesses"][0][
        "witness"
    ]["replayable_original_prefix"]
    assert "source_events" not in compact_prefix
    assert set(compact_prefix["ledger_prefixes"]) == {
        "source_events",
        "destination_events",
    }
    action_saturation = analysis["execution_path_coverage"]["action_coverage"][
        "cumulative_by_policy_order"
    ]
    assert action_saturation[0]["cumulative_offered_state_player_actions"] == 206
    assert action_saturation[0]["cumulative_selected_state_player_actions"] == 103
    assert action_saturation[1]["marginal_new_selected_state_player_actions"] == 2
    assert action_saturation[2]["marginal_new_selected_state_player_actions"] == 0
    assert analysis["execution_path_coverage"]["status_by_registry_stratum"][
        "sequential__deterministic"
    ] == {
        "pass": 836,
        "fail": 1,
        "inapplicable": 1,
        "infrastructure": 1,
        "unalignable": 1,
    }


def test_frozen_analysis_streams_jsonl_without_reading_whole_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    original_read_bytes = Path.read_bytes

    def reject_whole_batch_read(path: Path) -> bytes:
        if path == batch:
            raise AssertionError("analysis attempted a whole-batch read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_whole_batch_read)

    analysis = analyze_prospective_batch(batch, manifest, receipt)

    assert analysis["trace_level_accounting"]["counts"]["scheduled"] == 840


def test_latex_macros_keep_roots_manual_and_units_explicit(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    text = latex_result_macros(analyze_prospective_batch(batch, manifest, receipt))
    assert r"\newcommand{\ProspectiveGameCount}{105}" in text
    assert r"\newcommand{\ProspectiveTraceCount}{840}" in text
    assert r"\newcommand{\ProspectiveTraceFailures}{1}" in text
    assert r"\newcommand{\ProspectiveTerminalCompleteTraces}{838}" in text
    assert r"\newcommand{\ProspectiveBoundedPrefixPasses}{0}" in text
    assert r"\newcommand{\ProspectiveObligationCoverageRows}" in text
    assert "O3 & 837 & 1 & 1 & 1 & 1676" in text
    for macro_name in (
        "ConfirmedRepairAttempts",
        "ConfirmedRepairSuccesses",
        "ConfirmedRepairFailures",
        "ConfirmedRepairNonAttempts",
        "ConfirmedRepairNotApplicable",
    ):
        assert rf"\newcommand{{\{macro_name}}}" in text
    assert (
        r"\newcommand{\ProspectiveConfirmedRoots}"
        r"{\ResultPending{manual prospective confirmed roots}}" in text
    )
    assert (
        r"\newcommand{\ConfirmedRootTableRows}"
        r"{\multicolumn{4}{c}{\ResultPending{root rows from batch-bound "
        r"adjudication}} \\}" in text
    )
    assert r"\newcommand{\ProspectiveViolationTableRows}" in text
    assert r"\newcommand{\ConfirmedLocalizationTableRows}" in text
    assert r"\texttt{synthetic\_reward\_mismatch} & 1 & 1 & 1" in text
    assert r"\newcommand{\PreregistrationURL}{\url{https://doi.org/" in text
    assert (
        r"\newcommand{\MedianReductionRatio}"
        r"{not applicable (no minimizer)}" in text
    )
    assert (
        r"\newcommand{\FrozenSourceRevision}{\texttt{" + "c" * 40 + "}}"
        in text
    )
    assert (
        r"\newcommand{\RawBatchRevision}{\texttt{" + _sha256(batch) + "}}"
        in text
    )
    assert (
        r"\newcommand{\ReviewerPackageURL}"
        r"{\ResultPending{private reviewer-package URL}}" in text
    )
    assert (
        r"\newcommand{\ReviewerPackageRevision}"
        r"{\ResultPending{sealed reviewer-package revision}}" in text
    )
    assert r"\newcommand{\ArtifactURL}{\ReviewerPackageURL}" in text
    assert r"\newcommand{\ArtifactRevision}{\ReviewerPackageRevision}" in text
    assert r"\newcommand{\MutationSelectedTotal}{24}" in text
    assert r"\newcommand{\MutationSemanticKills}{0}" in text
    assert r"\newcommand{\MutationCrashOnlyKills}{0}" in text
    assert r"\newcommand{\MutationProgressControlsDetected}{2}" in text
    assert r"\newcommand{\MutationCohortComplete}{yes}" in text
    assert r"\newcommand{\MutationProgressControlsSatisfied}{yes}" in text
    assert r"\newcommand{\MutationRQSixReportable}{yes}" in text
    assert r"\newcommand{\MutationStrongPerformanceThresholdMet}{no (" in text
    assert r"\newcommand{\MutationSensitivityClaimReady}{no (" in text
    assert (
        r"\newcommand{\MutationReportingComplete}{\MutationRQSixReportable}"
        in text
    )
    assert (
        r"\newcommand{\MutationStrongSensitivityThresholdMet}"
        r"{\MutationStrongPerformanceThresholdMet}" in text
    )
    assert r"\newcommand{\MutationFamilyTableRows}" in text
    assert r"\newcommand{\MutationPairedComparatorRows}" in text


def test_latex_root_rows_are_sorted_confirmed_and_safely_escaped(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    value["roots"][0]["effect_summary"] = (
        r"Loss & drift_50% #1 {x} $5 \ path ~ ^"
    )

    alphabetic_root = json.loads(json.dumps(value["roots"][0]))
    alphabetic_root["root_id"] = "aaa_root"
    alphabetic_root["family"] = "alpha_family"
    alphabetic_root["provenance"] = "discovery"
    alphabetic_root["first_witness"]["case_id"] = "discovery_case"
    alphabetic_root["first_witness"]["evidence_artifact_sha256"] = "b" * 64
    alphabetic_root["effect_summary"] = "Alphabetically first."
    value["roots"].append(alphabetic_root)

    rejected_root = json.loads(json.dumps(value["roots"][0]))
    rejected_root["root_id"] = "rejected-root"
    rejected_root["adjudication_status"] = "rejected"
    rejected_root["contract"]["claim_classification"] = "not_a_defect"
    rejected_root["effect_summary"] = "SHOULD_NOT_RENDER"
    rejected_root["causal_patch"] = None
    for baseline in rejected_root["baselines"].values():
        baseline["credit"] = "not_scored"
        if baseline["causal_attribution"] == "same_root":
            baseline["causal_attribution"] = "different_or_unresolved"
            baseline["causal_evidence"] = None
    rejected_root["repair"] = {"status": "not_applicable", "evidence": None}
    rejected_root["upstream"] = {"status": "not_applicable", "reference": None}
    value["roots"].append(rejected_root)

    manual = tmp_path / "root_rows.json"
    write_json(manual, value)
    analysis = analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        manual_adjudication_path=manual,
    )
    text = latex_result_macros(analysis)

    assert text.index(r"\texttt{aaa\_root}") < text.index(
        r"\texttt{synthetic-reward-root}"
    )
    assert "SHOULD_NOT_RENDER" not in text
    assert (
        r"Loss \& drift\_50\% \#1 \{x\} \$5 \textbackslash{} path "
        r"\textasciitilde{} \textasciicircum{}" in text
    )
    assert r"\texttt{synthetic\_game\_000::smallest\_legal}" in text
    for label in ("SL", "MB", "MA", "EP", "RO", "API"):
        assert f"{label} " in text
    assert "[detected; detected; same\\_root; reached]" in text
    assert "[passed; missed; not\\_applicable; reached]" in text
    assert r"patch \texttt{777777777777}" in text
    assert "replay reproduced" in text
    assert "claim defect; upstream not\\_contacted" in text


def test_latex_root_rows_hide_partial_adjudication(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial, status="pending")
    value["roots"][0]["effect_summary"] = "PARTIAL_SHOULD_NOT_RENDER"
    manual = tmp_path / "partial.json"
    write_json(manual, value)
    analysis = analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        manual_adjudication_path=manual,
    )
    text = latex_result_macros(analysis)
    assert "PARTIAL" not in text
    assert r"\ResultPending{root rows from batch-bound adjudication}" in text


def test_analysis_rejects_reordered_case_identity(tmp_path: Path) -> None:
    batch, manifest, receipt, records = _batch(tmp_path)
    records[1]["case"], records[2]["case"] = (
        records[2]["case"],
        records[1]["case"],
    )
    write_jsonl(batch, records)
    with pytest.raises(BatchValidationError, match="identity/order"):
        analyze_prospective_batch(batch, manifest, receipt)


def test_analysis_rejects_status_or_footer_tampering(tmp_path: Path) -> None:
    batch, manifest, receipt, records = _batch(tmp_path)
    records[1]["status"] = "pass"
    write_jsonl(batch, records)
    with pytest.raises(BatchValidationError, match="classifier"):
        analyze_prospective_batch(batch, manifest, receipt)


def test_analysis_rejects_progress_replay_anchor_tampering(tmp_path: Path) -> None:
    batch, manifest, receipt, records = _batch(tmp_path)
    run = records[1]["run"]
    assert run is not None
    for event in (
        run["destination_events"][0],
        run["alignment"]["destination_events"][0],
        run["alignment"]["segments"][0]["destination_events"][0],
    ):
        event["metadata"]["progress_instrumentation"]["progress_after"] = 0
    write_jsonl(batch, records)

    with pytest.raises(BatchValidationError, match="progress instrumentation"):
        analyze_prospective_batch(batch, manifest, receipt)


def test_analysis_rejects_footer_count_tampering(tmp_path: Path) -> None:
    batch, manifest, receipt, records = _batch(tmp_path)
    records[-1]["status_counts"]["pass"] += 1
    write_jsonl(batch, records)
    with pytest.raises(BatchValidationError, match="footer status counts"):
        analyze_prospective_batch(batch, manifest, receipt)


def test_analysis_rejects_noncanonical_obligation_links(tmp_path: Path) -> None:
    batch, manifest, receipt, records = _batch(tmp_path)
    evaluations = records[1]["run"]["obligation_evaluations"]
    evaluations[2]["outcome"] = "evaluated_pass"
    evaluations[2]["finding_indices"] = []
    evaluations[7]["outcome"] = "evaluated_fail"
    evaluations[7]["finding_indices"] = [0]
    write_jsonl(batch, records)

    with pytest.raises(BatchValidationError, match="finding references differ"):
        analyze_prospective_batch(batch, manifest, receipt)


def test_structured_manual_claims_are_derived_and_batch_bound(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    manual = tmp_path / "manual.json"
    write_json(manual, _structured_manual(batch, initial))
    analysis = analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        manual_adjudication_path=manual,
    )
    adjudication = analysis["manual_adjudication"]
    assert adjudication["values"] == {
        "confirmed_roots": 1,
        "discovery_confirmed_roots": 0,
        "prospective_confirmed_roots": 1,
        "root_families": 1,
        "macro_baseline_misses": 0,
        "api_baseline_misses": 1,
        "repair_attempts": 1,
        "repair_successes": 1,
        "repair_failures": 0,
        "repair_non_attempts": 0,
        "repair_not_applicable": 0,
        "control_alarms": 0,
        "upstream_confirmed_roots": 0,
        "held_out_mutants_killed": 0,
        "held_out_mutants_total": 24,
        "peak_memory_bytes": 4096,
    }
    assert adjudication["derived_breakdown"][
        "confirmed_root_family_counts"
    ] == {"reward-conservation": 1}
    assert adjudication["derived_breakdown"][
        "confirmed_root_repair_status_counts"
    ] == {
        "successful": 1,
        "failed": 0,
        "not_attempted": 0,
        "not_applicable": 0,
        "pending": 0,
    }
    assert adjudication["roots"][0]["causal_patch"]["patch_sha256"] == "7" * 64
    assert adjudication["derived_breakdown"][
        "confirmed_prospective_root_finding_counts"
    ] == {"synthetic-reward-root": 1}
    assert analysis["violations"]["by_code"]["synthetic_reward_mismatch"][
        "trace_count"
    ] == 1


def test_complete_structured_manual_requires_mutation_measurements(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    value["optional_measurements"]["held_out_mutants_killed"] = None
    value["optional_measurements"]["held_out_mutants_total"] = None
    manual = tmp_path / "missing_mutation_measurements.json"
    write_json(manual, value)

    with pytest.raises(
        BatchValidationError,
        match="complete manual adjudication.*contract-derived mutant",
    ):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_preliminary_analysis_may_omit_secondary_evidence(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)

    analysis = _production_analyze_prospective_batch(batch, manifest, receipt)

    assert analysis["manual_adjudication"]["status"] == "pending"
    assert analysis["external_baselines"] is None
    assert analysis["mutation_evaluation"] is None
    assert analysis["input_identities"]["external_baselines"] is None
    assert analysis["input_identities"]["mutation_batch"] is None
    text = latex_result_macros(analysis)
    assert r"\ResultPending{validated sealed mutation evaluation}" in text
    assert r"\ResultPending{sealed reviewer-package revision}" in text


def test_complete_analysis_requires_both_secondary_artifacts(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    manual = tmp_path / "complete_without_secondary.json"
    write_json(manual, _structured_manual(batch, initial))

    with pytest.raises(
        BatchValidationError,
        match="requires exact external-baseline and mutation-batch artifacts",
    ):
        _production_analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_secondary_artifact_identities_and_mutation_scores_are_bound(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    external, mutation = _secondary_evidence(batch, manifest, receipt)

    analysis = _production_analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        external_baseline_path=external,
        mutation_batch_path=mutation,
    )

    assert analysis["input_identities"]["external_baselines"] == {
        "filename": external.name,
        "sha256": _sha256(external),
    }
    assert analysis["input_identities"]["mutation_batch"] == {
        "filename": mutation.name,
        "sha256": _sha256(mutation),
    }
    assert analysis["mutation_evaluation"]["overall"] == {
        "attempted_candidates": 48,
        "selected_total": 24,
        "semantic_kills": 0,
        "crash_only_kills": 0,
        "survived": 24,
    }
    assert len(analysis["mutation_evaluation"]["by_family"]) == 6
    validation = analysis["mutation_evaluation"]["validation"]
    assert validation["candidate_attempt_ledger_complete"] is True
    assert validation["cohort_complete_24"] is True
    assert validation["progress_controls_satisfied"] is True
    assert validation["rq6_reportable"] is True
    assert validation["strong_performance_threshold_met"] is False
    assert validation["strong_sensitivity_claim_ready"] is False


def test_analysis_rejects_tampered_secondary_artifacts(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    external, mutation = _secondary_evidence(batch, manifest, receipt)

    external_value = json.loads(external.read_text(encoding="utf-8"))
    external_value["manifest_sha256"] = "0" * 64
    tampered_external = tmp_path / "tampered-external.json"
    write_json(tampered_external, external_value)
    with pytest.raises(BatchValidationError, match="manifest_sha256 differs"):
        _production_analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            external_baseline_path=tampered_external,
            mutation_batch_path=mutation,
        )

    mutation_value = json.loads(mutation.read_text(encoding="utf-8"))
    mutation_value["score"]["selected_total"] = 23
    tampered_mutation = tmp_path / "tampered-mutation.json"
    write_json(tampered_mutation, mutation_value)
    with pytest.raises(BatchValidationError, match="score.selected_total"):
        _production_analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            external_baseline_path=external,
            mutation_batch_path=tampered_mutation,
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("schema_version",), True),
        (("stock_pettingzoo_api_test", "cycles"), 1000.0),
        (("stock_pettingzoo_api_test", "action_space_seed"), False),
        (("stock_pettingzoo_api_test", "case_count"), 105.0),
        (("stock_pettingzoo_api_test", "status_counts", "pass"), 105.0),
        (("stock_pettingzoo_api_test", "results", 0, "cycles"), 1000.0),
    ),
)
def test_external_baseline_rejects_json_scalar_aliases(
    tmp_path: Path,
    path: tuple[object, ...],
    replacement: object,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    external, mutation = _secondary_evidence(batch, manifest, receipt)
    value = json.loads(external.read_text(encoding="utf-8"))
    target: object = value
    for component in path[:-1]:
        target = target[component]  # type: ignore[index]
    target[path[-1]] = replacement  # type: ignore[index]
    write_json(external, value)

    with pytest.raises(BatchValidationError):
        _production_analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            external_baseline_path=external,
            mutation_batch_path=mutation,
        )


def test_external_suite_rejects_completed_result_with_exception(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    external, mutation = _secondary_evidence(batch, manifest, receipt)
    value = json.loads(external.read_text(encoding="utf-8"))
    value["released_shimmy_openspiel_suite"]["result"]["exception"] = "boom"
    write_json(external, value)

    with pytest.raises(BatchValidationError, match="cannot carry an exception"):
        _production_analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            external_baseline_path=external,
            mutation_batch_path=mutation,
        )


@pytest.mark.parametrize(
    ("artifact", "field", "replacement"),
    (
        ("manifest", "schema_version", 2.0),
        ("manifest", "extra", True),
        ("receipt", "schema_version", True),
        ("receipt", "extra", True),
    ),
)
def test_primary_identity_artifacts_reject_aliases_and_extra_keys(
    tmp_path: Path,
    artifact: str,
    field: str,
    replacement: object,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    target_path = manifest if artifact == "manifest" else receipt
    value = json.loads(target_path.read_text(encoding="utf-8"))
    value[field] = replacement
    write_json(target_path, value)
    if artifact == "manifest":
        receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_value["manifest_sha256"] = _sha256(manifest)
        write_json(receipt, receipt_value)

    with pytest.raises(BatchValidationError):
        _production_analyze_prospective_batch(batch, manifest, receipt)


@pytest.mark.parametrize(
    ("record_index", "field", "replacement"),
    (
        (0, "schema_version", True),
        (1, "schema_version", float(BATCH_SCHEMA_VERSION)),
        (-1, "case_count", 840.0),
    ),
)
def test_raw_batch_records_reject_json_scalar_aliases(
    tmp_path: Path,
    record_index: int,
    field: str,
    replacement: object,
) -> None:
    batch, manifest, receipt, records = _batch(tmp_path)
    records[record_index][field] = replacement
    write_jsonl(batch, records)

    with pytest.raises(BatchValidationError):
        _production_analyze_prospective_batch(batch, manifest, receipt)


def test_raw_batch_hash_is_of_the_exact_parsed_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    external, mutation = _secondary_evidence(batch, manifest, receipt)
    original_sha256 = _sha256(batch)
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b'{"replacement":true}\n')
    original_iterator = analysis_module._iter_canonical_jsonl

    def replacing_iterator(path: Path, *, digest: object | None = None):
        iterator = original_iterator(path, digest=digest)
        yield next(iterator)
        replacement.replace(path)
        yield from iterator

    monkeypatch.setattr(
        analysis_module,
        "_iter_canonical_jsonl",
        replacing_iterator,
    )
    analysis = _production_analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        external_baseline_path=external,
        mutation_batch_path=mutation,
    )

    assert analysis["input_identities"]["raw_batch"]["sha256"] == original_sha256
    assert analysis["input_identities"]["raw_batch"]["sha256"] != _sha256(batch)


def test_complete_manual_stock_api_evidence_matches_bound_artifact(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    value["roots"][0]["baselines"]["stock_api"]["outcome_evidence"][
        "evidence_reference"
    ] = "wrong-game"
    manual = tmp_path / "wrong-stock-reference.json"
    write_json(manual, value)

    with pytest.raises(BatchValidationError, match="bound external baseline"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_complete_manual_mutation_scalars_match_bound_batch(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    value["optional_measurements"]["held_out_mutants_killed"] = 1
    manual = tmp_path / "wrong-mutation-scalar.json"
    write_json(manual, value)

    with pytest.raises(BatchValidationError, match="bound mutation batch"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_repair_disposition_denominators_are_exhaustive_and_separate(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)

    for repair_status in ("failed", "not_attempted", "not_applicable"):
        root = json.loads(json.dumps(value["roots"][0]))
        root["root_id"] = f"discovery-{repair_status.replace('_', '-')}"
        root["provenance"] = "discovery"
        root["first_witness"]["case_id"] = f"discovery::{repair_status}"
        root["contract"]["claim_classification"] = "unsupported_capability"
        if repair_status == "failed":
            root["repair"]["status"] = "failed"
            root["repair"]["evidence"]["regression_passed"] = False
        else:
            root["repair"] = {"status": repair_status, "evidence": None}
        value["roots"].append(root)
    value["optional_measurements"]["peak_memory_bytes"] = None

    manual = tmp_path / "repair_dispositions.json"
    write_json(manual, value)
    analysis = analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        manual_adjudication_path=manual,
    )
    adjudication = analysis["manual_adjudication"]
    values = adjudication["values"]
    assert values["confirmed_roots"] == 4
    assert values["repair_attempts"] == 2
    assert values["repair_successes"] == 1
    assert values["repair_failures"] == 1
    assert values["repair_non_attempts"] == 1
    assert values["repair_not_applicable"] == 1
    assert adjudication["derived_breakdown"][
        "confirmed_root_repair_status_counts"
    ] == {
        "successful": 1,
        "failed": 1,
        "not_attempted": 1,
        "not_applicable": 1,
        "pending": 0,
    }

    text = latex_result_macros(analysis)
    assert r"\newcommand{\ConfirmedRepairAttempts}{2}" in text
    assert r"\newcommand{\ConfirmedRepairSuccesses}{1}" in text
    assert r"\newcommand{\ConfirmedRepairFailures}{1}" in text
    assert r"\newcommand{\ConfirmedRepairNonAttempts}{1}" in text
    assert r"\newcommand{\ConfirmedRepairNotApplicable}{1}" in text
    assert (
        r"\newcommand{\PeakMemory}{not measured (not instrumented)}" in text
    )


def test_complete_legacy_scalar_root_claims_are_rejected(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    manual = tmp_path / "legacy.json"
    write_json(
        manual,
        {
            "schema_version": 1,
            "artifact_type": "marlrefine_manual_adjudication",
            "raw_batch_sha256": _sha256(batch),
            "status": "complete",
            "values": {field: 0 for field in (
                "confirmed_roots",
                "root_families",
                "macro_baseline_misses",
                "api_baseline_misses",
                "repair_successes",
                "control_alarms",
                "upstream_confirmed_roots",
                "held_out_mutants_killed",
                "held_out_mutants_total",
                "peak_memory_bytes",
            )},
        },
    )
    with pytest.raises(BatchValidationError, match="structured.*schema_version 5"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_structured_v2_causal_credit_schema_is_invalidated(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    value["schema_version"] = 2
    manual = tmp_path / "invalidated_v2.json"
    write_json(manual, value)
    with pytest.raises(BatchValidationError, match="schema or batch identity"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_structured_manual_rejects_tampered_witness_reference(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    value["roots"][0]["first_witness"]["boundary"][
        "destination_event_stop"
    ] += 1
    manual = tmp_path / "tampered.json"
    write_json(manual, value)
    with pytest.raises(BatchValidationError, match="frozen localization"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_confirmed_root_requires_successful_standalone_replay(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    value["roots"][0]["replay"] = {
        "status": "failed",
        "evidence": {
            "artifact_sha256": "8" * 64,
            "evidence_reference": "synthetic failed replay",
            "same_case_inputs": True,
            "finding_reproduced": False,
            "boundary_reproduced": False,
        },
    }
    manual = tmp_path / "failed_replay.json"
    write_json(manual, value)

    with pytest.raises(BatchValidationError, match="requires a reproduced"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )


def test_complete_rejected_root_requires_attempted_bound_replay() -> None:
    with pytest.raises(
        BatchValidationError,
        match="complete adjudication requires an attempted standalone replay",
    ):
        _validate_replay(
            {"status": "not_applicable", "evidence": None},
            "roots[0].replay",
            adjudication_status="rejected",
            artifact_status="complete",
        )


def test_failed_replay_must_fail_at_least_one_criterion() -> None:
    with pytest.raises(BatchValidationError, match="fail at least one"):
        _validate_replay(
            {
                "status": "failed",
                "evidence": {
                    "artifact_sha256": "8" * 64,
                    "evidence_reference": "synthetic contradictory replay",
                    "same_case_inputs": True,
                    "finding_reproduced": True,
                    "boundary_reproduced": True,
                },
            },
            "roots[0].replay",
            adjudication_status="rejected",
            artifact_status="complete",
        )


def test_structured_manual_cross_checks_trace_baseline_and_repair_evidence(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)

    wrong_baseline = _structured_manual(batch, initial)
    wrong_baseline["roots"][0]["baselines"]["macro_aggregate"].update(
        {
            "outcome": "not_detected",
            "causal_attribution": "not_applicable",
            "causal_evidence": None,
            "credit": "missed",
        }
    )
    baseline_path = tmp_path / "wrong_baseline.json"
    write_json(baseline_path, wrong_baseline)
    with pytest.raises(BatchValidationError, match="first witness trace"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=baseline_path,
        )

    invalid_repair = _structured_manual(batch, initial)
    invalid_repair["roots"][0]["repair"]["evidence"][
        "reversion_restores_failure"
    ] = False
    repair_path = tmp_path / "invalid_repair.json"
    write_json(repair_path, invalid_repair)
    with pytest.raises(BatchValidationError, match="every criterion"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=repair_path,
        )

    unrepaired_defect = _structured_manual(batch, initial)
    unrepaired_defect["roots"][0]["repair"] = {
        "status": "not_attempted",
        "evidence": None,
    }
    unrepaired_path = tmp_path / "unrepaired_defect.json"
    write_json(unrepaired_path, unrepaired_defect)
    with pytest.raises(BatchValidationError, match="requires a successful"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=unrepaired_path,
        )


def test_baseline_detection_credit_requires_same_root_ablation(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)

    no_causal_evidence = _structured_manual(batch, initial)
    macro = no_causal_evidence["roots"][0]["baselines"]["macro_aggregate"]
    macro["causal_evidence"] = None
    no_evidence_path = tmp_path / "no_causal_evidence.json"
    write_json(no_evidence_path, no_causal_evidence)
    with pytest.raises(BatchValidationError, match="causal_evidence"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=no_evidence_path,
        )

    unresolved = _structured_manual(batch, initial)
    macro = unresolved["roots"][0]["baselines"]["macro_aggregate"]
    macro["causal_attribution"] = "different_or_unresolved"
    macro["causal_evidence"] = None
    macro["credit"] = "detected"
    unresolved_path = tmp_path / "unresolved.json"
    write_json(unresolved_path, unresolved)
    with pytest.raises(BatchValidationError, match="credit must be not_scored"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=unresolved_path,
        )

    macro["credit"] = "not_scored"
    unresolved_valid_path = tmp_path / "unresolved_valid.json"
    write_json(unresolved_valid_path, unresolved)
    analysis = analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        manual_adjudication_path=unresolved_valid_path,
    )
    adjudication = analysis["manual_adjudication"]
    assert adjudication["values"]["macro_baseline_misses"] == 0
    assert adjudication["derived_breakdown"][
        "confirmed_root_baseline_credit_counts"
    ]["macro_aggregate"] == {"not_scored": 1}

    false_ablation = _structured_manual(batch, initial)
    false_ablation["roots"][0]["baselines"]["macro_aggregate"][
        "causal_evidence"
    ]["baseline_signal_after"] = True
    false_ablation_path = tmp_path / "false_ablation.json"
    write_json(false_ablation_path, false_ablation)
    with pytest.raises(BatchValidationError, match="causal comparison differs"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=false_ablation_path,
        )


def test_baseline_miss_requires_reachable_root_witness(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    value = _structured_manual(batch, initial)
    api = value["roots"][0]["baselines"]["stock_api"]
    api["root_witness_reached"] = False
    manual = tmp_path / "unreached_api.json"
    write_json(manual, value)
    with pytest.raises(BatchValidationError, match="credit must be not_scored"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=manual,
        )

    api["credit"] = "not_scored"
    manual = tmp_path / "unreached_api_valid.json"
    write_json(manual, value)
    analysis = analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        manual_adjudication_path=manual,
    )
    assert analysis["manual_adjudication"]["values"]["api_baseline_misses"] == 0


def test_structured_manual_rejects_duplicate_roots_and_incomplete_controls(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)
    duplicate = _structured_manual(batch, initial)
    duplicate["roots"].append(duplicate["roots"][0])
    duplicate_path = tmp_path / "duplicate.json"
    write_json(duplicate_path, duplicate)
    with pytest.raises(BatchValidationError, match="root IDs.*unique"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=duplicate_path,
        )

    incomplete = _structured_manual(batch, initial)
    incomplete["controls"].pop()
    incomplete_path = tmp_path / "incomplete.json"
    write_json(incomplete_path, incomplete)
    with pytest.raises(BatchValidationError, match="three frozen controls"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=incomplete_path,
        )


def test_complete_finding_dispositions_are_exact_disjoint_and_nonempty(
    tmp_path: Path,
) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    initial = analyze_prospective_batch(batch, manifest, receipt)

    omitted = _structured_manual(batch, initial)
    omitted["finding_dispositions"].pop()
    omitted_path = tmp_path / "omitted_finding.json"
    write_json(omitted_path, omitted)
    with pytest.raises(BatchValidationError, match="omits prospective findings"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=omitted_path,
        )

    duplicated = _structured_manual(batch, initial)
    duplicated["finding_dispositions"].append(
        json.loads(json.dumps(duplicated["finding_dispositions"][0]))
    )
    duplicated_path = tmp_path / "duplicated_finding.json"
    write_json(duplicated_path, duplicated)
    with pytest.raises(BatchValidationError, match="duplicate prospective finding"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=duplicated_path,
        )

    empty_root = _structured_manual(batch, initial)
    root_disposition = next(
        item
        for item in empty_root["finding_dispositions"]
        if item["disposition"] == "root"
    )
    root_disposition.update(
        {
            "disposition": "rejected",
            "root_id": None,
            "rejection_reason": "Synthetic adjudicated rejection.",
        }
    )
    empty_root_path = tmp_path / "empty_root.json"
    write_json(empty_root_path, empty_root)
    with pytest.raises(BatchValidationError, match="has no finding membership"):
        analyze_prospective_batch(
            batch,
            manifest,
            receipt,
            manual_adjudication_path=empty_root_path,
        )


def test_legacy_all_null_pending_artifact_remains_supported(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    manual = tmp_path / "legacy_pending.json"
    fields = (
        "confirmed_roots",
        "root_families",
        "macro_baseline_misses",
        "api_baseline_misses",
        "repair_successes",
        "control_alarms",
        "upstream_confirmed_roots",
        "held_out_mutants_killed",
        "held_out_mutants_total",
        "peak_memory_bytes",
    )
    write_json(
        manual,
        {
            "schema_version": 1,
            "artifact_type": "marlrefine_manual_adjudication",
            "raw_batch_sha256": _sha256(batch),
            "status": "pending",
            "values": {field: None for field in fields},
        },
    )
    analysis = analyze_prospective_batch(
        batch,
        manifest,
        receipt,
        manual_adjudication_path=manual,
    )
    adjudication = analysis["manual_adjudication"]
    assert adjudication["schema_version"] == 1
    assert adjudication["status"] == "pending"
    assert all(value is None for value in adjudication["values"].values())


def test_analysis_rejects_noncanonical_jsonl(tmp_path: Path) -> None:
    batch, manifest, receipt, _ = _batch(tmp_path)
    first, *rest = batch.read_text().splitlines()
    value = json.loads(first)
    first = json.dumps(value, indent=2)
    batch.write_text("\n".join([first, *rest]) + "\n")
    with pytest.raises(BatchValidationError):
        _production_analyze_prospective_batch(batch, manifest, receipt)
