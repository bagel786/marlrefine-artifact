from __future__ import annotations

import hashlib
from collections import Counter

from marlrefine.mutations import (
    CANDIDATE_POOL,
    MUTANTS_PER_FAMILY,
    MUTATION_ENGINE_ID,
    MUTATION_FAMILIES,
    POOL_PER_FAMILY,
    PROGRESS_INSTRUMENTATION_CONTROLS,
    adapter_class_for,
    candidate_manifest_records,
    paired_reference_class_for,
    progress_transform_for,
)
from marlrefine.repairs import CombinedRepairV0


def test_sealed_mutation_pool_has_exact_48_to_24_family_design() -> None:
    counts = Counter(candidate.family for candidate in CANDIDATE_POOL)

    assert tuple(counts) == MUTATION_FAMILIES
    assert counts == Counter({family: POOL_PER_FAMILY for family in MUTATION_FAMILIES})
    assert len(CANDIDATE_POOL) == 48
    assert len(MUTATION_FAMILIES) * MUTANTS_PER_FAMILY == 24
    for family in MUTATION_FAMILIES:
        assert [
            candidate.priority
            for candidate in CANDIDATE_POOL
            if candidate.family == family
        ] == list(range(1, POOL_PER_FAMILY + 1))


def test_candidate_identity_and_synthetic_patch_hashes_are_stable_and_unique() -> None:
    records = candidate_manifest_records()

    assert len({record["candidate_id"] for record in records}) == 48
    assert len({record["patch_sha256"] for record in records}) == 48
    for candidate, record in zip(CANDIDATE_POOL, records, strict=True):
        patch = candidate.canonical_patch()
        assert record["canonical_patch"] == patch
        assert (
            record["patch_sha256"] == hashlib.sha256(patch.encode("utf-8")).hexdigest()
        )
        assert f"sealed-mutant/{candidate.candidate_id}" in patch
        assert record["operator_id"].endswith(".v1")
        assert record["mutation_engine_id"] == MUTATION_ENGINE_ID
        assert len(record["mutation_engine_source_sha256"]) == 64


def test_pool_contains_explicit_nondefault_configuration_triggers() -> None:
    nondefault_specs = {
        candidate.game_spec
        for candidate in CANDIDATE_POOL
        if "(" in candidate.game_spec
    }

    assert {
        "connect_four(rows=5,columns=6,x_in_row=4)",
        "hex(board_size=5)",
        "leduc_poker(players=3)",
        "mnk(m=3,n=3,k=3)",
    }.issubset(nondefault_specs)
    assert all(
        "(" in candidate.game_spec
        for candidate in CANDIDATE_POOL
        if candidate.family == "configuration_provenance"
    )


def test_paired_classes_are_candidate_bound_composite_repair_subclasses() -> None:
    candidate = CANDIDATE_POOL[0]
    reference = paired_reference_class_for(candidate)
    mutant = adapter_class_for(candidate)

    assert issubclass(reference, CombinedRepairV0)
    assert issubclass(mutant, CombinedRepairV0)
    assert reference.mutation_candidate is candidate
    assert mutant.mutation_candidate is candidate
    assert reference.mutation_role == "paired_clean_reference"
    assert mutant.mutation_role == "paired_mutant"


def test_progress_controls_are_exact_distinct_and_outside_denominator() -> None:
    records = [
        control.to_manifest_record() for control in PROGRESS_INSTRUMENTATION_CONTROLS
    ]

    assert [record["control_id"] for record in records] == [
        "progress-control-offset-plus-one",
        "progress-control-stall-on-advance",
    ]
    assert len({record["patch_sha256"] for record in records}) == 2
    assert all(
        record["included_in_24_mutant_denominator"] is False for record in records
    )
    offset, stall = PROGRESS_INSTRUMENTATION_CONTROLS
    assert progress_transform_for(offset)(3, 4, 5) == 6
    assert progress_transform_for(stall)(3, 4, 5) == 4
    assert progress_transform_for(stall)(3, 4, 4) == 4
