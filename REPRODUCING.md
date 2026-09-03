# Reproducing the reported analysis

## Scope

This procedure validates the released bytes and recomputes the deterministic
analysis from the complete recorded ledger. It does not rerun the environments.
That distinction matters: analysis reproduction is fast and exact, whereas a
new experimental execution is a semantic replication on a newly created run.

## Requirements

- Python 3.13 on Linux ARM64 for an exact environment match; other platforms
  can still inspect and attempt the deterministic analysis, but are not the
  recorded execution environment.
- [`uv`](https://docs.astral.sh/uv/) for the locked Python environment.
- Approximately 1.2 GB free while the compressed results are extracted.

The frozen environment records CPython 3.13.2, OpenSpiel 2.0.2, Shimmy 2.0.1,
PettingZoo 1.27.0, Gymnasium 1.3.0, and NumPy 2.5.2. The exact resolution is in
`frozen-source/uv.lock`.

## Clean-environment command

From the repository root, download and extract the primary release asset, then
run:

```bash
uv sync --project frozen-source --frozen
uv run --project frozen-source python verify_artifact.py \
  --results marlrefine-study-results-v1.0.0
```

The verifier checks:

1. the frozen source-tree SHA-256;
2. the source revision and lock identity recorded by both manifests;
3. the exact raw ledger, authorization, baseline, mutation, adjudication, and
   manifest hashes bound by the frozen analysis;
4. a fresh in-memory recomputation of the complete analysis; and
5. byte equality of the regenerated LaTeX result macros.

Success ends with `verified MARLRefine study artifact` and prints the analysis
and raw-ledger identities. The verification does not overwrite any released
file.

## Test suite

The implementation checks can be run independently:

```bash
uv run --project frozen-source pytest frozen-source/tests
uv run --project frozen-source ruff check frozen-source
```

The completed study checkout recorded 341 passing tests. A new platform can
produce different dependency behavior; report that as a reproduction result
rather than editing the retained evidence.

## Experimental replication

The original execution used Linux ARM64 and the precise runtime identities in
the study manifests. The retained code exposes the study, baseline, mutation,
replay, and analysis drivers under `frozen-source/experiments/`. Running those
drivers again creates a new experiment and should write to a new directory.
Never replace the released JSON or JSONL files with outputs from a later run.

The original Docker image archive is not redistributed because it contains
third-party operating-system and package layers. `frozen-source/Dockerfile`,
`uv.lock`, the source-tree identity, and installed-distribution hashes document
the recorded environment. Rebuilding the image can support semantic
replication, but it cannot recreate a timestamp- and cache-sensitive OCI image
digest byte for byte.
