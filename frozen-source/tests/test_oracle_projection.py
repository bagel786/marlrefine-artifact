from __future__ import annotations

import numpy as np
import pytest
from gymnasium.spaces import Discrete
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

from marlrefine.adapters.openspiel_shimmy import (
    _normalize_action_mask,
    _observation_evidence,
    _observation_mismatch_reason,
    _observation_numeric_residual,
    run_trace,
)
from marlrefine.model import Span


def _codes(run) -> tuple[str, ...]:
    return tuple(violation.code for violation in run.violations)


def test_floating_observations_use_frozen_tolerance_after_shape_and_dtype() -> None:
    expected = np.asarray([1.0, -2.0], dtype=np.float64)

    assert (
        _observation_mismatch_reason(
            expected,
            np.asarray([1.0 + 5e-8, -2.0], dtype=np.float64),
        )
        is None
    )
    assert (
        _observation_mismatch_reason(
            expected,
            np.asarray([1.0 + 5e-4, -2.0], dtype=np.float64),
        )
        == "value"
    )
    assert (
        _observation_mismatch_reason(expected, expected.astype(np.float32)) == "dtype"
    )
    assert _observation_mismatch_reason(expected, [1.0, -2.0]) == "container_type"
    assert (
        _observation_mismatch_reason(
            expected,
            np.asarray([1.0, -2.0, 3.0], dtype=np.float64),
        )
        == "element_count"
    )
    assert _observation_mismatch_reason(expected, expected.reshape((2, 1))) == "shape"


def test_observation_evidence_preserves_raw_signature() -> None:
    evidence = _observation_evidence(
        np.asarray([[1.0, 2.0]], dtype=np.float64)
    )

    assert evidence["container_type"] == "numpy.ndarray"
    assert evidence["element_count"] == 2
    assert evidence["shape"] == (1, 2)
    assert evidence["dtype"] == "float64"


def test_observation_residual_supports_prespecified_tolerance_sensitivity() -> None:
    expected = np.asarray([0.0, 10.0], dtype=np.float64)
    observed = np.asarray([5e-8, 10.0 + 5e-7], dtype=np.float64)

    residual = _observation_numeric_residual(expected, observed)

    assert residual is not None
    assert residual["finite"] is True
    assert 0.0 < residual["primary_tolerance_ratio"] < 1.0
    assert residual["maximum_absolute_difference"] == pytest.approx(5e-7)


def test_discrete_observations_use_exact_values() -> None:
    expected = np.asarray([True, False], dtype=np.bool_)

    assert _observation_mismatch_reason(expected, expected.copy()) is None
    assert (
        _observation_mismatch_reason(
            expected,
            np.asarray([True, True], dtype=np.bool_),
        )
        == "value"
    )


@pytest.mark.parametrize(
    ("info", "reason"),
    [
        ({}, "missing_action_mask"),
        (
            {"action_mask": np.ones((2, 2), dtype=np.int8)},
            "action_mask_not_one_dimensional",
        ),
        ({"action_mask": np.ones(2, dtype=np.int8)}, "action_mask_wrong_length"),
        (
            {"action_mask": np.ones(3, dtype=np.float64)},
            "action_mask_non_integer_dtype",
        ),
        (
            {"action_mask": np.asarray([0, 2, 1], dtype=np.int8)},
            "action_mask_not_binary",
        ),
    ],
)
def test_action_mask_validation_is_stable(info: object, reason: str) -> None:
    actions, observed_reason = _normalize_action_mask(info, action_count=3)

    assert actions is None
    assert observed_reason == reason


def test_action_mask_normalizes_to_an_ordered_action_set() -> None:
    actions, reason = _normalize_action_mask(
        {"action_mask": np.asarray([1, 0, 1, 0], dtype=np.int8)},
        action_count=4,
    )

    assert reason is None
    assert actions == (0, 2)


@pytest.mark.integration
def test_missing_public_action_mask_is_o8_finding_not_execution_crash() -> None:
    class MissingPublicMaskAdapter(OpenSpielCompatibilityV0):
        def last(self, observe: bool = True):
            observation, reward, termination, truncation, _info = super().last(
                observe=observe
            )
            return observation, reward, termination, truncation, {}

    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=MissingPublicMaskAdapter,
    )

    assert "action_mask_missing" in _codes(run)
    assert "adapter_step_failed" not in _codes(run)
    assert run.summary["stop_reason"] == "interface_projection_mismatch"
    assert run.summary["destination_calls"] == 0
    finding = next(
        item for item in run.violations if item.code == "action_mask_missing"
    )
    assert finding.destination_span == Span(0, 0)


