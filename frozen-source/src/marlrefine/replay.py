"""Archive-gated standalone replay evidence for a prospective finding."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from marlrefine.adapters.openspiel_shimmy import run_trace
from marlrefine.analysis import analyze_prospective_batch
from marlrefine.localization import LOCALIZER_ID, localize_divergence
from marlrefine.prospective import build_prospective_plan
from marlrefine.serialization import to_jsonable, write_json

REPLAY_SCHEMA_VERSION = 1
REPLAY_ARTIFACT_TYPE = "marlrefine_standalone_prospective_replay"


class ReplayEvidenceError(RuntimeError):
    """The requested finding cannot be replayed from sealed evidence."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        to_jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _case_record(batch_path: Path, case_id: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    with batch_path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReplayEvidenceError(
                    f"invalid sealed batch JSON at line {line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise ReplayEvidenceError(
                    f"sealed batch line {line_number} is not an object"
                )
            case = value.get("case")
            if isinstance(case, Mapping) and case.get("case_id") == case_id:
                matches.append(value)
    if len(matches) != 1:
        raise ReplayEvidenceError(
            f"expected one sealed record for {case_id!r}, found {len(matches)}"
        )
    return matches[0]


def _analysis_witness(
    analysis: Mapping[str, Any], case_id: str, violation_index: int
) -> dict[str, Any]:
    localization = analysis.get("witness_localization")
    if not isinstance(localization, Mapping):
        raise ReplayEvidenceError("analysis has no witness localization")
    matches = [
        item
        for item in localization.get("witnesses", [])
        if isinstance(item, Mapping)
        and item.get("case_id") == case_id
        and item.get("violation_index") == violation_index
    ]
    if len(matches) != 1:
        raise ReplayEvidenceError(
            "requested finding does not have exactly one frozen witness"
        )
    return dict(matches[0])


def replay_prospective_finding(
    batch_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    *,
    case_id: str,
    violation_index: int,
    output_path: Path,
) -> dict[str, Any]:
    """Re-execute one exact frozen case and bind the reproduction criteria."""
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite {output_path}")
    if isinstance(violation_index, bool) or violation_index < 0:
        raise ValueError("violation_index must be a nonnegative integer")

    # This validates the complete raw batch and public archive identities before
    # any case is re-executed. It never turns a malformed record into evidence.
    analysis = analyze_prospective_batch(batch_path, manifest_path, receipt_path)
    plan = build_prospective_plan(manifest_path, receipt_path)
    planned = [case for case in plan.cases if case.case_id == case_id]
    if len(planned) != 1:
        raise ReplayEvidenceError(f"case {case_id!r} is not in the frozen plan")
    case = planned[0]
    record = _case_record(batch_path, case_id)
    original_run = record.get("run")
    if not isinstance(original_run, Mapping):
        raise ReplayEvidenceError("requested case has no serialized run payload")
    original_violations = original_run.get("violations")
    if not isinstance(original_violations, list) or violation_index >= len(
        original_violations
    ):
        raise ReplayEvidenceError("violation_index is outside the original run")
    original_finding = original_violations[violation_index]
    frozen_witness_record = _analysis_witness(analysis, case_id, violation_index)
    frozen_witness = frozen_witness_record.get("witness")
    if not isinstance(frozen_witness, Mapping):
        raise ReplayEvidenceError("frozen analysis witness is malformed")

    replay = run_trace(
        case.game_name,
        seed=case.policy.environment_seed,
        trace_policy=case.policy,
        max_destination_calls=plan.destination_call_cap,
        max_source_decisions=plan.decision_cap,
    )
    replay_run = replay.to_dict()
    replay_violations = replay_run.get("violations")
    if not isinstance(replay_violations, list):
        raise ReplayEvidenceError("replay did not serialize a finding list")
    finding_reproduced = (
        violation_index < len(replay_violations)
        and _canonical_sha256(replay_violations[violation_index])
        == _canonical_sha256(original_finding)
    )
    replay_witness: dict[str, Any] | None = None
    boundary_reproduced = False
    if violation_index < len(replay_violations):
        replay_witness = localize_divergence(replay_run, violation_index)
        boundary_reproduced = replay_witness.get("boundary") == frozen_witness.get(
            "boundary"
        )

    original_case = record.get("case")
    expected_case = {
        "case_id": case.case_id,
        "ordinal": case.ordinal,
        "game_name": case.game_name,
        "trace_policy_name": case.policy.name,
        "trace_policy_id": case.policy.policy_id,
        "trace_policy_seed": case.policy.seed,
        "environment_seed": case.policy.environment_seed,
    }
    same_case_inputs = isinstance(original_case, Mapping) and dict(
        original_case
    ) == expected_case
    reproduced = same_case_inputs and finding_reproduced and boundary_reproduced
    payload = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "artifact_type": REPLAY_ARTIFACT_TYPE,
        "status": "reproduced" if reproduced else "failed",
        "raw_batch_sha256": _sha256(batch_path),
        "manifest_sha256": plan.gate.manifest_sha256,
        "receipt_sha256": plan.gate.receipt_sha256,
        "archive_identifier": plan.gate.archive_identifier,
        "localizer_id": LOCALIZER_ID,
        "case": expected_case,
        "violation_index": violation_index,
        "criteria": {
            "same_case_inputs": same_case_inputs,
            "finding_reproduced": finding_reproduced,
            "boundary_reproduced": boundary_reproduced,
        },
        "original": {
            "finding": original_finding,
            "finding_sha256": _canonical_sha256(original_finding),
            "localized_witness_sha256": frozen_witness_record.get(
                "localized_witness_sha256"
            ),
            "boundary": frozen_witness.get("boundary"),
        },
        "replay": {
            "run": replay_run,
            "run_sha256": _canonical_sha256(replay_run),
            "finding": (
                replay_violations[violation_index]
                if violation_index < len(replay_violations)
                else None
            ),
            "finding_sha256": (
                _canonical_sha256(replay_violations[violation_index])
                if violation_index < len(replay_violations)
                else None
            ),
            "witness": replay_witness,
        },
    }
    write_json(output_path, payload)
    return payload
