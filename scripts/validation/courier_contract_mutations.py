"""Courier canonical snapshot / critical mutation rejection checks."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from validation import golden_quests
from validation.core import ROOT, ValidationError, load_yaml


FIXTURE = ROOT / "scripts/validation/fixtures/golden_quests/courier_explicit_git_postcondition.yaml"


Mutation = tuple[str, Callable[[dict[str, Any]], None], str]


def _assert_rejected(name: str, mutator: Callable[[dict[str, Any]], None], expected_error: str) -> None:
    document = copy.deepcopy(load_yaml(str(FIXTURE)))
    mutator(document)
    original = golden_quests._fixture

    def fixture(name: str) -> dict[str, object]:
        if name == "courier_explicit_git_postcondition.yaml":
            return document
        return original(name)

    golden_quests._fixture = fixture
    try:
        try:
            golden_quests.validate_golden_quests()
        except ValidationError as exc:
            if expected_error in str(exc):
                return
            raise AssertionError(f"{name} は想定外の検証エラーを送出しました: {exc}") from exc
        raise AssertionError(f"{name} mutated courier contract was accepted")
    finally:
        golden_quests._fixture = original


def _assert_accepted(name: str, mutator: Callable[[dict[str, Any]], None]) -> None:
    document = copy.deepcopy(load_yaml(str(FIXTURE)))
    mutator(document)
    original = golden_quests._fixture

    def fixture(name: str) -> dict[str, object]:
        if name == "courier_explicit_git_postcondition.yaml":
            return document
        return original(name)

    golden_quests._fixture = fixture
    try:
        golden_quests.validate_golden_quests()
    except ValidationError as exc:
        raise AssertionError(f"{name} は受理されるべき変更ですが拒否されました: {exc}") from exc
    finally:
        golden_quests._fixture = original


def validate_courier_contract_mutations() -> None:
    golden_quests.validate_golden_quests()
    _assert_accepted(
        "same_digest_postwrite_snapshot",
        lambda doc: doc["expected"].__setitem__("postwrite_snapshot", copy.deepcopy(doc["input"]["subject_snapshot"])),
    )
    cases: list[Mutation] = [
        ("authorization_snapshot_mismatch", lambda doc: doc["expected"]["authorization"]["subject_snapshot"].__setitem__("revision_id", "commit:wrong"), "canonical snapshot"),
        ("preflight_snapshot_mismatch", lambda doc: doc["expected"]["preflight_snapshot"].__setitem__("revision_id", "commit:wrong"), "preflight canonical snapshot"),
        ("missing_canonical_field", lambda doc: doc["input"]["subject_snapshot"].pop("snapshot_id"), "canonical snapshot fields"),
        ("extra_canonical_field", lambda doc: doc["input"]["subject_snapshot"].__setitem__("unexpected", True), "canonical snapshot fields"),
        ("postwrite_scope_mismatch", lambda doc: doc["expected"]["postwrite_snapshot"].__setitem__("scope_paths", ["src/other.py"]), "post-write snapshot"),
        ("postwrite_lineage_mismatch", lambda doc: doc["expected"]["postwrite_snapshot"].__setitem__("base_ref", "commit:wrong"), "post-write snapshot"),
        ("postwrite_kind_mismatch", lambda doc: doc["expected"]["postwrite_snapshot"].__setitem__("kind", "commit_range"), "postwrite_snapshot.kind"),
        ("target_repo_root_confirmed_false", lambda doc: doc["expected"]["preconditions"].__setitem__("target_repo_root_confirmed", False), "helper snapshot完全一致"),
        ("committed_paths_match_scope_false", lambda doc: doc["expected"]["postconditions"].__setitem__("committed_paths_match_scope", False), "courier postcondition"),
        ("postwrite_untracked_state_false", lambda doc: doc["expected"]["postconditions"].__setitem__("postwrite_untracked_from_postwrite_state", False), "courier postcondition"),
        ("external_state_unchanged_false", lambda doc: doc["expected"]["postconditions"].__setitem__("external_state_unchanged", False), "courier postcondition"),
    ]
    for name, mutator, expected_error in cases:
        _assert_rejected(name, mutator, expected_error)


if __name__ == "__main__":
    validate_courier_contract_mutations()
    print("courier-contract-mutations: baseline passed; same-digest postwrite accepted; all expected mutations rejected")
