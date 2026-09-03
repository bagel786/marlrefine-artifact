"""Pre-freeze population and trace manifest construction."""

from __future__ import annotations

import re
from importlib.metadata import version
from typing import Any

from marlrefine.census import default_loadable_types
from marlrefine.evaluation import OBLIGATION_IDS, OBLIGATION_LEDGER_SCHEMA_ID
from marlrefine.mutations import (
    CANDIDATE_POOL,
    MUTANTS_PER_FAMILY,
    MUTATION_FAMILIES,
    POOL_PER_FAMILY,
)

PROTOCOL_VERSION = "1.1-prerun-final-2026-09-01"
MUTATION_MANIFEST_PATH = "manifests/mutation_v1.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MUTATION_NONDEFAULT_TRIGGER_GAME_SPECS = tuple(
    dict.fromkeys(
        candidate.game_spec
        for candidate in CANDIDATE_POOL
        if "(" in candidate.game_spec
    )
)

DISCOVERY_GAME_NAMES = (
    "coop_box_pushing",
    "go",
    "kuhn_poker",
    "matrix_rps",
    "mfg_crowd_modelling",
    "nim",
    "tic_tac_toe",
)

# Construction/reset behavior for this registry entry was inspected before the
# prospective freeze.  It therefore remains in the 113-type accounting table,
# but it is not a clean prospective semantic case.
KNOWN_DESCRIPTIVE_EXCLUSION_NAMES = ("crossword",)
PRIMARY_OUTCOME_CLASSIFIER_ID = "marlrefine_primary_outcome_v1"
PROSPECTIVE_DESTINATION_CALL_CAP = 10_000
PROSPECTIVE_MAX_CASE_ATTEMPTS = 2
PROSPECTIVE_RETRY_ELIGIBILITY = "caught_runner_exception_without_run_payload_only"
STOCK_API_CYCLES = 1_000
STOCK_API_ACTION_SPACE_SEED = 0
SHIMMY_SDIST_URL = (
    "https://files.pythonhosted.org/packages/44/72/"
    "8b1774448fba768b8d30bebf4deae3c301138522135a4ff4ae8e6a4109ad/"
    "shimmy-2.0.1.tar.gz"
)
SHIMMY_SDIST_SHA256 = "8770a697dd1272e433a852f7efae9faf826b10b33e7301cc2ca1a3b22646ad0c"
SHIMMY_OPENSPIEL_TEST_MEMBER = "shimmy-2.0.1/tests/test_openspiel.py"
SHIMMY_OPENSPIEL_TEST_SHA256 = (
    "9d5144d7793ff34cc826f9926485abb39cede4abdaa361281a4ed32b5ba36f11"
)
SHIMMY_SUITE_TIMEOUT_SECONDS = 7_200
MANIFEST_STATUSES = (
    "draft_not_timestamp_archived",
    "frozen_pending_archive",
)


def external_baseline_protocol() -> dict[str, Any]:
    """Return the exact post-authorization external-baseline execution contract."""
    return {
        "stock_pettingzoo_api_test": {
            "scope": "validation.semantic_cohort",
            "runs_per_game": 1,
            "cycles": STOCK_API_CYCLES,
            "action_space_seed": STOCK_API_ACTION_SPACE_SEED,
            "result_classifier": "exception_none_is_pass_v1",
        },
        "released_shimmy_openspiel_suite": {
            "role": "contextual_upstream_suite_evidence_not_cohort_comparator",
            "sdist_url": SHIMMY_SDIST_URL,
            "sdist_sha256": SHIMMY_SDIST_SHA256,
            "test_member": SHIMMY_OPENSPIEL_TEST_MEMBER,
            "test_member_sha256": SHIMMY_OPENSPIEL_TEST_SHA256,
            "pytest_args": ["-q", "--disable-warnings"],
            "timeout_seconds": SHIMMY_SUITE_TIMEOUT_SECONDS,
            "result_classifier": ("pytest_exit_0_pass_1_fail_else_infrastructure_v1"),
            "limitations": [
                "upstream fixed game list is not the 105-game cohort",
                "upstream passing/loading paths sample unseeded action spaces",
                "tests ship in the sdist but not the installed wheel",
            ],
        },
    }


