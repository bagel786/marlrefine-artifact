"""Self-describing runtime provenance for generated evidence artifacts."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from typing import Any

PINNED_PACKAGES = (
    "marlrefine",
    "shimmy",
    "open-spiel",
    "pettingzoo",
    "gymnasium",
    "numpy",
)
BYTE_BOUND_DISTRIBUTIONS = tuple(
    package for package in PINNED_PACKAGES if package != "marlrefine"
)

SOURCE_ROOT_FILES = (
    ".dockerignore",
    ".gitignore",
    "CITATION.cff",
    "Dockerfile",
    "LICENSE",
    "LICENSE-docs-data",
    "README.md",
    "adapter_refinement_pilot.py",
    "pyproject.toml",
    "uv.lock",
)
SOURCE_DIRECTORIES = (
    "container",
    "deposit",
    "docs",
    "experiments",
    "manifests",
    "src",
    "tests",
)
SOURCE_SUFFIXES = frozenset(
    {".cff", ".json", ".md", ".py", ".toml", ".yaml", ".yml"}
)
EXCLUDED_SOURCE_PATHS = frozenset(
    {
        "container/IMAGE_IDENTITY.json",
        "deposit/archive_receipt.json",
        "deposit/protocol_identity.json",
        "docs/journal_fit_novelty_audit.md",
        "docs/paper_outline.md",
        "docs/related_work.md",
        "manifests/mutation_v1.json",
        "manifests/study_v1_draft.json",
    }
)


def _project_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else None


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_revision(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _git_dirty(root: Path) -> bool | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout) if result.returncode == 0 else None


def source_identity_paths(root: Path) -> tuple[Path, ...]:
    """Return executable/protocol inputs covered by the project source identity.

    Manuscript sources and build outputs are publication artifacts, not inputs to
    the frozen executable study.  The entire ``paper/`` tree is therefore outside
    this identity as well as the matching container and protocol bundle scopes.
    """
    paths = {root / name for name in SOURCE_ROOT_FILES if (root / name).is_file()}
    for directory_name in SOURCE_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir():
            continue
        paths.update(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix in SOURCE_SUFFIXES
            and "__pycache__" not in path.parts
            and path.relative_to(root).as_posix() not in EXCLUDED_SOURCE_PATHS
        )
    return tuple(sorted(paths, key=lambda item: item.relative_to(root).as_posix()))


def _source_tree_sha256(root: Path) -> str:
    """Hash every executable/protocol source file in a canonical order.

    Generated manifests and artifacts are deliberately excluded so an artifact
    never hashes itself. Their generators and the protocol documents are
    included.
    """
    digest = hashlib.sha256()
    for path in source_identity_paths(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _package_tree_sha256(package_root: Path) -> str:
    """Hash installed Python sources without depending on the process CWD."""
    digest = hashlib.sha256()
    paths = sorted(
        (path for path in package_root.rglob("*.py") if path.is_file()),
        key=lambda item: item.relative_to(package_root).as_posix(),
    )
    for path in paths:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _distribution_tree_sha256(package: str) -> str | None:
    """Hash every installed byte declared by a third-party distribution.

    Versions and the resolver lock are insufficient to detect a locally
    patched wheel.  Reading the files named by installed metadata binds Python
    sources, native extensions, package data, and console scripts to the
    frozen environment.  Missing declared files receive an explicit marker so
    deletion also changes the identity.
    """
    try:
        package_distribution = distribution(package)
    except PackageNotFoundError:
        return None
    files = package_distribution.files
    if files is None:
        return None

    digest = hashlib.sha256()
    for declared_path in sorted(files, key=lambda item: item.as_posix()):
        relative = declared_path.as_posix().encode("utf-8")
        path = Path(package_distribution.locate_file(declared_path))
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        if not path.is_file():
            digest.update(b"\x00missing")
            continue
        digest.update(b"\x01present")
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(block)
        digest.update(file_digest.digest())
    return digest.hexdigest()


def project_file_identity(relative_path: str) -> dict[str, str | None]:
    """Identify a project file without embedding a machine-specific path."""
    root = _project_root()
    return {
        "path": relative_path,
        "sha256": _sha256(root / relative_path) if root is not None else None,
    }


def runtime_provenance() -> dict[str, Any]:
    """Return versions, platform, lock identity, and executable-code identity."""
    root = _project_root()
    package_root = Path(__file__).resolve().parent
    packages: dict[str, str | None] = {}
    for package in PINNED_PACKAGES:
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    installed_distribution_sha256 = {
        package: _distribution_tree_sha256(package)
        for package in BYTE_BOUND_DISTRIBUTIONS
    }
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable_name": Path(sys.executable).name,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "packages": packages,
        "installed_distribution_sha256": installed_distribution_sha256,
        "uv_lock_sha256": _sha256(root / "uv.lock") if root is not None else None,
        "source_identity_scope": "project_tree" if root is not None else "package_tree",
        "source_tree_sha256": (
            _source_tree_sha256(root)
            if root is not None
            else _package_tree_sha256(package_root)
        ),
        "git_revision": _git_revision(root) if root is not None else None,
        "git_dirty": _git_dirty(root) if root is not None else None,
    }
