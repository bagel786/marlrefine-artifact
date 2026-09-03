#!/usr/bin/env python3
"""Validate and analyze a sealed prospective batch without executing games."""

from __future__ import annotations

import argparse
from pathlib import Path

from marlrefine.analysis import write_analysis_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen 105-game/840-trace JSONL batch and emit "
            "canonical descriptive results plus LaTeX macros."
        )
    )
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    authorization = parser.add_mutually_exclusive_group(required=True)
    authorization.add_argument(
        "--execution-authorization", dest="archive_receipt", type=Path
    )
    authorization.add_argument(
        "--archive-receipt", dest="archive_receipt", type=Path,
        help="public Zenodo receipt (legacy explicit spelling)",
    )
    parser.add_argument(
        "--external-baselines",
        type=Path,
        help=(
            "exact authorization-gated external-baseline artifact; required with a "
            "complete manual adjudication"
        ),
    )
    parser.add_argument(
        "--mutation-batch",
        type=Path,
        help=(
            "exact validated sealed-mutation artifact; required with a complete "
            "manual adjudication"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--latex-output", type=Path, required=True)
    parser.add_argument(
        "--manual-adjudication",
        type=Path,
        help=(
            "optional batch-bound manual root/repair/control/upstream inputs; "
            "symptom counts are never substituted"
        ),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    analysis = write_analysis_artifacts(
        args.batch,
        args.manifest,
        args.archive_receipt,
        args.output,
        args.latex_output,
        manual_adjudication_path=args.manual_adjudication,
        external_baseline_path=args.external_baselines,
        mutation_batch_path=args.mutation_batch,
    )
    traces = analysis["trace_level_accounting"]["counts"]
    games = analysis["game_level_accounting"]
    print(
        f"validated {traces['scheduled']} scheduled traces over "
        f"{games['population']} distinct games; "
        f"analysis={args.output}; latex={args.latex_output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
