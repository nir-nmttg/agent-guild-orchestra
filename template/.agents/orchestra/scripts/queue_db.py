#!/usr/bin/env python3
"""Guild-native runtime 向け SQLite Ledger 補助。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Any


QUEUE_SCHEMA_VERSION = "4.0"
QUEUE_SCHEMA_SHA256 = "a883418f960b28bc84381ed00fe4a9b55eb870e50a41fa26122ace122acb7c7c"
DEFAULT_DB_NAME = "state.sqlite"
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
ALLOWED_OPERATIONS = {"append", "update", "replace", "mark_completed"}
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
REQUIRED_INBOX_MESSAGE_FIELDS = {"id", "sender", "recipient", "created_at", "type", "trusted", "payload", "status"}
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
LEGACY_TABLES = {"tickets"}
LEGACY_COLUMNS = {"assignments": {"task_id"}}
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


def static_root() -> Path:
    return Path(__file__).resolve().parent.parent


def guild_root() -> Path:
    return static_root().parent.parent


def default_runtime_root() -> Path:
    env_value = os.environ.get("AGENT_GUILD_ORCHESTRA_RUNTIME_ROOT")
    if env_value:
        return Path(env_value).expanduser()
    return guild_root() / ".orchestra"


def db_path(runtime_root: Path) -> Path:
    return runtime_root / "queue" / DEFAULT_DB_NAME


def schema_path() -> Path:
    return Path(__file__).resolve().with_name("queue_schema.sql")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def connect_write(runtime_root: Path) -> sqlite3.Connection:
    path = db_path(runtime_root)
    canonical_schema_signature()
    ensure_existing_schema_compatible(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def connect_read(runtime_root: Path) -> sqlite3.Connection:
    path = db_path(runtime_root)
    if not path.exists():
        raise SystemExit(f"SQLite runtime DB がありません: {path}。`queue_db.py init` を実行してください。")
    connection = connect_existing_read_only(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    ensure_schema_compatible(connection)
    return connection


def connect_existing_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def schema_mismatch_message(schema_errors: list[str]) -> str:
    return (
        f"SQLite runtime DB の schema contract が {QUEUE_SCHEMA_VERSION} と一致しません。"
        + "自動 migration は行いません。`--backup --reset-runtime` または `--clean-install` を使ってください: "
        + "; ".join(schema_errors)
    )


def existing_tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


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


def canonical_schema_signature() -> tuple[tuple[str, str, str, str], ...]:
    path = schema_path()
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != QUEUE_SCHEMA_SHA256:
        raise SystemExit(
            f"queue_schema.sql SHA-256 がv{QUEUE_SCHEMA_VERSION} contractと一致しません: {digest}"
        )
    try:
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(content.decode("utf-8"))
            return schema_signature(expected)
    except (UnicodeDecodeError, sqlite3.DatabaseError) as exc:
        raise SystemExit(f"canonical queue_schema.sqlを検証できません: {exc}") from exc


def collect_signature_errors(connection: sqlite3.Connection) -> list[str]:
    expected = canonical_schema_signature()
    actual = schema_signature(connection)
    if actual == expected:
        return []
    expected_by_name = {(item[0], item[1]): item for item in expected}
    actual_by_name = {(item[0], item[1]): item for item in actual}
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
    return ["canonical table/index/constraint signature不一致: " + "; ".join(details)]


def init_db(connection: sqlite3.Connection) -> None:
    existing = bool(existing_tables(connection))
    if existing:
        ensure_schema_compatible(connection)
    connection.executescript(schema_path().read_text(encoding="utf-8"))
    schema_errors = collect_schema_errors(connection)
    if schema_errors:
        raise SystemExit(schema_mismatch_message(schema_errors))
    if not existing:
        connection.execute(
            "INSERT INTO queue_metadata(key, value) VALUES('schema_version', ?)",
            (QUEUE_SCHEMA_VERSION,),
        )
    connection.commit()


def collect_schema_errors(connection: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    tables = existing_tables(connection)
    legacy_tables = sorted(LEGACY_TABLES & tables)
    if legacy_tables:
        errors.append("旧 table が残っています: " + ", ".join(legacy_tables))
    managed_tables = {table for table in tables if not table.startswith("sqlite_")}
    unexpected_tables = sorted(managed_tables - REQUIRED_TABLES - LEGACY_TABLES)
    if unexpected_tables:
        errors.append("未知 table が残っています: " + ", ".join(unexpected_tables))
    missing_tables = sorted(REQUIRED_TABLES - tables)
    if missing_tables:
        errors.append("不足 table: " + ", ".join(missing_tables))
    for table, required_columns in REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            errors.append(f"{table} の不足 column: " + ", ".join(missing_columns))
        legacy_columns = sorted(LEGACY_COLUMNS.get(table, set()) & columns)
        if legacy_columns:
            errors.append(f"{table} の旧 column: " + ", ".join(legacy_columns))
        unexpected_columns = sorted(columns - required_columns - LEGACY_COLUMNS.get(table, set()))
        if unexpected_columns:
            errors.append(f"{table} の未知 column: " + ", ".join(unexpected_columns))
    errors.extend(collect_signature_errors(connection))
    return errors


def collect_schema_version_errors(connection: sqlite3.Connection) -> list[str]:
    tables = existing_tables(connection)
    if "queue_metadata" not in tables:
        return ["queue_metadata.schema_version がありません"]
    columns = {row[1] for row in connection.execute("PRAGMA table_info(queue_metadata)")}
    if not {"key", "value"} <= columns:
        return []
    row = connection.execute(
        "SELECT value FROM queue_metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is None or row[0] is None:
        return ["queue_metadata.schema_version がありません"]
    actual = str(row[0])
    if actual != QUEUE_SCHEMA_VERSION:
        return [f"queue_metadata.schema_version は {QUEUE_SCHEMA_VERSION} が必要です: {actual}"]
    return []


def ensure_schema_compatible(connection: sqlite3.Connection) -> None:
    schema_errors = collect_schema_errors(connection)
    if not schema_errors:
        schema_errors.extend(collect_schema_version_errors(connection))
    if schema_errors:
        raise SystemExit(schema_mismatch_message(schema_errors))


def ensure_existing_schema_compatible(path: Path) -> None:
    if not path.exists():
        return
    with connect_existing_read_only(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        ensure_schema_compatible(connection)


def read_json_arg(raw: str) -> Any:
    if raw == "-":
        raw = sys.stdin.read()
    elif not raw.lstrip().startswith(("{", "[")):
        path = Path(raw).expanduser()
        if path.exists() and path.is_file():
            raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON を読めません: {exc}") from exc


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(f"{label} は JSON object にしてください。")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{label} は空でない文字列にしてください。")
    return value


def require_string_or_null(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return require_string(value, label)


def require_status(value: Any, allowed: set[str], label: str) -> str:
    status = require_string(value, label)
    if status not in allowed:
        raise SystemExit(f"{label} は許可値にしてください: {status}")
    return status


def parse_timestamp(value: Any, label: str) -> None:
    raw = require_string(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"{label} は ISO 8601 形式にしてください: {raw}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit(f"{label} は timezone offset 付きにしてください: {raw}")


def payload_body(event: dict[str, Any]) -> dict[str, Any]:
    return require_mapping(event.get("payload"), "event.payload")


def event_entity(event: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    entity = require_mapping(event.get("entity"), "event.entity")
    entity_type = require_string(entity.get("type") or entity.get("kind"), "event.entity.type")
    entity_id = require_string(entity.get("id"), "event.entity.id")
    return entity_type, entity_id, entity


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


def iter_values(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    findings: list[tuple[str, Any]] = [(path, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            findings.extend(iter_values(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(iter_values(item, f"{path}[{index}]"))
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


def memory_candidate_envelope(payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if payload.get("type") == MEMORY_CANDIDATE_MESSAGE_TYPE:
        body = require_mapping(payload.get("payload"), "message.payload")
        marker_path = memory_candidate_marker_path(body, "message.payload")
        if marker_path is not None:
            raise SystemExit(
                "memory candidate は message.type を "
                f"{MEMORY_CANDIDATE_MESSAGE_TYPE} にした courier review 専用 envelope として記録してください: {marker_path}"
            )
        return ("message.payload", body)
    marker_path = memory_candidate_marker_path(payload, "message")
    if marker_path is not None:
        raise SystemExit(
            "memory candidate は message.type を "
            f"{MEMORY_CANDIDATE_MESSAGE_TYPE} にした courier review 専用 envelope として記録してください: {marker_path}"
        )
    return None


def validate_memory_candidate_message_scope(payload: dict[str, Any], actor: Any | None = None) -> None:
    if payload.get("sender") != "courier":
        raise SystemExit("memory candidate の message.sender は courier にしてください。")
    if payload.get("recipient") != "courier":
        raise SystemExit("memory candidate の message.recipient は courier にしてください。")
    if payload.get("trusted") is not False:
        raise SystemExit("memory candidate の message.trusted は false にしてください。")
    if actor is not None and actor != "courier":
        raise SystemExit("memory candidate の event.actor は courier にしてください。")


def validate_memory_candidate_envelope(envelope: dict[str, Any], label: str) -> None:
    if envelope.get("explicit_memory_persistence_authority") is not True:
        raise SystemExit(f"{label}.explicit_memory_persistence_authority は true にしてください。")
    if envelope.get("sanitized_summary_only") is not True:
        raise SystemExit(f"{label}.sanitized_summary_only は true にしてください。")
    require_string(envelope.get("sanitized_summary"), f"{label}.sanitized_summary")
    artifact = require_mapping(envelope.get("prevention_artifact"), f"{label}.prevention_artifact")
    require_string(artifact.get("kind"), f"{label}.prevention_artifact.kind")
    if not any(isinstance(artifact.get(key), str) and artifact.get(key) for key in ("ref", "description")):
        raise SystemExit(f"{label}.prevention_artifact.ref または {label}.prevention_artifact.description が必要です。")
    require_string(envelope.get("ledger_disposition"), f"{label}.ledger_disposition")
    forbidden = require_mapping(envelope.get("forbidden"), f"{label}.forbidden")
    missing_markers = sorted(MEMORY_CANDIDATE_REQUIRED_FORBIDDEN_MARKERS - set(forbidden))
    if missing_markers:
        raise SystemExit(f"{label}.forbidden に必要な marker がありません: " + ", ".join(missing_markers))
    for marker in MEMORY_CANDIDATE_REQUIRED_FORBIDDEN_MARKERS:
        if forbidden.get(marker) is not True:
            raise SystemExit(f"{label}.forbidden.{marker} は true にしてください。")
    for json_path, key in iter_keys(envelope):
        if json_path.startswith("$.forbidden."):
            continue
        if key in MEMORY_CANDIDATE_REQUIRED_FORBIDDEN_MARKERS:
            raise SystemExit(f"{label}{json_path[1:]}: memory candidate に forbidden 内容 `{key}` を含めないでください。")


def validate_memory_candidate_event_safety(payload: dict[str, Any], event_safety: Any, actor: Any | None = None) -> None:
    if memory_candidate_envelope(payload) is None:
        return
    validate_memory_candidate_message_scope(payload, actor)
    safety = require_mapping(event_safety, "event.event_safety")
    safety_items = safety.get("safety_items")
    if not isinstance(safety_items, list):
        raise SystemExit("event.event_safety.safety_items は list にしてください。")
    missing = sorted(set(MEMORY_CANDIDATE_SAFETY_ITEMS) - set(safety_items))
    if missing:
        raise SystemExit("event.event_safety.safety_items に memory_candidate_gate が不足しています: " + ", ".join(missing))


def validate_no_legacy_runtime_shape(value: Any, label: str) -> None:
    for json_path, key in iter_keys(value):
        if key in LEGACY_JSON_KEYS or key in LEGACY_RUNTIME_STRING_VALUES:
            raise SystemExit(f"{label}{json_path[1:]}: 廃止済み key `{key}` が残っています。")
    for json_path, item in iter_values(value):
        if isinstance(item, str) and item in LEGACY_RUNTIME_STRING_VALUES:
            raise SystemExit(f"{label}{json_path[1:]}: 廃止済み runtime 値 `{item}` が残っています。")


def validate_target_repo_roots(value: Any, label: str, runtime_guild_root: Path) -> None:
    root = runtime_guild_root.resolve(strict=False)
    repositories_root = root / "repositories"
    for json_path, raw_path in iter_target_repo_roots(value):
        value_label = f"{label}{json_path[1:]}"
        if not isinstance(raw_path, str) or not raw_path:
            raise SystemExit(f"{value_label}: target_repo_root は空でない絶対 path 文字列にしてください。")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            raise SystemExit(f"{value_label}: target_repo_root は絶対 path にしてください: {raw_path}")
        normalized = candidate.resolve(strict=False)
        if normalized == root:
            raise SystemExit(f"{value_label}: target_repo_root にギルド規約ルート自体は指定できません: {raw_path}")
        if normalized == repositories_root:
            raise SystemExit(f"{value_label}: target_repo_root に repositories/ 自体は指定できません: {raw_path}")
        if normalized.parent != repositories_root:
            raise SystemExit(f"{value_label}: target_repo_root は <guild_root>/repositories/<repo> の直下 path にしてください: {raw_path}")


def validate_structured_data_usage(value: Any) -> None:
    usage = require_mapping(value, "event.structured_data_usage")
    for key in STRUCTURED_DATA_USAGE_FIELDS:
        if key not in usage:
            raise SystemExit(f"event.structured_data_usage.{key} がありません。")
    for key in ("structured_inputs", "evidence_refs"):
        if not isinstance(usage.get(key), list):
            raise SystemExit(f"event.structured_data_usage.{key} は list にしてください。")
    if usage.get("decision_rationale") is not None and not isinstance(usage.get("decision_rationale"), str):
        raise SystemExit("event.structured_data_usage.decision_rationale は null または文字列にしてください。")


def validate_event_safety(value: Any) -> None:
    safety = require_mapping(value, "event.event_safety")
    for key in EVENT_SAFETY_FIELDS:
        if key not in safety:
            raise SystemExit(f"event.event_safety.{key} がありません。")
    for key in EVENT_SAFETY_FIELDS:
        if not isinstance(safety.get(key), list):
            raise SystemExit(f"event.event_safety.{key} は list にしてください。")


def validate_entity_payload_identity(event: dict[str, Any], payload: dict[str, Any]) -> None:
    entity_type, entity_id, _entity = event_entity(event)
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
            raise SystemExit(f"event.payloadのcanonical {entity_type} idがありません。")
        if payload_id != entity_id:
            raise SystemExit(f"event.entity.idとpayloadのcanonical idが一致しません: {entity_id} != {payload_id}")
    payload_workflow_id = canonical.get("workflow_id")
    if payload_workflow_id is not None and payload_workflow_id != event.get("workflow_id"):
        raise SystemExit("event.workflow_idとpayload.workflow_idを一致させてください。")


def validate_event_input(event: dict[str, Any], runtime_guild_root: Path) -> None:
    for field in REQUIRED_EVENT_INPUT_FIELDS:
        if field not in event:
            raise SystemExit(f"event.{field} がありません。")
    require_string(event["event_id"], "event.event_id")
    require_string(event["actor"], "event.actor")
    event_type = require_string(event["event_type"], "event.event_type")
    operation = require_string(event["operation"], "event.operation")
    require_string_or_null(event.get("workflow_id"), "event.workflow_id")
    parse_timestamp(event["timestamp"], "event.timestamp")
    validate_structured_data_usage(event["structured_data_usage"])
    validate_event_safety(event["event_safety"])
    entity_type, _entity_id, _entity = event_entity(event)
    if event_type not in ALLOWED_EVENT_TYPES:
        raise SystemExit(f"event.event_type は許可値にしてください: {event_type}")
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise SystemExit(f"event.entity.type は許可値にしてください: {entity_type}")
    if operation not in ALLOWED_OPERATIONS:
        expected = ", ".join(sorted(ALLOWED_OPERATIONS))
        raise SystemExit(f"event.operation は {expected} のいずれかにしてください: {operation}")
    expected_entity_types = EVENT_ENTITY_TYPE_RULES.get(event_type)
    if expected_entity_types is not None and entity_type not in expected_entity_types:
        expected = ", ".join(sorted(expected_entity_types))
        raise SystemExit(f"event.entity.type は {event_type} では {expected} にしてください: {entity_type}")
    payload = payload_body(event)
    if memory_candidate_envelope(payload) is not None:
        if entity_type != "message":
            raise SystemExit("memory candidate event の event.entity.type は message にしてください。")
        validate_memory_candidate_event_safety(payload, event["event_safety"], event.get("actor"))
    validate_entity_payload_identity(event, payload)
    validate_target_repo_roots(event, "event", runtime_guild_root)
    validate_no_legacy_runtime_shape(event, "event")


def insert_event(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    event_id = require_string(event.get("event_id"), "event.event_id")
    entity_type, entity_id, entity = event_entity(event)
    payload = payload_body(event)
    row_values = (
        event_id,
        require_string(event.get("timestamp"), "event.timestamp"),
        require_string(event.get("actor"), "event.actor"),
        require_string(event.get("event_type"), "event.event_type"),
        entity_type,
        entity_id,
        json_dumps(entity),
        require_string(event.get("operation"), "event.operation"),
        event.get("workflow_id"),
        json_dumps(event.get("structured_data_usage") or {}),
        json_dumps(payload),
        json_dumps(event.get("event_safety") or {}),
    )
    existing = connection.execute("SELECT event_id FROM events WHERE event_id = ?", (event_id,)).fetchone()
    if existing is not None:
        raise SystemExit(f"event_id が重複しています: {event_id}")
    connection.execute(
        """
        INSERT INTO events(
          event_id, timestamp, actor, event_type, entity_type, entity_id, entity_json,
          operation, workflow_id, structured_data_usage_json, payload_json, event_safety_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row_values,
    )


