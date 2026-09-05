#!/usr/bin/env python3
"""Validate stateless Guild task boundaries and evidence.

The guard deliberately has no queue, database, workflow engine, or actor ACL.
It validates a caller supplied typed artifact against an explicit target root,
scope, authority, and helper-issued Git snapshot.  The host sandbox and
approval system remain the authority for who may perform an operation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:  # direct execution and package-style imports are both useful in tests
    from . import snapshot_digest  # type: ignore
except ImportError:  # pragma: no cover - exercised by the CLI
    import snapshot_digest  # type: ignore


SCHEMA_VERSION = "1.0"
SNAPSHOT_DIGEST_VERSION = snapshot_digest.DIGEST_VERSION
SNAPSHOT_FIELDS = {
    "snapshot_id",
    "digest_version",
    "kind",
    "revision_id",
    "base_ref",
    "head_ref",
    "scope_paths",
    "untracked_paths",
    "dirty_state",
    "diff_hash",
}
AUTHORITY_FIELDS = {"read", "edit", "validate", "local_git", "external_actions"}
SNAPSHOT_KINDS = {"revision_only", "working_tree_content", "commit_range"}
ACCEPTED_DECISIONS = {"accept", "accepted", "accept_with_risks", "request_changes", "needs_human", "blocked"}
ACCEPTED_OUTCOMES = {"completed", "success", "passed", "accepted", "done", "failed", "blocked"}
SHA256_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
ARTIFACT_TYPES = {
    "task_contract",
    "assignment",
    "result",
    "review_receipt",
    "checkpoint",
}
TYPE_ALIASES = {
    "TaskContract": "task_contract",
    "Assignment": "assignment",
    "Result": "result",
    "ReviewReceipt": "review_receipt",
    "Checkpoint": "checkpoint",
}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")


class BoundaryError(ValueError):
    """A typed artifact cannot be safely bound to the current target state."""

    def __init__(self, message: str, *, code: str = "invalid_boundary") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TaskContract:
    """Small typed wrapper for a validated task contract mapping."""

    value: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, recompute: bool = True) -> "TaskContract":
        return cls(validate_task_contract(value, recompute=recompute))

    def validate(self, *, recompute: bool = True) -> dict[str, Any]:
        return validate_task_contract(self.value, recompute=recompute)


@dataclass(frozen=True)
class Assignment:
    """Small typed wrapper for a validated assignment mapping."""

    value: dict[str, Any]

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        contract: Mapping[str, Any] | None = None,
        *,
        recompute: bool = True,
    ) -> "Assignment":
        return cls(validate_assignment(value, contract, recompute=recompute))


@dataclass(frozen=True)
class Result:
    """Small typed wrapper for a validated result mapping."""

    value: dict[str, Any]

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        assignment: Mapping[str, Any] | None = None,
        contract: Mapping[str, Any] | None = None,
        *,
        recompute: bool = True,
    ) -> "Result":
        return cls(validate_result(value, assignment, contract, recompute=recompute))


@dataclass(frozen=True)
class ReviewReceipt:
    """Small typed wrapper for a validated review receipt mapping."""

    value: dict[str, Any]

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        result: Mapping[str, Any] | None = None,
        contract: Mapping[str, Any] | None = None,
        *,
        recompute: bool = True,
    ) -> "ReviewReceipt":
        return cls(validate_review_receipt(value, result, contract, recompute=recompute))


@dataclass(frozen=True)
class Checkpoint:
    """Small typed wrapper for a validated checkpoint mapping."""

    value: dict[str, Any]

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        contract: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        *,
        recompute: bool = True,
    ) -> "Checkpoint":
        return cls(validate_checkpoint(value, contract, result, recompute=recompute))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BoundaryError(f"{label} はobjectにしてください。")
    return dict(value)


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value or "\r" in value or "\n" in value:
        raise BoundaryError(f"{label} は改行/NULを含まない空でない文字列にしてください。")
    return value


def _id(value: Any, label: str) -> str:
    candidate = _nonempty_string(value, label)
    if ID_RE.fullmatch(candidate) is None:
        raise BoundaryError(f"{label} の形式が不正です。")
    return candidate


def _artifact_type(value: Any, expected: str) -> str:
    if not isinstance(value, str):
        raise BoundaryError(f"artifact type は {expected} にしてください。")
    actual = TYPE_ALIASES.get(value, value)
    if actual != expected:
        raise BoundaryError(f"artifact type は {expected} にしてください。")
    return expected


def _version(value: Any) -> str:
    if value != SCHEMA_VERSION:
        raise BoundaryError(f"schema_version は {SCHEMA_VERSION} にしてください。")
    return SCHEMA_VERSION


def _first(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _target_raw(value: Mapping[str, Any], label: str) -> str:
    candidates: list[Any] = []
    for key in ("target_repo_root", "target_root"):
        if key in value:
            candidates.append(value[key])
    boundaries = value.get("boundaries")
    if isinstance(boundaries, Mapping) and "target_repo_root" in boundaries:
        candidates.append(boundaries["target_repo_root"])
    if not candidates:
        raise BoundaryError(f"{label}.target_repo_root がありません。")
    raw = _nonempty_string(candidates[0], f"{label}.target_repo_root")
    if raw.startswith("~"):
        raise BoundaryError(f"{label}.target_repo_root は明示的なabsolute pathにしてください。", code="invalid_target_root")
    for candidate in candidates[1:]:
        if not isinstance(candidate, str):
            raise BoundaryError(f"{label}.target_repo_root は文字列にしてください。")
        if candidate.startswith("~") or not Path(candidate).is_absolute():
            raise BoundaryError(f"{label}.target_repo_root は明示的なabsolute pathにしてください。", code="invalid_target_root")
        if Path(candidate).resolve(strict=False) != Path(raw).resolve(strict=False):
            raise BoundaryError(f"{label}.target_repo_root が複数指定され、canonical rootが一致しません。")
    path = Path(raw)
    if not path.is_absolute():
        raise BoundaryError(f"{label}.target_repo_root は絶対 path にしてください。")
    try:
        root = snapshot_digest.canonical_repo_root(path)
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise BoundaryError(f"{label}.target_repo_root はcanonical Git rootにしてください: {exc}", code="invalid_target_root") from exc
    return str(root)


def canonical_target_root(value: str | Path) -> Path:
    """Public target-root validator shared by callers and git_guard."""

    try:
        raw = str(value)
        if raw.startswith("~"):
            raise BoundaryError("target_repo_root は明示的なabsolute pathにしてください。", code="invalid_target_root")
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise BoundaryError("target_repo_root はpathに変換できません。", code="invalid_target_root") from exc
    if not path.is_absolute():
        raise BoundaryError("target_repo_root は絶対 path にしてください。", code="invalid_target_root")
    try:
        return snapshot_digest.canonical_repo_root(path)
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise BoundaryError(f"target_repo_root はcanonical Git rootにしてください: {exc}", code="invalid_target_root") from exc


def _safe_path(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise BoundaryError(f"{label} は文字列にしてください。")
    try:
        return snapshot_digest._safe_relative(value, label=label)  # type: ignore[attr-defined]
    except (snapshot_digest.SnapshotError, TypeError, ValueError) as exc:
        raise BoundaryError(str(exc), code="unsafe_path") from exc


def _path_list(value: Any, label: str, *, required: bool = True) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise BoundaryError(f"{label} はpath文字列のlistにしてください。")
    result = [_safe_path(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise BoundaryError(f"{label} に重複pathがあります。", code="scope_overlap")
    return sorted(result, key=lambda item: item.encode("utf-8"))


def _overlap(paths: Sequence[str], label: str) -> None:
    canonical = sorted(paths, key=lambda item: (len(PurePosixPath(item).parts), item.encode("utf-8")))
    for index, path in enumerate(canonical):
        parts = PurePosixPath(path).parts
        for other in canonical[index + 1 :]:
            other_parts = PurePosixPath(other).parts
            if parts == other_parts[: len(parts)]:
                raise BoundaryError(f"{label} が重複または包含関係です: {path}, {other}", code="scope_overlap")


def _covered(path: str, scopes: Sequence[str]) -> bool:
    path_parts = PurePosixPath(path).parts
    return any(path_parts[: len(PurePosixPath(scope).parts)] == PurePosixPath(scope).parts for scope in scopes)


def _scope_value(value: Mapping[str, Any], key: str, label: str, *, required: bool = True) -> list[str]:
    candidates: list[Any] = []
    if key in value:
        candidates.append(value[key])
    scope = value.get("scope")
    if isinstance(scope, Mapping) and key in scope:
        candidates.append(scope[key])
    boundaries = value.get("boundaries")
    if isinstance(boundaries, Mapping):
        for candidate_key in (key, f"{key[:-6]}_scope" if key.endswith("_paths") else key):
            if candidate_key in boundaries:
                candidate = boundaries[candidate_key]
                if isinstance(candidate, Mapping):
                    nested = candidate.get("edit" if key == "owned_paths" else "read")
                    if nested is not None:
                        candidates.append(nested)
                else:
                    candidates.append(candidate)
    if key == "allowed_paths":
        owned_scope = value.get("owned_scope")
        if isinstance(owned_scope, Mapping) and "read" in owned_scope:
            candidates.append(owned_scope["read"])
    if key == "owned_paths":
        owned_scope = value.get("owned_scope")
        if isinstance(owned_scope, Mapping) and "edit" in owned_scope:
            candidates.append(owned_scope["edit"])
    if not candidates:
        if required:
            raise BoundaryError(f"{label}.{key} がありません。")
        return []
    paths = _path_list(candidates[0], f"{label}.{key}", required=required)
    for candidate in candidates[1:]:
        other = _path_list(candidate, f"{label}.{key}", required=required)
        if other != paths:
            raise BoundaryError(f"{label}.{key} が複数指定され、一致しません。", code="scope_mismatch")
    _overlap(paths, f"{label}.{key}")
    return paths


def _authority(value: Mapping[str, Any], label: str, *, required: bool = True) -> dict[str, bool]:
    raw = value.get("authority")
    if raw is None:
        if required:
            raise BoundaryError(f"{label}.authority がありません。")
        return {}
    authority = _mapping(raw, f"{label}.authority")
    if set(authority) != AUTHORITY_FIELDS or not all(isinstance(authority[key], bool) for key in AUTHORITY_FIELDS):
        raise BoundaryError(f"{label}.authority はcanonical bool fieldsにしてください。")
    return {key: authority[key] for key in sorted(AUTHORITY_FIELDS)}


def _snapshot_shape(value: Any, label: str) -> dict[str, Any]:
    snapshot = _mapping(value, label)
    if set(snapshot) != SNAPSHOT_FIELDS:
        raise BoundaryError(f"{label} はcanonical snapshot fieldsにしてください。", code="invalid_snapshot")
    if snapshot.get("digest_version") != SNAPSHOT_DIGEST_VERSION or not isinstance(snapshot.get("kind"), str) or snapshot.get("kind") not in SNAPSHOT_KINDS:
        raise BoundaryError(f"{label}.digest_version/kindが不正です。", code="invalid_snapshot")
    if not isinstance(snapshot.get("snapshot_id"), str) or SHA256_ID_RE.fullmatch(snapshot["snapshot_id"]) is None:
        raise BoundaryError(f"{label}.snapshot_id はhelper形式sha256で指定してください。", code="invalid_snapshot")
    if not isinstance(snapshot.get("revision_id"), str) or COMMIT_OID_RE.fullmatch(snapshot["revision_id"]) is None:
        raise BoundaryError(f"{label}.revision_id はcommit OIDで指定してください。", code="invalid_snapshot")
    if not isinstance(snapshot.get("dirty_state"), str) or snapshot.get("dirty_state") not in {"clean", "dirty"}:
        raise BoundaryError(f"{label}.dirty_state が不正です。", code="invalid_snapshot")
    for key in ("scope_paths", "untracked_paths"):
        paths = _path_list(snapshot.get(key), f"{label}.{key}")
        if paths != snapshot[key]:
            raise BoundaryError(f"{label}.{key} はcanonical byte順にしてください。", code="invalid_snapshot")
    for key in ("base_ref", "head_ref"):
        raw = snapshot.get(key)
        if raw is not None and (not isinstance(raw, str) or COMMIT_OID_RE.fullmatch(raw) is None):
            raise BoundaryError(f"{label}.{key} はnullまたはcommit OIDにしてください。", code="invalid_snapshot")
    kind = snapshot["kind"]
    if kind == "revision_only":
        if snapshot["base_ref"] is not None or snapshot["head_ref"] is not None or snapshot["scope_paths"] or snapshot["untracked_paths"] or snapshot["dirty_state"] != "clean" or snapshot["diff_hash"] is not None:
            raise BoundaryError(f"{label}: revision_onlyのcanonical fieldsが不正です。", code="invalid_snapshot")
    elif kind == "working_tree_content":
        if snapshot["base_ref"] is None or snapshot["head_ref"] is not None or not snapshot["scope_paths"] or not isinstance(snapshot["diff_hash"], str) or snapshot["diff_hash"] != snapshot["snapshot_id"]:
            raise BoundaryError(f"{label}: working_tree_contentのcanonical fieldsが不正です。", code="invalid_snapshot")
    elif kind == "commit_range":
        if snapshot["base_ref"] is None or snapshot["head_ref"] is None or not snapshot["scope_paths"] or snapshot["untracked_paths"] or not isinstance(snapshot["diff_hash"], str) or snapshot["diff_hash"] != snapshot["snapshot_id"]:
            raise BoundaryError(f"{label}: commit_rangeのcanonical fieldsが不正です。", code="invalid_snapshot")
    if snapshot.get("diff_hash") is not None and (not isinstance(snapshot["diff_hash"], str) or SHA256_ID_RE.fullmatch(snapshot["diff_hash"]) is None):
        raise BoundaryError(f"{label}.diff_hash が不正です。", code="invalid_snapshot")
    return snapshot


def recompute_snapshot(target_repo_root: str | Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Reissue a snapshot through the hardened helper and return its JSON map."""

    root = canonical_target_root(target_repo_root)
    shaped = _snapshot_shape(snapshot, "snapshot")
    try:
        actual = snapshot_digest.compute_snapshot(
            root,
            kind=str(shaped["kind"]),
            base_ref=shaped.get("base_ref"),
            head_ref=shaped.get("head_ref"),
            scope_paths=list(shaped["scope_paths"]),
            untracked_paths=list(shaped["untracked_paths"]),
        )
    except (snapshot_digest.SnapshotError, OSError, UnicodeError, ValueError) as exc:
        raise BoundaryError(f"snapshotをtarget Git rootで再計算できません: {exc}", code="stale_evidence") from exc
    if actual != shaped:
        raise BoundaryError("helper-issued snapshotとtargetのactual Git stateが一致しません。", code="stale_evidence")
    return actual


