#!/usr/bin/env python3
"""Run destination API tests only on the pre-freeze discovery set."""

from pathlib import Path

from marlrefine.provenance import project_file_identity, runtime_provenance
from marlrefine.serialization import write_json
from marlrefine.stock_tests import run_stock_api_test
from marlrefine.study import DISCOVERY_GAME_NAMES


def main() -> None:
    results = tuple(run_stock_api_test(game_name) for game_name in DISCOVERY_GAME_NAMES)
    payload = {
        "schema_version": 1,
        "artifact_type": "pettingzoo_api_test_discovery_baseline",
        "environment": runtime_provenance(),
        "study_manifest": project_file_identity("manifests/study_v1_draft.json"),
        "population_role": "discovery_only",
        "results": results,
    }
    output = Path("artifacts/discovery_api_baselines.json")
    write_json(output, payload)
    passed = sum(result.passed for result in results)
    print(f"PettingZoo api_test: {passed}/{len(results)} discovery games passed")
    for result in results:
        status = "PASS" if result.passed else f"FAIL {result.exception}"
        print(f"  {result.game_spec}: {status}")


if __name__ == "__main__":
    main()