def nested_payload(payload: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = payload.get(name)
        if isinstance(value, dict):
            return value
    return payload


def assignment_parent_id(payload: dict[str, Any]) -> str | None:
    owner_assignment_id = require_string_or_null(payload.get("owner_assignment_id"), "assignment.owner_assignment_id")
    parent_id = require_string_or_null(payload.get("parent_id"), "assignment.parent_id")
    if owner_assignment_id and parent_id and owner_assignment_id != parent_id:
        raise SystemExit("assignment.owner_assignment_id と assignment.parent_id は同じ owner assignment を指してください。")
    worker_id = payload.get("worker_id")
    kind = payload.get("kind")
    if (worker_id == "sage" or kind == "sage_consultation") and not (owner_assignment_id or parent_id):
        raise SystemExit("sage assignment は owner_assignment_id または parent_id が必要です。")
    if worker_id == "warden" or kind in {"quest_awareness_control_monitor", "evidence_state_monitor"}:
        if not (owner_assignment_id or parent_id):
            raise SystemExit("warden assignment は owner_assignment_id または parent_id が必要です。")
        require_string(payload.get("control_trigger"), "assignment.control_trigger")
    return owner_assignment_id or parent_id or require_string_or_null(payload.get("quest_id"), "assignment.quest_id")


def require_nonempty_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise SystemExit(f"{label} は空でない文字列のnon-empty listにしてください。")
    return value


def require_evidence_items(value: Any, label: str) -> set[str]:
    if not isinstance(value, list):
        raise SystemExit(f"{label} はevidence item listにしてください。")
    item_ids: set[str] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict) or set(item) != {"id", "summary"}:
            raise SystemExit(f"{item_label} はid/summaryだけを持つobjectにしてください。")
        item_id = require_string(item.get("id"), f"{item_label}.id")
        require_string(item.get("summary"), f"{item_label}.summary")
        if item_id in item_ids:
            raise SystemExit(f"{label} のidは重複させないでください: {item_id}")
        item_ids.add(item_id)
    return item_ids


