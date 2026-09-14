#!/usr/bin/env python3
"""Prepare or issue one exact VS Code new-window launch request."""

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
PLAN_ID_VERSION = "open-directory-in-vscode-plan-v1"


class TargetValidationError(ValueError):
    """The supplied directory or launcher is not an allowed target."""


def _real_directory(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise TargetValidationError(f"{label}_must_be_absolute")
    try:
        supplied_mode = path.lstat().st_mode
    except OSError as exc:
        raise TargetValidationError(f"{label}_missing") from exc
    if stat.S_ISLNK(supplied_mode):
        raise TargetValidationError(f"{label}_symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TargetValidationError(f"{label}_missing") from exc
    if not resolved.is_dir():
        raise TargetValidationError(f"{label}_not_directory")
    return resolved


def validate_directory(directory: str | Path) -> Path:
    """Return the canonical explicit directory, rejecting symlink targets."""
    return _real_directory(Path(directory), "directory")


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


def _plan_id(directory: Path, launcher_identity: dict[str, int | str], argv: list[str]) -> str:
    payload = {
        "version": PLAN_ID_VERSION,
        "directory": str(directory),
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
    """Select only a verified VS Code CLI matching a fixed macOS bundle."""
    if (system or platform.system()) != "Darwin":
        return None
    bundled = [
        verified
        for candidate in bundled_paths
        if (verified := _verified_executable(candidate)) is not None
    ]
    path_launcher = which("code")
    if path_launcher:
        supplied = Path(path_launcher)
        verified = _verified_executable(supplied) if supplied.is_absolute() else None
        if verified is not None and verified in bundled:
            return verified
    return bundled[0] if bundled else None


def _launcher_unavailable(directory: Path) -> dict[str, object]:
    return {
        "status": "launcher_unavailable",
        "launch_state": "not_requested",
        "visual_confirmation": "unknown",
        "directory": str(directory),
        "launcher": None,
        "launcher_identity": None,
        "argv": None,
        "exit_code": None,
        "plan_id": None,
    }


def plan_launch(
    directory: str | Path,
    *,
    launcher: Path | None = None,
    system: str | None = None,
) -> dict[str, object]:
    """Validate a directory and build an approval plan without executing it."""
    target = validate_directory(directory)
    selected = launcher if launcher is not None else select_launcher(system=system)
    if selected is None:
        return _launcher_unavailable(target)
    verified = _verified_executable(selected)
    if verified is None:
        return _launcher_unavailable(target)
    argv = [str(verified), "-n", str(target)]
    launcher_identity = _launcher_identity(verified)
    return {
        "status": "approval_required",
        "launch_state": "not_requested",
        "visual_confirmation": "unknown",
        "directory": str(target),
        "launcher": str(verified),
        "launcher_identity": launcher_identity,
        "argv": argv,
        "exit_code": None,
        "plan_id": _plan_id(target, launcher_identity, argv),
    }


def execute_launch(
    plan: dict[str, object],
    approved_plan_id: str | None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> dict[str, object]:
    """Execute a plan once after exact approval identity comparison."""
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
    directory: str | Path,
    approved_plan_id: str | None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    launcher: Path | None = None,
    system: str | None = None,
) -> dict[str, object]:
    """Re-plan immediately before launch and compare the approved identity."""
    try:
        current_plan = plan_launch(directory, launcher=launcher, system=system)
    except TargetValidationError as exc:
        return {
            "status": "invalid_target",
            "launch_state": "not_requested",
            "visual_confirmation": "unknown",
            "directory": None,
            "launcher": None,
            "launcher_identity": None,
            "argv": None,
            "exit_code": None,
            "plan_id": None,
            "error": str(exc),
        }
    return execute_launch(current_plan, approved_plan_id, runner=runner)


def _public_result_view(result: dict[str, object]) -> dict[str, object]:
    """Redact approval-only local values from ordinary diagnostic output."""
    redacted = dict(result)
    for key in ("directory", "launcher", "launcher_identity", "argv"):
        if redacted.get(key) is not None:
            redacted[key] = "<redacted>"
    return redacted


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="明示された一つのディレクトリをVS Codeで開く要求を準備または発行します。")
    parser.add_argument("--directory", required=True, help="明示されたディレクトリの絶対パス。推測では指定しません。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="子プロセスを使わず、承認用の計画を表示します（既定）。")
    mode.add_argument("--execute", action="store_true", help="承認済みの計画を一度だけ実行要求として発行します。")
    parser.add_argument("--approved-plan-id", help="--executeで必須の、承認済みの計画の識別子。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = plan_launch(args.directory)
    except TargetValidationError as exc:
        result = {
            "status": "invalid_target",
            "launch_state": "not_requested",
            "visual_confirmation": "unknown",
            "directory": None,
            "launcher": None,
            "launcher_identity": None,
            "argv": None,
            "exit_code": None,
            "plan_id": None,
            "error": str(exc),
        }
    if args.execute and result.get("status") == "approval_required":
        result = execute_approved_launch(args.directory, args.approved_plan_id)
    output = result if args.plan else _public_result_view(result)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"approval_required", "launch_request_accepted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
