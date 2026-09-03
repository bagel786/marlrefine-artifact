"""Archive-gated external baselines for the prospective cohort.

This module is never imported by the primary semantic runner.  Its entry point
first verifies the public protocol archive, downloads and verifies the exact
Shimmy 2.0.1 source-test bytes, then runs PettingZoo's stock ``api_test`` once
per frozen game and the released Shimmy OpenSpiel test module.  The output is
published atomically only after every requested baseline finishes.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from marlrefine.prospective import build_prospective_plan
from marlrefine.provenance import runtime_provenance
from marlrefine.serialization import write_json
from marlrefine.stock_tests import StockApiResult, run_stock_api_test
from marlrefine.study import (
    SHIMMY_OPENSPIEL_TEST_MEMBER,
    SHIMMY_OPENSPIEL_TEST_SHA256,
    SHIMMY_SDIST_SHA256,
    SHIMMY_SDIST_URL,
    SHIMMY_SUITE_TIMEOUT_SECONDS,
    STOCK_API_ACTION_SPACE_SEED,
    STOCK_API_CYCLES,
    external_baseline_protocol,
)

EXTERNAL_BASELINE_SCHEMA_VERSION = 1
EXTERNAL_BASELINE_CLASSIFIER_ID = "marlrefine_external_baselines_v1"
MAX_SHIMMY_SDIST_BYTES = 2 * 1024 * 1024
EXPECTED_EXTERNAL_CASE_COUNT = 105


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _download_shimmy_sdist() -> bytes:
    request = urllib.request.Request(
        SHIMMY_SDIST_URL,
        headers={"User-Agent": "marlrefine-external-baseline/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.geturl() != SHIMMY_SDIST_URL:
                raise RuntimeError("Shimmy sdist download redirected")
            payload = response.read(MAX_SHIMMY_SDIST_BYTES + 1)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError(f"cannot download pinned Shimmy sdist: {exc}") from exc
    if len(payload) > MAX_SHIMMY_SDIST_BYTES:
        raise RuntimeError("pinned Shimmy sdist exceeds the frozen size limit")
    if _sha256_bytes(payload) != SHIMMY_SDIST_SHA256:
        raise RuntimeError("downloaded Shimmy sdist SHA-256 differs from the freeze")
    return payload


def _shimmy_test_source(sdist: bytes) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(sdist), mode="r:gz") as archive:
            member = archive.getmember(SHIMMY_OPENSPIEL_TEST_MEMBER)
            if not member.isfile() or member.size > 1024 * 1024:
                raise RuntimeError("Shimmy OpenSpiel test member is not a small file")
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError("Shimmy OpenSpiel test member cannot be read")
            source = handle.read()
    except (KeyError, OSError, tarfile.TarError) as exc:
        raise RuntimeError(f"cannot read pinned Shimmy OpenSpiel test: {exc}") from exc
    if _sha256_bytes(source) != SHIMMY_OPENSPIEL_TEST_SHA256:
        raise RuntimeError("Shimmy OpenSpiel test SHA-256 differs from the freeze")
    return source


def _suite_status(returncode: int) -> str:
    if returncode == 0:
        return "pass"
    if returncode == 1:
        return "fail"
    return "infrastructure"


def _captured_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_shimmy_suite(source: bytes) -> dict[str, Any]:
    started_ns = perf_counter_ns()
    with tempfile.TemporaryDirectory() as temporary:
        test_path = Path(temporary) / "test_openspiel.py"
        test_path.write_bytes(source)
        command = (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--disable-warnings",
            str(test_path),
        )
        environment = {**os.environ, "PYTHONHASHSEED": "0"}
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=SHIMMY_SUITE_TIMEOUT_SECONDS,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "infrastructure",
                "returncode": None,
                "exception": "TimeoutExpired",
                "stdout": _captured_text(exc.stdout),
                "stderr": _captured_text(exc.stderr),
                "elapsed_ns": perf_counter_ns() - started_ns,
            }
    return {
        "status": _suite_status(completed.returncode),
        "returncode": completed.returncode,
        "exception": None,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsed_ns": perf_counter_ns() - started_ns,
    }


def execute_external_baselines(
    manifest_path: Path,
    receipt_path: Path,
    output_path: Path,
    *,
    stock_runner: Any = run_stock_api_test,
) -> dict[str, Any]:
    """Run the frozen external baselines after public archive verification."""
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {output_path}")
    plan = build_prospective_plan(manifest_path, receipt_path)
    if plan.gate.manifest.get("external_baselines") != external_baseline_protocol():
        raise RuntimeError(
            "archive gate returned a different external-baseline protocol"
        )

    # Verify all upstream executable test bytes before consuming a cohort case.
    sdist = _download_shimmy_sdist()
    shimmy_source = _shimmy_test_source(sdist)

    game_names = tuple(plan.gate.manifest["validation"]["semantic_cohort"]["names"])
    if (
        len(game_names) != EXPECTED_EXTERNAL_CASE_COUNT
        or len(set(game_names)) != EXPECTED_EXTERNAL_CASE_COUNT
    ):
        raise RuntimeError(
            "external baseline requires the exact frozen 105-game semantic cohort"
        )
    started_ns = perf_counter_ns()
    stock_results: list[StockApiResult] = []
    for game_name in game_names:
        result = stock_runner(
            game_name,
            cycles=STOCK_API_CYCLES,
            seed=STOCK_API_ACTION_SPACE_SEED,
        )
        if (
            not isinstance(result, StockApiResult)
            or result.game_spec != game_name
            or result.cycles != STOCK_API_CYCLES
        ):
            raise RuntimeError(
                "stock api_test runner returned a result for a different case "
                "or schedule"
            )
        stock_results.append(result)
    stock_counts = Counter(
        "pass" if result.passed else "fail" for result in stock_results
    )
    suite_result = _run_shimmy_suite(shimmy_source)
    payload = {
        "schema_version": EXTERNAL_BASELINE_SCHEMA_VERSION,
        "artifact_type": "marlrefine_prospective_external_baselines",
        "classifier_id": EXTERNAL_BASELINE_CLASSIFIER_ID,
        "manifest_sha256": plan.gate.manifest_sha256,
        "source_tree_sha256": plan.gate.source_tree_sha256,
        "uv_lock_sha256": plan.gate.uv_lock_sha256,
        "receipt_sha256": plan.gate.receipt_sha256,
        "archive_identifier": plan.gate.archive_identifier,
        "archive_published_at_utc": plan.gate.published_at_utc,
        "runtime": runtime_provenance(),
        "stock_pettingzoo_api_test": {
            "cycles": STOCK_API_CYCLES,
            "action_space_seed": STOCK_API_ACTION_SPACE_SEED,
            "case_count": len(stock_results),
            "status_counts": dict(sorted(stock_counts.items())),
            "results": stock_results,
        },
        "released_shimmy_openspiel_suite": {
            "role": "contextual_upstream_suite_evidence_not_cohort_comparator",
            "sdist_url": SHIMMY_SDIST_URL,
            "sdist_sha256": _sha256_bytes(sdist),
            "test_member": SHIMMY_OPENSPIEL_TEST_MEMBER,
            "test_member_sha256": _sha256_bytes(shimmy_source),
            "pytest_args": ["-q", "--disable-warnings"],
            "pythonhashseed": "0",
            "result_classifier": ("pytest_exit_0_pass_1_fail_else_infrastructure_v1"),
            "limitations": external_baseline_protocol()[
                "released_shimmy_openspiel_suite"
            ]["limitations"],
            "result": suite_result,
        },
        "elapsed_ns": perf_counter_ns() - started_ns,
    }
    write_json(output_path, payload)
    return payload
