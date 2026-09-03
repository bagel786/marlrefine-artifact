from __future__ import annotations

import json
import re

import pytest

import marlrefine.cli as cli
from marlrefine.cli import main


@pytest.mark.integration
def test_trace_cli_writes_a_self_describing_envelope(tmp_path) -> None:
    output = tmp_path / "trace.jsonl"
    exit_code = main(
        [
            "trace",
            "coop_box_pushing",
            "--seed",
            "7",
            "--max-source-decisions",
            "1",
            "--max-calls",
            "2",
            "--allow-violations",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["artifact_type"] == "marlrefine_trace_run"
    assert payload["environment"]["uv_lock_sha256"]
    assert payload["environment"]["source_tree_sha256"]
    manifest_identity = payload["study_manifest"]
    assert manifest_identity["path"] == "manifests/study_v1_draft.json"
    assert manifest_identity["sha256"] is None or re.fullmatch(
        r"[0-9a-f]{64}", manifest_identity["sha256"]
    )
    assert payload["case_id"] == "coop_box_pushing"
    assert payload["run"]["game_spec"] == "coop_box_pushing"
    assert payload["run"]["summary"]["requested_max_destination_calls"] == 2
    assert payload["run"]["summary"]["trace_policy_id"] == "smallest_legal_v1"
    assert [
        row["obligation_id"]
        for row in payload["run"]["obligation_evaluations"]
    ] == [f"O{index}" for index in range(1, 9)]
    assert {violation["code"] for violation in payload["run"]["violations"]} == {
        "source_decision_clock_mismatch"
    }


@pytest.mark.integration
def test_allow_violations_does_not_mask_setup_failure() -> None:
    assert main(["trace", "not_a_real_game", "--allow-violations"]) == 1


def test_public_trace_cli_rejects_frozen_name_before_runner(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    frozen_name = sorted(cli._frozen_semantic_names())[0]
    called = False

    def forbidden_runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("public trace CLI crossed the freeze boundary")

    monkeypatch.setattr(cli, "run_trace", forbidden_runner)
    output = tmp_path / "must_not_exist.jsonl"
    assert main(["trace", frozen_name, "--output", str(output)]) == 2
    assert called is False
    assert output.exists() is False
    assert "authorization-gated quiet" in capsys.readouterr().err
