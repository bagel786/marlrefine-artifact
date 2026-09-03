from __future__ import annotations

import pytest

from marlrefine.adapters.openspiel_shimmy import run_trace
from marlrefine.policies import (
    TRACE_POLICIES,
    TRACE_POLICY_NAMES,
    canonical_game_identity,
    policy_namespace,
    select_action,
)


def test_frozen_schedule_has_exactly_eight_named_policies() -> None:
    assert TRACE_POLICY_NAMES == (
        "smallest_legal",
        "largest_legal",
        "pseudo_random_seed_0",
        "pseudo_random_seed_1",
        "pseudo_random_seed_2",
        "pseudo_random_seed_3",
        "pseudo_random_seed_4",
        "pseudo_random_seed_5",
    )


def test_hash_policy_is_namespaced_and_order_independent() -> None:
    identity = canonical_game_identity("fixture", {"width": 3})
    legal = (1, 3, 8, 13)
    sequences = []
    for policy in TRACE_POLICIES[2:]:
        namespace = policy_namespace(policy, identity)
        first = tuple(
            select_action(
                policy,
                legal,
                namespace_sha256=namespace,
                source_decision_index=index,
                player=0,
            )
            for index in range(16)
        )
        second = tuple(
            select_action(
                policy,
                tuple(reversed(legal)),
                namespace_sha256=namespace,
                source_decision_index=index,
                player=0,
            )
            for index in range(16)
        )
        assert first == second
        assert set(first).issubset(legal)
        sequences.append(first)
    assert len(set(sequences)) == 6


@pytest.mark.integration
def test_all_policies_drive_only_discovery_game_actions() -> None:
    first_actions: dict[str, object] = {}
    for policy in TRACE_POLICIES:
        run = run_trace(
            "matrix_rps",
            seed=policy.environment_seed,
            trace_policy=policy,
            max_source_decisions=1,
        )
        assert run.summary["trace_policy_name"] == policy.name
        assert run.summary["trace_policy_id"] == policy.policy_id
        assert run.summary["effective_max_source_decisions"] == 1
        assert run.source_events
        first_actions[policy.name] = run.source_events[0].metadata["action"]

    assert first_actions["smallest_legal"] == (0, 0)
    assert first_actions["largest_legal"] == (2, 2)


@pytest.mark.integration
def test_random_policy_replays_identically_on_discovery_game() -> None:
    left = run_trace(
        "tic_tac_toe",
        seed=4,
        trace_policy="pseudo_random_seed_4",
        max_source_decisions=5,
    )
    right = run_trace(
        "tic_tac_toe",
        seed=4,
        trace_policy="pseudo_random_seed_4",
        max_source_decisions=5,
    )
    assert [event.metadata["action"] for event in left.source_events] == [
        event.metadata["action"] for event in right.source_events
    ]
    assert (
        left.summary["trace_policy_rng_namespace_sha256"]
        == right.summary["trace_policy_rng_namespace_sha256"]
    )
