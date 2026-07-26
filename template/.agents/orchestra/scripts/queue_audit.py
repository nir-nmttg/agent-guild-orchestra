#!/usr/bin/env python3
"""Guild Ledger SQLite runtime を読み取り専用で監査する。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any


QUEUE_SCHEMA_VERSION = "4.0"
QUEUE_SCHEMA_SHA256 = "a883418f960b28bc84381ed00fe4a9b55eb870e50a41fa26122ace122acb7c7c"
SQLITE_DB_NAME = "state.sqlite"

REQUIRED_TABLES = {
    "queue_metadata",
    "events",
    "quests",
    "requests",
    "commands",
    "assignments",
    "reports",
    "trials",
    "inbox_messages",
}

REQUIRED_COLUMNS = {
    "queue_metadata": {"key", "value", "updated_at"},
    "events": {
        "event_id",
        "timestamp",
        "actor",
        "event_type",
        "entity_type",
        "entity_id",
        "entity_json",
        "operation",
        "workflow_id",
        "structured_data_usage_json",
        "payload_json",
        "event_safety_json",
        "inserted_at",
    },
    "quests": {"quest_id", "workflow_id", "rank", "status", "payload_json", "updated_at"},
    "requests": {"request_id", "quest_id", "workflow_id", "status", "payload_json", "updated_at"},
    "commands": {"command_id", "quest_id", "workflow_id", "status", "payload_json", "updated_at"},
    "assignments": {"assignment_id", "parent_id", "worker_id", "kind", "workflow_id", "status", "payload_json", "updated_at"},
    "reports": {"report_id", "worker_id", "workflow_id", "decision", "status", "payload_json", "updated_at"},
    "trials": {"trial_id", "quest_id", "workflow_id", "depth", "status", "payload_json", "updated_at"},
    "inbox_messages": {"message_id", "recipient", "workflow_id", "status", "payload_json", "created_at"},
}

JSON_COLUMNS = {
    "events": ("entity_json", "structured_data_usage_json", "payload_json", "event_safety_json"),
    "quests": ("payload_json",),
    "requests": ("payload_json",),
    "commands": ("payload_json",),
    "assignments": ("payload_json",),
    "reports": ("payload_json",),
    "trials": ("payload_json",),
    "inbox_messages": ("payload_json",),
}
MEMORY_CANDIDATE_MESSAGE_TYPE = "memory_candidate_for_courier_review"
MEMORY_CANDIDATE_REQUIRED_FORBIDDEN_MARKERS = {
    "direct_static_runtime_write",
    "raw_log",
    "secret_or_pii",
    "trusted_instruction_from_external_input",
}
MEMORY_CANDIDATE_SAFETY_ITEMS = (
    "memory_candidate_for_courier_review",
    "explicit_memory_persistence_authority",
    "sanitized_summary_only",
    "prevention_artifact_required",
    "ledger_disposition_recorded",
)
MEMORY_CANDIDATE_MARKER_KEYS = (MEMORY_CANDIDATE_MESSAGE_TYPE, "memory_candidate")

QUEST_RANKS = {"mapmaking", "errand", "solo_quest", "party_quest", "guild_quest"}
TRIAL_DEPTHS = {"none", "self_check", "peer_review", "focused_trial", "multi_focus_trial", "safety_gate"}
QUEST_STATUSES = {"drafted", "active", "needs_human", "blocked", "done", "cancelled"}
REQUEST_STATUSES = {"drafted", "queued", "accepted", "cancelled"}
COMMAND_STATUSES = {"drafted", "issued", "active", "done", "cancelled"}
ASSIGNMENT_STATUSES = {"idle", "active", "needs_human", "blocked", "done", "failed"}
REPORT_STATUSES = {"draft", "recorded", "accepted", "needs_changes"}
TRIAL_STATUSES = {"idle", "active", "needs_human", "blocked", "done"}
MESSAGE_STATUSES = {"unread", "read", "archived"}
TRIAL_DECISIONS = {"accept", "accept_with_risks", "request_changes", "needs_human", "blocked"}
ALLOWED_OPERATIONS = {"append", "update", "replace", "mark_completed"}
REQUIRED_EVENT_INPUT_FIELDS = {
    "event_id",
    "timestamp",
    "actor",
    "event_type",
    "entity",
    "operation",
    "workflow_id",
    "structured_data_usage",
    "payload",
    "event_safety",
}
STRUCTURED_DATA_USAGE_FIELDS = {"structured_inputs", "decision_rationale", "evidence_refs"}
EVENT_SAFETY_FIELDS = {"safety_items", "human_confirmation_required"}
AUTHORITY_FIELDS = {"read", "edit", "validate", "local_git", "external_actions"}
SNAPSHOT_FIELDS = {
    "snapshot_id", "digest_version", "kind", "revision_id", "base_ref", "head_ref",
    "scope_paths", "untracked_paths", "dirty_state", "diff_hash",
}
TERMINAL_ASSIGNMENT_WORKERS = {
    "adventurer", "artificer", "sage", "cartographer", "courier",
    "examiner", "guildmaster", "inquisitor", "captain", "warden",
}
READ_ONLY_ASSIGNMENT_WORKERS = {
    "sage", "cartographer", "examiner", "guildmaster", "inquisitor", "captain", "warden",
}
EXPECTED_ASSIGNMENT_ROLES = {
    "adventurer": "bounded_implementation_owner",
    "artificer": "cross_scope_artificer",
    "sage": "independent_focus_sage",
    "cartographer": "mapmaking_specialist",
    "examiner": "bounded_trial_examiner",
    "guildmaster": "guild_strategy_owner",
    "inquisitor": "trial_lead",
    "captain": "execution_designer",
    "warden": "exceptional_control_diagnostician",
    "courier": "ledger_and_git_courier",
}
SHA256_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
ALLOWED_EVENT_TYPES = {
    "quest_created",
    "quest_updated",
    "quest_completed",
    "request_enqueued",
    "command_created",
    "assignment_created",
    "assignment_updated",
    "report_recorded",
    "trial_recorded",
    "inbox_message_added",
    "status_changed",
    "dashboard_updated",
    "state_compacted",
}
ALLOWED_ENTITY_TYPES = {
    "quest",
    "request",
    "command",
    "assignment",
    "report",
    "trial",
    "message",
    "dashboard",
    "state",
    "status",
}
EVENT_ENTITY_TYPE_RULES = {
    "quest_created": {"quest"},
    "quest_updated": {"quest"},
    "quest_completed": {"quest"},
    "request_enqueued": {"request"},
    "command_created": {"command"},
    "assignment_created": {"assignment"},
    "assignment_updated": {"assignment"},
    "report_recorded": {"report"},
    "trial_recorded": {"trial", "report"},
    "inbox_message_added": {"message"},
    "status_changed": {"status"},
    "dashboard_updated": {"dashboard"},
    "state_compacted": {"state"},
}
LEGACY_JSON_KEYS = {
    "safety_checks",
    "requires_human_confirmation",
    "target_path",
    "scale_selected",
    "risk_dimensions",
    "edit_scope",
    "read_scope",
    "quality_profile",
    "review_task",
    "review_assignment",
    "task_id",
    "scout_plan",
    "scout_usage",
    "scout_calls",
    "scout_policy",
    "spark_request",
    "meta" "cognitive_state",
    "meta" "cognitive_control",
    "meta" "cognitive_controller",
    "invoke_" "meta" "cognitive_controller",
    "meta" "cognitive_task_loop",
}
RETIRED_AGENT_VALUES = {
    "advisor",
    "focus_reviewer",
    "integration_owner",
    "party_leader",
    "quest_sentinel",
    "spark",
    "scout",
    "meta" "cognitive_controller",
}
LEGACY_RUNTIME_STRING_VALUES = {
    "advisor",
    "focus_reviewer",
    "integration_owner",
    "party_leader",
    "quest_sentinel",
    "advisory_consultation",
    "bounded_trial_focus_reviewer",
    "cross_scope_integration_owner",
    "independent_focus_advisor",
    "spark",
    "scout",
    "meta" "cognitive",
    "meta" "cognitive_controller",
    "meta" "cognitive-task-loop",
    "meta" "cognitive_state",
    "meta" "cognitive_control",
    "invoke_" "meta" "cognitive_controller",
}
LEGACY_TABLES = {"tickets"}
LEGACY_COLUMNS = {"assignments": {"task_id"}}


def default_static_root() -> Path:
    env_value = os.environ.get("AGENT_GUILD_ORCHESTRA_ROOT")
    if env_value:
        return Path(env_value).expanduser() / ".agents" / "orchestra"
    return Path(__file__).resolve().parent.parent


def default_runtime_root() -> Path:
    env_value = os.environ.get("AGENT_GUILD_ORCHESTRA_RUNTIME_ROOT")
    if env_value:
        return Path(env_value).expanduser()
    static_root = default_static_root()
    guild_root = static_root.parent.parent
    return guild_root / ".orchestra"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Guild Ledger SQLite runtime を監査します。")
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root(), help="監査する `.orchestra` の path。")
    parser.add_argument("--static-root", type=Path, default=default_static_root(), help="静的 `.agents/orchestra` の path。")
    parser.add_argument("--json", action="store_true", help="結果を JSON で出力します。")
    return parser.parse_args()


def connect_read(database_path: Path) -> sqlite3.Connection:
    if not database_path.exists():
        raise SystemExit(f"SQLite runtime DB がありません: {database_path}。`queue_db.py init` を実行してください。")
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def parse_json(raw: str, label: str, errors: list[str]) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: JSON を読めません: {exc}")
        return None


def parse_timestamp(raw: str, label: str, errors: list[str]) -> None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: timestamp は ISO 8601 形式にしてください: {raw}")
        return
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{label}: timestamp は timezone offset 付きにしてください: {raw}")


def iter_keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            findings.append((child_path, key))
            findings.extend(iter_keys(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(iter_keys(item, f"{path}[{index}]"))
    return findings


def memory_candidate_marker_path(value: Any, path: str) -> str | None:
    if isinstance(value, dict):
        if value.get("type") == MEMORY_CANDIDATE_MESSAGE_TYPE:
            return f"{path}.type"
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in MEMORY_CANDIDATE_MARKER_KEYS:
                return child_path
            nested = memory_candidate_marker_path(item, child_path)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nested = memory_candidate_marker_path(item, f"{path}[{index}]")
            if nested is not None:
                return nested
    return None


def memory_candidate_envelope(value: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    payload = value.get("payload")
    if value.get("type") == MEMORY_CANDIDATE_MESSAGE_TYPE and not isinstance(payload, dict):
        return ("$.payload", {})
    if value.get("type") == MEMORY_CANDIDATE_MESSAGE_TYPE:
        marker_path = memory_candidate_marker_path(payload, "$.payload")
        if marker_path is not None:
            return (marker_path, {})
        return ("$.payload", payload)
    marker_path = memory_candidate_marker_path(value, "$")
    if marker_path is not None:
        candidate: Any = value
        for part in marker_path.removeprefix("$.").split("."):
            if isinstance(candidate, dict):
                candidate = candidate.get(part)
            else:
                candidate = None
                break
        if not isinstance(candidate, dict):
            candidate = {}
        return (marker_path, candidate)
    return None


def validate_memory_candidate_message_scope(value: dict[str, Any], label: str, errors: list[str]) -> None:
    if value.get("type") != MEMORY_CANDIDATE_MESSAGE_TYPE:
        errors.append(f"{label}.type: memory candidate は exact type `{MEMORY_CANDIDATE_MESSAGE_TYPE}` の courier review 専用 envelope として記録してください。")
    if value.get("sender") != "courier":
        errors.append(f"{label}.sender: memory candidate は courier sender で記録してください。")
    if value.get("recipient") != "courier":
        errors.append(f"{label}.recipient: memory candidate は courier 宛にしてください。")
    if value.get("trusted") is not False:
        errors.append(f"{label}.trusted: memory candidate は trusted=false にしてください。")


def validate_message_trust(value: dict[str, Any], label: str, errors: list[str]) -> None:
    if value.get("trusted") is not False:
        errors.append(f"{label}.trusted: message は trusted=false にしてください。")


def validate_memory_candidate_envelope(value: dict[str, Any], label: str, errors: list[str]) -> None:
    envelope_info = memory_candidate_envelope(value)
    if envelope_info is None:
        return
    envelope_path, envelope = envelope_info
    envelope_label = f"{label}{envelope_path[1:]}"
    validate_memory_candidate_message_scope(value, label, errors)
    if envelope.get("explicit_memory_persistence_authority") is not True:
        errors.append(f"{envelope_label}.explicit_memory_persistence_authority: true が必要です。")
    if envelope.get("sanitized_summary_only") is not True:
        errors.append(f"{envelope_label}.sanitized_summary_only: true が必要です。")
    if not isinstance(envelope.get("sanitized_summary"), str) or not envelope.get("sanitized_summary"):
        errors.append(f"{envelope_label}.sanitized_summary: 空でない文字列が必要です。")
    artifact = envelope.get("prevention_artifact")
    if not isinstance(artifact, dict):
        errors.append(f"{envelope_label}.prevention_artifact: JSON object が必要です。")
        artifact = {}
    if not isinstance(artifact.get("kind"), str) or not artifact.get("kind"):
        errors.append(f"{envelope_label}.prevention_artifact.kind: 空でない文字列が必要です。")
    if not any(isinstance(artifact.get(key), str) and artifact.get(key) for key in ("ref", "description")):
        errors.append(f"{envelope_label}.prevention_artifact.ref_or_description: ref または description が必要です。")
    if not isinstance(envelope.get("ledger_disposition"), str) or not envelope.get("ledger_disposition"):
        errors.append(f"{envelope_label}.ledger_disposition: 空でない文字列が必要です。")
    forbidden = envelope.get("forbidden")
    if not isinstance(forbidden, dict):
        errors.append(f"{envelope_label}.forbidden: JSON object が必要です。")
        forbidden = {}
    missing_markers = sorted(MEMORY_CANDIDATE_REQUIRED_FORBIDDEN_MARKERS - set(forbidden))
    if missing_markers:
        errors.append(f"{envelope_label}.forbidden: marker が不足しています: " + ", ".join(missing_markers))
    for marker in MEMORY_CANDIDATE_REQUIRED_FORBIDDEN_MARKERS:
        if forbidden.get(marker) is not True:
            errors.append(f"{envelope_label}.forbidden.{marker}: true が必要です。")
    for json_path, key in iter_keys(envelope):
        if json_path.startswith("$.forbidden."):
            continue
        if key in MEMORY_CANDIDATE_REQUIRED_FORBIDDEN_MARKERS:
            errors.append(f"{envelope_label}{json_path[1:]}: memory candidate に forbidden 内容 `{key}` を含めないでください。")


def validate_memory_candidate_event_safety(value: dict[str, Any], safety: dict[str, Any], label: str, errors: list[str], actor: Any | None = None) -> None:
    if memory_candidate_envelope(value) is None:
        return
    if actor != "courier":
        errors.append(f"{label}.actor: memory candidate event は courier actor で記録してください。")
    safety_items = safety.get("safety_items")
    if not isinstance(safety_items, list):
        errors.append(f"{label}.safety_items: list が必要です。")
        return
    missing = sorted(set(MEMORY_CANDIDATE_SAFETY_ITEMS) - set(safety_items))
    if missing:
        errors.append(f"{label}.safety_items: memory_candidate_gate が不足しています: " + ", ".join(missing))


def iter_values(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    findings: list[tuple[str, Any]] = [(path, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            findings.extend(iter_values(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(iter_values(item, f"{path}[{index}]"))
    return findings


def iter_target_repo_roots(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    findings: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key == "target_repo_root":
                findings.append((child_path, item))
            findings.extend(iter_target_repo_roots(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(iter_target_repo_roots(item, f"{path}[{index}]"))
    return findings


def validate_target_repo_roots(value: Any, label: str, guild_root: Path, errors: list[str]) -> None:
    root = guild_root.resolve(strict=False)
    repositories_root = root / "repositories"
    for json_path, raw_path in iter_target_repo_roots(value):
        value_label = f"{label}{json_path[1:]}"
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{value_label}: target_repo_root は空でない絶対 path 文字列にしてください。")
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            errors.append(f"{value_label}: target_repo_root は絶対 path にしてください: {raw_path}")
            continue
        normalized = candidate.resolve(strict=False)
        if normalized == root:
            errors.append(f"{value_label}: target_repo_root にギルド規約ルート自体は指定できません: {raw_path}")
        elif normalized == repositories_root:
            errors.append(f"{value_label}: target_repo_root に repositories/ 自体は指定できません: {raw_path}")
        elif normalized.parent != repositories_root:
            errors.append(f"{value_label}: target_repo_root は <guild_root>/repositories/<repo> の直下 path にしてください: {raw_path}")


def validate_no_legacy_keys(value: Any, label: str, errors: list[str]) -> None:
    for json_path, key in iter_keys(value):
        if key in LEGACY_JSON_KEYS or key in LEGACY_RUNTIME_STRING_VALUES:
            errors.append(f"{label}{json_path[1:]}: v4 Ledger に廃止済み key `{key}` が残っています。")
    for json_path, item in iter_values(value):
        if isinstance(item, str) and item in LEGACY_RUNTIME_STRING_VALUES:
            errors.append(f"{label}{json_path[1:]}: v4 Ledger に廃止済み runtime 値 `{item}` が残っています。")


def iter_named_values(value: Any, target_key: str, path: str = "$") -> list[tuple[str, Any]]:
    findings: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key == target_key:
                findings.append((child_path, item))
            findings.extend(iter_named_values(item, target_key, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(iter_named_values(item, target_key, f"{path}[{index}]"))
    return findings


def validate_report_trial_depths(value: Any, label: str, errors: list[str]) -> None:
    for json_path, depth in iter_named_values(value, "trial_depth"):
        if not isinstance(depth, str) or depth not in TRIAL_DEPTHS:
            expected = ", ".join(sorted(TRIAL_DEPTHS))
            errors.append(f"{label}{json_path[1:]}: Trial depth は {expected} のいずれかにしてください: {depth}")


def audit_entity_payload_identity(
    payload: dict[str, Any],
    *,
    entity_type: str,
    entity_id: str,
    workflow_id: Any,
    label: str,
    errors: list[str],
) -> None:
    nested_names = {
        "quest": ("quest",),
        "request": ("request",),
        "command": ("command",),
        "assignment": ("assignment",),
        "report": ("report",),
        "trial": ("trial",),
    }
    canonical = payload
    for name in nested_names.get(entity_type, ()):
        if isinstance(payload.get(name), dict):
            canonical = payload[name]
            break
    id_keys = {
        "quest": ("id", "quest_id"),
        "request": ("id", "request_id"),
        "command": ("id", "command_id"),
        "assignment": ("id", "assignment_id"),
        "report": ("id", "report_id"),
        "trial": ("id", "trial_id"),
        "message": ("id", "message_id"),
    }
    if entity_type in id_keys:
        payload_id = next((canonical.get(key) for key in id_keys[entity_type] if canonical.get(key) is not None), None)
        if not isinstance(payload_id, str) or not payload_id:
            errors.append(f"{label}: canonical {entity_type} idがありません。")
        elif payload_id != entity_id:
            errors.append(f"{label}: entity_idとpayload idが一致しません: {entity_id} != {payload_id}")
    if canonical.get("workflow_id") is not None and canonical.get("workflow_id") != workflow_id:
        errors.append(f"{label}: event workflow_idとpayload workflow_idが一致しません。")


def expected_assignment_parent_id(payload: dict[str, Any], label: str, errors: list[str]) -> str | None:
    owner_assignment_id = payload.get("owner_assignment_id")
    parent_id = payload.get("parent_id")
    if owner_assignment_id is not None and (not isinstance(owner_assignment_id, str) or not owner_assignment_id):
        errors.append(f"{label}.owner_assignment_id: null または空でない文字列にしてください。")
        owner_assignment_id = None
    if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
        errors.append(f"{label}.parent_id: null または空でない文字列にしてください。")
        parent_id = None
    if owner_assignment_id and parent_id and owner_assignment_id != parent_id:
        errors.append(f"{label}: owner_assignment_id と parent_id は同じ owner assignment を指してください。")
    worker_id = payload.get("worker_id")
    kind = payload.get("kind")
    if (worker_id == "sage" or kind == "sage_consultation") and not (owner_assignment_id or parent_id):
        errors.append(f"{label}: sage assignment は owner_assignment_id または parent_id が必要です。")
    if worker_id == "warden" or kind in {"quest_awareness_control_monitor", "evidence_state_monitor"}:
        if not (owner_assignment_id or parent_id):
            errors.append(f"{label}: warden assignment は owner_assignment_id または parent_id が必要です。")
        control_trigger = payload.get("control_trigger")
        if not isinstance(control_trigger, str) or not control_trigger:
            errors.append(f"{label}.control_trigger: warden assignment には空でない control_trigger が必要です。")
    quest_id = payload.get("quest_id")
    if quest_id is not None and (not isinstance(quest_id, str) or not quest_id):
        errors.append(f"{label}.quest_id: null または空でない文字列にしてください。")
        quest_id = None
    return owner_assignment_id or parent_id or quest_id


def audit_nonempty_string_list(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{label}: 空でない文字列のnon-empty listが必要です。")


def audit_evidence_items(value: Any, label: str, errors: list[str]) -> set[str]:
    if not isinstance(value, list):
        errors.append(f"{label}: evidence item listが必要です。")
        return set()
    item_ids: set[str] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict) or set(item) != {"id", "summary"}:
            errors.append(f"{item_label}: id/summaryだけを持つobjectが必要です。")
            continue
        item_id = item.get("id")
        summary = item.get("summary")
        if not isinstance(item_id, str) or not item_id:
            errors.append(f"{item_label}.id: 空でない文字列が必要です。")
            continue
        if not isinstance(summary, str) or not summary:
            errors.append(f"{item_label}.summary: 空でない文字列が必要です。")
        if item_id in item_ids:
            errors.append(f"{label}: idが重複しています: {item_id}")
        item_ids.add(item_id)
    return item_ids


def audit_finding_dispositions(value: Any, expected_ids: set[str], decision: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"adopted", "rejected", "unresolved"}:
        errors.append(f"{label}: adopted/rejected/unresolvedのcanonical fieldsが必要です。")
        return
    seen: set[str] = set()
    for key in ("adopted", "rejected", "unresolved"):
        refs = value.get(key)
        if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
            errors.append(f"{label}.{key}: evidence item ID listが必要です。")
            continue
        if len(refs) != len(set(refs)):
            errors.append(f"{label}.{key}: IDが重複しています。")
        overlap = seen.intersection(refs)
        if overlap:
            errors.append(f"{label}: IDはexactly once処置してください: {sorted(overlap)[0]}")
        seen.update(refs)
    if seen != expected_ids:
        errors.append(f"{label}: 全candidate/unknown IDをexactly once処置してください。")
    unresolved = value.get("unresolved")
    if decision in {"accept", "accept_with_risks"} and isinstance(unresolved, list) and unresolved:
        errors.append(f"{label}.unresolved: decision={decision}では空にしてください。")


def audit_relative_path_list(value: Any, label: str, errors: list[str], *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        errors.append(f"{label}: repo-relative path listが必要です。")
        return
    for item in value:
        if not isinstance(item, str) or not item:
            errors.append(f"{label}: 空でないrepo-relative path文字列だけにしてください。")
            continue
        path = Path(item)
        if path.is_absolute() or any(part in {"", ".", "..", ".git"} for part in path.parts):
            errors.append(f"{label}: 安全でないrepo-relative pathです: {item}")


def path_covered_by_scopes(path: str, scopes: list[str]) -> bool:
    path_parts = Path(path).parts
    return any(path_parts[: len(Path(scope).parts)] == Path(scope).parts for scope in scopes)


def audit_assignment_machine_contract(payload: dict[str, Any], label: str, errors: list[str]) -> None:
    worker_id = payload.get("worker_id")
    if worker_id not in TERMINAL_ASSIGNMENT_WORKERS:
        errors.append(f"{label}.worker_id: managed terminal worker ではありません: {worker_id}")
        return
    if payload.get("terminal_worker") is not True:
        errors.append(f"{label}.terminal_worker: true が必要です。")
    expected_role = EXPECTED_ASSIGNMENT_ROLES.get(worker_id)
    if expected_role is not None and payload.get("role") != expected_role:
        errors.append(f"{label}.role: worker_id={worker_id} に対応する {expected_role} が必要です。")
    if not isinstance(payload.get("objective"), str) or not payload.get("objective"):
        errors.append(f"{label}.objective: 空でない文字列が必要です。")

    authority = payload.get("authority")
    if not isinstance(authority, dict) or set(authority) != AUTHORITY_FIELDS or not all(isinstance(authority[key], bool) for key in AUTHORITY_FIELDS):
        errors.append(f"{label}.authority: bool の canonical 5 fields が必要です。")
    elif authority.get("external_actions") is True or authority.get("local_git") is True:
        errors.append(f"{label}.authority: assignment 作成時の権限を超えています。")
    elif authority.get("read") is not True:
        errors.append(f"{label}.authority.read: managed assignmentではtrueが必要です。")
    elif worker_id in READ_ONLY_ASSIGNMENT_WORKERS and authority.get("edit") is True:
        errors.append(f"{label}.authority.edit: read-only worker {worker_id} には許可できません。")

    boundaries = payload.get("boundaries")
    if not isinstance(boundaries, dict) or not isinstance(boundaries.get("target_repo_root"), str) or not boundaries.get("target_repo_root"):
        errors.append(f"{label}.boundaries.target_repo_root: 空でない文字列が必要です。")
    elif any(not isinstance(boundaries.get(key), list) for key in ("read_deny", "edit_deny", "safety_items")):
        errors.append(f"{label}.boundaries: deny/safety fields は list にしてください。")

    snapshot = payload.get("subject_snapshot")
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        errors.append(f"{label}.subject_snapshot: canonical snapshot fields が必要です。")
    else:
        snapshot_id = snapshot.get("snapshot_id")
        if not isinstance(snapshot_id, str) or SHA256_ID_RE.fullmatch(snapshot_id) is None:
            errors.append(f"{label}.subject_snapshot.snapshot_id: helper形式のsha256が必要です。")
        if snapshot.get("digest_version") != "agent-guild-orchestra-snapshot-v1":
            errors.append(f"{label}.subject_snapshot.digest_version: agent-guild-orchestra-snapshot-v1 が必要です。")
        if snapshot.get("kind") not in {"revision_only", "working_tree_content", "commit_range"}:
            errors.append(f"{label}.subject_snapshot.kind: 不正です。")
        if not isinstance(snapshot.get("revision_id"), str) or COMMIT_OID_RE.fullmatch(snapshot["revision_id"]) is None:
            errors.append(f"{label}.subject_snapshot.revision_id: commit OIDが必要です。")
        if snapshot.get("dirty_state") not in {"clean", "dirty"}:
            errors.append(f"{label}.subject_snapshot.dirty_state: clean / dirtyが必要です。")
        kind = snapshot.get("kind")
        diff_hash = snapshot.get("diff_hash")
        if kind == "revision_only":
            if (
                diff_hash is not None
                or snapshot.get("base_ref") is not None
                or snapshot.get("head_ref") is not None
                or snapshot.get("dirty_state") != "clean"
                or snapshot.get("untracked_paths") != []
            ):
                errors.append(f"{label}.subject_snapshot: revision_onlyはclean、diff/base/head=null、untracked_paths=[]にしてください。")
        else:
            if not isinstance(diff_hash, str) or SHA256_ID_RE.fullmatch(diff_hash) is None or snapshot_id != diff_hash:
                errors.append(f"{label}.subject_snapshot: content snapshotのid/hashが不正です。")
            if not isinstance(snapshot.get("base_ref"), str) or COMMIT_OID_RE.fullmatch(snapshot["base_ref"]) is None:
                errors.append(f"{label}.subject_snapshot.base_ref: commit OIDが必要です。")
            if not snapshot.get("scope_paths"):
                errors.append(f"{label}.subject_snapshot.scope_paths: content snapshotではnon-emptyにしてください。")
            if kind == "working_tree_content" and snapshot.get("head_ref") is not None:
                errors.append(f"{label}.subject_snapshot.head_ref: working_tree_contentではnullにしてください。")
            if kind == "commit_range":
                if not isinstance(snapshot.get("head_ref"), str) or COMMIT_OID_RE.fullmatch(snapshot["head_ref"]) is None:
                    errors.append(f"{label}.subject_snapshot.head_ref: commit_rangeではcommit OIDが必要です。")
                if snapshot.get("untracked_paths") != []:
                    errors.append(f"{label}.subject_snapshot.untracked_paths: commit_rangeでは空にしてください。")
        audit_relative_path_list(snapshot.get("scope_paths"), f"{label}.subject_snapshot.scope_paths", errors)
        audit_relative_path_list(snapshot.get("untracked_paths"), f"{label}.subject_snapshot.untracked_paths", errors)

    if worker_id == "examiner":
        lineage = payload.get("caller_lineage")
        if payload.get("owner_worker_id") != "inquisitor" or not isinstance(lineage, dict):
            errors.append(f"{label}.caller_lineage: inquisitor owner の queue lineage が必要です。")
        elif not isinstance(lineage.get("trial_ref"), str) or not lineage.get("trial_ref") or lineage.get("verification") != "verified":
            errors.append(f"{label}.caller_lineage: verified trial_ref が必要です。")

    if worker_id in {"adventurer", "artificer"}:
        audit_nonempty_string_list(payload.get("success_criteria"), f"{label}.success_criteria", errors)
        owned_scope = payload.get("owned_scope")
        if not isinstance(owned_scope, dict) or set(owned_scope) != {"read", "edit", "validate"}:
            errors.append(f"{label}.owned_scope: read/edit/validateのcanonical fieldsが必要です。")
        else:
            audit_relative_path_list(owned_scope["read"], f"{label}.owned_scope.read", errors)
            audit_relative_path_list(owned_scope["edit"], f"{label}.owned_scope.edit", errors, nonempty=True)
            audit_relative_path_list(owned_scope["validate"], f"{label}.owned_scope.validate", errors)
            if isinstance(owned_scope["read"], list) and isinstance(owned_scope["edit"], list) and all(isinstance(item, str) for item in owned_scope["read"] + owned_scope["edit"]):
                if any(not path_covered_by_scopes(path, owned_scope["read"]) for path in owned_scope["edit"]):
                    errors.append(f"{label}.owned_scope.read: 全edit pathを包含してください。")
        if isinstance(authority, dict) and (authority.get("edit") is not True or authority.get("validate") is not True):
            errors.append(f"{label}.authority: {worker_id}にはedit/validate authorityが必要です。")
    elif worker_id in {"cartographer", "guildmaster", "captain", "inquisitor"}:
        audit_nonempty_string_list(payload.get("success_criteria"), f"{label}.success_criteria", errors)

    if worker_id in {"sage", "examiner"}:
        if not isinstance(payload.get("focus"), str) or not payload.get("focus"):
            errors.append(f"{label}.focus: 空でない具体的focusが必要です。")
        audit_nonempty_string_list(payload.get("evidence_required"), f"{label}.evidence_required", errors)
        if worker_id == "examiner" and (not isinstance(payload.get("risk_trigger"), str) or not payload.get("risk_trigger")):
            errors.append(f"{label}.risk_trigger: 空でない文字列が必要です。")
    elif worker_id in {"cartographer", "warden"}:
        audit_nonempty_string_list(payload.get("evidence_required"), f"{label}.evidence_required", errors)

    if worker_id == "artificer":
        barrier = payload.get("integration_barrier")
        if not isinstance(barrier, dict) or barrier.get("status") != "complete" or barrier.get("mutation_stopped") is not True:
            errors.append(f"{label}.integration_barrier: completeかつmutation_stoppedのbarrierが必要です。")
        else:
            audit_nonempty_string_list(barrier.get("upstream_report_refs"), f"{label}.integration_barrier.upstream_report_refs", errors)
            if not isinstance(barrier.get("contract_ref"), str) or not barrier.get("contract_ref"):
                errors.append(f"{label}.integration_barrier.contract_ref: 空でないcommand refが必要です。")
            if barrier.get("verification") != "verified":
                errors.append(f"{label}.integration_barrier.verification: verifiedが必要です。")
    elif payload.get("integration_barrier") is not None:
        errors.append(f"{label}.integration_barrier: artificer以外には指定できません。")


def schema_signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'view', 'trigger')
        ORDER BY type, name, tbl_name
        """
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), " ".join(str(row[3] or "").split()))
        for row in rows
        if not str(row[1]).startswith("sqlite_")
    )