def prospective_execution_contract() -> dict[str, Any]:
    """Return stable identifiers for the source-aligned executable contract."""
    return {
        "runner_protocol_version": PROTOCOL_VERSION,
        "chance_policy_id": "explicit_adapter_history_replay_v1",
        "chance_claim_boundary": (
            "pathwise_conformance_conditional_on_observed_explicit_chance_transcript"
        ),
        "policy_engine_id": "sha256_source_legal_v1",
        "progress_annotation_method_id": (
            "independent_native_replay_event_count_v1"
        ),
        "progress_definition": (
            "cumulative atomic source transitions successfully replayed on the "
            "separately loaded native source after each destination call"
        ),
        "progress_trust_assumptions": [
            "wrapped history reports committed source transitions in execution order",
            "pre-call wrapped history is a prefix of post-call wrapped history",
            (
                "submitted player or joint actions and explicit chance outcomes "
                "replay legally"
            ),
            (
                "history current-player terminality and tagged state digest "
                "identify the exercised boundary"
            ),
            "instrumentation inspection does not perturb destination execution",
        ],
        "reward_atol": 1e-12,
        "reward_rtol": 1e-12,
        "reward_tolerance_rationale": (
            "float64 return-delta and public-reward roundoff only"
        ),
        "observation_atol": 1e-7,
        "observation_rtol": 1e-7,
        "observation_comparison_order": [
            "raw_container_type",
            "raw_element_count",
            "raw_shape",
            "raw_dtype",
            "normalized_value",
        ],
        "observation_tolerance_rationale": (
            "value-only allowance for float64 projections after exact raw "
            "signature checks"
        ),
        "contract_references": {
            "openspiel_game_interface": (
                "https://github.com/google-deepmind/open_spiel/blob/v2.0.2/"
                "open_spiel/spiel.h"
            ),
            "shimmy_openspiel_adapter_source": (
                "https://github.com/Farama-Foundation/Shimmy/blob/v2.0.1/"
                "shimmy/openspiel_compatibility.py"
            ),
            "pettingzoo_aec_api": "https://pettingzoo.farama.org/api/aec/",
            "posg_to_aec_semantics": "https://arxiv.org/html/2009.14471v7#Sx11",
        },
        "checker_family_ids": [
            "configuration_provenance",
            "interface_projection",
            "state_projection",
            "lifecycle_preservation",
            "decision_clock_preservation",
            "state_kind_soundness",
            "delivered_reward_conservation",
            "trace_execution",
            "stutter_reward_neutrality",
            "segment_reward_conservation",
            "monotone_progress_and_completeness",
            "terminal_cleanup_reward_neutrality",
            "boundary_lifecycle_preservation",
        ],
        "project_baseline_ids": [
            "strict_lockstep",
            "macro_boundary",
            "macro_aggregate",
            "endpoint",
            "return_only",
        ],
        "project_comparator_role": (
            "in_line_schedule_and_information_ablations_not_external_baselines"
        ),
        "obligation_evaluation_ledger": {
            "schema_id": OBLIGATION_LEDGER_SCHEMA_ID,
            "ordered_obligation_ids": list(OBLIGATION_IDS),
            "trace_outcomes": [
                "evaluated_pass",
                "evaluated_fail",
                "not_applicable",
                "not_evaluated",
            ],
            "site_definitions": {
                "O1": "successful_or_failed_simultaneous_buffer_microstep",
                "O2": "buffer_or_terminal_cleanup_microstep",
                "O3": "aligned_transition_plus_completed_consumer_return",
                "O4": "normalized_source_decision_clock_comparison",
                "O5": "aligned_lifecycle_boundary_or_cleanup_inertness_check",
                "O6": "caller_supplied_nondefault_configuration_comparison",
                "O7": "distinct_reached_source_node_kind_boundary",
                "O8": (
                    "live_agent_selection_schedule_and_declared_interface_boundary"
                ),
            },
            "finding_indices": (
                "sorted_unique_indices_into_the_enclosing_trace_violation_list"
            ),
            "classifier_effect": "none_measurement_only",
        },
    }


