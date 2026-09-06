#!/usr/bin/env python3
"""Maintainer-only Codex parent activation smoke.

Normal installs do not include this script. Discovery is the default; model
turns require ``--live`` and are deliberately bounded.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "adventurer": {"model": "gpt-5.6-luna", "effort": "max", "sandbox": "workspace-write"},
    "inquisitor": {"model": "gpt-6-astra", "effort": "xhigh", "sandbox": "read-only"},
}


class ProbeError(RuntimeError):
    pass


class Rpc:
    """Minimal JSON-RPC-over-stdio client with bounded reads."""

    def __init__(self, codex: str, cwd: Path, home: Path) -> None:
        self.codex, self.cwd, self.home = codex, cwd, home
        self.proc: subprocess.Popen[bytes] | None = None
        self.sel: selectors.BaseSelector | None = None
        self.next_id = 0
        self.messages: list[dict[str, Any]] = []
        self.read_buffer = bytearray()
        self.turn_message_cursor = 0
        self.last_turn_error: dict[str, Any] | None = None

    def start(self) -> None:
        env = dict(os.environ, CODEX_HOME=str(self.home))
        self.proc = subprocess.Popen(
            [self.codex, "app-server", "--stdio"], cwd=self.cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        assert self.proc.stdout is not None
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout.fileno(), selectors.EVENT_READ)

    def _read(self, deadline: float) -> dict[str, Any] | None:
        if self.sel is None or self.proc is None or self.proc.stdout is None:
            raise ProbeError("app-server is not running")
        while time.monotonic() < deadline:
            newline = self.read_buffer.find(b"\n")
            if newline < 0:
                if not self.sel.select(max(0.05, deadline - time.monotonic())):
                    return None
                try:
                    chunk = os.read(self.proc.stdout.fileno(), 65536)
                except OSError:
                    return None
                if not chunk:
                    return None
                self.read_buffer.extend(chunk)
                continue
            raw = bytes(self.read_buffer[:newline])
            del self.read_buffer[: newline + 1]
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                self.messages.append(value)
                return value
        return None

    def call(self, method: str, params: dict[str, Any], timeout: float = 20) -> dict[str, Any]:
        if self.proc is None or self.proc.stdin is None:
            raise ProbeError("app-server is not running")
        self.next_id += 1
        request_id = self.next_id
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            message = self._read(deadline)
            if message is None:
                raise ProbeError(f"timeout waiting for {method}")
            if message.get("id") == request_id:
                return message

    def notify_initialized(self) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise ProbeError("app-server is not running")
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def wait_turn(self, thread_id: str, timeout: float) -> str:
        self.last_turn_error = None
        deadline = time.monotonic() + timeout
        while True:
            # ``call("turn/start")`` can read notifications while waiting for
            # its response.  Consume those already buffered messages before
            # blocking on the fd, while keeping them available to
            # ``collab_items`` for later evidence.
            pending = self.messages[self.turn_message_cursor:]
            self.turn_message_cursor = len(self.messages)
            for message in pending:
                params = message.get("params")
                if not isinstance(params, dict) or params.get("threadId") != thread_id:
                    continue
                if message.get("method") == "error" and params.get("willRetry") is True:
                    self.last_turn_error = sanitized_error(params.get("error"))
                    self.last_turn_error["willRetry"] = True
                    return "blocked"
                if message.get("method") == "turn/completed":
                    return "completed"
            message = self._read(deadline)
            if message is None:
                return "timeout"

    def close(self) -> None:
        if self.sel is not None:
            self.sel.close()
            self.sel = None
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)
        self.proc = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="保守担当者向けの、Codexの親設定・エージェント起動の実機確認。")
    parser.add_argument("--target-repo-root", default=str(REPO_ROOT), help="template/を含むGitリポジトリ。")
    parser.add_argument("--codex", default=os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex", help="Codex実行ファイルのパス（CODEX_BINでも指定可能）。")
    parser.add_argument("--output", help="結果JSONのパス（既定: OSの一時ディレクトリ/codex-parent-smoke-*.json）。")
    parser.add_argument("--live", action="store_true", help="時間制限付きで、名前付きエージェントを実際に呼び出す最大2ターンを要求。")
    parser.add_argument("--live-timeout", type=float, default=45.0, help="実呼び出しの1ターン当たりの制限秒数（既定: 45）。")
    parser.add_argument("--keep-fixture", action="store_true", help="検証用の親・子を残す。一時的な認証設定ディレクトリは削除する。")
    return parser.parse_args(argv)


def observed(evidence: Any) -> dict[str, Any]:
    return {"status": "observed", "evidence": evidence}


def unknown(reason: str, evidence: Any | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "unknown", "reason": reason}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def sanitized_error(value: Any) -> dict[str, Any]:
    """Keep only structured error identifiers; never emit provider text."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("code", "type"):
        item = value.get(key)
        if isinstance(item, (str, int)):
            result[key] = item
    info = value.get("codexErrorInfo")
    if isinstance(info, str):
        result["codexErrorInfo"] = info
    elif isinstance(info, dict):
        keys = sorted(key for key in info if isinstance(key, str))
        if keys:
            result["codexErrorInfoType"] = keys[0]
    return result


