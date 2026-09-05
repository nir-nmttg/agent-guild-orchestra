#!/usr/bin/env python3
"""Git subject snapshot を canonical JSON と SHA-256 digest にする。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
from typing import Any


DIGEST_VERSION = "agent-guild-orchestra-snapshot-v1"
KINDS = {"revision_only", "working_tree_content", "commit_range"}
MAX_GIT_CONFIG_BYTES = 1_000_000
MAX_GIT_ATTRIBUTES_BYTES = 1_000_000
MAX_GIT_ATTRIBUTE_FILES = 1_024
MAX_UNTRACKED_BYTES = 64 * 1024 * 1024
_GIT_BINARY = shutil.which("git", path=os.defpath)
# macOS commonly exposes these two stable aliases.  They are resolved before
# the caller-controlled temporary/repository portion of a path; rejecting
# them would make ordinary temporary Git fixtures unusable on macOS.
SYSTEM_SYMLINK_ALIASES = {Path("/var"), Path("/tmp")}
SECRET_COMPONENTS = {
    ".env",
    ".envrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
PII_PATTERN = re.compile(r"(?:^|[-_.])(pii|personal[-_]?data|customer[-_]?export)(?:$|[-_.])", re.IGNORECASE)
PSEUDO_REFS = {
    "AUTO_MERGE",
    "BISECT_HEAD",
    "CHERRY_PICK_HEAD",
    "FETCH_HEAD",
    "MERGE_HEAD",
    "ORIG_HEAD",
    "REVERT_HEAD",
}


class SnapshotError(RuntimeError):
    """snapshot の入力または対象状態が安全に処理できない。"""


def _git_safe_environment() -> dict[str, str]:
    # Gitは環境変数でrepo、pathspec、config、trace、replace object等を広く上書きできる。
    # deny listでは将来追加分を取りこぼすため、全GIT_*を除去して必要な固定値だけを戻す。
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_GRAFT_FILE": os.devnull,
            "GIT_PAGER": "cat",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return environment


def _common_git_directory_for_config(repo: Path) -> Path:
    """Resolve the repository's common Git directory without invoking Git."""

    dot_git = repo / ".git"
    try:
        mode = dot_git.lstat().st_mode
    except OSError as exc:
        raise SnapshotError(f".gitを確認できません: {exc}") from exc
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        return dot_git
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        raise SnapshotError(".gitは実directoryまたは検証可能なlinked worktree pointerにしてください。")
    # The full pointer/backlink/common-dir relationship is checked without a
    # subprocess before reading config outside the worktree.
    return _linked_worktree_git_directory(repo, dot_git)[1]


def _assert_safe_local_git_config(repo: Path) -> str:
    """Reject repository configuration that can redirect or execute helpers."""

    config_path = _common_git_directory_for_config(repo) / "config"
    try:
        config_mode = config_path.lstat().st_mode
        if not stat.S_ISREG(config_mode) or stat.S_ISLNK(config_mode) or config_path.stat().st_size > MAX_GIT_CONFIG_BYTES:
            raise SnapshotError(f".git/config は{MAX_GIT_CONFIG_BYTES} bytes以下の通常fileにしてください。")
        config_text = config_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f".git/config を安全に検証できません: {exc}") from exc
    if re.search(r"(?im)^\s*\[\s*include(?:if)?\b", config_text):
        raise SnapshotError("repo-local Git config include はtarget root外を読めるため対応しません。")
    if re.search(r"(?im)^\s*\[\s*filter\b", config_text):
        raise SnapshotError(
            "repo-local Git content filter/process は外部commandを実行できるため対応しません。"
        )
    if re.search(r"(?im)^\s*worktree\s*=", config_text):
        raise SnapshotError("repo-local core.worktree override は対応しません。")
    if re.search(r"(?im)^\s*worktreeconfig\s*=\s*true\s*$", config_text):
        raise SnapshotError("repo-local extensions.worktreeConfig は対応しません。")
    return config_text


