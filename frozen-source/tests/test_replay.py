from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import marlrefine.replay as replay


def test_replay_prospective_finding_reexecutes_exact_sealed_inputs(
    tmp_path, monkeypatch
) -> None:
    finding = {
        "obligation": "state_projection",
        "code": "synthetic_mismatch",
        "message": "fixture",
        "source_span": None,
        "destination_span": {"start": 0, "stop": 1},
        "expected": 1,
        "observed": 2,
    }
    boundary = {
        "segment_index": 0,
        "source_event_stop": 1,
        "destination_event_stop": 1,
        "selected_violation_index": 0,
    }
    policy = SimpleNamespace(
        name="smallest_legal",
        policy_id="smallest_legal_v1",
        seed=None,
        environment_seed=0,
    )
    case = SimpleNamespace(
        case_id="sealed_game::smallest_legal",
        ordinal=0,
        game_name="sealed_game",
        policy=policy,
    )
    gate = SimpleNamespace(
        manifest_sha256="a" * 64,
        receipt_sha256="b" * 64,
        archive_identifier="10.5281/zenodo.123",
    )
    plan = SimpleNamespace(
        gate=gate,
        cases=(case,),
        decision_cap=1000,
        destination_call_cap=10000,
    )
    analysis = {
        "witness_localization": {
            "witnesses": [
                {
                    "case_id": case.case_id,
                    "violation_index": 0,
                    "localized_witness_sha256": "c" * 64,
                    "witness": {"boundary": boundary},
                }
            ]
        }
    }
    case_metadata = {
        "case_id": case.case_id,
        "ordinal": 0,
        "game_name": "sealed_game",
        "trace_policy_name": policy.name,
        "trace_policy_id": policy.policy_id,
        "trace_policy_seed": None,
        "environment_seed": 0,
    }
    original_record = {
        "case": case_metadata,
        "run": {"violations": [finding]},
    }
    replay_run = {"violations": [finding]}
    fake_trace = SimpleNamespace(to_dict=lambda: replay_run)

    monkeypatch.setattr(replay, "analyze_prospective_batch", lambda *args: analysis)
    monkeypatch.setattr(replay, "build_prospective_plan", lambda *args: plan)
    monkeypatch.setattr(replay, "_case_record", lambda *args: original_record)
    calls = []

    def fake_run_trace(game_name, **kwargs):
        calls.append((game_name, kwargs))
        return fake_trace

    monkeypatch.setattr(replay, "run_trace", fake_run_trace)
    monkeypatch.setattr(
        replay,
        "localize_divergence",
        lambda run, index: {"boundary": boundary},
    )
    batch = tmp_path / "batch.jsonl"
    batch.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "replay.json"

    payload = replay.replay_prospective_finding(
        batch,
        tmp_path / "manifest.json",
        tmp_path / "receipt.json",
        case_id=case.case_id,
        violation_index=0,
        output_path=output,
    )

    assert calls == [
        (
            "sealed_game",
            {
                "seed": 0,
                "trace_policy": policy,
                "max_destination_calls": 10000,
                "max_source_decisions": 1000,
            },
        )
    ]
    assert payload["status"] == "reproduced"
    assert payload["criteria"] == {
        "same_case_inputs": True,
        "finding_reproduced": True,
        "boundary_reproduced": True,
    }
    assert payload["raw_batch_sha256"] == hashlib.sha256(b"{}\n").hexdigest()
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "reproduced"


def test_replay_refuses_overwrite_before_validation(tmp_path) -> None:
    output = tmp_path / "existing.json"
    output.write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        replay.replay_prospective_finding(
            tmp_path / "batch.jsonl",
            tmp_path / "manifest.json",
            tmp_path / "receipt.json",
            case_id="sealed::policy",
            violation_index=0,
            output_path=output,
        )
