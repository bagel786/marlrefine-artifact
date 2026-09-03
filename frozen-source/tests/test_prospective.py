from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import marlrefine.prospective as prospective
from marlrefine.adapters.openspiel_shimmy import TraceRun
from marlrefine.alignment import align_traces
from marlrefine.evaluation import build_obligation_evaluations
from marlrefine.model import Violation
from marlrefine.policies import TRACE_POLICY_NAMES
from marlrefine.prospective import (
    OutcomeStatus,
    ProspectiveGateError,
    ResumeError,
    classify_run_payload,
    execute_prospective_batch,
)
from marlrefine.provenance import runtime_provenance
from marlrefine.serialization import write_json, write_jsonl
from marlrefine.study import (
    DISCOVERY_GAME_NAMES,
    build_draft_study_manifest,
    external_baseline_protocol,
    prospective_execution_contract,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate_files(
    tmp_path: Path,
    *,
    status: str = "frozen_pending_archive",
    semantic_names: list[str] | None = None,
) -> tuple[Path, Path]:
    names = semantic_names or ["nim"]
    provenance = runtime_provenance()
    source_hash = str(provenance["source_tree_sha256"])
    lock_hash = str(provenance["uv_lock_sha256"])
    manifest = {
        "schema_version": 2,
        "manifest_status": status,
        "environment": {
            "installed_distribution_sha256": provenance[
                "installed_distribution_sha256"
            ],
            "packages": provenance["packages"],
            "python": provenance["python"],
            "source_tree_sha256": source_hash,
            "uv_lock_sha256": lock_hash,
        },
        "target_versions": {
            "open_spiel": "2.0.2",
            "pettingzoo": "1.27.0",
            "shimmy": "2.0.1",
        },
        "discovery": {"names": ["go"]},
        "validation": {
            "accounting_size": 106,
            "semantic_cohort": {"size": len(names), "names": names},
            "descriptive_exclusions": {
                "size": 1,
                "names": ["crossword"],
            },
        },
        "trace_schedule": {
            "per_case": 8,
            "policies": list(TRACE_POLICY_NAMES),
            "decision_cap": 1000,
            "destination_call_cap": 10_000,
            "outcome_classifier_id": prospective.CLASSIFIER_ID,
            "max_case_attempts": prospective.MAX_CASE_ATTEMPTS,
            "retry_eligibility": prospective.PROSPECTIVE_RETRY_ELIGIBILITY,
        },
        "execution_contract": prospective_execution_contract(),
        "external_baselines": external_baseline_protocol(),
    }
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest)
    receipt = {
        "schema_version": 1,
        "artifact_type": "marlrefine_protocol_archive_receipt",
        "manifest_sha256": _sha256(manifest_path),
        "source_tree_sha256": source_hash,
        "uv_lock_sha256": lock_hash,
        "published_at_utc": "2020-01-01T00:00:00+00:00",
        "doi": "10.5281/zenodo.1234567",
        "record_id": 1234567,
        "archive_url": "https://zenodo.org/records/1234567",
        "protocol_bundle": {
            "filename": "marlrefine-protocol-v1.tar.gz",
            "sha256": "1" * 64,
        },
        "identity_file": {
            "filename": "protocol_identity.json",
            "sha256": "2" * 64,
        },
    }
    receipt_path = tmp_path / "receipt.json"
    write_json(receipt_path, receipt)
    return manifest_path, receipt_path


def _synthetic_run(game_name: str, **kwargs) -> TraceRun:
    alignment = align_traces((), ())
    return TraceRun(
        game_spec=game_name,
        seed=int(kwargs["seed"]),
        applicable=True,
        source_events=(),
        destination_events=(),
        alignment=alignment,
        baselines=(),
        violations=(),
        obligation_evaluations=build_obligation_evaluations(
            alignment,
            (),
            caller_supplied_nondefault=False,
            configuration_evaluated=True,
            state_kind_evaluation_count=0,
            interface_evaluation_count=0,
            complete_episode=False,
            unresolved="no_applicable_site",
        ),
        summary={
            "setup_status": "pass",
            "trace_policy_name": kwargs["trace_policy"].name,
            "caller_supplied_nondefault_configuration": False,
        },
    )


