"""Released destination-side tests used as explicit empirical baselines."""

from __future__ import annotations

import contextlib
import io
import warnings
from collections.abc import Callable
from dataclasses import dataclass

import pyspiel
from pettingzoo.test import api_test
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

from marlrefine.serialization import to_jsonable


@dataclass(frozen=True, slots=True)
class StockApiResult:
    game_spec: str
    cycles: int
    passed: bool
    exception: str | None
    warnings: tuple[str, ...]
    captured_output: str

    def to_dict(self):
        return to_jsonable(self)


def run_stock_api_test(
    game_spec: str,
    *,
    cycles: int = 1_000,
    seed: int = 0,
    adapter_class: type[OpenSpielCompatibilityV0] = OpenSpielCompatibilityV0,
    adapter_factory: Callable[[pyspiel.Game], OpenSpielCompatibilityV0] | None = None,
) -> StockApiResult:
    """Run PettingZoo's released API test against a selected adapter treatment.

    ``adapter_factory`` is available for mutation treatments that need a paired
    constructor.  Supplying it together with a nondefault ``adapter_class`` is
    rejected so that the executed treatment remains unambiguous.
    """
    if cycles <= 0:
        raise ValueError("cycles must be positive")
    if adapter_factory is not None and adapter_class is not OpenSpielCompatibilityV0:
        raise ValueError("supply either adapter_class or adapter_factory, not both")
    stream = io.StringIO()
    captured_warnings: tuple[str, ...] = ()
    exception: str | None = None
    env: OpenSpielCompatibilityV0 | None = None
    try:
        game = pyspiel.load_game(game_spec)
        env = (
            adapter_factory(game)
            if adapter_factory is not None
            else adapter_class(env=game)
        )
        for agent in env.possible_agents:
            env.action_space(agent).seed(seed)
        with warnings.catch_warnings(record=True) as warning_records:
            warnings.simplefilter("always")
            try:
                with (
                    contextlib.redirect_stdout(stream),
                    contextlib.redirect_stderr(stream),
                ):
                    api_test(env, num_cycles=cycles, verbose_progress=False)
            finally:
                captured_warnings = tuple(str(item.message) for item in warning_records)
    except Exception as exc:
        exception = f"{type(exc).__name__}:{exc}"
    finally:
        if env is not None:
            env.close()
    return StockApiResult(
        game_spec=game_spec,
        cycles=cycles,
        passed=exception is None,
        exception=exception,
        warnings=captured_warnings,
        captured_output=stream.getvalue(),
    )
