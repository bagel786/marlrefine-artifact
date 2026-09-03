# MARLRefine study artifact

This repository accompanies the manuscript *Phase-Aware Source-Aligned
Conformance Testing for Multi-Agent Environment Adapters*. It contains the
exact canonical source-identity snapshot used for the completed study, a
machine-checkable reproduction entry point, and documentation for the released
result data.

MARLRefine compares a multi-agent environment adapter with a separately loaded
native source environment. It aligns destination API calls with zero, one, or
multiple source transitions and evaluates only the semantic obligations that
apply at each observed boundary.

## Study result in brief

The completed bounded study covered 105 reserved OpenSpiel game types under
eight fixed policies, for 840 traces. It recorded 90 passing traces, 718 traces
with at least one detected violation, and 32 traces for which the adapter did
not expose an applicable semantic execution. Causal adjudication grouped the
2,737 emitted findings into three implementation-level roots: replayed rewards,
source-decision-clock mismatch, and unsupported mean-field capability. A
separately sealed mutation evaluation produced 19 semantic detections, four
crash-only detections, and one survivor among 24 selected mutants. The
prespecified strong-sensitivity threshold of 20 semantic detections was not met.

These are descriptive results for one adapter implementation and bounded
recorded prefixes. They are not ecosystem prevalence estimates, full-program
proofs, or 840 statistically independent replications.

## Repository layout

- `frozen-source/` is the exact canonical source-identity scope used by the
  corrected study rerun. Its SHA-256 is
  `c7f1edca01663dd8e918ab02c1f08c3ec901b428a35bfc160db8e1c4f066b1cd`.
- `verify_artifact.py` checks the source identity, every analysis input hash,
  the frozen analysis, and the generated LaTeX result macros.
- `REPRODUCING.md` gives the shortest complete verification procedure.
- `RELEASES.md` identifies the separately uploaded result archives and their
  checksums.

The historical Git object named by the evidence is
`640b1d89174e44fa7a0440f1d4d296e316cc5b41`. Original repository history is
intentionally not copied here because historical commit metadata and unrelated
research material are outside the public artifact. The complete bytes covered
by the study's canonical source-tree hash are preserved under `frozen-source/`.
Status language inside that directory is likewise frozen at execution time and
may describe later results as pending; this top-level README and the completed
results archive describe the final state.

## Results download

Download `marlrefine-study-results-v1.0.1.tar.gz` from the
[`study-v1.0.1` GitHub Release](https://github.com/bagel786/marlrefine-artifact/releases/tag/study-v1.0.1).
It contains the complete 840-trace raw ledger, the five frozen discovery
inputs, frozen manifests, external baseline output, mutation output, manual
adjudication, root evidence, frozen analysis, and generated result macros.

Version 1.0.1 adds five frozen discovery inputs inadvertently omitted from the
version 1.0.0 package. The raw study ledger, final evidence, manifests,
analysis, and reported results are byte-for-byte unchanged.

The separately labeled `marlrefine-superseded-attempt-1.tar.gz` is retained for
auditability only. Its run was superseded after a serialization inconsistency
stopped the mutation stage. Do not merge it with, or use it in place of, the
completed rerun.

## Reproduction

See [`REPRODUCING.md`](REPRODUCING.md). The primary verification is read-only:
it does not execute games or mutate the released evidence.

## Transparency

The study used a clean local Git freeze and an explicit authorization artifact;
it was not publicly preregistered. The corrected run is an integrity
replication performed after aggregate statuses from the first attempt had been
seen. The protocol and analysis preserve that chronology explicitly.

## Licensing

Author-owned software is licensed under the Apache License 2.0 in `LICENSE`.
Documentation, metadata, manifests, and generated research data are licensed
under CC BY 4.0 as described in `LICENSE-DATA`. Third-party dependencies retain
their own licenses.
