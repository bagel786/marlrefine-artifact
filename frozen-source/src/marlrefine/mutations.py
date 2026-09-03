"""Sealed behavioral mutants for the preregistered sensitivity cohort.

The mutation cohort changes only the destination adapter.  Expected values still
come from the separately loaded native OpenSpiel execution.  Candidate
definitions are declarative and can therefore be hashed and archived without
constructing a game or executing the checker.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

from marlrefine.repairs import CombinedRepairV0

MUTATION_PROTOCOL_ID = "marlrefine_sealed_mutation_protocol_v1"
MUTATION_ENGINE_ID = "marlrefine_paired_adapter_mutation_engine_v2"
MUTATION_OPERATOR_CONTRACT_VERSION = 1
MUTATION_SELECTION_SEED = 20260901
MUTANTS_PER_FAMILY = 4
POOL_PER_FAMILY = 8
MUTATION_MAX_DESTINATION_CALLS = 2_000

MUTATION_FAMILIES = (
    "reward_accounting",
    "progress_and_interface",
    "decision_clock",
    "lifecycle",
    "configuration_provenance",
    "special_node_kind",
)

_OPERATOR_IDS = {
    name: f"marlrefine.adapter_mutation.{name}.v1"
    for name in (
        "reward_scale",
        "reward_negate",
        "reward_rotate",
        "reward_drop",
        "reward_offset",
        "reward_first_agent_only",
        "history_lag_one",
        "action_mask_remove",
        "observation_dtype_float32",
        "observation_swap_agents",
        "history_duplicate_last",
        "observation_list_container",
        "observation_value_offset",
        "action_mask_add",
        "clock_reset_offset",
        "clock_extra_on_advance",
        "clock_cancel_on_advance",
        "clock_buffer_increment",
        "clock_chance_increment",
        "terminal_as_truncation",
        "suppress_terminal",
        "premature_termination",
        "premature_truncation",
        "partial_terminal_flags",
        "clear_agents_at_terminal",
        "config_replace",
        "config_drop",
        "chance_unresolved",
        "simultaneous_forget_buffer",
        "simultaneous_prefill_next",
        "mean_field_bypass_rejection",
        "chance_one_only",
    )
}


@cache
def mutation_engine_source_sha256() -> str:
    """Hash the exact operator implementation file embedded in the archive."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class MutationCandidate:
    """One unexecuted, single-hook candidate in the frozen replacement order."""

    candidate_id: str
    family: str
    priority: int
    operator: str
    hook: str
    game_spec: str
    trace_policy_name: str
    environment_seed: int
    max_source_decisions: int
    parameters: tuple[tuple[str, Any], ...] = ()

    def canonical_patch(self) -> str:
        """Return a stable synthetic diff describing the executed hook change."""
        parameter_text = json.dumps(
            dict(self.parameters),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            "--- shimmy-2.0.1/OpenSpielCompatibilityV0\n"
            f"+++ sealed-mutant/{self.candidate_id}\n"
            f"@@ hook:{self.hook} @@\n"
            f"- engine: {MUTATION_ENGINE_ID}\n"
            "- behavior: paired_combined_repair_v0\n"
            f"+ operator_id: {self.operator_id}\n"
            f"+ behavior: {self.operator} {parameter_text}\n"
        )

    @property
    def operator_id(self) -> str:
        try:
            return _OPERATOR_IDS[self.operator]
        except KeyError as exc:
            raise RuntimeError(
                f"unregistered mutation operator {self.operator!r}"
            ) from exc

    @property
    def patch_sha256(self) -> str:
        return hashlib.sha256(self.canonical_patch().encode("utf-8")).hexdigest()

    def to_manifest_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["parameters"] = dict(self.parameters)
        record["canonical_patch"] = self.canonical_patch()
        record["patch_sha256"] = self.patch_sha256
        record["operator_id"] = self.operator_id
        record["operator_contract_version"] = MUTATION_OPERATOR_CONTRACT_VERSION
        record["mutation_engine_id"] = MUTATION_ENGINE_ID
        record["mutation_engine_source_sha256"] = mutation_engine_source_sha256()
        return record


