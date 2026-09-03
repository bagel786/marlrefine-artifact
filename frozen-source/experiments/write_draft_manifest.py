#!/usr/bin/env python3
"""Write a draft or immutable freeze-candidate manifest without semantic traces."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from marlrefine.mutation_study import (
    MUTATION_MANIFEST_SCHEMA_VERSION,
    build_mutation_manifest,
)
from marlrefine.mutations import (
    CANDIDATE_POOL,
    MUTANTS_PER_FAMILY,
    MUTATION_ENGINE_ID,
    MUTATION_FAMILIES,
    MUTATION_PROTOCOL_ID,
    POOL_PER_FAMILY,
    PROGRESS_INSTRUMENTATION_CONTROLS,
    candidate_manifest_records,
    mutation_engine_source_sha256,
)
from marlrefine.provenance import runtime_provenance
from marlrefine.serialization import write_json
from marlrefine.study import (
    MANIFEST_STATUSES,
    MUTATION_MANIFEST_PATH,
    build_draft_study_manifest,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read sealed mutation manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("sealed mutation manifest must contain one JSON object")
    return value


def _mutation_manifest_binding(
    path: Path,
    *,
    source_revision: str,
    study_environment: dict[str, Any],
) -> str:
    """Validate the first-generated mutation manifest and return its byte hash."""
    payload = _read_object(path)
    if (
        payload.get("schema_version") != MUTATION_MANIFEST_SCHEMA_VERSION
        or payload.get("artifact_type")
        != "marlrefine_sealed_mutation_manifest"
        or payload.get("manifest_status") != "frozen_pending_archive"
        or payload.get("protocol_id") != MUTATION_PROTOCOL_ID
    ):
        raise ValueError("sealed mutation manifest identity is invalid")
    prearchive = payload.get("prearchive_activity")
    if (
        not isinstance(prearchive, dict)
        or prearchive.get("candidate_or_control_outcomes_executed") != 0
    ):
        raise ValueError("sealed mutation manifest records prearchive execution")

    selection = payload.get("selection")
    if not isinstance(selection, dict) or selection.get("families") != list(
        MUTATION_FAMILIES
    ):
        raise ValueError("sealed mutation manifest family order is invalid")
    expected_selection = {
        "required_eligible_per_family": MUTANTS_PER_FAMILY,
        "candidate_pool_per_family": POOL_PER_FAMILY,
        "required_total": len(MUTATION_FAMILIES) * MUTANTS_PER_FAMILY,
        "candidate_pool_total": len(CANDIDATE_POOL),
    }
    if any(selection.get(key) != value for key, value in expected_selection.items()):
        raise ValueError("sealed mutation manifest selection counts are invalid")
    candidates = payload.get("candidates")
    if candidates != list(candidate_manifest_records()):
        raise ValueError("sealed mutation manifest candidate pool differs from code")
    expected_controls = [
        control.to_manifest_record() for control in PROGRESS_INSTRUMENTATION_CONTROLS
    ]
    if payload.get("progress_instrumentation_controls") != expected_controls:
        raise ValueError("sealed mutation manifest progress controls differ from code")
    if payload.get("mutation_engine") != {
        "engine_id": MUTATION_ENGINE_ID,
        "source_module": "src/marlrefine/mutations.py",
        "source_sha256": mutation_engine_source_sha256(),
    }:
        raise ValueError("sealed mutation engine identity differs from code")
    expected_contract = build_mutation_manifest(
        manifest_status="draft_not_timestamp_archived"
    )
    for field in (
        "selection",
        "reference_adapter",
        "execution",
        "scoring",
        "prearchive_activity",
    ):
        if payload.get(field) != expected_contract[field]:
            raise ValueError(f"sealed mutation {field} contract differs from code")

    mutation_environment = payload.get("environment")
    if not isinstance(mutation_environment, dict):
        raise ValueError("sealed mutation manifest environment is missing")
    if (
        mutation_environment.get("git_revision") != source_revision
        or mutation_environment.get("git_dirty") is not False
    ):
        raise ValueError("sealed mutation manifest is not bound to source commit A")
    stable_environment_fields = (
        "python",
        "packages",
        "installed_distribution_sha256",
        "uv_lock_sha256",
        "source_tree_sha256",
    )
    if any(
        mutation_environment.get(field) != study_environment.get(field)
        for field in stable_environment_fields
    ):
        raise ValueError(
            "sealed mutation manifest environment differs from the study environment"
        )
    return _sha256(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status",
        choices=MANIFEST_STATUSES,
        default="draft_not_timestamp_archived",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("manifests/study_v1_draft.json"),
    )
    parser.add_argument(
        "--source-git-revision",
        help=(
            "clean source commit A recorded when generating a frozen manifest "
            "inside the pinned container"
        ),
    )
    parser.add_argument(
        "--mutation-manifest",
        type=Path,
        default=Path(MUTATION_MANIFEST_PATH),
        help=(
            "sealed mutation manifest generated first; mandatory for a frozen "
            "study manifest"
        ),
    )
    args = parser.parse_args()

    source_revision = args.source_git_revision
    if source_revision is not None and not re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source_revision
    ):
        parser.error("--source-git-revision must be a lowercase Git object ID")
    if args.status == "frozen_pending_archive" and source_revision is None:
        parser.error("frozen manifests require --source-git-revision for commit A")

    environment = runtime_provenance()
    if source_revision is not None:
        environment["git_revision"] = source_revision
        environment["git_dirty"] = False
    mutation_manifest_sha256 = None
    if args.status == "frozen_pending_archive":
        assert source_revision is not None
        try:
            mutation_manifest_sha256 = _mutation_manifest_binding(
                args.mutation_manifest,
                source_revision=source_revision,
                study_environment=environment,
            )
        except ValueError as exc:
            parser.error(str(exc))
    payload = build_draft_study_manifest(
        manifest_status=args.status,
        mutation_manifest_sha256=mutation_manifest_sha256,
    )
    payload["environment"] = environment
    write_json(args.output, payload)
    print(
        f"wrote {args.status} manifest to {args.output}: "
        f"{payload['population']['size']} population, "
        f"{payload['discovery']['size']} discovery, "
        f"{payload['validation']['semantic_cohort']['size']} prospective semantic, "
        f"{payload['validation']['descriptive_exclusions']['size']} known "
        "descriptive exclusion; mutation manifest "
        f"sha256={mutation_manifest_sha256 or 'unbound-draft'}"
    )


if __name__ == "__main__":
    main()
