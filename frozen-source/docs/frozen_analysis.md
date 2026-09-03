# Frozen prospective analysis

Pre-freeze analysis amendment reflected here: 2026-09-01. No
prospective semantic case has been executed.

`experiments/analyze_prospective_batch.py` is the read-only post-run analysis
entry point. It never calls the environment runner and cannot execute or retry
a game through its public analysis path. It requires the sealed raw JSONL batch, the exact frozen manifest,
and the exact execution authorization:

```console
uv run python experiments/analyze_prospective_batch.py \
  --batch artifacts/prospective_raw.jsonl \
  --manifest manifests/study_v1_draft.json \
  --execution-authorization /run/local_execution_authorization.json \
  --external-baselines artifacts/prospective_external_baselines.json \
  --mutation-batch artifacts/prospective_mutations.json \
  --manual-adjudication artifacts/manual_adjudication.json \
  --output artifacts/prospective_analysis.json \
  --latex-output artifacts/prospective_results_macros.tex
```

The three evidence arguments after the authorization may be omitted for a preliminary
analysis whose root-level and secondary-result fields remain visibly pending.
The final paper-generating run requires the complete batch-bound adjudication,
the exact authorization-gated external-baseline artifact, and the exact sealed
mutation-batch artifact. A complete manual adjudication is rejected if either
secondary artifact is absent.

The sealed prospective input uses batch schema version 2. Its header binds the
obligation-ledger schema ID `marlrefine_obligation_ledger_v1`, and every
serialized `TraceRun` carries an `obligation_evaluations` field. The normalized
analysis output is schema version 9, `marlrefine_frozen_analysis_v9`. Manual
adjudication is a separate schema-version-5 input. This pre-freeze amendment
retains streaming validation, compact hash-bound prefix references,
diagnostic-specificity summaries, replay-anchored progress validation, and
standalone-replay evidence for every retained root in a complete adjudication.
It additionally binds and
validates the complete external-baseline and mutation artifacts, derives RQ6
rows from the latter rather than from author-entered totals, and separates raw
batch identity from the later reviewer-package identity. It does not change the
sealed prospective cohort or run schedule.

The external-baseline artifact must use its frozen schema and classifier,
contain exactly the ordered 105-game stock-API panel, reproduce the frozen
cycles and seed, carry internally consistent pass/exception and status totals,
and carry the exact pinned Shimmy source-suite identity and result classifier.
Its manifest, source-tree, lock, authorization, authorization-time, and stable
runtime identities must equal the primary batch inputs.

The mutation artifact uses batch schema version 3. It explicitly binds the
study-manifest, mutation-manifest, source-tree, lockfile, and execution-authorization
SHA-256 values plus the authorization identifier/time and stable runtime identity.
Analysis calls `marlrefine_mutation_batch_validator_v3`, which recomputes all
48 candidate records, reference acceptability, hook reach from strictly typed
operator-specific mutation evidence, adapter-projection and behavior-delta
hashes, outcome-blind selection, paired finding and comparator deltas,
clean-reference alarms, and progress controls before any mutation value enters
the analysis or manuscript. Every serialized run is checked against the sealed
game, seed, policy identity/seed, source-decision cap, event/alignment schemas,
registered obligation/code vocabulary, baseline panel, and recomputed O1--O8
ledger. Both
progress controls must introduce the frozen
`monotone_progress_and_completeness/progress_instrumentation_inconsistent`
finding; an unrelated delta cannot satisfy the control. Their clean and
corrupted destination ledgers must be nonempty and identical outside the
frozen progress-only transform, and the validator rederives at least one exact
lag/overcount trigger from the stored per-call anchors.

The artifact-writing entry point records `analysis_runtime` separately from the
batch execution runtime and fails closed unless source-tree and lock hashes,
Python identity, installed distribution bytes, Git/source state, and stable
container platform fields agree. `analysis_runtime.created_at_utc` is an
explicitly volatile record of the analysis write, not part of the normalized
stable runtime comparison or deterministic result-macro claim; regenerating an
otherwise identical analysis may therefore change that timestamp channel. The
pure in-memory analysis function remains available for synthetic unit fixtures;
it is not the frozen publication path.