def build_draft_study_manifest(
    *,
    manifest_status: str = "draft_not_timestamp_archived",
    mutation_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the exact split without executing any semantic validation trace.

    ``frozen_pending_archive`` is the legacy schema spelling for the immutable
    execution candidate. A separate public receipt can prove archival, while a
    local authorization explicitly records that no such preregistration exists.
    """
    if manifest_status not in MANIFEST_STATUSES:
        raise ValueError(
            f"unsupported manifest status {manifest_status!r}; "
            f"expected one of {MANIFEST_STATUSES}"
        )
    if mutation_manifest_sha256 is not None and not SHA256_PATTERN.fullmatch(
        mutation_manifest_sha256
    ):
        raise ValueError("mutation manifest SHA-256 must be a lowercase digest")
    if (
        manifest_status == "frozen_pending_archive"
        and mutation_manifest_sha256 is None
    ):
        raise ValueError(
            "frozen study manifests require the sealed mutation manifest SHA-256"
        )
    population_types = default_loadable_types()
    population = tuple(game.short_name for game in population_types)
    population_metadata = tuple(
        {
            "short_name": game.short_name,
            "dynamics": str(getattr(game.dynamics, "name", game.dynamics)).lower(),
            "chance_mode": str(
                getattr(game.chance_mode, "name", game.chance_mode)
            ).lower(),
            "min_players": int(game.min_num_players),
            "max_players": int(game.max_num_players),
            "provides_observation": bool(
                game.provides_observation_tensor
                or game.provides_observation_string
            ),
            "provides_information_state": bool(
                game.provides_information_state_tensor
                or game.provides_information_state_string
            ),
        }
        for game in population_types
    )
    if len(population) != 113:
        raise RuntimeError(
            "expected 113 registry-marked default-loadable game types, found "
            f"{len(population)}"
        )
    missing = sorted(set(DISCOVERY_GAME_NAMES).difference(population))
    if missing:
        raise RuntimeError(f"discovery names absent from census: {missing}")
    validation_accounting = tuple(
        name for name in population if name not in DISCOVERY_GAME_NAMES
    )
    if len(validation_accounting) != 106:
        raise RuntimeError(
            "expected 106 names outside the seven-name discovery set, found "
            f"{len(validation_accounting)}"
        )
    missing_exclusions = sorted(
        set(KNOWN_DESCRIPTIVE_EXCLUSION_NAMES).difference(validation_accounting)
    )
    if missing_exclusions:
        raise RuntimeError(
            "known descriptive exclusions absent from residual census: "
            f"{missing_exclusions}"
        )
    semantic_cohort = tuple(
        name
        for name in validation_accounting
        if name not in KNOWN_DESCRIPTIVE_EXCLUSION_NAMES
    )
    if len(semantic_cohort) != 105:
        raise RuntimeError(
            f"expected 105 prospective semantic names, found {len(semantic_cohort)}"
        )

    return {
        "schema_version": 2,
        "manifest_status": manifest_status,
        "protocol_version": PROTOCOL_VERSION,
        "study_scope": {
            "adapter_implementation_count": 1,
            "adapter_implementation": "Shimmy OpenSpielCompatibilityV0 2.0.1",
            "claim_boundary": (
                "bounded traces over the pinned catalog; a passing trace means "
                "no violation detected under the specified budget"
            ),
        },
        "target_versions": {
            "shimmy": version("shimmy"),
            "open_spiel": version("open-spiel"),
            "pettingzoo": version("pettingzoo"),
        },
        "population": {
            "definition": (
                "OpenSpiel 2.0.2 registry entries whose "
                "GameType.default_loadable flag is true"
            ),
            "size": len(population),
            "names": population,
            "registry_metadata": population_metadata,
        },
        "discovery": {
            "size": len(DISCOVERY_GAME_NAMES),
            "names": DISCOVERY_GAME_NAMES,
            "nondefault_known_configuration": "go(board_size=5)",
            "status": "all outcomes inspected before archive",
        },
        "validation": {
            "accounting_size": len(validation_accounting),
            "accounting_names": validation_accounting,
            "semantic_cohort": {
                "size": len(semantic_cohort),
                "names": semantic_cohort,
                "status": (
                    "do not execute before either a verified public archive receipt "
                    "or an explicit local authorization that records "
                    "preregistered=false"
                ),
            },
            "descriptive_exclusions": {
                "size": len(KNOWN_DESCRIPTIVE_EXCLUSION_NAMES),
                "names": KNOWN_DESCRIPTIVE_EXCLUSION_NAMES,
                "reason": (
                    "crossword construction/reset capability was inspected "
                    "before freeze; retain it in descriptive 113-type accounting "
                    "but run no prospective semantic trace"
                ),
            },
        },
        "configuration_evaluation": {
            "prospective_nondefault_panel": False,
            "primary_105_game_configuration_scope": "default_configuration_only",
            "prospective_default_configuration_claim": (
                "default configurations cannot establish preservation of "
                "caller-supplied nondefault parameters"
            ),
            "mutation_nondefault_trigger_panel": True,
            "mutation_nondefault_game_specs": (
                MUTATION_NONDEFAULT_TRIGGER_GAME_SPECS
            ),
            "mutation_panel_role": (
                "sealed synthetic-sensitivity triggers only; it does not expand "
                "the default-only primary 105-game configuration claim"
            ),
            "configuration_preservation_evidence": (
                "discovery_and_sealed_mutation_sensitivity_only"
            ),
        },
        "case_inclusion": {
            "single_agent": (
                "include and report as a separate metadata subgroup; do not use "
                "it as evidence for multi-agent buffering claims"
            ),
            "other_nonstandard_types": (
                "retain in census and prospective accounting; report by state "
                "kind, trace status, and finding incidence rather than post hoc "
                "exclusion"
            ),
        },
        "mean_field_success": {
            "accepted": (
                "genuine distribution-update support preserving applicable "
                "source boundaries",
                "explicit fail-fast rejection before episode execution with a "
                "specific unsupported-mean-field classification",
            ),
            "rejected": "silent termination or ordinary-terminal projection",
        },
        "mutation_evaluation": {
            "role": "mandatory_sealed_secondary_sensitivity_cohort",
            "required_for_primary_study": True,
            "mutation_manifest_path": MUTATION_MANIFEST_PATH,
            "mutation_manifest_sha256": mutation_manifest_sha256,
            "candidate_pool_count": len(CANDIDATE_POOL),
            "candidate_pool_per_family": POOL_PER_FAMILY,
            "family_count": len(MUTATION_FAMILIES),
            "families": MUTATION_FAMILIES,
            "required_eligible_per_family": MUTANTS_PER_FAMILY,
            "required_selected_count": (
                len(MUTATION_FAMILIES) * MUTANTS_PER_FAMILY
            ),
            "selection_rule": (
                "within each family select the first four candidates in frozen "
                "priority order whose clean reference is acceptable, whose hook "
                "fires, whose adapter-facing execution differs, and whose paired "
                "behavior-delta hash is distinct from prior selected candidates "
                "in that family; MARLRefine findings, baseline findings, and "
                "stock api_test outcomes are ignored by selection"
            ),
            "prearchive_activity": {
                "permitted": (
                    "declarative operator authoring, deterministic candidate and "
                    "control generation, import and syntax validation, canonical "
                    "patch generation, and hashing without game construction or "
                    "outcome execution"
                ),
                "excluded_from_held_out_scoring": (
                    "any candidate or control with an executed outcome before the "
                    "archive, plus any direct recreation of a development fixture"
                ),
                "candidate_or_control_outcomes_executed": 0,
            },
            "sealed_candidate_pool": (
                "all 48 candidates are reserved without execution before the "
                "execution authorization; eligibility selects exactly 24 afterward"
            ),
        },
        "outcome_reporting": {
            "zero_new_validation_defects": (
                "reportable primary outcome; it does not cancel the study or "
                "authorize relabeling exploratory findings as prospective"
            ),
        },
        "trace_schedule": {
            "applies_to": "validation.semantic_cohort (105 names) only",
            "per_case": 8,
            "policies": (
                "smallest_legal",
                "largest_legal",
                "pseudo_random_seed_0",
                "pseudo_random_seed_1",
                "pseudo_random_seed_2",
                "pseudo_random_seed_3",
                "pseudo_random_seed_4",
                "pseudo_random_seed_5",
            ),
            "decision_cap": 1000,
            "destination_call_cap": PROSPECTIVE_DESTINATION_CALL_CAP,
            "outcome_classifier_id": PRIMARY_OUTCOME_CLASSIFIER_ID,
            "max_case_attempts": PROSPECTIVE_MAX_CASE_ATTEMPTS,
            "retry_eligibility": PROSPECTIVE_RETRY_ELIGIBILITY,
            "claim_boundary": (
                "no violation detected under these bounded traces is not a claim "
                "of semantic equivalence"
            ),
            "chance_policy": (
                "record explicit adapter chance outcomes and replay them on the "
                "separately loaded native source; sampled-stochastic games are "
                "inapplicable "
                "until RNG/state coupling is implemented"
            ),
            "implementation_status": (
                "all eight source-legal policies implemented; guarded batch "
                "execution remains disabled until a public receipt or explicit "
                "local unregistered authorization is verified"
            ),
        },
        "execution_contract": prospective_execution_contract(),
        "external_baselines": external_baseline_protocol(),
        "preregistration_warning": (
            "This manifest is not a preregistration until its exact bytes and "
            "the matching checker, baselines, manifests, and analysis code are "
            "timestamp-archived and a published receipt verifies their hashes."
        ),
    }
