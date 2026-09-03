from __future__ import annotations

import ast
import shlex
from pathlib import Path

from marlrefine.provenance import (
    SOURCE_DIRECTORIES,
    SOURCE_ROOT_FILES,
    source_identity_paths,
)

ROOT = Path(__file__).resolve().parents[1]


def _docker_copy_sources() -> frozenset[str]:
    sources: set[str] = set()
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        parts = shlex.split(line)
        sources.update(part.removesuffix("/") for part in parts[1:-1])
    return frozenset(sources)


def _literal_tuple_assignment(path: Path, name: str) -> tuple[str, ...]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            expression = statement.value
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id == "frozenset"
                and len(expression.args) == 1
            ):
                expression = expression.args[0]
            value = ast.literal_eval(expression)
            return tuple(value)
    raise AssertionError(f"{name} assignment not found in {path}")


def test_container_copies_every_source_identity_input_and_manifest() -> None:
    copied = _docker_copy_sources()
    assert set(SOURCE_ROOT_FILES).issubset(copied)
    assert set(SOURCE_DIRECTORIES).issubset(copied)
    assert "manifests" in copied
    assert "paper" not in copied
    assert "marlrefine_research_dossier.md" not in copied

    ignored = set(
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert set(SOURCE_ROOT_FILES).isdisjoint(ignored)
    assert set(SOURCE_DIRECTORIES).isdisjoint(ignored)
    assert "manifests" not in ignored

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!manifests/mutation_v1.json" in dockerignore
    assert "!manifests/README.md" in dockerignore
    assert "!manifests/study_v1_draft.json" in dockerignore
    assert "!paper" not in dockerignore
    assert "!marlrefine_research_dossier.md" not in dockerignore
    for private_path in (
        "docs/journal_fit_novelty_audit.md",
        "docs/paper_outline.md",
        "docs/related_work.md",
    ):
        assert private_path in dockerignore


def test_manifest_readme_is_source_but_generated_manifests_are_nonrecursive() -> None:
    paths = {path.relative_to(ROOT).as_posix() for path in source_identity_paths(ROOT)}

    assert "manifests/README.md" in paths
    assert "manifests/mutation_v1.json" not in paths
    assert "manifests/study_v1_draft.json" not in paths


def test_protocol_bundle_requires_every_discovery_artifact() -> None:
    artifacts = _literal_tuple_assignment(
        ROOT / "deposit/build_protocol_bundle.py",
        "DISCOVERY_ARTIFACTS",
    )
    assert "artifacts/discovery_controls.json" in artifacts

    frozen_generated = _literal_tuple_assignment(
        ROOT / "deposit/build_protocol_bundle.py",
        "FROZEN_GENERATED_PATHS",
    )
    assert "container/IMAGE_IDENTITY.json" in frozen_generated
    assert "manifests/mutation_v1.json" in frozen_generated
    assert "manifests/study_v1_draft.json" in frozen_generated


def test_freeze_runbooks_require_mutation_first_and_exact_eight_path_commit() -> None:
    deposit_runbook = (ROOT / "deposit/README.md").read_text(encoding="utf-8")
    container_runbook = (ROOT / "container/README.md").read_text(encoding="utf-8")

    for runbook in (deposit_runbook, container_runbook):
        assert runbook.index("write_mutation_manifest.py") < runbook.index(
            "write_draft_manifest.py"
        )
        assert "two manifests" in runbook.lower()
        assert "five discovery artifacts" in runbook.lower()
        assert "eight generated identity paths" in runbook.lower()
        assert "container/IMAGE_IDENTITY.json" in runbook
        assert "A′" in runbook
        assert "B′" in runbook
        assert "A′..B′" in runbook
        assert "force-push" in runbook
        assert "linux/arm64" in runbook

    for generated_path in (
        "manifests/mutation_v1.json",
        "manifests/study_v1_draft.json",
        "artifacts/discovery_api_baselines.json",
        "artifacts/discovery_controls.json",
        "artifacts/discovery_repairs.json",
        "artifacts/pilot.jsonl",
        "artifacts/registry_census.json",
        "container/IMAGE_IDENTITY.json",
    ):
        assert generated_path in deposit_runbook


def test_public_runbook_covers_post_archive_execution_and_recovery() -> None:
    runbook = (ROOT / "deposit/README.md").read_text(encoding="utf-8")
    lower = runbook.lower()

    assert "50 gib" in lower
    assert "100 gib" in lower
    assert "separate backup target" in lower
    assert "docs/run_diary_template.md" in runbook
    assert "docs/deviation_log_template.md" in runbook
    assert "--external-baselines" in runbook
    assert "--mutation-batch" in runbook
    assert "reviewer package" in lower
    assert "private github release" in lower
    assert "fresh authenticated path" in lower
    assert "round-trip hash" in lower
    assert "container_image_archive" in runbook
    assert "docker image load" in lower
    assert "cold rebuild" in lower
    assert "docker engine 29.5.2" in lower
    assert "containerd image store" in lower
    assert "linux/arm64" in lower
    assert "**only** these two files" in runbook
    assert "marl-adapter-conformance-protocol-v1.1.tar.gz" in runbook
    assert "protocol_identity.json" in runbook
    assert "does not claim to prove" in lower
    assert "literal recorded id" in lower
    assert "atomically only after the entire invocation completes" in lower
    assert "must not be silently retried" in lower

    external_position = runbook.index("prospective-baselines")
    backup_position = lower.index("before any irreversible zenodo publication")
    publish_position = runbook.index("click Publish")
    mutation_position = runbook.index("experiments/run_mutation_study.py")
    preliminary_position = lower.index("run a **preliminary read-only analysis**")
    evidence_position = runbook.index("experiments/replay_prospective_finding.py")
    final_position = runbook.index("/results/prospective_analysis.json")
    sealing_position = lower.index("assemble and seal the private reviewer package")
    assert (
        backup_position
        < publish_position
        < external_position
        < mutation_position
        < preliminary_position
        < evidence_position
        < final_position
        < sealing_position
    )

    command_start = runbook.index("```bash", preliminary_position)
    command_end = runbook.index("```", command_start + len("```bash"))
    preliminary_command = runbook[command_start:command_end]
    assert "--external-baselines" in preliminary_command
    assert "--mutation-batch" in preliminary_command
    assert "--manual-adjudication" not in preliminary_command
    assert "prospective_analysis_preliminary.json" in preliminary_command
    assert "prospective_results_macros_preliminary.tex" in preliminary_command
    assert "schema-version-5 manual adjudication" in lower

    assert (ROOT / "docs/run_diary_template.md").is_file()
    assert (ROOT / "docs/deviation_log_template.md").is_file()


def test_root_readme_defers_frozen_execution_to_exact_image_runbook() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lower = readme.lower()

    assert "`uv run` invocations are development commands" in lower
    assert "not frozen study execution" in lower
    assert "docker engine 29.5.2" in lower
    assert "containerd image store" in lower
    assert "literal id" in lower
    assert "a′" in lower and "b′" in lower
    assert "the frozen-container commands are" not in lower
