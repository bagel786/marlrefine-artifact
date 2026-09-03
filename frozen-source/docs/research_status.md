# MARLRefine research status

Status date: 1 September 2026
Protocol amendment date reflected: 2026-09-01
Evidence class: discovery and implementation control only
Confirmatory status: not started; protocol is not yet timestamp-archived

## Bottom line

The paper scope is locked: an adapter-parameterized non-bijective trace-conformance method,
evaluated as a prospective case study of one adapter implementation—Shimmy
2.0.1's OpenSpiel adapter. The current evidence supports a research prototype
and discovery case study; confirmatory evidence begins only after a timestamped
freeze. The contribution is not a new refinement theory or evidence about all
adapters. It is an executable source-aligned checker for APIs whose schedules
differ, evaluated under a bounded trace budget.

The working title is:

> **Phase-Aware Source-Aligned Conformance Testing for Multi-Agent Environment
> Adapters**

## What is established locally

| Item | Current result | Claim boundary |
|---|---:|---|
| Pinned registry census | 113 registry-marked default-loadable game types in OpenSpiel 2.0.2 | Catalog definition, not all OpenSpiel games, independent replications, or semantic coverage |
| Native load | 113/113 | Construction only |
| Shimmy construction/reset | 112/113; `crossword` construction fails because its observation tensor shape is unimplemented | Capability census, not a semantic defect count |
| Frozen accounting partition | 7 semantic discovery / 1 known descriptive exclusion (`crossword`) / 105 prospective semantic names | No prospective semantic name has been traced; construction/reset was already inspected catalog-wide |
| PettingZoo `api_test` | 7/7 discovery names pass | Destination-local API conformance only |
| Separately loaded native trace runner | Deterministic explicit-chance cases supported; sampled-stochastic cases explicitly inapplicable | Pathwise semantics only; not an independent source implementation |
| Alignment | Stuttering, skipping, and terminal tails represented as monotone source/destination spans | Executable testing relation, not a proof |
| In-line trace ablations | Project-defined strict lockstep, commit-sampling macro-boundary, whole-block macro-aggregate, endpoint, and return-only implemented with explicit applicability and available state-digest checks | These consume the same paired trace and test losses of schedule/information resolution; they are not independent external baselines, and digest agreement is not equivalence proof |
| Archive-gated external comparators | Stock PettingZoo `api_test` scheduled once for each of 105 games; exact Shimmy 2.0.1 source-test bytes and command frozen | Stock API is a separate destination-only comparator; released Shimmy tests are contextual, not a root-level cohort comparator |
| Semantic-preserving controls | Native clone/replay, official OpenSpiel turn-based transform, and synthetic PettingZoo Parallel-to-AEC control pass 3/3 with zero unexplained alarms | Exercised discovery/synthetic mappings only; not a registry-wide false-positive rate |
| Isolated treatments | Reward accounting, decision-clock accounting, configuration retention, and mean-field fail-fast behavior implemented | Discovery-only causal evidence |
| Combined repair | Passes the four non-mean-field discovery witnesses used for regression | Not a full-census repair result |
| Frozen prospective pipeline | Eight policies, exact 7/1/105 gate, public-Zenodo verification, blind checkpointed 840-trace batch, and at most one retry only for a caught runner exception with no run payload | No 105-name semantic trace has executed; other infrastructure statuses are final and an archive receipt is required |
| Sealed mutation validation | Mandatory 48-candidate manifest; fixed-order selection of four eligible mutants in each of six families against a paired composite repaired reference; mutation-only nondefault O6 triggers and progress-history corruption controls | No candidate outcome has been executed; synthetic sensitivity is not real-defect prevalence and crash-only detections are reported separately |
| Replay-anchored progress | Every destination `source_progress` tag is derived from successful replay of the disclosed wrapped-history delta on the separately loaded native source and carries a validated `independent_native_replay_event_count_v1` anchor | White-box instrumentation; soundness still assumes complete, ordered, nonperturbing history disclosure |
| Raw observation representation | O8 checks public container type, element count, shape, and dtype before value comparison; the observed representation is not reshaped or cast to pass | The source expectation is normalized to its declared representation; floating value comparison then uses the frozen tolerance |
| Frozen analysis | Schema v9 (`marlrefine_frozen_analysis_v9`) streams the canonical batch, binds and validates the external-baseline and mutation-batch artifacts for complete reporting, preserves semantic evidence separately from execution completeness, derives the mutation tables, and retains phase/exact-call/destination-prefix, action/stratum/saturation, tolerance-sensitivity, and ablation-resolution summaries; localizer v3 retains a compact hash-bound reference to each unmodified original prefix and verifies materialization against the sealed raw run; structured manual adjudication v5 requires a reproduced replay for confirmed roots and an evidence-bound reproduced/failed replay for every retained root at completion | Synthetic fixtures only; contract, root, consequence, comparator, replay, and repair judgments remain pending/manual until the sealed run and evidence exist |
| RQ1 boundary | Prospective-cohort finding incidence plus an explicit eight-row O1–O8 applicability/evaluation ledger for every trace; discovery evidence is reported separately | Coverage describes checks executed on bounded prefixes, not unvisited behavior or ecosystem prevalence |
| Localization boundary | `marlrefine_original_prefix_v3` attributes every recorded finding to a destination phase and, when possible, one exact destination call; its compact reference binds raw-run, full-ledger, prefix, and replay-context hashes and is materialized only after verification. Analysis also reports destination-prefix call counts, selected obligation/code, and raw signaling-ablation resolution, while the root table shows resolution only with same-root causal credit | Original-prefix localization, not semantic minimization, human diagnosis time, or causal proof by prefix position alone |
| Short-budget discovery policy matrix | All 56 seven-name/eight-policy traces ran; 48 emitted at least one finding and 8 emitted none, with no infrastructure or unalignable outcome. Raw in-line-ablation signal traces were 34 for macro-aggregate, 26 each for macro-boundary and endpoint, 20 for return-only (24 applicable), and 0 for strict lockstep (8 applicable). | Post-development discovery only, with correlated policies and unequal ablation applicability; these are neither prospective results, same-root comparator credit, nor independent defect counts. The ignored artifact is `tmp/discovery_policy_matrix.json`. |
| Automated checks | Full unit and pinned integration suite passes; Ruff is required clean at freeze | Harness regression evidence, not oracle validation by itself |