def _allow_discovery_fixture_partition(monkeypatch) -> None:
    def discovery_only(manifest) -> tuple[str, ...]:
        names = tuple(manifest["validation"]["semantic_cohort"]["names"])
        assert set(names).issubset(DISCOVERY_GAME_NAMES)
        return names

    monkeypatch.setattr(prospective, "_verify_frozen_partition", discovery_only)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"applicable": True, "violations": [], "summary": {"setup_status": "pass"}},
            OutcomeStatus.PASS,
        ),
        (
            {
                "applicable": True,
                "violations": [{"code": "semantic_mismatch"}],
                "summary": {"setup_status": "pass"},
            },
            OutcomeStatus.FAIL,
        ),
        (
            {"applicable": False, "violations": [], "summary": {}},
            OutcomeStatus.INAPPLICABLE,
        ),
        (
            {
                "applicable": False,
                "violations": [{"code": "source_setup_failed"}],
                "summary": {"setup_status": "error:source_setup:RuntimeError"},
            },
            OutcomeStatus.INFRASTRUCTURE,
        ),
        (
            {
                "applicable": True,
                "violations": [{"code": "adapter_setup_failed"}],
                "summary": {"setup_status": "error:adapter_setup:RuntimeError"},
            },
            OutcomeStatus.FAIL,
        ),
        (
            {
                "applicable": True,
                "violations": [{"code": "adapter_step_failed"}],
                "summary": {"setup_status": "pass"},
            },
            OutcomeStatus.FAIL,
        ),
        (
            {
                "applicable": False,
                "violations": [],
                "summary": {"setup_status": "inapplicable:sampled_stochastic_chance"},
            },
            OutcomeStatus.INAPPLICABLE,
        ),
        (
            {
                "applicable": True,
                "violations": [{"code": "unalignable_chance"}],
                "summary": {"setup_status": "pass"},
            },
            OutcomeStatus.UNALIGNABLE,
        ),
        (
            {
                "applicable": True,
                "violations": [
                    {"code": "progress_instrumentation_inconsistent"}
                ],
                "summary": {"setup_status": "pass"},
            },
            OutcomeStatus.INFRASTRUCTURE,
        ),
        (
            {"applicable": True, "violations": "corrupt", "summary": {}},
            OutcomeStatus.INFRASTRUCTURE,
        ),
    ],
)
def test_frozen_classifier_has_disjoint_outcomes(payload, expected) -> None:
    assert classify_run_payload(payload) is expected


def test_remote_zenodo_identity_binds_manifest_source_lock_and_bundle(
    monkeypatch,
) -> None:
    manifest_hash = "a" * 64
    source_hash = "b" * 64
    lock_hash = "c" * 64
    bundle_hash = "d" * 64
    identity = {
        "artifact_type": "marlrefine_protocol_freeze_identity",
        "manifest_sha256": manifest_hash,
        "protocol_bundle": {
            "filename": "protocol.tar.gz",
            "sha256": bundle_hash,
        },
        "schema_version": 1,
        "source_tree_sha256": source_hash,
        "uv_lock_sha256": lock_hash,
    }
    identity_payload = json.dumps(identity, sort_keys=True).encode()
    receipt = {
        "record_id": 7654321,
        "doi": "10.5281/zenodo.7654321",
        "protocol_bundle": {
            "filename": "protocol.tar.gz",
            "sha256": bundle_hash,
        },
        "identity_file": {
            "filename": "protocol_identity.json",
            "sha256": hashlib.sha256(identity_payload).hexdigest(),
        },
    }
    record = {
        "created": "2020-01-01T00:00:00+00:00",
        "doi": receipt["doi"],
        "files": [
            {
                "key": "protocol.tar.gz",
                "links": {"content": "https://zenodo.org/api/bundle"},
            },
            {
                "key": "protocol_identity.json",
                "links": {"content": "https://zenodo.org/api/identity"},
            },
        ],
    }

    def fake_read(url: str, *, maximum_bytes: int) -> bytes:
        del maximum_bytes
        if url.endswith("/api/records/7654321"):
            return json.dumps(record).encode()
        if url.endswith("/api/identity"):
            return identity_payload
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(prospective, "_read_https", fake_read)
    monkeypatch.setattr(
        prospective,
        "_hash_https",
        lambda url, maximum_bytes: bundle_hash,
    )
    prospective._verify_zenodo_publication(
        receipt,
        manifest_sha256=manifest_hash,
        source_tree_sha256=source_hash,
        uv_lock_sha256=lock_hash,
        published_at_utc="2020-01-01T00:00:00+00:00",
    )