The validator rejects noncanonical JSONL, duplicate keys, non-finite numbers,
Python-style numeric aliases for JSON integers/Booleans, unknown schema keys,
wrong line counts,
identity mismatches, reordered game-policy cases, changed policy metadata,
stored labels that disagree with the frozen classifier, malformed event or
obligation ledgers, invalid finding links, inconsistent alignment covers,
incomplete in-line-ablation panels, and footer/count disagreement. Each JSON
object artifact is hashed from the exact bytes decoded and validated in the
same read, and the streaming raw-batch digest is accumulated from the exact
descriptor supplying the parsed JSONL records; later path replacement cannot
bind different bytes to an already parsed result.
The manifest supplies the exact ordered 105-game cohort; the only accepted case
body is its game-major product with the frozen eight-policy schedule, for 840
trace records.

Every destination event must carry a `progress_instrumentation` record using
`independent_native_replay_event_count_v1`. Its `progress_before`,
`progress_after`, replayed event count, and consecutive source-event progress
list must agree with the preceding destination event and the event's
`source_progress`; its wrapped-history delta must contain integer actions. The
runner obtains this anchor by replaying the disclosed history delta on the
separately loaded native source. Consequently, a merely monotone but internally
inconsistent progress tag fails validation. This still depends on the declared
white-box trust assumptions: wrapped history must disclose committed source
transitions in order, remain prefix-monotone, and not be perturbed by
inspection.

At each live O8 boundary, the runner checks the destination observation's raw
public container type, element count, shape, and dtype in that order before any
value normalization. Only floating values that pass those signature checks use
the frozen value tolerance; the observed object is not reshaped or cast to make
it match. A compact raw signature is retained in the destination-event metadata,
and complete expected/observed evidence is retained on a mismatch.

The canonical JSON keeps trace-level and distinct-game accounting separate.
The five trace statuses are disjoint. A separate two-axis table preserves
semantic evidence independently of terminal, bounded, unalignable,
infrastructure, or inapplicable execution completeness. Game-level output includes both a fixed,
non-overlapping evidence-completeness partition and explicitly overlapping
“any status” flags; the latter must not be summed. Violation and in-line-ablation
tables count symptoms only and never infer causal roots. Obligation coverage is
reported from the explicit O1–O8 evaluation ledger rather than inferred from
the presence or absence of violations.

Timing summaries use each selected final case record's `elapsed_ns` runner
measurement. Batch schema version 2 does not record archive-verification or
orchestration wall time, and a resumed artifact does not retain the duration of
a superseded failed attempt, so the reported total is explicitly a sum of
available case-runner durations rather than end-to-end elapsed time. Ledger
size is reported both as event counts and canonical JSON bytes; peak memory is
not derivable from the raw batch.

## Obligation-evaluation coverage

Each serialized run contains exactly eight rows in the fixed order O1 through
O8. Every row has exactly these fields:

```json
{
  "obligation_id": "O1",
  "applicable": true,
  "evaluated": true,
  "outcome": "evaluated_pass",
  "reason_code": "buffer_microstep_observed",
  "evaluation_count": 1,
  "finding_indices": []
}
```

The four allowed outcomes are `evaluated_pass`, `evaluated_fail`,
`not_applicable`, and `not_evaluated`. They are mutually exclusive for one
obligation on one scheduled trace. Evaluated rows are applicable, have a
positive `evaluation_count`, and fail exactly when their sorted, unique
`finding_indices` link one or more entries in that run's violation list.
`not_applicable` rows have known-false applicability and no evaluated sites or
links. `not_evaluated` rows have no evaluated sites or links and preserve the
difference between an undetermined applicability predicate and a known
applicable path that stopped before its first check. Stable reason codes record
why a row received its outcome.

