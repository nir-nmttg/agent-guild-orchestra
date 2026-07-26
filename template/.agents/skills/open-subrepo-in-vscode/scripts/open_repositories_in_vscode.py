#!/usr/bin/env python3
"""一つだけに限定した VS Code 新規ウィンドウ起動要求を準備または発行する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
from typing import Callable, Sequence


DEFAULT_MACOS_BUNDLED_CODE_PATHS = (
    Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
    Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
)
PLAN_ID_VERSION = "open-subrepo-in-vscode-plan-v1"


class TargetValidationError(ValueError):
    """与えられた root が唯一許可されるフォルダを指していない。"""


def _real_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TargetValidationError(f"{label}_missing") from exc
    if not resolved.is_dir():
        raise TargetValidationError(f"{label}_not_directory")
    return resolved


def validate_roots(guild_root: str | Path, repositories_root: str | Path) -> tuple[Path, Path]:
    """与えられた target が正確に許可される時だけ canonical root を返す。"""
    supplied_guild = Path(guild_root)
    supplied_repositories = Path(repositories_root)
    if not supplied_guild.is_absolute():
        raise TargetValidationError("guild_root_must_be_absolute")
    if not supplied_repositories.is_absolute():
        raise TargetValidationError("repositories_root_must_be_absolute")
    guild = _real_directory(supplied_guild, "guild_root")
    expected = guild / "repositories"
    if expected.is_symlink():
        raise TargetValidationError("repositories_root_symlink")
    repositories = _real_directory(expected, "repositories_root")
    supplied = supplied_repositories
    if supplied.is_symlink():
        raise TargetValidationError("repositories_root_symlink")
    supplied_real = _real_directory(supplied, "repositories_root")
    if supplied_real != repositories:
        raise TargetValidationError("repositories_root_must_equal_guild_root_repositories")
    return guild, repositories


def _verified_executable(candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        return None
    return resolved


def _launcher_identity(launcher: Path) -> dict[str, int | str]:
    """承認対象に含める launcher の canonical path と file identity を返す。"""
    resolved = _verified_executable(launcher)
    if resolved is None:
        raise TargetValidationError("launcher_unavailable")
    try:
        details = resolved.stat()
    except OSError as exc:
        raise TargetValidationError("launcher_unavailable") from exc
    return {
        "path": str(resolved),
        "device": details.st_dev,
        "inode": details.st_ino,
        "size": details.st_size,
        "mtime_ns": details.st_mtime_ns,
    }


def _plan_id(guild: Path, repositories: Path, launcher_identity: dict[str, int | str], argv: list[str]) -> str:
    payload = {
        "version": PLAN_ID_VERSION,
        "guild_root": str(guild),
        "repositories_root": str(repositories),
        "launcher": launcher_identity,
        "argv": argv,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def select_launcher(
    *,
    which: Callable[[str], str | None] = shutil.which,
    system: str | None = None,
    bundled_paths: Sequence[Path] = DEFAULT_MACOS_BUNDLED_CODE_PATHS,
) -> Path | None:
    """起動やshellを使わず、固定bundleと同一実体のVS Code CLIだけを選ぶ。"""
    if (system or platform.system()) != "Darwin":
        return None
    bundled_launchers = [verified for candidate in bundled_paths if (verified := _verified_executable(candidate)) is not None]
    path_launcher = which("code")
    if path_launcher:
        supplied = Path(path_launcher)
        verified = _verified_executable(supplied) if supplied.is_absolute() else None
        if verified is not None and verified in bundled_launchers:
            return verified
    return bundled_launchers[0] if bundled_launchers else None


def plan_launch(guild_root: str | Path, repositories_root: str | Path, *, launcher: Path | None = None) -> dict[str, object]:
    """target を検証して起動 argv を組み立てるが、決して実行しない。"""
    guild, repositories = validate_roots(guild_root, repositories_root)
    selected = launcher if launcher is not None else select_launcher()
    if selected is None:
        return {
            "status": "launcher_unavailable",
            "launch_state": "not_requested",
            "visual_confirmation": "unknown",
            "guild_root": str(guild),
            "repositories_root": str(repositories),
            "launcher": None,
            "launcher_identity": None,
            "argv": None,
            "exit_code": None,
            "plan_id": None,
        }
    verified = _verified_executable(selected)
    if verified is None:
        return {
            "status": "launcher_unavailable",
            "launch_state": "not_requested",
            "visual_confirmation": "unknown",
            "guild_root": str(guild),
            "repositories_root": str(repositories),
            "launcher": None,
            "launcher_identity": None,
            "argv": None,
            "exit_code": None,
            "plan_id": None,
        }
    argv = [str(verified), "-n", str(repositories)]
    launcher_identity = _launcher_identity(verified)
    return {
        "status": "approval_required",
        "launch_state": "not_requested",
        "visual_confirmation": "unknown",
        "guild_root": str(guild),
        "repositories_root": str(repositories),
        "launcher": str(verified),
        "launcher_identity": launcher_identity,
        "argv": argv,
        "exit_code": None,
        "plan_id": _plan_id(guild, repositories, launcher_identity, argv),
    }


def execute_launch(
    plan: dict[str, object],
    approved_plan_id: str | None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> dict[str, object]:
    """計画済みの argv を一度だけ実行する。exit zero は視覚的確認ではない。"""
    if plan.get("status") != "approval_required":
        return plan
    if not approved_plan_id:
        result = dict(plan)
        result.update(status="approved_plan_id_required", launch_state="not_requested", exit_code=None)
        return result
    if approved_plan_id != plan.get("plan_id"):
        result = dict(plan)
        result.update(status="approved_plan_mismatch", launch_state="not_requested", exit_code=None)
        return result
    argv = plan.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        result = dict(plan)
        result.update(status="invalid_plan", launch_state="not_requested", exit_code=None)
        return result
    try:
        completed = runner(argv, check=False)
    except OSError as exc:
        result = dict(plan)
        result.update(status="launch_failed", launch_state="failed", exit_code=None, error=type(exc).__name__)
        return result
    result = dict(plan)
    result["exit_code"] = completed.returncode
    if completed.returncode == 0:
        result.update(status="launch_request_accepted", launch_state="request_accepted")
    else:
        result.update(status="launch_failed", launch_state="failed")
    return result


def execute_approved_launch(
    guild_root: str | Path,
    repositories_root: str | Path,
    approved_plan_id: str | None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    launcher: Path | None = None,
) -> dict[str, object]:
    """実行直前に再計画し、承認済み identity と一致する時だけ起動する。"""
    try:
        current_plan = plan_launch(guild_root, repositories_root, launcher=launcher)
    except TargetValidationError as exc:
        return _failure(str(exc))
    return execute_launch(current_plan, approved_plan_id, runner=runner)


def _failure(error: str) -> dict[str, object]:
    return {
        "status": "invalid_target",
        "launch_state": "not_requested",
        "visual_confirmation": "unknown",
        "guild_root": None,
        "repositories_root": None,
        "launcher": None,
        "launcher_identity": None,
        "argv": None,
        "exit_code": None,
        "plan_id": None,
        "error": error,
    }


def _public_result_view(result: dict[str, object]) -> dict[str, object]:
    """通常の診断出力向けに、approval 専用のローカル値を伏せる。"""
    redacted = dict(result)
    for key in ("guild_root", "repositories_root", "launcher", "launcher_identity", "argv"):
        if key in redacted and redacted[key] is not None:
            redacted[key] = "<redacted>"
    return redacted


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="限定された一つの VS Code 起動要求を準備または発行します。")
    parser.add_argument("--guild-root", required=True, help="推測しない明示 guild root。")
    parser.add_argument("--repositories-root", required=True, help="明示 repositories root。<guild-root>/repositories と一致する必要があります。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="subprocess を使わない承認用 plan を検証して表示します（既定）。")
    mode.add_argument("--execute", action="store_true", help="承認済み caller が計画済み request を一度だけ発行します。")
    parser.add_argument("--approved-plan-id", help="Root が承認済み plan 出力から渡す identity。--execute では必須です。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = plan_launch(args.guild_root, args.repositories_root)
    except TargetValidationError as exc:
        result = _failure(str(exc))
    if args.execute and result.get("status") == "approval_required":
        result = execute_approved_launch(args.guild_root, args.repositories_root, args.approved_plan_id)
    # --plan の JSON だけが、人間承認のための構造化された完全な表示である。
    # --execute と失敗結果は通常の診断出力なので、ローカル path / argv を伏せる。
    output = result if args.plan else _public_result_view(result)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"approval_required", "launch_request_accepted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
