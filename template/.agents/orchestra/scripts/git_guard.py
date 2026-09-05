#!/usr/bin/env python3
"""Perform the stateless, closed allowlist of safe local Git operations.

The guard accepts a root-scoped operation contract and reissues the assigned
snapshot immediately before every write.  It never pushes, rewrites history,
resets a worktree, deletes refs, or stores workflow state.  A caller/actor
label is metadata only; host sandbox and approval enforcement provide the
actual authority.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:  # direct execution and package-style imports
    from . import boundary_guard, snapshot_digest  # type: ignore
except ImportError:  # pragma: no cover - exercised by the CLI
    import boundary_guard  # type: ignore
    import snapshot_digest  # type: ignore


ALLOWED_OPERATIONS = {
    "branch_create_and_switch_new",
    "rename_origin_unpushed_branch",
    "stage_exact_paths_or_hunks",
    "unstage_index_only_exact_paths",
    "commit_non_amend",
}
FORBIDDEN_OPERATIONS = {
    "push",
    "pull",
    "fetch",
    "merge",
    "rebase",
    "reset",
    "reset_hard",
    "clean",
    "commit_amend",
    "amend",
    "checkout",
    "switch",
    "branch_delete",
    "branch_force",
    "tag_delete",
    "filter_repo",
    "gc",
    "stash",
}
PROTECTED_BRANCHES = {"main", "master", "develop", "trunk", "production"}
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
MAX_PATCH_BYTES = 8 * 1024 * 1024
REQUIRED_GUARD_FIELDS = {
    "target_repo_root",
    "allowed_operations",
    "path_or_ref_scope",
    "subject_snapshot",
    "preconditions",
    "postconditions",
    "forbidden_operations",
}


class GitGuardError(RuntimeError):
    """A requested Git operation is unsupported or failed a safety gate."""

    def __init__(self, message: str, *, code: str = "guard_rejected") -> None:
        super().__init__(message)
        self.code = code


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GitGuardError(f"{label} はobjectにしてください。")
    return dict(value)


def _string(value: Any, label: str, *, allow_newlines: bool = False) -> str:
    if not isinstance(value, str) or not value or "\0" in value or (not allow_newlines and ("\r" in value or "\n" in value)):
        raise GitGuardError(f"{label} は安全な空でない文字列にしてください。")
    return value


def _extract(contract: Mapping[str, Any], key: str, *nested: str) -> Any:
    if key in contract:
        return contract[key]
    for parent_key in nested:
        parent = contract.get(parent_key)
        if isinstance(parent, Mapping) and key in parent:
            return parent[key]
    return None


def _scope(contract: Mapping[str, Any]) -> dict[str, Any]:
    raw = _extract(contract, "path_or_ref_scope", "authorization", "scope")
    if raw is None:
        raw = {}
    result = _mapping(raw, "path_or_ref_scope")
    if "paths" not in result:
        direct = _extract(contract, "allowed_paths", "authorization")
        if direct is not None:
            result["paths"] = direct
    return result


def _safe_path(value: Any, label: str) -> str:
    try:
        return snapshot_digest._safe_relative(value, label=label)  # type: ignore[attr-defined]
    except (snapshot_digest.SnapshotError, TypeError, ValueError) as exc:
        raise GitGuardError(str(exc), code="unsafe_path") from exc


def _paths(scope: Mapping[str, Any], label: str = "path_or_ref_scope.paths", *, required: bool = True) -> list[str]:
    raw = scope.get("paths")
    if raw is None:
        if required:
            raise GitGuardError(f"{label} がありません。", code="scope_mismatch")
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise GitGuardError(f"{label} はpath listにしてください。", code="scope_mismatch")
    result = [_safe_path(item, f"{label}[{index}]") for index, item in enumerate(raw)]
    if len(result) != len(set(result)):
        raise GitGuardError(f"{label} に重複pathがあります。", code="scope_overlap")
    # An operation's path list must be an exact, deterministic set.  Parent /
    # child overlaps would make a directory stage silently widen its scope.
    ordered = sorted(result, key=lambda item: (len(PurePosixPath(item).parts), item.encode("utf-8")))
    for index, path in enumerate(ordered):
        parts = PurePosixPath(path).parts
        for other in ordered[index + 1 :]:
            if parts == PurePosixPath(other).parts[: len(parts)]:
                raise GitGuardError(f"{label} が重複または包含関係です: {path}, {other}", code="scope_overlap")
    return sorted(result, key=lambda item: item.encode("utf-8"))


def _covered(path: str, scopes: Sequence[str]) -> bool:
    path_parts = PurePosixPath(path).parts
    return any(path_parts[: len(PurePosixPath(scope).parts)] == PurePosixPath(scope).parts for scope in scopes)


def _validate_contract(contract_value: Mapping[str, Any], operation: str | None = None) -> tuple[dict[str, Any], Path, str, dict[str, Any], list[str]]:
    contract = _mapping(contract_value, "git contract")
    authorization = contract.get("authorization")
    if authorization is not None and not isinstance(authorization, Mapping):
        raise GitGuardError("authorization はobjectにしてください。")
    raw_allowed = _extract(contract, "allowed_operations", "authorization")
    if not isinstance(raw_allowed, Sequence) or isinstance(raw_allowed, (str, bytes, bytearray)):
        raise GitGuardError("allowed_operations はlistにしてください。", code="unsupported")
    allowed = [_string(item, "allowed_operations item") for item in raw_allowed]
    if len(allowed) != len(set(allowed)) or any(item not in ALLOWED_OPERATIONS for item in allowed):
        raise GitGuardError("closed Git operation allowlist外です。", code="unsupported")
    selected = operation or _extract(contract, "operation", "authorization")
    if selected is None and len(allowed) == 1:
        selected = allowed[0]
    selected = _string(selected, "operation")
    if selected in FORBIDDEN_OPERATIONS or selected not in ALLOWED_OPERATIONS:
        raise GitGuardError(f"Git operationは許可されたclosed allowlist外です: {selected}", code="unsupported")
    if selected not in allowed:
        raise GitGuardError(f"operationがassignment allowed_operationsにありません: {selected}", code="unsupported")
    forbidden = _extract(contract, "forbidden_operations", "authorization")
    if forbidden is None:
        forbidden_values: list[str] = []
    elif isinstance(forbidden, Mapping):
        forbidden_values = [_string(key, "forbidden_operations key") for key, value in forbidden.items() if value is True]
    elif isinstance(forbidden, Sequence) and not isinstance(forbidden, (str, bytes, bytearray)):
        forbidden_values = [_string(item, "forbidden_operations item") for item in forbidden]
    else:
        raise GitGuardError("forbidden_operations はlistまたはobjectにしてください。")
    if selected in forbidden_values:
        raise GitGuardError(f"operationがforbidden_operationsに含まれています: {selected}", code="unsupported")
    target = _extract(contract, "target_repo_root", "authorization")
    try:
        root = boundary_guard.canonical_target_root(_string(target, "target_repo_root"))
    except boundary_guard.BoundaryError as exc:
        raise GitGuardError(str(exc), code="invalid_target_root") from exc
    raw_snapshot = _extract(contract, "subject_snapshot", "authorization")
    if raw_snapshot is None:
        raise GitGuardError("subject_snapshot がありません。", code="stale_evidence")
    try:
        snapshot = boundary_guard._snapshot_shape(raw_snapshot, "subject_snapshot")  # type: ignore[attr-defined]
    except boundary_guard.BoundaryError as exc:
        raise GitGuardError(str(exc), code="invalid_snapshot") from exc
    scope = _scope(contract)
    preconditions = _extract(contract, "preconditions")
    if preconditions is None:
        preconditions = {}
    preconditions = _mapping(preconditions, "preconditions")
    for key, value in preconditions.items():
        if isinstance(value, bool) and value is False:
            raise GitGuardError(f"preconditionが満たされていません: {key}", code="precondition_failed")
    postconditions = _extract(contract, "postconditions")
    if postconditions is None:
        postconditions = {}
    postconditions = _mapping(postconditions, "postconditions")
    paths = _paths(scope, required=selected in {"stage_exact_paths_or_hunks", "unstage_index_only_exact_paths", "commit_non_amend"})
    return contract, root, selected, snapshot, paths


def _git(repo: Path, args: list[str], *, check: bool = True, input_data: bytes | None = None) -> bytes:
    try:
        return snapshot_digest._run(repo, args, check=check, input_data=input_data)  # type: ignore[attr-defined]
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError(str(exc), code="git_failed") from exc


def _git_text(repo: Path, args: list[str], *, check: bool = True) -> str:
    try:
        return _git(repo, args, check=check).decode("utf-8", errors="strict").strip()
    except UnicodeError as exc:
        raise GitGuardError(f"Git outputを安全にdecodeできません: {exc}", code="git_failed") from exc


def _preflight_snapshot(root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        actual = boundary_guard.recompute_snapshot(root, expected)
    except boundary_guard.BoundaryError as exc:
        raise GitGuardError(str(exc), code="stale_evidence") from exc
    if actual != dict(expected):
        raise GitGuardError("preflight helper snapshotがassignment snapshotと一致しません。", code="stale_evidence")
    return actual


def _post_snapshot(root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    # The same scope/base is intentionally used after the write.  For a commit
    # this yields the new revision; for index-only stage/unstage it proves the
    # worktree content did not drift.
    try:
        shaped = boundary_guard._snapshot_shape(expected, "postwrite source snapshot")  # type: ignore[attr-defined]
        return snapshot_digest.compute_snapshot(
            root,
            kind=str(shaped["kind"]),
            base_ref=shaped.get("base_ref"),
            head_ref=shaped.get("head_ref"),
            scope_paths=list(shaped["scope_paths"]),
            untracked_paths=list(shaped["untracked_paths"]),
        )
    except (boundary_guard.BoundaryError, snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError(f"post-write snapshotを発行できません: {exc}", code="postcondition_failed") from exc


def _status(repo: Path) -> tuple[str, set[str]]:
    raw = _git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"])
    # NUL records contain XY + space + path.  Rename records have a second
    # path; include both so an unrelated rename cannot hide in the index.
    records = [item for item in raw.split(b"\0") if item]
    paths: set[str] = set()
    for record in records:
        body = record[3:] if len(record) >= 3 else b""
        try:
            first = body.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise GitGuardError("git status pathをdecodeできません。", code="unsafe_path") from exc
        if " -> " in first:
            old, new = first.split(" -> ", 1)
            paths.update({_safe_path(old, "status path"), _safe_path(new, "status path")})
        elif first:
            paths.add(_safe_path(first, "status path"))
    return _git_text(repo, ["branch", "--show-current"], check=False), paths


def _staged_paths(repo: Path) -> set[str]:
    raw = _git(repo, ["diff", "--cached", "--name-only", "-z", "--no-ext-diff", "--no-textconv"])
    result: set[str] = set()
    for item in raw.split(b"\0"):
        if item:
            result.add(_safe_path(item.decode("utf-8", errors="strict"), "staged path"))
    return result


def _ensure_no_unrelated_staged(repo: Path, paths: Sequence[str]) -> set[str]:
    staged = _staged_paths(repo)
    if any(not _covered(path, paths) for path in staged):
        outside = sorted(path for path in staged if not _covered(path, paths))
        raise GitGuardError(f"unrelated staged pathがあります: {outside[0]}", code="scope_expansion")
    return staged


def _ensure_no_symlink_path(root: Path, rel: str, *, reject_directory: bool = False) -> None:
    current = root
    parts = PurePosixPath(rel).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if current.is_symlink():
            raise GitGuardError(f"scope pathにsymlink componentがあります: {rel}", code="unsafe_path")
        if index == len(parts) - 1 and reject_directory and current.is_dir():
            raise GitGuardError(f"exact path operationにdirectoryを指定できません: {rel}", code="scope_mismatch")


def _branch_name(value: Any, label: str) -> str:
    branch = _string(value, label)
    if BRANCH_RE.fullmatch(branch) is None or ".." in branch or "//" in branch or branch.endswith(".") or branch.endswith("/") or "@{" in branch or "/." in branch:
        raise GitGuardError(f"{label} の形式が不正です。", code="unsafe_ref")
    if any(token in branch.casefold() for token in ("secret", "token", "credential", "password", "private", "apikey", "pii")):
        raise GitGuardError(f"{label} はsecret-like refのため拒否します。", code="unsafe_ref")
    return branch


def _ref(scope: Mapping[str, Any], contract: Mapping[str, Any], key: str, label: str, *, required: bool = True) -> str | None:
    raw = scope.get(key)
    if raw is None:
        raw = contract.get(key)
    if raw is None and required:
        raise GitGuardError(f"{label} がありません。", code="unsafe_ref")
    if raw is None:
        return None
    value = _string(raw, label)
    if value.startswith("-") or any(char in value for char in ("\0", "\r", "\n")):
        raise GitGuardError(f"{label} が安全なrefではありません。", code="unsafe_ref")
    return value


def _assert_no_operation_state(repo: Path) -> None:
    # These are repository-local state markers, not user content.  Refuse a
    # write in the middle of another history operation.
    dot_git = repo / ".git"
    if dot_git.is_dir() and not dot_git.is_symlink():
        candidates = [dot_git / name for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge", "sequencer")]
        if any(path.exists() for path in candidates):
            raise GitGuardError("merge/rebase/cherry-pick state中はGit writeできません。", code="precondition_failed")


def _assert_branch_clean_state(repo: Path, *, require_branch: bool = True) -> str:
    _assert_no_operation_state(repo)
    branch, _paths = _status(repo)
    if require_branch and not branch:
        raise GitGuardError("detached HEADではこのGit operationを実行できません。", code="precondition_failed")
    return branch


def _run_branch_create(root: Path, contract: Mapping[str, Any], scope: Mapping[str, Any]) -> dict[str, Any]:
    current = _assert_branch_clean_state(root, require_branch=True)
    new_branch = _branch_name(_ref(scope, contract, "new_branch", "new_branch"), "new_branch")
    base_ref = _ref(scope, contract, "base_ref", "base_ref", required=False) or "HEAD"
    if current == new_branch:
        raise GitGuardError("new_branchが現在branchと同じです。", code="unsafe_ref")
    # `_run` exposes stdout rather than a return code; branch --list gives an
    # exact, literal local-ref check without accepting a remote guess.
    branches = _git_text(root, ["branch", "--format=%(refname:short)", "--list", new_branch], check=False)
    if branches.strip() == new_branch:
        raise GitGuardError("new_branchが既に存在します。", code="unsafe_ref")
    try:
        resolved_base = snapshot_digest._resolve_commit(root, base_ref, label="base_ref")  # type: ignore[attr-defined]
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError(str(exc), code="unsafe_ref") from exc
    _git(root, ["switch", "--no-guess", "--create", new_branch, resolved_base])
    after = _assert_branch_clean_state(root, require_branch=True)
    if after != new_branch:
        raise GitGuardError("branch create後のcurrent branchが一致しません。", code="postcondition_failed")
    return {"branch": after, "base_ref": resolved_base}


def _run_branch_rename(root: Path, contract: Mapping[str, Any], scope: Mapping[str, Any]) -> dict[str, Any]:
    current = _assert_branch_clean_state(root, require_branch=True)
    if current in PROTECTED_BRANCHES or current.startswith(("release/", "hotfix/")):
        raise GitGuardError("protected branchはrenameできません。", code="precondition_failed")
    expected_current = _ref(scope, contract, "current_branch", "current_branch", required=False)
    if expected_current is not None and expected_current != current:
        raise GitGuardError("current_branchがactual branchと一致しません。", code="stale_evidence")
    new_branch = _branch_name(_ref(scope, contract, "new_branch", "new_branch"), "new_branch")
    if new_branch == current:
        raise GitGuardError("new_branchがcurrent branchと同じです。", code="unsafe_ref")
    branches = _git_text(root, ["branch", "--format=%(refname:short)", "--list", new_branch], check=False)
    if branches.strip() == new_branch:
        raise GitGuardError("new_branchが既に存在します。", code="unsafe_ref")
    upstream = _git_text(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], check=False)
    if upstream:
        raise GitGuardError("current branchにupstreamがあるため、unpushed renameを証明できません。", code="precondition_failed")
    remotes = _git_text(root, ["for-each-ref", "--format=%(refname)", "refs/remotes"], check=False).splitlines()
    if any(ref.endswith("/" + current) for ref in remotes):
        raise GitGuardError("current branch名のremote-tracking refがあるためunpushedを証明できません。", code="precondition_failed")
    _git(root, ["branch", "--move", current, new_branch])
    after = _assert_branch_clean_state(root, require_branch=True)
    if after != new_branch:
        raise GitGuardError("branch rename後のcurrent branchが一致しません。", code="postcondition_failed")
    return {"branch": after, "previous_branch": current}


def _patch_paths(patch: bytes) -> list[str]:
    if not patch or len(patch) > MAX_PATCH_BYTES or b"\0" in patch:
        raise GitGuardError("patchが空、過大、またはNULを含みます。", code="unsafe_patch")
    try:
        text = patch.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GitGuardError("patchはUTF-8 textにしてください。", code="unsafe_patch") from exc
    if "GIT binary patch" in text:
        raise GitGuardError("binary patchはhunk allowlistでは対応しません。", code="unsafe_patch")
    paths: list[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            raw = line.removeprefix("diff --git ")
            try:
                tokens = shlex.split(raw, posix=True)
            except ValueError as exc:
                raise GitGuardError("diff headerを安全にparseできません。", code="unsafe_patch") from exc
            if len(tokens) != 2 or not tokens[0].startswith("a/") or not tokens[1].startswith("b/"):
                raise GitGuardError("patch diff headerのpathが不正です。", code="unsafe_patch")
            left = _safe_path(tokens[0][2:], "patch path")
            right = _safe_path(tokens[1][2:], "patch path")
            if left != right:
                raise GitGuardError("rename patchはhunk allowlistでは対応しません。", code="unsafe_patch")
            paths.append(left)
        elif line.startswith("--- ") or line.startswith("+++ "):
            raw = line[4:].split("\t", 1)[0]
            if raw == "/dev/null":
                continue
            prefix = "a/" if line.startswith("--- ") else "b/"
            if not raw.startswith(prefix):
                raise GitGuardError("patch file headerのpathが不正です。", code="unsafe_patch")
            path = _safe_path(raw[2:], "patch path")
            paths.append(path)
    if not paths:
        raise GitGuardError("patchから安全なpathを抽出できません。", code="unsafe_patch")
    unique = sorted(set(paths), key=lambda item: item.encode("utf-8"))
    return unique


def _patch_input(contract: Mapping[str, Any], patch: bytes | str | None) -> bytes | None:
    if patch is None:
        raw = contract.get("patch")
        if raw is None and isinstance(contract.get("path_or_ref_scope"), Mapping):
            raw = contract["path_or_ref_scope"].get("patch")
        if raw is None:
            return None
        patch = raw
    if isinstance(patch, str):
        return patch.encode("utf-8")
    if isinstance(patch, bytes):
        return patch
    raise GitGuardError("patchはUTF-8 stringまたはbytesにしてください。", code="unsafe_patch")


def _run_stage(root: Path, contract: Mapping[str, Any], paths: Sequence[str], patch: bytes | str | None) -> dict[str, Any]:
    _assert_branch_clean_state(root, require_branch=True)
    before = _ensure_no_unrelated_staged(root, paths)
    patch_bytes = _patch_input(contract, patch)
    for path in paths:
        _ensure_no_symlink_path(root, path, reject_directory=patch_bytes is None)
    if patch_bytes is not None:
        patch_paths = _patch_paths(patch_bytes)
        if set(patch_paths) != set(paths):
            raise GitGuardError("patch pathsがexact path scopeと一致しません。", code="scope_expansion")
        try:
            _git(root, ["apply", "--cached", "--check", "--whitespace=nowarn", "-"], input_data=patch_bytes)
            _git(root, ["apply", "--cached", "--whitespace=nowarn", "-"], input_data=patch_bytes)
        except GitGuardError as exc:
            raise GitGuardError(f"scope-bound patchを適用できません: {exc}", code="unsafe_patch") from exc
    else:
        _git(root, ["add", "--", *paths])
    after = _ensure_no_unrelated_staged(root, paths)
    if not after.issuperset(before):
        raise GitGuardError("stage後に既存staged pathが失われました。", code="postcondition_failed")
    return {"staged_paths": sorted(after, key=lambda item: item.encode("utf-8"))}


def _working_content_snapshot(root: Path, paths: Sequence[str]) -> dict[str, Any]:
    actual_untracked = snapshot_digest._nul_paths(  # type: ignore[attr-defined]
        snapshot_digest._run(root, ["ls-files", "--others", "--exclude-standard", "-z", "--", *paths]),  # type: ignore[attr-defined]
        label="actual untracked path",
    )
    return snapshot_digest.compute_snapshot(root, kind="working_tree_content", base_ref="HEAD", scope_paths=list(paths), untracked_paths=actual_untracked)


def _run_unstage(root: Path, paths: Sequence[str]) -> dict[str, Any]:
    _assert_branch_clean_state(root, require_branch=True)
    before_paths = _ensure_no_unrelated_staged(root, paths)
    before_content = _working_content_snapshot(root, paths)
    for path in paths:
        _ensure_no_symlink_path(root, path, reject_directory=True)
    _git(root, ["restore", "--staged", "--", *paths])
    after_paths = _ensure_no_unrelated_staged(root, paths)
    after_content = _working_content_snapshot(root, paths)
    if before_content != after_content:
        raise GitGuardError("unstageがworktree contentを変更しました。", code="postcondition_failed")
    if any(path in after_paths for path in paths):
        raise GitGuardError("exact path unstage後も対象pathがstagedです。", code="postcondition_failed")
    return {"staged_paths_before": sorted(before_paths, key=lambda item: item.encode("utf-8")), "staged_paths_after": sorted(after_paths, key=lambda item: item.encode("utf-8"))}


def _commit_message(contract: Mapping[str, Any], scope: Mapping[str, Any]) -> str:
    raw = scope.get("message", contract.get("message"))
    return _string(raw, "commit message", allow_newlines=True)


def _run_commit(root: Path, contract: Mapping[str, Any], paths: Sequence[str], scope: Mapping[str, Any]) -> dict[str, Any]:
    _assert_branch_clean_state(root, require_branch=True)
    staged = _ensure_no_unrelated_staged(root, paths)
    if not staged:
        raise GitGuardError("commit対象のstaged pathがありません。", code="precondition_failed")
    if any(not _covered(path, paths) for path in staged):
        raise GitGuardError("staged pathがexact commit scopeの外です。", code="scope_expansion")
    for path in staged:
        _ensure_no_symlink_path(root, path)
    message = _commit_message(contract, scope)
    before_head = _git_text(root, ["rev-parse", "HEAD"])
    # Explicitly disable hooks/signing and pass only argv, so repository
    # configuration cannot execute an external helper during this write.
    _git(root, ["commit", "--no-verify", "--no-gpg-sign", "-m", message])
    after_head = _git_text(root, ["rev-parse", "HEAD"])
    if after_head == before_head:
        raise GitGuardError("commit後のHEADが変化していません。", code="postcondition_failed")
    return {"commit": after_head, "committed_paths": sorted(staged, key=lambda item: item.encode("utf-8"))}


def apply(
    contract: Mapping[str, Any],
    *,
    operation: str | None = None,
    patch: bytes | str | None = None,
) -> dict[str, Any]:
    """Validate and execute one closed-allowlist operation.

    The returned mapping contains helper-issued pre/post snapshots and concise
    operation evidence.  It does not contain raw patches, commit messages, or
    repository content.
    """

    contract_map, root, selected, subject_snapshot, paths = _validate_contract(contract, operation)
    scope = _scope(contract_map)
    pre_snapshot = _preflight_snapshot(root, subject_snapshot)
    if selected in {"stage_exact_paths_or_hunks", "unstage_index_only_exact_paths", "commit_non_amend"}:
        _ensure_no_unrelated_staged(root, paths)
    if selected == "branch_create_and_switch_new":
        evidence = _run_branch_create(root, contract_map, scope)
    elif selected == "rename_origin_unpushed_branch":
        evidence = _run_branch_rename(root, contract_map, scope)
    elif selected == "stage_exact_paths_or_hunks":
        evidence = _run_stage(root, contract_map, paths, patch)
    elif selected == "unstage_index_only_exact_paths":
        evidence = _run_unstage(root, paths)
    elif selected == "commit_non_amend":
        evidence = _run_commit(root, contract_map, paths, scope)
    else:  # protected by _validate_contract; defensive for future changes
        raise GitGuardError(f"unsupported operation: {selected}", code="unsupported")
    post_snapshot = _post_snapshot(root, subject_snapshot)
    expected_post = _extract(contract_map, "postwrite_snapshot", "postconditions")
    if expected_post is not None:
        try:
            expected_shape = boundary_guard._snapshot_shape(expected_post, "postwrite_snapshot")  # type: ignore[attr-defined]
        except boundary_guard.BoundaryError as exc:
            raise GitGuardError(str(exc), code="postcondition_failed") from exc
        if post_snapshot != expected_shape:
            raise GitGuardError("actual postwrite snapshotがcontract postwrite_snapshotと一致しません。", code="postcondition_failed")
    return {
        "ok": True,
        "operation": selected,
        "target_repo_root": str(root),
        "preflight_snapshot": pre_snapshot,
        "postwrite_snapshot": post_snapshot,
        "evidence": evidence,
    }


def _read_contract(path: str | None) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8") if path and path != "-" else sys.stdin.read()
        return _mapping(json.loads(raw), "git contract")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GitGuardError(f"Git contract JSONを安全に読み込めません: {exc}", code="invalid_input") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    apply_parser = sub.add_parser("apply", help="one closed-allowlist Git operationを実行します")
    apply_parser.add_argument("--operation", choices=sorted(ALLOWED_OPERATIONS), required=True)
    apply_parser.add_argument("--contract", metavar="JSON")
    apply_parser.add_argument("--patch-file", metavar="PATCH")
    apply_parser.add_argument("--patch")
    # Direct form: git_guard.py --operation ... --contract ...
    parser.add_argument("--operation", dest="direct_operation", choices=sorted(ALLOWED_OPERATIONS))
    parser.add_argument("--contract", dest="direct_contract", metavar="JSON")
    parser.add_argument("--patch-file", dest="direct_patch_file", metavar="PATCH")
    parser.add_argument("--patch", dest="direct_patch")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    operation = args.operation if args.command == "apply" else args.direct_operation
    contract_path = args.contract if args.command == "apply" else args.direct_contract
    patch_path = args.patch_file if args.command == "apply" else args.direct_patch_file
    patch_inline = args.patch if args.command == "apply" else args.direct_patch
    try:
        contract = _read_contract(contract_path)
        patch: bytes | str | None = patch_inline
        if patch_path and patch_path != "-":
            patch = Path(patch_path).read_bytes()
        elif patch_path == "-":
            patch = sys.stdin.buffer.read()
        result = apply(contract, operation=operation, patch=patch)
    except (GitGuardError, OSError, UnicodeError) as exc:
        if not isinstance(exc, GitGuardError):
            exc = GitGuardError(f"Git contract/patchを安全に読み込めません: {exc}", code="invalid_input")
        print(json.dumps({"ok": False, "error": exc.code, "message": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2 if exc.code == "unsupported" else 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
