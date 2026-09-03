from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from marlrefine import mutation_study
from marlrefine.evaluation import OBLIGATION_IDS, OBLIGATION_LEDGER_SCHEMA_ID
from marlrefine.mutation_study import build_mutation_manifest
from marlrefine.study import (
    DISCOVERY_GAME_NAMES,
    KNOWN_DESCRIPTIVE_EXCLUSION_NAMES,
    MUTATION_NONDEFAULT_TRIGGER_GAME_SPECS,
    PROTOCOL_VERSION,
    build_draft_study_manifest,
    external_baseline_protocol,
    prospective_execution_contract,
)

_WRITER_PATH = (
    Path(__file__).resolve().parents[1] / "experiments/write_draft_manifest.py"
)
_WRITER_SPEC = importlib.util.spec_from_file_location(
    "marlrefine_test_manifest_writer", _WRITER_PATH
)
assert _WRITER_SPEC is not None and _WRITER_SPEC.loader is not None
manifest_writer = importlib.util.module_from_spec(_WRITER_SPEC)
_WRITER_SPEC.loader.exec_module(manifest_writer)


@pytest.fixture(autouse=True)
def _synthetic_registry(monkeypatch) -> None:
    """Keep manifest tests structural: never construct or execute an OpenSpiel game."""
    names = (
        *DISCOVERY_GAME_NAMES,
        *KNOWN_DESCRIPTIVE_EXCLUSION_NAMES,
        *(f"sealed_fixture_game_{index:03d}" for index in range(105)),
    )
    records = tuple(
        SimpleNamespace(
            short_name=name,
            dynamics="SEQUENTIAL",
            chance_mode="DETERMINISTIC",
            min_num_players=2,
            max_num_players=2,
            provides_observation_tensor=True,
            provides_observation_string=False,
            provides_information_state_tensor=False,
            provides_information_state_string=False,
        )
        for name in names
    )
    monkeypatch.setattr("marlrefine.study.default_loadable_types", lambda: records)


def test_draft_manifest_freezes_population_without_running_traces() -> None:
    manifest = build_draft_study_manifest()
    population = set(manifest["population"]["names"])
    discovery = set(manifest["discovery"]["names"])
    semantic = set(manifest["validation"]["semantic_cohort"]["names"])
    exclusions = set(manifest["validation"]["descriptive_exclusions"]["names"])

    assert manifest["schema_version"] == 2
    assert manifest["population"]["size"] == 113
    assert manifest["discovery"]["size"] == 7
    assert manifest["validation"]["accounting_size"] == 106
    assert manifest["validation"]["semantic_cohort"]["size"] == 105
    assert manifest["validation"]["descriptive_exclusions"]["size"] == 1
    assert discovery == set(DISCOVERY_GAME_NAMES)
    assert exclusions == set(KNOWN_DESCRIPTIVE_EXCLUSION_NAMES) == {"crossword"}
    assert discovery.isdisjoint(semantic)
    assert discovery.isdisjoint(exclusions)
    assert semantic.isdisjoint(exclusions)
    assert discovery | semantic | exclusions == population
    assert manifest["manifest_status"] == "draft_not_timestamp_archived"


def test_draft_manifest_freezes_claim_boundaries() -> None:
    manifest = build_draft_study_manifest()

    assert manifest["study_scope"]["adapter_implementation_count"] == 1
    assert not manifest["configuration_evaluation"]["prospective_nondefault_panel"]
    assert (
        manifest["configuration_evaluation"][
            "primary_105_game_configuration_scope"
        ]
        == "default_configuration_only"
    )
    assert manifest["configuration_evaluation"][
        "mutation_nondefault_trigger_panel"
    ]
    assert tuple(
        manifest["configuration_evaluation"]["mutation_nondefault_game_specs"]
    ) == MUTATION_NONDEFAULT_TRIGGER_GAME_SPECS
    assert (
        manifest["configuration_evaluation"]["configuration_preservation_evidence"]
        == "discovery_and_sealed_mutation_sensitivity_only"
    )
    assert manifest["mutation_evaluation"]["candidate_pool_count"] == 48
    assert manifest["mutation_evaluation"]["required_selected_count"] == 24
    assert manifest["mutation_evaluation"]["required_for_primary_study"]
    assert (
        manifest["mutation_evaluation"]["role"]
        == "mandatory_sealed_secondary_sensitivity_cohort"
    )
    assert manifest["mutation_evaluation"]["prearchive_activity"][
        "candidate_or_control_outcomes_executed"
    ] == 0
    assert manifest["protocol_version"] == PROTOCOL_VERSION
    assert (
        manifest["execution_contract"]["runner_protocol_version"]
        == PROTOCOL_VERSION
    )
    assert "fail-fast" in manifest["mean_field_success"]["accepted"][1]
    assert "silent termination" in manifest["mean_field_success"]["rejected"]
    assert "reportable" in manifest["outcome_reporting"]["zero_new_validation_defects"]
    assert manifest["trace_schedule"]["destination_call_cap"] == 10_000
    assert manifest["trace_schedule"]["max_case_attempts"] == 2
    assert (
        manifest["trace_schedule"]["retry_eligibility"]
        == "caught_runner_exception_without_run_payload_only"
    )
    assert manifest["execution_contract"] == prospective_execution_contract()
    ledger = manifest["execution_contract"]["obligation_evaluation_ledger"]
    assert ledger["schema_id"] == OBLIGATION_LEDGER_SCHEMA_ID
    assert tuple(ledger["ordered_obligation_ids"]) == OBLIGATION_IDS
    assert ledger["classifier_effect"] == "none_measurement_only"
    assert manifest["external_baselines"] == external_baseline_protocol()


