from __future__ import annotations

import pytest
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

import marlrefine.adapters.openspiel_shimmy as openspiel_runner
from marlrefine.adapters.openspiel_shimmy import run_trace
from marlrefine.model import Span
from marlrefine.prospective import OutcomeStatus, classify_run_payload


def _codes(game_run) -> set[str]:
    return {violation.code for violation in game_run.violations}


def _baseline(game_run, name: str):
    return next(result for result in game_run.baselines if result.baseline == name)


def _evaluation(game_run, obligation_id: str):
    return next(
        item
        for item in game_run.obligation_evaluations
        if item.obligation_id == obligation_id
    )


def test_source_setup_failure_has_complete_not_evaluated_ledger() -> None:
    run = run_trace("not_a_real_game", seed=0)

    assert tuple(item.obligation_id for item in run.obligation_evaluations) == tuple(
        f"O{index}" for index in range(1, 9)
    )
    assert {
        item.outcome.value for item in run.obligation_evaluations
    } == {"not_evaluated"}
    assert all(not item.finding_indices for item in run.obligation_evaluations)


@pytest.mark.integration
def test_buffered_reward_witness_uses_independent_source() -> None:
    run = run_trace("coop_box_pushing", seed=7, max_source_decisions=10)
    assert run.summary["source_decisions"] == 10
    assert run.summary["destination_calls"] == 20
    assert "nonzero_stutter_reward" in _codes(run)
    assert "segment_reward_mismatch" in _codes(run)
    assert _baseline(run, "strict_lockstep").applicable is False
    assert _baseline(run, "macro_boundary").detected is False


@pytest.mark.integration
def test_bounded_prefix_stops_at_exact_call_limit_without_budget_error() -> None:
    run = run_trace(
        "coop_box_pushing",
        seed=7,
        max_source_decisions=1,
        max_destination_calls=2,
    )
    assert run.summary["stop_reason"] == "source_decision_limit"
    assert "destination_call_budget_exhausted" not in _codes(run)
    assert _codes(run) == {"source_decision_clock_mismatch"}


@pytest.mark.integration
def test_submitted_action_remapping_is_detected(monkeypatch) -> None:
    original = OpenSpielCompatibilityV0._execute_action_node

    def remap_player_zero(self, action):
        if (
            self.game_state.is_simultaneous_node()
            and self.agent_selection == "player_0"
        ):
            legal = tuple(self.game_state.legal_actions(0))
            replacement = next(candidate for candidate in legal if candidate != action)
            return original(self, replacement)
        return original(self, action)

    monkeypatch.setattr(
        OpenSpielCompatibilityV0,
        "_execute_action_node",
        remap_player_zero,
    )
    run = run_trace("matrix_rps", seed=0, max_source_decisions=1)
    assert "submitted_action_mismatch" in _codes(run)


@pytest.mark.integration
def test_silent_agent_disappearance_is_detected() -> None:
    class DisappearingAdapter(OpenSpielCompatibilityV0):
        def step(self, action):
            super().step(action)
            if self.game_state.is_simultaneous_node() and not self.game_state.history():
                self.agents.clear()

    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=DisappearingAdapter,
    )
    assert "unfinished_joint_action_buffer" in _codes(run)
    assert "agents_exhausted_before_source_terminal" in _codes(run)
    assert "consumer_return_mismatch" not in _codes(run)
    assert _baseline(run, "return_only").applicable is False


@pytest.mark.integration
def test_reset_and_final_state_digests_are_distinct_fields() -> None:
    run = run_trace("matrix_rps", seed=0, max_source_decisions=1)
    assert run.summary["post_reset_source_state_digest"]
    assert run.summary["final_source_state_digest"]
    assert (
        run.summary["post_reset_source_state_digest"]
        != run.summary["final_source_state_digest"]
    )
    assert run.summary["chance_tape_sha256"]


@pytest.mark.integration
def test_exact_decision_cap_still_allows_terminal_cleanup() -> None:
    run = run_trace("matrix_rps", seed=0, max_source_decisions=1)

    assert run.summary["source_decisions"] == 1
    assert run.summary["source_terminal"] is True
    assert run.summary["stop_reason"] == "destination_episode_end"
    assert run.summary["adapter_agents_remaining"] == 0
    assert run.summary["destination_calls"] == 4
    assert [event.cleanup for event in run.destination_events] == [
        False,
        False,
        True,
        True,
    ]


@pytest.mark.integration
def test_decision_cap_allows_cleanup_that_exposes_premature_truncation() -> None:
    class PrematureTruncationAdapter(OpenSpielCompatibilityV0):
        def step(self, action):
            super().step(action)
            if self.game_length == 1:
                for agent in self.agents:
                    self.truncations[agent] = True

    run = run_trace(
        "coop_box_pushing",
        seed=7,
        max_source_decisions=1,
        adapter_class=PrematureTruncationAdapter,
    )

    assert run.summary["source_terminal"] is False
    assert run.summary["stop_reason"] == "destination_episode_end"
    assert any(event.cleanup for event in run.destination_events)
    assert "pre_cleanup_lifecycle_mismatch" in _codes(run)


