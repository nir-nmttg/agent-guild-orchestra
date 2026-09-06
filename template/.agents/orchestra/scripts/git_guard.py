#!/usr/bin/env python3
"""状態を保持せず、許可一覧に限定した安全なローカルGit操作を実行します。

対象Gitルートを指定した操作条件を受け取り、書き込みの直前に毎回
指定されたスナップショットを再発行します。push、履歴の書き換え、
作業ツリーのreset、参照の削除、作業状態の保存は行いません。
呼び出し元や担当者のラベルはメタデータにすぎず、実際の権限は
実行環境のサンドボックスと承認機構が制御します。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:  # direct execution and package-style imports
    from . import snapshot_digest  # type: ignore
except ImportError:  # pragma: no cover - exercised by the CLI
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
TREE_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
MAX_PATCH_BYTES = 8 * 1024 * 1024


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
        return snapshot_digest.safe_relative(value, label=label)
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
        root = snapshot_digest.canonical_target_root(_string(target, "target_repo_root"))
    except snapshot_digest.SnapshotError as exc:
        raise GitGuardError(str(exc), code="invalid_target_root") from exc
    raw_snapshot = _extract(contract, "subject_snapshot", "authorization")
    if raw_snapshot is None:
        raise GitGuardError("subject_snapshot がありません。", code="stale_evidence")
    try:
        snapshot = snapshot_digest.snapshot_shape(raw_snapshot, "subject_snapshot")
    except snapshot_digest.SnapshotError as exc:
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
        return snapshot_digest.run_git(repo, args, check=check, input_data=input_data)
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError(str(exc), code="git_failed") from exc


def _git_text(repo: Path, args: list[str], *, check: bool = True) -> str:
    try:
        return _git(repo, args, check=check).decode("utf-8", errors="strict").strip()
    except UnicodeError as exc:
        raise GitGuardError(f"Git outputを安全にdecodeできません: {exc}", code="git_failed") from exc


def _preflight_snapshot(root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        actual = snapshot_digest.recompute_snapshot(root, expected)
    except snapshot_digest.SnapshotError as exc:
        raise GitGuardError(str(exc), code="stale_evidence") from exc
    if actual != dict(expected):
        raise GitGuardError("preflight helper snapshotがassignment snapshotと一致しません。", code="stale_evidence")
    return actual


def _post_snapshot(root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    # The same scope/base is intentionally used after the write.  For a commit
    # this yields the new revision; for index-only stage/unstage it proves the
    # worktree content did not drift.
    try:
        shaped = snapshot_digest.snapshot_shape(expected, "postwrite source snapshot")
        untracked_paths = list(shaped["untracked_paths"])
        if shaped["kind"] == "working_tree_content":
            untracked_paths = _actual_untracked_paths(root, list(shaped["scope_paths"]))
        return snapshot_digest.compute_snapshot(
            root,
            kind=str(shaped["kind"]),
            base_ref=shaped.get("base_ref"),
            head_ref=shaped.get("head_ref"),
            scope_paths=list(shaped["scope_paths"]),
            untracked_paths=untracked_paths,
        )
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError(f"post-write snapshotを発行できません: {exc}", code="postcondition_failed") from exc


def _status(repo: Path) -> tuple[str, set[str]]:
    raw = _git(
        repo,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--no-renames",
            "--untracked-files=all",
        ],
    )
    # --no-renames expands a rename/copy into endpoint records. Each NUL
    # record then contains XY + space + path, so neither endpoint can hide in
    # the index status set.
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
    raw = _git(
        repo,
        [
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--no-renames",
            "--ignore-submodules=none",
            "--no-ext-diff",
            "--no-textconv",
        ],
    )
    result: set[str] = set()
    for item in raw.split(b"\0"):
        if item:
            result.add(_safe_path(item.decode("utf-8", errors="strict"), "staged path"))
    return result


def _index_tree(repo: Path) -> str:
    """Return the Git tree OID represented by the current index.

    ``git write-tree`` serializes the index without consulting the worktree,
    so this is the machine-issued identifier used to bind a reviewed staged
    result to a later commit.  It also rejects an unmerged or otherwise
    unusable index before a commit can be attempted.
    """

    tree = _git_text(repo, ["write-tree"])
    if TREE_OID_RE.fullmatch(tree) is None:
        raise GitGuardError("index treeを安全なGit tree OIDとして取得できません。", code="git_failed")
    return tree


def _tree_changed_paths(repo: Path, base_head: str, tree: str) -> set[str]:
    try:
        raw = _git(
            repo,
            [
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                "--ignore-submodules=none",
                "--no-ext-diff",
                "--no-textconv",
                base_head,
                tree,
                "--",
            ],
        )
        return set(snapshot_digest.nul_paths(raw, label="expected tree path"))
    except (snapshot_digest.SnapshotError, UnicodeError) as exc:
        raise GitGuardError(f"expected index treeのpathを安全に確認できません: {exc}", code="stale_evidence") from exc


def index_tree(repo: str | Path) -> str:
    """Return a machine-issued tree OID for a validated repository index."""

    try:
        root = snapshot_digest.canonical_target_root(repo)
    except snapshot_digest.SnapshotError as exc:
        raise GitGuardError(str(exc), code="invalid_target_root") from exc
    _assert_branch_clean_state(root, require_branch=False)
    return _index_tree(root)


def _expected_index_tree(contract: Mapping[str, Any]) -> str:
    raw = _extract(contract, "expected_index_tree", "authorization", "preconditions")
    if raw is None:
        raise GitGuardError("commit_non_amendにはexpected_index_treeが必要です。", code="stale_evidence")
    value = _string(raw, "expected_index_tree")
    if TREE_OID_RE.fullmatch(value) is None:
        raise GitGuardError("expected_index_treeはhelper発行のGit tree OIDにしてください。", code="invalid_snapshot")
    return value


def _ensure_no_unrelated_staged(repo: Path, paths: Sequence[str]) -> set[str]:
    staged = _staged_paths(repo)
    if any(not _covered(path, paths) for path in staged):
        outside = sorted(path for path in staged if not _covered(path, paths))
        raise GitGuardError(f"unrelated staged pathがあります: {outside[0]}", code="scope_expansion")
    return staged


def _outside_staged(staged: set[str], paths: Sequence[str]) -> set[str]:
    return {path for path in staged if not _covered(path, paths)}


def _staged_index_fingerprint(repo: Path, staged: set[str], paths: Sequence[str]) -> bytes:
    outside = sorted(_outside_staged(staged, paths), key=lambda item: item.encode("utf-8"))
    if not outside:
        return b""
    return _git(repo, ["ls-files", "-s", "-z", "--", *outside])


def _ensure_outside_staged_unchanged(
    repo: Path,
    before: set[str],
    after: set[str],
    paths: Sequence[str],
    before_index: bytes,
) -> None:
    before_outside = _outside_staged(before, paths)
    after_outside = _outside_staged(after, paths)
    if before_outside != after_outside:
        raise GitGuardError("operationが対象外のstaged pathを変更しました。", code="postcondition_failed")
    if before_index != _staged_index_fingerprint(repo, after, paths):
        raise GitGuardError("operationが対象外のindex内容を変更しました。", code="postcondition_failed")


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
    try:
        worktree_git, common_git = snapshot_digest.resolve_git_directories(repo)
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError(f"Git metadataを安全に確認できません: {exc}", code="precondition_failed") from exc
    directories: list[Path] = []
    for directory in (common_git, worktree_git):
        if directory not in directories:
            directories.append(directory)
    marker_names = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge", "sequencer")
    for directory in directories:
        for name in marker_names:
            marker = directory / name
            try:
                mode = marker.lstat().st_mode
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise GitGuardError("Git operation state markerを安全に確認できません。", code="precondition_failed") from exc
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise GitGuardError("Git operation state markerが安全な通常file/directoryではありません。", code="precondition_failed")
            raise GitGuardError("merge/rebase/cherry-pick state中はGit writeできません。", code="precondition_failed")


def _branch_ref(repo: Path) -> str:
    branch_ref = _git_text(repo, ["symbolic-ref", "--quiet", "HEAD"], check=False)
    if (
        not branch_ref.startswith("refs/heads/")
        or "\0" in branch_ref
        or "\r" in branch_ref
        or "\n" in branch_ref
        or ".." in branch_ref
        or "//" in branch_ref
        or branch_ref.endswith((".", "/"))
        or "/." in branch_ref
    ):
        raise GitGuardError("current branch refを安全に確認できません。", code="precondition_failed")
    return branch_ref


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
        resolved_base = snapshot_digest.resolve_commit(root, base_ref, label="base_ref")
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
    before = _staged_paths(root)
    before_index = _staged_index_fingerprint(root, before, paths)
    before_content = _worktree_fingerprints(root, paths)
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
    after = _staged_paths(root)
    _ensure_outside_staged_unchanged(root, before, after, paths, before_index)
    after_content = _worktree_fingerprints(root, paths)
    if before_content != after_content:
        raise GitGuardError("stageがworktree contentを変更しました。", code="postcondition_failed")
    return {"staged_paths": sorted(after, key=lambda item: item.encode("utf-8"))}


def _actual_untracked_paths(root: Path, paths: Sequence[str]) -> list[str]:
    try:
        raw = snapshot_digest.run_git(root, ["ls-files", "--others", "--exclude-standard", "-z", "--", *paths])
        return snapshot_digest.nul_paths(raw, label="actual untracked path")
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError("actual untracked pathを安全に確認できません。", code="postcondition_failed") from exc


def _path_fingerprint(root: Path, rel: str) -> tuple[object, ...]:
    """Safely fingerprint one exact regular worktree path without exposing data."""

    current = root
    parts = PurePosixPath(rel).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return ("missing",)
        except OSError as exc:
            raise GitGuardError("exact pathを安全にfingerprintできません。", code="unsafe_path") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise GitGuardError(f"scope pathにsymlink componentがあります: {rel}", code="unsafe_path")
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise GitGuardError(f"scope pathのancestorはdirectoryにしてください: {rel}", code="unsafe_path")
        if index == len(parts) - 1:
            if not stat.S_ISREG(metadata.st_mode):
                raise GitGuardError(f"exact pathは通常fileにしてください: {rel}", code="scope_mismatch")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(current, flags)
            except OSError as exc:
                raise GitGuardError("exact pathを安全に開けません。", code="unsafe_path") from exc
            try:
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode):
                        raise GitGuardError(f"exact pathは通常fileにしてください: {rel}", code="scope_mismatch")
                    hasher = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        hasher.update(chunk)
                    closed = os.fstat(stream.fileno())
            except GitGuardError:
                raise
            except OSError as exc:
                raise GitGuardError("exact pathを安全に読み取れません。", code="unsafe_path") from exc
            if (
                opened.st_ino != closed.st_ino
                or opened.st_dev != closed.st_dev
                or opened.st_size != closed.st_size
                or opened.st_mtime_ns != closed.st_mtime_ns
                or opened.st_mode != closed.st_mode
            ):
                raise GitGuardError("exact pathがfingerprint中に変化しました。", code="precondition_failed")
            return ("file", stat.S_IMODE(opened.st_mode), size, hasher.hexdigest())
    return ("missing",)


def _worktree_fingerprints(root: Path, paths: Sequence[str]) -> dict[str, tuple[object, ...]]:
    return {path: _path_fingerprint(root, path) for path in paths}


def _run_unstage(root: Path, paths: Sequence[str]) -> dict[str, Any]:
    _assert_branch_clean_state(root, require_branch=True)
    before_paths = _staged_paths(root)
    before_index = _staged_index_fingerprint(root, before_paths, paths)
    before_content = _worktree_fingerprints(root, paths)
    for path in paths:
        _ensure_no_symlink_path(root, path, reject_directory=True)
    _git(root, ["restore", "--staged", "--", *paths])
    after_paths = _staged_paths(root)
    _ensure_outside_staged_unchanged(root, before_paths, after_paths, paths, before_index)
    after_content = _worktree_fingerprints(root, paths)
    if before_content != after_content:
        raise GitGuardError("unstageがworktree contentを変更しました。", code="postcondition_failed")
    if any(path in after_paths for path in paths):
        raise GitGuardError("exact path unstage後も対象pathがstagedです。", code="postcondition_failed")
    return {"staged_paths_before": sorted(before_paths, key=lambda item: item.encode("utf-8")), "staged_paths_after": sorted(after_paths, key=lambda item: item.encode("utf-8"))}


def _commit_message(contract: Mapping[str, Any], scope: Mapping[str, Any]) -> str:
    raw = scope.get("message", contract.get("message"))
    return _string(raw, "commit message", allow_newlines=True)


def _run_commit(
    root: Path,
    contract: Mapping[str, Any],
    paths: Sequence[str],
    scope: Mapping[str, Any],
    *,
    expected_head: str,
    branch_ref: str,
) -> dict[str, Any]:
    _assert_branch_clean_state(root, require_branch=True)
    if _branch_ref(root) != branch_ref:
        raise GitGuardError("commit中にcurrent branch refが変化しました。", code="stale_evidence")
    staged = _ensure_no_unrelated_staged(root, paths)
    if not staged:
        raise GitGuardError("commit対象のstaged pathがありません。", code="precondition_failed")
    if any(not _covered(path, paths) for path in staged):
        raise GitGuardError("staged pathがexact commit scopeの外です。", code="scope_expansion")
    expected_tree = _expected_index_tree(contract)
    actual_tree = _index_tree(root)
    if actual_tree != expected_tree:
        raise GitGuardError("review済みexpected_index_treeとactual index treeが一致しません。", code="stale_evidence")
    committed_paths = _tree_changed_paths(root, expected_head, expected_tree)
    if not committed_paths:
        raise GitGuardError("expected_index_treeにcommit対象の変更がありません。", code="precondition_failed")
    if any(not _covered(path, paths) for path in committed_paths):
        outside = sorted(path for path in committed_paths if not _covered(path, paths))
        raise GitGuardError(f"expected_index_treeにscope外の変更があります: {outside[0]}", code="scope_expansion")
    for path in committed_paths:
        _ensure_no_symlink_path(root, path)
    message = _commit_message(contract, scope)
    try:
        identity_name, identity_email = snapshot_digest.resolve_git_identity(root)
    except (snapshot_digest.SnapshotError, OSError, UnicodeError) as exc:
        raise GitGuardError("Git commit identityを安全に解決できません。", code="precondition_failed") from exc
    if _git_text(root, ["rev-parse", "HEAD"]) != expected_head:
        raise GitGuardError("subject snapshot後にHEADが変化しました。", code="stale_evidence")

    # `git commit` would reread the mutable index after the validation above.
    # Build the commit from the already verified tree instead, then update the
    # symbolic branch ref with the expected old HEAD.  This closes the index
    # replacement window without introducing a process-wide lock or daemon.
    commit = _git_text(
        root,
        [
            "-c",
            f"user.name={identity_name}",
            "-c",
            f"user.email={identity_email}",
            "commit-tree",
            "--no-gpg-sign",
            expected_tree,
            "-p",
            expected_head,
            "-m",
            message,
        ],
    )
    # A commit OID has the same SHA-1/SHA-256 width as a tree OID.
    if TREE_OID_RE.fullmatch(commit) is None:
        raise GitGuardError("commit-treeが安全なcommit OIDを返しませんでした。", code="git_failed")
    _git(root, ["update-ref", "--no-deref", branch_ref, commit, expected_head])
    after_head = _git_text(root, ["rev-parse", "HEAD"])
    if after_head != commit or after_head == expected_head:
        raise GitGuardError("commit後のHEADが期待したcommitと一致しません。", code="postcondition_failed")
    after_tree = _git_text(root, ["rev-parse", "--verify", "--end-of-options", f"{after_head}^{{tree}}"])
    if after_tree != expected_tree:
        raise GitGuardError("commit後のtreeがexpected_index_treeと一致しません。", code="postcondition_failed")
    return {
        "commit": after_head,
        "expected_index_tree": expected_tree,
        "commit_tree": after_tree,
        "committed_paths": sorted(committed_paths, key=lambda item: item.encode("utf-8")),
    }


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
    commit_branch_ref = _branch_ref(root) if selected == "commit_non_amend" else None
    pre_snapshot = _preflight_snapshot(root, subject_snapshot)
    if selected == "commit_non_amend":
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
        assert commit_branch_ref is not None
        evidence = _run_commit(
            root,
            contract_map,
            paths,
            scope,
            expected_head=str(subject_snapshot["revision_id"]),
            branch_ref=commit_branch_ref,
        )
    else:  # protected by _validate_contract; defensive for future changes
        raise GitGuardError(f"unsupported operation: {selected}", code="unsupported")
    post_snapshot = _post_snapshot(root, subject_snapshot)
    expected_post = _extract(contract_map, "postwrite_snapshot", "postconditions")
    if expected_post is not None:
        try:
            expected_shape = snapshot_digest.snapshot_shape(expected_post, "postwrite_snapshot")
        except snapshot_digest.SnapshotError as exc:
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
    index_parser = sub.add_parser("index-tree", help="現在のindexから機械的に発行したGitツリーOIDを返します")
    index_parser.add_argument("--repo", required=True, metavar="REPO")
    apply_parser = sub.add_parser("apply", help="許可一覧にあるGit操作を一つ実行します")
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
    if args.command == "index-tree":
        try:
            root = snapshot_digest.canonical_target_root(args.repo)
            tree = index_tree(root)
            result = {
                "ok": True,
                "operation": "index_tree",
                "target_repo_root": str(root),
                "expected_index_tree": tree,
            }
        except (GitGuardError, OSError, UnicodeError, snapshot_digest.SnapshotError) as exc:
            if not isinstance(exc, GitGuardError):
                exc = GitGuardError(f"index treeを安全に取得できません: {exc}", code="invalid_input")
            print(json.dumps({"ok": False, "error": exc.code, "message": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 2 if exc.code == "unsupported" else 1
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
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
    except (GitGuardError, OSError, UnicodeError, snapshot_digest.SnapshotError) as exc:
        if not isinstance(exc, GitGuardError):
            exc = GitGuardError(f"Git contract/patchを安全に読み込めません: {exc}", code="invalid_input")
        print(json.dumps({"ok": False, "error": exc.code, "message": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2 if exc.code == "unsupported" else 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