For each obligation, analysis reports all four scheduled-trace counts,
`applicable_trace_count` (evaluated pass plus evaluated fail), the sum of
`evaluation_count` across traces, the number of linked finding references, and
overlapping distinct-game counts for each outcome. The four trace outcomes for
each O1–O8 row must sum to 840. Distinct-game outcome counts may overlap when a
game's eight policies produce different outcomes and therefore must not be
summed. Site checks are repeated boundary or microstep evaluations, not
independent replications. A finding linked to two obligations contributes one
link to each corresponding obligation row; unlinked execution or alignment
diagnostics remain separately counted by occurrence, trace, and distinct game.

An infrastructure case whose case record has `run: null` has no serialized
obligation rows. During aggregation, analysis contributes one synthetic
`not_evaluated` outcome to every O1–O8 obligation for that scheduled trace, with
zero site checks and zero linked findings. Thus infrastructure loss remains in
the fixed 840-trace denominator without being mistaken for non-applicability or
a semantic pass.

## Execution-path and structural coverage

Analysis cross-tabulates the five frozen classifier statuses against four
exhaustive execution-path categories:

- `terminal_complete`: a serialized run stopped with
  `destination_episode_end`, the source is terminal, and no adapter agents
  remain;
- `bounded_prefix`: a serialized run stopped at `source_decision_limit`;
- `other_serialized_run`: every other non-null run; and
- `no_run_infrastructure`: an infrastructure record with `run: null`.

`completion_by_status` contains all five status cells for each path and all
cells together must sum to 840. `stop_reason_by_status` preserves the finer
serialized stop reasons. The separately reported structural coverage counts
aligned transition segments, one-to-many and many-to-one transition segments,
destination buffer, advancing-commit, other-stutter, and cleanup calls, source
event-origin node kinds, final source-boundary kinds, and source chance events
at occurrence, scheduled-trace, and distinct-game levels.
These are observations of the recorded paths, not claims about unvisited
behavior. In particular, a terminal-complete count and a bounded-prefix count
describe how execution ended; neither replaces the five-status classifier.

Registry-stratum reporting uses the manifest metadata fixed before execution:
sequential deterministic, sequential stochastic, simultaneous deterministic,
simultaneous stochastic, and mean-field. Explicit versus sampled stochastic
chance remains a secondary manifest tag rather than a sixth primary stratum.
Analysis reports all five outcome statuses and finding occurrences within each
stratum without treating the cells as independent samples.

Action coverage uses the unit `(game, native-state-digest, source-player,
action)`. At every live interface event with complete metadata, the analysis
adds every source-legal action to the offered set and the policy choice to the
selected set. It reports cumulative offered and selected sets after each policy
in the fixed schedule and the marginal number of new selected tuples. The ratio
is coverage of actions offered on visited states only; it is not coverage of the
source state space or of unvisited legal actions.

Tolerance sensitivity is a secondary reclassification of stored residuals,
not a rerun and not a change to a primary verdict. Reward residual ratios use
the exact `math.isclose` threshold `max(atol, rtol * max(abs(expected),
abs(observed)))`. Floating-observation ratios use the NumPy comparison
threshold after exact raw container/count/shape/dtype checks. For each channel,
the report counts comparable sites within 0.1, 1, and 10 times the primary
threshold and separately counts nonfinite or unavailable residuals.

For every recorded violation in every serialized run, the analysis invokes the
pure `marlrefine_original_prefix_v3` localizer. It stores a compact reference to
the original source and destination ledger prefix plus invocation/replay context
for that finding. The reference binds the sealed raw run, complete ledgers,
prefixes, and context by canonical SHA-256 and is materialized only after those
hashes verify; the earliest trace-level divergence remains derivable by
chronological order.
Per-finding prefixes allow a later causally distinct root in the same trace to
serve as a manual root witness. The localizer also records the selected obligation
family and violation code; destination-phase attribution (`buffer`, `commit`,
`cleanup`, another stutter, a multi-call span, or a setup/boundary category);
the exact destination-call index when the implicated span contains one call;
the number of implicated calls; and the number of destination calls required
to reach the boundary. It performs no game execution, delta debugging,
semantic minimization, or verdict-preserving reduction.

