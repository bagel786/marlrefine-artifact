#!/usr/bin/env python3
"""Minimal reproduction for semantic drift in an OpenSpiel->PettingZoo adapter.

This is a diagnostic pilot, not the final experiment.  It checks source-grounded
refinement obligations that ordinary destination-API checks do not establish.
All examples are non-wagering board, grid, or mean-field games.
"""

from __future__ import annotations

import contextlib
import io
import warnings
from importlib.metadata import version

import numpy as np
import pyspiel
from pettingzoo.test import api_test
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0


def smallest_legal_action(env: OpenSpielCompatibilityV0) -> int | None:
    """Return a deterministic legal action for the currently selected agent."""
    _, _, terminated, truncated, info = env.last()
    if terminated or truncated:
        return None
    legal = np.flatnonzero(info["action_mask"])
    if legal.size == 0:
        raise RuntimeError("nonterminal agent has no legal action")
    return int(legal[0])


def immediate_reward_vector(env: OpenSpielCompatibilityV0) -> np.ndarray:
    return np.asarray(
        [env.rewards.get(agent, 0.0) for agent in env.possible_agents],
        dtype=float,
    )


def reference_buffer_step() -> dict[str, object]:
    """OpenSpiel's reference turn-based transform emits zero on a buffer step."""
    game = pyspiel.load_game_as_turn_based("coop_box_pushing")
    state = game.new_initial_state()
    while state.is_chance_node():
        state.apply_action(state.chance_outcomes()[0][0])
    state.apply_action(state.legal_actions()[0])
    return {
        "reference_midstep_rewards": list(state.rewards()),
        "source_advanced": False,
    }


def buffered_reward_probe(source_transitions: int = 10) -> dict[str, object]:
    """Check exactly-once reward accounting across AEC buffer microsteps."""
    source = pyspiel.load_game("coop_box_pushing")
    env = OpenSpielCompatibilityV0(env=source)
    env.reset(seed=7)

    emitted = np.zeros(source.num_players(), dtype=float)
    advances = 0
    stutter_calls_with_reward = 0
    calls = 0
    prior_history = tuple(env.game_state.history())

    while advances < source_transitions:
        env.step(smallest_legal_action(env))
        calls += 1
        reward = immediate_reward_vector(env)
        emitted += reward
        history = tuple(env.game_state.history())
        advanced = history != prior_history
        advances += int(advanced)
        if not advanced and np.any(reward != 0):
            stutter_calls_with_reward += 1
        prior_history = history

    return {
        "aec_calls": calls,
        "source_transitions": advances,
        "adapter_reward_sum": emitted.tolist(),
        "source_return": list(env.game_state.returns()),
        "nonadvance_calls_with_reward": stutter_calls_with_reward,
    }


def horizon_probe() -> dict[str, object]:
    """Check whether chance events are incorrectly counted as decisions."""
    source = pyspiel.load_game("coop_box_pushing")
    env = OpenSpielCompatibilityV0(env=source)
    env.reset(seed=0)

    calls = 0
    advances = 0
    prior_history = tuple(env.game_state.history())
    while env.agents and not any(env.truncations.values()):
        env.step(smallest_legal_action(env))
        calls += 1
        history = tuple(env.game_state.history())
        advances += int(history != prior_history)
        prior_history = history

    return {
        "aec_calls_at_truncation": calls,
        "source_decisions_observed": advances,
        "adapter_game_length": env.game_length,
        "declared_max_game_length": env._env.max_game_length(),
        "source_is_terminal": env.game_state.is_terminal(),
    }


def terminal_reward_probe() -> dict[str, object]:
    """Check whether terminal rewards replay during dead-agent cleanup."""
    source = pyspiel.load_game("nim")
    env = OpenSpielCompatibilityV0(env=source)
    env.reset(seed=0)
    emitted = np.zeros(source.num_players(), dtype=float)

    while env.agents:
        env.step(smallest_legal_action(env))
        emitted += immediate_reward_vector(env)

    return {
        "adapter_reward_sum": emitted.tolist(),
        "source_return": list(env.game_state.returns()),
    }


def consumer_return_probe(game_name: str) -> dict[str, object]:
    """Sum rewards exactly as a normal AEC consumer receives them via last()."""
    source = pyspiel.load_game(game_name)
    env = OpenSpielCompatibilityV0(env=source)
    env.reset(seed=0)
    delivered = np.zeros(source.num_players(), dtype=float)
    calls = 0

    while env.agents:
        agent = env.agent_selection
        agent_index = env.possible_agents.index(agent)
        _, reward, terminated, truncated, info = env.last()
        delivered[agent_index] += reward
        if terminated or truncated:
            action = None
        else:
            legal = np.flatnonzero(info["action_mask"])
            if legal.size == 0:
                raise RuntimeError("nonterminal agent has no legal action")
            action = int(legal[0])
        env.step(action)
        calls += 1

    return {
        "aec_calls": calls,
        "consumer_reward_sum": delivered.tolist(),
        "source_return": list(env.game_state.returns()),
        "source_is_terminal": env.game_state.is_terminal(),
    }


def parameter_probe() -> dict[str, object]:
    """Check whether reset preserves the supplied source game's identity."""
    source = pyspiel.load_game("go(board_size=5)")
    before = int(source.get_parameters()["board_size"])
    env = OpenSpielCompatibilityV0(env=source)
    env.reset(seed=3)
    after = int(env._env.get_parameters()["board_size"])
    return {"board_size_before_reset": before, "board_size_after_reset": after}


def mean_field_probe() -> dict[str, object]:
    """Check whether a nonterminal mean-field node is mistaken for terminal."""
    source = pyspiel.load_game("mfg_crowd_modelling")
    env = OpenSpielCompatibilityV0(env=source)
    env.reset(seed=0)
    env.step(smallest_legal_action(env))
    return {
        "source_is_mean_field_node": env.game_state.is_mean_field_node(),
        "source_is_terminal": env.game_state.is_terminal(),
        "adapter_terminations": dict(env.terminations),
    }


def stock_api_probe(game_name: str) -> str:
    """Show that structural API conformance does not imply semantic refinement."""
    env = OpenSpielCompatibilityV0(env=pyspiel.load_game(game_name))
    try:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            api_test(env, num_cycles=1_000, verbose_progress=False)
        return "PASS"
    except Exception as exc:  # pragma: no cover - diagnostic output
        return f"FAIL {type(exc).__name__}: {exc}"


def main() -> None:
    print(
        "versions:",
        {
            "shimmy": version("shimmy"),
            "open_spiel": version("open-spiel"),
            "pettingzoo": version("pettingzoo"),
        },
    )
    print("reference_control:", reference_buffer_step())
    print("buffered_reward:", buffered_reward_probe())
    print("early_horizon:", horizon_probe())
    print("terminal_reward:", terminal_reward_probe())
    print(
        "consumer_visible_returns:",
        {
            "nim": consumer_return_probe("nim"),
            "coop_box_pushing": consumer_return_probe("coop_box_pushing"),
        },
    )
    print("parameter_reset:", parameter_probe())
    print("mean_field:", mean_field_probe())
    print(
        "stock_api_tests:",
        {
            "coop_box_pushing": stock_api_probe("coop_box_pushing"),
            "nim": stock_api_probe("nim"),
            "mfg_crowd_modelling": stock_api_probe("mfg_crowd_modelling"),
        },
    )


if __name__ == "__main__":
    main()
