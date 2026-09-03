from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from types import SimpleNamespace

import marlrefine.external_baselines as external
from marlrefine.stock_tests import StockApiResult
from marlrefine.study import external_baseline_protocol


def _tar_with_member(name: str, payload: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def test_shimmy_suite_source_requires_exact_frozen_member(monkeypatch) -> None:
    source = b"def test_released_fixture():\n    assert True\n"
    archive = _tar_with_member("release/tests/test_openspiel.py", source)
    monkeypatch.setattr(
        external,
        "SHIMMY_OPENSPIEL_TEST_MEMBER",
        "release/tests/test_openspiel.py",
    )
    monkeypatch.setattr(
        external,
        "SHIMMY_OPENSPIEL_TEST_SHA256",
        hashlib.sha256(source).hexdigest(),
    )

    assert external._shimmy_test_source(archive) == source


def test_external_baselines_are_archive_gated_atomic_and_once_per_game(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(external, "EXPECTED_EXTERNAL_CASE_COUNT", 2)
    manifest = {
        "external_baselines": external_baseline_protocol(),
        "validation": {"semantic_cohort": {"names": ["nim", "matrix_rps"]}},
    }
    gate = SimpleNamespace(
        manifest=manifest,
        manifest_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        uv_lock_sha256="c" * 64,
        receipt_sha256="d" * 64,
        archive_identifier="10.5281/zenodo.123",
        published_at_utc="2020-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        external,
        "build_prospective_plan",
        lambda manifest_path, receipt_path: SimpleNamespace(gate=gate),
    )
    monkeypatch.setattr(external, "_download_shimmy_sdist", lambda: b"sdist")
    monkeypatch.setattr(external, "_shimmy_test_source", lambda payload: b"test")
    monkeypatch.setattr(
        external,
        "_run_shimmy_suite",
        lambda source: {
            "status": "pass",
            "returncode": 0,
            "exception": None,
            "stdout": "ok",
            "stderr": "",
            "elapsed_ns": 1,
        },
    )
    monkeypatch.setattr(
        external,
        "runtime_provenance",
        lambda: {"source_tree_sha256": "b" * 64},
    )
    calls: list[tuple[str, int, int]] = []

    def stock_runner(game_name: str, *, cycles: int, seed: int) -> StockApiResult:
        calls.append((game_name, cycles, seed))
        return StockApiResult(game_name, cycles, True, None, (), "")

    output = tmp_path / "external.json"
    payload = external.execute_external_baselines(
        tmp_path / "manifest.json",
        tmp_path / "receipt.json",
        output,
        stock_runner=stock_runner,
    )

    assert calls == [("nim", 1000, 0), ("matrix_rps", 1000, 0)]
    assert payload["stock_pettingzoo_api_test"]["case_count"] == 2
    assert payload["stock_pettingzoo_api_test"]["status_counts"] == {"pass": 2}
    assert json.loads(output.read_text(encoding="utf-8"))["classifier_id"] == (
        external.EXTERNAL_BASELINE_CLASSIFIER_ID
    )


def test_shimmy_timeout_output_is_normalized_to_text(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=1,
            output=b"partial stdout\xff",
            stderr=b"partial stderr\xfe",
        )

    monkeypatch.setattr(external.subprocess, "run", timeout)
    monkeypatch.setattr(external, "SHIMMY_SUITE_TIMEOUT_SECONDS", 1)

    result = external._run_shimmy_suite(b"def test_placeholder():\n    pass\n")

    assert result["status"] == "infrastructure"
    assert isinstance(result["stdout"], str)
    assert isinstance(result["stderr"], str)
