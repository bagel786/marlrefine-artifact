#!/usr/bin/env python3
"""Verify and deterministically recompute the released MARLRefine analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXPECTED_REVISION = "640b1d89174e44fa7a0440f1d4d296e316cc5b41"
EXPECTED_SOURCE_SHA256 = (
    "c7f1edca01663dd8e918ab02c1f08c3ec901b428a35bfc160db8e1c4f066b1cd"
)
EXPECTED_LOCK_SHA256 = (
    "3ba28d4b28623ad7394f6b0474b28780ed52dbbf66817a8babd2629a086ce29a"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} differs: expected {expected!r}, got {actual!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()

    repository = Path(__file__).resolve().parent
    source = repository / "frozen-source"
    results = args.results.resolve()
    sys.path.insert(0, str(source / "src"))

    from marlrefine.analysis import analyze_prospective_batch, latex_result_macros
    from marlrefine.provenance import _source_tree_sha256

    require_equal(
        _source_tree_sha256(source), EXPECTED_SOURCE_SHA256, "source-tree SHA-256"
    )
    require_equal(sha256(source / "uv.lock"), EXPECTED_LOCK_SHA256, "lock SHA-256")

    manifest = results / "manifests" / "study_v1_draft.json"
    mutation_manifest = results / "manifests" / "mutation_v1.json"
    raw = results / "output" / "prospective_raw.jsonl"
    authorization = results / "output" / "local_execution_authorization.json"
    baselines = results / "output" / "external_baselines.json"
    mutations = results / "output" / "mutation_batch.json"
    adjudication = results / "output" / "manual_adjudication.json"
    frozen_analysis_path = results / "output" / "frozen_analysis.json"
    macros_path = results / "output" / "results_macros.tex"

    analysis = load_json(frozen_analysis_path)
    identities = analysis["input_identities"]
    expected_files = {
        manifest: identities["manifest"]["sha256"],
        raw: identities["raw_batch"]["sha256"],
        authorization: identities["archive_receipt"]["sha256"],
        baselines: identities["external_baselines"]["sha256"],
        mutations: identities["mutation_batch"]["sha256"],
        adjudication: identities["manual_adjudication_sha256"],
    }
    for path, expected in expected_files.items():
        require_equal(sha256(path), expected, f"SHA-256 for {path.name}")

    manifest_value = load_json(manifest)
    mutation_manifest_value = load_json(mutation_manifest)
    for label, value in (
        ("study manifest", manifest_value),
        ("mutation manifest", mutation_manifest_value),
    ):
        environment = value["environment"]
        require_equal(environment["git_revision"], EXPECTED_REVISION, f"{label} revision")
        require_equal(
            environment["source_tree_sha256"], EXPECTED_SOURCE_SHA256, f"{label} source"
        )
        require_equal(
            environment["uv_lock_sha256"], EXPECTED_LOCK_SHA256, f"{label} lock"
        )

    recomputed = analyze_prospective_batch(
        raw,
        manifest,
        authorization,
        manual_adjudication_path=adjudication,
        external_baseline_path=baselines,
        mutation_batch_path=mutations,
    )
    packaged = dict(analysis)
    packaged.pop("analysis_runtime", None)
    require_equal(recomputed, packaged, "recomputed frozen analysis")
    require_equal(
        latex_result_macros(recomputed),
        macros_path.read_text(encoding="utf-8"),
        "generated LaTeX result macros",
    )

    print("verified MARLRefine study artifact")
    print(f"source_tree_sha256={EXPECTED_SOURCE_SHA256}")
    print(f"raw_batch_sha256={identities['raw_batch']['sha256']}")
    print(f"analysis_sha256={sha256(frozen_analysis_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