@dataclass(frozen=True, slots=True)
class ProgressInstrumentationControl:
    """A negative control that corrupts only the observed progress annotation."""

    control_id: str
    operator: str
    game_spec: str
    trace_policy_name: str
    environment_seed: int
    max_source_decisions: int

    @property
    def operator_id(self) -> str:
        return f"marlrefine.progress_instrumentation.{self.operator}.v1"

    def canonical_patch(self) -> str:
        return (
            "--- progress-annotation/reference-v1\n"
            f"+++ progress-annotation/{self.control_id}\n"
            "@@ destination-event source_progress @@\n"
            f"+ operator_id: {self.operator_id}\n"
            f"+ behavior: {self.operator}\n"
        )

    def to_manifest_record(self) -> dict[str, Any]:
        record = asdict(self)
        record.update(
            {
                "operator_id": self.operator_id,
                "canonical_patch": self.canonical_patch(),
                "patch_sha256": hashlib.sha256(
                    self.canonical_patch().encode("utf-8")
                ).hexdigest(),
                "mutation_engine_id": MUTATION_ENGINE_ID,
                "mutation_engine_source_sha256": (mutation_engine_source_sha256()),
                "included_in_24_mutant_denominator": False,
            }
        )
        return record


PROGRESS_INSTRUMENTATION_CONTROLS = (
    ProgressInstrumentationControl(
        control_id="progress-control-offset-plus-one",
        operator="offset_plus_one",
        game_spec="matrix_bos",
        trace_policy_name="smallest_legal",
        environment_seed=0,
        max_source_decisions=4,
    ),
    ProgressInstrumentationControl(
        control_id="progress-control-stall-on-advance",
        operator="stall_on_advance",
        game_spec="connect_four",
        trace_policy_name="smallest_legal",
        environment_seed=0,
        max_source_decisions=8,
    ),
)


def progress_transform_for(
    control: ProgressInstrumentationControl,
) -> Callable[[int, int, int], int]:
    """Return the exact progress-only corruption seam for a frozen control."""

    def transform(
        destination_index: int,
        progress_before: int,
        progress_after: int,
    ) -> int:
        del destination_index
        if control.operator == "offset_plus_one":
            return progress_after + 1
        if control.operator == "stall_on_advance":
            return (
                progress_before if progress_after > progress_before else progress_after
            )
        raise RuntimeError(
            f"unsupported progress instrumentation operator {control.operator!r}"
        )

    return transform


def _candidate(
    family: str,
    priority: int,
    operator: str,
    hook: str,
    game_spec: str,
    *,
    policy: str = "smallest_legal",
    seed: int = 0,
    decisions: int = 40,
    **parameters: Any,
) -> MutationCandidate:
    slug = family.replace("_", "-")
    return MutationCandidate(
        candidate_id=f"mut-{slug}-{priority:02d}",
        family=family,
        priority=priority,
        operator=operator,
        hook=hook,
        game_spec=game_spec,
        trace_policy_name=policy,
        environment_seed=seed,
        max_source_decisions=decisions,
        parameters=tuple(sorted(parameters.items())),
    )