def test_freeze_candidate_changes_only_explicit_status() -> None:
    digest = "a" * 64
    draft = build_draft_study_manifest(mutation_manifest_sha256=digest)
    candidate = build_draft_study_manifest(
        manifest_status="frozen_pending_archive",
        mutation_manifest_sha256=digest,
    )

    assert candidate["manifest_status"] == "frozen_pending_archive"
    candidate["manifest_status"] = draft["manifest_status"]
    assert candidate == draft


def test_unknown_manifest_status_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported manifest status"):
        build_draft_study_manifest(manifest_status="timestamp_archived")


def test_frozen_manifest_requires_sealed_mutation_hash() -> None:
    with pytest.raises(ValueError, match="sealed mutation manifest SHA-256"):
        build_draft_study_manifest(manifest_status="frozen_pending_archive")


def test_frozen_manifest_requires_explicit_clean_source_revision(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "write_draft_manifest.py",
            "--status",
            "frozen_pending_archive",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit):
        manifest_writer.main()
    assert not output.exists()


def test_container_manifest_records_validated_commit_a(tmp_path, monkeypatch) -> None:
    output = tmp_path / "manifest.json"
    revision = "a" * 40
    monkeypatch.setattr(
        manifest_writer,
        "build_draft_study_manifest",
        lambda *, manifest_status, mutation_manifest_sha256: {
            "manifest_status": manifest_status,
            "mutation_manifest_sha256": mutation_manifest_sha256,
            "population": {"size": 0},
            "discovery": {"size": 0},
            "validation": {
                "semantic_cohort": {"size": 0},
                "descriptive_exclusions": {"size": 0},
            },
        },
    )
    monkeypatch.setattr(
        manifest_writer,
        "runtime_provenance",
        lambda: {"git_revision": None, "git_dirty": None},
    )
    monkeypatch.setattr(
        manifest_writer,
        "_mutation_manifest_binding",
        lambda path, *, source_revision, study_environment: "b" * 64,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "write_draft_manifest.py",
            "--status",
            "frozen_pending_archive",
            "--source-git-revision",
            revision,
            "--output",
            str(output),
        ],
    )

    manifest_writer.main()

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["environment"]["git_revision"] == revision
    assert payload["environment"]["git_dirty"] is False
    assert payload["mutation_manifest_sha256"] == "b" * 64


def test_writer_binds_first_generated_mutation_manifest_to_commit_a(
    tmp_path, monkeypatch
) -> None:
    revision = "a" * 40
    environment = {
        "python": {"implementation": "CPython", "version": "3.13.2"},
        "packages": {"marlrefine": "0.1.0"},
        "installed_distribution_sha256": {"shimmy": "b" * 64},
        "uv_lock_sha256": "c" * 64,
        "source_tree_sha256": "d" * 64,
        "git_revision": revision,
        "git_dirty": False,
    }
    monkeypatch.setattr(
        mutation_study,
        "runtime_provenance",
        lambda: dict(environment),
    )
    mutation_path = tmp_path / "mutation.json"
    mutation_path.write_text(
        json.dumps(
            build_mutation_manifest(
                manifest_status="frozen_pending_archive",
                source_git_revision=revision,
            ),
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    assert manifest_writer._mutation_manifest_binding(
        mutation_path,
        source_revision=revision,
        study_environment=environment,
    ) == hashlib.sha256(mutation_path.read_bytes()).hexdigest()
