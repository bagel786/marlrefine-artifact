#!/usr/bin/env python3
"""Run the execution-authorization-gated sealed mutation cohort."""

from __future__ import annotations

import argparse
from pathlib import Path

from marlrefine.mutation_study import execute_mutation_study


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("manifests/study_v1_draft.json"),
    )
    parser.add_argument(
        "--mutation-manifest",
        type=Path,
        default=Path("manifests/mutation_v1.json"),
    )
    authorization = parser.add_mutually_exclusive_group(required=True)
    authorization.add_argument(
        "--execution-authorization", dest="archive_receipt", type=Path
    )
    authorization.add_argument(
        "--archive-receipt", dest="archive_receipt", type=Path,
        help="public Zenodo receipt (legacy explicit spelling)",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = execute_mutation_study(
        args.manifest,
        args.mutation_manifest,
        args.archive_receipt,
        args.output,
    )
    score = payload["score"]
    complete = payload["selection"]["complete"]
    print(
        f"sealed mutation batch: selected={score['selected_total']} "
        f"killed={score['killed_total']} complete={complete} output={args.output}"
    )
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
