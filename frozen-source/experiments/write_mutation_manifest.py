#!/usr/bin/env python3
"""Generate the sealed mutation candidate manifest without executing a game."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from marlrefine.mutation_study import build_mutation_manifest
from marlrefine.serialization import write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status",
        choices=("draft_not_timestamp_archived", "frozen_pending_archive"),
        default="draft_not_timestamp_archived",
    )
    parser.add_argument(
        "--source-git-revision",
        help="clean source commit A for a frozen candidate manifest",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("manifests/mutation_v1.json"),
    )
    args = parser.parse_args()
    if args.source_git_revision is not None and not re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", args.source_git_revision
    ):
        parser.error("--source-git-revision must be a lowercase Git object ID")
    if args.status == "frozen_pending_archive" and args.source_git_revision is None:
        parser.error("frozen mutation manifests require --source-git-revision")
    payload = build_mutation_manifest(
        manifest_status=args.status,
        source_git_revision=args.source_git_revision,
    )
    write_json(args.output, payload)
    print(
        f"wrote {len(payload['candidates'])} unexecuted candidates to "
        f"{args.output}; prearchive_execution_count=0"
    )


if __name__ == "__main__":
    main()