def _subject_snapshot(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    raw = _first(value, "subject_snapshot", "snapshot")
    if raw is None:
        raise BoundaryError(f"{label}.subject_snapshot がありません。")
    return _snapshot_shape(raw, f"{label}.subject_snapshot")


def _nonempty_sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
        raise BoundaryError(f"{label} は空でないlistにしてください。")
    return list(value)


def _outcome(value: Any, label: str) -> Any:
    if isinstance(value, str):
        return _nonempty_string(value, label)
    if isinstance(value, Mapping):
        result = _mapping(value, label)
        if not result or not any(item not in (None, "", [], {}) for item in result.values()):
            raise BoundaryError(f"{label} は空にできません。")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)) and value:
        return list(value)
    raise BoundaryError(f"{label} は空でない文字列/object/listにしてください。")


def _expected_checks(value: Mapping[str, Any], label: str) -> list[dict[str, Any]]:
    raw = _first(value, "acceptance_checks", "validation_expectations", "checks")
    checks = _nonempty_sequence(raw, f"{label}.acceptance_checks")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(checks):
        if isinstance(item, str):
            check_id = _id(item, f"{label}.acceptance_checks[{index}]")
            check = {"id": check_id, "required": True}
        else:
            check = _mapping(item, f"{label}.acceptance_checks[{index}]")
            check_id = _id(check.get("id"), f"{label}.acceptance_checks[{index}].id")
            required = check.get("required", True)
            if not isinstance(required, bool):
                raise BoundaryError(f"{label}.acceptance_checks[{index}].required はboolにしてください。")
            check = {"id": check_id, "required": required, **check}
        if check_id in seen:
            raise BoundaryError(f"{label}.acceptance_checks に重複IDがあります。")
        seen.add(check_id)
        result.append(check)
    return result