The schema-version-8 aggregate is computed by streaming the canonical raw batch
and reports finding-level destination-phase counts,
the count of finding witnesses attributable to an exact destination call, the
distribution of destination-prefix call counts, and the existing
prefix-to-original-event ratio. For each case, it also reports the finest resolution at which each
signaling project ablation retains a symptom: destination call for applicable
strict lockstep, advancing commit for macro-boundary, aligned block for
macro-aggregate, trace endpoint, or episode return. A null resolution means the
ablation did not signal on that trace. These fields compare diagnostic
granularity; they neither assign same-root causal credit nor claim that the
prefix is minimal. The paper's confirmed-root localization table displays an
ablation resolution only when manual isolated-treatment evidence awards that
ablation same-root detection credit.

Root clustering and causal claims require an optional, batch-hash-bound
`marlrefine_manual_adjudication` JSON object. Schema version 5 stores the
records from which the paper counts are derived; it does not accept author-entered
root, ablation, repair, control, or upstream totals. In particular, failed
traces, repeated violation codes, affected games, and ablation findings are
never silently promoted to causal roots.

The top-level version-5 object has these exact keys:

```json
{
  "schema_version": 5,
  "artifact_type": "marlrefine_manual_adjudication",
  "raw_batch_sha256": "<sha256 of sealed JSONL>",
  "status": "pending",
  "roots": [],
  "finding_dispositions": [],
  "controls": [],
  "optional_measurements": {
    "held_out_mutants_killed": null,
    "held_out_mutants_total": null,
    "peak_memory_bytes": null
  }
}
```

Each `roots` entry has exactly the following shape:

