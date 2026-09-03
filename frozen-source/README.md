# MARLRefine

MARLRefine is a research prototype for checking whether a multi-agent
environment adapter preserves the semantics of its source environment. It is
designed for adapters with **non-bijective traces**: a destination call may
represent no source transition, one source transition, or several source
transitions.

The first integration targets OpenSpiel 2.0.2 through Shimmy 2.0.1's
PettingZoo AEC adapter. The project distinguishes destination API conformance
from source-semantic conformance and records machine-readable original prefixes
ending at the first divergent boundary.

## Status

This repository is an early research artifact, not a completed confirmatory
study or a claim about adapter ecosystems in general. The pinned census contains
113 registry-marked default-loadable OpenSpiel game types: seven names are
explicitly contaminated discovery cases, one (`crossword`) is a known
descriptive capability exclusion, and 105 form the prospective semantic cohort.
No semantic trace has been run on those 105 names.

The current pre-run milestone includes a separately loaded native oracle,
variable-length alignment, obligation and baseline checkers, machine-readable
provenance, stock destination API tests, isolated causal treatments, and a
combined repair regression, all eight frozen source-legal trace policies,
semantic-preserving controls, localization, an authorization-gated blind checkpointed
batch runner, and a mandatory sealed 24-mutant sensitivity evaluation selected
from a 48-candidate pool, plus a frozen synthetic-tested analysis layer that
separates 105-game from 840-trace accounting. The selected execution route uses
a clean Git freeze plus an explicit local authorization that records
`preregistered=false`; it is not a public preregistration.
The prospective matrix, mutation outcomes, upstream adjudication, and independent
reproduction remain pending.

## Reproduce

The package supports Python 3.12--3.13. Prospective execution additionally
requires the exact Python implementation/version and every pinned package
version recorded in the frozen manifest:

```bash
uv sync
uv run python -m pytest
uv run ruff check .
uv run python experiments/write_mutation_manifest.py
uv run python experiments/write_draft_manifest.py
uv run python experiments/run_registry_census.py
uv run python experiments/run_discovery_api_baselines.py
uv run marlrefine probe --output artifacts/pilot.jsonl
uv run python experiments/run_discovery_repairs.py
uv run python experiments/run_discovery_controls.py
```

The trace command exits nonzero when a checked obligation is violated. Use
`--allow-violations` only when collecting expected, successfully executed
semantic counterexamples for the pinned released adapter; it does not mask setup
failures or inapplicable runs.

The public `marlrefine trace` command is discovery-only and refuses any direct
or wrapped reference to a frozen 105-name cohort game. Use the quiet
`marlrefine prospective` batch only with either a verified public receipt or the
explicit local non-preregistered authorization. The Python `run_trace` function remains an
internal testing primitive, not an access-control mechanism: importing it
directly is outside the preregistered workflow and any code change invalidates
the frozen source hash.

Freeze provenance uses two commits: source commit A, followed by commit B that
contains only the two generated manifests, five tracked discovery artifacts,
and the force-added final container identity. The manifest records A; the
deposit records A, B, their exact parent relationship, and the allowlisted
eight-path diff. When correcting an already published-to-Git B locally, the
runbook uses forward commits A′/B′: A′ deletes the old eight generated paths
while landing all source changes, and B′ re-adds exactly
those eight paths. It never rewrites or force-pushes the earlier freeze.

See `deposit/README.md` for the normative build, exact-image export, private
round-trip backup, public deposit, recovery, and command sequence. Prospective
execution uses Docker Engine 29.5.2 with its containerd image store, loads the
exact hash-bound image archive, confirms the recorded `linux/arm64` OCI
manifest image ID, and gives that literal ID to every `docker run`. Host
`uv run` invocations are development commands, not frozen study execution.
A cold build on any engine or platform is semantic reproduction only after its
platform, source, lock, base, Python, package, and installed-byte identities
match; it is not expected to reproduce the timestamp- and cache-sensitive OCI
manifest digest.

After execution authorization, the gated inner entry points are
`prospective`, `prospective-baselines`, the mutation driver,
preliminary analysis, standalone replay/evidence development, and final
analysis. Each must be invoked through the exact-ID `docker run` forms in the
deposit runbook. Preliminary analysis omits `--manual-adjudication`; the final
paper-generating analysis requires the complete raw-batch-hash-bound file.

The stock API matrix is an authorization-gated external baseline. The mutation study
is gated by the same execution authorization and verifies that the main manifest binds
the exact mutation-manifest bytes before any candidate executes. The exact released
Shimmy OpenSpiel test module is hash-verified and executed in the same external
artifact as contextual upstream evidence, but is not scored as a 105-game
root-level comparator. No prospective command above is authorized before the
selected execution authorization succeeds.

## Research claim

The novelty-safe claim is:

> An executable, obligation-aware conformance-testing method for multi-agent
> adapters with variable-length source/destination trace mappings can test
> preservation contracts that destination-local API tests do not address and
> retain phases that strict one-to-one comparators cannot represent.

The project does **not** claim to invent refinement theory, POSG-to-AEC
equivalence, differential testing, or source/target rollout comparison.

## Licenses

Documentation, protocols, metadata, manifests, and generated research data are
available under CC BY 4.0 as scoped in
`LICENSE-docs-data`. Software source code, executable scripts, build
configuration, and container recipes are available under the Apache License
2.0 in `LICENSE`, unless a file states otherwise. Third-party components retain
their own licenses.

## Layout

- `src/marlrefine/`: trace model, alignment, obligations, and integrations
- `tests/`: pure unit tests and pinned third-party regression tests
- `experiments/`: census, baseline, manifest, and causal-treatment drivers
- `docs/research_status.md`: current evidence, gaps, and paper decision gates
- `docs/protocol.md`: pre-freeze empirical protocol and stop/go gates
- `docs/contract_evidence.md`: O1--O8 contract, mapping, policy, and claim gates
- `docs/paper_outline.md`: manuscript structure and claim boundaries
- `docs/journal_fit_novelty_audit.md`: private 2026-08-31 venue and novelty audit
- `docs/related_work.md`: primary-source novelty matrix
- `artifacts/`: generated JSON/JSONL outputs; the five discovery/freeze inputs are tracked, while prospective and transient outputs are ignored
- `adapter_refinement_pilot.py`: original diagnostic pilot
