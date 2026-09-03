from __future__ import annotations

import pyspiel
import pytest

from marlrefine.census import build_registry_record, default_loadable_types


def test_default_loadable_population_is_stable_for_pinned_release() -> None:
    population = default_loadable_types()
    assert len(population) == 113
    assert [game.short_name for game in population] == sorted(
        game.short_name for game in population
    )


@pytest.mark.integration
def test_nim_registry_record_loads_and_resets() -> None:
    game_type = next(
        game for game in pyspiel.registered_games() if game.short_name == "nim"
    )
    record = build_registry_record(game_type)
    assert record.load_status == "pass"
    assert record.adapter_construct_status == "pass"
    assert record.adapter_reset_status == "pass"
    assert record.configuration_preserved is True
    assert record.initial_node_kind == "decision"
