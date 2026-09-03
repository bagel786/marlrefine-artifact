from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from experiments.run_discovery_policy_matrix import (
    DEFAULT_MAX_DESTINATION_CALLS,
    DEFAULT_MAX_SOURCE_DECISIONS,
    DiscoveryScopeError,
    build_discovery_policy_matrix,
)
from marlrefine.policies import TRACE_POLICY_NAMES
from marlrefine.study import DISCOVERY_GAME_NAMES


@dataclass(frozen=True)
class SyntheticRun:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload


def _synthetic_runner(game_name: str, **kwargs: Any) -> SyntheticRun:
    policy = kwargs["trace_policy"]
    failing = policy.name == "smallest_legal"
    violations = (
        [
            {
                "obligation": "synthetic_obligation",
                "code": "synthetic_finding",
                "message": "synthetic discovery-only fixture",
            }
        ]
        if failing
        else []
    )
    return SyntheticRun(
        {
            "game_spec": game_name,
            "seed": kwargs["seed"],
            "applicable": True,
            "violations": violations,
            "baselines": [
                {
                    "baseline": "synthetic_baseline",
                    "applicable": True,
                    "findings": violations,
                    "reason": None,
                }
            ],
            "summary": {"setup_status": "pass"},
        }
    )


def test_rejects_mixed_scope_before_runner_call() -> None:
    called = False

    def forbidden_runner(*args: Any, **kwargs: Any) -> SyntheticRun:
        nonlocal called
        called = True
        raise AssertionError("runner must not be called")

    with pytest.raises(DiscoveryScopeError, match="outside the discovery-only"):
        build_discovery_policy_matrix(
            selected_names=(DISCOVERY_GAME_NAMES[0], "non_discovery_sentinel"),
            runner=forbidden_runner,
        )

    assert called is False


def test_runs_exact_seven_by_eight_matrix_with_default_caps(monkeypatch) -> None:
    monkeypatch.setattr(
        "experiments.run_discovery_policy_matrix.runtime_provenance",
        lambda: {
            "source_identity_scope": "synthetic",
            "source_tree_sha256": "a" * 64,
            "uv_lock_sha256": "b" * 64,
            "git_revision": "c" * 40,
            "git_dirty": False,
        },
    )
    monkeypatch.setattr(
        "experiments.run_discovery_policy_matrix.project_file_identity",
        lambda path: {"path": path, "sha256": "d" * 64},
    )
    calls: list[tuple[str, str, int, int, int]] = []

    def recording_runner(game_name: str, **kwargs: Any) -> SyntheticRun:
        calls.append(
            (
                game_name,
                kwargs["trace_policy"].name,
                kwargs["seed"],
                kwargs["max_source_decisions"],
                kwargs["max_destination_calls"],
            )
        )
        return _synthetic_runner(game_name, **kwargs)

    payload = build_discovery_policy_matrix(runner=recording_runner)

    assert len(calls) == 7 * 8
    assert tuple(dict.fromkeys(call[0] for call in calls)) == DISCOVERY_GAME_NAMES
    assert tuple(call[1] for call in calls[:8]) == TRACE_POLICY_NAMES
    assert all(call[3] == DEFAULT_MAX_SOURCE_DECISIONS for call in calls)
    assert all(call[4] == DEFAULT_MAX_DESTINATION_CALLS for call in calls)
    assert payload["configuration"]["expected_case_count"] == 56
    assert payload["aggregate"]["case_count"] == 56
    assert payload["aggregate"]["status_counts"] == {
        "pass": 49,
        "fail": 7,
        "inapplicable": 0,
        "infrastructure": 0,
        "unalignable": 0,
    }
    assert payload["aggregate"]["finding_codes"]["synthetic_finding"] == {
        "occurrence_count": 7,
        "trace_count": 7,
        "distinct_game_count": 7,
    }
    assert payload["aggregate"]["raw_baseline_signals"]["baselines"] == {
        "synthetic_baseline": {
            "record_count": 56,
            "applicable_trace_count": 56,
            "signal_trace_count": 7,
            "finding_occurrence_count": 7,
        }
    }
    assert payload["source_identity"]["source_tree_sha256"] == "a" * 64


def test_runner_exception_is_recorded_without_broadening_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "experiments.run_discovery_policy_matrix.runtime_provenance",
        lambda: {},
    )
    monkeypatch.setattr(
        "experiments.run_discovery_policy_matrix.project_file_identity",
        lambda path: {"path": path, "sha256": None},
    )

    def broken_runner(game_name: str, **kwargs: Any) -> SyntheticRun:
        del game_name, kwargs
        raise RuntimeError("synthetic worker failure")

    payload = build_discovery_policy_matrix(
        selected_names=(DISCOVERY_GAME_NAMES[0],),
        runner=broken_runner,
    )

    assert payload["aggregate"]["case_count"] == 8
    assert payload["aggregate"]["status_counts"]["infrastructure"] == 8
    assert payload["aggregate"]["runner_exception_counts"] == {"RuntimeError": 8}
    assert all(record["run"] is None for record in payload["records"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_source_decisions", 0),
        ("max_source_decisions", True),
        ("max_destination_calls", -1),
    ],
)
def test_invalid_caps_fail_before_runner_call(field: str, value: Any) -> None:
    called = False

    def forbidden_runner(*args: Any, **kwargs: Any) -> SyntheticRun:
        nonlocal called
        called = True
        raise AssertionError("runner must not be called")

    kwargs = {field: value, "runner": forbidden_runner}
    with pytest.raises(ValueError, match="positive integer"):
        build_discovery_policy_matrix(**kwargs)
    assert called is False