Every generated trace case records requested limits, action policy, chance
policy, chance-tape hash, reset digest, final digest, and the replay-anchored
progress method. Live interface events retain the raw observation signature;
any O8 mismatch retains complete expected/observed evidence. Artifact- or
batch-level provenance binds the executable source-tree hash, lockfile hash,
runtime versions, and manifest identity. Setup failures and inapplicable runs
cannot serialize as empty ablation passes.

## Discovery mechanisms under test

1. **Reward refresh on destination-only phases.** In simultaneous games, early
   AEC action-buffer calls can expose a prior nonzero source reward even though
   the native source has not advanced. Terminal cleanup can likewise redeliver a
   reward to the consumer. One isolated treatment suppresses reward refresh when
   source history does not advance and clears cleanup rewards; the targeted
   discrepancies disappear on the discovery witnesses.

2. **Destination calls used as a source-decision clock.** The released adapter
   initializes its counter above zero and increments it for internal chance
   transitions. This can trigger truncation before the source reaches its stated
   maximum number of player/joint decisions and can mark a naturally terminal
   game as both terminated and truncated. An isolated source-decision-clock
   treatment removes the discovery discrepancies. Contract adjudication for the
   terminal/truncation classification is still pending.

3. **Prebuilt game configuration lost on reset.** A supplied nondefault
   `go(board_size=5)` instance is reloaded as default Go at reset. Retaining the
   prebuilt game's parameter map removes the configuration mismatch in the
   discovery control. This is not a cleanly novel root: Shimmy
   [PR #96](https://github.com/Farama-Foundation/Shimmy/pull/96) and
   [PR #97](https://github.com/Farama-Foundation/Shimmy/pull/97) fixed closely
   related reset/configuration paths in 2023. The current prebuilt-`env` path
   should be classified as an incomplete/adjacent historical fix or regression
   until code-history and maintainer adjudication establish otherwise.

4. **Mean-field capability silently projected as ordinary AEC lifecycle.** The
   current adapter has no distribution-update protocol. The candidate treatment
   fails fast with an explicit unsupported-game error. This is a capability
   repair, not evidence that mean-field semantics were implemented.

These are candidate causal mechanisms, not yet publication-grade “confirmed
bugs.” Counting requires a pinned contract, a localized original prefix, a
successful hash-bound standalone replay reproducing the same finding and
boundary from the same case inputs, an isolated patch, regression evidence,
duplicate/root clustering, and preferably upstream maintainer adjudication.

Mean-field repair success is predeclared narrowly: either the adapter genuinely
supports distribution updates while preserving applicable source boundaries,
or it rejects the game before episode execution with a specific unsupported
mean-field classification. Silent termination, an unrelated constructor crash,
or avoiding the witness path is not a successful repair.

## Why the alignment matters

The key motivating witness is phase-sensitive: on a partial simultaneous-action
cycle, the strict-lockstep ablation is inapplicable and the commit-sampling
macro-boundary ablation passes because it discards the target-only buffer
output. The whole-block macro-aggregate ablation detects the total-reward
discrepancy; MARLRefine additionally reports the nonzero stutter reward at the
exact intermediate call. This supports a localization and diagnosis
contribution beyond ordinary matched-action or compressed-rollout comparison,
not a claim of unique detection on this witness.

That claim survives only if semantic-preserving transform controls have zero
unexplained alarms. A prospective recurrence would strengthen the empirical
case, but finding a new defect is not a condition for completing or reporting
the study. Zero newly detected defects across the 105-name prospective semantic
cohort is a valid primary result.

## Work required before the prospective paper claim

1. Freeze and timestamp the current protocol, lock, source, manifests, and
   analysis before running any validation trace.
2. The author-approved licenses are now in place: Apache-2.0 for software and
   CC BY 4.0 for documentation/data. Build the pinned container and complete
   the two-commit freeze described in `deposit/README.md` after Git metadata is
   restored and all source changes are final.
3. Record and verify the selected execution authorization; the current route is
   local and explicitly not a public preregistration.
4. Run the authorization-gated 105-game stock API matrix and contextual pinned Shimmy
   suite, the 105-name, 840-trace prospective semantic matrix, and the mandatory
   paired 24-mutant evaluation selected from its sealed 48-candidate pool. Keep
   `crossword` as the known descriptive capability exclusion; report bounded
   trace-level pass stop reasons, fail, inapplicable, infrastructure, and
   unalignable outcomes separately.
5. Generate one hash-bound standalone replay per candidate root; then cluster
   symptoms by causal root, perform full before/after repair regression, and
   obtain upstream issue/patch disposition where possible.
6. Package a one-command artifact and obtain an independent clean-machine
   reproduction.

The mandatory mutation study defines and seals 48 unexecuted candidates across
six prespecified families, then uses a fixed outcome-blind rule to select four
eligible candidates per family. The score is paired against the composite
semantic-preserving reference, separates semantic from crash-only detections,
and reports all attempted replacements. Previously executed adversarial fixtures
and direct recreations remain development tests and are excluded.

There is no nondefault-configuration panel for the released-adapter 105-name
cohort: default cases cannot establish preservation of caller-supplied
nondefault parameters, so O6 remains discovery-only there. A separately sealed
mutation-only nondefault trigger panel evaluates O6 detector sensitivity without
broadening the released-adapter claim. Single-agent cases remain included and are reported separately;
they do not support specifically multi-agent scheduling claims. Mean-field and
other nonstandard types remain in accounting with explicit statuses, observed
finding incidence, or unsupported outcomes rather than post hoc exclusion.

## Locked decisions and remaining manual prerequisites

- Scope: focused one-adapter software-testing/tool paper; another adapter is a
  separately registered future extension, not part of the primary run.
- Author: Safiullah Baig, Independent Researcher; ORCID
  [`0009-0008-5547-6088`](https://orcid.org/0009-0008-5547-6088). The public
  protocol record intentionally omits personal email and location.
- Licensing: Apache-2.0 for software; CC BY 4.0 for documentation and data.
- Declarations: no external funding; no conflicts of interest.
- Repository identity: `marl-adapter-conformance`; the complete frozen source
  and the exact Docker archive's filename/hash/size/format commitment are carried inside
  the protocol bundle, so no external public source repository is required to
  validate the pre-run commitment. The binary archive itself is privately
  round-trip backed up and is mandatory in the hash-bound reviewer package.
- Archive: a manual public pre-run Zenodo protocol deposit, followed by a
  private hash-bound reviewer package for submission and public post-run release
  on acceptance. The prospective runner must fail closed until a
  receipt proves the exact manifest and source hashes were publicly
  timestamped and hash-bound. Zenodo's post-publication file-editing facility
  will not be used; corrections receive a new linked record version and version
  DOI. The private `paper/` tree is excluded from the executable identity,
  container, and pre-run protocol bundle.
- Upstream contact: prepare first-divergence witness reports locally, but contact
  maintainers only after raw prospective results and primary labels are frozen.
- Compute: budget the 105-name, eight-trace semantic matrix, the mandatory sealed
  mutation evaluation, controls, and repair regressions.
