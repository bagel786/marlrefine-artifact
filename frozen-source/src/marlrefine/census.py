"""Deterministic inventory of the pinned OpenSpiel registry.

The census deliberately records the full registry before behavioral examples are
selected.  This prevents a later evaluation from quietly redefining its sample
around whichever games happen to expose a defect.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import pyspiel
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0


def _enum_name(value: object) -> str:
    """Return a stable lowercase name for a pybind enum value."""
    name = getattr(value, "name", None)
    return str(name if name is not None else value).lower()


def _plain_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenSpiel parameter values to JSON-compatible primitives."""
    plain: dict[str, Any] = {}
    for key, value in sorted(parameters.items()):
        if isinstance(value, (bool, int, float, str)) or value is None:
            plain[key] = value
        else:
            plain[key] = str(value)
    return plain


def _task_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Remove the explicit reset seed from task-identity comparisons."""
    return {key: value for key, value in parameters.items() if key != "seed"}


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    """One preregistration record for a registry-marked default-loadable game."""

    short_name: str
    long_name: str
    dynamics: str
    chance_mode: str
    information: str
    reward_model: str
    utility: str
    min_players: int
    max_players: int
    parameters: dict[str, Any]
    provides_observation: bool
    provides_information_state: bool
    load_status: str
    adapter_construct_status: str
    adapter_reset_status: str
    configuration_preserved: bool | None
    source_parameters: dict[str, Any] | None
    reset_parameters: dict[str, Any] | None
    initial_node_kind: str | None
    num_players: int | None
    max_game_length: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def default_loadable_types() -> tuple[pyspiel.GameType, ...]:
    """Return the frozen registry population in stable name order."""
    return tuple(
        sorted(
            (
                game_type
                for game_type in pyspiel.registered_games()
                if game_type.default_loadable
            ),
            key=lambda game_type: game_type.short_name,
        )
    )


def build_registry_record(game_type: pyspiel.GameType) -> RegistryRecord:
    """Load and adapter-reset one registry entry while retaining all failures."""
    common = {
        "short_name": game_type.short_name,
        "long_name": game_type.long_name,
        "dynamics": _enum_name(game_type.dynamics),
        "chance_mode": _enum_name(game_type.chance_mode),
        "information": _enum_name(game_type.information),
        "reward_model": _enum_name(game_type.reward_model),
        "utility": _enum_name(game_type.utility),
        "min_players": int(game_type.min_num_players),
        "max_players": int(game_type.max_num_players),
        "parameters": _plain_parameters(game_type.parameter_specification),
        "provides_observation": bool(
            game_type.provides_observation_tensor
            or game_type.provides_observation_string
        ),
        "provides_information_state": bool(
            game_type.provides_information_state_tensor
            or game_type.provides_information_state_string
        ),
    }

    try:
        game = pyspiel.load_game(game_type.short_name)
    except Exception as exc:  # registry census must retain, rather than hide, failures
        return RegistryRecord(
            **common,
            load_status=f"error:{type(exc).__name__}:{exc}",
            adapter_construct_status="not_run",
            adapter_reset_status="not_run",
            configuration_preserved=None,
            source_parameters=None,
            reset_parameters=None,
            initial_node_kind=None,
            num_players=None,
            max_game_length=None,
        )

    state = game.new_initial_state()
    # Some OpenSpiel games materialize default parameters lazily when their
    # first state is created.  Snapshot the resolved identity afterwards.
    source_parameters = _plain_parameters(game.get_parameters())
    try:
        max_game_length: int | None = int(game.max_game_length())
    except Exception:
        max_game_length = None

    try:
        adapter = OpenSpielCompatibilityV0(env=game)
    except Exception as exc:
        return RegistryRecord(
            **common,
            load_status="pass",
            adapter_construct_status=f"error:{type(exc).__name__}:{exc}",
            adapter_reset_status="not_run",
            configuration_preserved=None,
            source_parameters=source_parameters,
            reset_parameters=None,
            initial_node_kind=_node_kind(state),
            num_players=int(game.num_players()),
            max_game_length=max_game_length,
        )

    try:
        # A concrete seed makes construction/reset outcomes reproducible. The
        # seed parameter itself is recorded but excluded from task identity.
        adapter.reset(seed=0)
    except Exception as exc:
        return RegistryRecord(
            **common,
            load_status="pass",
            adapter_construct_status="pass",
            adapter_reset_status=f"error:{type(exc).__name__}:{exc}",
            configuration_preserved=None,
            source_parameters=source_parameters,
            reset_parameters=None,
            initial_node_kind=_node_kind(state),
            num_players=int(game.num_players()),
            max_game_length=max_game_length,
        )

    adapter_parameters = _plain_parameters(adapter._env.get_parameters())
    return RegistryRecord(
        **common,
        load_status="pass",
        adapter_construct_status="pass",
        adapter_reset_status="pass",
        configuration_preserved=(
            _task_parameters(source_parameters) == _task_parameters(adapter_parameters)
        ),
        source_parameters=source_parameters,
        reset_parameters=adapter_parameters,
        initial_node_kind=_node_kind(adapter.game_state),
        num_players=int(game.num_players()),
        max_game_length=max_game_length,
    )


def registry_census(
    game_types: Iterable[pyspiel.GameType] | None = None,
) -> tuple[RegistryRecord, ...]:
    """Evaluate the full pinned population or an explicitly supplied subset."""
    population = default_loadable_types() if game_types is None else tuple(game_types)
    return tuple(build_registry_record(game_type) for game_type in population)
