"""Clean semantic-preserving controls for the discovery phase.

The controls exercise the same trace alignment and obligation layer used by the
adapter study, but place known semantic-preserving systems on the destination
side.  They are deliberately guarded to the seven-name discovery set (or to a
fully synthetic PettingZoo fixture) so importing or running this module cannot
consume a prospective semantic case.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyspiel
from gymnasium.spaces import Discrete
from pettingzoo import ParallelEnv
from pettingzoo.utils.conversions import parallel_to_aec

from marlrefine.alignment import align_traces
from marlrefine.model import Alignment, DestinationEvent, SourceEvent, Span, Violation
from marlrefine.obligations import (
    BOUNDARY_LIFECYCLE_PRESERVATION,
    PROGRESS_COMPLETENESS,
    SEGMENT_REWARD_CONSERVATION,
    STUTTER_REWARD_NEUTRALITY,
    TERMINAL_CLEANUP_REWARD_NEUTRALITY,
    check_all,
)
from marlrefine.serialization import to_jsonable
from marlrefine.study import DISCOVERY_GAME_NAMES

CONTROL_CONFORMANCE = "semantic_control_conformance"
CONFIGURATION_PROVENANCE = "configuration_provenance"
INTERFACE_PROJECTION = "interface_projection"
STATE_PROJECTION = "state_projection"
DELIVERED_REWARD_CONSERVATION = "delivered_reward_conservation"


@dataclass(frozen=True, slots=True)
class ControlRun:
    """Evidence from one clean semantic-preserving control."""

    control_id: str
    control_system: str
    population_role: str
    alignment: Alignment
    violations: tuple[Violation, ...]
    validation_scope: dict[str, str]
    summary: dict[str, Any]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


def _base_game_name(game_spec: str) -> str:
    return game_spec.partition("(")[0].strip()


def _require_discovery_game(game_spec: str) -> None:
    """Reject a game spec before OpenSpiel sees any non-discovery name."""
    name = _base_game_name(game_spec)
    if name not in DISCOVERY_GAME_NAMES:
        raise ValueError(
            "semantic controls may inspect only predeclared discovery games; "
            f"received {name!r}"
        )


def _state_digest(state: pyspiel.State) -> str:
    try:
        payload = state.serialize()
    except Exception:
        payload = f"{tuple(state.history())}\n{state}"
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _reward_delta(
    before: tuple[float, ...], after: tuple[float, ...]
) -> tuple[float, ...]:
    return tuple(right - left for left, right in zip(before, after, strict=True))


def _control_violation(
    *,
    obligation: str,
    code: str,
    message: str,
    expected: object,
    observed: object,
    destination_index: int | None = None,
) -> Violation:
    destination_span = (
        None
        if destination_index is None
        else Span(destination_index, destination_index + 1)
    )
    return Violation(
        obligation=obligation,
        code=code,
        message=message,
        destination_span=destination_span,
        expected=expected,
        observed=observed,
    )


def run_native_clone_control(
    game_spec: str = "tic_tac_toe",
    *,
    max_decisions: int = 32,
) -> ControlRun:
    """Replay one deterministic path through two independent native loads.

    This is a clean 1:1 control for the independent-source oracle.  It checks
    configuration, acting player, legal actions, applied history, serialized
    state, rewards, and lifecycle at every captured boundary.
    """
    _require_discovery_game(game_spec)
    if max_decisions <= 0:
        raise ValueError("max_decisions must be positive")

    source_game = pyspiel.load_game(game_spec)
    replay_game = pyspiel.load_game(game_spec)
    source_state = source_game.new_initial_state()
    replay_state = replay_game.new_initial_state()
    source_events: list[SourceEvent] = []
    destination_events: list[DestinationEvent] = []
    violations: list[Violation] = []

    source_configuration = (
        source_game.get_type().short_name,
        tuple(
            sorted((str(k), str(v)) for k, v in source_game.get_parameters().items())
        ),
    )
    replay_configuration = (
        replay_game.get_type().short_name,
        tuple(
            sorted((str(k), str(v)) for k, v in replay_game.get_parameters().items())
        ),
    )
    if source_configuration != replay_configuration:
        violations.append(
            _control_violation(
                obligation=CONFIGURATION_PROVENANCE,
                code="native_clone_configuration_mismatch",
                message="independent native loads resolved to different games",
                expected=source_configuration,
                observed=replay_configuration,
            )
        )

    progress = 0
    while not source_state.is_terminal() and progress < max_decisions:
        destination_index = len(destination_events)
        if source_state.is_chance_node() or source_state.is_simultaneous_node():
            violations.append(
                _control_violation(
                    obligation=CONTROL_CONFORMANCE,
                    code="native_clone_unsupported_node",
                    message=(
                        "this clean clone control requires a deterministic "
                        "sequential discovery path"
                    ),
                    expected="sequential player node",
                    observed={
                        "chance": source_state.is_chance_node(),
                        "simultaneous": source_state.is_simultaneous_node(),
                    },
                    destination_index=destination_index,
                )
            )
            break

        source_player = int(source_state.current_player())
        replay_player = int(replay_state.current_player())
        source_legal = tuple(int(action) for action in source_state.legal_actions())
        replay_legal = tuple(int(action) for action in replay_state.legal_actions())
        if (source_player, source_legal) != (replay_player, replay_legal):
            violations.append(
                _control_violation(
                    obligation=INTERFACE_PROJECTION,
                    code="native_clone_interface_mismatch",
                    message=(
                        "independent native states disagree on acting player or "
                        "legal actions"
                    ),
                    expected=(source_player, source_legal),
                    observed=(replay_player, replay_legal),
                    destination_index=destination_index,
                )
            )
            break
        if not source_legal:
            violations.append(
                _control_violation(
                    obligation=INTERFACE_PROJECTION,
                    code="native_clone_live_state_without_action",
                    message="a live native state exposed no legal action",
                    expected="one or more legal actions",
                    observed=source_legal,
                    destination_index=destination_index,
                )
            )
            break

        action = source_legal[0]
        source_returns_before = tuple(float(value) for value in source_state.returns())
        replay_returns_before = tuple(float(value) for value in replay_state.returns())
        source_state.apply_action(action)
        replay_state.apply_action(action)
        progress += 1
        source_returns = tuple(float(value) for value in source_state.returns())
        replay_returns = tuple(float(value) for value in replay_state.returns())
        source_reward = _reward_delta(source_returns_before, source_returns)
        replay_reward = _reward_delta(replay_returns_before, replay_returns)

        source_events.append(
            SourceEvent(
                progress=progress,
                rewards=source_reward,
                terminated=source_state.is_terminal(),
                metadata={
                    "action": action,
                    "player": source_player,
                    "history_after": tuple(source_state.history()),
                    "state_digest_after": _state_digest(source_state),
                },
            )
        )
        destination_events.append(
            DestinationEvent(
                source_progress=progress,
                rewards=replay_reward,
                terminated=replay_state.is_terminal(),
                metadata={
                    "action": action,
                    "player": replay_player,
                    "history_after": tuple(replay_state.history()),
                    "state_digest_after": _state_digest(replay_state),
                },
            )
        )

        source_projection = (
            tuple(source_state.history()),
            int(source_state.current_player()),
            source_state.is_terminal(),
            source_returns,
            _state_digest(source_state),
        )
        replay_projection = (
            tuple(replay_state.history()),
            int(replay_state.current_player()),
            replay_state.is_terminal(),
            replay_returns,
            _state_digest(replay_state),
        )
        if source_projection != replay_projection:
            violations.append(
                _control_violation(
                    obligation=STATE_PROJECTION,
                    code="native_clone_state_mismatch",
                    message="independent native replay diverged at a boundary",
                    expected=source_projection,
                    observed=replay_projection,
                    destination_index=destination_index,
                )
            )
            break

    alignment = align_traces(source_events, destination_events)
    violations[:0] = check_all(alignment)
    return ControlRun(
        control_id="native_clone_replay_v1",
        control_system="two independent pyspiel.load_game instances",
        population_role="discovery_only",
        alignment=alignment,
        violations=tuple(violations),
        validation_scope={
            CONFIGURATION_PROVENANCE: "resolved game name and parameter equality",
            INTERFACE_PROJECTION: "acting-player and legal-action equality",
            STATE_PROJECTION: (
                "history, current player, terminality, returns, and serialized "
                "state equality at each 1:1 boundary"
            ),
            PROGRESS_COMPLETENESS: "1:1 progress and complete prefix coverage",
            SEGMENT_REWARD_CONSERVATION: "per-transition return-delta equality",
            BOUNDARY_LIFECYCLE_PRESERVATION: "per-boundary terminality equality",
        },
        summary={
            "game_spec": game_spec,
            "source_load_identity": source_configuration,
            "replay_load_identity": replay_configuration,
            "source_transitions": len(source_events),
            "destination_calls": len(destination_events),
            "source_terminal": source_state.is_terminal(),
            "replay_terminal": replay_state.is_terminal(),
            "bounded": not source_state.is_terminal(),
            "violation_count": len(violations),
        },
    )


def run_turn_based_simultaneous_control(
    game_spec: str = "matrix_rps",
    *,
    max_joint_decisions: int = 8,
) -> ControlRun:
    """Compare a simultaneous game with OpenSpiel's official turn-based view."""
    _require_discovery_game(game_spec)
    if max_joint_decisions <= 0:
        raise ValueError("max_joint_decisions must be positive")

    source_game = pyspiel.load_game(game_spec)
    transformed_game = pyspiel.convert_to_turn_based(pyspiel.load_game(game_spec))
    source_state = source_game.new_initial_state()
    transformed_state = transformed_game.new_initial_state()
    source_events: list[SourceEvent] = []
    destination_events: list[DestinationEvent] = []
    violations: list[Violation] = []
    progress = 0

    if not source_state.is_simultaneous_node():
        violations.append(
            _control_violation(
                obligation=CONTROL_CONFORMANCE,
                code="source_not_simultaneous",
                message="turn-based control requires a simultaneous discovery game",
                expected=True,
                observed=False,
            )
        )

    while (
        not violations
        and not source_state.is_terminal()
        and progress < max_joint_decisions
    ):
        if not source_state.is_simultaneous_node():
            violations.append(
                _control_violation(
                    obligation=CONTROL_CONFORMANCE,
                    code="unsupported_intermediate_source_node",
                    message=(
                        "the bounded control currently covers simultaneous-to-"
                        "sequential decision expansion without chance nodes"
                    ),
                    expected="simultaneous node",
                    observed=int(source_state.current_player()),
                )
            )
            break

        joint_action: list[int] = []
        players = tuple(
            player
            for player in range(source_game.num_players())
            if source_state.legal_actions(player)
        )
        source_returns_before = tuple(float(value) for value in source_state.returns())
        for player_offset, player in enumerate(players):
            destination_index = len(destination_events)
            source_legal = tuple(
                int(action) for action in source_state.legal_actions(player)
            )
            transformed_player = int(transformed_state.current_player())
            transformed_legal = tuple(
                int(action) for action in transformed_state.legal_actions()
            )
            if (player, source_legal) != (transformed_player, transformed_legal):
                violations.append(
                    _control_violation(
                        obligation=INTERFACE_PROJECTION,
                        code="turn_based_interface_mismatch",
                        message=(
                            "official transform disagrees on acting player or "
                            "legal actions"
                        ),
                        expected=(player, source_legal),
                        observed=(transformed_player, transformed_legal),
                        destination_index=destination_index,
                    )
                )
                break

            # Exercise a nonzero payoff in matrix_rps while remaining generic.
            action_index = (progress + player * max(len(source_legal) - 1, 0)) % len(
                source_legal
            )
            action = source_legal[action_index]
            joint_action.append(action)
            transformed_returns_before = tuple(
                float(value) for value in transformed_state.returns()
            )
            transformed_state.apply_action(action)
            commits_joint_action = player_offset == len(players) - 1

            if commits_joint_action:
                source_state.apply_actions(joint_action)
                progress += 1
                source_returns = tuple(float(value) for value in source_state.returns())
                source_reward = _reward_delta(
                    source_returns_before,
                    source_returns,
                )
                source_events.append(
                    SourceEvent(
                        progress=progress,
                        rewards=source_reward,
                        terminated=source_state.is_terminal(),
                        metadata={
                            "joint_action": tuple(joint_action),
                            "history_after": tuple(source_state.history()),
                        },
                    )
                )

            transformed_returns = tuple(
                float(value) for value in transformed_state.returns()
            )
            transformed_reward = _reward_delta(
                transformed_returns_before,
                transformed_returns,
            )
            destination_events.append(
                DestinationEvent(
                    source_progress=progress,
                    rewards=transformed_reward,
                    terminated=transformed_state.is_terminal(),
                    metadata={
                        "action": action,
                        "player": player,
                        "commits_joint_action": commits_joint_action,
                        "history_after": tuple(transformed_state.history()),
                    },
                )
            )

            if commits_joint_action:
                source_projection = (
                    tuple(source_state.history()),
                    source_state.is_terminal(),
                    tuple(float(value) for value in source_state.returns()),
                )
                transformed_projection = (
                    tuple(transformed_state.history()),
                    transformed_state.is_terminal(),
                    tuple(float(value) for value in transformed_state.returns()),
                )
                if source_projection != transformed_projection:
                    violations.append(
                        _control_violation(
                            obligation=STATE_PROJECTION,
                            code="turn_based_boundary_mismatch",
                            message=(
                                "official transform changed the simultaneous "
                                "macro-boundary projection"
                            ),
                            expected=source_projection,
                            observed=transformed_projection,
                            destination_index=destination_index,
                        )
                    )
                    break

    alignment = align_traces(source_events, destination_events)
    violations[:0] = check_all(alignment)
    return ControlRun(
        control_id="openspiel_turn_based_simultaneous_v1",
        control_system="pyspiel.convert_to_turn_based",
        population_role="discovery_only",
        alignment=alignment,
        violations=tuple(violations),
        validation_scope={
            INTERFACE_PROJECTION: "per-player schedule and legal-action equality",
            STATE_PROJECTION: "history, terminality, and returns at macro boundaries",
            STUTTER_REWARD_NEUTRALITY: (
                "non-final turn-based actions buffer without reward"
            ),
            SEGMENT_REWARD_CONSERVATION: (
                "one simultaneous reward equals the expanded segment reward"
            ),
            PROGRESS_COMPLETENESS: "one progress increment per joint action",
            BOUNDARY_LIFECYCLE_PRESERVATION: (
                "transformed commit terminality equals simultaneous terminality"
            ),
        },
        summary={
            "game_spec": game_spec,
            "source_dynamics": str(source_game.get_type().dynamics),
            "transformed_dynamics": str(transformed_game.get_type().dynamics),
            "source_transitions": len(source_events),
            "destination_calls": len(destination_events),
            "stutter_calls": len(destination_events) - len(source_events),
            "source_terminal": source_state.is_terminal(),
            "transformed_terminal": transformed_state.is_terminal(),
            "bounded": not source_state.is_terminal(),
            "violation_count": len(violations),
        },
    )


