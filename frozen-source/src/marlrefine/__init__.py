"""MARLRefine: source-aligned conformance testing for MARL adapters."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("marlrefine")
except PackageNotFoundError:  # pragma: no cover - source-tree import
    __version__ = "0.1.0"

__all__ = ["__version__"]