def _invoke_git(repo: Path, args: list[str], *, check: bool = True, input_data: bytes | None = None) -> bytes:
    if _GIT_BINARY is None:
        raise SnapshotError("Git executable が見つかりません。")
    try:
        result = subprocess.run(
            [
                _GIT_BINARY,
                "--no-pager",
                "--literal-pathspecs",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.sshCommand=",
                "-c",
                "credential.helper=",
                "-c",
                "diff.external=",
                "-c",
                "diff.orderFile=/dev/null",
                "-c",
                "core.attributesFile=/dev/null",
                "-c",
                "core.excludesFile=/dev/null",
                *args,
            ],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=_git_safe_environment(),
            input=input_data,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(f"git {' '.join(args)} timed out") from exc
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SnapshotError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout


def _attributes_select_filter(content: bytes, *, label: str) -> bool:
    if len(content) > MAX_GIT_ATTRIBUTES_BYTES:
        raise SnapshotError(f"{label} は{MAX_GIT_ATTRIBUTES_BYTES} bytes以下にしてください。")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SnapshotError(f"{label} をUTF-8として安全に読めません: {exc}") from exc
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped, comments=False, posix=True)
        except ValueError as exc:
            raise SnapshotError(f"{label} のattribute syntaxを安全に解釈できません: {exc}") from exc
        # Attribute syntax is whitespace separated after the path pattern. We
        # only need to identify the built-in `filter` attribute.
        for token in tokens[1:]:
            name = token.split("=", 1)[0].lstrip("-!")
            if name == "filter":
                return True
    return False


def _assert_no_worktree_filter_attributes(repo: Path) -> None:
    count = 0

    def fail_walk(exc: OSError) -> None:
        raise SnapshotError(f".gitattributes探索中にdirectoryを安全に読めません: {exc}") from exc

    for directory, dirnames, filenames in os.walk(repo, topdown=True, onerror=fail_walk, followlinks=False):
        directory_path = Path(directory)
        dirnames[:] = [
            name
            for name in dirnames
            if name != ".git" and not (directory_path / name).is_symlink()
        ]
        if ".gitattributes" not in filenames:
            continue
        count += 1
        if count > MAX_GIT_ATTRIBUTE_FILES:
            raise SnapshotError(".gitattributes fileが多すぎるため安全に検証できません。")
        path = directory_path / ".gitattributes"
        try:
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                raise SnapshotError(f"Git attributesは通常fileにしてください: {path.relative_to(repo)}")
            content = path.read_bytes()
        except OSError as exc:
            raise SnapshotError(f"Git attributesを安全に読めません: {path}: {exc}") from exc
        if _attributes_select_filter(content, label=str(path.relative_to(repo))):
            raise SnapshotError("Git attributesのcontent filter指定はhelperでは対応しません。")
    info_attributes = _common_git_directory_for_config(repo) / "info/attributes"
    try:
        mode = info_attributes.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SnapshotError(f".git/info/attributesを安全に確認できません: {exc}") from exc
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        raise SnapshotError(".git/info/attributesは通常fileにしてください。")
    try:
        content = info_attributes.read_bytes()
    except OSError as exc:
        raise SnapshotError(f".git/info/attributesを安全に読めません: {exc}") from exc
    if _attributes_select_filter(content, label=".git/info/attributes"):
        raise SnapshotError("Git attributesのcontent filter指定はhelperでは対応しません。")


