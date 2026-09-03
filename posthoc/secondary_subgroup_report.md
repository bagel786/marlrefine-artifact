# Recovered prespecified secondary-subgroup report

## Status and scope

This is a **post-hoc recovery of a prespecified descriptive report that was omitted from the initial manuscript analysis**. It does not change the frozen cohort, traces, outcomes, endpoint definitions, or causal adjudication. Counts come from the hashed registry census and raw prospective ledger. Causal-root labels are joined from the separately hashed final manual adjudication; they are not inferred from metadata.

No hypothesis tests were run. These are exact descriptive counts, and overlapping metadata categories must not be treated as independent comparisons.

## Immutable input identities

| Input | Release component | Extracted path | SHA-256 |
| --- | --- | --- | --- |
| protocol | tagged GitHub source archive | `frozen-source/docs/protocol.md` | a828d27f36059d8a5f22f6bdadc244efdc0f7b3ee89936f54a117f4c3195f3c0 |
| study_manifest | marlrefine-study-results-v1.0.1.tar.gz | `manifests/study_v1_draft.json` | 02e01818bfa8f6ec2bbebcc8a679dd0968d6c058097e9f607cdd378871e76075 |
| registry_census | marlrefine-study-results-v1.0.1.tar.gz | `artifacts/registry_census.json` | 5fba0a4cf82e7a06a34a686ce263fb1accc53a35651dddc729b7ea8c0bf122ed |
| prospective_raw_ledger | marlrefine-study-results-v1.0.1.tar.gz | `output/prospective_raw.jsonl` | b47aeb50ab15fc21418dfeb781e8224c77addaddddcb4b15cd40a2d148fc0ce0 |
| manual_adjudication | marlrefine-study-results-v1.0.1.tar.gz | `output/manual_adjudication.json` | 474ac6dbf940d147e2ad64c71598793ea90cf51dc92ac378b30a1eba2860a9ae |
| frozen_analysis | marlrefine-study-results-v1.0.1.tar.gz | `output/frozen_analysis.json` | 663a05a06ee4a1336992eb1017852f6be33ed71aabc325ed19ddab52ba5aefe9 |

## Primary registry strata

The protocol's five mutually exclusive primary strata are rederived below. Finding occurrences are correlated symptom records; finding traces and games count at least one semantic finding. These rows recover reporting that existed in the frozen analysis but was omitted from the initial manuscript table.