def _evidence_refs(value: Mapping[str, Any], label: str, *, required: bool = True) -> tuple[list[str], set[str]]:
    raw = value.get("evidence_refs")
    if raw is None and isinstance(value.get("validation_evidence"), Mapping):
        raw = value["validation_evidence"].get("evidence_refs")
    if raw is None:
        if required:
            raise BoundaryError(f"{label}.evidence_refs がありません。", code="missing_evidence")
        return [], set()
    refs = _nonempty_sequence(raw, f"{label}.evidence_refs")
    result = [_id(item, f"{label}.evidence_refs[{index}]") for index, item in enumerate(refs)]
    if len(result) != len(set(result)):
        raise BoundaryError(f"{label}.evidence_refs に重複IDがあります。", code="missing_evidence")
    items = value.get("evidence")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)) or not items:
        raise BoundaryError(f"{label}.evidence は空でないevidence object listにしてください。", code="missing_evidence")
    known: set[str] = set()
    for index, item in enumerate(items):
        evidence = _mapping(item, f"{label}.evidence[{index}]")
        evidence_id = _id(evidence.get("id"), f"{label}.evidence[{index}].id")
        if not any(key != "id" and item_value not in (None, "", [], {}) for key, item_value in evidence.items()):
            raise BoundaryError(f"{label}.evidence[{index}] に根拠内容がありません。", code="missing_evidence")
        if evidence_id in known:
            raise BoundaryError(f"{label}.evidence に重複IDがあります。", code="missing_evidence")
        known.add(evidence_id)
    if not set(result).issubset(known):
        raise BoundaryError(f"{label}.evidence_refs に未定義IDがあります。", code="missing_evidence")
    return result, known


