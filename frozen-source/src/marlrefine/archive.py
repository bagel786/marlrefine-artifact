"""Canonical identity records for the immutable protocol deposit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def protocol_freeze_identity(
    *,
    manifest_sha256: str,
    source_tree_sha256: str,
    uv_lock_sha256: str,
    bundle_filename: str,
    bundle_sha256: str,
) -> dict[str, Any]:
    """Build the exact separate identity object verified by the run gate."""
    return {
        "artifact_type": "marlrefine_protocol_freeze_identity",
        "manifest_sha256": manifest_sha256,
        "protocol_bundle": {
            "filename": bundle_filename,
            "sha256": bundle_sha256,
        },
        "schema_version": 1,
        "source_tree_sha256": source_tree_sha256,
        "uv_lock_sha256": uv_lock_sha256,
    }


def two_commit_freeze_identity(
    manifest_environment: Mapping[str, Any],
    *,
    source_parent_revision: str,
    archive_revision: str,
    changed_paths: Sequence[str],
    allowed_generated_paths: frozenset[str],
    required_manifest_path: str,
) -> dict[str, Any]:
    """Validate the non-recursive source/evidence commit relationship."""
    source_revision = manifest_environment.get("git_revision")
    if not isinstance(source_revision, str) or not source_revision:
        raise ValueError("study manifest has no source Git revision")
    if manifest_environment.get("git_dirty") is not False:
        raise ValueError("study manifest was not generated from a clean source commit")
    if source_revision != source_parent_revision:
        raise ValueError(
            "freeze requires exactly one generated-evidence commit after the "
            "manifest's source commit"
        )
    canonical_paths = tuple(sorted(changed_paths))
    unexpected_paths = sorted(set(canonical_paths).difference(allowed_generated_paths))
    if unexpected_paths:
        raise ValueError(
            "generated-evidence commit changes source-controlled paths: "
            f"{unexpected_paths}"
        )
    if required_manifest_path not in canonical_paths:
        raise ValueError("generated-evidence commit does not contain the manifest")
    return {
        "git_identity_model": "two_commit_nonrecursive_v1",
        "source_git_revision": source_revision,
        "archive_git_revision": archive_revision,
        "generated_evidence_paths": canonical_paths,
    }
