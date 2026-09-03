"""Separately loaded native runner for Shimmy's OpenSpiel-to-AEC adapter.

The adapter's action history is used only as an instrumentation signal telling
the source compiler which concrete chance outcomes occurred.  Expected states,
rewards, legal actions, observations, terminality, and decision counts come
from a separate native OpenSpiel state.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from numbers import Integral
from typing import Any

import numpy as np
import pyspiel
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

from marlrefine.alignment import align_traces
from marlrefine.baselines import (
    BaselineResult,
    endpoint,
    inapplicable_baselines,
    macro_aggregate,
    macro_boundary,
    return_only,
    strict_lockstep,
)
from marlrefine.capabilities import UnsupportedCapabilityError
from marlrefine.evaluation import build_obligation_evaluations
from marlrefine.model import (
    Alignment,
    DestinationEvent,
    ObligationEvaluation,
    SourceEvent,
    Span,
    Violation,
)
from marlrefine.obligations import check_all
from marlrefine.policies import (
    POLICY_ENGINE_ID,
    TracePolicy,
    canonical_game_identity,
    get_trace_policy,
    policy_namespace,
    select_action,
)
from marlrefine.serialization import to_jsonable

CONFIGURATION_PROVENANCE = "configuration_provenance"
INTERFACE_PROJECTION = "interface_projection"
STATE_PROJECTION = "state_projection"
LIFECYCLE_PRESERVATION = "lifecycle_preservation"
DECISION_CLOCK_PRESERVATION = "decision_clock_preservation"
STATE_KIND_SOUNDNESS = "state_kind_soundness"
DELIVERED_REWARD_CONSERVATION = "delivered_reward_conservation"
TRACE_EXECUTION = "trace_execution"
RESET_SEEDED_GAMES = frozenset({"deep_sea", "hanabi", "mfg_garnet"})
PROTOCOL_VERSION = "1.1-prerun-final-2026-09-01"
CHANCE_POLICY_ID = "explicit_adapter_history_replay_v1"
PROGRESS_ANNOTATION_METHOD_ID = "independent_native_replay_event_count_v1"
OBSERVATION_ATOL = 1e-7
OBSERVATION_RTOL = 1e-7


class ReplayMismatch(RuntimeError):
    """The instrumented adapter trace cannot be replayed on the source state."""


class ActionMappingMismatch(ReplayMismatch):
    """The adapter applied a different player action from the submitted one."""


class ChanceReplayMismatch(ReplayMismatch):
    """A path cannot be aligned because a chance outcome is unavailable."""


class ResetReplayMismatch(ChanceReplayMismatch):
    """Reset replay failed after a persistable partial chance transcript."""

    def __init__(
        self,
        message: str,
        transcript: tuple[dict[str, float | int], ...],
    ) -> None:
        super().__init__(message)
        self.transcript = transcript


def _plain_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in sorted(parameters.items()):
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        else:
            result[key] = str(value)
    return result


def _node_kind(state: pyspiel.State) -> str:
    if state.is_terminal():
        return "terminal"
    if state.is_mean_field_node():
        return "mean_field"
    if state.is_chance_node():
        return "chance"
    if state.is_simultaneous_node():
        return "simultaneous"
    return "decision"


def _state_identity(state: pyspiel.State) -> tuple[str, str]:
    """Return a digest and the exact serialization method used."""
    try:
        payload = state.serialize()
        method = "openspiel_serialize"
    except Exception:
        payload = f"{tuple(state.history())}\n{state}"
        method = "history_text_fallback"
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
    return digest, method


def _state_digest(state: pyspiel.State) -> str:
    return _state_identity(state)[0]


def _state_equivalent(source: pyspiel.State, adapted: pyspiel.State) -> bool:
    return (
        tuple(source.history()) == tuple(adapted.history())
        and source.current_player() == adapted.current_player()
        and source.is_terminal() == adapted.is_terminal()
        and _state_digest(source) == _state_digest(adapted)
    )


def _state_oracle_strength(source: pyspiel.State, adapted: pyspiel.State) -> str:
    """Say whether equality rests on canonical OpenSpiel serialization."""
    return (
        "strong"
        if _state_identity(source)[1] == "openspiel_serialize"
        and _state_identity(adapted)[1] == "openspiel_serialize"
        else "weak_diagnostic_only"
    )


def _state_structurally_equivalent(
    source: pyspiel.State,
    adapted: pyspiel.State,
) -> bool:
    """Compare non-serialization state facts available on every supported game."""
    return (
        tuple(source.history()) == tuple(adapted.history())
        and source.current_player() == adapted.current_player()
        and source.is_terminal() == adapted.is_terminal()
    )


def _reward_vector(values: Any, dimension: int) -> tuple[float, ...]:
    result = [0.0] * dimension
    for index, value in enumerate(values):
        if index >= dimension:
            break
        result[index] = float(value)
    return tuple(result)


def _adapter_reward_vector(
    env: OpenSpielCompatibilityV0,
    attribute: str,
) -> tuple[float, ...]:
    mapping = getattr(env, attribute)
    return tuple(float(mapping.get(agent, 0.0)) for agent in env.possible_agents)


def _source_observation(
    game_type: pyspiel.GameType,
    state: pyspiel.State,
    player: int,
) -> Any:
    if game_type.provides_observation_tensor:
        return np.asarray(state.observation_tensor(player), dtype=np.float64).reshape(
            state.get_game().observation_tensor_shape()
        )
    if game_type.provides_information_state_tensor:
        return np.asarray(
            state.information_state_tensor(player), dtype=np.float64
        ).reshape(state.get_game().information_state_tensor_shape())
    if game_type.provides_observation_string:
        return state.observation_string(player)
    if game_type.provides_information_state_string:
        return state.information_state_string(player)
    raise NotImplementedError("source game provides no supported observation")


def _observation_mismatch_reason(expected: Any, observed: Any) -> str | None:
    """Return the first raw-type/count/shape/dtype/value mismatch, if any.

    The expected tensor is normalized from the separately loaded source.  The
    destination value is deliberately *not* coerced before its public
    container, element count, shape, and dtype have been checked.  This keeps a
    Python list (which has no ndarray dtype contract) from passing merely
    because ``numpy.asarray`` can manufacture a compatible array.
    """
    if isinstance(expected, np.ndarray) or isinstance(observed, np.ndarray):
        if not isinstance(expected, np.ndarray) or not isinstance(observed, np.ndarray):
            return "container_type"
        expected_array = expected
        observed_array = observed
        if expected_array.size != observed_array.size:
            return "element_count"
        if expected_array.shape != observed_array.shape:
            return "shape"
        if expected_array.dtype != observed_array.dtype:
            return "dtype"
        if expected_array.dtype.kind in "fc":
            try:
                equal = bool(
                    np.allclose(
                        expected_array,
                        observed_array,
                        atol=OBSERVATION_ATOL,
                        rtol=OBSERVATION_RTOL,
                    )
                )
            except (TypeError, ValueError):
                return "value"
        else:
            try:
                equal = bool(np.array_equal(expected_array, observed_array))
            except (TypeError, ValueError):
                return "value"
        return None if equal else "value"
    return (
        None if type(expected) is type(observed) and expected == observed else "value"
    )


def _observation_evidence(value: Any) -> dict[str, Any]:
    """Serialize the representation properties governed by O8."""
    if isinstance(value, np.ndarray):
        return {
            "container_type": "numpy.ndarray",
            "element_count": int(value.size),
            "shape": tuple(int(item) for item in value.shape),
            "dtype": str(value.dtype),
            "value": _safe_jsonable(value),
        }
    return {
        "container_type": f"{type(value).__module__}.{type(value).__qualname__}",
        "element_count": None,
        "shape": None,
        "dtype": None,
        "value": _safe_jsonable(value),
    }


def _observation_signature(value: Any) -> dict[str, Any]:
    """Persist the raw public representation signature without full values."""
    evidence = _observation_evidence(value)
    return {
        key: evidence[key]
        for key in ("container_type", "element_count", "shape", "dtype")
    }


def _observation_numeric_residual(
    expected: Any,
    observed: Any,
) -> dict[str, float | bool] | None:
    """Summarize float residuals for prespecified tolerance sensitivity."""
    if not isinstance(expected, np.ndarray) or not isinstance(observed, np.ndarray):
        return None
    if (
        expected.size != observed.size
        or expected.shape != observed.shape
        or expected.dtype != observed.dtype
        or expected.dtype.kind not in "fc"
    ):
        return None
    difference = np.abs(expected - observed)
    tolerance = OBSERVATION_ATOL + OBSERVATION_RTOL * np.abs(observed)
    if not bool(np.all(np.isfinite(difference))) or not bool(
        np.all(np.isfinite(tolerance))
    ):
        return {"finite": False}
    relative_denominator = np.maximum(
        np.abs(observed),
        np.finfo(np.float64).tiny,
    )
    return {
        "finite": True,
        "maximum_absolute_difference": float(np.max(difference, initial=0.0)),
        "maximum_relative_difference": float(
            np.max(difference / relative_denominator, initial=0.0)
        ),
        "primary_tolerance_ratio": float(np.max(difference / tolerance, initial=0.0)),
    }


def _safe_jsonable(value: Any) -> Any:
    """Describe malformed interface output without letting evidence capture fail."""
    try:
        return to_jsonable(value)
    except (TypeError, ValueError):
        return {"unsupported_type": type(value).__qualname__}


def _action_mask_evidence(info: object) -> dict[str, Any]:
    """Capture stable mask diagnostics without serializing arbitrary objects."""
    evidence: dict[str, Any] = {"info_type": type(info).__qualname__}
    if not isinstance(info, Mapping) or "action_mask" not in info:
        return evidence
    value = info["action_mask"]
    evidence["action_mask_type"] = type(value).__qualname__
    try:
        mask = np.asarray(value)
    except (TypeError, ValueError):
        return evidence
    evidence.update(
        {
            "action_mask_shape": tuple(int(item) for item in mask.shape),
            "action_mask_dtype": str(mask.dtype),
            "action_mask_value": _safe_jsonable(mask),
        }
    )
    return evidence


def _normalize_action_mask(
    info: object,
    *,
    action_count: int,
) -> tuple[tuple[int, ...] | None, str | None]:
    """Validate an integer/Boolean binary mask and return its action set."""
    if not isinstance(info, Mapping):
        return None, "info_not_mapping"
    if "action_mask" not in info:
        return None, "missing_action_mask"
    try:
        mask = np.asarray(info["action_mask"])
    except (TypeError, ValueError):
        return None, "action_mask_not_array_like"
    if mask.ndim != 1:
        return None, "action_mask_not_one_dimensional"
    if mask.shape != (action_count,):
        return None, "action_mask_wrong_length"
    if mask.dtype.kind not in "biu":
        return None, "action_mask_non_integer_dtype"
    try:
        if not bool(np.all((mask == 0) | (mask == 1))):
            return None, "action_mask_not_binary"
        actions = tuple(int(item) for item in np.flatnonzero(mask))
    except (TypeError, ValueError):
        return None, "action_mask_unreadable"
    return actions, None


def _make_source_event(
    state: pyspiel.State,
    *,
    progress: int,
    decision_count: int,
    node_kind_before: str,
    action: object,
    chance_probability: float | None,
    returns_before: tuple[float, ...],
) -> SourceEvent:
    """Create one additive source event after its native transition."""
    returns_after = tuple(float(value) for value in state.returns())
    rewards = tuple(
        after - before
        for before, after in zip(returns_before, returns_after, strict=True)
    )
    state_digest, state_digest_method = _state_identity(state)
    return SourceEvent(
        progress=progress,
        rewards=rewards,
        terminated=state.is_terminal(),
        metadata={
            "node_kind_before": node_kind_before,
            "action": action,
            "chance_probability": chance_probability,
            "decision_count_after": decision_count,
            "history_length_after": len(state.history()),
            "state_digest_after": state_digest,
            "state_digest_method": state_digest_method,
            "returns_after": returns_after,
        },
    )


def _replay_reset_chance_transcript(
    state: pyspiel.State,
    history: tuple[int, ...],
) -> tuple[dict[str, float | int], ...]:
    """Replay and persist only reset-time chance outcomes."""
    transcript: list[dict[str, float | int]] = []

    def fail(message: str) -> None:
        raise ResetReplayMismatch(message, tuple(transcript))

    cursor = 0
    while state.is_chance_node():
        if cursor >= len(history):
            fail("adapter reset omitted a required chance outcome")
        action = int(history[cursor])
        cursor += 1
        outcomes = {int(item): float(prob) for item, prob in state.chance_outcomes()}
        if action not in outcomes:
            fail(f"illegal reset chance outcome {action}")
        state.apply_action(action)
        transcript.append({"action": action, "probability": outcomes[action]})
    if cursor != len(history):
        fail("adapter reset history contains a player decision")
    return tuple(transcript)


def _advance_independent_source(
    state: pyspiel.State,
    history_delta: tuple[int, ...],
    expected_decision: int | tuple[int, ...] | None,
    *,
    progress: int,
    decision_count: int,
) -> tuple[tuple[SourceEvent, ...], int, int]:
    """Apply harness-selected decisions and adapter-observed chance outcomes.

    Player actions come exclusively from ``expected_decision``. Adapter history
    is checked against those actions and is then used only as a chance tape.
    """
    if expected_decision is None:
        if history_delta:
            raise ReplayMismatch(
                "adapter advanced source history during an expected buffer or cleanup"
            )
        return (), progress, decision_count

    if state.is_terminal() or state.is_chance_node() or state.is_mean_field_node():
        raise ReplayMismatch(
            f"submitted decision cannot apply at source node {_node_kind(state)}"
        )

    events: list[SourceEvent] = []
    cursor = 0
    kind = _node_kind(state)
    returns_before = tuple(float(value) for value in state.returns())

    if state.is_simultaneous_node():
        if not isinstance(expected_decision, tuple):
            raise ReplayMismatch("simultaneous source requires a joint action")
        width = state.get_game().num_players()
        if len(expected_decision) != width:
            raise ReplayMismatch("joint action has the wrong player dimension")
        observed = history_delta[:width]
        if len(observed) != width:
            raise ReplayMismatch("adapter omitted the expected joint decision")
        if observed != expected_decision:
            raise ActionMappingMismatch(
                f"adapter joint action {observed} differs from submitted "
                f"{expected_decision}"
            )
        for player, action in enumerate(expected_decision):
            legal = tuple(int(item) for item in state.legal_actions(player))
            if legal and action not in legal:
                raise ReplayMismatch(
                    f"submitted joint action {action} is illegal for player {player}"
                )
        state.apply_actions(list(expected_decision))
        cursor = width
        action_metadata: object = expected_decision
    else:
        if isinstance(expected_decision, tuple):
            raise ReplayMismatch("sequential source requires one player action")
        if not history_delta:
            raise ReplayMismatch("adapter omitted the submitted player decision")
        observed_action = int(history_delta[0])
        if observed_action != expected_decision:
            raise ActionMappingMismatch(
                f"adapter action {observed_action} differs from submitted "
                f"{expected_decision}"
            )
        legal = tuple(int(item) for item in state.legal_actions())
        if expected_decision not in legal:
            raise ReplayMismatch(
                f"submitted source action {expected_decision} is illegal"
            )
        state.apply_action(expected_decision)
        cursor = 1
        action_metadata = expected_decision

    decision_count += 1
    progress += 1
    events.append(
        _make_source_event(
            state,
            progress=progress,
            decision_count=decision_count,
            node_kind_before=kind,
            action=action_metadata,
            chance_probability=None,
            returns_before=returns_before,
        )
    )

    while state.is_chance_node():
        if cursor >= len(history_delta):
            raise ChanceReplayMismatch(
                "adapter omitted an internally resolved chance outcome"
            )
        action = int(history_delta[cursor])
        cursor += 1
        outcomes = {int(item): float(prob) for item, prob in state.chance_outcomes()}
        if action not in outcomes:
            raise ChanceReplayMismatch(f"illegal chance outcome {action}")
        returns_before = tuple(float(value) for value in state.returns())
        state.apply_action(action)
        progress += 1
        events.append(
            _make_source_event(
                state,
                progress=progress,
                decision_count=decision_count,
                node_kind_before="chance",
                action=action,
                chance_probability=outcomes[action],
                returns_before=returns_before,
            )
        )

    if cursor != len(history_delta):
        raise ReplayMismatch("adapter history contains unexpected extra transitions")
    return tuple(events), progress, decision_count


def _sum_rewards(
    events: tuple[DestinationEvent, ...], *, delivered: bool
) -> tuple[float, ...]:
    if not events:
        return ()
    dimension = len(events[0].rewards)
    totals = [0.0] * dimension
    for event in events:
        values = event.delivered_rewards if delivered else event.rewards
        if values is None:
            continue
        for index, value in enumerate(values):
            totals[index] += value
    return tuple(totals)


def _vectors_close(
    expected: tuple[float, ...],
    observed: tuple[float, ...],
    *,
    atol: float = 1e-12,
) -> bool:
    return len(expected) == len(observed) and all(
        math.isclose(left, right, abs_tol=atol, rel_tol=1e-12)
        for left, right in zip(expected, observed, strict=True)
    )


def _chance_tape_identity(
    reset_transcript: tuple[dict[str, float | int], ...],
    source_events: tuple[SourceEvent, ...],
) -> tuple[str, int]:
    """Identify the exact reset and in-trace chance outcomes used by the oracle."""
    tape: list[dict[str, Any]] = [
        {"phase": "reset", **event} for event in reset_transcript
    ]
    tape.extend(
        {
            "phase": "trace",
            "source_progress": event.progress,
            "action": event.metadata.get("action"),
            "probability": event.metadata.get("chance_probability"),
        }
        for event in source_events
        if event.metadata.get("node_kind_before") == "chance"
    )
    payload = json.dumps(
        to_jsonable(tape),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), len(tape)


@dataclass(frozen=True, slots=True)
class TraceRun:
    """Complete evidence bundle for one deterministic adapter trace."""

    game_spec: str
    seed: int
    applicable: bool
    source_events: tuple[SourceEvent, ...]
    destination_events: tuple[DestinationEvent, ...]
    alignment: Alignment
    baselines: tuple[BaselineResult, ...]
    violations: tuple[Violation, ...]
    obligation_evaluations: tuple[ObligationEvaluation, ...]
    summary: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.applicable and not self.violations

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


def run_trace(
    game_spec: str,
    *,
    seed: int = 0,
    trace_policy: str | TracePolicy = "smallest_legal",
    max_destination_calls: int = 10_000,
    max_source_decisions: int | None = None,
    adapter_class: type[OpenSpielCompatibilityV0] = OpenSpielCompatibilityV0,
    progress_annotation_transform: Callable[[int, int, int], int] | None = None,
    progress_annotation_control_id: str | None = None,
) -> TraceRun:
    """Run a deterministic coupled trace with a separately loaded native oracle.

    This library primitive supports discovery tests and controlled batch
    injection; it is not a security boundary. The public ``trace`` CLI blocks
    prospective cohort names, while the sealed study uses only the verified,
    quiet batch entry point. Direct library use is outside that prospectively
    workflow and changes the executable source identity.
    """
    if max_destination_calls <= 0:
        raise ValueError("max_destination_calls must be positive")
    if max_source_decisions is not None and max_source_decisions <= 0:
        raise ValueError("max_source_decisions must be positive when supplied")
    if (progress_annotation_transform is None) != (
        progress_annotation_control_id is None
    ):
        raise ValueError(
            "progress annotation transform and control ID must be supplied together"
        )

    policy = get_trace_policy(trace_policy)
    invocation: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "trace_policy_name": policy.name,
        "trace_policy_id": policy.policy_id,
        "trace_policy_engine_id": POLICY_ENGINE_ID,
        "trace_policy_seed": policy.seed,
        "chance_policy_id": CHANCE_POLICY_ID,
        "progress_annotation_method_id": PROGRESS_ANNOTATION_METHOD_ID,
        "requested_seed": seed,
        "requested_max_destination_calls": max_destination_calls,
        "requested_max_source_decisions": max_source_decisions,
        "progress_annotation_control_id": progress_annotation_control_id,
    }

    source_events: list[SourceEvent] = []
    destination_events: list[DestinationEvent] = []
    integration_violations: list[Violation] = []
    source_progress = 0
    decision_count = 0
    setup_status = "pass"
    caller_supplied_nondefault: bool | None = None
    configuration_evaluated = False
    state_kind_boundaries: set[tuple[int, str]] = set()
    interface_evaluation_count = 0

    def evaluation_rows(
        alignment: Alignment,
        violations: tuple[Violation, ...],
        *,
        complete_episode: bool = False,
        unresolved: str,
    ) -> tuple[ObligationEvaluation, ...]:
        return build_obligation_evaluations(
            alignment,
            violations,
            caller_supplied_nondefault=caller_supplied_nondefault,
            configuration_evaluated=configuration_evaluated,
            state_kind_evaluation_count=len(state_kind_boundaries),
            interface_evaluation_count=interface_evaluation_count,
            complete_episode=complete_episode,
            unresolved=unresolved,
        )

    try:
        source_game = pyspiel.load_game(game_spec)
        source_game_type = source_game.get_type()
        source_name = source_game_type.short_name
        requested_parameters = _plain_parameters(source_game.get_parameters())
        default_parameters = _plain_parameters(
            pyspiel.load_game(source_name).get_parameters()
        )
        caller_supplied_nondefault = requested_parameters != default_parameters
        if source_name in RESET_SEEDED_GAMES:
            source_config = dict(source_game.get_parameters())
            source_config["seed"] = seed
            source_game = pyspiel.load_game(source_name, source_config)
        source_state = source_game.new_initial_state()
        source_parameters = _plain_parameters(source_game.get_parameters())
        game_identity_sha256 = canonical_game_identity(
            source_name,
            source_parameters,
        )
        action_namespace_sha256 = policy_namespace(policy, game_identity_sha256)
        invocation.update(
            {
                "canonical_game_identity_sha256": game_identity_sha256,
                "trace_policy_rng_namespace_sha256": action_namespace_sha256,
                "caller_supplied_nondefault_configuration": (
                    caller_supplied_nondefault
                ),
                "source_dynamics": str(
                    getattr(
                        source_game_type.dynamics,
                        "name",
                        source_game_type.dynamics,
                    )
                ).lower(),
                "source_chance_mode": str(
                    getattr(
                        source_game_type.chance_mode,
                        "name",
                        source_game_type.chance_mode,
                    )
                ).lower(),
                "source_num_players": int(source_game.num_players()),
            }
        )
    except Exception as exc:
        setup_status = f"error:source_setup:{type(exc).__name__}:{exc}"
        alignment = align_traces((), ())
        violation = Violation(
            obligation=TRACE_EXECUTION,
            code="source_setup_failed",
            message="separately loaded native construction failed",
            expected="successful source construction",
            observed=setup_status,
        )
        return TraceRun(
            game_spec=game_spec,
            seed=seed,
            applicable=False,
            source_events=(),
            destination_events=(),
            alignment=alignment,
            baselines=inapplicable_baselines("native source setup failed"),
            violations=(violation,),
            obligation_evaluations=evaluation_rows(
                alignment,
                (violation,),
                unresolved="not_evaluated",
            ),
            summary={
                **invocation,
                "setup_status": setup_status,
                "adapter_class": adapter_class.__name__,
                "stop_reason": "source_setup_failed",
                "destination_calls": 0,
                "source_transitions": 0,
                "source_decisions": 0,
                "source_terminal": None,
                "adapter_agents_remaining": None,
                "chance_event_count": 0,
            },
        )

    try:
        adapter_game = pyspiel.load_game(game_spec)
        env = adapter_class(env=adapter_game)
        env.reset(seed=seed)
        adapter_parameters = _plain_parameters(env._env.get_parameters())
    except Exception as exc:
        unsupported_capability = (
            exc.to_dict() if isinstance(exc, UnsupportedCapabilityError) else None
        )
        # Preserve the frozen legacy prefix while retaining a structured,
        # typed capability record for classification without message parsing.
        exception_name = (
            "NotImplementedError"
            if isinstance(exc, UnsupportedCapabilityError)
            else type(exc).__name__
        )
        setup_status = f"error:adapter_setup:{exception_name}:{exc}"
        alignment = align_traces((), ())
        violation = Violation(
            obligation=TRACE_EXECUTION,
            code="adapter_setup_failed",
            message="adapter construction or reset failed for a valid source game",
            expected="successful adapter construction and reset",
            observed=setup_status,
        )
        return TraceRun(
            game_spec=game_spec,
            seed=seed,
            applicable=True,
            source_events=(),
            destination_events=(),
            alignment=alignment,
            baselines=inapplicable_baselines("adapter construction or reset failed"),
            violations=(violation,),
            obligation_evaluations=evaluation_rows(
                alignment,
                (violation,),
                unresolved="not_evaluated",
            ),
            summary={
                **invocation,
                "setup_status": setup_status,
                "unsupported_capability": unsupported_capability,
                "adapter_class": adapter_class.__name__,
                "source_parameters": source_parameters,
                "stop_reason": "adapter_setup_failed",
                "destination_calls": 0,
                "source_transitions": 0,
                "source_decisions": 0,
                "source_terminal": source_state.is_terminal(),
                "adapter_agents_remaining": None,
                "chance_event_count": 0,
            },
        )

    declared_max_game_length = int(source_game.max_game_length())
    effective_decision_limit = max_source_decisions
    if max_source_decisions is not None and declared_max_game_length > 0:
        effective_decision_limit = min(
            max_source_decisions,
            declared_max_game_length,
        )
    invocation.update(
        {
            "declared_max_game_length": declared_max_game_length,
            "effective_max_source_decisions": effective_decision_limit,
        }
    )
    chance_mode = str(getattr(source_game.get_type().chance_mode, "name", "")).lower()
    if chance_mode == "sampled_stochastic":
        alignment = align_traces((), ())
        return TraceRun(
            game_spec=game_spec,
            seed=seed,
            applicable=False,
            source_events=(),
            destination_events=(),
            alignment=alignment,
            baselines=inapplicable_baselines(
                "sampled-stochastic pathwise replay is not sound"
            ),
            violations=(),
            obligation_evaluations=evaluation_rows(
                alignment,
                (),
                unresolved="not_applicable",
            ),
            summary={
                **invocation,
                "setup_status": "inapplicable:sampled_stochastic_chance",
                "adapter_class": adapter_class.__name__,
                "source_parameters": source_parameters,
                "adapter_parameters": adapter_parameters,
                "reason": (
                    "pathwise replay is not sound without explicit RNG or "
                    "state coupling"
                ),
                "stop_reason": "sampled_stochastic_inapplicable",
                "destination_calls": 0,
                "source_transitions": 0,
                "source_decisions": 0,
                "source_terminal": source_state.is_terminal(),
                "adapter_agents_remaining": len(env.agents),
                "chance_event_count": 0,
            },
        )

    configuration_evaluated = True
    if source_parameters != adapter_parameters:
        integration_violations.append(
            Violation(
                obligation=CONFIGURATION_PROVENANCE,
                code="parameters_changed_on_reset",
                message="adapter reset changed the supplied source game parameters",
                expected=source_parameters,
                observed=adapter_parameters,
            )
        )

    dimension = source_game.num_players()
    if dimension != len(env.possible_agents):
        integration_violations.append(
            Violation(
                obligation=CONFIGURATION_PROVENANCE,
                code="player_count_changed_on_reset",
                message="adapter agent count differs from the native source game",
                expected=dimension,
                observed=len(env.possible_agents),
            )
        )
    expected_agents = tuple(f"player_{player}" for player in range(dimension))
    if tuple(env.possible_agents) != expected_agents:
        integration_violations.append(
            Violation(
                obligation=INTERFACE_PROJECTION,
                code="agent_identity_mismatch",
                message=(
                    "adapter agent identities differ from the declared player mapping"
                ),
                expected=expected_agents,
                observed=tuple(env.possible_agents),
            )
        )

    # A parameter mismatch means the two states do not describe the same task;
    # stepping them would turn a setup defect into misleading downstream noise.
    if integration_violations:
        alignment = align_traces((), ())
        return TraceRun(
            game_spec=game_spec,
            seed=seed,
            applicable=True,
            source_events=(),
            destination_events=(),
            alignment=alignment,
            baselines=inapplicable_baselines(
                "configuration mismatch prevented trace execution"
            ),
            violations=tuple(integration_violations),
            obligation_evaluations=evaluation_rows(
                alignment,
                tuple(integration_violations),
                unresolved="not_evaluated",
            ),
            summary={
                **invocation,
                "setup_status": setup_status,
                "adapter_class": adapter_class.__name__,
                "source_parameters": source_parameters,
                "adapter_parameters": adapter_parameters,
                "stop_reason": "configuration_mismatch",
                "destination_calls": 0,
                "source_transitions": 0,
                "source_decisions": 0,
                "source_terminal": source_state.is_terminal(),
                "adapter_agents_remaining": len(env.agents),
                "chance_event_count": 0,
            },
        )

    # Replay chance outcomes performed inside reset. They are setup events and
    # therefore establish the alignment origin rather than entering the trace.
    initial_history = tuple(int(action) for action in env.game_state.history())
    reset_chance_transcript: tuple[dict[str, float | int], ...] = ()
    try:
        reset_chance_transcript = _replay_reset_chance_transcript(
            source_state, initial_history
        )
    except ReplayMismatch as exc:
        if isinstance(exc, ResetReplayMismatch):
            reset_chance_transcript = exc.transcript
        integration_violations.append(
            Violation(
                obligation=TRACE_EXECUTION,
                code="unalignable_chance",
                message="adapter reset history cannot be replayed on the source",
                expected="legal source history",
                observed=str(exc),
            )
        )

    if integration_violations:
        alignment = align_traces((), ())
        return TraceRun(
            game_spec=game_spec,
            seed=seed,
            applicable=True,
            source_events=(),
            destination_events=(),
            alignment=alignment,
            baselines=inapplicable_baselines("reset transcript replay failed"),
            violations=tuple(integration_violations),
            obligation_evaluations=evaluation_rows(
                alignment,
                tuple(integration_violations),
                unresolved="not_evaluated",
            ),
            summary={
                **invocation,
                "setup_status": "error:reset_replay_failed",
                "adapter_class": adapter_class.__name__,
                "source_parameters": source_parameters,
                "adapter_parameters": adapter_parameters,
                "reset_history": initial_history,
                "reset_chance_transcript": reset_chance_transcript,
                "stop_reason": "reset_replay_failed",
                "destination_calls": 0,
                "source_transitions": 0,
                "source_decisions": 0,
                "source_terminal": source_state.is_terminal(),
                "adapter_agents_remaining": len(env.agents),
                "chance_event_count": 0,
            },
        )

    (
        post_reset_source_state_digest,
        post_reset_source_state_digest_method,
    ) = _state_identity(source_state)
    adapter_reset_digest, adapter_reset_digest_method = _state_identity(env.game_state)
    post_reset_state_oracle_strength = _state_oracle_strength(
        source_state,
        env.game_state,
    )
    adapter_game_length_at_reset = int(env.game_length)
    reset_state_mismatch = (
        not _state_equivalent(source_state, env.game_state)
        if post_reset_state_oracle_strength == "strong"
        else not _state_structurally_equivalent(source_state, env.game_state)
    )
    if reset_state_mismatch:
        integration_violations.append(
            Violation(
                obligation=STATE_PROJECTION,
                code="reset_state_mismatch",
                message=(
                    "separately loaded native and adapted states disagree after "
                    "reset replay"
                ),
                expected=post_reset_source_state_digest,
                observed=adapter_reset_digest,
            )
        )
        alignment = align_traces((), ())
        return TraceRun(
            game_spec=game_spec,
            seed=seed,
            applicable=True,
            source_events=(),
            destination_events=(),
            alignment=alignment,
            baselines=inapplicable_baselines(
                "reset state mismatch prevented trace execution"
            ),
            violations=tuple(integration_violations),
            obligation_evaluations=evaluation_rows(
                alignment,
                tuple(integration_violations),
                unresolved="not_evaluated",
            ),
            summary={
                **invocation,
                "setup_status": "error:reset_state_mismatch",
                "adapter_class": adapter_class.__name__,
                "source_parameters": source_parameters,
                "adapter_parameters": adapter_parameters,
                "reset_history": initial_history,
                "reset_chance_transcript": reset_chance_transcript,
                "post_reset_source_state_digest": post_reset_source_state_digest,
                "post_reset_source_state_digest_method": (
                    post_reset_source_state_digest_method
                ),
                "post_reset_adapter_state_digest": adapter_reset_digest,
                "post_reset_adapter_state_digest_method": adapter_reset_digest_method,
                "adapter_game_length_at_reset": adapter_game_length_at_reset,
                "stop_reason": "reset_state_mismatch",
                "destination_calls": 0,
                "source_transitions": 0,
                "source_decisions": 0,
                "source_terminal": source_state.is_terminal(),
                "adapter_agents_remaining": len(env.agents),
                "chance_event_count": 0,
            },
        )

    state_kind_boundaries.add((source_progress, _node_kind(source_state)))
    pending_joint_actions: dict[int, int] = {}
    expected_delivered_rewards = [0.0] * dimension
    stop_reason = "running"
    pre_cleanup_lifecycle_reported = False
    decision_clock_mismatch_reported = False
    delivery_comparison_count = 0
    delivery_mismatch_count = 0

    while env.agents:
        destination_index = len(destination_events)
        attempted_destination_span = Span(destination_index, destination_index)
        agent = env.agent_selection
        try:
            player = expected_agents.index(agent)
        except ValueError:
            integration_violations.append(
                Violation(
                    obligation=INTERFACE_PROJECTION,
                    code="selected_agent_identity_mismatch",
                    message=(
                        "adapter selected an agent outside the source player mapping"
                    ),
                    destination_span=attempted_destination_span,
                    expected=expected_agents,
                    observed=agent,
                )
            )
            stop_reason = "agent_identity_mismatch"
            break
        if env.agent_name_id_mapping.get(agent) != player:
            integration_violations.append(
                Violation(
                    obligation=INTERFACE_PROJECTION,
                    code="agent_index_mapping_mismatch",
                    message=(
                        "adapter's private agent index disagrees with its public name"
                    ),
                    destination_span=attempted_destination_span,
                    expected=player,
                    observed=env.agent_name_id_mapping.get(agent),
                )
            )
        cleanup_preview = bool(
            env.terminations.get(agent, False) or env.truncations.get(agent, False)
        )
        if (
            effective_decision_limit is not None
            and decision_count >= effective_decision_limit
            and not cleanup_preview
        ):
            stop_reason = "source_decision_limit"
            break
        if len(destination_events) >= max_destination_calls:
            stop_reason = "destination_call_budget"
            break

        observation, delivered, terminated_before, truncated_before, info = env.last()
        cleanup = bool(terminated_before or truncated_before)
        observation_signature_before = (
            None if cleanup else _observation_signature(observation)
        )
        observation_numeric_residual_before: dict[str, float | bool] | None = None
        expected_delivered = float(expected_delivered_rewards[player])
        delivered_matches_native = math.isclose(
            expected_delivered,
            float(delivered),
            abs_tol=1e-12,
            rel_tol=1e-12,
        )
        delivery_comparison_count += 1
        if not delivered_matches_native:
            delivery_mismatch_count += 1
        expected_delivered_rewards[player] = 0.0

        if cleanup:
            expected_lifecycle = (source_state.is_terminal(), False)
            observed_lifecycle = (
                bool(terminated_before),
                bool(truncated_before),
            )
            if (
                expected_lifecycle != observed_lifecycle
                and not pre_cleanup_lifecycle_reported
            ):
                integration_violations.append(
                    Violation(
                        obligation=LIFECYCLE_PRESERVATION,
                        code="pre_cleanup_lifecycle_mismatch",
                        message=(
                            "adapter requested cleanup with lifecycle flags that "
                            "disagree with the live source boundary"
                        ),
                        destination_span=attempted_destination_span,
                        expected=expected_lifecycle,
                        observed=observed_lifecycle,
                    )
                )
                pre_cleanup_lifecycle_reported = True

        delivered_vector = [0.0] * dimension
        delivered_vector[player] = float(delivered)

        action: int | None = None
        expected_decision: int | tuple[int, ...] | None = None
        declared_action_space_n: int | None = None
        source_legal_actions_before: tuple[int, ...] | None = None
        source_state_digest_before: str | None = None
        source_state_digest_method_before: str | None = None
        if not cleanup:
            if source_state.is_mean_field_node():
                integration_violations.append(
                    Violation(
                        obligation=STATE_KIND_SOUNDNESS,
                        code="mean_field_protocol_missing",
                        message=(
                            "adapter requests an ordinary agent action or terminal "
                            "cleanup at a native mean-field distribution node"
                        ),
                        destination_span=attempted_destination_span,
                        expected=(
                            "distribution update or explicit unsupported-game error"
                        ),
                        observed={
                            "adapter_terminated": bool(terminated_before),
                            "adapter_truncated": bool(truncated_before),
                        },
                    )
                )
                stop_reason = "unsupported_node"
                break
            if source_state.is_chance_node() or source_state.is_terminal():
                integration_violations.append(
                    Violation(
                        obligation=LIFECYCLE_PRESERVATION,
                        code="adapter_requests_action_at_nondecision_node",
                        message=(
                            "adapter requested an agent action at a nondecision "
                            "source node"
                        ),
                        destination_span=attempted_destination_span,
                        expected=_node_kind(source_state),
                        observed=agent,
                    )
                )
                stop_reason = "source_node_mismatch"
                break

            if (
                not source_state.is_simultaneous_node()
                and source_state.current_player() != player
            ):
                integration_violations.append(
                    Violation(
                        obligation=INTERFACE_PROJECTION,
                        code="acting_player_mismatch",
                        message="adapter selected a different player from the source",
                        destination_span=attempted_destination_span,
                        expected=source_state.current_player(),
                        observed=player,
                    )
                )
                stop_reason = "agent_schedule_mismatch"
                break

            legal_actions = tuple(
                sorted(
                    {
                        int(item)
                        for item in (
                            source_state.legal_actions(player)
                            if source_state.is_simultaneous_node()
                            else source_state.legal_actions()
                        )
                    }
                )
            )
            source_legal_actions_before = legal_actions
            (
                source_state_digest_before,
                source_state_digest_method_before,
            ) = _state_identity(source_state)
            source_action_count = int(source_game.num_distinct_actions())
            try:
                action_space = env.action_space(agent)
                declared_n = getattr(action_space, "n", None)
            except Exception as exc:
                action_space = None
                declared_n = None
                action_space_error = f"{type(exc).__name__}:{exc}"
            else:
                action_space_error = None
            if (
                not isinstance(declared_n, Integral)
                or isinstance(declared_n, bool)
                or int(declared_n) <= 0
            ):
                integration_violations.append(
                    Violation(
                        obligation=INTERFACE_PROJECTION,
                        code="action_space_malformed",
                        message=(
                            "adapter does not declare a positive Discrete-like "
                            "action-space length"
                        ),
                        destination_span=attempted_destination_span,
                        expected={
                            "type": "Discrete-like",
                            "n": source_action_count,
                        },
                        observed={
                            "type": (
                                None
                                if action_space is None
                                else type(action_space).__qualname__
                            ),
                            "n": _safe_jsonable(declared_n),
                            "error": action_space_error,
                        },
                    )
                )
                stop_reason = "interface_projection_mismatch"
                break
            declared_action_space_n = int(declared_n)
            if declared_action_space_n != source_action_count:
                integration_violations.append(
                    Violation(
                        obligation=INTERFACE_PROJECTION,
                        code="action_space_size_mismatch",
                        message=(
                            "adapter's declared action-space length differs from "
                            "the source action namespace"
                        ),
                        destination_span=attempted_destination_span,
                        expected=source_action_count,
                        observed=declared_action_space_n,
                    )
                )
                stop_reason = "interface_projection_mismatch"
                break
            mask, mask_error = _normalize_action_mask(
                info,
                action_count=declared_action_space_n,
            )
            if mask_error is not None:
                integration_violations.append(
                    Violation(
                        obligation=INTERFACE_PROJECTION,
                        code=(
                            "action_mask_missing"
                            if mask_error == "missing_action_mask"
                            else "action_mask_malformed"
                        ),
                        message=(
                            "adapter info does not expose a valid one-dimensional "
                            "binary action mask"
                        ),
                        destination_span=attempted_destination_span,
                        expected={
                            "length": declared_action_space_n,
                            "dtype": "integer_or_bool",
                            "values": "binary",
                        },
                        observed={
                            "reason": mask_error,
                            **_action_mask_evidence(info),
                        },
                    )
                )
                stop_reason = "interface_projection_mismatch"
                break
            elif legal_actions != mask:
                integration_violations.append(
                    Violation(
                        obligation=INTERFACE_PROJECTION,
                        code="legal_action_mismatch",
                        message="adapter action mask differs from native legal actions",
                        destination_span=attempted_destination_span,
                        expected=legal_actions,
                        observed=mask,
                    )
                )
                stop_reason = "interface_projection_mismatch"
                break
            if not legal_actions:
                integration_violations.append(
                    Violation(
                        obligation=INTERFACE_PROJECTION,
                        code="no_legal_action_for_live_agent",
                        message=(
                            "source has no legal action for the selected live agent"
                        ),
                        destination_span=attempted_destination_span,
                        expected="one or more legal actions",
                        observed=(),
                    )
                )
                stop_reason = "no_legal_action"
                break

            expected_observation = _source_observation(
                source_game.get_type(), source_state, player
            )
            observation_mismatch = _observation_mismatch_reason(
                expected_observation,
                observation,
            )
            observation_numeric_residual_before = _observation_numeric_residual(
                expected_observation,
                observation,
            )
            interface_evaluation_count += 1
            if observation_mismatch is not None:
                integration_violations.append(
                    Violation(
                        obligation=INTERFACE_PROJECTION,
                        code="observation_mismatch",
                        message=(
                            "adapter observation differs from its declared source "
                            f"projection ({observation_mismatch} mismatch)"
                        ),
                        destination_span=attempted_destination_span,
                        expected=_observation_evidence(expected_observation),
                        observed={
                            "mismatch": observation_mismatch,
                            **_observation_evidence(observation),
                        },
                    )
                )
            action = select_action(
                policy,
                legal_actions,
                namespace_sha256=action_namespace_sha256,
                source_decision_index=decision_count,
                player=player,
            )
            if source_state.is_simultaneous_node():
                if player in pending_joint_actions:
                    integration_violations.append(
                        Violation(
                            obligation=INTERFACE_PROJECTION,
                            code="duplicate_simultaneous_player",
                            message=(
                                "adapter selected the same simultaneous player "
                                "twice before commit"
                            ),
                            destination_span=attempted_destination_span,
                            expected={
                                "schedule": "each required player exactly once",
                                "interface_boundary_evaluated": True,
                            },
                            observed={
                                "player": player,
                                "interface_boundary_evaluated": True,
                            },
                        )
                    )
                    stop_reason = "agent_schedule_mismatch"
                    break
                pending_joint_actions[player] = action
                required_players = tuple(
                    candidate
                    for candidate in range(dimension)
                    if source_state.legal_actions(candidate)
                )
                if all(
                    candidate in pending_joint_actions for candidate in required_players
                ):
                    expected_decision = tuple(
                        pending_joint_actions.get(
                            candidate, int(pyspiel.INVALID_ACTION)
                        )
                        for candidate in range(dimension)
                    )
                    pending_joint_actions.clear()
            else:
                expected_decision = action

        buffer_only_attempt = bool(
            not cleanup
            and source_state.is_simultaneous_node()
            and expected_decision is None
        )
        history_before = tuple(int(item) for item in env.game_state.history())
        try:
            env.step(action)
        except Exception as exc:
            integration_violations.append(
                Violation(
                    obligation=TRACE_EXECUTION,
                    code="adapter_step_failed",
                    message="adapter step raised during a generated legal trace",
                    destination_span=attempted_destination_span,
                    expected="successful step",
                    observed={
                        "error": f"{type(exc).__name__}:{exc}",
                        "interface_boundary_evaluated": not cleanup,
                    },
                )
            )
            stop_reason = "adapter_step_error"
            break

        history_after = tuple(int(item) for item in env.game_state.history())
        if history_after[: len(history_before)] != history_before:
            integration_violations.append(
                Violation(
                    obligation=STATE_PROJECTION,
                    code="instrumentation_history_not_prefix_monotone",
                    message=(
                        "wrapped history rewrote an earlier prefix, so progress "
                        "instrumentation is unusable"
                    ),
                    destination_span=attempted_destination_span,
                    expected={
                        "history_prefix": history_before,
                        "buffer_only_attempt": buffer_only_attempt,
                        "interface_boundary_evaluated": not cleanup,
                    },
                    observed=history_after,
                )
            )
            stop_reason = "history_regression"
            break

        history_delta = history_after[len(history_before) :]
        source_progress_before = source_progress
        try:
            new_events, source_progress, decision_count = _advance_independent_source(
                source_state,
                history_delta,
                expected_decision,
                progress=source_progress,
                decision_count=decision_count,
            )
            source_events.extend(new_events)
            for source_event in new_events:
                for reward_player, reward in enumerate(source_event.rewards):
                    expected_delivered_rewards[reward_player] += float(reward)
                node_kind_before = source_event.metadata.get("node_kind_before")
                if isinstance(node_kind_before, str):
                    state_kind_boundaries.add(
                        (source_event.progress - 1, node_kind_before)
                    )
            state_kind_boundaries.add((source_progress, _node_kind(source_state)))
        except ReplayMismatch as exc:
            if isinstance(exc, ActionMappingMismatch):
                code = "submitted_action_mismatch"
            elif isinstance(exc, ChanceReplayMismatch):
                code = "unalignable_chance"
            else:
                code = "instrumentation_replay_failed"
            integration_violations.append(
                Violation(
                    obligation=STATE_PROJECTION,
                    code=code,
                    message=(
                        "adapter transition cannot be replayed on the separately "
                        "loaded native source"
                    ),
                    destination_span=attempted_destination_span,
                    expected="legal source transition sequence",
                    observed={
                        "error": str(exc),
                        "buffer_only_attempt": buffer_only_attempt,
                        "interface_boundary_evaluated": not cleanup,
                    },
                )
            )
            stop_reason = "source_replay_error"
            break

        state_oracle_strength = _state_oracle_strength(source_state, env.game_state)
        state_mismatch = (
            not _state_equivalent(source_state, env.game_state)
            if state_oracle_strength == "strong"
            else not _state_structurally_equivalent(source_state, env.game_state)
        )
        if state_mismatch:
            integration_violations.append(
                Violation(
                    obligation=STATE_PROJECTION,
                    code="aligned_state_mismatch",
                    message="source and adapter states disagree at an aligned boundary",
                    destination_span=Span(destination_index, destination_index + 1),
                    expected=_state_digest(source_state),
                    observed=_state_digest(env.game_state),
                )
            )

        adapter_decision_clock_elapsed = (
            int(env.game_length) - adapter_game_length_at_reset
        )
        decision_clock_matches = adapter_decision_clock_elapsed == decision_count
        if not decision_clock_matches and not decision_clock_mismatch_reported:
            integration_violations.append(
                Violation(
                    obligation=DECISION_CLOCK_PRESERVATION,
                    code="source_decision_clock_mismatch",
                    message=(
                        "adapter elapsed game-length clock differs from the "
                        "native source player/joint-decision count"
                    ),
                    destination_span=Span(destination_index, destination_index + 1),
                    expected={"source_decisions": decision_count},
                    observed={
                        "adapter_game_length_at_reset": (adapter_game_length_at_reset),
                        "adapter_game_length": int(env.game_length),
                        "adapter_elapsed_game_length": (adapter_decision_clock_elapsed),
                    },
                )
            )
            decision_clock_mismatch_reported = True

        active_terminations = tuple(bool(value) for value in env.terminations.values())
        active_truncations = tuple(bool(value) for value in env.truncations.values())
        destination_terminated = (
            bool(active_terminations) and all(active_terminations)
        ) or (cleanup and bool(terminated_before))
        destination_truncated = (
            bool(active_truncations) and all(active_truncations)
        ) or (cleanup and bool(truncated_before))

        source_state_digest, source_state_digest_method = _state_identity(source_state)
        adapter_state_digest, adapter_state_digest_method = _state_identity(
            env.game_state
        )
        annotated_source_progress = source_progress
        if progress_annotation_transform is not None:
            annotated_source_progress = progress_annotation_transform(
                destination_index,
                source_progress_before,
                source_progress,
            )
        destination_events.append(
            DestinationEvent(
                source_progress=annotated_source_progress,
                rewards=_adapter_reward_vector(env, "rewards"),
                delivered_rewards=tuple(delivered_vector),
                terminated=destination_terminated,
                truncated=destination_truncated,
                cleanup=cleanup,
                metadata={
                    "agent": agent,
                    "player": player,
                    "action": action,
                    "declared_action_space_n": declared_action_space_n,
                    "source_legal_actions_before": source_legal_actions_before,
                    "source_state_digest_before": source_state_digest_before,
                    "source_state_digest_method_before": (
                        source_state_digest_method_before
                    ),
                    "source_history_delta": history_delta,
                    "progress_instrumentation": {
                        "method_id": PROGRESS_ANNOTATION_METHOD_ID,
                        "progress_before": source_progress_before,
                        "progress_after": source_progress,
                        "annotated_progress_after": annotated_source_progress,
                        "replayed_source_event_count": len(new_events),
                        "source_event_progresses": tuple(
                            event.progress for event in new_events
                        ),
                        "wrapped_history_delta": history_delta,
                    },
                    "source_decision_count_after": decision_count,
                    "buffer_only": buffer_only_attempt,
                    "observation_signature_before": observation_signature_before,
                    "observation_numeric_residual_before": (
                        observation_numeric_residual_before
                    ),
                    "adapter_game_length_after": int(env.game_length),
                    "adapter_decision_clock_elapsed": (adapter_decision_clock_elapsed),
                    "source_node_kind_after": _node_kind(source_state),
                    "source_state_digest_after": source_state_digest,
                    "source_state_digest_method": source_state_digest_method,
                    "adapter_state_digest_after": adapter_state_digest,
                    "adapter_state_digest_method": adapter_state_digest_method,
                    "state_oracle_strength": state_oracle_strength,
                    "native_expected_delivered_reward": expected_delivered,
                    "delivered_reward_matches_native": delivered_matches_native,
                },
            )
        )

        if not delivered_matches_native:
            integration_violations.append(
                Violation(
                    obligation=DELIVERED_REWARD_CONSERVATION,
                    code="consumer_delivery_mismatch",
                    message=(
                        "the reward returned by last() differs from the native "
                        "reward accumulated for the selected agent since its prior "
                        "delivery"
                    ),
                    destination_span=Span(
                        destination_index,
                        destination_index + 1,
                    ),
                    expected=expected_delivered,
                    observed=float(delivered),
                )
            )

        if not cleanup and source_state.is_terminal() != destination_terminated:
            integration_violations.append(
                Violation(
                    obligation=LIFECYCLE_PRESERVATION,
                    code="terminality_mismatch",
                    message="adapter and source disagree about terminality",
                    destination_span=Span(destination_index, destination_index + 1),
                    expected=source_state.is_terminal(),
                    observed=destination_terminated,
                )
            )

        if not cleanup and source_state.is_mean_field_node():
            integration_violations.append(
                Violation(
                    obligation=STATE_KIND_SOUNDNESS,
                    code="mean_field_node_silently_terminated",
                    message="adapter treated a live mean-field node as terminal",
                    destination_span=Span(destination_index, destination_index + 1),
                    expected={"node_kind": "mean_field", "terminal": False},
                    observed={"terminated": destination_terminated},
                )
            )

        if not cleanup and destination_truncated and not source_state.is_terminal():
            integration_violations.append(
                Violation(
                    obligation=DECISION_CLOCK_PRESERVATION,
                    code="premature_adapter_truncation",
                    message="adapter truncated before the native source ended",
                    destination_span=Span(destination_index, destination_index + 1),
                    expected={
                        "source_terminal": False,
                        "source_decisions": decision_count,
                        "max_game_length": int(source_game.max_game_length()),
                    },
                    observed={
                        "adapter_truncated": True,
                        "adapter_game_length": int(env.game_length),
                    },
                )
            )

    if stop_reason == "running":
        stop_reason = "destination_episode_end" if not env.agents else "unknown"

    if stop_reason == "destination_episode_end":
        if pending_joint_actions:
            integration_violations.append(
                Violation(
                    obligation=LIFECYCLE_PRESERVATION,
                    code="unfinished_joint_action_buffer",
                    message=(
                        "destination agents disappeared before a simultaneous "
                        "joint action committed"
                    ),
                    expected="all required player actions followed by one commit",
                    observed=tuple(
                        {
                            "player": player,
                            "action": action,
                        }
                        for player, action in sorted(pending_joint_actions.items())
                    ),
                )
            )
        if not source_state.is_terminal():
            integration_violations.append(
                Violation(
                    obligation=LIFECYCLE_PRESERVATION,
                    code="agents_exhausted_before_source_terminal",
                    message=(
                        "destination agent set became empty while the separately "
                        "loaded native source remained live"
                    ),
                    expected={"source_terminal": True},
                    observed={
                        "source_terminal": False,
                        "source_node_kind": _node_kind(source_state),
                    },
                )
            )

    if stop_reason == "destination_call_budget":
        integration_violations.append(
            Violation(
                obligation=TRACE_EXECUTION,
                code="destination_call_budget_exhausted",
                message=(
                    "trace hit its safety call budget before a requested stop condition"
                ),
                expected=f"fewer than {max_destination_calls} calls",
                observed=len(destination_events),
            )
        )

    alignment = align_traces(tuple(source_events), tuple(destination_events))
    generic_violations = check_all(alignment)
    delivered_sum = _sum_rewards(tuple(destination_events), delivered=True)
    instantaneous_sum = _sum_rewards(tuple(destination_events), delivered=False)
    source_return = _reward_vector(source_state.returns(), dimension)
    chance_tape_sha256, chance_event_count = _chance_tape_identity(
        reset_chance_transcript,
        tuple(source_events),
    )

    if (
        not env.agents
        and source_state.is_terminal()
        and not _vectors_close(source_return, delivered_sum)
    ):
        integration_violations.append(
            Violation(
                obligation=DELIVERED_REWARD_CONSERVATION,
                code="consumer_return_mismatch",
                message=(
                    "consumer-delivered AEC reward does not equal the native return"
                ),
                expected=source_return,
                observed=delivered_sum,
            )
        )

    violations = tuple((*generic_violations, *integration_violations))
    baselines = (
        strict_lockstep(alignment),
        macro_boundary(alignment),
        macro_aggregate(alignment),
        endpoint(alignment),
        return_only(
            source_return,
            delivered_sum,
            complete_episode=not env.agents and source_state.is_terminal(),
        ),
    )
    final_source_state_digest, final_source_state_digest_method = _state_identity(
        source_state
    )
    return TraceRun(
        game_spec=game_spec,
        seed=seed,
        applicable=True,
        source_events=tuple(source_events),
        destination_events=tuple(destination_events),
        alignment=alignment,
        baselines=baselines,
        violations=violations,
        obligation_evaluations=evaluation_rows(
            alignment,
            violations,
            complete_episode=not env.agents and source_state.is_terminal(),
            unresolved="no_applicable_site",
        ),
        summary={
            **invocation,
            "setup_status": setup_status,
            "adapter_class": adapter_class.__name__,
            "source_parameters": source_parameters,
            "adapter_parameters": adapter_parameters,
            "reset_history": initial_history,
            "reset_chance_transcript": reset_chance_transcript,
            "post_reset_source_state_digest": post_reset_source_state_digest,
            "post_reset_source_state_digest_method": (
                post_reset_source_state_digest_method
            ),
            "post_reset_adapter_state_digest": adapter_reset_digest,
            "post_reset_adapter_state_digest_method": adapter_reset_digest_method,
            "post_reset_state_oracle_strength": post_reset_state_oracle_strength,
            "adapter_game_length_at_reset": adapter_game_length_at_reset,
            "adapter_game_length_final": int(env.game_length),
            "adapter_decision_clock_elapsed_final": (
                int(env.game_length) - adapter_game_length_at_reset
            ),
            "decision_clock_mismatch_reported": decision_clock_mismatch_reported,
            "final_source_state_digest": final_source_state_digest,
            "final_source_state_digest_method": final_source_state_digest_method,
            "chance_tape_sha256": chance_tape_sha256,
            "chance_event_count": chance_event_count,
            "stop_reason": stop_reason,
            "destination_calls": len(destination_events),
            "source_transitions": len(source_events),
            "source_decisions": decision_count,
            "source_terminal": source_state.is_terminal(),
            "source_node_kind": _node_kind(source_state),
            "source_return": source_return,
            "destination_instantaneous_reward_sum": instantaneous_sum,
            "destination_delivered_reward_sum": delivered_sum,
            "delivery_comparison_count": delivery_comparison_count,
            "delivery_mismatch_count": delivery_mismatch_count,
            "adapter_agents_remaining": len(env.agents),
            "violation_count": len(violations),
        },
    )