| Stratum | Games | Traces | Pass | Fail | Inapplicable | Finding occurrences | Finding traces | Finding games |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mean_field` | 3 | 24 | 0 | 24 | 0 | 136 | 24 | 3 |
| `sequential__deterministic` | 42 | 336 | 70 | 266 | 0 | 809 | 266 | 40 |
| `sequential__stochastic` | 42 | 336 | 6 | 298 | 32 | 1202 | 298 | 38 |
| `simultaneous__deterministic` | 12 | 96 | 1 | 95 | 0 | 377 | 95 | 12 |
| `simultaneous__stochastic` | 6 | 48 | 13 | 35 | 0 | 213 | 35 | 6 |

### Final native source-boundary reachability

These counts describe the final recorded native boundary for the 808 semantically evaluated traces. Trace categories are mutually exclusive; distinct-game counts can overlap because different policies for one game can stop at different boundary kinds.

| Final source boundary kind | Traces | Distinct games |
| --- | --- | --- |
| `decision` | 114 | 19 |
| `mean_field` | 24 | 3 |
| `simultaneous` | 23 | 4 |
| `terminal` | 647 | 85 |

## Prespecified single-player subgroup

The prospective cohort contains **12 effective single-player game types** and **96 scheduled traces**. An observed semantic finding occurred in **67/96 traces** across **10/12 games**. The names are: `2048`, `blackjack`, `catch`, `cliff_walking`, `deep_sea`, `mfg_crowd_modelling_2d`, `mfg_dynamic_routing`, `mfg_garnet`, `morpion_solitaire`, `pathfinding`, `solitaire`, `stones_and_gems`.

These traces can inform generic reward, lifecycle, clock, state-kind, and interface-projection behavior. Under the frozen protocol they are **not evidence for specifically inter-agent buffering or multi-agent scheduling claims**. The default-only prospective panel also supplies no configuration-preservation evidence.

For claim narrowing, the arithmetic two-or-more-player complement contains **93 games** and **744 traces**. It contains observed semantic findings in **651/744 traces** across **89/93 games**. This complement was not used to redefine the frozen primary endpoint.

### Trace outcomes by game

| Game | Finding | No finding | No verdict | Terminal | Bounded | Abort | Inapplicable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `2048` | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| `blackjack` | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| `catch` | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| `cliff_walking` | 1 | 7 | 0 | 8 | 0 | 0 | 0 |
| `deep_sea` | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| `mfg_crowd_modelling_2d` | 8 | 0 | 0 | 0 | 0 | 8 | 0 |
| `mfg_dynamic_routing` | 8 | 0 | 0 | 0 | 0 | 8 | 0 |
| `mfg_garnet` | 8 | 0 | 0 | 0 | 0 | 8 | 0 |
| `morpion_solitaire` | 0 | 8 | 0 | 8 | 0 | 0 | 0 |
| `pathfinding` | 2 | 6 | 0 | 8 | 0 | 0 | 0 |
| `solitaire` | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| `stones_and_gems` | 0 | 0 | 8 | 0 | 0 | 0 | 8 |

### Single-player symptom and root breakdown

| Violation code | Occurrences | Traces | Games |
| --- | --- | --- | --- |
| `agents_exhausted_before_source_terminal` | 24 | 24 | 3 |
| `boundary_lifecycle_mismatch` | 51 | 51 | 8 |
| `mean_field_node_silently_terminated` | 24 | 24 | 3 |
| `pre_cleanup_lifecycle_mismatch` | 51 | 51 | 8 |
| `source_decision_clock_mismatch` | 40 | 40 | 5 |
| `terminality_mismatch` | 24 | 24 | 3 |

Protocol-obligation symptoms:

| Obligation | Occurrences | Traces | Games |
| --- | --- | --- | --- |
| `boundary_lifecycle_preservation` | 51 | 51 | 8 |
| `decision_clock_preservation` | 40 | 40 | 5 |
| `lifecycle_preservation` | 99 | 51 | 8 |
| `state_kind_soundness` | 24 | 24 | 3 |

Adjudicated causal roots (occurrences are symptom records attributed to a root, not additional distinct defects):

| Causal root | Attributed symptoms | Traces | Games |
| --- | --- | --- | --- |
| `mean-field-capability-boundary` | 136 | 24 | 3 |
| `reward-accounting-replay` | 0 | 0 | 0 |
| `source-decision-clock` | 78 | 43 | 7 |

### O1--O8 evaluation coverage within the subgroup

| ID | Pass | Fail | N/A | Not eval. | Sites | Linked findings | Finding traces | Finding games |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| O1 | 0 | 0 | 96 | 0 | 0 | 0 | 0 | 0 |
| O2 | 88 | 0 | 8 | 0 | 88 | 0 | 0 | 0 |
| O3 | 88 | 0 | 8 | 0 | 6623 | 0 | 0 | 0 |
| O4 | 48 | 40 | 8 | 0 | 6647 | 40 | 40 | 5 |
| O5 | 37 | 51 | 8 | 0 | 6647 | 150 | 51 | 8 |
| O6 | 0 | 0 | 96 | 0 | 0 | 0 | 0 | 0 |
| O7 | 64 | 24 | 8 | 0 | 7783 | 24 | 24 | 3 |
| O8 | 88 | 0 | 8 | 0 | 6559 | 0 | 0 | 0 |

`evaluated_pass` denominators are obligation-specific. They must not be reconstructed from traces lacking a finding.

## All prespecified secondary tags

The table below reports every category that occurs in the 105-game prospective cohort. `Finding traces` and `finding games` count at least one semantic finding; they do not count causal defects.

| Dimension | Category | Games | Traces | Finding occurrences | Finding traces | Finding games | No-finding traces | No verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `effective_num_players` | `1` | 12 | 96 | 214 | 67 | 10 | 21 | 8 |
| `effective_num_players` | `2` | 80 | 640 | 1964 | 558 | 77 | 66 | 16 |
| `effective_num_players` | `3` | 6 | 48 | 195 | 39 | 5 | 1 | 8 |
| `effective_num_players` | `4` | 5 | 40 | 194 | 38 | 5 | 2 | 0 |
| `effective_num_players` | `5` | 1 | 8 | 75 | 8 | 1 | 0 | 0 |
| `effective_num_players` | `6` | 1 | 8 | 95 | 8 | 1 | 0 | 0 |
| `declared_chance_mode` | `deterministic` | 54 | 432 | 1186 | 361 | 52 | 71 | 0 |
| `declared_chance_mode` | `explicit_stochastic` | 47 | 376 | 1551 | 357 | 47 | 19 | 0 |
| `declared_chance_mode` | `sampled_stochastic` | 4 | 32 | 0 | 0 | 0 | 0 | 32 |
| `declared_reward_timing` | `intermediate_rewards_allowed` | 13 | 104 | 345 | 75 | 11 | 21 | 8 |
| `declared_reward_timing` | `terminal_only` | 92 | 736 | 2392 | 643 | 88 | 69 | 24 |
| `declared_information_type` | `imperfect_information` | 45 | 360 | 1247 | 314 | 42 | 22 | 24 |
| `declared_information_type` | `one_shot` | 11 | 88 | 357 | 88 | 11 | 0 | 0 |
| `declared_information_type` | `perfect_information` | 49 | 392 | 1133 | 316 | 46 | 68 | 8 |
| `declared_utility_type` | `general_sum` | 36 | 288 | 1080 | 235 | 32 | 29 | 24 |
| `declared_utility_type` | `identical` | 5 | 40 | 107 | 31 | 4 | 1 | 8 |
| `declared_utility_type` | `zero_sum` | 64 | 512 | 1550 | 452 | 63 | 60 | 0 |
| `provides_observation` | `false` | 7 | 56 | 154 | 38 | 5 | 2 | 16 |
| `provides_observation` | `true` | 98 | 784 | 2583 | 680 | 94 | 88 | 16 |
| `provides_information_state` | `false` | 32 | 256 | 773 | 199 | 28 | 41 | 16 |
| `provides_information_state` | `true` | 73 | 584 | 1964 | 519 | 71 | 49 | 16 |
| `declared_observation_information_state_pair` | `observation=false|information_state=true` | 7 | 56 | 154 | 38 | 5 | 2 | 16 |
| `declared_observation_information_state_pair` | `observation=true|information_state=false` | 32 | 256 | 773 | 199 | 28 | 41 | 16 |
| `declared_observation_information_state_pair` | `observation=true|information_state=true` | 66 | 528 | 1810 | 481 | 66 | 47 | 0 |
| `finite_declared_max_game_length` | `finite_positive` | 105 | 840 | 2737 | 718 | 99 | 90 | 32 |
| `presence_of_configurable_parameters` | `empty_parameter_mapping` | 24 | 192 | 658 | 172 | 23 | 20 | 0 |
| `presence_of_configurable_parameters` | `nonempty_parameter_mapping` | 81 | 648 | 2079 | 546 | 76 | 70 | 32 |

## Suggested manuscript language

The protocol prespecified primary-stratum status and finding incidence, final source-node reachability, and descriptive reporting by player count, chance and reward mode, information and utility type, declared tensor capabilities, finite-length status, and parameterization. The initial manuscript omitted part of this reporting. We recovered it after the primary analysis by a deterministic join of the frozen census, raw ledger, and frozen analysis; no cohort, trace, outcome, or adjudication changed.

Twelve of the 105 prospective game types had an effective num_players() of one, contributing 96 of 840 scheduled traces. A semantic finding was observed in 67 of 96 traces across 10 of 12 games; 21 traces had no observed finding and 8 had no verdict. The subgroup contained findings attributed by the final adjudication to 2 causal roots (mean-field-capability-boundary, source-decision-clock).

The single-player traces inform generic reward, lifecycle, clock, state-kind, and projection behavior. They are not evidence for claims specifically about inter-agent buffering or multi-agent schedules, and the default-only prospective cohort does not test preservation of caller-supplied nondefault configuration.

## Reproduction

From the manuscript source directory, after extracting the tagged GitHub source archive and the v1.0.1 results asset into the paths shown below, run:

```console
python paper/analysis/recover_secondary_subgroups.py \
  --protocol extracted-source/marlrefine-artifact-study-v1.0.1/frozen-source/docs/protocol.md \
  --manifest extracted-results/marlrefine-study-results-v1.0.1/manifests/study_v1_draft.json \
  --census extracted-results/marlrefine-study-results-v1.0.1/artifacts/registry_census.json \
  --ledger extracted-results/marlrefine-study-results-v1.0.1/output/prospective_raw.jsonl \
  --adjudication extracted-results/marlrefine-study-results-v1.0.1/output/manual_adjudication.json \
  --analysis extracted-results/marlrefine-study-results-v1.0.1/output/frozen_analysis.json \
  --json-out recovered-secondary-subgroups.json \
  --markdown-out recovered-secondary-subgroups.md
```

The script records SHA-256 identities for all six supplied inputs. It terminates on internal manifest-to-ledger, ledger-to-adjudication, and frozen-analysis-to-manifest/ledger/adjudication hash-link mismatches, as well as cohort, schedule, player-count, status-total, obligation-ledger, adjudication-key, stratum, or final-boundary inconsistencies. It deliberately does not hardcode a release-version hash: reviewers can compare the recorded identities with the table above and the release checksums.

## Limitation: no invented nonstandard-game taxonomy

The frozen census and manifest do not define an exhaustive classifier for `special-node` or `other nonstandard` game types. This recovery therefore does not invent one after seeing results. It reports the prespecified components that are unambiguous: mean-field as a primary stratum, one-shot as a declared information category, chance mode, effective player count, and reached final source boundary kinds. A separate exhaustive residual taxonomy remains unresolved.
