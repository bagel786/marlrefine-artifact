from __future__ import annotations

import json

import pytest

from marlrefine.provenance import runtime_provenance
from marlrefine.serialization import to_jsonable, write_json


def test_sets_have_a_canonical_order() -> None:
    assert to_jsonable({3, 1, 2}) == [1, 2, 3]


def test_unknown_types_are_rejected_instead_of_stringified() -> None:
    with pytest.raises(TypeError, match="unsupported artifact value type"):
        to_jsonable(object())


def test_mapping_keys_cannot_collapse_during_stringification() -> None:
    with pytest.raises(TypeError, match="require string keys"):
        to_jsonable({1: "integer", "1": "string"})


def test_nonfinite_json_is_rejected_without_partial_target(tmp_path) -> None:
    target = tmp_path / "evidence.json"
    with pytest.raises(ValueError, match="non-finite"):
        write_json(target, {"value": float("nan")})
    assert not target.exists()


def test_atomic_json_is_utf8_and_standard_compliant(tmp_path) -> None:
    target = tmp_path / "evidence.json"
    write_json(target, {"message": "ありがとう", "value": 1.5})
    assert json.loads(target.read_text(encoding="utf-8"))["message"] == "ありがとう"


def test_provenance_identifies_source_tree_even_without_git() -> None:
    provenance = runtime_provenance()
    assert len(provenance["source_tree_sha256"]) == 64
    assert "git_dirty" in provenance
    assert provenance["python"]["executable_name"]
    assert "executable" not in provenance["python"]
    assert set(provenance["installed_distribution_sha256"]) == {
        "shimmy",
        "open-spiel",
        "pettingzoo",
        "gymnasium",
        "numpy",
    }
    assert all(
        len(digest) == 64
        for digest in provenance["installed_distribution_sha256"].values()
    )
