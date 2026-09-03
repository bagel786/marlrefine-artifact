#!/usr/bin/env python3
"""Generate the pre-freeze clean semantic-control artifact."""

from __future__ import annotations

from pathlib import Path

from marlrefine.controls import run_discovery_semantic_controls
from marlrefine.provenance import project_file_identity, runtime_provenance
from marlrefine.serialization import write_json


def main() -> None:
    runs = run_discovery_semantic_controls()
    payload = {
        "schema_version": 1,
        "artifact_type": "marlrefine_discovery_semantic_controls",
        "environment": runtime_provenance(),
        "study_manifest": project_file_identity("manifests/study_v1_draft.json"),
        "scope_warning": (
            "These are clean controls over predeclared discovery games or a fully "
            "synthetic fixture. They are not prospective validation results."
        ),
        "alarm_policy": (
            "Every control alarm is a failing result; there is no expected-alarm "
            "allowlist."
        ),
        "runs": runs,
        "all_passed": all(run.passed for run in runs),
    }
    output = Path("artifacts/discovery_controls.json")
    write_json(output, payload)
    for run in runs:
        status = "PASS" if run.passed else "FAIL"
        print(f"{run.control_id}: {status} ({len(run.violations)} alarms)")
    if not payload["all_passed"]:
        raise SystemExit("one or more clean semantic controls raised an alarm")


if __name__ == "__main__":
    main()