def _required_acceptance(
    value: Mapping[str, Any],
    expected: Sequence[Mapping[str, Any]],
    label: str,
    evidence_ids: set[str],
) -> None:
    raw = _first(value, "acceptance_checks", "checks")
    checks = _nonempty_sequence(raw, f"{label}.acceptance_checks")
    actual: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(checks):
        if isinstance(item, Mapping):
            check = _mapping(item, f"{label}.acceptance_checks[{index}]")
            check_id = _id(check.get("id"), f"{label}.acceptance_checks[{index}].id")
        else:
            raise BoundaryError(f"{label}.acceptance_checks[{index}] はresult check objectにしてください。")
        if check_id in actual:
            raise BoundaryError(f"{label}.acceptance_checks に重複IDがあります。")
        refs = check.get("evidence_refs")
        if refs is not None:
            if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
                raise BoundaryError(f"{label}.acceptance_checks[{check_id}].evidence_refs はlistにしてください。", code="missing_evidence")
            check_refs = [_id(item, f"{label}.acceptance_checks[{check_id}].evidence_refs") for item in refs]
            if len(check_refs) != len(set(check_refs)) or not set(check_refs).issubset(evidence_ids):
                raise BoundaryError(
                    f"acceptance check のevidence_refsに未定義または重複IDがあります: {check_id}",
                    code="missing_evidence",
                )
        actual[check_id] = check
    for expected_check in expected:
        check_id = str(expected_check["id"])
        if check_id not in actual:
            if expected_check.get("required", True):
                raise BoundaryError(f"required acceptance check がありません: {check_id}", code="missing_acceptance")
            continue
        check = actual[check_id]
        if check.get("passed") is not True and expected_check.get("required", True):
            raise BoundaryError(f"required acceptance check がpassしていません: {check_id}", code="acceptance_failed")
        if expected_check.get("required", True):
            refs = check.get("evidence_refs")
            if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)) or not refs:
                raise BoundaryError(f"required acceptance check にevidence_refsがありません: {check_id}", code="missing_evidence")


