"""Typed declarations for adapter capabilities that are intentionally absent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnsupportedCapability:
    """Machine-readable identity for one explicitly unsupported capability."""

    capability_id: str
    adapter_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "capability_id": self.capability_id,
            "adapter_id": self.adapter_id,
            "reason": self.reason,
        }


class UnsupportedCapabilityError(NotImplementedError):
    """An expected, typed refusal rather than an unstructured adapter crash."""

    def __init__(self, capability: UnsupportedCapability) -> None:
        self.capability = capability
        super().__init__(capability.reason)

    def to_dict(self) -> dict[str, str]:
        return self.capability.to_dict()