```json
{
  "root_id": "reward-replay-root",
  "provenance": "prospective",
  "family": "reward-conservation",
  "adjudication_status": "confirmed",
  "first_witness": {
    "case_id": "<game>::<policy>",
    "evidence_artifact_sha256": "<raw-batch sha256>",
    "localizer_id": "marlrefine_original_prefix_v3",
    "localized_witness_sha256": "<canonical localized-witness sha256>",
    "boundary": {
      "segment_index": 0,
      "source_event_stop": 1,
      "destination_event_stop": 2,
      "selected_violation_index": 0
    }
  },
  "contract": {
    "citation": "<primary-contract citation and anchor>",
    "claim_classification": "defect"
  },
  "effect_summary": "<consumer-visible effect>",
  "replay": {
    "status": "reproduced",
    "evidence": {
      "artifact_sha256": "<standalone-replay artifact sha256>",
      "evidence_reference": "<replay case or command reference>",
      "same_case_inputs": true,
      "finding_reproduced": true,
      "boundary_reproduced": true
    }
  },
  "baselines": {
    "strict_lockstep": {
      "outcome": "detected",
      "root_witness_reached": true,
      "outcome_evidence": {
        "artifact_sha256": "<raw-batch sha256>",
        "evidence_reference": "<game>::<policy>"
      },
      "causal_attribution": "same_root",
      "causal_evidence": {
        "artifact_sha256": "<isolated-treatment-evidence sha256>",
        "evidence_reference": "<before/after comparison reference>",
        "patch_sha256": "<causal patch sha256>",
        "isolated_treatment": true,
        "target_root_present_before": true,
        "target_root_absent_after": true,
        "baseline_signal_before": true,
        "baseline_signal_after": false
      },
      "credit": "detected"
    },
    "macro_boundary": {
      "outcome": "not_detected",
      "root_witness_reached": true,
      "outcome_evidence": {
        "artifact_sha256": "<raw-batch sha256>",
        "evidence_reference": "<game>::<policy>"
      },
      "causal_attribution": "not_applicable",
      "causal_evidence": null,
      "credit": "missed"
    },
    "macro_aggregate": {
      "outcome": "not_detected",
      "root_witness_reached": true,
      "outcome_evidence": {
        "artifact_sha256": "<raw-batch sha256>",
        "evidence_reference": "<game>::<policy>"
      },
      "causal_attribution": "not_applicable",
      "causal_evidence": null,
      "credit": "missed"
    },
    "endpoint": {
      "outcome": "not_detected",
      "root_witness_reached": true,
      "outcome_evidence": {
        "artifact_sha256": "<raw-batch sha256>",
        "evidence_reference": "<game>::<policy>"
      },
      "causal_attribution": "not_applicable",
      "causal_evidence": null,
      "credit": "missed"
    },
    "return_only": {
      "outcome": "not_detected",
      "root_witness_reached": true,
      "outcome_evidence": {
        "artifact_sha256": "<raw-batch sha256>",
        "evidence_reference": "<game>::<policy>"
      },
      "causal_attribution": "not_applicable",
      "causal_evidence": null,
      "credit": "missed"
    },
    "stock_api": {
      "outcome": "passed",
      "root_witness_reached": true,
      "outcome_evidence": {
        "artifact_sha256": "<API-evidence sha256>",
        "evidence_reference": "<API case reference>"
      },
      "causal_attribution": "not_applicable",
      "causal_evidence": null,
      "credit": "missed"
    }
  },
  "causal_patch": {
    "stock_source_tree_sha256": "<frozen stock tree sha256>",
    "treatment_source_tree_sha256": "<isolated treatment tree sha256>",
    "patch_sha256": "<causal patch sha256>",
    "evidence_reference": "<patch reference>"
  },
  "repair": {
    "status": "successful",
    "evidence": {
      "artifact_sha256": "<repair-evidence sha256>",
      "evidence_reference": "<case or matrix reference>",
      "failing_before": true,
      "targeted_findings_absent_after": true,
      "no_new_findings": true,
      "reachability_preserved": true,
      "regression_passed": true,
      "reversion_restores_failure": true,
      "patch_sha256": "<causal patch sha256>"
    }
  },
  "upstream": {
    "status": "not_contacted",
    "reference": null
  }
}
```

Each `finding_dispositions` entry has exactly this shape:

```json
{
  "case_id": "<game>::<policy>",
  "violation_index": 0,
  "finding_sha256": "<canonical finding sha256>",
  "disposition": "root",
  "root_id": "reward-replay-root",
  "rejection_reason": null
}
```

The alternative disposition is `rejected`, with `root_id: null` and a nonempty
`rejection_reason`. On a complete adjudication, the list must cover the exact
batch-derived set of `(case_id, violation_index)` identifiers once each. An
entry may belong to only one confirmed prospective root or be rejected;
unknown, duplicate, and omitted findings are errors. Every confirmed
prospective root must own at least one finding, and its first-witness finding
must be among its memberships. Discovery roots retain external witness
provenance and are not assigned findings from the prospective batch.

`root_id` values are unique stable lowercase identifiers. Provenance is
`discovery` or `prospective`; family is another stable identifier.
Adjudication status is `confirmed`, `rejected`, or `pending`. Confirmed records
use the protocol's `defect`, `mismatch`, or `unsupported_capability` claim
classification; rejected records use `oracle_issue` or `not_a_defect`; pending
records use `pending`. A complete artifact may not contain a pending root,
replay, repair, or upstream disposition.

A confirmed root also requires a stable causal patch identity: frozen stock
tree hash, treatment tree hash, patch hash, and evidence reference. The stock
and treatment trees must differ; a prospective stock-tree hash must equal the
sealed source identity. Every same-root ablation comparison and scored repair
must name that same patch hash.

