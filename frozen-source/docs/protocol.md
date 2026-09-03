# MARLRefine pre-freeze research protocol

Protocol version: 1.1
Protocol version date: 1 September 2026
Execution freeze: pending explicit local, non-preregistered authorization
Target stack: Shimmy 2.0.1, OpenSpiel 2.0.2, and PettingZoo 1.27.0

## Status and precedence

This document is the prospective protocol for the confirmatory MARLRefine trace study. The existing pilot, implementation-control traces, and registry/constructability census used to establish the 113 registry-marked default-loadable game types are hypothesis-generating or procedural evidence: they influenced the obligations, examples, implementation, and population definition. Nothing already observed is described as preregistered or held out.

Before any reserved semantic trace verdict or sealed mutation outcome is inspected, this protocol, the exact environment lock, both manifests, census, trace schedule, and analysis code must be committed and hash-bound. Execution may then be authorized either by a verified public Zenodo receipt or by an explicit local authorization artifact. The latter records `preregistered=false` and `public_archive=false`: it preserves the pre-result Git freeze but is not a preregistration or independent timestamp. The author selected that local route before any reserved outcome was executed. Any pre-freeze construction, reset, or semantic result is labeled discovery evidence. Later changes require a dated deviation entry stating the reason, affected cases, and whether the analysis remains prospectively specified or becomes exploratory. A changed obligation is never applied retroactively as though it had been frozen.

Under the selected local route, “frozen” denotes a clean Git source commit, separately generated hash-bound manifests, and the immutable authorization bytes; it does not mean preregistered. A future public archive may preserve those materials but is not an execution prerequisite. Any post-authorization correction requires a new commit and a documented effect on prospective status. The manuscript and the entire `paper/` tree remain outside the executable source identity so results prose cannot alter the code identity.

### Pre-freeze change note — 31 August 2026

Exploratory implementation checks inspected semantic traces for `tic_tac_toe`, `kuhn_poker`, and `matrix_rps` before authorization. `kuhn_poker` exhibited apparent premature truncation. The `tic_tac_toe` observation mismatch was diagnosed as an oracle-shape bug, corrected before freeze, and disappeared; its terminal reward output remains discovery evidence. After termination and truncation were made separate obligations, `matrix_rps` exhibited a lifecycle mismatch: the adapter reported both termination and truncation at a native terminal boundary. Defect adjudication for that lifecycle mismatch remains pending. These outcomes are neither confirmatory nor held out. The three names have been added to the four pilot-exposed names, making seven semantic discovery/implementation-control names. Construction and reset were also inspected across the catalog, so the known `crossword` construction limitation is descriptive discovery evidence. The residual 106-name accounting partition therefore comprises one known descriptive capability exclusion (`crossword`) and 105 names eligible for prospective semantic execution. No result generated before the recorded authorization may be called held out.

The study operationalizes existing contracts; it does not propose a new equivalence theorem. The primary sources are:

- [Shimmy's OpenSpiel adapter documentation](https://shimmy.farama.org/environments/open_spiel/)
- [PettingZoo's AEC API contract](https://pettingzoo.farama.org/api/aec/)
- [The POSG-to-AEC construction and reward treatment](https://arxiv.org/html/2009.14471v7#Sx11)
- [OpenSpiel 2.0.2's game and `max_game_length` interface](https://github.com/google-deepmind/open_spiel/blob/v2.0.2/open_spiel/spiel.h)
- [OpenSpiel 2.0.2's simultaneous-to-turn-based transform](https://github.com/google-deepmind/open_spiel/blob/v2.0.2/open_spiel/game_transforms/turn_based_simultaneous_game.cc)
- [OpenSpiel 2.0.2's transform equivalence test](https://github.com/google-deepmind/open_spiel/blob/v2.0.2/open_spiel/game_transforms/turn_based_simultaneous_game_test.cc)

### Pre-run change note — 2 September 2026

No reserved prospective semantic case or sealed mutation candidate has been executed. This amendment adds four safeguards from independent pre-run review: (1) every observed selected-agent `last()` reward is checked against native per-agent accumulation; (2) O1 pass credit requires canonical OpenSpiel serialization, while history/text fallback is diagnostic and yields `not_evaluated`; (3) semantic evidence and execution completeness are reported as separate axes so later execution trouble cannot hide an earlier semantic finding; and (4) the half-open alignment notation is made explicit. The existing replay anchors, raw observation-shape checks, agent scheduling checks, localization reports, structured adjudication, and mandatory 24-mutant sensitivity evaluation remain. Analysis output is version 9 and first-divergence localization is version 3. The author also chose the disclosed local authorization route rather than public preregistration. This amendment does not add an independent adjudicator or a second adapter.

### Post-authorization implementation deviation — 2 September 2026

The first locally authorized run sealed the complete 840-trace primary batch and
the two external-baseline outputs. Their aggregate status counts had been
displayed when the mutation stage stopped before writing an artifact: strict
self-validation found that `mut-special-node-kind-02` recorded two evaluated O8
interface boundaries but the serialized validator inferred a different count.
The cause was representational, not a changed oracle: the duplicate-player
early-stop finding did not state that its pre-event interface boundary had
already been evaluated. The correction adds that explicit boolean evidence and
a regression test; it changes no submitted actions, source replay, destination
events, obligation mapping, or outcome rule. Because the executable source hash
nevertheless changed, all primary and external stages are rerun under new
manifests and a new local authorization, rather than combining revisions. The
first attempt is retained as superseded evidence. The rerun is an integrity
replication whose primary aggregate results were no longer blinded; mutation
inferences involving the exposed candidate are reported as post-authorization
validation evidence, not as untouched preregistered evidence.

## Study objective

The study asks whether one released adapter implementation—Shimmy 2.0.1's OpenSpiel-to-PettingZoo adapter—preserves documented source-game semantics across its conversion to the AEC interaction protocol. MARLRefine will execute a separately loaded native OpenSpiel game alongside the corresponding adapter execution, align source macrotransitions to destination microsteps, and evaluate only obligations whose applicability conditions are met. The two executions are coupled only to submit the harness-selected player actions and to synchronize explicit chance outcomes as described below; expected semantic values never come from the adapter. The empirical population is 113 registry-marked default-loadable game types in OpenSpiel 2.0.2, not all OpenSpiel games or 113 statistically independent replications.

The unit of engineering evidence is a causally isolated implementation root, not a seed, episode, game, configuration, player, or failed assertion. Multiple symptoms removed by the same minimal causal patch count as one root. Roots may belong to the same fault family; the paper reports both levels and does not inflate the real-defect count with mutants.

## Frozen research questions

**RQ1 — Prospective finding incidence and obligation coverage.** Which frozen violation codes occur, and on how many scheduled traces and distinct game types, across the 105-name prospective semantic cohort? For each O1–O8 obligation, how many scheduled traces are `evaluated_pass`, `evaluated_fail`, `not_applicable`, or `not_evaluated`, and how many applicable check sites execute? The seven-name discovery evidence and `crossword` descriptive exclusion are reported separately. These are descriptive coverage counts over bounded recorded prefixes, not estimates of ecosystem prevalence or proof beyond the exercised traces.

**RQ2 — Detection complementarity.** Which contract-backed, causally isolated roots detected by MARLRefine are missed by PettingZoo's separately executed stock API test and the project-defined strict-lockstep, commit-sampling macro-boundary, whole-block macro-aggregate, endpoint, and return-only in-line ablations when the root witness is reached? The released Shimmy suite is reported as contextual upstream evidence, not scored as a 105-game root-level comparator.

**RQ3 — Localization and diagnostic specificity.** For each confirmed root, does MARLRefine identify setup, buffer, commit, cleanup, or another stutter phase; an exact destination-call index or only a multi-call boundary; the destination-prefix call count and prefix/full-trace ratio; and the adjudicated obligation family and violation code? Does the required standalone replay reproduce the same finding at the same boundary? Detection and localization resolution are reported separately for each in-line ablation; the prefix ratio is witness position/compactness, not localization accuracy.

**RQ4 — Consequence.** For each confirmed root, what exact semantic discrepancy does the manually adjudicated, evidence-linked record identify in reward, lifecycle, source-decision horizon, discovery-only configuration, state kind, legal action exposure, or declared observation representation, and does the evidence show that it crosses a public destination-API consumer boundary?

**RQ5 — Repair.** What repair disposition is recorded for every confirmed root, and, for each attempted isolated repair, what does a separately released, hash-bound before/after regression artifact report for the original witness, the enumerated discovery and prospective matrices, released tests, clean controls, and patch reversion?

**RQ6 — Prospectively sealed mutation sensitivity.** What proportion of the 24 reachable, behaviorally non-equivalent contract-derived mutants does MARLRefine detect overall and within each of six fault families; which are detected by the destination-side API test and each in-line ablation; which detections are semantic rather than crash-only; and how many unexplained alarms occur on the paired clean references?

RQ1–RQ6 are fixed before execution authorization. The mutation benchmark is a mandatory validation component, not evidence about real-world defect prevalence. A second adapter or historical bug corpus may be reported as an exploratory extension, but it cannot replace the frozen census, mutation evaluation, or repair evaluation.

## Claim boundaries

### Claims the design can support

Subject to the gates below, the study may claim that:

1. published source and destination contracts can be instantiated as an executable, source-aligned event-ledger checker for this adapter;
2. on the frozen versions and exercised traces, MARLRefine detected specified contract violations or capability mismatches;
3. the stock API test or specified in-line comparators did not detect particular roots whose witnesses they reached, while the released Shimmy suite supplies contextual evidence only;
4. released, hash-bound before/after evidence supports the manual judgment that isolated patches removed specified discrepancies without observed regressions in the frozen test matrix; and
5. on the prospectively sealed mutation model, the frozen oracle detected a measured fraction of reachable, behaviorally non-equivalent mutants at a measured clean-reference alarm cost.

### Claims outside the design

The study will not claim:

- invention of stuttering refinement, conformance testing, differential testing, POSG-to-AEC equivalence, or simultaneous-to-turn-based conversion;
- proof that any adapter is semantically equivalent on unexecuted traces;
- that stock API conformance was intended to establish source equivalence;
- that all MARL adapters, all Shimmy adapters, or current unreleased versions share the observed behavior;
- a population prevalence of adapter defects from paths, seeds, or configurations;
- correctness of chance-outcome probabilities when pathwise replay conditions on a sampled chance transcript;
- preservation of an information state when the documented adapter representation is an observation tensor, or vice versa;
- downstream policy or learning effects without a separately designed learning experiment; or
- that a mismatch is a defect when the governing contract or advertised support boundary is ambiguous.

Results are version-, configuration-, platform-, and trace-bounded. A passing trace means only “no violation detected under the specified budget,” never semantic equivalence. “Defect” is reserved for a violation supported by an unambiguous primary contract; an upstream test or maintainer confirmation may corroborate but cannot replace that contract basis. Other divergences are labeled “mismatch” or “unsupported capability.” A zero-new-defect outcome in the 105-name prospective semantic cohort is a valid, reportable primary result.

## Semantic model and frozen obligations

One source player or joint decision is a **macrotransition**. One PettingZoo `step` call is a **microstep**. A simultaneous macrotransition may contain several action-buffering microsteps followed by a single commit. A **source boundary** is the initial state or the state reached after one player/joint decision and all immediately resulting chance resolution needed to reach the next player, simultaneous, mean-field, or terminal node.

For destination call (v_j), (p(v_j)) is the cumulative number of atomic source transitions that the harness has successfully replayed on the separately loaded native OpenSpiel state after (v_j). It is not `game_length`, wrapped-history length, or an adapter-supplied semantic answer. The runner first verifies that the pre-call wrapped history is a prefix of the post-call history, checks the submitted player or joint action, replays each explicit legal chance outcome, and verifies native/adapted boundary identity. Each destination event then stores a `progress_instrumentation` anchor containing the method ID, progress before/after, replayed event count, replayed source-progress sequence, and wrapped-history delta. An anchor/tag disagreement is an instrumentation/infrastructure failure; an unavailable explicit chance outcome is unalignable; an independently witnessed public semantic mismatch remains a semantic finding.

The white-box alignment claim assumes: (A1) wrapped history reports committed source transitions in order and omits none; (A2) inspection observes an append-only history across each call; (A3) submitted actions and explicit chance outcomes replay legally on the native state; (A4) history, current player, terminality, and the tagged serialization digest adequately identify the exercised boundary; and (A5) inspection does not perturb execution. A plausible monotone history that violates these assumptions but is indistinguishable from a legitimate many-to-one call cannot be rejected from tags alone; this is an explicit instrumentation limit, not evidence of conformance.

**Conditional partition property.** For fixed source events with consecutive progress values and destination tags satisfying the stated monotonicity, bounds, and boundary-naming assumptions, the indices at which (p(v_j)) strictly increases uniquely determine contiguous destination blocks and source intervals ((p_{k-1},p_k]). Cursor induction gives original order, no destination deletion or duplication, and exactly-once coverage of every source event through the maximum destination progress. If final destination progress equals the final source progress, the source ledger is covered completely. This is a property of the deterministic partitioning algorithm under stated instrumentation assumptions, not a new refinement theorem.

The comparison distinguishes three reward quantities:

1. native OpenSpiel immediate rewards and cumulative returns;
2. PettingZoo's instantaneous `env.rewards` vector after each microstep; and
3. the reward delivered to a consumer through `last()`, which can include AEC accumulation since that agent last acted.

These channels are recorded and tested separately. Repeatedly summing snapshots without respecting their contract is not an oracle.

| ID | Frozen obligation | Applicability | Verdict condition |
|---|---|---|---|
| O1 | Stutter-state preservation | Simultaneous source nodes before the joint action commits, when both states expose canonical OpenSpiel serialization | The serialized wrapped/source state and native history do not advance on a buffer-only microstep. A history/text fallback is retained only as diagnostic evidence and yields `not_evaluated`, never an O1 pass. Adapter-local action buffers may change. |
| O2 | Stutter reward neutrality | Buffer-only and terminal-cleanup microsteps | The instantaneous destination reward vector is zero; consumer accumulation is evaluated separately. |
| O3 | Exactly-once reward and return preservation | Every aligned source macrotransition with comparable reward channels, every observed selected-agent `last()` delivery, plus genuinely completed episodes | Destination instantaneous rewards across the aligned segment sum once to the native immediate reward vector. Each `last()` reward equals the native reward accumulated for that player since its preceding delivery, and complete consumer-delivered episode returns equal native source returns after player mapping. |
| O4 | Source-decision clock preservation | Only where the adapter exposes or documents a source-game-length mapping | After subtracting the adapter clock's reset baseline, elapsed `game_length` equals the independently counted source player/joint decisions. Buffered actions and internally resolved chance events do not increment that source-decision count. A cited adapter mapping is required before a mismatch is classified as a defect; the destination call budget remains separate. |
| O5 | Lifecycle preservation | All aligned boundaries and terminal tails | Source terminality agrees with destination termination at the corresponding boundary; truncation is permitted only under a declared external budget; cleanup neither advances source state nor re-emits immediate reward. |
| O6 | Configuration provenance | Discovery cases with caller-supplied nondefault parameters, including the prebuilt `env=` route | Canonical game identity and all supplied OpenSpiel parameters after reset equal the construction manifest. Source state is newly initialized; state identity itself need not persist. Default-only prospective cases cannot establish preservation of caller-supplied nondefault parameters, so O6 is discovery-only in this study. |
| O7 | State-kind soundness | Every state kind inside the adapter's advertised support domain | Decision, simultaneous, chance, mean-field, and terminal nodes are handled according to their contract, or unsupported kinds are rejected explicitly before episode execution. A nonterminal special node is not silently reported as terminal. |
| O8 | Agent schedule and interface projection | States for which the source and adapter declare the relevant representation | Sequential selection equals the current source player. A simultaneous cycle contains each required live player exactly once before commit, in any permutation unless a primary contract fixes order. Player-mapped legal actions agree with the one-dimensional binary integer/Boolean mask of the declared action-space length. For tensor observations, the raw destination container type, element count, shape, and dtype agree before normalized floating values are compared. Deliberate action/agent renaming is normalized. |

For a mean-field game, O7 has exactly two acceptable success modes: (1) genuine support for distribution updates whose resulting source boundaries satisfy all applicable obligations, or (2) deliberate fail-fast rejection before episode execution with a specific unsupported-mean-field classification. Silently projecting a nonterminal mean-field node as ordinary termination is neither support nor successful fail-fast behavior.

Exact integer, Boolean, action-set, parameter, history, lifecycle, container, element-count, shape, and dtype comparisons use equality. Floating rewards use `atol=1e-12, rtol=1e-12`, a tight float64 roundoff allowance for native return-delta versus public-reward arithmetic. Floating observation values use `atol=1e-7, rtol=1e-7` only after exact raw-signature checks; this accommodates value-level projection roundoff without allowing representation coercion. Discrete values use equality. The primary verdict always uses these thresholds. A prespecified secondary sensitivity report reclassifies stored reward and observation residuals at 0.1, 1, and 10 times the primary tolerance without rerunning traces or changing the primary result.

O4, O6, O7, and O8 require an adapter documentation or source-code citation in the frozen execution contract. If the contract is ambiguous, the checker records a mismatch but does not count a defect. Laws that fail a clean native or official reference control are rejected or narrowed before adapter results are unblinded.

### Obligation-evaluation ledger

Every serialized trace contains exactly eight ordered evaluation rows, O1
through O8. Each row records `applicable` (`true`, `false`, or `null` when a
setup failure prevents determination), `evaluated`, one of the four outcomes
`evaluated_pass`, `evaluated_fail`, `not_applicable`, or `not_evaluated`, a
stable reason code, the number of applicable boundary/microstep sites actually
checked, and sorted unique indices linking any findings to the trace's finding
list. Evaluated outcomes require a positive site count;
`evaluated_fail` holds exactly when linked findings are present. Alignment or
execution diagnostics that do not implement an O1–O8 contract remain visible as
unlinked findings rather than being forced into an obligation.

The frozen evaluation sites are: every destination microstep explicitly
classified `buffer_only` for O1 (mere lack of progress is insufficient); every
buffer-only or cleanup call for O2; every aligned transition reward
comparison and, when genuinely terminal-complete, consumer-return comparison
for O3; every normalized adapter/source clock comparison for O4; every aligned
lifecycle boundary and cleanup-inertness check for O5; one caller-supplied
nondefault construction/reset comparison for O6; every distinct reached source
node-kind boundary for O7; and every live decision boundary at which selected-agent
identity/schedule, mapping, action space/mask, and raw-signature-first declared
observation are checked for O8. Prospective
default cases leave O6 `not_applicable`. A row marked `evaluated_pass` means
only that all applicable sites observed on that recorded prefix emitted no
linked finding.

## Separately loaded native oracle design

MARLRefine uses three logical layers within one process. The execution layers exchange only the
information required for action/chance synchronization; the adapter never
supplies an expected reward, state, lifecycle, legal-action set, observation,
clock, or configuration value.

### 1. Native execution and semantic oracle

For each case, a separately loaded native OpenSpiel 2.0.2 game is loaded from
the canonical short name and full parameter map and advanced online. The
harness chooses each player or joint action from the native legal-action set
and submits that action through the adapter. Adapter-observed history is used
only to check that player actions agree and to replay explicit chance outcomes
onto the native execution. The resulting immutable native event ledger contains:

- a SHA-256 digest of OpenSpiel serialization where available, otherwise a tagged history/text fallback, plus history length;
- node kind before the transition;
- player action, joint action component, or chance action;
- explicit chance outcomes and chance probabilities where available;
- immediate reward vector and cumulative return;
- source-decision count and terminality.

At each live player boundary, the runner also compares native legal actions and
the declared observation or information-state representation with the adapter
interface before selecting an action. A discrepancy persists its complete
expected/observed evidence in the violation record; passing high-dimensional
interface values are checked online but are not duplicated into every event
row. Resolved game parameters are persisted in the case summary.

Native expected values are read only from the separately loaded native game.
Synchronization code may inspect adapter action history and explicit chance
outcomes, but it never turns `env.game_state`, adapter reward dictionaries,
adapter clocks, destination lifecycle flags, or other destination outputs into
the expected answer.

### 2. Adapter runner

The logical adapter runner constructs the adapter in the same process as the
separately loaded native object, submits the harness-selected
source-legal player actions in AEC order, and records observed destination
behavior plus synchronization evidence:

- AEC call index, selected agent, submitted action, and buffer/commit classification;
- append-only wrapped history solely to derive and anchor independently replayed native progress;
- instantaneous `env.rewards`, consumer-visible `last()` reward, termination, and truncation separately;
- adapter clock and reset parameters; selected-agent schedule; raw observation signature at every evaluated live boundary; and full legal-mask or observation values when an interface discrepancy occurs;
- terminal-cleanup calls and any exception or explicit unsupported-state rejection.

Wrapped-state inspection is permitted for white-box synchronization and first-divergence localization, but values read from it never become the expected semantic oracle. The per-call progress anchor carries the stable `independent_native_replay_event_count_v1` method ID and its history/replay evidence; other private synchronization fields remain explicitly named metadata. MARLRefine's current claim is therefore white-box source alignment, not a black-box universal adapter test.

### 3. Pure comparator and witness localization

The pure comparator reads the two immutable ledgers, maps source
players/actions to destination agents/actions, segments destination calls into
stutters, commits, and terminal tails, and evaluates the structural, reward,
progress, cleanup, and aligned-lifecycle obligations. Framework-dependent O1
state-digest, O4 clock, O6 configuration, O7 state-kind, O8 interface, and
consumer-delivered-return checks execute online at the boundary where their
source and destination objects are available; they emit the same immutable
violation schema and never use a destination value as an expectation. The
combined result emits an exact original prefix and replay context for every
recorded finding, while the chronologically first divergent boundary remains a
derived trace summary. This per-finding representation prevents a later,
causally distinct finding in the same trace from becoming inexpressible as a
root witness. Replayability is claimed only after the required per-root replay
artifact succeeds. The frozen system
does not claim delta debugging, semantic minimization, or verdict-preserving
trace reduction.

The batch header binds package versions, source and lock hashes, the archive
receipt, and the manifest. Each case record binds the game/configuration
identity, trace-policy ID, seed, budgets, source and destination ledgers,
alignment, findings, and execution summary. Event rows carry the local progress,
action/chance, reward, lifecycle, state-digest, and synchronization fields that
exist at that side of the comparison; global provenance is not redundantly
copied into every event row.

### Chance handling

The frozen runner does not claim that the released adapter accepts an injected
exogenous tape. It records the adapter's explicit chance actions and replays
that same transcript on the separately loaded native execution. Those cases
test pathwise semantics conditional on the observed transcript; they do not
test sampling probabilities, RNG identity, or distributional equivalence.
Cases whose chance events cannot be observed or synchronized are reported as
`unalignable_chance`, not silently dropped or marked as semantic failures.

## Census and strata

### Frozen population

The primary census is the **113 registry-marked default-loadable game types in OpenSpiel 2.0.2**. It is constructed from the clean locked environment as the sorted, unique `short_name` values returned by `pyspiel.registered_games()` whose `GameType.default_loadable` flag is true.

The expected cardinality is exactly 113. If it is not 113, execution stops before adapter results are inspected. The registry dump, wheel hash, platform, and discrepancy are archived; the environment is repaired or the protocol is amended transparently. The denominator is never changed after observing passes or failures.

Each of the 113 names was attempted for the already-inspected load/construct/reset census with its default parameters. The prospective semantic run applies only to the 105-name cohort defined below. Failure to align or finish within the bounded trace budget is an outcome with a reason code, not a post hoc exclusion. The released census provides all 113 rows and distinguishes discovery, known descriptive exclusion, and prospective semantic status; the main paper may summarize that table.

There is no prospective nondefault-configuration panel. Default configurations cannot test whether caller-supplied nondefault parameters survive adapter construction and reset, so configuration-preservation evidence is explicitly discovery-only. The inspected `go(board_size=5)` case remains discovery evidence and is never counted as prospective validation. Every prospective trace still receives an O6 ledger row, but default-only traces are `not_applicable`: O6 has no prospectively evaluated, applicable site denominator in this study.

### Frozen strata

Primary strata are assigned from OpenSpiel registry metadata before execution and are mutually exclusive:

1. sequential dynamics without stochastic chance;
2. sequential dynamics with explicit or sampled stochastic chance;
3. simultaneous dynamics without stochastic chance;
4. simultaneous dynamics with explicit or sampled stochastic chance; and
5. mean-field dynamics, with chance mode retained as a secondary tag.

Secondary descriptive tags are number of players, terminal-only versus intermediate rewards, perfect versus imperfect information, utility type, declared observation/information-state capabilities, finite declared maximum game length, and presence of configurable parameters. No stratum is removed for having few members; metadata counts and observed finding incidence are reported exactly. Obligation-specific evaluated-pass denominators come only from the explicit O1--O8 applicability/evaluation ledger, never from absence of a finding.

Single-agent and other nonstandard game types are not removed from the catalog or the prospective cohort merely because the paper studies a multi-agent adapter. After default native construction, an effective `num_players() == 1` case is reported in a separate single-agent subgroup. It can inform generic reward, lifecycle, and projection findings, but it is not evidence for claims specifically about inter-agent buffering or multi-agent schedules; configuration preservation remains discovery-only. Mean-field, special-node, one-shot, and other cases outside ordinary two-or-more-player sequential/simultaneous play remain in the 113-type accounting and are reported by node kind, reachability, status, and observed finding incidence. An explicit unsupported classification is an outcome; silent termination and post hoc deletion are not.

### Frozen trace schedule

Each of the 105 prospective semantic cases receives eight bounded traces: smallest legal action, largest legal action, and six source-legal pseudo-random traces with seeds 0–5. Seeds are namespaced by the canonical game/configuration hash so execution order cannot change a trace. Each trace ends at native terminality or after the smaller of a finite positive declared maximum and 1,000 source player/joint decisions; if no finite positive maximum is declared, the cap is 1,000. A separate 10,000-destination-call safety cap prevents a stuck destination schedule. Chance outcomes follow the chance policy above.

The frozen primary status precedence is runner exception, then `unalignable`, `infrastructure`, `inapplicable`, `fail`, and `pass`. It supports operational accounting, but analysis also emits two independent axes: semantic evidence (`observed_failure`, `no_observed_failure`, `no_verdict`) and execution completeness (`terminal_complete`, `bounded_prefix`, `semantic_abort`, `unalignable`, `infrastructure`, `inapplicable`). Thus a semantic finding observed before a later alignment or infrastructure failure remains visible. Only a caught runner exception that produced no run payload is eligible for one retry; other infrastructure statuses are final.

The same recorded paired execution is evaluated by MARLRefine and every
compatible in-line trace baseline. A trace that diverges in legal actions ends
at the first divergence and records that divergence under O8. Additional
traces, search strategies, or larger budgets are exploratory and reported
separately.

## Discovery and validation split

The pilot and pre-freeze implementation checks exposed the checker author to seven game names. All cases from these short names and configurations are the **discovery/implementation-control set**:

- `coop_box_pushing`: simultaneous reward replay and decision-clock behavior;
- `nim`: terminal-cleanup reward behavior;
- `go`, including `go(board_size=5)`: reset configuration behavior; and
- `mfg_crowd_modelling`: mean-field state-kind behavior;
- `kuhn_poker`: apparent premature truncation during exploratory implementation checks;
- `tic_tac_toe`: terminal reward and observation outputs inspected during implementation; the observation mismatch was an oracle shape bug, was corrected before freeze, and is not an adapter result; and
- `matrix_rps`: lifecycle output inspected during implementation; under the separated lifecycle obligation, the adapter reports both termination and truncation at a native terminal boundary, with defect adjudication pending.

Their replication and implementation-control results are reported in a visibly separate table. They may establish reproducibility, oracle correction, and causal repair, but they do not establish prospective discovery. In particular, the `tic_tac_toe` observation mismatch is not counted as an adapter finding.

After removing the seven semantic discovery names, the **residual validation accounting partition** contains 106 registry-marked default-loadable game types. It is divided before prospective execution into:

- one **known descriptive exclusion**, `crossword`, whose adapter construction/reset capability was already inspected and found unable to construct because its observation tensor shape is unavailable; and
- a **105-name prospective semantic cohort**, which is the only set that receives the frozen semantic trace schedule.

All three partitions—seven semantic discovery names, one known descriptive exclusion, and 105 prospective semantic names—must be present, pairwise disjoint, and together equal the 113-type catalog. If that invariant fails, execution stops before outcomes are opened. The `crossword` row remains in descriptive and infrastructure accounting; it is not presented as a clean prospective semantic result. No nondefault prospective panel exists, so alternate configurations cannot be used to relabel discovery evidence as prospective.

An occurrence of a known mechanism in a prospective semantic game is prospective generalization of that mechanism, not a new root. A genuinely new root requires a distinct code path and an isolated causal repair. The 113-row released census shows partition and construction status; the discovery table remains descriptive, while RQ1 reports finding incidence only for the 105-name prospective semantic cohort.

## External baseline, in-line ablations, and controls

### Detection comparator panel

The released Shimmy OpenSpiel test module is contextual upstream evidence only.
Its fixed game list is not the 105-game cohort, some passing/loading paths use
unseeded action-space samples, and the module is present in the pinned source
source distribution rather than the installed wheel. The authorization-gated external-baseline
runner verifies the exact Shimmy 2.0.1 source-archive and test-module hashes,
runs the frozen command, and captures its result, but no root-level miss or
detection credit is assigned to that suite.

Each reachable real root and selected sealed mutant is evaluated against one external destination-side baseline and five in-line schedule/information ablations:

1. PettingZoo's stock `api_test`, executed separately exactly once per prospective game with 1,000 cycles, action-space seed 0, and captured warnings/output;
2. a naïve strict-lockstep baseline comparing reward, lifecycle, and available serialized-state digests only when the schedules are one-to-one;
3. a macro-boundary baseline that discards target-only phases before comparing reward, lifecycle, and available serialized-state digests at advancing commits;
4. a macro-aggregate baseline that sums all destination microstep rewards in each aligned transition block, then compares that total plus commit lifecycle and available serialized-state digest with the corresponding source boundary;
5. an endpoint baseline comparing only final lifecycle and available serialized-state digests; and
6. a return-only differential baseline comparing final consumer-delivered returns with native returns, without intermediate alignment.

The five project ablations consume the same serialized paired trace as
MARLRefine. The stock API test is a separate destination-only execution; its
pass or failure is not path-matched by construction.

Gymnasium's environment matcher is discussed as a related one-step lockstep utility but is not executed as an external comparator: native OpenSpiel and the PettingZoo AEC adapter do not implement a common Gymnasium `Env` interface, and wrapping both solely to satisfy that interface would introduce another adapter under test. The `strict_lockstep` in-line ID remains a transparent schedule-compatible ablation and is not labeled as Gymnasium or Karten-style evaluation.

Root-level “macro baseline miss” credit is assigned from the stronger
`macro_aggregate` comparator. The commit-sampling `macro_boundary` outcome is
retained separately to show what is lost when target-only phases are simply
discarded.

A comparator is credited with detection only when a separately hash-referenced isolated-treatment comparison attributes its signal to the same root. A pass or no-detection outcome receives miss credit only when the root witness was reached. An unrelated failure, unreached path, inapplicability, or unevaluated comparator is not scored. Detection and localization resolution are separate: strict lockstep can name a call only when one-to-one, macro-boundary names the advancing commit, macro-aggregate names an aligned block, endpoint names the trace endpoint, and return-only names only the episode return. MARLRefine's incremental yield is the number and identity of roots or mutants it detects that each evaluated comparator misses, plus any finer phase/call attribution—not the claim that those comparators were intended to prove refinement.

### Clean negative controls

The pre-freeze clean-control panel is deliberately bounded to three exercised
mappings: (1) a seven-transition native `tic_tac_toe` execution replayed
against an independently loaded native clone; (2) one `matrix_rps` joint
transition compared with OpenSpiel's official simultaneous-to-turn-based
transform, including its buffer and commit; and (3) a fully synthetic
two-agent PettingZoo Parallel environment converted to AEC for two source
transitions and six destination calls, including two cleanup calls. Every
applicable checked obligation must produce no violation on these exact
controls. They validate the exercised oracle paths; they do not estimate a
registry-wide false-positive rate.

Any unexplained failure on a clean reference blocks unblinding and invalidates the affected obligation until it is corrected or narrowed through a registered amendment.

### Detector-positive controls

Before authorized sealed scoring, transparent, trivially non-equivalent fixtures are used to confirm that the comparator and failure reporter can fire. All adversarial changes already executed during development—including action remapping, silent agent disappearance, arbitrary constructor failure, and path-avoiding repair controls—are development tests. They and direct recreations are excluded from the prospectively sealed mutation score. Pre-execution declarative operator authoring, deterministic manifest generation, import/syntax validation, and hashing are permitted because they reveal no candidate outcome.

## Root diagnosis and adjudication

Every candidate finding receives:

1. the exact original prefix and replay context ending at the selected finding boundary (with the chronologically first divergence retained separately), followed by a successful standalone replay artifact before replayability is claimed;
2. a primary-contract citation and applicability argument;
3. the bounded implicated implementation region;
4. an isolated test that fails before repair;
5. an isolated causal patch; and
6. before/after results across all comparators, ablations, controls, and census cases.

Two symptoms are one root when the same isolated patch removes both without another semantic change. They are separate roots only when independently toggled patches and distinct causal paths demonstrate separability. Reward roots may be distinct mechanisms but remain one reward-accounting family for family-count gates.

Findings are adjudicated before outreach using the structured classes `defect`, `mismatch`, `unsupported_capability`, `oracle_issue`, and `not_a_defect`. Upstream acknowledgement can strengthen but does not replace the contract evidence. Maintainer disagreement, issue links, and changed classifications are archived.

The current study has one primary adjudicator: the checker designer. No inter-rater reliability statistic is claimed. If a qualified second adjudicator is recruited before prospective root labels are finalized, both adjudicators receive the same frozen rubric and de-identified contract/evidence packets, label independently, and the paper reports the full agreement/disagreement table plus resolution rule. If that condition is not met, the study remains explicitly single-adjudicator. Maintainer outreach occurs only after the primary labels are locked, and maintainer disagreement is preserved rather than silently replacing the original classification.

A completed adjudication must dispose of every prospective finding identifier
exactly once: either assign it to one confirmed prospective root or record a
nonblank rejection reason. Root memberships are nonempty and disjoint, each
confirmed root owns its first-witness finding, and the causal patch has explicit
stock-source, treatment-source, and patch identities. The analyzer validates
this coverage, the raw-batch identity, schema, and internal consistency. It does
not determine whether a contract interpretation, causal attribution, or repair
judgment is substantively true.

## Mandatory prospectively sealed mutation evaluation

The same authorization that gates the 105-name study binds a separately generated mutation manifest through the main study manifest's exact hash. Before authorization, it is permissible to define operators, deterministically enumerate candidates, import and syntax-check implementation code, generate canonical descriptions and hashes, and test the runner with synthetic monkeypatched fixtures. No candidate game may be loaded or stepped, and no candidate may be run against MARLRefine, an ablation, or the stock API test until authorization verifies. Previously executed adversarial fixtures and direct recreations of discovery edits remain development controls and are excluded. The candidates are therefore described as **prospectively sealed contract-derived mutants**, not independently authored held-out faults.

The frozen pool contains 48 single-hook candidates in a fixed priority order, eight in each family. Exactly 24 eligible candidates are selected, four in each family:

1. reward emission or accumulation (O2/O3);
2. state progress, legal actions, or declared representation (O1/O8);
3. source-decision clock accounting (O4);
4. termination, truncation, or cleanup lifecycle (O5);
5. construction/reset configuration provenance (O6); and
6. special node-kind handling (O7).

Each mutant is applied to the frozen composite semantic-preserving reference treatment, not to the already-failing released adapter. The separately loaded native OpenSpiel execution remains the expected-value oracle. The candidate manifest freezes operator identity, hook, target game/configuration, policy and seed, parameters, canonical patch text, patch hash, selection order, and the composite-reference identity. Configuration candidates use a mutation-only nondefault trigger panel; they do not broaden O6 claims about the default-only 105-name released-adapter cohort.

Candidate screening is paired and outcome-blind. A candidate is eligible only when its clean reference is acceptable, its mutation hook fires, and its adapter-facing execution differs from the reference. A behaviorally duplicate candidate is skipped in favor of the next candidate in that family's frozen order. Screening must not use checker, ablation, or API-test verdicts. Every attempted candidate and replacement reason remains in the artifact. The post-run validator strictly checks JSON scalar types; the sealed game, seed, trace-policy identity/seed, and decision cap; complete source/destination/alignment, finding, baseline, and O1--O8 ledger schemas; registered obligation/code pairs; operator-specific hook evidence; and all derived hashes and totals. The two progress controls require nonempty paired event ledgers, exact equality outside the named annotation seam, at least one rederived frozen transform trigger, and the specific instrumentation finding. Failure to obtain four eligible candidates in any family yields an explicitly short denominator and blocks the prespecified 24-mutant sensitivity claim.

A semantic kill is a new full MARLRefine finding signature in the mutant relative to its matched clean reference, with the mutation site reached. Constructor or execution exceptions are reported separately as crash-only detections rather than silently inflating semantic sensitivity. The five in-line ablations and the stock PettingZoo API test receive paired reference/mutant scoring under their declared applicability. Results report overall and family denominators, semantic kills, crash-only detections, first detecting obligation, phase/call localization, and paired clean-reference alarms. Two separately identified progress-history corruption controls exercise lag and overcount instrumentation and must yield named instrumentation failures rather than semantic adapter-defect credit; they are outside the 24-mutant denominator. Synthetic mutants are never called real defects or added to the root count.

## Repair evaluation

Every confirmed real root receives exactly one repair status from the frozen
schema: `successful`, `failed`, `not_attempted`, or `not_applicable`; `pending`
is permitted only before complete adjudication. Counts report all confirmed
roots, attempted repairs, successful attempts, failed attempts, and
non-attempts separately. This prevents a repair-success count from silently
excluding difficult roots. Each attempted root receives one minimal patch
branch. A repair is
manually labeled successful only if a separately released, hash-bound
before/after runner artifact reports all of the following:

- its original first-divergence witness prefix changes from fail to pass;
- every scheduled semantic trace in the seven-name discovery set and 105-name prospective cohort is rerun, while the known `crossword` construction exclusion remains descriptively accounted for;
- the Shimmy suite and PettingZoo `api_test` pass wherever they passed before;
- all clean references retain zero unexplained failures;
- no unrelated new semantic finding or worse frozen trace status appears; and
- reverting only that patch restores the target failure.

For a mean-field capability finding, “repair success” has the same two allowed modes as O7: genuine distribution support that preserves applicable source semantics, or a precise fail-fast unsupported rejection before episode execution. Making the adapter terminate silently, crash for an unrelated reason, or avoid the witness path does not count as repair success.

If one patch removes several symptoms, those symptoms are clustered as one root. Large refactors that prevent isolation may be useful engineering, but they do not satisfy the causal-repair gate without an additional ablation. The frozen analyzer derives counts from the structured labels and checks boolean consistency; it does not execute repairs, resolve evidence hashes from the network, or recompute causal validity.

## Metrics and reporting

All deterministic findings are reported as exact counts and manually adjudicated effect descriptions. Seeds and paths measure exercised coverage; they are not treated as independent defect samples, and no unnecessary null-hypothesis tests are used.

### Primary metrics

- occurrence, scheduled-trace, and distinct-game counts for every observed checker-family/code pair;
- for each O1–O8 obligation, mutually exclusive scheduled-trace counts for `evaluated_pass`, `evaluated_fail`, `not_applicable`, and `not_evaluated`, plus applicable site-check totals and linked-finding counts;
- terminal-complete versus bounded-prefix accounting and occurrence/trace/distinct-game counts for aligned segments, one-to-many and many-to-one segments, destination buffer, advancing-commit, other-stutter, and cleanup calls, source event-origin node kinds, final source-boundary kinds, and source chance events;
- status and finding counts in the five preregistered dynamics/stochasticity strata, with explicit versus sampled chance retained as a secondary registry tag;
- unique offered and selected game-state-player-action tuples on visited states, plus cumulative and marginal selected-action coverage after each policy in the fixed order;
- finding- and confirmed-root-level localization: destination phase, exact call versus multi-call boundary, destination-prefix length and ratio, obligation/code, standalone-replay outcome, and same-root-credited ablation resolution;
- secondary reward and floating-observation sensitivity counts at 0.1, 1, and 10 times the primary tolerance, without changing primary verdicts;
- number of contract-backed real roots, grouped by fault family and discovery/prospective provenance;
- number of roots detected by MARLRefine and missed by each in-line ablation and the separately executed destination-side comparator;
- number of clean-control false alarms, by obligation;
- mandatory paired mutation score overall and within each of six families, with semantic and crash-only detections separated, plus mutation-only stock-API and in-line-ablation matrices;
- repair success matrix: target fixed, stock tests, census regressions, and reference regressions; and
- census accounting across all 113 registry-marked types, partitioned as seven semantic discovery names, one known descriptive exclusion, and 105 prospective semantic game types; trace-level accounting separately reports the five mutually exclusive frozen classifier outcomes across exactly 840 scheduled game-policy traces (105 games times eight policies). `pass` plus `fail` is the automatically derivable semantically completed trace count. The batch schema does not contain a separate frozen `aligned` status, so the analysis must not manufacture one.

### Trace and game accounting

Trace-level counts use one scheduled game-policy trace as the unit. The frozen
statuses `pass`, `fail`, `inapplicable`, `infrastructure`, and `unalignable` are
mutually exclusive and must sum to 840. A `pass` means the trace-level classifier
observed no violation under its bound. The separate obligation ledger states
which O1–O8 checks were applicable and evaluated on that prefix; it does not
extend the verdict to unvisited behavior. A `fail` means a semantic or
advertised-capability violation remained after the classifier's explicit
unalignable and infrastructure categories were removed. Diagnostic violation
records may also explain an unalignable or infrastructure outcome, so the mere
presence of a violation object does not by itself define `fail`. Neither count
is a root count.

Game-level counts use one of the 105 distinct prospective game types as the
unit. The primary non-overlapping reporting buckets use a fixed
evidence-completeness precedence: any infrastructure outcome; otherwise any
unalignable outcome; otherwise any inapplicable outcome; otherwise any failed
trace; otherwise all eight traces passed. These five buckets must sum to 105.
The analysis also reports overlapping flags such as “at least one failed
trace” and “at least one infrastructure outcome,” plus each exact eight-trace
status profile. Overlapping flags may describe the same game and must never be
summed. The precedence is an accounting rule, not a causal-severity ranking.

Obligation-code and compatible-baseline aggregates are descriptive symptom
counts at occurrence, trace, and distinct-game levels. They do not adjudicate
causal roots or assign root-level baseline credit. Root clustering, repair
success, clean-control alarms, upstream confirmation, and any secondary
mutation outcome remain explicit identity-bound manual inputs to the analysis.
The analyzer records its own runtime identity separately and, in the frozen
artifact-writing path, verifies source-tree, lock, Python, installed-package
bytes, and container platform identity against the sealed batch runtime.

### Semantic effect sizes

- reward: per-transition vector difference, episode-return difference, and any zero-sum or constant-sum residual;
- lifecycle: first divergent source boundary and number of early/late decisions;
- clock: adapter clock increments per source decision and source decisions reached before truncation;
- configuration (discovery only): canonical parameter-map difference after reset for the caller-supplied nondefault witness;
- state kind: expected and observed node classification at first divergence;
- actions: symmetric difference of mapped legal-action sets and masks; and
- agent schedule: selected-agent identity, implicated cycle position, and missing, duplicate, or premature-commit participants;
- observations: raw container type, element count, shape, dtype, maximum absolute/relative difference, and first differing index under the declared representation.

### Tool metrics

- wall-clock time, peak memory where available, and ledger size per case and in total;
- original trace length and selected per-finding/root witness-prefix length;
- adapter-specific instrumentation lines/configuration effort; and
- proportion of cases requiring conditional chance replay or white-box access.

Every outcome-accounting table includes trace-level passes, failures,
inapplicable outcomes, infrastructure failures, and unalignable cases where
relevant. The finding-incidence table reports only observed codes. “No observed
violation” replaces “equivalent” for finite passing traces.

## Required artifacts

The release archive must contain:

- this frozen protocol, its hash, archive timestamp, and all deviations;
- package lockfile, wheel or source hashes, Python version, operating-system information, and exact repository commits when available;
- the 113-type registry dump, census/strata manifest, explicit 7/1/105 partition, and bounded trace manifest; there is no prospective nondefault-configuration panel;
- source-oracle, adapter-runner, comparator/witness-localizer, and analysis code;
- immutable native and destination JSONL ledgers, execution logs, and machine-readable verdict tables;
- the complete raw-batch-hash-bound manual adjudication JSON used to derive root, baseline-credit, repair, control, and upstream tables;
- one standalone replay of the original first-divergence prefix per candidate root;
- clean-control and baseline outputs;
- the sealed 48-candidate mutation manifest, all attempted candidate records, the fixed-order 24-mutant inclusion result, paired clean-reference evidence, progress-instrumentation controls, and semantic/crash-only kill matrices;
- one isolated repair diff and before/after matrix per confirmed root;
- upstream disposition and any issue or pull-request links or maintainer responses;
- a README with a clean-environment reproduction command and expected hashes; and
- negative results, exclusions, crashes, and unavailable/unalignable cases.

## Stop/go gates

### Gate 0 — environment and registration integrity

Proceed to confirmatory execution only if the locked stack reports the target versions; the registry rule yields exactly 113 unique registry-marked default-loadable game types; the seven semantic discovery names, `crossword` descriptive exclusion, and 105-name prospective semantic cohort are pairwise disjoint and exhaustive; the main manifest binds the exact separately generated 48-candidate mutation manifest; both manifests are sealed; and a clean rerun reproduces or explicitly refutes every pre-freeze discovery observation. A changed released version starts a new study; it is not silently substituted.

### Gate 1 — oracle validity

Unblind adapter census verdicts only if the three frozen clean controls have
zero unexplained failures, all detector-positive development fixtures are
detected, player/action mappings are complete, and chance cases are either
synchronized or explicitly labeled pathwise/unalignable. This gate does not
upgrade the three controls into registry-wide false-positive evidence.

If a law fails a reference, stop use of that law. Correct it and register an amendment, rerun all controls, and treat any previously inspected adapter result under that law as exploratory.

### Gate 2 — complete and report the focused study

Write and release the focused one-adapter study if all of the following methodological conditions hold:

- all 113 catalog entries are accounted for in the frozen 7/1/105 partition;
- every prospective trace has one of the five frozen statuses---pass, fail, inapplicable, infrastructure, or unalignable---and every failure retains its exact first-divergence prefix and replay context; a bounded-prefix pass retains its stop reason rather than creating a sixth `budget` status;
- no clean reference has an unexplained failure for an obligation used in primary results;
- the fixed mutation replacement rule has either selected exactly four eligible candidates in every family or reported the resulting short denominator without substitution based on detector outcome;
- primary labels are generated by the frozen classifier before upstream outreach; and
- the complete artifact and exact bounded trace budget are reproducible.

This gate is deliberately independent of the number of newly detected defects. Zero new defects in the 105-name prospective cohort is a valid primary outcome and must be reported without changing the cohort, extending the budget after inspection, or promoting discovery evidence to prospective evidence.

### Gate 3 — claim-specific evidence

The manuscript remains a focused one-adapter case study regardless of whether stronger claim thresholds pass. Individual claims require their own evidence:

- a real-defect claim requires an unambiguous contract, an exact first-divergence witness prefix, a successful standalone replay artifact, causal diagnosis, and a successful isolated repair;
- a general sensitivity claim based on mutation score requires an RQ6-reportable exact 48-candidate attempt ledger with both named progress controls detected, all 24 prospectively sealed contract-derived mutants reachable and behaviorally non-equivalent after frozen replacement, at least 20/24 semantic detections, and at least 3/4 semantic detections in every family; crash-only detections do not satisfy this threshold;
- a low-false-alarm claim is bounded to the exact clean controls and obligations with zero unexplained failures; and
- an upstream-confirmed claim requires archived maintainer acknowledgement or patch disposition.

Failure of any stronger threshold narrows or removes that claim; it does not cancel publication or suppress a zero-new-defect result. Adding a second adapter, another repository, historical versions, or post hoc traces is outside the primary protocol and may appear only as a separately registered future extension.