def test_draft_manifest_cannot_authorize_execution(tmp_path: Path) -> None:
    manifest, receipt = _gate_files(
        tmp_path,
        status="draft_not_timestamp_archived",
    )
    called = False

    def forbidden_runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("gate executed a trace")

    with pytest.raises(ProspectiveGateError, match="not frozen"):
        execute_prospective_batch(
            manifest,
            receipt,
            tmp_path / "raw.jsonl",
            runner=forbidden_runner,
        )
    assert called is False


def test_local_unregistered_authorization_runs_without_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_discovery_fixture_partition(monkeypatch)
    manifest_path, receipt_path = _gate_files(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["environment"]["git_revision"] = "c" * 40
    write_json(manifest_path, manifest)
    manifest_sha256 = _sha256(manifest_path)
    authorization = {
        "schema_version": 1,
        "artifact_type": "marlrefine_local_execution_authorization",
        "manifest_sha256": manifest_sha256,
        "source_tree_sha256": manifest["environment"]["source_tree_sha256"],
        "uv_lock_sha256": manifest["environment"]["uv_lock_sha256"],
        "authorized_at_utc": "2026-09-02T00:00:00+00:00",
        "authorization_id": f"local-unregistered:{manifest_sha256}",
        "source_git_revision": "c" * 40,
        "preregistered": False,
        "public_archive": False,
    }
    write_json(receipt_path, authorization)
    monkeypatch.setattr(
        prospective,
        "_verify_zenodo_publication",
        lambda *args, **kwargs: pytest.fail("local authorization touched Zenodo"),
    )

    summary = execute_prospective_batch(
        manifest_path,
        receipt_path,
        tmp_path / "local.jsonl",
        runner=_synthetic_run,
    )

    assert summary.status_counts == {"pass": 8}
    header = json.loads((tmp_path / "local.jsonl").read_text().splitlines()[0])
    assert header["archive_identifier"] == f"local-unregistered:{manifest_sha256}"


def test_crossword_is_hard_excluded_before_runner_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        prospective,
        "_verify_frozen_partition",
        lambda manifest: tuple(manifest["validation"]["semantic_cohort"]["names"]),
    )
    monkeypatch.setattr(
        prospective,
        "_verify_zenodo_publication",
        lambda *args, **kwargs: None,
    )
    manifest, receipt = _gate_files(tmp_path, semantic_names=["crossword"])
    called = False

    def forbidden_runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("crossword reached runner")

    with pytest.raises(ProspectiveGateError, match="crossword"):
        execute_prospective_batch(
            manifest,
            receipt,
            tmp_path / "raw.jsonl",
            runner=forbidden_runner,
        )
    assert called is False