def _binding_target(value: Mapping[str, Any], label: str, expected_root: str) -> None:
    actual = _target_raw(value, label)
    if actual != expected_root:
        raise BoundaryError(f"{label}.target_repo_root がcontract targetと一致しません。", code="contract_binding")


def _binding_snapshot(value: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    actual = _subject_snapshot(value, label)
    if actual != dict(expected):
        raise BoundaryError(f"{label}.subject_snapshot が親contract snapshotと一致しません。", code="contract_binding")


def _contract_mapping(value: Mapping[str, Any] | None, owner: Mapping[str, Any], label: str) -> dict[str, Any]:
    if value is not None:
        return _mapping(value, f"{label}.contract")
    nested = owner.get("contract") or owner.get("task_contract")
    if isinstance(nested, Mapping):
        return dict(nested)
    raise BoundaryError(f"{label}.contract のcomplete mappingが必要です。", code="contract_binding")


def validate_task_contract(value: Mapping[str, Any], *, recompute: bool = True) -> dict[str, Any]:
    contract = _mapping(value, "task_contract")
    _artifact_type(contract.get("type"), "task_contract")
    _version(contract.get("schema_version"))
    _id(contract.get("id"), "task_contract.id")
    root = _target_raw(contract, "task_contract")
    allowed = _scope_value(contract, "allowed_paths", "task_contract")
    owned = _scope_value(contract, "owned_paths", "task_contract")
    authority = _authority(contract, "task_contract")
    if authority.get("edit") and not owned:
        raise BoundaryError("edit authorityにはowned_pathsが必要です。", code="scope_mismatch")
    if any(not _covered(path, allowed) for path in owned):
        raise BoundaryError("owned_paths はallowed_pathsのsubsetにしてください。", code="scope_mismatch")
    denied = _path_list(_first(contract, "forbidden_paths", "edit_deny"), "task_contract.forbidden_paths", required=False)
    if any(_covered(path, denied) or _covered(deny, owned) for path in owned for deny in denied):
        raise BoundaryError("owned_paths と forbidden_paths が重なっています。", code="scope_mismatch")
    snapshot = _subject_snapshot(contract, "task_contract")
    # A revision-only subject is a clean repository-wide lineage anchor and can
    # still carry an explicit read/edit scope in the contract.  Content
    # snapshots, by contrast, must enumerate exactly the contract's allowed
    # scope so the evidence cannot silently cover a different set of paths.
    if snapshot["kind"] != "revision_only" and set(snapshot["scope_paths"]) != set(allowed):
        raise BoundaryError("contract scopeとsubject_snapshot.scope_pathsが一致しません。", code="scope_mismatch")
    if recompute:
        recompute_snapshot(root, snapshot)
    _outcome(_first(contract, "expected_outcome", "outcome"), "task_contract.expected_outcome")
    _expected_checks(contract, "task_contract")
    return contract


def validate_assignment(
    value: Mapping[str, Any],
    contract: Mapping[str, Any] | None = None,
    *,
    peer_assignments: Sequence[Mapping[str, Any]] | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    assignment = _mapping(value, "assignment")
    _artifact_type(assignment.get("type"), "assignment")
    _version(assignment.get("schema_version"))
    assignment_id = _id(assignment.get("id", assignment.get("assignment_id")), "assignment.id")
    contract_map = _contract_mapping(contract, assignment, "assignment")
    checked_contract = validate_task_contract(contract_map, recompute=recompute)
    contract_id = _id(assignment.get("contract_id", assignment.get("task_contract_id")), "assignment.contract_id")
    if contract_id != checked_contract["id"]:
        raise BoundaryError("assignment.contract_id がcontract.idと一致しません。", code="contract_binding")
    root = _target_raw(checked_contract, "task_contract")
    _binding_target(assignment, "assignment", root)
    allowed = _scope_value(assignment, "allowed_paths", "assignment")
    owned = _scope_value(assignment, "owned_paths", "assignment")
    contract_allowed = _scope_value(checked_contract, "allowed_paths", "task_contract")
    contract_owned = _scope_value(checked_contract, "owned_paths", "task_contract")
    if any(not _covered(path, contract_allowed) for path in allowed + owned):
        raise BoundaryError("assignment scopeがcontract.allowed_pathsの外です。", code="scope_mismatch")
    if any(not _covered(path, allowed) for path in owned):
        raise BoundaryError("assignment.owned_paths はassignment.allowed_pathsのsubsetにしてください。", code="scope_mismatch")
    if any(not _covered(path, contract_owned) for path in owned):
        raise BoundaryError("assignment.owned_pathsがcontract.owned_pathsの外です。", code="scope_mismatch")
    authority = _authority(assignment, "assignment")
    contract_authority = _authority(checked_contract, "task_contract")
    if any(authority[key] and not contract_authority[key] for key in AUTHORITY_FIELDS):
        raise BoundaryError("assignment authorityがcontract authorityを拡張しています。", code="authority_expansion")
    _binding_snapshot(assignment, _subject_snapshot(checked_contract, "task_contract"), "assignment")
    _nonempty_string(assignment.get("worker_id"), "assignment.worker_id")
    _nonempty_string(assignment.get("role"), "assignment.role")
    if "expected_outcome" in assignment:
        _outcome(assignment["expected_outcome"], "assignment.expected_outcome")
    if "acceptance_checks" in assignment:
        _expected_checks(assignment, "assignment")
    if peer_assignments is None:
        candidate_peers = assignment.get("peer_assignments")
        if isinstance(candidate_peers, Sequence) and not isinstance(candidate_peers, (str, bytes, bytearray)):
            peer_assignments = [item for item in candidate_peers if isinstance(item, Mapping)]
    if peer_assignments:
        for index, peer in enumerate(peer_assignments):
            peer_owned = _scope_value(_mapping(peer, f"assignment.peer_assignments[{index}]"), "owned_paths", f"assignment.peer_assignments[{index}]")
            if any(
                _covered(path, [peer_path]) or _covered(peer_path, [path])
                for path in owned
                for peer_path in peer_owned
            ):
                raise BoundaryError("assignment owned scopeがpeer assignmentと重なっています。", code="scope_overlap")
    return assignment


def validate_assignment_set(
    assignments: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    *,
    recompute: bool = True,
) -> list[dict[str, Any]]:
    values = [validate_assignment(item, contract, recompute=recompute) for item in assignments]
    seen: list[tuple[str, str]] = []
    for item in values:
        for path in _scope_value(item, "owned_paths", "assignment"):
            for previous_id, previous_path in seen:
                if _covered(path, [previous_path]) or _covered(previous_path, [path]):
                    raise BoundaryError(f"assignment scopeが重なっています: {previous_id}, {item.get('id')}", code="scope_overlap")
            seen.append((str(item.get("id")), path))
    return values


def validate_result(
    value: Mapping[str, Any],
    assignment: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    *,
    recompute: bool = True,
) -> dict[str, Any]:
    result = _mapping(value, "result")
    _artifact_type(result.get("type"), "result")
    _version(result.get("schema_version"))
    _id(result.get("id", result.get("result_id")), "result.id")
    assignment_map = assignment
    if assignment_map is None and isinstance(result.get("assignment"), Mapping):
        assignment_map = result["assignment"]
    if assignment_map is None or not isinstance(assignment_map, Mapping):
        raise BoundaryError("result.assignment のcomplete mappingが必要です。", code="contract_binding")
    # The base snapshot is expected to become stale as an assignment edits the
    # tree.  Revalidate its shape and lineage here, while recomputing only the
    # result snapshot below.
    assignment_checked = validate_assignment(assignment_map, contract, recompute=False)
    contract_map = _contract_mapping(contract, result, "result")
    contract_checked = validate_task_contract(contract_map, recompute=False)
    assignment_id = _id(result.get("assignment_id"), "result.assignment_id")
    if assignment_id != assignment_checked.get("id", assignment_checked.get("assignment_id")):
        raise BoundaryError("result.assignment_id がassignment.idと一致しません。", code="contract_binding")
    contract_id = _id(result.get("contract_id"), "result.contract_id")
    if contract_id != contract_checked["id"]:
        raise BoundaryError("result.contract_id がcontract.idと一致しません。", code="contract_binding")
    root = _target_raw(assignment_checked, "assignment")
    _binding_target(result, "result", root)
    assignment_snapshot = _subject_snapshot(assignment_checked, "assignment")
    base_snapshot = _snapshot_shape(result.get("base_snapshot"), "result.base_snapshot")
    if base_snapshot != assignment_snapshot:
        raise BoundaryError("result.base_snapshot がassignment subject snapshotと一致しません。", code="contract_binding")
    result_snapshot = _snapshot_shape(_first(result, "result_snapshot", "snapshot"), "result.result_snapshot")
    owned = _scope_value(assignment_checked, "owned_paths", "assignment")
    if owned:
        if result_snapshot["kind"] == "revision_only" or set(result_snapshot["scope_paths"]) != set(owned):
            raise BoundaryError("result_snapshot scopeがassignment.owned_pathsと一致しません。", code="scope_mismatch")
    changed_files = _path_list(result.get("changed_files", []), "result.changed_files", required=False)
    if any(not _covered(path, owned) for path in changed_files):
        raise BoundaryError("result.changed_files がowned_pathsの外です。", code="scope_expansion")
    if any(not _covered(path, _scope_value(contract_checked, "allowed_paths", "task_contract")) for path in changed_files):
        raise BoundaryError("result.changed_files がallowed_pathsの外です。", code="scope_expansion")
    if recompute:
        recompute_snapshot(root, result_snapshot)
    _outcome(result.get("outcome"), "result.outcome")
    expected = _expected_checks(contract_checked, "task_contract")
    _result_refs, evidence_ids = _evidence_refs(result, "result", required=True)
    _required_acceptance(result, expected, "result", evidence_ids)
    checkpoint = result.get("checkpoint")
    if checkpoint is not None:
        validate_checkpoint(checkpoint, contract_checked, result, recompute=recompute)
    return result


def _findings(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    findings = _nonempty_sequence(value, label) if value else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(findings):
        finding = _mapping(item, f"{label}[{index}]")
        finding_id = _id(finding.get("id"), f"{label}[{index}].id")
        if finding_id in seen:
            raise BoundaryError(f"{label} に重複IDがあります。")
        seen.add(finding_id)
        _nonempty_string(finding.get("summary"), f"{label}[{index}].summary")
        required = finding.get("required", True)
        if not isinstance(required, bool):
            raise BoundaryError(f"{label}[{index}].required はboolにしてください。")
        result.append(finding)
    return result


def _dispositions(value: Any, finding_ids: set[str], label: str) -> dict[str, list[str]]:
    if value is None:
        if finding_ids:
            raise BoundaryError(f"{label} がありません。", code="unresolved_finding")
        return {"adopted": [], "rejected": [], "unresolved": []}
    raw = _mapping(value, label)
    if set(raw) != {"adopted", "rejected", "unresolved"}:
        raise BoundaryError(f"{label} はadopted/rejected/unresolvedだけにしてください。")
    result: dict[str, list[str]] = {}
    seen: set[str] = set()
    for key in ("adopted", "rejected", "unresolved"):
        refs = _nonempty_sequence(raw[key], f"{label}.{key}") if raw[key] else []
        ids = [_id(item, f"{label}.{key}") for item in refs]
        if len(ids) != len(set(ids)) or seen.intersection(ids):
            raise BoundaryError(f"{label} のfinding IDはexactly once処置してください。", code="unresolved_finding")
        seen.update(ids)
        result[key] = ids
    if seen != finding_ids:
        raise BoundaryError(f"{label} が全finding IDをexactly once処置していません。", code="unresolved_finding")
    return result


def validate_review_receipt(
    value: Mapping[str, Any],
    result: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    *,
    recompute: bool = True,
) -> dict[str, Any]:
    receipt = _mapping(value, "review_receipt")
    _artifact_type(receipt.get("type"), "review_receipt")
    _version(receipt.get("schema_version"))
    _id(receipt.get("id", receipt.get("review_id")), "review_receipt.id")
    result_map = result
    if result_map is None and isinstance(receipt.get("result"), Mapping):
        result_map = receipt["result"]
    if result_map is None:
        raise BoundaryError("review_receipt.result のcomplete mappingが必要です。", code="contract_binding")
    result_assignment = result_map.get("assignment") if isinstance(result_map, Mapping) and isinstance(result_map.get("assignment"), Mapping) else None
    if result_assignment is None and isinstance(receipt.get("assignment"), Mapping):
        result_assignment = receipt["assignment"]
    contract_map = _contract_mapping(contract, receipt, "review_receipt")
    result_checked = validate_result(result_map, result_assignment, contract_map, recompute=recompute)
    contract_checked = validate_task_contract(contract_map, recompute=False)
    result_id = _id(receipt.get("result_id"), "review_receipt.result_id")
    if result_id != result_checked.get("id", result_checked.get("result_id")):
        raise BoundaryError("review_receipt.result_id がresult.idと一致しません。", code="contract_binding")
    contract_id = _id(receipt.get("contract_id"), "review_receipt.contract_id")
    if contract_id != contract_checked["id"]:
        raise BoundaryError("review_receipt.contract_id がcontract.idと一致しません。", code="contract_binding")
    root = _target_raw(result_checked, "result")
    _binding_target(receipt, "review_receipt", root)
    result_snapshot = _snapshot_shape(_first(result_checked, "result_snapshot", "snapshot"), "result.result_snapshot")
    receipt_snapshot = _snapshot_shape(_first(receipt, "subject_snapshot", "snapshot"), "review_receipt.subject_snapshot")
    if receipt_snapshot != result_snapshot:
        raise BoundaryError("review receipt snapshotがresult snapshotと一致しません。", code="stale_evidence")
    if recompute:
        recompute_snapshot(root, receipt_snapshot)
    _evidence_refs(receipt, "review_receipt", required=True)
    decision = _nonempty_string(receipt.get("decision"), "review_receipt.decision")
    if decision not in ACCEPTED_DECISIONS:
        raise BoundaryError("review_receipt.decision が不正です。")
    findings = _findings(receipt.get("findings", receipt.get("finding_candidates")), "review_receipt.findings")
    dispositions = _dispositions(receipt.get("finding_dispositions", receipt.get("dispositions")), {str(item["id"]) for item in findings}, "review_receipt.finding_dispositions")
    required_ids = {str(item["id"]) for item in findings if item.get("required", True)}
    unresolved = set(dispositions["unresolved"])
    if decision in {"accept", "accepted", "accept_with_risks"} and required_ids.intersection(unresolved):
        raise BoundaryError("acceptance decisionにunresolved required findingがあります。", code="unresolved_finding")
    return receipt


def validate_checkpoint(
    value: Mapping[str, Any],
    contract: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
    *,
    recompute: bool = True,
) -> dict[str, Any]:
    checkpoint = _mapping(value, "checkpoint")
    _artifact_type(checkpoint.get("type"), "checkpoint")
    _version(checkpoint.get("schema_version"))
    _id(checkpoint.get("id", checkpoint.get("checkpoint_id")), "checkpoint.id")
    contract_map = _contract_mapping(contract, checkpoint, "checkpoint")
    contract_checked = validate_task_contract(contract_map, recompute=False)
    contract_id = _id(checkpoint.get("contract_id"), "checkpoint.contract_id")
    if contract_id != contract_checked["id"]:
        raise BoundaryError("checkpoint.contract_id がcontract.idと一致しません。", code="contract_binding")
    root = _target_raw(contract_checked, "task_contract")
    _binding_target(checkpoint, "checkpoint", root)
    snapshot = _snapshot_shape(_first(checkpoint, "subject_snapshot", "snapshot"), "checkpoint.subject_snapshot")
    if result is not None:
        result_snapshot = _snapshot_shape(_first(result, "result_snapshot", "snapshot"), "result.result_snapshot")
        if snapshot != result_snapshot:
            raise BoundaryError("checkpoint snapshotがresult snapshotと一致しません。", code="checkpoint_mismatch")
    if recompute:
        recompute_snapshot(root, snapshot)
    expected = _first(contract_checked, "checkpoint", "expected_checkpoint")
    if expected is not None:
        expected_map = _mapping(expected, "task_contract.checkpoint")
        for key in ("id", "stage", "status"):
            if key in expected_map and checkpoint.get(key) != expected_map[key]:
                raise BoundaryError(f"checkpoint.{key} がcontract checkpointと一致しません。", code="checkpoint_mismatch")
        if "snapshot" in expected_map and expected_map["snapshot"] != snapshot:
            raise BoundaryError("checkpoint snapshotがcontract checkpointと一致しません。", code="checkpoint_mismatch")
    if "stage" in checkpoint:
        _nonempty_string(checkpoint["stage"], "checkpoint.stage")
    if "status" in checkpoint:
        _nonempty_string(checkpoint["status"], "checkpoint.status")
    return checkpoint


def validate_artifact(
    value: Mapping[str, Any],
    *,
    kind: str | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """Dispatch validation for one artifact or a small stateless envelope."""

    root_value = _mapping(value, "artifact")
    if kind is None:
        raw_type = root_value.get("type")
        kind = TYPE_ALIASES.get(raw_type, raw_type)
    if not isinstance(kind, str) or kind not in ARTIFACT_TYPES:
        raise BoundaryError(f"未知のartifact typeです: {kind}", code="unsupported")
    if kind == "task_contract":
        return validate_task_contract(root_value, recompute=recompute)
    if kind == "assignment":
        contract = root_value.get("contract") if isinstance(root_value.get("contract"), Mapping) else None
        return validate_assignment(root_value, contract, recompute=recompute)
    if kind == "result":
        assignment = root_value.get("assignment") if isinstance(root_value.get("assignment"), Mapping) else None
        contract = root_value.get("contract") if isinstance(root_value.get("contract"), Mapping) else None
        return validate_result(root_value, assignment, contract, recompute=recompute)
    if kind == "review_receipt":
        result = root_value.get("result") if isinstance(root_value.get("result"), Mapping) else None
        contract = root_value.get("contract") if isinstance(root_value.get("contract"), Mapping) else None
        return validate_review_receipt(root_value, result, contract, recompute=recompute)
    contract = root_value.get("contract") if isinstance(root_value.get("contract"), Mapping) else None
    result = root_value.get("result") if isinstance(root_value.get("result"), Mapping) else None
    return validate_checkpoint(root_value, contract, result, recompute=recompute)


def _read_json(path: str | None) -> dict[str, Any]:
    try:
        if path and path != "-":
            raw = Path(path).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BoundaryError(f"JSONを安全に読み込めません: {exc}", code="invalid_input") from exc
    return _mapping(value, "artifact")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    validate = subparsers.add_parser("validate", help="one stateless boundary artifactを検証します")
    validate.add_argument("--kind", choices=sorted(ARTIFACT_TYPES))
    validate.add_argument("--input", metavar="JSON")
    validate.add_argument("--no-recompute", action="store_true", help=argparse.SUPPRESS)
    # A direct invocation without the subcommand is convenient for shell
    # integrations and remains unambiguous because --kind is required there.
    parser.add_argument("--kind", dest="direct_kind", choices=sorted(ARTIFACT_TYPES))
    parser.add_argument("--input", dest="direct_input", metavar="JSON")
    parser.add_argument("--no-recompute", dest="direct_no_recompute", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    kind = args.kind if args.command == "validate" else args.direct_kind
    path = args.input if args.command == "validate" else args.direct_input
    no_recompute = args.no_recompute if args.command == "validate" else args.direct_no_recompute
    try:
        artifact = _read_json(path)
        checked = validate_artifact(artifact, kind=kind, recompute=not no_recompute)
    except (BoundaryError, TypeError, OSError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": exc.code, "message": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2 if exc.code == "unsupported" else 1
    output = {
        "ok": True,
        "type": TYPE_ALIASES.get(checked.get("type"), checked.get("type")),
        "schema_version": checked.get("schema_version"),
        "id": checked.get("id", checked.get("assignment_id", checked.get("result_id", checked.get("review_id", checked.get("checkpoint_id"))))),
    }
    if isinstance(checked.get("target_repo_root"), str):
        output["target_repo_root"] = str(canonical_target_root(checked["target_repo_root"]))
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
