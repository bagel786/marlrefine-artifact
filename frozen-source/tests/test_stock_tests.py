from __future__ import annotations

import pytest

from marlrefine.stock_tests import run_stock_api_test


def test_stock_api_cycles_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        run_stock_api_test("nim", cycles=0)


def test_stock_api_accepts_adapter_class_without_loading_a_real_game(
    monkeypatch,
) -> None:
    calls: list[object] = []

    class Space:
        def seed(self, seed: int) -> None:
            calls.append(("seed", seed))

    class FakeAdapter:
        possible_agents = ("player_0",)

        def __init__(self, *, env) -> None:
            calls.append(("class", env))

        def action_space(self, agent: str) -> Space:
            calls.append(("space", agent))
            return Space()

        def close(self) -> None:
            calls.append("close")

    game = object()
    monkeypatch.setattr("marlrefine.stock_tests.pyspiel.load_game", lambda spec: game)
    monkeypatch.setattr("marlrefine.stock_tests.api_test", lambda *args, **kwargs: None)

    result = run_stock_api_test("sealed-fake", cycles=2, adapter_class=FakeAdapter)

    assert result.passed is True
    assert ("class", game) in calls
    assert "close" in calls


def test_stock_api_accepts_factory_and_rejects_ambiguous_treatment(
    monkeypatch,
) -> None:
    class FakeAdapter:
        possible_agents = ()

        def close(self) -> None:
            pass

    game = object()
    monkeypatch.setattr("marlrefine.stock_tests.pyspiel.load_game", lambda spec: game)
    monkeypatch.setattr("marlrefine.stock_tests.api_test", lambda *args, **kwargs: None)
    factory_calls: list[object] = []

    result = run_stock_api_test(
        "sealed-fake",
        cycles=2,
        adapter_factory=lambda observed: (
            factory_calls.append(observed) or FakeAdapter()
        ),
    )

    assert result.passed is True
    assert factory_calls == [game]
    with pytest.raises(ValueError, match="either"):
        run_stock_api_test(
            "sealed-fake",
            cycles=2,
            adapter_class=FakeAdapter,
            adapter_factory=lambda observed: FakeAdapter(),
        )


@pytest.mark.integration
def test_stock_api_baseline_passes_known_semantic_counterexample() -> None:
    result = run_stock_api_test("nim", cycles=100)
    assert result.passed is True
    assert result.exception is None
