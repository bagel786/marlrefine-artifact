#!/usr/bin/env python3
"""Write the frozen OpenSpiel registry inventory as reproducible JSON."""

from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path

from marlrefine.census import registry_census
from marlrefine.provenance import project_file_identity, runtime_provenance
from marlrefine.serialization import write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/registry_census.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = registry_census()
    payload = {
        "schema_version": 1,
        "artifact_type": "openspiel_registry_census",
        "environment": runtime_provenance(),
        "study_manifest": project_file_identity("manifests/study_v1_draft.json"),
        "versions": {
            "open_spiel": version("open-spiel"),
            "shimmy": version("shimmy"),
            "pettingzoo": version("pettingzoo"),
        },
        "population_definition": "registered_games where default_loadable is true",
        "population_size": len(records),
        "records": [record.to_dict() for record in records],
    }
    write_json(args.output, payload)
    print(f"wrote {len(records)} records to {args.output}")


if __name__ == "__main__":
    main()