@pytest.mark.integration
def test_malformed_public_action_mask_is_o8_finding_not_execution_crash() -> None:
    class MalformedPublicMaskAdapter(OpenSpielCompatibilityV0):
        def last(self, observe: bool = True):
            observation, reward, termination, truncation, _info = super().last(
                observe=observe
            )
            return (
                observation,
                reward,
                termination,
                truncation,
                {"action_mask": object()},
            )

    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=MalformedPublicMaskAdapter,
    )

    assert "action_mask_malformed" in _codes(run)
    assert "adapter_step_failed" not in _codes(run)
    assert run.summary["stop_reason"] == "interface_projection_mismatch"
    assert run.summary["destination_calls"] == 0
    assert run.to_dict()["violations"]
    finding = next(
        item for item in run.violations if item.code == "action_mask_malformed"
    )
    assert finding.destination_span == Span(0, 0)


@pytest.mark.integration
def test_legal_action_set_mismatch_stops_before_submitting_an_action() -> None:
    class MissingLegalActionAdapter(OpenSpielCompatibilityV0):
        def last(self, observe: bool = True):
            observation, reward, termination, truncation, _info = super().last(
                observe=observe
            )
            return (
                observation,
                reward,
                termination,
                truncation,
                {"action_mask": np.asarray([0, 1, 1], dtype=np.int8)},
            )

    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=MissingLegalActionAdapter,
    )

    mismatches = [
        violation
        for violation in run.violations
        if violation.code == "legal_action_mismatch"
    ]
    assert len(mismatches) == 1
    assert mismatches[0].expected == (0, 1, 2)
    assert mismatches[0].observed == (1, 2)
    assert run.summary["destination_calls"] == 0
    assert mismatches[0].destination_span == Span(0, 0)


@pytest.mark.integration
def test_declared_action_space_length_is_checked_before_mask() -> None:
    class WrongActionSpaceAdapter(OpenSpielCompatibilityV0):
        def reset(self, seed=None, options=None):
            result = super().reset(seed=seed, options=options)
            self.action_spaces["player_0"] = Discrete(4)
            return result

    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=WrongActionSpaceAdapter,
    )

    finding = next(
        item for item in run.violations if item.code == "action_space_size_mismatch"
    )
    assert finding.expected == 3
    assert finding.observed == 4
    assert finding.destination_span == Span(0, 0)
    assert run.summary["destination_calls"] == 0


@pytest.mark.integration
def test_observation_dtype_mismatch_is_reported_under_o8() -> None:
    class Float32PublicObservationAdapter(OpenSpielCompatibilityV0):
        def last(self, observe: bool = True):
            observation, reward, termination, truncation, info = super().last(
                observe=observe
            )
            if observation is not None:
                observation = np.asarray(observation, dtype=np.float32)
            return observation, reward, termination, truncation, info

    run = run_trace(
        "tic_tac_toe",
        seed=0,
        max_source_decisions=1,
        adapter_class=Float32PublicObservationAdapter,
    )

    mismatches = [
        violation
        for violation in run.violations
        if violation.code == "observation_mismatch"
    ]
    assert len(mismatches) == 1
    assert mismatches[0].observed["mismatch"] == "dtype"


@pytest.mark.integration
def test_observation_container_type_is_checked_before_array_coercion() -> None:
    class ListPublicObservationAdapter(OpenSpielCompatibilityV0):
        def last(self, observe: bool = True):
            observation, reward, termination, truncation, info = super().last(
                observe=observe
            )
            if observation is not None:
                observation = np.asarray(observation).tolist()
            return observation, reward, termination, truncation, info

    run = run_trace(
        "tic_tac_toe",
        seed=0,
        max_source_decisions=1,
        adapter_class=ListPublicObservationAdapter,
    )

    finding = next(
        item for item in run.violations if item.code == "observation_mismatch"
    )
    assert finding.observed["mismatch"] == "container_type"
    assert finding.observed["container_type"] == "builtins.list"


@pytest.mark.integration
def test_elapsed_adapter_clock_is_compared_to_source_decisions_once() -> None:
    run = run_trace("coop_box_pushing", seed=0, max_source_decisions=2)

    clock_findings = [
        violation
        for violation in run.violations
        if violation.code == "source_decision_clock_mismatch"
    ]
    assert len(clock_findings) == 1
    assert clock_findings[0].expected == {"source_decisions": 1}
    assert clock_findings[0].observed["adapter_elapsed_game_length"] > 1
    assert run.summary["decision_clock_mismatch_reported"] is True
    assert run.summary["adapter_decision_clock_elapsed_final"] > 2


@pytest.mark.integration
def test_clock_origin_normalization_keeps_buffer_calls_neutral() -> None:
    run = run_trace("matrix_rps", seed=0, max_source_decisions=1)

    assert "source_decision_clock_mismatch" not in _codes(run)
    assert [
        event.metadata["adapter_decision_clock_elapsed"]
        for event in run.destination_events
    ] == [0, 1, 1, 1]
    assert [
        event.metadata["source_decision_count_after"]
        for event in run.destination_events
    ] == [0, 1, 1, 1]
    assert run.summary["decision_clock_mismatch_reported"] is False
    assert run.summary["adapter_decision_clock_elapsed_final"] == 1