Every root has a mandatory `replay` object. A confirmed root must use status
`reproduced`, carry a hash and evidence reference for the standalone replay,
and assert all three checked criteria: identical case inputs, reproduction of
the finding, and reproduction of the localized boundary. `failed` replay
records retain the same structured evidence, whereas `pending` and
`not_applicable` records carry no evidence only while adjudication remains
pending. A root without a reproduced replay cannot be confirmed. Every retained
root in a complete adjudication must record an attempted replay as either
`reproduced` or `failed`, with bound evidence; `pending` and `not_applicable`
are rejected at completion even for rejected roots.

A confirmed record classified as a `defect` must carry a `successful` isolated
repair whose six criteria validate. A record whose contract basis or causal
repair is not yet publication-grade remains a `mismatch`,
`unsupported_capability`, pending, or rejected record as appropriate. Upstream
agreement may corroborate but cannot substitute for these evidence requirements.

Every root carries a first localized witness. For prospective roots, analysis
requires the case to be a localized failed trace in the sealed batch, requires
the evidence-artifact hash to equal the raw-batch hash, and exactly matches the
localizer ID, canonical witness hash, segment/event boundary, and selected
violation index. A discovery root cannot claim a prospective case ID; its
external discovery artifact and localized witness remain SHA-256 referenced
because they are outside the prospective JSONL.

Comparator outcomes and credit are cross-checked for the root-level
complementarity panel. `strict_lockstep`, `macro_boundary`, `macro_aggregate`,
`endpoint`, and `return_only` are project-defined in-line schedule and
information ablations over the same paired trace; they are not independent
external baselines. The stock PettingZoo API test is the separately executed
destination-only external comparator. The released Shimmy suite is archived as
stack-level context only; it is not a 105-cohort or root-level comparator and
therefore cannot receive root detection/miss credit. The stable JSON key remains
`baselines` for compatibility with the sealed schema.
`macro_boundary` is the commit-sampling comparator; `macro_aggregate` is the
credited macro comparator that sums all destination microstep rewards in a
transition block.

Each comparator record separates the observed `outcome`, whether the root's
witness was actually reached, `causal_attribution`, outcome evidence, causal
evidence, and final credit. Attribution is `same_root`,
`different_or_unresolved`, or `not_applicable`. A detected/failed outcome alone
does not receive detection credit. `same_root` is allowed only for a confirmed
root with a reached detection and separate hash-referenced isolated-treatment
evidence recording all of the following: the target root and ablation signal
are present before treatment, the isolated treatment is applied, and both are
absent afterward. A detected/failed outcome labeled
`different_or_unresolved` receives `not_scored` credit. An applicable
`not_detected`/`passed` outcome receives `missed` credit only when the confirmed
root's witness was reached. Unreached, inapplicable, not-evaluated, rejected,
and pending-root cases receive `not_scored`.

For prospective records, the raw batch cross-checks all five in-line ablation
outcomes, applicability-derived reachability, batch hash, and case reference. It does
**not** supply same-root causal evidence; a separate before/after treatment
artifact is still required. The stock API outcome and evidence hash/reference
are cross-checked against the bound external artifact's exact game result;
root-witness reachability and same-root causal credit remain manual claims.
Every evaluated
comparator outcome requires an outcome-artifact hash and reference;
`not_evaluated` requires `outcome_evidence` to be `null`. Causal evidence is
required only for `same_root` and must otherwise be `null`.

These checks establish schema, identity, and internal consistency. They do not
establish that a cited contract is correctly interpreted, that a manually
reviewed treatment is truly isolated, or that a causal claim is substantively
correct. The cited and hash-referenced evidence remains available for human
review and challenge.

A successful repair requires hash-referenced evidence and all
six frozen success criteria to be true, including restoration of the target
failure when only the patch is reverted. Failed repairs also require evidence
but cannot satisfy all six. `not_attempted`, `not_applicable`, and `pending`
repairs carry `null` evidence.

Upstream status is one of `pending`, `not_contacted`, `reported`,
`acknowledged`, `fixed`, `rejected`, or `not_applicable`. Reported and disposed
statuses require a reference; non-contact and non-applicable statuses prohibit
one. Only confirmed roots with `acknowledged` or `fixed` status contribute to
the upstream-confirmed count.