_CANDIDATES_BY_FAMILY: dict[str, tuple[MutationCandidate, ...]] = {
    "reward_accounting": (
        _candidate(
            "reward_accounting",
            1,
            "reward_scale",
            "_update_rewards",
            "matrix_bos",
            factor=2.0,
        ),
        _candidate(
            "reward_accounting", 2, "reward_negate", "_update_rewards", "matrix_pd"
        ),
        _candidate(
            "reward_accounting",
            3,
            "reward_rotate",
            "_update_rewards",
            "matching_pennies_3p",
        ),
        _candidate(
            "reward_accounting",
            4,
            "reward_drop",
            "_update_rewards",
            "matrix_coordination",
        ),
        _candidate(
            "reward_accounting",
            5,
            "reward_offset",
            "_update_rewards",
            "matrix_sh",
            offset=0.125,
        ),
        _candidate(
            "reward_accounting",
            6,
            "reward_first_agent_only",
            "_update_rewards",
            "blotto",
            policy="largest_legal",
        ),
        _candidate(
            "reward_accounting",
            7,
            "reward_scale",
            "_update_rewards",
            "goofspiel",
            decisions=20,
            factor=0.5,
        ),
        _candidate(
            "reward_accounting",
            8,
            "reward_negate",
            "_update_rewards",
            "leduc_poker",
            decisions=30,
        ),
    ),
    "progress_and_interface": (
        _candidate(
            "progress_and_interface",
            1,
            "history_lag_one",
            "game_state.history",
            "connect_four",
            decisions=20,
        ),
        _candidate(
            "progress_and_interface",
            2,
            "action_mask_remove",
            "_update_action_masks",
            "connect_four",
            decisions=20,
        ),
        _candidate(
            "progress_and_interface",
            3,
            "observation_dtype_float32",
            "_update_observations",
            "hex(board_size=5)",
            decisions=20,
        ),
        _candidate(
            "progress_and_interface",
            4,
            "observation_swap_agents",
            "_update_observations",
            "matrix_bos",
        ),
        _candidate(
            "progress_and_interface",
            5,
            "history_duplicate_last",
            "game_state.history",
            "leduc_poker",
            decisions=20,
        ),
        _candidate(
            "progress_and_interface",
            6,
            "observation_list_container",
            "_update_observations",
            "mnk(m=3,n=3,k=3)",
            decisions=20,
        ),
        _candidate(
            "progress_and_interface",
            7,
            "observation_value_offset",
            "_update_observations",
            "chess",
            decisions=12,
            offset=0.01,
        ),
        _candidate(
            "progress_and_interface",
            8,
            "action_mask_add",
            "_update_action_masks",
            "chess",
            decisions=12,
        ),
    ),
    "decision_clock": (
        _candidate(
            "decision_clock",
            1,
            "clock_reset_offset",
            "reset",
            "connect_four",
            decisions=20,
            offset=1,
        ),
        _candidate(
            "decision_clock",
            2,
            "clock_extra_on_advance",
            "_execute_action_node",
            "matrix_bos",
            offset=1,
        ),
        _candidate(
            "decision_clock",
            3,
            "clock_cancel_on_advance",
            "_execute_action_node",
            "matrix_pd",
            offset=-1,
        ),
        _candidate(
            "decision_clock",
            4,
            "clock_buffer_increment",
            "_execute_action_node",
            "matching_pennies_3p",
        ),
        _candidate(
            "decision_clock",
            5,
            "clock_chance_increment",
            "_execute_chance_node",
            "leduc_poker",
            decisions=30,
        ),
        _candidate(
            "decision_clock",
            6,
            "clock_reset_offset",
            "reset",
            "backgammon",
            decisions=20,
            offset=-1,
        ),
        _candidate(
            "decision_clock",
            7,
            "clock_extra_on_advance",
            "_execute_action_node",
            "pig",
            decisions=20,
            offset=2,
        ),
        _candidate(
            "decision_clock",
            8,
            "clock_buffer_increment",
            "_execute_action_node",
            "goofspiel",
            decisions=20,
        ),
    ),
    "lifecycle": (
        _candidate(
            "lifecycle",
            1,
            "terminal_as_truncation",
            "_update_termination_truncation",
            "matrix_bos",
        ),
        _candidate(
            "lifecycle",
            2,
            "suppress_terminal",
            "_update_termination_truncation",
            "matrix_pd",
        ),
        _candidate(
            "lifecycle",
            3,
            "premature_termination",
            "_update_termination_truncation",
            "connect_four",
            decisions=20,
        ),
        _candidate(
            "lifecycle",
            4,
            "premature_truncation",
            "_update_termination_truncation",
            "hex(board_size=5)",
            decisions=20,
        ),
        _candidate(
            "lifecycle",
            5,
            "partial_terminal_flags",
            "_update_termination_truncation",
            "matching_pennies_3p",
        ),
        _candidate(
            "lifecycle",
            6,
            "clear_agents_at_terminal",
            "_choose_next_agent",
            "matrix_coordination",
        ),
        _candidate(
            "lifecycle",
            7,
            "suppress_terminal",
            "_update_termination_truncation",
            "blotto",
            policy="largest_legal",
        ),
        _candidate(
            "lifecycle",
            8,
            "premature_truncation",
            "_update_termination_truncation",
            "leduc_poker",
            decisions=20,
        ),
    ),
    "configuration_provenance": (
        _candidate(
            "configuration_provenance",
            1,
            "config_replace",
            "reset",
            "connect_four(rows=5,columns=6,x_in_row=4)",
            key="rows",
            value=6,
        ),
        _candidate(
            "configuration_provenance",
            2,
            "config_replace",
            "reset",
            "hex(board_size=5)",
            key="board_size",
            value=6,
        ),
        _candidate(
            "configuration_provenance",
            3,
            "config_replace",
            "reset",
            "leduc_poker(players=3)",
            key="players",
            value=2,
        ),
        _candidate(
            "configuration_provenance",
            4,
            "config_replace",
            "reset",
            "mnk(m=3,n=3,k=3)",
            key="m",
            value=4,
        ),
        _candidate(
            "configuration_provenance",
            5,
            "config_replace",
            "reset",
            "bargaining(max_turns=5)",
            key="max_turns",
            value=6,
        ),
        _candidate(
            "configuration_provenance",
            6,
            "config_replace",
            "reset",
            "oshi_zumo(coins=20,horizon=50,size=3)",
            key="coins",
            value=21,
        ),
        _candidate(
            "configuration_provenance",
            7,
            "config_drop",
            "reset",
            "connect_four(rows=5,columns=6,x_in_row=4)",
            key="x_in_row",
        ),
        _candidate(
            "configuration_provenance",
            8,
            "config_replace",
            "reset",
            "pig(winscore=20)",
            key="winscore",
            value=21,
        ),
    ),
    "special_node_kind": (
        _candidate(
            "special_node_kind",
            1,
            "chance_unresolved",
            "_execute_chance_node",
            "leduc_poker",
            decisions=20,
        ),
        _candidate(
            "special_node_kind",
            2,
            "simultaneous_forget_buffer",
            "_execute_action_node",
            "matrix_bos",
        ),
        _candidate(
            "special_node_kind",
            3,
            "simultaneous_prefill_next",
            "_execute_action_node",
            "matrix_pd",
        ),
        _candidate(
            "special_node_kind",
            4,
            "mean_field_bypass_rejection",
            "__init__",
            "mfg_dynamic_routing",
            decisions=10,
        ),
        _candidate(
            "special_node_kind",
            5,
            "chance_one_only",
            "_execute_chance_node",
            "backgammon",
            decisions=20,
        ),
        _candidate(
            "special_node_kind",
            6,
            "simultaneous_forget_buffer",
            "_execute_action_node",
            "goofspiel",
            decisions=20,
        ),
        _candidate(
            "special_node_kind",
            7,
            "mean_field_bypass_rejection",
            "__init__",
            "mfg_crowd_modelling_2d",
            decisions=10,
        ),
        _candidate(
            "special_node_kind",
            8,
            "chance_unresolved",
            "_execute_chance_node",
            "blackjack",
            decisions=20,
        ),
    ),
}

