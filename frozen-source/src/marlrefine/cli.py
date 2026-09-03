"""Command-line entry point for reproducible MARLRefine experiments."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from marlrefine.adapters.openspiel_shimmy import TraceRun, run_trace
from marlrefine.external_baselines import execute_external_baselines
from marlrefine.policies import TRACE_POLICY_NAMES
from marlrefine.prospective import build_prospective_plan, execute_prospective_batch
from marlrefine.provenance import project_file_identity, runtime_provenance
from marlrefine.serialization import write_jsonl
from marlrefine.study import build_draft_study_manifest

GAME_SPEC_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _frozen_semantic_names() -> frozenset[str]:
    """Read registry metadata only; never construct or step a frozen game."""
    manifest = build_draft_study_manifest()
    return frozenset(manifest["validation"]["semantic_cohort"]["names"])


def _trace_cli_crosses_freeze_boundary(game_spec: str) -> bool:
    """Detect direct or wrapped references to a frozen semantic game name."""
    return bool(set(GAME_SPEC_TOKEN.findall(game_spec)) & _frozen_semantic_names())


def _write_jsonl(
    path: Path,
    runs_and_labels: Sequence[tuple[TraceRun, str | None]],
) -> None:
    provenance = runtime_provenance()
    manifest = project_file_identity("manifests/study_v1_draft.json")
    write_jsonl(
        path,
        (
            {
                "schema_version": 1,
                "artifact_type": "marlrefine_trace_run",
                "environment": provenance,
                "study_manifest": manifest,
                "case_id": label or run.game_spec,
                "run": run,
            }
            for run, label in runs_and_labels
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marlrefine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    trace = subparsers.add_parser("trace", help="run one coupled source/adapter trace")
    trace.add_argument("game_spec")
    trace.add_argument("--seed", type=int, default=0)
    trace.add_argument(
        "--policy",
        choices=TRACE_POLICY_NAMES,
        default="smallest_legal",
    )
    trace.add_argument("--max-calls", type=int, default=10_000)
    trace.add_argument("--max-source-decisions", type=int)
    trace.add_argument("--output", type=Path)
    trace.add_argument("--allow-violations", action="store_true")

    probe = subparsers.add_parser(
        "probe", help="reproduce the frozen discovery witnesses"
    )
    probe.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/pilot.jsonl"),
    )
    probe.add_argument("--allow-violations", action="store_true")

    prospective = subparsers.add_parser(
        "prospective",
        help="run the authorization-gated 105-name prospective semantic batch",
    )
    prospective.add_argument(
        "--manifest",
        type=Path,
        default=Path("manifests/study_v1_draft.json"),
    )
    prospective_authorization = prospective.add_mutually_exclusive_group(required=True)
    prospective_authorization.add_argument(
        "--execution-authorization", dest="archive_receipt", type=Path
    )
    prospective_authorization.add_argument(
        "--archive-receipt", dest="archive_receipt", type=Path,
        help="public Zenodo receipt (legacy explicit spelling)",
    )
    prospective.add_argument("--output", type=Path, required=True)
    prospective.add_argument(
        "--resume-infrastructure-from",
        type=Path,
        help="retry only eligible infrastructure outcomes from a sealed batch",
    )

    external = subparsers.add_parser(
        "prospective-baselines",
        help="run authorization-gated stock API and released-suite baselines",
    )
    external.add_argument(
        "--manifest",
        type=Path,
        default=Path("manifests/study_v1_draft.json"),
    )
    external_authorization = external.add_mutually_exclusive_group(required=True)
    external_authorization.add_argument(
        "--execution-authorization", dest="archive_receipt", type=Path
    )
    external_authorization.add_argument(
        "--archive-receipt", dest="archive_receipt", type=Path,
        help="public Zenodo receipt (legacy explicit spelling)",
    )
    external.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser(
        "verify-archive",
        help="verify the public Zenodo freeze without executing semantic traces",
    )
    verify.add_argument(
        "--manifest",
        type=Path,
        default=Path("manifests/study_v1_draft.json"),
    )
    verify.add_argument("--archive-receipt", type=Path, required=True)
    return parser


def _print_summary(run: TraceRun, label: str | None = None) -> None:
    name = label or run.game_spec
    codes = sorted({violation.code for violation in run.violations})
    setup_status = str(run.summary.get("setup_status", ""))
    if run.passed:
        status = "pass"
    elif setup_status.startswith("error:"):
        status = "execution_error"
    elif run.applicable:
        status = "violation"
    else:
        status = "inapplicable"
    print(
        f"{name}: status={status} calls={run.summary.get('destination_calls', 0)} "
        f"source_events={run.summary.get('source_transitions', 0)} "
        f"violations={len(run.violations)} codes={','.join(codes) or '-'}"
    )


def _acceptable_exit(run: TraceRun, *, allow_violations: bool) -> bool:
    """Relax only completed, applicable semantic counterexamples."""
    return run.passed or (
        allow_violations
        and run.applicable
        and run.summary.get("setup_status") == "pass"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "trace":
        if _trace_cli_crosses_freeze_boundary(args.game_spec):
            print(
                "error: the public `trace` command cannot execute a frozen "
                "prospective cohort name; use the authorization-gated quiet "
                "`marlrefine prospective` command",
                file=sys.stderr,
            )
            return 2
        run = run_trace(
            args.game_spec,
            seed=args.seed,
            trace_policy=args.policy,
            max_destination_calls=args.max_calls,
            max_source_decisions=args.max_source_decisions,
        )
        _print_summary(run)
        if args.output is not None:
            _write_jsonl(args.output, [(run, None)])
        return 0 if _acceptable_exit(run, allow_violations=args.allow_violations) else 1

    if args.command == "prospective":
        summary = execute_prospective_batch(
            args.manifest,
            args.archive_receipt,
            args.output,
            resume_infrastructure_from=args.resume_infrastructure_from,
        )
        counts = " ".join(
            f"{status}={count}"
            for status, count in sorted(summary.status_counts.items())
        )
        print(
            f"sealed {summary.case_count} prospective cases at {summary.output}; "
            f"{counts}; infrastructure_retries="
            f"{summary.resumed_infrastructure_cases}"
        )
        return 2 if summary.status_counts.get("infrastructure", 0) else 0

    if args.command == "verify-archive":
        plan = build_prospective_plan(args.manifest, args.archive_receipt)
        print(
            f"verified {plan.gate.archive_identifier}; "
            f"manifest_sha256={plan.gate.manifest_sha256}; "
            f"source_tree_sha256={plan.gate.source_tree_sha256}; "
            f"uv_lock_sha256={plan.gate.uv_lock_sha256}; "
            f"prospective_cases={len(plan.cases)}"
        )
        return 0

    if args.command == "prospective-baselines":
        payload = execute_external_baselines(
            args.manifest,
            args.archive_receipt,
            args.output,
        )
        stock = payload["stock_pettingzoo_api_test"]
        suite = payload["released_shimmy_openspiel_suite"]["result"]
        counts = " ".join(
            f"{status}={count}"
            for status, count in sorted(stock["status_counts"].items())
        )
        print(
            f"sealed {stock['case_count']} stock API results at {args.output}; "
            f"{counts}; shimmy_suite={suite['status']}"
        )
        return 2 if suite["status"] == "infrastructure" else 0

    runs_and_labels = (
        (
            run_trace("coop_box_pushing", seed=7, max_source_decisions=10),
            "buffered_reward",
        ),
        (run_trace("coop_box_pushing", seed=0), "chance_horizon"),
        (run_trace("nim", seed=0), "terminal_cleanup"),
        (run_trace("go(board_size=5)", seed=3), "configuration_reset"),
        (run_trace("mfg_crowd_modelling", seed=0), "mean_field"),
    )
    for run, label in runs_and_labels:
        _print_summary(run, label)
    _write_jsonl(args.output, runs_and_labels)
    failed = any(
        not _acceptable_exit(run, allow_violations=args.allow_violations)
        for run, _ in runs_and_labels
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
