"""Isolated candidate repairs for causal ablation experiments.

These subclasses are research treatments, not vendored replacements for
Shimmy. Each changes one diagnosed mechanism so the full matrix can distinguish
causal repair from coincidental trace suppression.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyspiel
from shimmy.openspiel_compatibility import OpenSpielCompatibilityV0

from marlrefine.capabilities import (
    UnsupportedCapability,
    UnsupportedCapabilityError,
)

MEAN_FIELD_DISTRIBUTION_CAPABILITY = UnsupportedCapability(
    capability_id="openspiel_mean_field_distribution_update_v1",
    adapter_id="shimmy.OpenSpielCompatibilityV0@2.0.1",
    reason=("Shimmy's OpenSpiel AEC adapter has no mean-field distribution protocol"),
)


class _RewardAccountingRepairMixin:
    """Refresh rewards only when a source transition actually advances."""

    _source_advanced: bool = False

    def _execute_action_node(self, action: int | np.integer[Any]) -> None:
        history_before = tuple(self.game_state.history())
        super()._execute_action_node(action)
        self._source_advanced = tuple(self.game_state.history()) != history_before

    def _update_rewards(self) -> None:
        if self._source_advanced:
            super()._update_rewards()
        else:
            self.rewards = {agent: 0.0 for agent in self.agents}

    def _end_routine(self) -> bool:
        ended = super()._end_routine()
        if ended:
            self.rewards = {agent: 0.0 for agent in self.agents}
        return ended


class _DecisionClockRepairMixin:
    """Count source player/joint decisions and exclude internal chance nodes."""

    def reset(self, seed: int | None = None, options: dict | None = None) -> None:
        super().reset(seed=seed, options=options)
        self.game_length = 0

    def _execute_chance_node(self) -> None:
        decision_count = self.game_length
        super()._execute_chance_node()
        self.game_length = decision_count


class _ConfigurationRepairMixin:
    """Retain parameters from the supplied prebuilt OpenSpiel game."""

    def __init__(
        self,
        env: pyspiel.Game | None = None,
        game_name: str | None = None,
        render_mode: str | None = None,
        config: dict | None = None,
    ) -> None:
        super().__init__(
            env=env,
            game_name=game_name,
            render_mode=render_mode,
            config=config,
        )
        if env is not None and config is None:
            self.config = dict(env.get_parameters())


class _MeanFieldFailFastRepairMixin:
    """Reject unsupported mean-field games instead of silently terminating."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        dynamics = str(getattr(self.game_type.dynamics, "name", "")).lower()
        if dynamics == "mean_field":
            raise UnsupportedCapabilityError(MEAN_FIELD_DISTRIBUTION_CAPABILITY)


class RewardAccountingRepairV0(
    _RewardAccountingRepairMixin,
    OpenSpielCompatibilityV0,
):
    """Treatment that repairs only buffer and cleanup reward replay."""


class DecisionClockRepairV0(
    _DecisionClockRepairMixin,
    OpenSpielCompatibilityV0,
):
    """Treatment that repairs only source-decision accounting."""


class ConfigurationRepairV0(
    _ConfigurationRepairMixin,
    OpenSpielCompatibilityV0,
):
    """Treatment that repairs only prebuilt-game configuration provenance."""


class MeanFieldFailFastRepairV0(
    _MeanFieldFailFastRepairMixin,
    OpenSpielCompatibilityV0,
):
    """Treatment that converts silent mean-field failure to explicit rejection."""


class CombinedRepairV0(
    _MeanFieldFailFastRepairMixin,
    _ConfigurationRepairMixin,
    _RewardAccountingRepairMixin,
    _DecisionClockRepairMixin,
    OpenSpielCompatibilityV0,
):
    """All four independent causal treatments composed for regression checks."""
