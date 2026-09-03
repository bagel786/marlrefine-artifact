#!/usr/bin/env python3
"""Write an explicit, non-preregistered local execution authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marlrefine.provenance import runtime_provenance
from marlrefine.serialization import write_json

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _read_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("study manifest must contain one JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite an execution authorization")

    try:
        manifest, manifest_sha256 = _read_manifest(args.manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    if manifest.get("schema_version") != 2:
        parser.error("local authorization requires study manifest schema 2")
    if manifest.get("manifest_status") != "frozen_pending_archive":
        parser.error("local authorization requires a frozen manifest")
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        parser.error("manifest environment is missing")
    source_tree_sha256 = environment.get("source_tree_sha256")
    uv_lock_sha256 = environment.get("uv_lock_sha256")
    source_git_revision = environment.get("git_revision")
    if (
        not isinstance(source_tree_sha256, str)
        or not SHA256_PATTERN.fullmatch(source_tree_sha256)
        or not isinstance(uv_lock_sha256, str)
        or not SHA256_PATTERN.fullmatch(uv_lock_sha256)
        or not isinstance(source_git_revision, str)
        or not GIT_REVISION_PATTERN.fullmatch(source_git_revision)
        or environment.get("git_dirty") is not False
    ):
        parser.error("manifest is not bound to a clean source revision")

    runtime = runtime_provenance()
    if runtime.get("source_tree_sha256") != source_tree_sha256:
        parser.error("current executable source differs from the frozen manifest")
    if runtime.get("uv_lock_sha256") != uv_lock_sha256:
        parser.error("current dependency lock differs from the frozen manifest")

    payload = {
        "schema_version": 1,
        "artifact_type": "marlrefine_local_execution_authorization",
        "manifest_sha256": manifest_sha256,
        "source_tree_sha256": source_tree_sha256,
        "uv_lock_sha256": uv_lock_sha256,
        "authorized_at_utc": datetime.now(UTC).isoformat(),
        "authorization_id": f"local-unregistered:{manifest_sha256}",
        "source_git_revision": source_git_revision,
        "preregistered": False,
        "public_archive": False,
    }
    write_json(args.output, payload)
    print(
        "wrote local, non-preregistered execution authorization: "
        f"{args.output}; authorization_id={payload['authorization_id']}"
    )


if __name__ == "__main__":
    main()