def canonical_schema_signature(static_root: Path) -> tuple[tuple[str, str, str, str], ...]:
    path = static_root / "scripts" / "queue_schema.sql"
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != QUEUE_SCHEMA_SHA256:
        raise ValueError(f"queue_schema.sql SHA-256 がv{QUEUE_SCHEMA_VERSION} contractと一致しません: {digest}")
    try:
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(content.decode("utf-8"))
            return schema_signature(expected)
    except (UnicodeDecodeError, sqlite3.DatabaseError) as exc:
        raise ValueError(f"canonical queue_schema.sqlを検証できません: {exc}") from exc


def audit_schema(connection: sqlite3.Connection, static_root: Path, errors: list[str]) -> bool:
    schema_errors: list[str] = []
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    legacy_tables = sorted(LEGACY_TABLES & tables)
    if legacy_tables:
        schema_errors.append("SQLite schema に旧 table があります: " + ", ".join(legacy_tables))
    managed_tables = {table for table in tables if not table.startswith("sqlite_")}
    unexpected_tables = sorted(managed_tables - REQUIRED_TABLES - LEGACY_TABLES)
    if unexpected_tables:
        schema_errors.append("SQLite schema に未知 table があります: " + ", ".join(unexpected_tables))
    missing_tables = sorted(REQUIRED_TABLES - tables)
    if missing_tables:
        schema_errors.append("SQLite schema に不足 table があります: " + ", ".join(missing_tables))
    for table, required_columns in REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        legacy_columns = sorted(LEGACY_COLUMNS.get(table, set()) & columns)
        if legacy_columns:
            schema_errors.append(f"{table}: 旧 column があります: " + ", ".join(legacy_columns))
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            schema_errors.append(f"{table}: 不足 column があります: " + ", ".join(missing_columns))
        unexpected_columns = sorted(columns - required_columns - LEGACY_COLUMNS.get(table, set()))
        if unexpected_columns:
            schema_errors.append(f"{table}: 未知 column があります: " + ", ".join(unexpected_columns))
    try:
        expected_signature = canonical_schema_signature(static_root)
        actual_signature = schema_signature(connection)
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        schema_errors.append(str(exc))
    else:
        if actual_signature != expected_signature:
            expected_by_name = {(item[0], item[1]): item for item in expected_signature}
            actual_by_name = {(item[0], item[1]): item for item in actual_signature}
            missing = sorted(f"{kind}:{name}" for kind, name in expected_by_name.keys() - actual_by_name.keys())
            unexpected = sorted(f"{kind}:{name}" for kind, name in actual_by_name.keys() - expected_by_name.keys())
            changed = sorted(
                f"{kind}:{name}"
                for kind, name in expected_by_name.keys() & actual_by_name.keys()
                if expected_by_name[(kind, name)] != actual_by_name[(kind, name)]
            )
            details = []
            if missing:
                details.append("不足=" + ",".join(missing))
            if unexpected:
                details.append("未知=" + ",".join(unexpected))
            if changed:
                details.append("定義差分=" + ",".join(changed))
            schema_errors.append("canonical table/index/constraint signature不一致: " + "; ".join(details))
    errors.extend(schema_errors)
    return not schema_errors