@pytest.mark.integration
def test_adapter_step_failure_is_a_valid_semantic_finding() -> None:
    class FailingStepAdapter(OpenSpielCompatibilityV0):
        def step(self, action):
            del action
            raise RuntimeError("deterministic adapter defect")

    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=FailingStepAdapter,
    )

    finding = next(
        item for item in run.violations if item.code == "adapter_step_failed"
    )
    assert finding.destination_span == Span(0, 0)
    assert run.applicable is True
    assert run.summary["destination_calls"] == 0
    assert classify_run_payload(run.to_dict()) is OutcomeStatus.FAIL


@pytest.mark.integration
def test_adapter_reset_failure_is_a_valid_semantic_finding() -> None:
    class FailingResetAdapter(OpenSpielCompatibilityV0):
        def reset(self, seed=None, options=None):
            del seed, options
            raise RuntimeError("deterministic adapter reset defect")

    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=FailingResetAdapter,
    )

    assert run.applicable is True
    assert _codes(run) == {"adapter_setup_failed"}
    assert _evaluation(run, "O6").outcome.value == "not_applicable"
    assert {
        item.outcome.value
        for item in run.obligation_evaluations
        if item.obligation_id != "O6"
    } == {"not_evaluated"}
    assert classify_run_payload(run.to_dict()) is OutcomeStatus.FAIL


@pytest.mark.integration
def test_reset_state_mismatch_stops_before_first_adapter_call(monkeypatch) -> None:
    calls = 0

    class CountingAdapter(OpenSpielCompatibilityV0):
        def step(self, action):
            nonlocal calls
            calls += 1
            return super().step(action)

    monkeypatch.setattr(openspiel_runner, "_state_equivalent", lambda *_: False)
    run = run_trace(
        "matrix_rps",
        seed=0,
        max_source_decisions=1,
        adapter_class=CountingAdapter,
    )

    assert calls == 0
    assert run.summary["stop_reason"] == "reset_state_mismatch"
    assert run.summary["destination_calls"] == 0
    assert _codes(run) == {"reset_state_mismatch"}
    assert all(
        not item.finding_indices for item in run.obligation_evaluations
    )


@pytest.mark.integration
def test_terminal_cleanup_replays_reward_to_consumer() -> None:
    run = run_trace("nim", seed=0)
    assert run.summary["source_terminal"] is True
    assert run.summary["source_return"] == (1.0, -1.0)
    assert run.summary["destination_delivered_reward_sum"] == (1.0, -2.0)
    assert "nonzero_terminal_cleanup_reward" in _codes(run)
    assert "consumer_return_mismatch" in _codes(run)
    assert "consumer_delivery_mismatch" in _codes(run)
    assert run.summary["delivery_mismatch_count"] > 0
    macro_codes = {
        finding.code for finding in _baseline(run, "macro_boundary").findings
    }
    assert "boundary_reward_mismatch" not in macro_codes
    assert _baseline(run, "return_only").detected is True


@pytest.mark.integration
def test_every_observed_delivery_is_checked_against_native_accumulation() -> None:
    run = run_trace("matrix_rps", seed=0, max_source_decisions=1)

    assert run.summary["delivery_comparison_count"] == len(run.destination_events)
    assert run.summary["delivery_mismatch_count"] == 0
    assert all(
        "native_expected_delivered_reward" in event.metadata
        and event.metadata["delivered_reward_matches_native"] is True
        for event in run.destination_events
    )


@pytest.mark.integration
def test_weak_state_fallback_does_not_award_o1_pass(monkeypatch) -> None:
    original = openspiel_runner._state_identity

    def weak_identity(state):
        digest, _ = original(state)
        return digest, "history_text_fallback"

    monkeypatch.setattr(openspiel_runner, "_state_identity", weak_identity)
    run = run_trace("matrix_rps", seed=0, max_source_decisions=1)
    o1 = _evaluation(run, "O1")

    assert o1.outcome.value == "not_evaluated"
    assert o1.reason_code == "weak_state_identity_only"
    assert all(
        event.metadata["state_oracle_strength"] == "weak_diagnostic_only"
        for event in run.destination_events
    )


@pytest.mark.integration
def test_chance_events_cause_premature_horizon() -> None:
    run = run_trace("coop_box_pushing", seed=0)
    assert run.summary["source_decisions"] == 25
    assert run.summary["source_terminal"] is False
    assert "premature_adapter_truncation" in _codes(run)


@pytest.mark.integration
def test_preconfigured_game_identity_is_not_preserved() -> None:
    run = run_trace("go(board_size=5)", seed=3)
    assert "parameters_changed_on_reset" in _codes(run)
    assert run.summary["source_parameters"]["board_size"] == 5
    assert run.summary["adapter_parameters"]["board_size"] == 19
    assert _evaluation(run, "O6").outcome.value == "evaluated_fail"


@pytest.mark.integration
def test_explicit_default_configuration_does_not_enter_o6_denominator() -> None:
    run = run_trace("go(board_size=19)", seed=3, max_source_decisions=1)

    assert run.summary["caller_supplied_nondefault_configuration"] is False
    assert _evaluation(run, "O6").outcome.value == "not_applicable"


@pytest.mark.integration
def test_mean_field_node_is_silently_terminated() -> None:
    run = run_trace("mfg_crowd_modelling", seed=0)
    assert run.summary["source_node_kind"] == "mean_field"
    assert "mean_field_node_silently_terminated" in _codes(run)