Each control record has exactly these fields:

```json
{
  "control_id": "native_clone_replay_v1",
  "evidence_artifact_sha256": "<control-artifact sha256>",
  "outcome": "pass",
  "observed_alarm_count": 0,
  "unexplained_alarm_count": 0
}
```

A complete artifact must contain exactly one record for each frozen control:
`native_clone_replay_v1`, `openspiel_turn_based_simultaneous_v1`, and
`pettingzoo_parallel_to_aec_v1`. IDs must be unique, unexplained alarms cannot
exceed observed alarms, and pass/fail must agree with whether any alarm was
observed.

When status is `complete`, analysis derives total, discovery, and prospective
confirmed-root counts from record provenance. The `root_families`,
macro-aggregate misses, stock-API misses, and repair-disposition totals are
pooled over all confirmed roots while retaining each row's
discovery/prospective provenance; they are not prospective-only metrics.
Repair attempts are `successful + failed`; successes, failures,
`not_attempted`, and `not_applicable` are reported separately, and those four
root dispositions must exhaust the confirmed-root count. These values come
from distinct family IDs, `macro_aggregate` and stock-API credit, and repair
status, respectively. Analysis derives
unexplained control alarms from the three control records, and upstream counts
from their dispositions. The analysis JSON retains all normalized records and
also emits status/provenance/family/upstream breakdowns. When status is
`pending`, those claim-level values remain visibly pending even if partial
records exist.

The historical sealed-mutation killed/total fields and optional peak-memory
value are non-negative scalars. Mutation values must be set as a pair, kills
cannot exceed total, and a complete paper-generating adjudication must provide
the pair. The historical `held_out_mutants_*` field names are retained only for
schema compatibility: `killed` now means the bound mutation artifact's semantic
kill total and `total` means its selected denominator. Analysis rejects any
disagreement and derives every manuscript mutation result from the validated
mutation artifact, not these redundant manual fields. Legacy
schema-version-1 artifacts are accepted only as `pending` files whose ten
values are all `null`; a complete scalar-only version-1 artifact is rejected.
Structured schema versions 2, 3, and 4 are invalidated: version 2 conflated
comparator signal with same-root causal credit, version 3 omitted
patch-reversion evidence, and version 4 omitted the mandatory standalone-replay
record. Complete structured adjudication requires version 5.

The generated LaTeX file has the following exact accounting expectations:

- `ProspectiveTraceCount`, `ProspectiveTraceCompleted`,
  `ProspectiveTracePasses`, `ProspectiveTraceFailures`,
  `ProspectiveTraceInapplicable`, `ProspectiveTraceUnalignable`, and
  `ProspectiveTraceInfrastructureFailures` come from the disjoint trace-level
  accounting. `ProspectiveTraceCompleted` is pass plus fail.
- `ProspectiveTerminalCompleteTraces` is the sum of all five classifier-status
  cells in the `terminal_complete` execution-path row.
  `ProspectiveBoundedPrefixPasses` is only the `pass` cell in the
  `bounded_prefix` row; it does not count failed or otherwise classified bounded
  prefixes.
- `ProspectiveGameCount` and the five `ProspectiveGamesExclusive*` macros come
  from the non-overlapping game partition. Older `ProspectiveCompleted`,
  `ProspectiveInapplicable`, `ProspectiveUnalignable`,
  `ProspectiveInfrastructureFailures`, `ProspectiveCasesWithViolation`, and
  `ProspectiveCasesWithoutViolation` names remain explicitly game-level aliases
  derived from the overlapping game flags.
- `ProspectiveViolationTableRows` contains only observed checker-family/code
  rows, each with occurrence, scheduled-trace, and distinct-game counts; a
  zero-finding analysis emits an explicit no-violation row.