CANDIDATE_POOL = tuple(
    candidate
    for family in MUTATION_FAMILIES
    for candidate in _CANDIDATES_BY_FAMILY[family]
)

if len(CANDIDATE_POOL) != len(MUTATION_FAMILIES) * POOL_PER_FAMILY:
    raise RuntimeError("sealed mutation pool has the wrong size")
if len({candidate.candidate_id for candidate in CANDIDATE_POOL}) != len(CANDIDATE_POOL):
    raise RuntimeError("sealed mutation candidate IDs are not unique")


class _HistoryProxy:
    """Delegate a state while corrupting only its disclosed history."""

    def __init__(self, state: Any, operator: str, mark: Any) -> None:
        self._state = state
        self._operator = operator
        self._mark = mark

    def __getattr__(self, name: str) -> Any:
        return getattr(self._state, name)

    def history(self) -> list[int]:
        history = list(self._state.history())
        if not history:
            return history
        self._mark({"history_length": len(history)})
        if self._operator == "history_lag_one":
            return history[:-1]
        if self._operator == "history_duplicate_last":
            return [*history, history[-1]]
        raise RuntimeError(f"unsupported history operator {self._operator}")


def paired_reference_class_for(
    candidate: MutationCandidate,
) -> type[CombinedRepairV0]:
    """Create the candidate-bound clean member of a paired comparison.

    The generated subclass is behaviorally identical to ``CombinedRepairV0``.
    Its explicit identity prevents an accidental comparison against the stock
    adapter or against a differently composed repair treatment.
    """

    class PairedCombinedRepairReference(CombinedRepairV0):
        mutation_candidate = candidate
        mutation_engine_id = MUTATION_ENGINE_ID
        mutation_role = "paired_clean_reference"

    PairedCombinedRepairReference.__name__ = (
        "PairedReference_" + candidate.candidate_id.replace("-", "_")
    )
    PairedCombinedRepairReference.__qualname__ = PairedCombinedRepairReference.__name__
    return PairedCombinedRepairReference