def failed(reason: str, evidence: Any | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "failed", "reason": reason}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def version_evidence(value: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    match = re.search(r"codex-cli\s+([0-9]+(?:\.[0-9]+)*)", value.stdout)
    return {"version": match.group(1) if match else None}


def result_of(response: dict[str, Any], method: str) -> dict[str, Any]:
    if "error" in response:
        error = response.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        raise ProbeError(f"{method} JSON-RPC error: {code or 'unknown'}")
    value = response.get("result")
    if not isinstance(value, dict):
        raise ProbeError(f"{method} returned no object")
    return value


def make_fixture(source: Path) -> tuple[Path, Path]:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import install  # type: ignore[import-not-found]

    parent = Path(tempfile.mkdtemp(prefix="guild-native-parent-")).resolve()
    try:
        install_args = install.parse_args(["--target", str(parent), "--source", str(source), "--allow-non-default-source"])
        with contextlib.redirect_stdout(io.StringIO()):
            install.execute(install_args)
        child = parent / "repositories" / "app"
        child.mkdir(parents=True)
        if subprocess.run(["git", "-C", str(child), "init", "-q"], check=False, timeout=10).returncode != 0:
            raise ProbeError("could not initialize child Git root")
        child_codex = child / ".codex"
        child_codex.mkdir()
        (child_codex / "config.toml").write_text('model = "child-collision-model"\n\n[agents]\nenabled = false\n', encoding="utf-8")
        skill = child / ".agents" / "skills" / "child-collision"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: child-collision\ndescription: 子だけに配置したSkillの競合確認用。\n---\n子リポジトリ専用の検証用Skillです。\n", encoding="utf-8")
        return parent, child
    except BaseException:
        shutil.rmtree(parent, ignore_errors=True)
        raise


def make_home(parent: Path, child: Path) -> Path:
    home = Path(tempfile.mkdtemp(prefix="guild-native-home-")).resolve()
    try:
        home.joinpath("config.toml").write_text(
            f"[projects.{json.dumps(str(parent))}]\ntrust_level = \"trusted\"\n"
            f"[projects.{json.dumps(str(child))}]\ntrust_level = \"trusted\"\n", encoding="utf-8"
        )
        auth = Path.home() / ".codex" / "auth.json"
        if auth.is_file():
            # Symlink only; credentials are never copied or emitted.
            home.joinpath("auth.json").symlink_to(auth)
        return home
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise


def config_view(value: dict[str, Any]) -> dict[str, Any]:
    config = value.get("config")
    if not isinstance(config, dict):
        raise ProbeError("config/read returned no config")
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    features = config.get("features") if isinstance(config.get("features"), dict) else {}
    return {"model": config.get("model"), "contextWindow": config.get("model_context_window"), "reasoningEffort": config.get("model_reasoning_effort"), "agentsEnabled": agents.get("enabled"), "maxThreads": agents.get("max_concurrent_threads_per_session"), "multiAgent": features.get("multi_agent")}


def project_skill_view(value: dict[str, Any], root: Path, forbidden_root: Path) -> dict[str, Any]:
    data = value.get("data")
    if not isinstance(data, list):
        raise ProbeError("skills/list returned no data")
    prefix = str(root / ".agents" / "skills") + "/"
    forbidden_prefix = str(forbidden_root / ".agents" / "skills") + "/"
    row = next((item for item in data if isinstance(item, dict) and item.get("cwd") == str(root)), None)
    if row is None:
        return {"names": [], "paths": [], "enabled": False, "errors": ["missing-cwd"], "leakedNames": []}
    names: list[str] = []
    paths: list[str] = []
    leaked: list[str] = []
    enabled = True
    for skill in row.get("skills", []):
        if not isinstance(skill, dict) or not isinstance(skill.get("path"), str):
            continue
        path = skill["path"]
        if path.startswith(prefix):
            paths.append(path)
            if isinstance(skill.get("name"), str):
                names.append(skill["name"])
            enabled = enabled and skill.get("enabled") is True
        elif path.startswith(forbidden_prefix) and isinstance(skill.get("name"), str):
            leaked.append(skill["name"])
    errors = row.get("errors") if isinstance(row.get("errors"), list) else ["invalid-errors"]
    return {
        "names": sorted(set(names)),
        "paths": sorted(paths),
        "enabled": enabled,
        "errors": errors,
        "leakedNames": sorted(set(leaked)),
    }


def named_defs(parent: Path) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name, expected in EXPECTED.items():
        path = parent / ".codex" / "agents" / f"{name}.toml"
        try:
            value = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            output[name] = {"status": "failed", "errorType": type(exc).__name__}
            continue
        actual = {"name": value.get("name"), "model": value.get("model"), "effort": value.get("model_reasoning_effort"), "sandbox": value.get("sandbox_mode"), "developerInstructions": isinstance(value.get("developer_instructions"), str)}
        want = {"name": name, "model": expected["model"], "effort": expected["effort"], "sandbox": expected["sandbox"], "developerInstructions": True}
        output[name] = {"status": "observed" if actual == want else "failed", "actual": actual}
    return output


def static_probe(rpc: Rpc, parent: Path, child: Path) -> tuple[dict[str, Any], str]:
    checks: dict[str, Any] = {}
    parent_cfg = config_view(result_of(rpc.call("config/read", {"cwd": str(parent), "includeLayers": True}), "config/read"))
    child_cfg = config_view(result_of(rpc.call("config/read", {"cwd": str(child), "includeLayers": True}), "config/read"))
    expected_parent = {"model": "gpt-6-astra", "contextWindow": 1_000_000, "reasoningEffort": None, "agentsEnabled": True, "maxThreads": 2, "multiAgent": True}
    checks["parent_config"] = observed(parent_cfg) if parent_cfg == expected_parent else failed("parent_config_mismatch", parent_cfg)
    checks["child_config_collision"] = observed(child_cfg) if child_cfg["model"] == "child-collision-model" and child_cfg["agentsEnabled"] is False else failed("child_config_mismatch", child_cfg)

    skills = result_of(rpc.call("skills/list", {"cwds": [str(parent), str(child)], "forceReload": True}), "skills/list")
    parent_skills = project_skill_view(skills, parent, child)
    child_skills = project_skill_view(skills, child, parent)
    expected_skills = sorted(path.name for path in (parent / ".agents" / "skills").iterdir() if path.is_dir())
    skill_evidence = {"parent": parent_skills, "child": child_skills}
    skills_ok = (
        parent_skills["names"] == expected_skills and child_skills["names"] == ["child-collision"]
        and parent_skills["enabled"] and child_skills["enabled"]
        and not parent_skills["errors"] and not child_skills["errors"]
        and not parent_skills["leakedNames"] and not child_skills["leakedNames"]
    )
    checks["project_skills_boundary"] = observed(skill_evidence) if skills_ok else failed("skills_boundary_mismatch", skill_evidence)

    defs = named_defs(parent)
    checks["named_agent_definitions"] = observed(defs) if all(item["status"] == "observed" for item in defs.values()) else failed("named_agent_definition_mismatch", defs)

    thread = result_of(rpc.call("thread/start", {"cwd": str(parent), "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never"}), "thread/start")
    thread_id = (thread.get("thread") or {}).get("id")
    if not isinstance(thread_id, str):
        raise ProbeError("thread/start returned no thread id")
    evidence = {key: thread.get(key) for key in ("model", "reasoningEffort", "cwd", "instructionSources", "approvalPolicy", "approvalsReviewer", "sandbox", "activePermissionProfile")}
    checks["parent_thread_and_permissions"] = observed(evidence) if (
        evidence["model"] == "gpt-6-astra" and evidence["reasoningEffort"] is None and evidence["cwd"] == str(parent)
        and str(parent / "AGENTS.md") in (evidence["instructionSources"] or []) and evidence["approvalPolicy"] == "never"
        and (evidence["sandbox"] or {}).get("type") == "readOnly"
    ) else failed("parent_thread_mismatch", evidence)
    return checks, thread_id


def collab_items(rpc: Rpc, thread_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in rpc.messages:
        if message.get("method") != "item/completed" or not isinstance(message.get("params"), dict):
            continue
        params = message["params"]
        item = params.get("item")
        if params.get("threadId") == thread_id and isinstance(item, dict) and item.get("type") == "collabAgentToolCall":
            states = item.get("agentsStates") if isinstance(item.get("agentsStates"), dict) else {}
            items.append({
                "tool": item.get("tool"),
                "model": item.get("model"),
                "reasoningEffort": item.get("reasoningEffort"),
                "receiverThreadIds": item.get("receiverThreadIds"),
                "agentStatuses": {
                    agent_id: state.get("status")
                    for agent_id, state in states.items()
                    if isinstance(agent_id, str) and isinstance(state, dict)
                },
                "status": item.get("status"),
            })
    return items


def live_probe(
    rpc: Rpc, parent_thread: str, parent: Path, child: Path, timeout: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    root_reads: list[dict[str, Any]] = []
    blocked: str | None = None
    for index, (effort, agent) in enumerate((("low", "adventurer"), ("xhigh", "inquisitor")), 1):
        prompt = (
            f"Codex標準のエージェント起動を確認します。名前付きエージェント`{agent}`を選び、標準の協調機能でちょうど1回起動してください。"
            f"そのエージェントに親={parent}と子Gitルート={child}を読み取り専用で調べさせてください。"
            "書き込み、Gitの変更、別のエージェントの起動は行わせません。標準ツールを実行し、完了を待ってください。"
        )
        response = rpc.call("turn/start", {"threadId": parent_thread, "input": [{"type": "text", "text": prompt}], "effort": effort})
        if "error" in response:
            return (
                failed("turn_start_error", {"turns": turns, "requestedEffort": effort, "requestedAgent": agent, "error": sanitized_error(response.get("error"))}),
                unknown("no child permission metadata was available", {"requested": True}),
            )
        turn = (response.get("result") or {}).get("turn")
        state = rpc.wait_turn(parent_thread, timeout)
        turn_evidence = {"index": index, "requestedEffort": effort, "requestedAgent": agent, "turnId": turn.get("id") if isinstance(turn, dict) else None, "status": "observed" if state == "completed" else "unknown", "reason": None if state == "completed" else state}
        if state == "blocked":
            turn_evidence["error"] = getattr(rpc, "last_turn_error", {})
        turns.append(turn_evidence)
        if state != "completed":
            blocked = state
            break
        try:
            root = result_of(
                rpc.call("thread/read", {"threadId": parent_thread, "includeTurns": False}, timeout=10),
                "thread/read",
            ).get("thread") or {}
        except ProbeError as exc:
            root_reads.append({"requestedEffort": effort, "status": "unknown", "errorType": type(exc).__name__})
        else:
            actual = {
                "model": root.get("model"),
                "reasoningEffort": root.get("reasoningEffort"),
                "cwd": root.get("cwd"),
            }
            root_reads.append({
                "requestedEffort": effort,
                "status": "observed" if actual["model"] == "gpt-6-astra" and actual["reasoningEffort"] == effort else "failed",
                "actual": actual,
            })

    events = collab_items(rpc, parent_thread)
    spawns = [event for event in events if event.get("tool") == "spawnAgent"]
    child_ids = list(dict.fromkeys(child_id for event in spawns for child_id in (event.get("receiverThreadIds") or []) if isinstance(child_id, str)))
    metadata: list[dict[str, Any]] = []
    for child_id in child_ids:
        try:
            value = result_of(rpc.call("thread/read", {"threadId": child_id, "includeTurns": False}, timeout=10), "thread/read")
            thread = value.get("thread") or {}
            metadata.append({
                key: thread.get(key)
                for key in (
                    "id", "parentThreadId", "agentNickname", "agentRole", "model",
                    "reasoningEffort", "cwd", "sandbox", "approvalPolicy",
                    "activePermissionProfile",
                )
            })
        except ProbeError:
            metadata.append({"id": child_id, "status": "unknown"})

    expected_events: dict[str, Any] = {}
    for agent, want in EXPECTED.items():
        matches = [event for event in spawns if event.get("model") == want["model"] and event.get("reasoningEffort") == want["effort"]]
        named = [item for item in metadata if item.get("parentThreadId") == parent_thread and (item.get("agentNickname") == agent or item.get("agentRole") == agent) and item.get("model") == want["model"] and item.get("reasoningEffort") == want["effort"]]
        status = "observed" if len(matches) == 1 and named else "unknown" if blocked else "failed"
        expected_events[agent] = {"status": status, "spawnEventCount": len(matches), "childMetadataMatches": len(named)}
    evidence = {"turns": turns, "rootThreadReads": root_reads, "spawnEvents": spawns, "childThreadMetadata": metadata, "expected": expected_events, "requested": True}
    child_permissions = [
        item
        for item in metadata
        if item.get("sandbox") is not None or item.get("approvalPolicy") is not None or item.get("activePermissionProfile") is not None
    ]
    if len(child_permissions) == len(metadata) and metadata:
        permission_result = observed({"metadata": child_permissions})
    else:
        permission_result = unknown(
            "child thread/read metadata did not expose effective permission fields" if child_ids else "no live child thread was observed",
            {"childThreadIds": child_ids, "checkedFields": ["sandbox", "approvalPolicy", "activePermissionProfile"]},
        )
    if blocked:
        evidence["blocked"] = True
        return unknown(f"bounded live probe stopped: {blocked}", evidence), permission_result
    if all(item["status"] == "observed" for item in expected_events.values()) and all(item["status"] == "observed" for item in root_reads) and len(spawns) == 2 and len(metadata) == 2:
        return observed(evidence), permission_result
    if any(item["status"] == "unknown" for item in root_reads):
        return unknown("parent thread/read metadata unavailable", evidence), permission_result
    return failed("native_spawn_missing_or_mismatched", evidence), permission_result


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    if options.live_timeout <= 0:
        raise SystemExit("--live-timeout must be positive")
    target = Path(options.target_repo_root).expanduser().resolve()
    source = target / "template"
    if not source.is_dir():
        raise SystemExit(f"template source does not exist: {source}")
    output = Path(options.output).expanduser() if options.output else Path(tempfile.gettempdir()) / f"codex-parent-smoke-{int(time.time())}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"schema": 1, "status": "failed", "codex": options.codex, "inputs": {"targetRepoRoot": str(target), "liveRequested": bool(options.live), "liveTimeoutSeconds": options.live_timeout}, "checks": {}}
    parent: Path | None = None
    home: Path | None = None
    rpc: Rpc | None = None
    try:
        version = subprocess.run([options.codex, "--version"], capture_output=True, text=True, timeout=10, check=False)
        report["checks"]["codex_version"] = observed(version_evidence(version)) if version.returncode == 0 else failed("codex_version_failed")
        parent, child = make_fixture(source)
        home = make_home(parent, child)
        rpc = Rpc(options.codex, parent, home)
        rpc.start()
        init = result_of(rpc.call("initialize", {"clientInfo": {"name": "guild-parent-smoke", "version": "1"}, "capabilities": {"experimentalApi": True}}), "initialize")
        rpc.notify_initialized()
        report["checks"]["app_server_initialize"] = observed({"userAgent": init.get("userAgent"), "platformOs": init.get("platformOs")})
        checks, parent_thread = static_probe(rpc, parent, child)
        report["checks"].update(checks)
        if options.live:
            native_spawn, child_permission = live_probe(rpc, parent_thread, parent, child, options.live_timeout)
            report["checks"]["native_spawn"] = native_spawn
            report["checks"]["child_effective_permission"] = child_permission
        else:
            report["checks"]["native_spawn"] = unknown("live model calls are opt-in", {"requested": False})
            report["checks"]["child_effective_permission"] = unknown("no live child thread was created", {"requested": False})
    except (OSError, ProbeError, UnicodeError, subprocess.SubprocessError) as exc:
        report["checks"]["probe_runtime"] = failed("probe_runtime_error", {"errorType": type(exc).__name__})
    finally:
        if rpc is not None:
            rpc.close()
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
        if parent is not None:
            report["fixture"] = {"parent": str(parent), "child": str(parent / "repositories" / "app"), "retained": bool(options.keep_fixture)}
            if not options.keep_fixture:
                shutil.rmtree(parent, ignore_errors=True)
    statuses = [item.get("status") for item in report["checks"].values() if isinstance(item, dict)]
    report["status"] = "failed" if "failed" in statuses else "unknown" if "unknown" in statuses else "observed"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "observed" or (report["status"] == "unknown" and not options.live) else 3


if __name__ == "__main__":
    raise SystemExit(main())