class _DeterministicParallelFixture(ParallelEnv[str, np.ndarray, int]):
    """Two-agent additive fixture used only as a canonical conversion control."""

    metadata = {"name": "marlrefine_parallel_control_v1"}
    possible_agents = ["player_0", "player_1"]
    render_mode = None

    def __init__(self, *, horizon: int = 2) -> None:
        if horizon <= 0:
            raise ValueError("horizon must be positive")
        self.horizon = horizon
        self.agents: list[str] = []
        self.transition_count = 0
        self.action_history: list[tuple[tuple[str, int], ...]] = []
        self.return_vector = (0.0, 0.0)
        self._action_space = Discrete(2)
        self._observation_space = Discrete(horizon + 1)

    def observation_space(self, agent: str) -> Discrete:
        if agent not in self.possible_agents:
            raise KeyError(agent)
        return self._observation_space

    def action_space(self, agent: str) -> Discrete:
        if agent not in self.possible_agents:
            raise KeyError(agent)
        return self._action_space

    def _observations(self) -> dict[str, np.ndarray]:
        return {
            agent: np.asarray(self.transition_count, dtype=np.int64)
            for agent in self.possible_agents
        }

    def reset(
        self,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, np.ndarray], dict[str, dict[str, object]]]:
        del seed, options
        self.agents = self.possible_agents[:]
        self.transition_count = 0
        self.action_history = []
        self.return_vector = (0.0, 0.0)
        return self._observations(), {agent: {} for agent in self.agents}

    def step(
        self, actions: dict[str, int]
    ) -> tuple[
        dict[str, np.ndarray],
        dict[str, float],
        dict[str, bool],
        dict[str, bool],
        dict[str, dict[str, object]],
    ]:
        expected_agents = tuple(self.agents)
        if tuple(actions) != expected_agents:
            raise ValueError(
                f"expected actions for {expected_agents}, received {tuple(actions)}"
            )
        if not all(self._action_space.contains(action) for action in actions.values()):
            raise ValueError("fixture received an illegal action")

        canonical_actions = tuple((agent, int(actions[agent])) for agent in self.agents)
        self.action_history.append(canonical_actions)
        self.transition_count += 1
        direction = 1.0 if actions["player_0"] != actions["player_1"] else -0.5
        rewards = {"player_0": direction, "player_1": -direction}
        self.return_vector = tuple(
            old + rewards[agent]
            for old, agent in zip(
                self.return_vector,
                self.possible_agents,
                strict=True,
            )
        )
        done = self.transition_count >= self.horizon
        terminations = {agent: done for agent in expected_agents}
        truncations = {agent: False for agent in expected_agents}
        infos = {agent: {} for agent in expected_agents}
        observations = self._observations()
        self.agents = [] if done else self.possible_agents[:]
        return observations, rewards, terminations, truncations, infos