def audit_metadata(connection: sqlite3.Connection, errors: list[str]) -> None:
    try:
        row = connection.execute("SELECT value FROM queue_metadata WHERE key = 'schema_version'").fetchone()
    except sqlite3.Error as exc:
        errors.append(f"queue_metadata を読めません: {exc}")
        return
    if row is None:
        errors.append("queue_metadata.schema_version がありません。")
    elif row["value"] != QUEUE_SCHEMA_VERSION:
        errors.append(f"queue_metadata.schema_version は {QUEUE_SCHEMA_VERSION} にしてください: {row['value']}")


def audit_json_columns(connection: sqlite3.Connection, guild_root: Path, errors: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, columns in JSON_COLUMNS.items():
        try:
            rows = connection.execute(f"SELECT rowid, * FROM {table}").fetchall()
        except sqlite3.Error as exc:
            errors.append(f"{table}: 読み取りに失敗しました: {exc}")
            continue
        counts[table] = len(rows)
        for row in rows:
            row_label = f"{table}[rowid={row['rowid']}]"
            for column in columns:
                raw = row[column]
                if not isinstance(raw, str) or not raw:
                    errors.append(f"{row_label}.{column}: JSON 文字列が必要です。")
                    continue
                parsed = parse_json(raw, f"{row_label}.{column}", errors)
                if parsed is None:
                    continue
                validate_target_repo_roots(parsed, f"{row_label}.{column}", guild_root, errors)
                validate_no_legacy_keys(parsed, f"{row_label}.{column}", errors)
                if table == "inbox_messages" and column == "payload_json" and isinstance(parsed, dict):
                    validate_message_trust(parsed, f"{row_label}.{column}", errors)
                if isinstance(parsed, dict) and column == "payload_json" and table in {"events", "inbox_messages"}:
                    validate_memory_candidate_envelope(parsed, f"{row_label}.{column}", errors)
                if table in {"events", "reports"} and column == "payload_json":
                    validate_report_trial_depths(parsed, f"{row_label}.{column}", errors)
    return counts


def audit_events(connection: sqlite3.Connection, errors: list[str]) -> None:
    try:
        rows = connection.execute("SELECT * FROM events").fetchall()
    except sqlite3.Error as exc:
        errors.append(f"events: 読み取りに失敗しました: {exc}")
        return
    required_strings = ("event_id", "timestamp", "actor", "event_type", "entity_type", "entity_id", "operation")
    for row in rows:
        label = f"events[{row['event_id'] or 'unknown'}]"
        for column in required_strings:
            if not isinstance(row[column], str) or not row[column]:
                errors.append(f"{label}.{column}: 空でない文字列にしてください。")
        event_type = row["event_type"]
        entity_type = row["entity_type"]
        if isinstance(event_type, str) and event_type and event_type not in ALLOWED_EVENT_TYPES:
            errors.append(f"{label}.event_type: 未定義の event type です: {event_type}")
        if isinstance(entity_type, str) and entity_type and entity_type not in ALLOWED_ENTITY_TYPES:
            errors.append(f"{label}.entity_type: 未定義の entity type です: {entity_type}")
        operation = row["operation"]
        if isinstance(operation, str) and operation and operation not in ALLOWED_OPERATIONS:
            errors.append(f"{label}.operation: 未定義の operation です: {operation}")
        expected_entity_types = EVENT_ENTITY_TYPE_RULES.get(event_type)
        if expected_entity_types is not None and entity_type not in expected_entity_types:
            expected = ", ".join(sorted(expected_entity_types))
            errors.append(f"{label}.entity_type: {event_type} では {expected} にしてください: {entity_type}")
        if isinstance(row["timestamp"], str) and row["timestamp"]:
            parse_timestamp(row["timestamp"], f"{label}.timestamp", errors)

        usage = parse_json(row["structured_data_usage_json"], f"{label}.structured_data_usage_json", errors)
        if isinstance(usage, dict):
            for key in STRUCTURED_DATA_USAGE_FIELDS:
                if key not in usage:
                    errors.append(f"{label}.structured_data_usage_json.{key}: 必須 field です。")
            for key in ("structured_inputs", "evidence_refs"):
                if not isinstance(usage.get(key), list):
                    errors.append(f"{label}.structured_data_usage_json.{key}: list にしてください。")
            if usage.get("decision_rationale") is not None and not isinstance(usage.get("decision_rationale"), str):
                errors.append(f"{label}.structured_data_usage_json.decision_rationale: null または文字列にしてください。")

        payload = parse_json(row["payload_json"], f"{label}.payload_json", errors)
        if isinstance(payload, dict):
            audit_entity_payload_identity(
                payload,
                entity_type=entity_type,
                entity_id=row["entity_id"],
                workflow_id=row["workflow_id"],
                label=f"{label}.payload_json",
                errors=errors,
            )
        safety = parse_json(row["event_safety_json"], f"{label}.event_safety_json", errors)
        if isinstance(safety, dict):
            for key in EVENT_SAFETY_FIELDS:
                if key not in safety:
                    errors.append(f"{label}.event_safety_json.{key}: 必須 field です。")
            for key in EVENT_SAFETY_FIELDS:
                if not isinstance(safety.get(key), list):
                    errors.append(f"{label}.event_safety_json.{key}: list にしてください。")
            if isinstance(payload, dict):
                if entity_type == "message":
                    validate_message_trust(payload, f"{label}.payload_json", errors)
                if memory_candidate_envelope(payload) is not None and entity_type != "message":
                    errors.append(f"{label}.entity_type: memory candidate event は message entity で記録してください。")
                validate_memory_candidate_event_safety(payload, safety, f"{label}.event_safety_json", errors, row["actor"])


def audit_rank_and_trial_values(connection: sqlite3.Connection, errors: list[str]) -> None:
    for table, id_column in (("quests", "quest_id"),):
        try:
            rows = connection.execute(f"SELECT {id_column}, rank FROM {table} WHERE rank IS NOT NULL").fetchall()
        except sqlite3.Error as exc:
            errors.append(f"{table}: rank 読み取りに失敗しました: {exc}")
            continue
        for row in rows:
            if row["rank"] not in QUEST_RANKS:
                errors.append(f"{table}[{row[id_column]}].rank: 未定義の Quest Rank です: {row['rank']}")

    try:
        rows = connection.execute("SELECT trial_id, depth FROM trials WHERE depth IS NOT NULL").fetchall()
    except sqlite3.Error as exc:
        errors.append(f"trials: depth 読み取りに失敗しました: {exc}")
        return
    for row in rows:
        if row["depth"] not in TRIAL_DEPTHS:
            errors.append(f"trials[{row['trial_id']}].depth: 未定義の Trial depth です: {row['depth']}")


def audit_status_values(connection: sqlite3.Connection, errors: list[str]) -> None:
    checks = (
        ("quests", "quest_id", QUEST_STATUSES),
        ("requests", "request_id", REQUEST_STATUSES),
        ("commands", "command_id", COMMAND_STATUSES),
        ("assignments", "assignment_id", ASSIGNMENT_STATUSES),
        ("reports", "report_id", REPORT_STATUSES),
        ("trials", "trial_id", TRIAL_STATUSES),
        ("inbox_messages", "message_id", MESSAGE_STATUSES),
    )
    for table, id_column, allowed in checks:
        try:
            rows = connection.execute(f"SELECT {id_column}, status FROM {table}").fetchall()
        except sqlite3.Error as exc:
            errors.append(f"{table}: status読み取りに失敗しました: {exc}")
            continue
        for row in rows:
            if row["status"] not in allowed:
                errors.append(f"{table}[{row[id_column]}].status: 未定義値です: {row['status']}")


def audit_materialized_columns(connection: sqlite3.Connection, errors: list[str]) -> None:
    specs = {
        "quests": ("quest_id", "quest", {"workflow_id": "workflow_id", "rank": "rank", "status": "status"}),
        "requests": ("request_id", "request", {"quest_id": "quest_id", "workflow_id": "workflow_id", "status": "status"}),
        "commands": ("command_id", "command", {"quest_id": "quest_id", "workflow_id": "workflow_id", "status": "status"}),
        "assignments": (
            "assignment_id",
            "assignment",
            {"worker_id": "worker_id", "kind": "kind", "workflow_id": "workflow_id", "status": "status"},
        ),
        "reports": (
            "report_id",
            "report",
            {"worker_id": "worker_id", "workflow_id": "workflow_id", "decision": "decision", "status": "status"},
        ),
        "trials": (
            "trial_id",
            "trial",
            {"quest_id": "quest_id", "workflow_id": "workflow_id", "depth": "depth", "status": "status"},
        ),
        "inbox_messages": (
            "message_id",
            "message",
            {"recipient": "recipient", "workflow_id": "workflow_id", "status": "status", "created_at": "created_at"},
        ),
    }
    id_aliases = {
        "quest": ("id", "quest_id"),
        "request": ("id", "request_id"),
        "command": ("id", "command_id"),
        "assignment": ("id", "assignment_id"),
        "report": ("id", "report_id"),
        "trial": ("id", "trial_id"),
        "message": ("id", "message_id"),
    }
    for table, (id_column, entity_type, columns) in specs.items():
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        for row in rows:
            label = f"{table}[{row[id_column]}]"
            payload = parse_json(row["payload_json"], f"{label}.payload_json", errors)
            if not isinstance(payload, dict):
                continue
            payload_id = next((payload.get(key) for key in id_aliases[entity_type] if payload.get(key) is not None), None)
            if payload_id != row[id_column]:
                errors.append(f"{label}: materialized idとpayload idが一致しません。")
            for column, payload_key in columns.items():
                if row[column] != payload.get(payload_key):
                    errors.append(f"{label}.{column}: materialized valueとpayload.{payload_key}が一致しません。")
            latest_event = connection.execute(
                "SELECT payload_json FROM events WHERE entity_type = ? AND entity_id = ? ORDER BY rowid DESC LIMIT 1",
                (entity_type, row[id_column]),
            ).fetchone()
            if latest_event is None:
                errors.append(f"{label}: 対応するeventがありません。")
                continue
            event_payload = parse_json(latest_event["payload_json"], f"events[{entity_type}={row[id_column]}].payload_json", errors)
            canonical_event_payload = event_payload.get(entity_type) if isinstance(event_payload, dict) and isinstance(event_payload.get(entity_type), dict) else event_payload
            if canonical_event_payload != payload:
                errors.append(f"{label}: latest event payloadとmaterialized payloadが一致しません。")


def audit_helper_snapshot(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != SNAPSHOT_FIELDS:
        errors.append(f"{label}: canonical snapshot fieldsが必要です。")
        return
    if value.get("digest_version") != "agent-guild-orchestra-snapshot-v1" or value.get("kind") not in {"revision_only", "working_tree_content", "commit_range"}:
        errors.append(f"{label}: digest_version/kindが不正です。")
    if not isinstance(value.get("snapshot_id"), str) or SHA256_ID_RE.fullmatch(value["snapshot_id"]) is None:
        errors.append(f"{label}.snapshot_id: helper形式sha256が必要です。")
    if not isinstance(value.get("revision_id"), str) or COMMIT_OID_RE.fullmatch(value["revision_id"]) is None:
        errors.append(f"{label}.revision_id: commit OIDが必要です。")
    audit_relative_path_list(value.get("scope_paths"), f"{label}.scope_paths", errors)
    audit_relative_path_list(value.get("untracked_paths"), f"{label}.untracked_paths", errors)
    kind = value.get("kind")
    if value.get("dirty_state") not in {"clean", "dirty"}:
        errors.append(f"{label}.dirty_state: clean/dirtyが必要です。")
    if kind == "revision_only":
        if value.get("dirty_state") != "clean" or value.get("base_ref") is not None or value.get("head_ref") is not None or value.get("diff_hash") is not None or value.get("untracked_paths") != []:
            errors.append(f"{label}: revision_only canonical invariantが不正です。")
    elif kind in {"working_tree_content", "commit_range"}:
        if not isinstance(value.get("diff_hash"), str) or SHA256_ID_RE.fullmatch(value["diff_hash"]) is None or value.get("snapshot_id") != value.get("diff_hash"):
            errors.append(f"{label}: content snapshot id/hashが不正です。")
        if not isinstance(value.get("base_ref"), str) or COMMIT_OID_RE.fullmatch(value["base_ref"]) is None or not value.get("scope_paths"):
            errors.append(f"{label}: content snapshot base/scopeが不正です。")
        if kind == "working_tree_content" and value.get("head_ref") is not None:
            errors.append(f"{label}.head_ref: working_tree_contentではnullが必要です。")
        if kind == "commit_range" and (
            not isinstance(value.get("head_ref"), str)
            or COMMIT_OID_RE.fullmatch(value["head_ref"]) is None
            or value.get("untracked_paths") != []
        ):
            errors.append(f"{label}: commit_range head/untracked invariantが不正です。")


def audit_trial_contracts(connection: sqlite3.Connection, errors: list[str]) -> None:
    rows = connection.execute("SELECT trial_id, quest_id, workflow_id, depth, status, payload_json FROM trials").fetchall()
    for row in rows:
        label = f"trials[{row['trial_id']}]"
        payload = parse_json(row["payload_json"], f"{label}.payload_json", errors)
        if not isinstance(payload, dict):
            continue
        if payload.get("worker_id") != "inquisitor" or payload.get("role") != "trial_lead":
            errors.append(f"{label}: worker_id/roleはinquisitor/trial_leadにしてください。")
        if not isinstance(payload.get("objective"), str) or not payload.get("objective"):
            errors.append(f"{label}.objective: 空でない文字列が必要です。")
        audit_nonempty_string_list(payload.get("success_criteria"), f"{label}.success_criteria", errors)
        expected_authority = {"read": True, "edit": False, "validate": True, "local_git": False, "external_actions": False}
        if payload.get("authority") != expected_authority:
            errors.append(f"{label}.authority: read-only validation authorityが必要です。")
        boundaries = payload.get("boundaries")
        if not isinstance(boundaries, dict) or not isinstance(boundaries.get("target_repo_root"), str) or not boundaries.get("target_repo_root"):
            errors.append(f"{label}.boundaries.target_repo_root: 空でない文字列が必要です。")
        audit_helper_snapshot(payload.get("subject_snapshot"), f"{label}.subject_snapshot", errors)
        assignment_ids = payload.get("subject_assignment_ids")
        report_ids = payload.get("subject_report_ids")
        if not isinstance(assignment_ids, list) or not assignment_ids or not all(isinstance(item, str) and item for item in assignment_ids):
            errors.append(f"{label}.subject_assignment_ids: non-empty文字列listが必要です。")
            assignment_ids = []
        if not isinstance(report_ids, list) or not all(isinstance(item, str) and item for item in report_ids):
            errors.append(f"{label}.subject_report_ids: 文字列listが必要です。")
            report_ids = []
        for assignment_id in assignment_ids:
            assignment = connection.execute("SELECT workflow_id, status, payload_json FROM assignments WHERE assignment_id = ?", (assignment_id,)).fetchone()
            assignment_payload = parse_json(assignment["payload_json"], f"assignments[{assignment_id}].payload_json", errors) if assignment else None
            if assignment is None or assignment["workflow_id"] != row["workflow_id"] or not isinstance(assignment_payload, dict) or assignment_payload.get("quest_id") != row["quest_id"] or assignment["status"] != "done":
                errors.append(f"{label}: subject assignment lineage/statusが不正です: {assignment_id}")
        for report_id in report_ids:
            report = connection.execute("SELECT workflow_id, status, payload_json FROM reports WHERE report_id = ?", (report_id,)).fetchone()
            report_payload = parse_json(report["payload_json"], f"reports[{report_id}].payload_json", errors) if report else None
            if report is None or report["workflow_id"] != row["workflow_id"] or not isinstance(report_payload, dict) or report_payload.get("quest_id") != row["quest_id"] or report["status"] not in {"recorded", "accepted"} or report_payload.get("assignment_id") not in assignment_ids:
                errors.append(f"{label}: subject report lineage/statusが不正です: {report_id}")


def audit_examiner_reports(connection: sqlite3.Connection, errors: list[str]) -> None:
    rows = connection.execute("SELECT report_id, workflow_id, status, payload_json FROM reports WHERE worker_id = 'examiner'").fetchall()
    for row in rows:
        label = f"reports[{row['report_id']}]"
        payload = parse_json(row["payload_json"], f"{label}.payload_json", errors)
        if not isinstance(payload, dict):
            continue
        assignment_id = payload.get("assignment_id")
        trial_id = payload.get("trial_id")
        assignment = connection.execute(
            "SELECT workflow_id, status, payload_json FROM assignments WHERE assignment_id = ? AND worker_id = 'examiner'",
            (assignment_id,),
        ).fetchone()
        if assignment is None:
            errors.append(f"{label}: 参照examiner assignmentがありません: {assignment_id}")
            continue
        assignment_payload = parse_json(assignment["payload_json"], f"assignments[{assignment_id}].payload_json", errors)
        trial = connection.execute("SELECT quest_id, workflow_id, payload_json FROM trials WHERE trial_id = ?", (trial_id,)).fetchone()
        trial_payload = parse_json(trial["payload_json"], f"trials[{trial_id}].payload_json", errors) if trial else None
        lineage = assignment_payload.get("caller_lineage") if isinstance(assignment_payload, dict) else None
        expected_caller_check = {
            "required_parent_role": "inquisitor",
            "trial_owner_worker_id": "inquisitor",
            "trial_ref": trial_id,
            "verified": True,
            "status": "verified",
        }
        if (
            trial is None
            or not isinstance(assignment_payload, dict)
            or not isinstance(trial_payload, dict)
            or assignment["workflow_id"] != row["workflow_id"]
            or assignment["status"] != "done"
            or assignment_payload.get("trial_id") != trial_id
            or assignment_payload.get("quest_id") != payload.get("quest_id")
            or assignment_payload.get("owner_worker_id") != "inquisitor"
            or not isinstance(lineage, dict)
            or lineage.get("verification") != "verified"
            or trial["quest_id"] != payload.get("quest_id")
            or trial["workflow_id"] != row["workflow_id"]
            or trial_payload.get("worker_id") != "inquisitor"
            or assignment_payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
            or payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
            or payload.get("focus") != assignment_payload.get("focus")
            or payload.get("risk_trigger") != assignment_payload.get("risk_trigger")
            or payload.get("caller_lineage_check") != expected_caller_check
            or payload.get("snapshot_check") != {"start_match": True, "report_match": True, "status": "matched"}
        ):
            errors.append(f"{label}: assignment/Trial/quest/workflow/snapshot/verified lineageが不正です。")
        if payload.get("owner_worker_id") != "inquisitor":
            errors.append(f"{label}.owner_worker_id: inquisitorが必要です。")
        if payload.get("terminal_worker") is not True or payload.get("decision_authority") is not False or payload.get("severity_authority") is not False:
            errors.append(f"{label}: terminal/non-decision/non-severity contractが不正です。")
        if not isinstance(payload.get("summary"), str) or not payload.get("summary"):
            errors.append(f"{label}.summary: 空でない文字列が必要です。")
        audit_nonempty_string_list(payload.get("evidence_refs"), f"{label}.evidence_refs", errors)
        candidate_ids = audit_evidence_items(payload.get("finding_candidates"), f"{label}.finding_candidates", errors)
        unknown_ids = audit_evidence_items(payload.get("important_unknowns"), f"{label}.important_unknowns", errors)
        if candidate_ids.intersection(unknown_ids):
            errors.append(f"{label}: finding candidate/important unknown IDが重複しています。")
        audit_helper_snapshot(payload.get("subject_snapshot"), f"{label}.subject_snapshot", errors)


def audit_inquisitor_reports(connection: sqlite3.Connection, errors: list[str]) -> None:
    rows = connection.execute("SELECT report_id, workflow_id, status, payload_json FROM reports WHERE worker_id = 'inquisitor'").fetchall()
    for row in rows:
        label = f"reports[{row['report_id']}]"
        payload = parse_json(row["payload_json"], f"{label}.payload_json", errors)
        if not isinstance(payload, dict):
            continue
        trial_id = payload.get("trial_id")
        trial = connection.execute("SELECT quest_id, workflow_id, depth, status, payload_json FROM trials WHERE trial_id = ?", (trial_id,)).fetchone()
        if trial is None:
            errors.append(f"{label}: 参照Trialがありません: {trial_id}")
            continue
        trial_payload = parse_json(trial["payload_json"], f"trials[{trial_id}].payload_json", errors)
        if (
            not isinstance(trial_payload, dict)
            or trial["quest_id"] != payload.get("quest_id")
            or trial["workflow_id"] != row["workflow_id"]
            or trial["status"] not in {"active", "done"}
            or payload.get("trial_depth") != trial["depth"]
            or payload.get("target_repo_root") != trial_payload.get("boundaries", {}).get("target_repo_root")
            or payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
        ):
            errors.append(f"{label}: quest/workflow/depth/target/snapshotが参照Trialと一致しません。")
        decision = payload.get("decision")
        if decision is not None and decision not in TRIAL_DECISIONS:
            errors.append(f"{label}.decision: 不正です。")
        if row["status"] in {"recorded", "accepted"} and decision not in TRIAL_DECISIONS:
            errors.append(f"{label}.decision: 完了reportにはdecisionが必要です。")
        if row["status"] in {"recorded", "accepted"}:
            if not isinstance(payload.get("summary"), str) or not payload.get("summary"):
                errors.append(f"{label}.summary: 空でない文字列が必要です。")
            audit_nonempty_string_list(payload.get("evidence_refs"), f"{label}.evidence_refs", errors)
            assignment_rows = connection.execute(
                "SELECT assignment_id, status, payload_json FROM assignments WHERE worker_id = 'examiner' AND workflow_id = ?",
                (row["workflow_id"],),
            ).fetchall()
            expected_report_ids: set[str] = set()
            evidence_item_ids: set[str] = set()
            for assignment_row in assignment_rows:
                assignment_payload = parse_json(assignment_row["payload_json"], f"assignments[{assignment_row['assignment_id']}].payload_json", errors)
                if not isinstance(assignment_payload, dict) or assignment_payload.get("trial_id") != trial_id:
                    continue
                if assignment_row["status"] != "done":
                    errors.append(f"{label}: examiner assignmentが未完了です: {assignment_row['assignment_id']}")
                examiner_rows = connection.execute(
                    "SELECT report_id, status, payload_json FROM reports WHERE worker_id = 'examiner' AND workflow_id = ?",
                    (row["workflow_id"],),
                ).fetchall()
                matching_ids: list[str] = []
                for examiner_row in examiner_rows:
                    examiner_payload = parse_json(examiner_row["payload_json"], f"reports[{examiner_row['report_id']}].payload_json", errors)
                    if not isinstance(examiner_payload, dict) or examiner_payload.get("assignment_id") != assignment_row["assignment_id"] or examiner_row["status"] not in {"recorded", "accepted"}:
                        continue
                    caller_check = examiner_payload.get("caller_lineage_check")
                    if (
                        examiner_payload.get("trial_id") != trial_id
                        or examiner_payload.get("quest_id") != payload.get("quest_id")
                        or not isinstance(trial_payload, dict)
                        or examiner_payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
                        or caller_check != {
                            "required_parent_role": "inquisitor",
                            "trial_owner_worker_id": "inquisitor",
                            "trial_ref": trial_id,
                            "verified": True,
                            "status": "verified",
                        }
                    ):
                        errors.append(f"{label}: examiner report lineage/snapshotが不正です: {examiner_row['report_id']}")
                    report_item_ids = audit_evidence_items(
                        examiner_payload.get("finding_candidates"),
                        f"reports[{examiner_row['report_id']}].finding_candidates",
                        errors,
                    )
                    unknown_ids = audit_evidence_items(
                        examiner_payload.get("important_unknowns"),
                        f"reports[{examiner_row['report_id']}].important_unknowns",
                        errors,
                    )
                    if report_item_ids.intersection(unknown_ids):
                        errors.append(f"{label}: examiner report内のevidence item IDが重複しています: {examiner_row['report_id']}")
                    report_item_ids.update(unknown_ids)
                    duplicate_ids = evidence_item_ids.intersection(report_item_ids)
                    if duplicate_ids:
                        errors.append(f"{label}: Trial内のexaminer evidence item IDが重複しています: {sorted(duplicate_ids)[0]}")
                    evidence_item_ids.update(report_item_ids)
                    matching_ids.append(examiner_row["report_id"])
                if not matching_ids:
                    errors.append(f"{label}: 完了examiner reportがありません: {assignment_row['assignment_id']}")
                expected_report_ids.update(matching_ids)
            examiner_refs = payload.get("examiner_reports")
            if not isinstance(examiner_refs, list) or not all(isinstance(item, str) and item for item in examiner_refs) or len(examiner_refs) != len(set(examiner_refs)) or set(examiner_refs) != expected_report_ids:
                errors.append(f"{label}.examiner_reports: 完了examiner reportの完全集合ではありません。")
            audit_finding_dispositions(
                payload.get("finding_dispositions"),
                evidence_item_ids,
                decision,
                f"{label}.finding_dispositions",
                errors,
            )
        audit_helper_snapshot(payload.get("subject_snapshot"), f"{label}.subject_snapshot", errors)


def audit_retired_agent_columns(connection: sqlite3.Connection, errors: list[str]) -> None:
    checks = (
        ("events", "event_id", "actor"),
        ("assignments", "assignment_id", "worker_id"),
        ("reports", "report_id", "worker_id"),
        ("inbox_messages", "message_id", "recipient"),
    )
    for table, id_column, value_column in checks:
        try:
            rows = connection.execute(f"SELECT {id_column}, {value_column} FROM {table}").fetchall()
        except sqlite3.Error as exc:
            errors.append(f"{table}: {value_column} 読み取りに失敗しました: {exc}")
            continue
        for row in rows:
            value = row[value_column]
            if isinstance(value, str) and value in RETIRED_AGENT_VALUES:
                errors.append(f"{table}[{row[id_column]}].{value_column}: 廃止済み agent 値 `{value}` が残っています。")


def audit_assignment_parent_contract(connection: sqlite3.Connection, errors: list[str]) -> None:
    try:
        rows = connection.execute("SELECT assignment_id, parent_id, workflow_id, payload_json FROM assignments").fetchall()
    except sqlite3.Error as exc:
        errors.append(f"assignments: parent_id 読み取りに失敗しました: {exc}")
        return
    for row in rows:
        label = f"assignments[{row['assignment_id']}].payload_json"
        payload = parse_json(row["payload_json"], label, errors)
        if not isinstance(payload, dict):
            continue
        audit_assignment_machine_contract(payload, label, errors)
        expected_parent_id = expected_assignment_parent_id(payload, label, errors)
        if row["parent_id"] != expected_parent_id:
            errors.append(
                f"assignments[{row['assignment_id']}].parent_id: payload の owner/parent relation と一致しません: "
                f"{row['parent_id']} != {expected_parent_id}"
            )
        worker_id = payload.get("worker_id")
        workflow_id = payload.get("workflow_id") or row["workflow_id"]
        quest_id = payload.get("quest_id")

        if worker_id in {"sage", "warden"}:
            owner_id = payload.get("owner_assignment_id") or payload.get("parent_id")
            owner = connection.execute(
                "SELECT worker_id, workflow_id, payload_json FROM assignments WHERE assignment_id = ?",
                (owner_id,),
            ).fetchone()
            if owner is None:
                errors.append(f"{label}: assignment ownerがLedgerにありません: {owner_id}")
            else:
                owner_payload = parse_json(owner["payload_json"], f"assignments[{owner_id}].payload_json", errors)
                if owner["workflow_id"] != workflow_id or not isinstance(owner_payload, dict) or owner_payload.get("quest_id") != quest_id:
                    errors.append(f"{label}: assignment ownerのquest/workflow lineageが一致しません。")
                if payload.get("owner_worker_id") != owner["worker_id"]:
                    errors.append(f"{label}.owner_worker_id: Ledger上のownerと一致しません。")

        if worker_id == "examiner":
            lineage = payload.get("caller_lineage")
            if not isinstance(lineage, dict):
                continue
            trial_ref = lineage.get("trial_ref")
            if payload.get("trial_id") != trial_ref:
                errors.append(f"{label}: trial_idとcaller_lineage.trial_refが一致しません。")
            trial = connection.execute(
                "SELECT quest_id, workflow_id, payload_json FROM trials WHERE trial_id = ?",
                (trial_ref,),
            ).fetchone()
            if trial is None:
                errors.append(f"{label}: 参照TrialがLedgerにありません: {trial_ref}")
            else:
                trial_payload = parse_json(trial["payload_json"], f"trials[{trial_ref}].payload_json", errors)
                if not isinstance(trial_payload, dict) or trial_payload.get("worker_id") != "inquisitor":
                    errors.append(f"{label}: 参照Trial ownerはinquisitorにしてください。")
                if trial["quest_id"] != quest_id or trial["workflow_id"] != workflow_id:
                    errors.append(f"{label}: quest/workflowが参照Trialと一致しません。")
                if lineage.get("required_parent_role") != "inquisitor" or lineage.get("trial_owner_worker_id") != "inquisitor":
                    errors.append(f"{label}: caller_lineageはinquisitor Trialを指してください。")
                if isinstance(trial_payload, dict):
                    subject_ids = trial_payload.get("subject_assignment_ids")
                    if not isinstance(subject_ids, list) or not subject_ids or not all(isinstance(item, str) and item for item in subject_ids):
                        errors.append(f"{label}: 参照Trialにはsubject_assignment_idsが必要です。")
                    else:
                        for subject_id in subject_ids:
                            subject = connection.execute(
                                "SELECT workflow_id, payload_json FROM assignments WHERE assignment_id = ?",
                                (subject_id,),
                            ).fetchone()
                            if subject is None:
                                errors.append(f"{label}: Trial subject assignmentがありません: {subject_id}")
                                continue
                            subject_payload = parse_json(subject["payload_json"], f"assignments[{subject_id}].payload_json", errors)
                            if subject["workflow_id"] != workflow_id or not isinstance(subject_payload, dict) or subject_payload.get("quest_id") != quest_id:
                                errors.append(f"{label}: Trial subject assignmentのquest/workflowが一致しません: {subject_id}")
                    if trial_payload.get("subject_snapshot") != payload.get("subject_snapshot"):
                        errors.append(f"{label}: assignmentとTrialのsubject_snapshotが一致しません。")
                    trial_boundaries = trial_payload.get("boundaries")
                    child_boundaries = payload.get("boundaries")
                    if not isinstance(trial_boundaries, dict) or not isinstance(child_boundaries, dict):
                        errors.append(f"{label}: Trial/examiner boundariesが不正です。")
                    else:
                        if child_boundaries.get("target_repo_root") != trial_boundaries.get("target_repo_root"):
                            errors.append(f"{label}: target_repo_rootが参照Trialと一致しません。")
                        for key in ("read_deny", "edit_deny", "safety_items"):
                            parent_items = trial_boundaries.get(key)
                            child_items = child_boundaries.get(key)
                            if not isinstance(parent_items, list) or not isinstance(child_items, list):
                                errors.append(f"{label}: Trial/examiner boundaries.{key}はlistにしてください。")
                            elif any(item not in child_items for item in parent_items):
                                errors.append(f"{label}: 親Trialのboundaries.{key}を削除できません。")
                    if payload.get("authority") != trial_payload.get("authority"):
                        errors.append(f"{label}: authorityが親Trialのread-only authorityと一致しません。")

        if worker_id == "artificer":
            barrier = payload.get("integration_barrier")
            if not isinstance(barrier, dict):
                continue
            refs = barrier.get("upstream_report_refs")
            if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
                continue
            if len(refs) != len(set(refs)):
                errors.append(f"{label}.integration_barrier.upstream_report_refs: 重複があります。")
            contract_ref = barrier.get("contract_ref")
            contract_row = connection.execute(
                "SELECT quest_id, workflow_id, status, payload_json FROM commands WHERE command_id = ?",
                (contract_ref,),
            ).fetchone()
            if contract_row is None:
                errors.append(f"{label}: integration barrier contractがLedgerにありません: {contract_ref}")
            else:
                command_payload = parse_json(contract_row["payload_json"], f"commands[{contract_ref}].payload_json", errors)
                contract = command_payload.get("integration_contract") if isinstance(command_payload, dict) else None
                required_refs = contract.get("required_report_refs") if isinstance(contract, dict) else None
                required_assignments = contract.get("required_assignment_ids") if isinstance(contract, dict) else None
                integration_scope = contract.get("integration_scope") if isinstance(contract, dict) else None
                if (
                    contract_row["quest_id"] != quest_id
                    or contract_row["workflow_id"] != workflow_id
                    or contract_row["status"] not in {"issued", "active"}
                    or not isinstance(contract, dict)
                    or contract.get("artificer") != "artificer"
                    or contract.get("mutation_barrier_required") is not True
                    or not isinstance(required_refs, list)
                    or not required_refs
                    or not all(isinstance(item, str) and item for item in required_refs)
                    or not isinstance(required_assignments, list)
                    or not required_assignments
                    or not all(isinstance(item, str) and item for item in required_assignments)
                    or not isinstance(integration_scope, dict)
                    or set(integration_scope) != {"read", "edit", "validate"}
                ):
                    errors.append(f"{label}: integration barrier command契約が不正です。")
                elif len(required_refs) != len(set(required_refs)) or set(required_refs) != set(refs):
                    errors.append(f"{label}: upstream_report_refsがcommandのrequired_report_refs完全集合と一致しません。")
                else:
                    for scope_key in ("read", "edit", "validate"):
                        audit_relative_path_list(integration_scope[scope_key], f"{label}.integration_scope.{scope_key}", errors, nonempty=scope_key == "edit")
                    if payload.get("owned_scope") != integration_scope:
                        errors.append(f"{label}: artificer scopeがcommand contractと一致しません。")
            source_assignment_ids: list[str] = []
            for report_id in refs:
                report = connection.execute(
                    "SELECT worker_id, workflow_id, status, payload_json FROM reports WHERE report_id = ?",
                    (report_id,),
                ).fetchone()
                if report is None:
                    errors.append(f"{label}: upstream reportがLedgerにありません: {report_id}")
                    continue
                report_payload = parse_json(report["payload_json"], f"reports[{report_id}].payload_json", errors)
                if report["worker_id"] not in {"adventurer", "artificer"}:
                    errors.append(f"{label}: upstream report ownerが実装workerではありません: {report_id}")
                if report["workflow_id"] != workflow_id or not isinstance(report_payload, dict) or report_payload.get("quest_id") != quest_id:
                    errors.append(f"{label}: upstream report lineageが一致しません: {report_id}")
                if report["status"] not in {"recorded", "accepted"}:
                    errors.append(f"{label}: upstream reportが完了していません: {report_id}")
                if not isinstance(report_payload, dict):
                    continue
                source_assignment_id = report_payload.get("assignment_id")
                if not isinstance(source_assignment_id, str) or not source_assignment_id:
                    errors.append(f"{label}: upstream reportにassignment_idがありません: {report_id}")
                    continue
                source = connection.execute(
                    "SELECT worker_id, workflow_id, status, payload_json FROM assignments WHERE assignment_id = ?",
                    (source_assignment_id,),
                ).fetchone()
                if source is None:
                    errors.append(f"{label}: source assignmentがありません: {source_assignment_id}")
                    continue
                source_payload = parse_json(source["payload_json"], f"assignments[{source_assignment_id}].payload_json", errors)
                if (
                    source["worker_id"] != report["worker_id"]
                    or source["workflow_id"] != workflow_id
                    or not isinstance(source_payload, dict)
                    or source_payload.get("quest_id") != quest_id
                    or source["status"] != "done"
                ):
                    errors.append(f"{label}: source assignment lineage/statusが不正です: {report_id}")
                elif report_payload.get("target_repo_root") != source_payload.get("boundaries", {}).get("target_repo_root"):
                    errors.append(f"{label}: report targetがsource assignmentと一致しません: {report_id}")
                elif report_payload.get("base_snapshot") != source_payload.get("subject_snapshot"):
                    errors.append(f"{label}: report base snapshotがsource assignmentと一致しません: {report_id}")
                result_snapshot = report_payload.get("result_snapshot")
                if not isinstance(result_snapshot, dict) or set(result_snapshot) != SNAPSHOT_FIELDS:
                    errors.append(f"{label}: upstream reportにcanonical result_snapshotがありません: {report_id}")
                elif isinstance(source_payload, dict) and isinstance(source_payload.get("owned_scope"), dict):
                    source_edit = source_payload["owned_scope"].get("edit")
                    integration_read = integration_scope.get("read") if isinstance(integration_scope, dict) else None
                    result_scopes = result_snapshot.get("scope_paths")
                    if isinstance(source_edit, list) and isinstance(integration_read, list) and any(
                        isinstance(path, str) and not path_covered_by_scopes(path, integration_read) for path in source_edit
                    ):
                        errors.append(f"{label}: integration read scopeがsource edit scopeを包含しません: {source_assignment_id}")
                    if isinstance(source_edit, list) and isinstance(result_scopes, list) and any(
                        isinstance(path, str) and not path_covered_by_scopes(path, result_scopes) for path in source_edit
                    ):
                        errors.append(f"{label}: result snapshotがsource edit scopeを包含しません: {report_id}")
                source_assignment_ids.append(source_assignment_id)
            if contract_row is not None and isinstance(command_payload, dict) and isinstance(contract, dict):
                required_assignments = contract.get("required_assignment_ids")
                if (
                    isinstance(required_assignments, list)
                    and all(isinstance(item, str) and item for item in required_assignments)
                    and (
                        len(required_assignments) != len(set(required_assignments))
                        or len(source_assignment_ids) != len(set(source_assignment_ids))
                        or set(source_assignment_ids) != set(required_assignments)
                    )
                ):
                    errors.append(f"{label}: source assignmentsがcommandのrequired_assignment_ids完全集合と一致しません。")

def main() -> int:
    args = parse_args()
    database_path = args.runtime_root / "queue" / SQLITE_DB_NAME
    guild_root = args.runtime_root.resolve(strict=False).parent
    errors: list[str] = []

    with connect_read(database_path) as connection:
        schema_ok = audit_schema(connection, args.static_root, errors)
        counts: dict[str, int] = {}
        if schema_ok:
            audit_metadata(connection, errors)
            counts = audit_json_columns(connection, guild_root, errors)
            audit_events(connection, errors)
            audit_rank_and_trial_values(connection, errors)
            audit_status_values(connection, errors)
            audit_materialized_columns(connection, errors)
            audit_trial_contracts(connection, errors)
            audit_examiner_reports(connection, errors)
            audit_inquisitor_reports(connection, errors)
            audit_retired_agent_columns(connection, errors)
            audit_assignment_parent_contract(connection, errors)

    result = {"ok": not errors, "database": str(database_path), "counts": counts, "errors": errors}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
    else:
        print("ledger audit: ok")
        print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
