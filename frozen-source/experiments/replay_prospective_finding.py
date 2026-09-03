#!/usr/bin/env python3
"""Generate one authorization-gated standalone prospective replay artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from marlrefine.replay import replay_prospective_finding


def main() -> int:
    parser = argparse.ArgumentParser()
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
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--violation-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = replay_prospective_finding(
        args.batch,
        args.manifest,
        args.archive_receipt,
        case_id=args.case_id,
        violation_index=args.violation_index,
        output_path=args.output,
    )
    print(
        f"standalone replay {payload['status']}: "
        f"{args.case_id} violation {args.violation_index} -> {args.output}"
    )
    return 0 if payload["status"] == "reproduced" else 2


if __name__ == "__main__":
    raise SystemExit(main())