def _assert_no_index_filter_attributes(repo: Path) -> None:
    raw = _invoke_git(repo, ["ls-files", "-s", "-z"])
    blob_ids: set[str] = set()
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        prefix, separator, path = entry.partition(b"\t")
        if not separator or not (path == b".gitattributes" or path.endswith(b"/.gitattributes")):
            continue
        fields = prefix.split()
        if len(fields) != 3:
            raise SnapshotError("index内の.gitattributes entryを安全に解釈できません。")
        mode, oid, _stage = fields
        if mode not in {b"100644", b"100755"}:
            raise SnapshotError("index内の.gitattributesは通常fileにしてください。")
        oid_text = oid.decode("ascii", errors="strict")
        if set(oid_text) == {"0"}:
            continue
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid_text) is None:
            raise SnapshotError("index内の.gitattributes blob idが不正です。")
        blob_ids.add(oid_text)
    if len(blob_ids) > MAX_GIT_ATTRIBUTE_FILES:
        raise SnapshotError("index内の.gitattributes blobが多すぎるため安全に検証できません。")
    for oid in blob_ids:
        size_text = _invoke_git(repo, ["cat-file", "-s", oid]).decode("ascii", errors="strict").strip()
        if not size_text.isdecimal() or int(size_text) > MAX_GIT_ATTRIBUTES_BYTES:
            raise SnapshotError(f"index内の.gitattributesは{MAX_GIT_ATTRIBUTES_BYTES} bytes以下にしてください。")
        content = _invoke_git(repo, ["cat-file", "blob", oid])
        if _attributes_select_filter(content, label="index内の.gitattributes"):
            raise SnapshotError("index内Git attributesのcontent filter指定はhelperでは対応しません。")


def _run(repo: Path, args: list[str], *, check: bool = True, input_data: bytes | None = None) -> bytes:
    # Git evaluates clean/process filters for commands including status, diff,
    # switch, and add. Check both working-tree and index attributes before any
    # such command so globally configured LFS/filter drivers cannot silently
    # transform content even though host configuration is disabled below.
    _assert_safe_local_git_config(repo)
    if args and args[0] in {"add", "apply", "commit", "diff", "restore", "status", "switch"}:
        _assert_no_worktree_filter_attributes(repo)
        _assert_no_index_filter_attributes(repo)
    return _invoke_git(repo, args, check=check, input_data=input_data)


def _assert_regular_if_present(path: Path, *, label: str, required: bool = False) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if required:
            raise SnapshotError(f"Git metadata がありません: {label}")
        return
    except OSError as exc:
        raise SnapshotError(f"Git metadata を安全に検証できません: {label}: {exc}") from exc
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        raise SnapshotError(f"Git metadata は通常fileにしてください: {label}")


def _assert_internal_git_tree(git_directory: Path) -> None:
    for entry in os.scandir(git_directory):
        if entry.is_symlink() or not (entry.is_file(follow_symlinks=False) or entry.is_dir(follow_symlinks=False)):
            raise SnapshotError(f".git直下にsymlink / special entryがあります: {entry.name}")
    for relative in (
        "commondir",
        "objects/info/alternates",
        "objects/info/http-alternates",
        "info/grafts",
        "refs/replace",
        "config.worktree",
    ):
        path = git_directory / relative
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SnapshotError(f"Git metadata indirectionを検証できません: .git/{relative}: {exc}") from exc
        raise SnapshotError(f"external Git object/config indirection は対応しません: .git/{relative}")
    for relative, required in (("HEAD", True), ("index", False), ("packed-refs", False), ("shallow", False)):
        _assert_regular_if_present(git_directory / relative, label=f".git/{relative}", required=required)
    entry_count = 0
    for relative, required in (
        ("objects", True),
        ("refs", True),
        ("info", False),
        ("logs", False),
        ("rebase-apply", False),
        ("rebase-merge", False),
        ("sequencer", False),
    ):
        root = git_directory / relative
        try:
            root_mode = root.lstat().st_mode
        except FileNotFoundError:
            if required:
                raise SnapshotError(f"Git metadata directory がありません: .git/{relative}")
            continue
        except OSError as exc:
            raise SnapshotError(f"Git metadata directory を検証できません: .git/{relative}: {exc}") from exc
        if not stat.S_ISDIR(root_mode) or stat.S_ISLNK(root_mode):
            raise SnapshotError(f"Git metadata directory はrepo内の実directoryにしてください: .git/{relative}")
        for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            directory_path = Path(directory)
            for name in dirnames:
                entry_count += 1
                mode = (directory_path / name).lstat().st_mode
                if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                    raise SnapshotError(f"Git metadata tree にsymlink / special directoryがあります: {directory_path / name}")
            for name in filenames:
                entry_count += 1
                mode = (directory_path / name).lstat().st_mode
                if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                    raise SnapshotError(f"Git metadata tree にsymlink / special fileがあります: {directory_path / name}")
            if entry_count > 1_000_000:
                raise SnapshotError("Git metadata tree が大きすぎるため安全に検証できません。")


