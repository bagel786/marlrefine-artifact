from __future__ import annotations

import pytest

from marlrefine.adapters.openspiel_shimmy import PROTOCOL_VERSION, run_trace
from marlrefine.capabilities import (
    UnsupportedCapability,
    UnsupportedCapabilityError,
)
from marlrefine.repairs import MEAN_FIELD_DISTRIBUTION_CAPABILITY


def test_unsupported_capability_is_typed_and_machine_readable() -> None:
    capability = UnsupportedCapability(
        capability_id="example_v1",
        adapter_id="example.Adapter@1",
        reason="deliberately unsupported",
    )
    error = UnsupportedCapabilityError(capability)

    assert isinstance(error, NotImplementedError)
    assert str(error) == "deliberately unsupported"
    assert error.to_dict() == {
        "capability_id": "example_v1",
        "adapter_id": "example.Adapter@1",
        "reason": "deliberately unsupported",
    }


def test_mean_field_capability_and_runner_protocol_are_frozen() -> None:
    assert MEAN_FIELD_DISTRIBUTION_CAPABILITY.capability_id == (
        "openspiel_mean_field_distribution_update_v1"
    )
    assert PROTOCOL_VERSION == "1.1-prerun-final-2026-09-01"


def test_progress_control_seam_requires_a_manifest_bound_id_before_game_use() -> None:
    with pytest.raises(ValueError, match="supplied together"):
        run_trace(
            "must-not-be-loaded",
            progress_annotation_transform=lambda index, before, after: after,
        )