def test_manifest_dependency_lock_identity_is_mandatory(tmp_path: Path) -> None:
    manifest_path, receipt_path = _gate_files(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    del manifest["environment"]["uv_lock_sha256"]
    write_json(manifest_path, manifest)
    receipt = json.loads(receipt_path.read_text())
    receipt["manifest_sha256"] = _sha256(manifest_path)
    write_json(receipt_path, receipt)
    with pytest.raises(ProspectiveGateError, match="lock identity"):
        execute_prospective_batch(
            manifest_path,
            receipt_path,
            tmp_path / "raw.jsonl",
            runner=_synthetic_run,
        )


def test_runtime_identity_freezes_python_and_every_package_but_not_platform() -> None:
    provenance = runtime_provenance()
    environment = {
        "installed_distribution_sha256": dict(
            provenance["installed_distribution_sha256"]
        ),
        "packages": dict(provenance["packages"]),
        "platform": {"system": "different-host-is-allowed"},
        "python": {
            **provenance["python"],
            "executable_name": "different-python-path-is-allowed",
        },
    }
    prospective._verify_runtime_identity(environment, provenance)

    wrong_python = json.loads(json.dumps(environment))
    wrong_python["python"]["version"] = "0.0.0"
    with pytest.raises(ProspectiveGateError, match="Python implementation/version"):
        prospective._verify_runtime_identity(wrong_python, provenance)

    wrong_numpy = json.loads(json.dumps(environment))
    wrong_numpy["packages"]["numpy"] = "0.0.0"
    with pytest.raises(ProspectiveGateError, match="package versions"):
        prospective._verify_runtime_identity(wrong_numpy, provenance)

    missing_package = json.loads(json.dumps(environment))
    del missing_package["packages"]["gymnasium"]
    with pytest.raises(ProspectiveGateError, match="every pinned runtime package"):
        prospective._verify_runtime_identity(missing_package, provenance)

    patched_distribution = json.loads(json.dumps(environment))
    patched_distribution["installed_distribution_sha256"]["shimmy"] = "0" * 64
    with pytest.raises(ProspectiveGateError, match="distribution bytes"):
        prospective._verify_runtime_identity(patched_distribution, provenance)


def test_partition_tampering_is_rejected_before_archive_verification() -> None:
    manifest = json.loads(json.dumps(build_draft_study_manifest()))
    names = prospective._verify_frozen_partition(manifest)
    assert len(names) == 105

    tampered = json.loads(json.dumps(manifest))
    tampered["validation"]["accounting_names"][0] = DISCOVERY_GAME_NAMES[0]
    with pytest.raises(ProspectiveGateError, match="population minus discovery"):
        prospective._verify_frozen_partition(tampered)


def test_quiet_batch_seals_all_eight_policy_records(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _allow_discovery_fixture_partition(monkeypatch)
    monkeypatch.setattr(
        "marlrefine.prospective._verify_zenodo_publication",
        lambda *args, **kwargs: None,
    )
    manifest, receipt = _gate_files(tmp_path)
    output = tmp_path / "raw.jsonl"
    seen: list[tuple[str, str]] = []

    def recording_runner(game_name: str, **kwargs) -> TraceRun:
        assert game_name == "nim"
        seen.append((game_name, kwargs["trace_policy"].name))
        print(f"hidden case {kwargs['trace_policy'].name}")
        return _synthetic_run(game_name, **kwargs)

    summary = execute_prospective_batch(
        manifest,
        receipt,
        output,
        runner=recording_runner,
    )
    assert capsys.readouterr().out == ""
    assert seen == [("nim", name) for name in TRACE_POLICY_NAMES]
    assert summary.case_count == 8
    assert summary.status_counts == {"pass": 8}
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert records[0]["artifact_type"] == "marlrefine_prospective_batch_header"
    assert records[1]["captured_stdout"].startswith("hidden case ")
    assert records[-1]["status_counts"] == {"pass": 8}
    assert len(records) == 10


def test_primary_checkpoint_resumes_contiguous_prefix_without_disclosure(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _allow_discovery_fixture_partition(monkeypatch)
    monkeypatch.setattr(
        "marlrefine.prospective._verify_zenodo_publication",
        lambda *args, **kwargs: None,
    )
    manifest, receipt = _gate_files(tmp_path)
    output = tmp_path / "raw.jsonl"
    first_calls: list[str] = []

    def interrupted_runner(game_name: str, **kwargs) -> TraceRun:
        policy_name = kwargs["trace_policy"].name
        first_calls.append(policy_name)
        if policy_name == "pseudo_random_seed_0":
            raise KeyboardInterrupt("synthetic process interruption")
        return _synthetic_run(game_name, **kwargs)

    with pytest.raises(KeyboardInterrupt, match="synthetic process interruption"):
        execute_prospective_batch(
            manifest,
            receipt,
            output,
            runner=interrupted_runner,
        )

    assert output.exists() is False
    assert capsys.readouterr().out == ""
    checkpoint = prospective._primary_checkpoint_path(output)
    assert checkpoint.is_dir()
    assert sorted(path.name for path in checkpoint.iterdir()) == [
        "case-000000.bin",
        "case-000001.bin",
        "header.json",
    ]
    assert b'"status"' not in (checkpoint / "case-000000.bin").read_bytes()
    checkpoint_header = json.loads(
        (checkpoint / "header.json").read_text(encoding="utf-8")
    )
    assert "status_counts" not in checkpoint_header["batch_header"]
    assert first_calls == [
        "smallest_legal",
        "largest_legal",
        "pseudo_random_seed_0",
    ]

    resumed_calls: list[str] = []

    def resumed_runner(game_name: str, **kwargs) -> TraceRun:
        resumed_calls.append(kwargs["trace_policy"].name)
        return _synthetic_run(game_name, **kwargs)

    summary = execute_prospective_batch(
        manifest,
        receipt,
        output,
        runner=resumed_runner,
    )

    assert resumed_calls == list(TRACE_POLICY_NAMES[2:])
    assert summary.status_counts == {"pass": 8}
    assert checkpoint.exists() is False
    assert capsys.readouterr().out == ""


def test_complete_checkpoint_retries_atomic_seal_without_rerunning_cases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_discovery_fixture_partition(monkeypatch)
    monkeypatch.setattr(
        "marlrefine.prospective._verify_zenodo_publication",
        lambda *args, **kwargs: None,
    )
    manifest, receipt = _gate_files(tmp_path)
    output = tmp_path / "raw.jsonl"
    original_write_jsonl = prospective.write_jsonl
    calls: list[str] = []

    def recording_runner(game_name: str, **kwargs) -> TraceRun:
        calls.append(kwargs["trace_policy"].name)
        return _synthetic_run(game_name, **kwargs)

    monkeypatch.setattr(
        prospective,
        "write_jsonl",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("synthetic seal interruption")
        ),
    )
    with pytest.raises(OSError, match="synthetic seal interruption"):
        execute_prospective_batch(
            manifest,
            receipt,
            output,
            runner=recording_runner,
        )
    assert calls == list(TRACE_POLICY_NAMES)
    assert output.exists() is False

    monkeypatch.setattr(prospective, "write_jsonl", original_write_jsonl)

    def forbidden_runner(*args, **kwargs):
        del args, kwargs
        raise AssertionError("completed checkpoint reran a case")

    summary = execute_prospective_batch(
        manifest,
        receipt,
        output,
        runner=forbidden_runner,
    )
    assert summary.status_counts == {"pass": 8}


def test_resume_retries_only_one_prespecified_infrastructure_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_discovery_fixture_partition(monkeypatch)
    monkeypatch.setattr(
        "marlrefine.prospective._verify_zenodo_publication",
        lambda *args, **kwargs: None,
    )
    manifest, receipt = _gate_files(tmp_path)
    initial = tmp_path / "initial.jsonl"

    def one_crash(game_name: str, **kwargs) -> TraceRun:
        if kwargs["trace_policy"].name == "largest_legal":
            raise RuntimeError("synthetic worker loss")
        return _synthetic_run(game_name, **kwargs)

    first = execute_prospective_batch(
        manifest,
        receipt,
        initial,
        runner=one_crash,
    )
    assert first.status_counts == {"infrastructure": 1, "pass": 7}

    resumed = tmp_path / "resumed.jsonl"
    calls: list[str] = []

    def retry_runner(game_name: str, **kwargs) -> TraceRun:
        calls.append(kwargs["trace_policy"].name)
        return _synthetic_run(game_name, **kwargs)

    second = execute_prospective_batch(
        manifest,
        receipt,
        resumed,
        resume_infrastructure_from=initial,
        runner=retry_runner,
    )
    assert calls == ["largest_legal"]
    assert second.status_counts == {"pass": 8}
    assert second.resumed_infrastructure_cases == 1

    with pytest.raises(ResumeError, match="only an original primary batch"):
        execute_prospective_batch(
            manifest,
            receipt,
            tmp_path / "third.jsonl",
            resume_infrastructure_from=resumed,
            runner=retry_runner,
        )


def test_deterministic_call_budget_exhaustion_is_not_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_discovery_fixture_partition(monkeypatch)
    monkeypatch.setattr(
        "marlrefine.prospective._verify_zenodo_publication",
        lambda *args, **kwargs: None,
    )
    manifest, receipt = _gate_files(tmp_path)
    initial = tmp_path / "budget.jsonl"

    def budget_once(game_name: str, **kwargs) -> TraceRun:
        run = _synthetic_run(game_name, **kwargs)
        if kwargs["trace_policy"].name != "largest_legal":
            return run
        return replace(
            run,
            violations=(
                Violation(
                    obligation="trace_execution",
                    code="destination_call_budget_exhausted",
                    message="synthetic fixed-budget exhaustion",
                ),
            ),
        )

    first = execute_prospective_batch(
        manifest,
        receipt,
        initial,
        runner=budget_once,
    )
    assert first.status_counts == {"infrastructure": 1, "pass": 7}

    retry_called = False

    def forbidden_retry(*args, **kwargs):
        del args, kwargs
        nonlocal retry_called
        retry_called = True
        raise AssertionError("deterministic budget outcome was retried")

    with pytest.raises(ResumeError, match="no eligible infrastructure retry"):
        execute_prospective_batch(
            manifest,
            receipt,
            tmp_path / "budget-retry.jsonl",
            resume_infrastructure_from=initial,
            runner=forbidden_retry,
        )
    assert retry_called is False


def test_resume_rejects_schema_receipt_lineage_and_noncanonical_tampering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_discovery_fixture_partition(monkeypatch)
    monkeypatch.setattr(
        "marlrefine.prospective._verify_zenodo_publication",
        lambda *args, **kwargs: None,
    )
    manifest, receipt = _gate_files(tmp_path)
    initial = tmp_path / "initial-for-tamper.jsonl"

    def one_crash(game_name: str, **kwargs) -> TraceRun:
        if kwargs["trace_policy"].name == "largest_legal":
            raise RuntimeError("synthetic worker loss")
        return _synthetic_run(game_name, **kwargs)

    execute_prospective_batch(manifest, receipt, initial, runner=one_crash)
    original_records = [
        json.loads(line) for line in initial.read_text(encoding="utf-8").splitlines()
    ]

    def extra_header(records):
        records[0]["unexpected"] = True

    def wrong_receipt(records):
        records[0]["receipt_sha256"] = "0" * 64

    def second_attempt(records):
        records[1]["attempt"] = 2

    def boolean_attempt(records):
        records[1]["attempt"] = True

    def resumed_footer(records):
        records[-1]["resumed_infrastructure_cases"] = 1

    def extra_run_field(records):
        records[1]["run"]["unexpected"] = True

    def invalid_obligation_ledger(records):
        records[1]["run"]["obligation_evaluations"][0][
            "evaluation_count"
        ] = True

    def wrong_obligation_ledger_identity(records):
        records[0]["obligation_ledger_schema_id"] = "wrong"

    cases = (
        ("extra-header", extra_header, "header.*schema"),
        ("wrong-receipt", wrong_receipt, "receipt identity"),
        ("attempt-two", second_attempt, "not an original attempt"),
        ("attempt-boolean", boolean_attempt, "not an original attempt"),
        ("resumed-footer", resumed_footer, "not an original primary batch"),
        ("extra-run", extra_run_field, "trace run schema"),
        (
            "invalid-obligation-ledger",
            invalid_obligation_ledger,
            "evaluation_count",
        ),
        (
            "wrong-obligation-ledger-identity",
            wrong_obligation_ledger_identity,
            "obligation-ledger identity",
        ),
    )
    for label, mutate, error in cases:
        records = json.loads(json.dumps(original_records))
        mutate(records)
        candidate = tmp_path / f"{label}.jsonl"
        write_jsonl(candidate, records)
        with pytest.raises(ResumeError, match=error):
            execute_prospective_batch(
                manifest,
                receipt,
                tmp_path / f"{label}-retry.jsonl",
                resume_infrastructure_from=candidate,
                runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("tampered resume reached runner")
                ),
            )

    noncanonical = tmp_path / "noncanonical.jsonl"
    noncanonical.write_bytes(initial.read_bytes().replace(b"{", b"{ ", 1))
    with pytest.raises(ResumeError, match="not canonical"):
        execute_prospective_batch(
            manifest,
            receipt,
            tmp_path / "noncanonical-retry.jsonl",
            resume_infrastructure_from=noncanonical,
            runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("noncanonical resume reached runner")
            ),
        )