def adapter_class_for(candidate: MutationCandidate) -> type[CombinedRepairV0]:
    """Create the deterministic adapter subclass for one sealed candidate."""

    parameters = dict(candidate.parameters)

    class SealedMutationAdapter(CombinedRepairV0):
        mutation_candidate = candidate
        mutation_engine_id = MUTATION_ENGINE_ID
        mutation_role = "paired_mutant"
        last_trigger_count = 0
        last_trigger_contexts: list[dict[str, Any]] = []

        @classmethod
        def reset_evidence(cls) -> None:
            cls.last_trigger_count = 0
            cls.last_trigger_contexts = []

        @classmethod
        def mutation_evidence(cls) -> dict[str, Any]:
            return {
                "candidate_id": candidate.candidate_id,
                "operator": candidate.operator,
                "trigger_count": cls.last_trigger_count,
                "trigger_contexts": list(cls.last_trigger_contexts[:8]),
            }

        def _mark_mutation(self, context: dict[str, Any]) -> None:
            type(self).last_trigger_count += 1
            if len(type(self).last_trigger_contexts) < 8:
                type(self).last_trigger_contexts.append(context)

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            type(self).reset_evidence()
            if candidate.operator == "mean_field_bypass_rejection":
                OpenSpielCompatibilityV0.__init__(self, *args, **kwargs)
                env = kwargs.get("env")
                if env is None and args:
                    env = args[0]
                if env is not None and self.config is None:
                    self.config = dict(env.get_parameters())
                self._mark_mutation({"dynamics": "mean_field"})
                return
            super().__init__(*args, **kwargs)

        def reset(self, seed: int | None = None, options: dict | None = None) -> None:
            if candidate.operator in {"config_replace", "config_drop"}:
                config = dict(self.config or self._env.get_parameters())
                key = str(parameters["key"])
                before = config.get(key)
                if candidate.operator == "config_replace":
                    config[key] = parameters["value"]
                else:
                    config.pop(key, None)
                self.config = config
                self._mark_mutation(
                    {"key": key, "before": before, "after": config.get(key)}
                )
            super().reset(seed=seed, options=options)
            if candidate.operator == "clock_reset_offset":
                offset = int(parameters["offset"])
                self.game_length += offset
                self._mark_mutation({"offset": offset})
            if candidate.operator in {"history_lag_one", "history_duplicate_last"}:
                self.game_state = _HistoryProxy(
                    self.game_state,
                    candidate.operator,
                    self._mark_mutation,
                )

        def _update_rewards(self) -> None:
            super()._update_rewards()
            if not candidate.operator.startswith("reward_"):
                return
            values = tuple(float(self.rewards.get(agent, 0.0)) for agent in self.agents)
            if not any(value != 0.0 for value in values):
                return
            if candidate.operator == "reward_scale":
                factor = float(parameters["factor"])
                self.rewards = {
                    agent: float(value) * factor
                    for agent, value in self.rewards.items()
                }
            elif candidate.operator == "reward_negate":
                self.rewards = {
                    agent: -float(value) for agent, value in self.rewards.items()
                }
            elif candidate.operator == "reward_rotate":
                agents = list(self.agents)
                rotated = values[1:] + values[:1]
                self.rewards = dict(zip(agents, rotated, strict=True))
            elif candidate.operator == "reward_drop":
                self.rewards = {agent: 0.0 for agent in self.agents}
            elif candidate.operator == "reward_offset":
                offset = float(parameters["offset"])
                first = self.agents[0]
                self.rewards[first] = float(self.rewards[first]) + offset
            elif candidate.operator == "reward_first_agent_only":
                self.rewards = {
                    agent: (float(self.rewards[agent]) if index == 0 else 0.0)
                    for index, agent in enumerate(self.agents)
                }
            else:
                raise RuntimeError(f"unsupported reward operator {candidate.operator}")
            self._mark_mutation(
                {"before": values, "after": tuple(self.rewards.values())}
            )

        def _update_action_masks(self) -> None:
            super()._update_action_masks()
            if candidate.operator not in {"action_mask_remove", "action_mask_add"}:
                return
            for agent in self.agents:
                mask = np.asarray(self.infos[agent]["action_mask"])
                if candidate.operator == "action_mask_remove":
                    legal = np.flatnonzero(mask)
                    if len(legal) >= 2:
                        mask[int(legal[0])] = 0
                        self._mark_mutation({"agent": agent, "action": int(legal[0])})
                        return
                else:
                    illegal = np.flatnonzero(mask == 0)
                    if len(illegal):
                        mask[int(illegal[0])] = 1
                        self._mark_mutation({"agent": agent, "action": int(illegal[0])})
                        return

        def _update_observations(self) -> None:
            super()._update_observations()
            operator = candidate.operator
            if operator not in {
                "observation_dtype_float32",
                "observation_swap_agents",
                "observation_list_container",
                "observation_value_offset",
            } or not getattr(self, "observations", None):
                return
            agents = list(self.observations)
            if operator == "observation_swap_agents" and len(agents) >= 2:
                left, right = agents[:2]
                self.observations[left], self.observations[right] = (
                    self.observations[right],
                    self.observations[left],
                )
                self._mark_mutation({"agents": [left, right]})
                return
            agent = agents[0]
            value = self.observations[agent]
            if not isinstance(value, np.ndarray):
                return
            if operator == "observation_dtype_float32":
                self.observations[agent] = value.astype(np.float32)
            elif operator == "observation_list_container":
                self.observations[agent] = value.tolist()
            elif operator == "observation_value_offset":
                changed = value.copy()
                changed.flat[0] += float(parameters["offset"])
                self.observations[agent] = changed
            self._mark_mutation({"agent": agent, "shape": tuple(value.shape)})

        def _execute_action_node(self, action: Any) -> None:
            simultaneous_before = bool(self.game_state.is_simultaneous_node())
            history_before = tuple(self.game_state.history())
            super()._execute_action_node(action)
            history_after = tuple(self.game_state.history())
            advanced = history_after != history_before
            operator = candidate.operator
            if (
                operator in {"clock_extra_on_advance", "clock_cancel_on_advance"}
                and advanced
            ):
                offset = int(parameters["offset"])
                self.game_length += offset
                self._mark_mutation({"offset": offset, "advanced": True})
            elif (
                operator == "clock_buffer_increment"
                and simultaneous_before
                and not advanced
            ):
                self.game_length += 1
                self._mark_mutation({"buffer_only": True})
            elif (
                operator == "simultaneous_forget_buffer"
                and simultaneous_before
                and not advanced
            ):
                self.simultaneous_actions = {}
                self._mark_mutation({"forgot_buffer": True})
            elif (
                operator == "simultaneous_prefill_next"
                and simultaneous_before
                and not advanced
            ):
                pending = [
                    agent
                    for agent in self.agents
                    if agent not in self.simultaneous_actions
                ]
                if pending:
                    self.simultaneous_actions[pending[0]] = action
                    self._mark_mutation(
                        {"prefilled_agent": pending[0], "action": int(action)}
                    )

        def _execute_chance_node(self) -> None:
            operator = candidate.operator
            if operator == "chance_unresolved" and self.game_state.is_chance_node():
                self._mark_mutation({"chance_left_unresolved": True})
                return
            if operator == "chance_one_only" and self.game_state.is_chance_node():
                outcomes = self.game_state.chance_outcomes()
                actions, probabilities = zip(*outcomes, strict=True)
                action = self.np_random.choice(actions, p=probabilities)
                self.game_state.apply_action(action)
                self._mark_mutation({"resolved_action": int(action)})
                return
            history_before = tuple(self.game_state.history())
            super()._execute_chance_node()
            if operator == "clock_chance_increment":
                count = len(tuple(self.game_state.history())) - len(history_before)
                if count:
                    self.game_length += count
                    self._mark_mutation({"chance_events": count})

        def _update_termination_truncation(self) -> None:
            super()._update_termination_truncation()
            operator = candidate.operator
            terminal = bool(self.game_state.is_terminal())
            if operator == "terminal_as_truncation" and terminal:
                self.terminations = {agent: False for agent in self.agents}
                self.truncations = {agent: True for agent in self.agents}
            elif operator == "suppress_terminal" and terminal:
                self.terminations = {agent: False for agent in self.agents}
                self.truncations = {agent: False for agent in self.agents}
            elif operator == "premature_termination" and not terminal:
                self.terminations = {agent: True for agent in self.agents}
            elif operator == "premature_truncation" and not terminal:
                self.truncations = {agent: True for agent in self.agents}
            elif (
                operator == "partial_terminal_flags"
                and terminal
                and len(self.agents) >= 2
            ):
                self.terminations = {
                    agent: index == 0 for index, agent in enumerate(self.agents)
                }
            else:
                return
            self._mark_mutation({"source_terminal": terminal})

        def _choose_next_agent(self) -> None:
            super()._choose_next_agent()
            if (
                candidate.operator == "clear_agents_at_terminal"
                and self.agents
                and self.game_state.is_terminal()
            ):
                self.agents = []
                self._mark_mutation({"cleared_agents": True})

    SealedMutationAdapter.__name__ = "SealedMutation_" + candidate.candidate_id.replace(
        "-", "_"
    )
    SealedMutationAdapter.__qualname__ = SealedMutationAdapter.__name__
    return SealedMutationAdapter


def candidate_manifest_records() -> tuple[dict[str, Any], ...]:
    """Return canonical records without constructing or stepping any game."""
    return tuple(candidate.to_manifest_record() for candidate in CANDIDATE_POOL)