- `ProspectiveObligationCoverageRows` contains exactly eight rows in O1–O8
  order. Each row has exactly six table cells: obligation ID,
  `evaluated_pass`, `evaluated_fail`, `not_applicable`, `not_evaluated`, and
  total `evaluation_count`. Linked-finding and overlapping distinct-game counts
  remain in the analysis JSON and are not additional LaTeX table columns.
- `ConfirmedRepairAttempts`, `ConfirmedRepairSuccesses`,
  `ConfirmedRepairFailures`, `ConfirmedRepairNonAttempts`, and
  `ConfirmedRepairNotApplicable` come from the exhaustive confirmed-root
  repair-status accounting. Attempts equal successes plus failures; all four
  root dispositions sum to `ConfirmedRootsTotal`.
- `MutationAttemptedCandidates`, `MutationSelectedTotal`,
  `MutationSemanticKills`, `MutationCrashOnlyKills`, `MutationSurvivors`, both
  clean-reference-alarm counts, and the progress-control counts come from the
  validated mutation artifact. `MutationFamilyTableRows` reports selected,
  semantic, crash-only, survivor, and clean-alarm counts in the frozen family
  order. `MutationPairedComparatorRows`, `MutationFirstObligationRows`,
  `MutationFirstPhaseRows`, and `MutationReplacementReasonRows` are likewise
  deterministic artifact-derived tables.
- `MutationCohortComplete` reports whether frozen replacement selected exactly
  24 candidates (four per family). `MutationProgressControlsSatisfied` requires
  both controls to produce the frozen named instrumentation finding.
  `MutationRQSixReportable` means the exact ordered 48-candidate attempt ledger
  is validated and the named controls pass; an honestly short selected
  denominator therefore remains reportable. `MutationStrongPerformanceThresholdMet`
  is a strict subset of reporting readiness: it requires the named controls,
  the complete 24-candidate cohort, at least 20/24 semantic kills, and at least
  3/4 in every family. `MutationSensitivityClaimReady` is the same combined
  strong gate rather than a score-only shortcut.
  The historical `MutationReportingComplete` and
  `MutationStrongSensitivityThresholdMet` names are compatibility aliases to
  the unambiguous RQ6-readiness and performance-threshold macros. Neither
  crash-only detections nor clean-reference alarms are counted as semantic
  kills.
- `ExternalStockAPIPasses`, `ExternalStockAPIFailures`, and
  `ExternalShimmySuiteStatus` render the bound contextual external evidence.
  `ExternalBaselineRuntime` and `MutationRuntime` preserve the secondary-panel
  costs separately; `MutationPeakMemory` explicitly says that per-mutation-batch
  peak memory was not instrumented rather than borrowing the primary-run value.

`ConfirmedRootTableRows` is visibly pending until structured manual
adjudication schema version 5 is complete. It then sorts confirmed roots by
`root_id`, safely escapes all record text, and emits root/family; provenance,
witness boundary, and effect; all six root-level comparator
outcome/credit/attribution/reachability tuples; and replay, repair, claim
classification, and upstream disposition directly into the manuscript table.
`ConfirmedLocalizationTableRows` separately emits, for each confirmed root,
the destination phase/exact-call attribution, destination-call prefix, selected
obligation/code, replay status, and signaling ablation resolutions. Rejected
records never become rows; a completed zero-root adjudication emits an explicit
zero-root row. `MedianReductionRatio` is emitted as not applicable because the
protocol performs no minimization; the separately named
`MedianWitnessPrefixRatio` is automatically derived when witnesses exist.
`PreregistrationURL` and `FrozenSourceRevision` refer to the pre-run record.
`RawBatchRevision`, `ExternalBaselineRevision`, and `MutationBatchRevision`
identify the three validated result inputs. `ReviewerPackageURL` and
`ReviewerPackageRevision` remain explicitly pending until the complete package
is independently built and sealed; the compatibility aliases `ArtifactURL` and
`ArtifactRevision` point only to those reviewer-package macros and never to the
raw-batch hash. The final data-availability route follows editor guidance; in
the absence of an approved private-review exception, the complete post-run
package must receive a public final DOI.