def require_finding_dispositions(value: Any, expected_ids: set[str], label: str, decision: str) -> None:
    dispositions = require_mapping(value, label)
    if set(dispositions) != {"adopted", "rejected", "unresolved"}:
        raise SystemExit(f"{label} はadopted/rejected/unresolvedのcanonical fieldsにしてください。")
    seen: set[str] = set()
    for key in ("adopted", "rejected", "unresolved"):
        refs = dispositions.get(key)
        if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
            raise SystemExit(f"{label}.{key} はevidence item ID listにしてください。")
        if len(refs) != len(set(refs)):
            raise SystemExit(f"{label}.{key} のIDは重複させないでください。")
        overlap = seen.intersection(refs)
        if overlap:
            raise SystemExit(f"{label} のIDはexactly once処置してください: {sorted(overlap)[0]}")
        seen.update(refs)
    if seen != expected_ids:
        missing = sorted(expected_ids - seen)
        unknown = sorted(seen - expected_ids)
        raise SystemExit(f"{label} は全candidate/unknown IDをexactly once処置してください: missing={missing}, unknown={unknown}")
    if decision in {"accept", "accept_with_risks"} and dispositions["unresolved"]:
        raise SystemExit(f"decision={decision} ではunresolved finding dispositionを残せません。")


def require_relative_path_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise SystemExit(f"{label} は{qualifier}repo-relative path listにしてください。")
    for item in value:
        if not isinstance(item, str) or not item:
            raise SystemExit(f"{label} は空でないrepo-relative path文字列だけにしてください。")
        path = Path(item)
        if path.is_absolute() or any(part in {"", ".", "..", ".git"} for part in path.parts):
            raise SystemExit(f"{label} に安全でないrepo-relative pathがあります: {item}")
    return value


def path_covered_by_scopes(path: str, scopes: list[str]) -> bool:
    path_parts = Path(path).parts
    return any(path_parts[: len(Path(scope).parts)] == Path(scope).parts for scope in scopes)


def verify_snapshot_with_helper(payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
    target_repo_root = Path(payload["boundaries"]["target_repo_root"])
    command = [
        sys.executable,
        "-I",
        str(Path(__file__).resolve().with_name("snapshot_digest.py")),
        "--repo",
        str(target_repo_root),
        "--kind",
        snapshot["kind"],
    ]
    if snapshot.get("base_ref") is not None:
        command.extend(["--base-ref", snapshot["base_ref"]])
    if snapshot.get("head_ref") is not None:
        command.extend(["--head-ref", snapshot["head_ref"]])
    for scope in snapshot["scope_paths"]:
        command.extend(["--scope", scope])
    for path in snapshot["untracked_paths"]:
        command.extend(["--untracked", path])
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("assignment.subject_snapshot のhelper再計算がtimeoutしました。") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"assignment.subject_snapshot をtarget Git rootで再検証できません: {detail}")
    try:
        actual = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit("snapshot helperがcanonical JSONを返しませんでした。") from exc
    if actual != snapshot:
        raise SystemExit("assignment.subject_snapshot がtarget Git rootのhelper出力と一致しません。")


