# Post-hoc recovery of prespecified descriptive reporting

This directory addresses a reporting omission discovered after the primary
analysis. The protocol prespecified descriptive reporting by player count,
chance and reward mode, information and utility type, declared observation and
information-state capabilities, finite-length status, parameterization,
primary registry stratum, reached source node kind, and a separate
single-player subgroup. The initial frozen tables did not render all of those
views.

The recovery is post hoc and descriptive. It joins immutable inputs from the
public `study-v1.0.1` source and results artifacts; it does not change the
cohort, trace schedule, status, semantic evidence, endpoint, finding, or manual
causal adjudication.

## Files

- `recover_secondary_subgroups.py`: streaming validator and table generator;
  SHA-256
  `6ed34fbaeb290c7e6a25042c20d14adeb12997e2f857c4c72aa9f68effb807b1`.
- `secondary_subgroup_report.json`: structured output; SHA-256
  `7bdd5fa50ca73a24df95dae8feeaa36e5647ff50050d665ce11dbbd1d19eb4d8`.
- `secondary_subgroup_report.md`: reviewer-readable output; SHA-256
  `f6b94dc2c99d9934ef3eab09737bde5cc801b22de5fa5a9a567c92d8ba5df1f8`.

The report records the identities of all six inputs and gives a complete
command using explicit paths from fresh extractions. The script checks internal
hash links and independently rederives the schedule, player counts, statuses,
obligation ledger, causal-adjudication join, stratum summaries, and final
source-boundary kinds before writing output.

## Scope limitation

The frozen inputs do not define an exhaustive classifier for every
`special-node` or `other nonstandard` game. No such taxonomy was created after
results were observed. The report instead exposes the unambiguous frozen
categories: mean-field stratum, one-shot information type, chance mode,
effective player count, and reached final source-boundary kind.