def _read_git_pointer(path: Path, *, label: str) -> Path:
    _assert_regular_if_present(path, label=label, required=True)
    try:
        text = path.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"{label} を安全に読めません: {exc}") from exc
    if not text or "\n" in text or "\r" in text or "\0" in text:
        raise SnapshotError(f"{label} は単一pathにしてください。")
    return Path(text).expanduser().resolve()


def _linked_worktree_git_directory(repo: Path, dot_git: Path) -> tuple[Path, Path]:
    try:
        text = dot_git.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"linked worktree .git pointerを安全に読めません: {exc}") from exc
    match = re.fullmatch(r"gitdir:\s*(.+)", text)
    if match is None or "\n" in text or "\r" in text or "\0" in text:
        raise SnapshotError("linked worktree .gitは単一のgitdir pointerにしてください。")
    pointer = Path(match.group(1))
    git_directory = (dot_git.parent / pointer).resolve() if not pointer.is_absolute() else pointer.resolve()
    if not git_directory.is_dir() or git_directory.is_symlink():
        raise SnapshotError("linked worktree gitdirは実directoryにしてください。")

    commondir_pointer = git_directory / "commondir"
    _assert_regular_if_present(commondir_pointer, label="linked worktree commondir", required=True)
    try:
        commondir_text = commondir_pointer.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"linked worktree commondirを安全に読めません: {exc}") from exc
    if not commondir_text or "\n" in commondir_text or "\r" in commondir_text or "\0" in commondir_text:
        raise SnapshotError("linked worktree commondirは単一pathにしてください。")
    common_pointer = Path(commondir_text)
    common_directory = (git_directory / common_pointer).resolve() if not common_pointer.is_absolute() else common_pointer.resolve()
    if git_directory.parent.name != "worktrees" or git_directory.parent.parent != common_directory:
        raise SnapshotError("linked worktree gitdirはcommon Git dir直下のworktrees entryに限定します。")
    if not common_directory.is_dir() or common_directory.is_symlink() or common_directory.name != ".git":
        raise SnapshotError("linked worktree common dirは通常repositoryの.git directoryにしてください。")
    primary_worktree = common_directory.parent
    try:
        primary_mode = primary_worktree.lstat().st_mode
    except OSError as exc:
        raise SnapshotError(f"linked worktree primary rootを検証できません: {exc}") from exc
    if not stat.S_ISDIR(primary_mode) or stat.S_ISLNK(primary_mode):
        raise SnapshotError("linked worktreeのprimary worktreeは実directoryにしてください。")

    backlink = _read_git_pointer(git_directory / "gitdir", label="linked worktree gitdir backlink")
    if backlink != dot_git.resolve():
        raise SnapshotError("linked worktree gitdir backlinkがtarget .gitと一致しません。")
    for entry in os.scandir(git_directory):
        if entry.is_symlink() or not (entry.is_file(follow_symlinks=False) or entry.is_dir(follow_symlinks=False)):
            raise SnapshotError(f"linked worktree gitdirにsymlink / special entryがあります: {entry.name}")
    for relative, required in (("HEAD", True), ("index", False), ("commondir", True), ("gitdir", True)):
        _assert_regular_if_present(git_directory / relative, label=f"linked worktree/{relative}", required=required)
    _assert_regular_if_present(git_directory / "config.worktree", label="linked worktree/config.worktree", required=False)
    if (git_directory / "config.worktree").exists():
        raise SnapshotError("linked worktree固有configは対応しません。")
    return git_directory, common_directory