def _mapping_vector(
    values: dict[str, float | int], agents: tuple[str, ...]
) -> tuple[float, ...]:
    return tuple(float(values.get(agent, 0.0)) for agent in agents)


def run_parallel_to_aec_control(*, horizon: int = 2) -> ControlRun:
    """Compare a synthetic ParallelEnv with PettingZoo's canonical AEC view."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")

    source = _DeterministicParallelFixture(horizon=horizon)
    transformed_source = _DeterministicParallelFixture(horizon=horizon)
    aec = parallel_to_aec(transformed_source)
    source_observations, _ = source.reset(seed=0)
    aec.reset(seed=0)
    agents = tuple(source.possible_agents)
    pending_actions: dict[str, int] = {}
    source_events: list[SourceEvent] = []
    destination_events: list[DestinationEvent] = []
    violations: list[Violation] = []
    delivered_sum = [0.0] * len(agents)
    progress = 0

    while aec.agents:
        destination_index = len(destination_events)
        agent = str(aec.agent_selection)
        observation, delivered, terminated_before, truncated_before, _ = aec.last()
        cleanup = bool(terminated_before or truncated_before)
        player = agents.index(agent)
        delivered_vector = [0.0] * len(agents)
        delivered_vector[player] = float(delivered)
        delivered_sum[player] += float(delivered)

        if not cleanup and not np.array_equal(observation, source_observations[agent]):
            violations.append(
                _control_violation(
                    obligation=INTERFACE_PROJECTION,
                    code="parallel_to_aec_observation_mismatch",
                    message=(
                        "canonical AEC observation differs from the independent "
                        "parallel source"
                    ),
                    expected=source_observations[agent],
                    observed=observation,
                    destination_index=destination_index,
                )
            )

        action: int | None
        if cleanup:
            action = None
        else:
            expected_agent = source.agents[len(pending_actions)]
            if agent != expected_agent:
                violations.append(
                    _control_violation(
                        obligation=INTERFACE_PROJECTION,
                        code="parallel_to_aec_schedule_mismatch",
                        message=(
                            "canonical AEC order differs from the parallel agent order"
                        ),
                        expected=expected_agent,
                        observed=agent,
                        destination_index=destination_index,
                    )
                )
            action = (progress + player) % 2
            pending_actions[agent] = action

        source_step_result = None
        commits_joint_action = not cleanup and len(pending_actions) == len(
            source.agents
        )
        if commits_joint_action:
            source_step_result = source.step(dict(pending_actions))
            source_observations, source_rewards, source_terms, source_truncs, _ = (
                source_step_result
            )

        aec.step(action)
        if transformed_source.transition_count > progress:
            if not commits_joint_action or source_step_result is None:
                violations.append(
                    _control_violation(
                        obligation=STATE_PROJECTION,
                        code="parallel_to_aec_unexpected_commit",
                        message=(
                            "canonical conversion advanced without a complete "
                            "independent joint action"
                        ),
                        expected=progress,
                        observed=transformed_source.transition_count,
                        destination_index=destination_index,
                    )
                )
            else:
                progress += 1
                source_terminated = bool(source_terms) and all(source_terms.values())
                source_truncated = bool(source_truncs) and all(source_truncs.values())
                source_events.append(
                    SourceEvent(
                        progress=progress,
                        rewards=_mapping_vector(source_rewards, agents),
                        terminated=source_terminated,
                        truncated=source_truncated,
                        metadata={
                            "joint_action": tuple(pending_actions.items()),
                            "transition_count_after": source.transition_count,
                            "return_after": source.return_vector,
                        },
                    )
                )
                pending_actions.clear()

                source_projection = (
                    source.transition_count,
                    tuple(source.action_history),
                    source.return_vector,
                    tuple(source.agents),
                )
                transformed_projection = (
                    transformed_source.transition_count,
                    tuple(transformed_source.action_history),
                    transformed_source.return_vector,
                    tuple(transformed_source.agents),
                )
                if source_projection != transformed_projection:
                    violations.append(
                        _control_violation(
                            obligation=STATE_PROJECTION,
                            code="parallel_to_aec_boundary_mismatch",
                            message=(
                                "canonical AEC conversion changed the parallel "
                                "macro-boundary state"
                            ),
                            expected=source_projection,
                            observed=transformed_projection,
                            destination_index=destination_index,
                        )
                    )

        active_terminations = tuple(bool(value) for value in aec.terminations.values())
        active_truncations = tuple(bool(value) for value in aec.truncations.values())
        destination_terminated = (
            bool(active_terminations) and all(active_terminations)
        ) or (cleanup and bool(terminated_before))
        destination_truncated = (
            bool(active_truncations) and all(active_truncations)
        ) or (cleanup and bool(truncated_before))
        destination_events.append(
            DestinationEvent(
                source_progress=progress,
                rewards=_mapping_vector(aec.rewards, agents),
                delivered_rewards=tuple(delivered_vector),
                terminated=destination_terminated,
                truncated=destination_truncated,
                cleanup=cleanup,
                metadata={
                    "agent": agent,
                    "action": action,
                    "commits_joint_action": commits_joint_action,
                    "parallel_transition_count_after": (
                        transformed_source.transition_count
                    ),
                },
            )
        )

    alignment = align_traces(source_events, destination_events)
    violations[:0] = check_all(alignment)
    source_return = tuple(source.return_vector)
    delivered_return = tuple(delivered_sum)
    if source_return != delivered_return:
        violations.append(
            _control_violation(
                obligation=DELIVERED_REWARD_CONSERVATION,
                code="parallel_to_aec_consumer_return_mismatch",
                message=(
                    "consumer-delivered AEC return differs from the independent "
                    "parallel return"
                ),
                expected=source_return,
                observed=delivered_return,
            )
        )

    return ControlRun(
        control_id="pettingzoo_parallel_to_aec_v1",
        control_system="pettingzoo.utils.conversions.parallel_to_aec",
        population_role="synthetic_clean_control",
        alignment=alignment,
        violations=tuple(violations),
        validation_scope={
            INTERFACE_PROJECTION: "agent order, observations, and action buffering",
            STATE_PROJECTION: (
                "joint-action history, transition count, returns, and live agents "
                "at macro boundaries"
            ),
            STUTTER_REWARD_NEUTRALITY: "pre-commit AEC calls emit no new reward",
            SEGMENT_REWARD_CONSERVATION: (
                "parallel rewards equal each expanded AEC segment"
            ),
            PROGRESS_COMPLETENESS: "one progress increment per ParallelEnv.step",
            BOUNDARY_LIFECYCLE_PRESERVATION: (
                "AEC commit lifecycle equals parallel lifecycle"
            ),
            TERMINAL_CLEANUP_REWARD_NEUTRALITY: (
                "dead-agent cleanup emits no new instantaneous reward"
            ),
            DELIVERED_REWARD_CONSERVATION: (
                "summed AEC last() delivery equals independent parallel return"
            ),
        },
        summary={
            "fixture": _DeterministicParallelFixture.metadata["name"],
            "horizon": horizon,
            "source_transitions": len(source_events),
            "destination_calls": len(destination_events),
            "stutter_and_cleanup_calls": len(destination_events) - len(source_events),
            "source_return": source_return,
            "delivered_return": delivered_return,
            "source_terminal": not source.agents,
            "aec_agents_remaining": len(aec.agents),
            "violation_count": len(violations),
        },
    )


def run_discovery_semantic_controls() -> tuple[ControlRun, ...]:
    """Run the complete pre-freeze clean-control panel."""
    return (
        run_native_clone_control(),
        run_turn_based_simultaneous_control(),
        run_parallel_to_aec_control(),
    )
