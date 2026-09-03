"""Frozen source-legal action policies for the prospective trace schedule.

Pseudo-random choices are derived directly from a SHA-256 namespace rather
than process-global or mutable PRNG state.  Consequently, case ordering,
parallel execution, and infrastructure-only retries cannot change a trace.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from marlrefine.serialization import to_jsonable

type PolicyKind = Literal["smallest", "largest", "pseudo_random"]

POLICY_ENGINE_ID = "sha256_source_legal_v1"


@dataclass(frozen=True, slots=True)
class TracePolicy:
    """One immutable member of the frozen eight-policy schedule."""

    name: str
    kind: PolicyKind
    seed: int | None = None

    @property
    def policy_id(self) -> str:
        if self.kind == "pseudo_random":
            return f"pseudo_random_source_legal_v1_seed_{self.seed}"
        return f"{self.kind}_legal_v1"

    @property
    def environment_seed(self) -> int:
        """Seed chance-bearing adapters consistently with the trace seed."""
        return 0 if self.seed is None else self.seed


TRACE_POLICIES = (
    TracePolicy("smallest_legal", "smallest"),
    TracePolicy("largest_legal", "largest"),
    *(
        TracePolicy(f"pseudo_random_seed_{seed}", "pseudo_random", seed)
        for seed in range(6)
    ),
)
TRACE_POLICY_NAMES = tuple(policy.name for policy in TRACE_POLICIES)
_POLICIES_BY_NAME = {policy.name: policy for policy in TRACE_POLICIES}


def get_trace_policy(value: str | TracePolicy) -> TracePolicy:
    """Resolve only a frozen policy; arbitrary callables are not study policies."""
    if isinstance(value, TracePolicy):
        registered = _POLICIES_BY_NAME.get(value.name)
        if registered != value:
            raise ValueError(f"unregistered trace policy: {value!r}")
        return value
    try:
        return _POLICIES_BY_NAME[value]
    except KeyError as exc:
        choices = ", ".join(TRACE_POLICY_NAMES)
        raise ValueError(
            f"unknown trace policy {value!r}; choose one of {choices}"
        ) from exc


def canonical_game_identity(
    short_name: str,
    parameters: dict[str, Any],
) -> str:
    """Hash the canonical game/configuration independently of execution order."""
    payload = json.dumps(
        to_jsonable({"parameters": parameters, "short_name": short_name}),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def policy_namespace(policy: TracePolicy, game_identity_sha256: str) -> str:
    """Return the fixed namespace from which every random choice is derived."""
    payload = (
        f"marlrefine\0{POLICY_ENGINE_ID}\0{policy.policy_id}\0{game_identity_sha256}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def select_action(
    policy: TracePolicy,
    legal_actions: tuple[int, ...],
    *,
    namespace_sha256: str,
    source_decision_index: int,
    player: int,
) -> int:
    """Select one source-legal action under a frozen deterministic policy."""
    if not legal_actions:
        raise ValueError("cannot select from an empty legal-action set")
    ordered = tuple(sorted(legal_actions))
    if policy.kind == "smallest":
        return ordered[0]
    if policy.kind == "largest":
        return ordered[-1]

    choice_material = (
        f"{namespace_sha256}\0decision={source_decision_index}\0player={player}"
    ).encode()
    choice = int.from_bytes(hashlib.sha256(choice_material).digest(), "big")
    return ordered[choice % len(ordered)]