def _repo_root(value: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value or "\n" in value or "\r" in value:
        raise SnapshotError("repo は改行/NULを含まないabsolute pathにしてください。")
    if value.startswith("~"):
        raise SnapshotError("repo は明示的なabsolute pathにしてください。")
    raw = Path(value)
    if not raw.is_absolute():
        raise SnapshotError("repo は明示的なabsolute pathにしてください。")
    if ".." in raw.parts:
        raise SnapshotError("repo path にparent traversalがあるため処理できません。")
    # The target is an explicit canonical root.  Resolving a symlink and then
    # proceeding would make a caller supplied path ambiguous and could let a
    # later replacement redirect the operation to a sibling/outside tree.
    current = raw
    components: list[Path] = []
    while True:
        components.append(current)
        if current.parent == current:
            break
        current = current.parent
    for component in reversed(components):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SnapshotError(f"repo path を安全に確認できません: {component}: {exc}") from exc
        if stat.S_ISLNK(mode) and component not in SYSTEM_SYMLINK_ALIASES:
            raise SnapshotError(f"repo path にsymlink componentがあります: {component}")
    candidate = raw.resolve(strict=False)
    if not candidate.is_dir():
        raise SnapshotError(f"repo が directory ではありません: {candidate}")
    git_directory = candidate / ".git"
    try:
        git_mode = git_directory.lstat().st_mode
    except OSError as exc:
        raise SnapshotError(f".gitを確認できません: {exc}") from exc
    if stat.S_ISDIR(git_mode) and not stat.S_ISLNK(git_mode):
        common_directory = git_directory
    elif stat.S_ISREG(git_mode) and not stat.S_ISLNK(git_mode):
        git_directory, common_directory = _linked_worktree_git_directory(candidate, git_directory)
    else:
        raise SnapshotError(".gitは実directoryまたは検証可能なlinked worktree pointerにしてください。")
    _assert_internal_git_tree(common_directory)
    # `_run` repeats this immediately before every Git subprocess. Keeping a
    # check here also makes canonical-root validation fail without launching
    # Git when the repository uses an executable filter.
    _assert_safe_local_git_config(candidate)
    actual = Path(_run(candidate, ["rev-parse", "--show-toplevel"]).decode().strip()).resolve()
    if candidate != actual:
        raise SnapshotError(f"--repo は Git root と一致させてください: expected={candidate} actual={actual}")
    actual_git_dir = Path(_run(candidate, ["rev-parse", "--absolute-git-dir"]).decode().strip()).resolve()
    actual_common_dir = Path(_run(candidate, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).decode().strip()).resolve()
    if actual_git_dir != git_directory or actual_common_dir != common_directory:
        raise SnapshotError("事前検証したGit metadata pathとGitの解決結果が一致しません。")
    return actual


def _resolve_commit(repo: Path, value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("-") or "\0" in value or "\n" in value or "\r" in value:
        raise SnapshotError(f"{label} は単一のcommit refにしてください。")
    match = re.fullmatch(r"(.+?)(?:([~^][0-9]*)*)", value)
    base = match.group(1) if match is not None else ""
    ancestry = value[len(base):]
    oid = re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base) is not None
    head = base == "HEAD"
    named_ref = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", base) is not None
    invalid_named_ref = (
        base in PSEUDO_REFS
        or (base.isupper() and "_" in base)
        or ".." in base
        or "//" in base
        or base.endswith(".")
        or base.endswith("/")
        or "/." in base
    )
    if ancestry and re.fullmatch(r"(?:[~^][0-9]*)+", ancestry) is None:
        raise SnapshotError(f"{label} のancestry表現が不正です。")
    if not (oid or head or (named_ref and not invalid_named_ref)):
        raise SnapshotError(f"{label} はOID、HEAD、または通常のheads/tags/remotes refに限定してください。")
    raw = _run(repo, ["rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}"])
    resolved = raw.decode("ascii", errors="strict").strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", resolved) is None:
        raise SnapshotError(f"{label} を単一のcommit OIDへ解決できません。")
    return resolved


def _safe_relative(value: str, *, label: str) -> str:
    if not isinstance(value, str) or "\0" in value or "\n" in value or "\r" in value or "\\" in value:
        raise SnapshotError(f"{label} は単一の repo-relative path にしてください。")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
        or value.startswith("~")
        or re.match(r"^[A-Za-z]:[\\/]", value)
        or any(char in value for char in "*?[]{}")
        or value.startswith(":")
    ):
        raise SnapshotError(f"{label} が安全な repo-relative path ではありません: {value}")
    normalized = path.as_posix()
    if normalized != value:
        raise SnapshotError(f"{label} はcanonical repo-relative pathにしてください: {value}")
    for part in path.parts:
        folded = part.casefold()
        stem = PurePosixPath(part).stem.casefold()
        if (
            folded in SECRET_COMPONENTS
            or stem in SECRET_COMPONENTS
            or folded.startswith(".env.")
            or PurePosixPath(part).suffix.casefold() in SECRET_SUFFIXES
            or PII_PATTERN.search(folded)
        ):
            raise SnapshotError(f"{label} は secret-like / PII-like path のため読み取りません: {normalized}")
    return normalized