def validate_helper_snapshot(value: Any, label: str, payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = require_mapping(value, label)
    if set(snapshot) != SNAPSHOT_FIELDS:
        raise SystemExit(f"{label} はcanonical snapshot fieldsにしてください。")
    if snapshot.get("digest_version") != "agent-guild-orchestra-snapshot-v1" or snapshot.get("kind") not in {"revision_only", "working_tree_content", "commit_range"}:
        raise SystemExit(f"{label} のdigest_version/kindが不正です。")
    for key in ("scope_paths", "untracked_paths"):
        require_relative_path_list(snapshot.get(key), f"{label}.{key}")
    for key in ("base_ref", "head_ref"):
        if snapshot.get(key) is not None and not isinstance(snapshot.get(key), str):
            raise SystemExit(f"{label}.{key} はnullまたは文字列にしてください。")
    verify_snapshot_with_helper(payload, snapshot)
    return snapshot


def validate_trial_machine_contract(connection: sqlite3.Connection, payload: dict[str, Any], event: dict[str, Any]) -> None:
    if payload.get("worker_id") != "inquisitor" or payload.get("role") != "trial_lead":
        raise SystemExit("trial worker_id/roleはinquisitor/trial_leadにしてください。")
    require_string(payload.get("objective"), "trial.objective")
    require_nonempty_string_list(payload.get("success_criteria"), "trial.success_criteria")
    authority = require_mapping(payload.get("authority"), "trial.authority")
    if set(authority) != AUTHORITY_FIELDS or not all(isinstance(authority[key], bool) for key in AUTHORITY_FIELDS):
        raise SystemExit("trial.authorityはcanonical bool fieldsにしてください。")
    if authority != {"read": True, "edit": False, "validate": True, "local_git": False, "external_actions": False}:
        raise SystemExit("inquisitor Trialはread-only validation authorityにしてください。")
    boundaries = require_mapping(payload.get("boundaries"), "trial.boundaries")
    require_string(boundaries.get("target_repo_root"), "trial.boundaries.target_repo_root")
    for key in ("read_deny", "edit_deny", "safety_items"):
        if not isinstance(boundaries.get(key), list):
            raise SystemExit(f"trial.boundaries.{key}はlistにしてください。")
    validate_helper_snapshot(payload.get("subject_snapshot"), "trial.subject_snapshot", payload)

    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    quest_id = require_string(payload.get("quest_id"), "trial.quest_id")
    assignment_ids = require_nonempty_string_list(payload.get("subject_assignment_ids"), "trial.subject_assignment_ids")
    report_ids = payload.get("subject_report_ids")
    if not isinstance(report_ids, list) or not all(isinstance(item, str) and item for item in report_ids):
        raise SystemExit("trial.subject_report_idsは文字列listにしてください。")
    if len(assignment_ids) != len(set(assignment_ids)) or len(report_ids) != len(set(report_ids)):
        raise SystemExit("Trial subject IDsは重複させないでください。")
    for assignment_id in assignment_ids:
        assignment = connection.execute(
            "SELECT workflow_id, status, payload_json FROM assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        if assignment is None:
            raise SystemExit(f"Trial subject assignmentがLedgerにありません: {assignment_id}")
        assignment_payload = json.loads(assignment["payload_json"])
        if assignment["workflow_id"] != workflow_id or assignment_payload.get("quest_id") != quest_id or assignment["status"] != "done":
            raise SystemExit(f"Trial subject assignmentのquest/workflow/statusが不正です: {assignment_id}")
    for report_id in report_ids:
        report = connection.execute(
            "SELECT workflow_id, status, payload_json FROM reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if report is None:
            raise SystemExit(f"Trial subject reportがLedgerにありません: {report_id}")
        report_payload = json.loads(report["payload_json"])
        if (
            report["workflow_id"] != workflow_id
            or report_payload.get("quest_id") != quest_id
            or report["status"] not in {"recorded", "accepted"}
            or report_payload.get("assignment_id") not in assignment_ids
        ):
            raise SystemExit(f"Trial subject reportのlineage/statusが不正です: {report_id}")


def validate_inquisitor_report_contract(connection: sqlite3.Connection, payload: dict[str, Any], event: dict[str, Any]) -> None:
    if payload.get("worker_id") != "inquisitor":
        return
    trial_id = require_string(payload.get("trial_id"), "report.trial_id")
    trial = connection.execute(
        "SELECT quest_id, workflow_id, depth, status, payload_json FROM trials WHERE trial_id = ?",
        (trial_id,),
    ).fetchone()
    if trial is None:
        raise SystemExit(f"inquisitor reportが参照するTrialがLedgerにありません: {trial_id}")
    trial_payload = json.loads(trial["payload_json"])
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    if (
        trial["quest_id"] != payload.get("quest_id")
        or trial["workflow_id"] != workflow_id
        or trial["status"] not in {"active", "done"}
        or payload.get("trial_depth") != trial["depth"]
        or payload.get("target_repo_root") != trial_payload.get("boundaries", {}).get("target_repo_root")
        or payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
    ):
        raise SystemExit("inquisitor reportのquest/workflow/depth/target/snapshotが参照Trialと一致しません。")
    decision = payload.get("decision")
    if decision is not None and decision not in TRIAL_DECISIONS:
        raise SystemExit("inquisitor report.decisionが不正です。")
    if payload.get("status") in {"recorded", "accepted"} and decision not in TRIAL_DECISIONS:
        raise SystemExit("完了inquisitor reportにはdecisionが必要です。")
    if payload.get("status") in {"recorded", "accepted"}:
        require_string(payload.get("summary"), "inquisitor report.summary")
        require_nonempty_string_list(payload.get("evidence_refs"), "inquisitor report.evidence_refs")
        assignment_rows = connection.execute(
            "SELECT assignment_id, status, payload_json FROM assignments WHERE worker_id = 'examiner' AND workflow_id = ?",
            (workflow_id,),
        ).fetchall()
        examiner_assignments: dict[str, dict[str, Any]] = {}
        for row in assignment_rows:
            assignment_payload = json.loads(row["payload_json"])
            if assignment_payload.get("trial_id") == trial_id:
                if row["status"] != "done":
                    raise SystemExit(f"inquisitor final reportには完了examiner assignmentが必要です: {row['assignment_id']}")
                examiner_assignments[row["assignment_id"]] = assignment_payload

        completed_report_ids: set[str] = set()
        evidence_item_ids: set[str] = set()
        for assignment_id, assignment_payload in examiner_assignments.items():
            report_rows = connection.execute(
                "SELECT report_id, status, payload_json FROM reports WHERE worker_id = 'examiner' AND workflow_id = ?",
                (workflow_id,),
            ).fetchall()
            matching_reports: list[str] = []
            for report_row in report_rows:
                report_payload = json.loads(report_row["payload_json"])
                if report_payload.get("assignment_id") != assignment_id or report_row["status"] not in {"recorded", "accepted"}:
                    continue
                caller_check = report_payload.get("caller_lineage_check")
                if (
                    report_payload.get("trial_id") != trial_id
                    or report_payload.get("quest_id") != payload.get("quest_id")
                    or report_payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
                    or caller_check != {
                        "required_parent_role": "inquisitor",
                        "trial_owner_worker_id": "inquisitor",
                        "trial_ref": trial_id,
                        "verified": True,
                        "status": "verified",
                    }
                ):
                    raise SystemExit(f"examiner reportのTrial lineage/snapshotが不正です: {report_row['report_id']}")
                report_item_ids = require_evidence_items(
                    report_payload.get("finding_candidates"),
                    f"examiner report[{report_row['report_id']}].finding_candidates",
                )
                unknown_ids = require_evidence_items(
                    report_payload.get("important_unknowns"),
                    f"examiner report[{report_row['report_id']}].important_unknowns",
                )
                if report_item_ids.intersection(unknown_ids):
                    raise SystemExit(f"examiner report内のevidence item IDは重複させないでください: {report_row['report_id']}")
                report_item_ids.update(unknown_ids)
                duplicate_ids = evidence_item_ids.intersection(report_item_ids)
                if duplicate_ids:
                    raise SystemExit(f"Trial内のexaminer evidence item IDは重複させないでください: {sorted(duplicate_ids)[0]}")
                evidence_item_ids.update(report_item_ids)
                matching_reports.append(report_row["report_id"])
            if not matching_reports:
                raise SystemExit(f"inquisitor final reportに対応する完了examiner reportがありません: {assignment_id}")
            completed_report_ids.update(matching_reports)

        examiner_report_refs = payload.get("examiner_reports")
        if not isinstance(examiner_report_refs, list) or not all(isinstance(item, str) and item for item in examiner_report_refs):
            raise SystemExit("inquisitor report.examiner_reportsはreport ID listにしてください。")
        if len(examiner_report_refs) != len(set(examiner_report_refs)) or set(examiner_report_refs) != completed_report_ids:
            raise SystemExit("inquisitor report.examiner_reportsは完了examiner reportの完全集合にしてください。")
        require_finding_dispositions(
            payload.get("finding_dispositions"),
            evidence_item_ids,
            "inquisitor report.finding_dispositions",
            decision,
        )
    validate_helper_snapshot(payload.get("subject_snapshot"), "report.subject_snapshot", trial_payload)


def validate_examiner_report_contract(connection: sqlite3.Connection, payload: dict[str, Any], event: dict[str, Any]) -> None:
    if payload.get("worker_id") != "examiner":
        return
    assignment_id = require_string(payload.get("assignment_id"), "examiner report.assignment_id")
    trial_id = require_string(payload.get("trial_id"), "examiner report.trial_id")
    assignment = connection.execute(
        "SELECT workflow_id, status, payload_json FROM assignments WHERE assignment_id = ? AND worker_id = 'examiner'",
        (assignment_id,),
    ).fetchone()
    if assignment is None:
        raise SystemExit(f"examiner reportが参照するassignmentがLedgerにありません: {assignment_id}")
    assignment_payload = json.loads(assignment["payload_json"])
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    lineage = assignment_payload.get("caller_lineage")
    if (
        assignment["workflow_id"] != workflow_id
        or assignment["status"] != "done"
        or assignment_payload.get("trial_id") != trial_id
        or assignment_payload.get("quest_id") != payload.get("quest_id")
        or assignment_payload.get("owner_worker_id") != "inquisitor"
        or not isinstance(lineage, dict)
        or lineage.get("verification") != "verified"
    ):
        raise SystemExit("examiner reportのassignment lineage/statusが不正です。")
    trial = connection.execute(
        "SELECT quest_id, workflow_id, payload_json FROM trials WHERE trial_id = ?",
        (trial_id,),
    ).fetchone()
    if trial is None:
        raise SystemExit(f"examiner reportが参照するTrialがLedgerにありません: {trial_id}")
    trial_payload = json.loads(trial["payload_json"])
    if (
        trial["quest_id"] != payload.get("quest_id")
        or trial["workflow_id"] != workflow_id
        or trial_payload.get("worker_id") != "inquisitor"
        or assignment_payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
        or payload.get("subject_snapshot") != trial_payload.get("subject_snapshot")
        or payload.get("focus") != assignment_payload.get("focus")
        or payload.get("risk_trigger") != assignment_payload.get("risk_trigger")
    ):
        raise SystemExit("examiner reportのquest/workflow/Trial/snapshot/focus lineageが不正です。")
    if payload.get("terminal_worker") is not True or payload.get("decision_authority") is not False or payload.get("severity_authority") is not False:
        raise SystemExit("examiner reportはterminalかつnon-decision/non-severityにしてください。")
    require_string(payload.get("summary"), "examiner report.summary")
    require_nonempty_string_list(payload.get("evidence_refs"), "examiner report.evidence_refs")
    candidate_ids = require_evidence_items(payload.get("finding_candidates"), "examiner report.finding_candidates")
    unknown_ids = require_evidence_items(payload.get("important_unknowns"), "examiner report.important_unknowns")
    if candidate_ids.intersection(unknown_ids):
        raise SystemExit("examiner reportのfinding candidate/important unknown IDは重複させないでください。")
    payload["owner_worker_id"] = "inquisitor"
    payload["caller_lineage_check"] = {
        "required_parent_role": "inquisitor",
        "trial_owner_worker_id": "inquisitor",
        "trial_ref": trial_id,
        "verified": True,
        "status": "verified",
    }
    payload["snapshot_check"] = {"start_match": True, "report_match": True, "status": "matched"}
    validate_helper_snapshot(payload.get("subject_snapshot"), "report.subject_snapshot", trial_payload)


def validate_assignment_machine_contract(payload: dict[str, Any]) -> None:
    """helper管理の安全境界がないmanaged assignmentを拒否する。"""

    worker_id = require_string(payload.get("worker_id"), "assignment.worker_id")
    if worker_id not in TERMINAL_ASSIGNMENT_WORKERS:
        raise SystemExit(f"assignment.worker_id は managed terminal worker にしてください: {worker_id}")
    if payload.get("terminal_worker") is not True:
        raise SystemExit("assignment.terminal_worker は true にしてください。")
    expected_role = EXPECTED_ASSIGNMENT_ROLES[worker_id]
    if payload.get("role") != expected_role:
        raise SystemExit(f"assignment.role は worker_id={worker_id} に対応する {expected_role} にしてください。")
    require_string(payload.get("objective"), "assignment.objective")

    authority = require_mapping(payload.get("authority"), "assignment.authority")
    if set(authority) != AUTHORITY_FIELDS or not all(isinstance(authority[key], bool) for key in AUTHORITY_FIELDS):
        raise SystemExit("assignment.authority は bool の canonical 5 fields にしてください。")
    if authority["external_actions"] is True:
        raise SystemExit("assignment.authority.external_actions は assignment 作成時に許可できません。")
    if authority["read"] is not True:
        raise SystemExit("assignment.authority.read はmanaged assignmentでtrueにしてください。")
    if authority["local_git"] is True:
        raise SystemExit("assignment.authority.local_git はqueueから付与できません。最新の人間指示をsession authorityとして別途確認してください。")
    if worker_id in READ_ONLY_ASSIGNMENT_WORKERS and authority["edit"] is True:
        raise SystemExit(f"assignment.authority.edit は read-only worker {worker_id} に許可できません。")

    boundaries = require_mapping(payload.get("boundaries"), "assignment.boundaries")
    require_string(boundaries.get("target_repo_root"), "assignment.boundaries.target_repo_root")
    for key in ("read_deny", "edit_deny", "safety_items"):
        if not isinstance(boundaries.get(key), list):
            raise SystemExit(f"assignment.boundaries.{key} は list にしてください。")

    snapshot = require_mapping(payload.get("subject_snapshot"), "assignment.subject_snapshot")
    if set(snapshot) != SNAPSHOT_FIELDS:
        raise SystemExit("assignment.subject_snapshot は canonical snapshot fields にしてください。")
    snapshot_id = require_string(snapshot.get("snapshot_id"), "assignment.subject_snapshot.snapshot_id")
    if SHA256_ID_RE.fullmatch(snapshot_id) is None:
        raise SystemExit("assignment.subject_snapshot.snapshot_id は helper形式のsha256にしてください。")
    if snapshot.get("digest_version") != "agent-guild-orchestra-snapshot-v1":
        raise SystemExit("assignment.subject_snapshot.digest_version は agent-guild-orchestra-snapshot-v1 にしてください。")
    if snapshot.get("kind") not in {"revision_only", "working_tree_content", "commit_range"}:
        raise SystemExit("assignment.subject_snapshot.kind が不正です。")
    kind = snapshot["kind"]
    revision_id = snapshot.get("revision_id")
    if not isinstance(revision_id, str) or COMMIT_OID_RE.fullmatch(revision_id) is None:
        raise SystemExit("assignment.subject_snapshot.revision_id はcommit OIDにしてください。")
    if snapshot.get("dirty_state") not in {"clean", "dirty"}:
        raise SystemExit("assignment.subject_snapshot.dirty_state は clean / dirty にしてください。")
    diff_hash = snapshot.get("diff_hash")
    if kind == "revision_only":
        if (
            diff_hash is not None
            or snapshot.get("base_ref") is not None
            or snapshot.get("head_ref") is not None
            or snapshot.get("dirty_state") != "clean"
            or snapshot.get("untracked_paths") != []
        ):
            raise SystemExit("revision_only snapshotはcleanで、diff/base/headがnull、untracked_pathsが空である必要があります。")
    else:
        if not isinstance(diff_hash, str) or SHA256_ID_RE.fullmatch(diff_hash) is None or snapshot_id != diff_hash:
            raise SystemExit("content snapshotのsnapshot_id/diff_hashは同じhelper形式sha256にしてください。")
        if not isinstance(snapshot.get("base_ref"), str) or COMMIT_OID_RE.fullmatch(snapshot["base_ref"]) is None:
            raise SystemExit("content snapshot.base_refはcommit OIDにしてください。")
        if not snapshot.get("scope_paths"):
            raise SystemExit("content snapshot.scope_pathsはnon-emptyにしてください。")
        if kind == "working_tree_content" and snapshot.get("head_ref") is not None:
            raise SystemExit("working_tree_content snapshot.head_refはnullにしてください。")
        if kind == "commit_range":
            if not isinstance(snapshot.get("head_ref"), str) or COMMIT_OID_RE.fullmatch(snapshot["head_ref"]) is None:
                raise SystemExit("commit_range snapshot.head_refはcommit OIDにしてください。")
            if snapshot.get("untracked_paths") != []:
                raise SystemExit("commit_range snapshot.untracked_pathsは空にしてください。")
    for key in ("scope_paths", "untracked_paths"):
        require_relative_path_list(snapshot.get(key), f"assignment.subject_snapshot.{key}")

    if worker_id in {"adventurer", "artificer"}:
        require_nonempty_string_list(payload.get("success_criteria"), "assignment.success_criteria")
        owned_scope = require_mapping(payload.get("owned_scope"), "assignment.owned_scope")
        if set(owned_scope) != {"read", "edit", "validate"}:
            raise SystemExit("assignment.owned_scope は read/edit/validate のcanonical fieldsにしてください。")
        require_relative_path_list(owned_scope["read"], "assignment.owned_scope.read")
        require_relative_path_list(owned_scope["edit"], "assignment.owned_scope.edit", nonempty=True)
        require_relative_path_list(owned_scope["validate"], "assignment.owned_scope.validate")
        if any(not path_covered_by_scopes(path, owned_scope["read"]) for path in owned_scope["edit"]):
            raise SystemExit("assignment.owned_scope.readは全edit pathを包含してください。")
        if authority["edit"] is not True or authority["validate"] is not True:
            raise SystemExit(f"{worker_id} assignmentにはedit/validate authorityが必要です。")
    elif worker_id in {"cartographer", "guildmaster", "captain", "inquisitor"}:
        require_nonempty_string_list(payload.get("success_criteria"), "assignment.success_criteria")

    if worker_id in {"sage", "examiner"}:
        require_string(payload.get("focus"), "assignment.focus")
        require_nonempty_string_list(payload.get("evidence_required"), "assignment.evidence_required")
        if worker_id == "examiner":
            require_string(payload.get("risk_trigger"), "assignment.risk_trigger")
    elif worker_id in {"cartographer", "warden"}:
        require_nonempty_string_list(payload.get("evidence_required"), "assignment.evidence_required")

    if worker_id == "artificer":
        barrier = require_mapping(payload.get("integration_barrier"), "assignment.integration_barrier")
        if barrier.get("status") != "complete" or barrier.get("mutation_stopped") is not True:
            raise SystemExit("artificer assignmentにはcompleteかつmutation_stoppedのintegration barrierが必要です。")
        require_nonempty_string_list(barrier.get("upstream_report_refs"), "assignment.integration_barrier.upstream_report_refs")
        require_string(barrier.get("contract_ref"), "assignment.integration_barrier.contract_ref")
    elif payload.get("integration_barrier") is not None:
        raise SystemExit("assignment.integration_barrier は artificer だけに指定できます。")

    if worker_id == "examiner":
        if payload.get("owner_worker_id") != "inquisitor":
            raise SystemExit("examiner assignment.owner_worker_id は inquisitor にしてください。")
        lineage = require_mapping(payload.get("caller_lineage"), "assignment.caller_lineage")
        require_string(lineage.get("trial_ref"), "assignment.caller_lineage.trial_ref")

    verify_snapshot_with_helper(payload, snapshot)


def validate_assignment_relations(connection: sqlite3.Connection, payload: dict[str, Any], event: dict[str, Any]) -> None:
    worker_id = payload["worker_id"]
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    quest_id = payload.get("quest_id")

    owner_assignment_id = payload.get("owner_assignment_id") or payload.get("parent_id")
    if worker_id in {"sage", "warden"}:
        if not isinstance(owner_assignment_id, str) or not owner_assignment_id:
            raise SystemExit(f"{worker_id} assignmentにはowner_assignment_idまたはparent_idが必要です。")
        owner = connection.execute(
            "SELECT worker_id, workflow_id, payload_json FROM assignments WHERE assignment_id = ?",
            (owner_assignment_id,),
        ).fetchone()
        if owner is None:
            raise SystemExit(f"assignment ownerがLedgerにありません: {owner_assignment_id}")
        owner_payload = json.loads(owner["payload_json"])
        if owner["workflow_id"] != workflow_id or owner_payload.get("quest_id") != quest_id:
            raise SystemExit("assignment ownerのquest/workflow lineageが一致しません。")
        if payload.get("owner_worker_id") not in {None, owner["worker_id"]}:
            raise SystemExit("assignment.owner_worker_id がLedger上のownerと一致しません。")
        payload["owner_worker_id"] = owner["worker_id"]

    if worker_id == "examiner":
        lineage = payload["caller_lineage"]
        trial_ref = lineage["trial_ref"]
        if payload.get("trial_id") != trial_ref:
            raise SystemExit("examiner assignment.trial_id と caller_lineage.trial_ref を一致させてください。")
        trial = connection.execute(
            "SELECT quest_id, workflow_id, payload_json FROM trials WHERE trial_id = ?",
            (trial_ref,),
        ).fetchone()
        if trial is None:
            raise SystemExit(f"examinerが参照するTrialがLedgerにありません: {trial_ref}")
        trial_payload = json.loads(trial["payload_json"])
        if trial_payload.get("worker_id") != "inquisitor":
            raise SystemExit("examinerが参照するTrial ownerはinquisitorにしてください。")
        if trial["quest_id"] != quest_id or trial["workflow_id"] != workflow_id:
            raise SystemExit("examinerのquest/workflowが参照Trialと一致しません。")
        if lineage.get("required_parent_role") != "inquisitor" or lineage.get("trial_owner_worker_id") != "inquisitor":
            raise SystemExit("examiner caller_lineage はinquisitor Trialを指してください。")
        subject_assignment_ids = trial_payload.get("subject_assignment_ids")
        if not isinstance(subject_assignment_ids, list) or not subject_assignment_ids or not all(isinstance(item, str) and item for item in subject_assignment_ids):
            raise SystemExit("examinerが参照するTrialにはsubject_assignment_idsが必要です。")
        if trial_payload.get("subject_snapshot") != payload.get("subject_snapshot"):
            raise SystemExit("examiner assignmentと参照Trialのsubject_snapshotを一致させてください。")
        trial_boundaries = require_mapping(trial_payload.get("boundaries"), "trial.boundaries")
        child_boundaries = require_mapping(payload.get("boundaries"), "examiner assignment.boundaries")
        if child_boundaries.get("target_repo_root") != trial_boundaries.get("target_repo_root"):
            raise SystemExit("examiner assignmentのtarget_repo_rootは参照Trialと一致させてください。")
        for key in ("read_deny", "edit_deny", "safety_items"):
            parent_items = trial_boundaries.get(key)
            child_items = child_boundaries.get(key)
            if not isinstance(parent_items, list) or not isinstance(child_items, list):
                raise SystemExit(f"Trial/examiner boundaries.{key}はlistにしてください。")
            missing_items = [item for item in parent_items if item not in child_items]
            if missing_items:
                raise SystemExit(f"examiner assignmentは親Trialのboundaries.{key}を削除できません。")
        if payload.get("authority") != trial_payload.get("authority"):
            raise SystemExit("examiner assignmentのauthorityは親Trialのread-only authorityと一致させてください。")
        for subject_assignment_id in subject_assignment_ids:
            subject = connection.execute(
                "SELECT workflow_id, payload_json FROM assignments WHERE assignment_id = ?",
                (subject_assignment_id,),
            ).fetchone()
            if subject is None:
                raise SystemExit(f"Trial subject assignmentがLedgerにありません: {subject_assignment_id}")
            subject_payload = json.loads(subject["payload_json"])
            if subject["workflow_id"] != workflow_id or subject_payload.get("quest_id") != quest_id:
                raise SystemExit(f"Trial subject assignmentのquest/workflowが一致しません: {subject_assignment_id}")
        lineage["verification"] = "verified"

    if worker_id == "artificer":
        barrier = payload["integration_barrier"]
        refs = barrier["upstream_report_refs"]
        if len(refs) != len(set(refs)):
            raise SystemExit("integration_barrier.upstream_report_refs は重複させないでください。")
        contract_ref = barrier["contract_ref"]
        contract_row = connection.execute(
            "SELECT quest_id, workflow_id, status, payload_json FROM commands WHERE command_id = ?",
            (contract_ref,),
        ).fetchone()
        if contract_row is None:
            raise SystemExit(f"integration barrier contractがLedgerにありません: {contract_ref}")
        contract_payload = json.loads(contract_row["payload_json"])
        contract = contract_payload.get("integration_contract")
        if not isinstance(contract, dict):
            raise SystemExit("integration barrier commandにintegration_contractがありません。")
        required_refs = contract.get("required_report_refs")
        required_assignments = contract.get("required_assignment_ids")
        integration_scope = contract.get("integration_scope")
        if (
            contract_row["quest_id"] != quest_id
            or contract_row["workflow_id"] != workflow_id
            or contract_row["status"] not in {"issued", "active"}
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
            raise SystemExit("integration barrier commandのquest/workflow/status/required reports契約が不正です。")
        for scope_key in ("read", "edit", "validate"):
            require_relative_path_list(
                integration_scope[scope_key],
                f"integration_contract.integration_scope.{scope_key}",
                nonempty=scope_key == "edit",
            )
        if payload.get("owned_scope") != integration_scope:
            raise SystemExit("artificer.owned_scopeはcommandのintegration_scopeと一致させてください。")
        if len(required_refs) != len(set(required_refs)) or set(required_refs) != set(refs):
            raise SystemExit("integration_barrier.upstream_report_refsはcommandのrequired_report_refs完全集合と一致させてください。")
        source_assignment_ids: list[str] = []
        for report_id in refs:
            report = connection.execute(
                "SELECT worker_id, workflow_id, status, payload_json FROM reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if report is None:
                raise SystemExit(f"integration barrierのupstream reportがLedgerにありません: {report_id}")
            report_payload = json.loads(report["payload_json"])
            if report["worker_id"] not in {"adventurer", "artificer"}:
                raise SystemExit(f"integration barrierのupstream report ownerが実装workerではありません: {report_id}")
            if report["workflow_id"] != workflow_id or report_payload.get("quest_id") != quest_id:
                raise SystemExit(f"integration barrierのupstream report lineageが一致しません: {report_id}")
            if report["status"] not in {"recorded", "accepted"}:
                raise SystemExit(f"integration barrierのupstream reportが完了していません: {report_id}")
            source_assignment_id = report_payload.get("assignment_id")
            if not isinstance(source_assignment_id, str) or not source_assignment_id:
                raise SystemExit(f"integration barrierのupstream reportにassignment_idがありません: {report_id}")
            source = connection.execute(
                "SELECT worker_id, workflow_id, status, payload_json FROM assignments WHERE assignment_id = ?",
                (source_assignment_id,),
            ).fetchone()
            if source is None:
                raise SystemExit(f"upstream reportのsource assignmentがLedgerにありません: {source_assignment_id}")
            source_payload = json.loads(source["payload_json"])
            if (
                source["worker_id"] != report["worker_id"]
                or source["workflow_id"] != workflow_id
                or source_payload.get("quest_id") != quest_id
                or source["status"] != "done"
            ):
                raise SystemExit(f"upstream reportのsource assignment lineage/statusが不正です: {report_id}")
            if report_payload.get("target_repo_root") != source_payload.get("boundaries", {}).get("target_repo_root"):
                raise SystemExit(f"upstream reportのtarget_repo_rootがsource assignmentと一致しません: {report_id}")
            if report_payload.get("base_snapshot") != source_payload.get("subject_snapshot"):
                raise SystemExit(f"upstream reportのbase_snapshotがsource assignmentと一致しません: {report_id}")
            result_snapshot = report_payload.get("result_snapshot")
            if not isinstance(result_snapshot, dict) or set(result_snapshot) != SNAPSHOT_FIELDS:
                raise SystemExit(f"upstream reportにcanonical result_snapshotがありません: {report_id}")
            verify_snapshot_with_helper(source_payload, result_snapshot)
            source_scope = source_payload.get("owned_scope")
            if not isinstance(source_scope, dict):
                raise SystemExit(f"source assignmentにowned_scopeがありません: {source_assignment_id}")
            if any(not path_covered_by_scopes(path, integration_scope["read"]) for path in source_scope.get("edit", [])):
                raise SystemExit(f"artificer.read scopeがupstream edit scopeを包含していません: {source_assignment_id}")
            if any(not path_covered_by_scopes(path, result_snapshot["scope_paths"]) for path in source_scope.get("edit", [])):
                raise SystemExit(f"upstream result snapshotがsource edit scopeを包含していません: {report_id}")
            source_assignment_ids.append(source_assignment_id)
        if (
            len(required_assignments) != len(set(required_assignments))
            or len(source_assignment_ids) != len(set(source_assignment_ids))
            or set(source_assignment_ids) != set(required_assignments)
        ):
            raise SystemExit("upstream reportsのsource assignmentsはcommandのrequired_assignment_ids完全集合と一致させてください。")
        barrier["verification"] = "verified"


def upsert_quest(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload = nested_payload(payload_body(event), "quest")
    quest_id = require_string(payload.get("id") or payload.get("quest_id"), "quest.id")
    rank = payload.get("rank")
    if rank is not None and (not isinstance(rank, str) or rank not in QUEST_RANKS):
        expected = ", ".join(sorted(QUEST_RANKS))
        raise SystemExit(f"quest.rank は {expected} のいずれかにしてください: {rank}")
    status = require_status(payload.get("status") or "drafted", QUEST_STATUSES, "quest.status")
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    payload["status"] = status
    payload["workflow_id"] = workflow_id
    connection.execute(
        """
        INSERT INTO quests(quest_id, workflow_id, rank, status, payload_json)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(quest_id) DO UPDATE SET
          workflow_id = excluded.workflow_id,
          rank = excluded.rank,
          status = excluded.status,
          payload_json = excluded.payload_json,
          updated_at = datetime('now')
        """,
        (quest_id, workflow_id, rank, status, json_dumps(payload)),
    )


def upsert_request(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload = nested_payload(payload_body(event), "request", "quest")
    request_id = require_string(payload.get("id") or payload.get("request_id") or payload.get("quest_id"), "request.id")
    status = require_status(payload.get("status") or "drafted", REQUEST_STATUSES, "request.status")
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    payload["status"] = status
    payload["workflow_id"] = workflow_id
    connection.execute(
        """
        INSERT INTO requests(request_id, quest_id, workflow_id, status, payload_json)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(request_id) DO UPDATE SET
          quest_id = excluded.quest_id,
          workflow_id = excluded.workflow_id,
          status = excluded.status,
          payload_json = excluded.payload_json,
          updated_at = datetime('now')
        """,
        (request_id, payload.get("quest_id"), workflow_id, status, json_dumps(payload)),
    )


def upsert_command(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload = nested_payload(payload_body(event), "command")
    command_id = require_string(payload.get("id") or payload.get("command_id"), "command.id")
    status = require_status(payload.get("status") or "drafted", COMMAND_STATUSES, "command.status")
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    payload["status"] = status
    payload["workflow_id"] = workflow_id
    connection.execute(
        """
        INSERT INTO commands(command_id, quest_id, workflow_id, status, payload_json)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(command_id) DO UPDATE SET
          quest_id = excluded.quest_id,
          workflow_id = excluded.workflow_id,
          status = excluded.status,
          payload_json = excluded.payload_json,
          updated_at = datetime('now')
        """,
        (command_id, payload.get("quest_id"), workflow_id, status, json_dumps(payload)),
    )


def upsert_assignment(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload = nested_payload(payload_body(event), "assignment")
    assignment_id = require_string(payload.get("id") or payload.get("assignment_id"), "assignment.id")
    validate_assignment_machine_contract(payload)
    validate_assignment_relations(connection, payload, event)
    status = require_status(payload.get("status") or "idle", ASSIGNMENT_STATUSES, "assignment.status")
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    kind = payload.get("kind") or payload.get("role")
    payload["status"] = status
    payload["workflow_id"] = workflow_id
    payload["kind"] = kind
    connection.execute(
        """
        INSERT INTO assignments(assignment_id, parent_id, worker_id, kind, workflow_id, status, payload_json)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(assignment_id) DO UPDATE SET
          parent_id = excluded.parent_id,
          worker_id = excluded.worker_id,
          kind = excluded.kind,
          workflow_id = excluded.workflow_id,
          status = excluded.status,
          payload_json = excluded.payload_json,
          updated_at = datetime('now')
        """,
        (
            assignment_id,
            assignment_parent_id(payload),
            payload.get("worker_id") or payload.get("role") or "unassigned",
            kind,
            workflow_id,
            status,
            json_dumps(payload),
        ),
    )


def upsert_report(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload = nested_payload(payload_body(event), "report")
    report_id = require_string(payload.get("id") or payload.get("report_id") or event_entity(event)[1], "report.id")
    if "trial_depth" in payload:
        depth = payload["trial_depth"]
        if not isinstance(depth, str) or depth not in TRIAL_DEPTHS:
            expected = ", ".join(sorted(TRIAL_DEPTHS))
            raise SystemExit(f"report.trial_depth は {expected} のいずれかにしてください: {depth}")
    status = require_status(payload.get("status") or "draft", REPORT_STATUSES, "report.status")
    worker_id = require_string(payload.get("worker_id"), "report.worker_id")
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    payload["status"] = status
    payload["workflow_id"] = workflow_id
    validate_examiner_report_contract(connection, payload, event)
    validate_inquisitor_report_contract(connection, payload, event)
    connection.execute(
        """
        INSERT INTO reports(report_id, worker_id, workflow_id, decision, status, payload_json)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
          worker_id = excluded.worker_id,
          workflow_id = excluded.workflow_id,
          decision = excluded.decision,
          status = excluded.status,
          payload_json = excluded.payload_json,
          updated_at = datetime('now')
        """,
        (
            report_id,
            worker_id,
            workflow_id,
            payload.get("decision"),
            status,
            json_dumps(payload),
        ),
    )


def upsert_trial(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload = nested_payload(payload_body(event), "trial", "report")
    trial_id = require_string(payload.get("id") or payload.get("trial_id") or event_entity(event)[1], "trial.id")
    depth = None
    if "depth" in payload:
        depth = payload["depth"]
    elif "trial_depth" in payload:
        depth = payload["trial_depth"]
    if depth is not None and (not isinstance(depth, str) or depth not in TRIAL_DEPTHS):
        expected = ", ".join(sorted(TRIAL_DEPTHS))
        raise SystemExit(f"trial.depth は {expected} のいずれかにしてください: {depth}")
    if depth is None:
        raise SystemExit("trial.depth は具体的なTrial depthにしてください。")
    require_string(payload.get("worker_id"), "trial.worker_id")
    validate_trial_machine_contract(connection, payload, event)
    status = require_status(payload.get("status") or "idle", TRIAL_STATUSES, "trial.status")
    workflow_id = payload.get("workflow_id") or event.get("workflow_id")
    payload["status"] = status
    payload["workflow_id"] = workflow_id
    connection.execute(
        """
        INSERT INTO trials(trial_id, quest_id, workflow_id, depth, status, payload_json)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(trial_id) DO UPDATE SET
          quest_id = excluded.quest_id,
          workflow_id = excluded.workflow_id,
          depth = excluded.depth,
          status = excluded.status,
          payload_json = excluded.payload_json,
          updated_at = datetime('now')
        """,
        (trial_id, payload.get("quest_id"), workflow_id, depth, status, json_dumps(payload)),
    )


def validate_inbox_message_payload(payload: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_INBOX_MESSAGE_FIELDS - set(payload))
    if missing:
        raise SystemExit("message に必要な field がありません: " + ", ".join(missing))
    require_string(payload.get("id"), "message.id")
    require_string(payload.get("sender"), "message.sender")
    require_string(payload.get("recipient"), "message.recipient")
    parse_timestamp(payload.get("created_at"), "message.created_at")
    require_string(payload.get("type"), "message.type")
    if not isinstance(payload.get("trusted"), bool):
        raise SystemExit("message.trusted は bool にしてください。")
    if payload.get("trusted") is not False:
        raise SystemExit("message.trusted は false にしてください。")
    require_mapping(payload.get("payload"), "message.payload")
    require_status(payload.get("status"), MESSAGE_STATUSES, "message.status")
    envelope = memory_candidate_envelope(payload)
    if envelope is not None:
        validate_memory_candidate_message_scope(payload)
        validate_memory_candidate_envelope(envelope[1], envelope[0])


def inbox_event_safety(payload: dict[str, Any]) -> dict[str, list[str]]:
    envelope = memory_candidate_envelope(payload)
    if envelope is None:
        return {"safety_items": [], "human_confirmation_required": []}
    return {
        "safety_items": list(MEMORY_CANDIDATE_SAFETY_ITEMS),
        "human_confirmation_required": [],
    }


def upsert_message(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload = payload_body(event)
    validate_inbox_message_payload(payload)
    message_id = require_string(payload.get("id"), "message.id")
    connection.execute(
        """
        INSERT INTO inbox_messages(message_id, recipient, workflow_id, status, payload_json, created_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
          recipient = excluded.recipient,
          workflow_id = excluded.workflow_id,
          status = excluded.status,
          payload_json = excluded.payload_json,
          created_at = excluded.created_at
        """,
        (
            message_id,
            require_string(payload.get("recipient"), "message.recipient"),
            payload.get("workflow_id") or event.get("workflow_id"),
            require_string(payload.get("status"), "message.status"),
            json_dumps(payload),
            require_string(payload.get("created_at"), "message.created_at"),
        ),
    )


def record_event(connection: sqlite3.Connection, event: dict[str, Any], runtime_guild_root: Path) -> None:
    validate_event_input(event, runtime_guild_root)
    entity_type = event_entity(event)[0]
    if entity_type == "quest":
        upsert_quest(connection, event)
    elif entity_type == "request":
        upsert_request(connection, event)
    elif entity_type == "command":
        upsert_command(connection, event)
    elif entity_type == "assignment":
        upsert_assignment(connection, event)
    elif entity_type == "report":
        upsert_report(connection, event)
    elif entity_type == "trial":
        upsert_trial(connection, event)
    elif entity_type == "message":
        upsert_message(connection, event)
    # relation validatorが付与したverified lineageを含む同一payloadをevent logへ保存する。
    insert_event(connection, event)


def cmd_init(args: argparse.Namespace) -> None:
    with connect_write(args.runtime_root) as connection:
        init_db(connection)
    print(db_path(args.runtime_root))


def cmd_record_event(args: argparse.Namespace) -> None:
    event = require_mapping(read_json_arg(args.event), "event")
    with connect_write(args.runtime_root) as connection:
        ensure_schema_compatible(connection)
        record_event(connection, event, args.runtime_root.resolve().parent)
        connection.commit()
    print(event["event_id"])


def cmd_add_inbox_message(args: argparse.Namespace) -> None:
    payload = require_mapping(read_json_arg(args.message), "message")
    validate_inbox_message_payload(payload)
    event = {
        "event_id": f"evt_message_{payload['id']}",
        "timestamp": payload["created_at"],
        "actor": payload["sender"],
        "event_type": "inbox_message_added",
        "entity": {"type": "message", "id": payload["id"]},
        "operation": "append",
        "workflow_id": payload.get("workflow_id"),
        "structured_data_usage": {"structured_inputs": ["message"], "decision_rationale": "inbox message append", "evidence_refs": []},
        "payload": payload,
        "event_safety": inbox_event_safety(payload),
    }
    with connect_write(args.runtime_root) as connection:
        ensure_schema_compatible(connection)
        record_event(connection, event, args.runtime_root.resolve().parent)
        connection.commit()
    print(event["event_id"])


def cmd_dump(args: argparse.Namespace) -> None:
    allowed = {"queue_metadata", "events", "quests", "requests", "commands", "assignments", "reports", "trials", "inbox_messages"}
    if args.table not in allowed:
        raise SystemExit("dump table は次のいずれかにしてください: " + ", ".join(sorted(allowed)))
    with connect_read(args.runtime_root) as connection:
        rows = [dict(row) for row in connection.execute(f"SELECT * FROM {args.table} ORDER BY rowid")]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    with connect_read(args.runtime_root) as connection:
        result = {}
        for table in ("quests", "requests", "commands", "assignments", "reports", "trials", "inbox_messages", "events"):
            result[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guild runtime Ledger helper")
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.set_defaults(func=cmd_init)

    record = sub.add_parser("record-event")
    record.add_argument("event")
    record.set_defaults(func=cmd_record_event)

    inbox = sub.add_parser("add-inbox-message")
    inbox.add_argument("message")
    inbox.set_defaults(func=cmd_add_inbox_message)

    dump = sub.add_parser("dump")
    dump.add_argument("table")
    dump.set_defaults(func=cmd_dump)

    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