def _nul_paths(raw: bytes, *, label: str) -> list[str]:
    values: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        values.append(_safe_relative(item.decode("utf-8", errors="strict"), label=label))
    return values


def _path_is_within_scope(path: str, scopes: list[str]) -> bool:
    path_parts = PurePosixPath(path).parts
    return any(path_parts[: len(PurePosixPath(scope).parts)] == PurePosixPath(scope).parts for scope in scopes)


def _assert_no_symlink(repo: Path, paths: list[str]) -> None:
    for rel in paths:
        current = repo
        for part in PurePosixPath(rel).parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                break
            if stat.S_ISLNK(mode):
                raise SnapshotError(f"symlink path または ancestor は snapshot 対象にできません: {rel}")


def _read_untracked_beneath(repo: Path, rel: str) -> tuple[bytes, int]:
    parts = PurePosixPath(rel).parts
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(repo, directory_flags)
    try:
        for part in parts[:-1]:
            try:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            except OSError as exc:
                raise SnapshotError(f"untracked path の ancestor を安全に辿れません: {rel}: {exc}") from exc
            os.close(directory_fd)
            directory_fd = next_fd
            try:
                os.stat(".git", dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SnapshotError(f"nested repository 配下は snapshot 対象にできません: {rel}")
        try:
            descriptor = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        except OSError as exc:
            raise SnapshotError(f"untracked path を安全に開けません: {rel}: {exc}") from exc
        with os.fdopen(descriptor, "rb") as stream:
            opened_mode = os.fstat(stream.fileno()).st_mode
            if not stat.S_ISREG(opened_mode):
                raise SnapshotError(f"untracked path は通常 file にしてください: {rel}")
            return stream.read(), opened_mode
    finally:
        os.close(directory_fd)


def _assert_no_tracked_special_path(
    repo: Path,
    paths: list[str],
    *,
    kind: str,
    base_ref: str | None,
    head_ref: str | None,
) -> None:
    if not paths:
        return
    commands: list[list[str]]
    if kind == "commit_range":
        assert base_ref is not None and head_ref is not None
        commands = [["ls-tree", "-r", "-z", ref, "--", *paths] for ref in (base_ref, head_ref)]
    elif kind == "working_tree_content":
        assert base_ref is not None
        commands = [
            ["ls-tree", "-r", "-z", base_ref, "--", *paths],
            ["ls-files", "-s", "-z", "--", *paths],
        ]
    else:
        commands = [["ls-files", "-s", "-z", "--", *paths]]
    for command in commands:
        raw = _run(repo, command)
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            prefix, _, path_bytes = entry.partition(b"\t")
            if prefix.startswith(b"120000 "):
                rel = path_bytes.decode("utf-8", errors="replace")
                raise SnapshotError(f"tracked symlink は snapshot 対象にできません: {rel}")
            if prefix.startswith(b"160000 "):
                rel = path_bytes.decode("utf-8", errors="replace")
                raise SnapshotError(f"submodule / nested repository は snapshot 対象にできません: {rel}")


def _changed_paths(repo: Path, *, kind: str, base_ref: str | None, head_ref: str | None, scopes: list[str]) -> list[str]:
    args = ["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv"]
    if kind == "working_tree_content":
        args.append(base_ref or "HEAD")
    elif kind == "commit_range":
        if not base_ref or not head_ref:
            raise SnapshotError("commit_range には --base-ref と --head-ref が必要です。")
        args.extend([base_ref, head_ref])
    if scopes:
        args.extend(["--", *scopes])
    return _nul_paths(_run(repo, args), label="changed path")


def _patch_bytes(repo: Path, *, kind: str, base_ref: str | None, head_ref: str | None, scopes: list[str]) -> bytes:
    args = ["diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv"]
    if kind == "working_tree_content":
        args.append(base_ref or "HEAD")
    elif kind == "commit_range":
        assert base_ref is not None and head_ref is not None
        args.extend([base_ref, head_ref])
    if scopes:
        args.extend(["--", *scopes])
    return _run(repo, args)


def _record(hasher: Any, label: str, payload: bytes) -> None:
    label_bytes = label.encode("utf-8")
    hasher.update(len(label_bytes).to_bytes(8, "big"))
    hasher.update(label_bytes)
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def _untracked_records(repo: Path, paths: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for rel in sorted(paths, key=lambda value: value.encode("utf-8")):
        safe = _safe_relative(rel, label="untracked path")
        tracked = _run(repo, ["ls-files", "-z", "--", safe])
        if tracked:
            raise SnapshotError(f"--untracked に tracked path を指定しないでください: {safe}")
        content, opened_mode = _read_untracked_beneath(repo, safe)
        if len(content) > MAX_UNTRACKED_BYTES:
            raise SnapshotError(
                f"untracked path が大きすぎます ({MAX_UNTRACKED_BYTES} bytes超過): {safe}"
            )
        content_hash = hashlib.sha256(content).hexdigest()
        records.append(
            {
                "path": safe,
                "mode": stat.S_IMODE(opened_mode),
                "size": len(content),
                "sha256": content_hash,
            }
        )
    return records


def build_snapshot(args: argparse.Namespace) -> dict[str, object]:
    if args.kind not in KINDS:
        raise SnapshotError(f"kind は {sorted(KINDS)} のいずれかにしてください。")
    repo = _repo_root(args.repo)
    scopes = sorted({_safe_relative(value, label="scope path") for value in args.scope}, key=lambda value: value.encode("utf-8"))
    for index, scope in enumerate(scopes):
        scope_parts = PurePosixPath(scope).parts
        for other in scopes[index + 1 :]:
            other_parts = PurePosixPath(other).parts
            if scope_parts[: len(other_parts)] == other_parts or other_parts[: len(scope_parts)] == scope_parts:
                raise SnapshotError(f"scope path が重複または包含関係です: {scope}, {other}")
    untracked = sorted({_safe_relative(value, label="untracked path") for value in args.untracked}, key=lambda value: value.encode("utf-8"))
    if args.kind == "revision_only" and untracked:
        raise SnapshotError("revision_only は --untracked を受け付けません。")
    if args.kind == "commit_range" and untracked:
        raise SnapshotError("commit_range は --untracked を受け付けません。")
    if args.kind != "revision_only" and not scopes:
        raise SnapshotError("content digest には1件以上の --scope が必要です。")
    if any(not _path_is_within_scope(path, scopes) for path in untracked):
        raise SnapshotError("--untracked は明示した --scope 内だけにしてください。")
    if args.kind == "working_tree_content":
        actual_untracked = _nul_paths(
            _run(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--", *scopes]),
            label="actual untracked path",
        )
        if set(actual_untracked) != set(untracked):
            raise SnapshotError("subject scope 内の実 untracked path 集合をすべて --untracked で明示してください。")

    current_head = _resolve_commit(repo, "HEAD", label="HEAD")
    resolved_base_ref: str | None = None
    resolved_head_ref: str | None = None
    revision_id = current_head
    if args.kind == "working_tree_content":
        resolved_base_ref = _resolve_commit(repo, args.base_ref or "HEAD", label="base ref")
    elif args.kind == "commit_range":
        if not args.base_ref or not args.head_ref:
            raise SnapshotError("commit_range には --base-ref と --head-ref が必要です。")
        resolved_base_ref = _resolve_commit(repo, args.base_ref, label="base ref")
        resolved_head_ref = _resolve_commit(repo, args.head_ref, label="head ref")
        revision_id = resolved_head_ref
    status_args = ["status", "--porcelain=v1", "-z", "--untracked-files=normal"]
    if scopes:
        status_args.extend(["--", *scopes])
    status_raw = _run(repo, status_args)
    dirty_state = "dirty" if status_raw else "clean"
    snapshot: dict[str, object] = {
        "digest_version": DIGEST_VERSION,
        "kind": args.kind,
        "revision_id": revision_id,
        "base_ref": resolved_base_ref,
        "head_ref": resolved_head_ref,
        "scope_paths": scopes,
        "untracked_paths": untracked,
        "dirty_state": dirty_state,
        "diff_hash": None,
    }
    if args.kind == "revision_only":
        if dirty_state != "clean":
            raise SnapshotError("revision_only の subject scope は clean にしてください。dirty 内容は working_tree_content を使います。")
        identity = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        snapshot["snapshot_id"] = "sha256:" + hashlib.sha256(identity).hexdigest()
        return snapshot

    changed = _changed_paths(
        repo,
        kind=args.kind,
        base_ref=resolved_base_ref,
        head_ref=resolved_head_ref,
        scopes=scopes,
    )
    for path in changed:
        _safe_relative(path, label="changed path")
    _assert_no_symlink(repo, changed)
    _assert_no_tracked_special_path(
        repo,
        changed,
        kind=args.kind,
        base_ref=resolved_base_ref,
        head_ref=resolved_head_ref,
    )
    records = _untracked_records(repo, untracked)
    patch = _patch_bytes(
        repo,
        kind=args.kind,
        base_ref=resolved_base_ref,
        head_ref=resolved_head_ref,
        scopes=scopes,
    )
    hasher = hashlib.sha256()
    _record(hasher, "digest_version", DIGEST_VERSION.encode())
    _record(hasher, "kind", args.kind.encode())
    _record(hasher, "revision_id", revision_id.encode())
    _record(hasher, "base_ref", (resolved_base_ref or "").encode())
    _record(hasher, "head_ref", (resolved_head_ref or "").encode())
    _record(hasher, "scope_paths", json.dumps(scopes, ensure_ascii=False, separators=(",", ":")).encode())
    _record(hasher, "tracked_patch", patch)
    _record(hasher, "untracked_records", json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    digest = "sha256:" + hasher.hexdigest()
    snapshot["diff_hash"] = digest
    snapshot["snapshot_id"] = digest
    return snapshot


def compute_snapshot(
    repo: str | Path,
    *,
    kind: str,
    base_ref: str | None = None,
    head_ref: str | None = None,
    scope_paths: list[str] | tuple[str, ...] = (),
    untracked_paths: list[str] | tuple[str, ...] = (),
) -> dict[str, object]:
    """Return a helper-issued snapshot for callers inside the runtime.

    This is intentionally a small wrapper around the same implementation used
    by the CLI.  Consumers must compare the complete returned mapping; a
    caller supplied digest or ``snapshot_id`` is never treated as evidence.
    """

    args = argparse.Namespace(
        repo=str(repo),
        kind=kind,
        base_ref=base_ref,
        head_ref=head_ref,
        scope=list(scope_paths),
        untracked=list(untracked_paths),
    )
    return build_snapshot(args)


def canonical_repo_root(repo: str | Path) -> Path:
    """Validate and return an explicit canonical Git worktree root."""

    return _repo_root(str(repo))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--kind", choices=sorted(KINDS), required=True)
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--untracked", action="append", default=[])
    return parser


def main() -> int:
    args = _parser().parse_args()
    snapshot = build_snapshot(args)
    print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SnapshotError, UnicodeError, OSError) as exc:
        print(f"snapshot-digest: error: {exc}", file=sys.stderr)
        raise SystemExit(1)
