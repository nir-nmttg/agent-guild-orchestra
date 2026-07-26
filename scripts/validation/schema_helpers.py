"""再利用する schema 検証 helper。"""

from __future__ import annotations

from .core import mapping, require, sequence
from .rules import (
    ARTIFACT_REQUIRED_FIELDS,
    AUTHORITY_KEYS,
    EVIDENCE_STATE_KEYS,
    STRUCTURED_DATA_USAGE_FIELDS,
)

def validate_template_metadata(document: dict[str, object], rel: str) -> None:
    for key in ARTIFACT_REQUIRED_FIELDS:
        require(key in document, f"{rel} に {key} が必要です。")
    require(document["schema_version"] == "4.0", f"{rel}.schema_version は 4.0 にしてください。")
    usage = mapping(document.get("structured_data_usage"), f"{rel}.structured_data_usage")
    for key in STRUCTURED_DATA_USAGE_FIELDS:
        require(key in usage, f"{rel}.structured_data_usage.{key} が必要です。")


def validate_authority(value: object, label: str) -> None:
    authority = mapping(value, label)
    require(set(authority) == AUTHORITY_KEYS, f"{label} は authority key と一致させてください。")
    for key in AUTHORITY_KEYS:
        require(isinstance(authority[key], bool), f"{label}.{key} は bool にしてください。")


def validate_boundaries(value: object, label: str) -> None:
    boundaries = mapping(value, label)
    for key in ("target_repo_root", "read_deny", "edit_deny", "safety_items"):
        require(key in boundaries, f"{label}.{key} が必要です。")
    for key in ("read_deny", "edit_deny", "safety_items"):
        sequence(boundaries[key], f"{label}.{key}")


def validate_subject_snapshot(value: object, label: str) -> None:
    snapshot = mapping(value, label)
    required = {
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
    require(set(snapshot) == required, f"{label} は canonical subject snapshot key と一致させてください。")
    require(snapshot.get("digest_version") == "agent-guild-orchestra-snapshot-v1", f"{label}.digest_version は agent-guild-orchestra-snapshot-v1 にしてください。")
    require(snapshot.get("kind") in {"revision_only", "working_tree_content", "commit_range", None}, f"{label}.kind が不正です。")
    sequence(snapshot.get("scope_paths"), f"{label}.scope_paths")
    sequence(snapshot.get("untracked_paths"), f"{label}.untracked_paths")
    require(snapshot.get("dirty_state") in {"clean", "dirty", None}, f"{label}.dirty_state は clean / dirty / null にしてください。")


def validate_evidence_state(value: object, label: str) -> None:
    """ownerの次アクションを変え得る小さな状態だけを検証する。"""

    state = mapping(value, label)
    require(set(state) == EVIDENCE_STATE_KEYS, f"{label} は compact evidence_state key と一致させてください。")
    for key in ("blocking_unknowns", "failed_checks", "high_risk_triggers"):
        sequence(state[key], f"{label}.{key}")
    require(isinstance(state.get("scope_drift"), bool), f"{label}.scope_drift は bool にしてください。")
    require(
        state.get("verification_status") in {"not_checked", "partially_checked", "verified", "failed", "blocked"},
        f"{label}.verification_status が不正です。",
    )
    require(state.get("next_action") is None or isinstance(state.get("next_action"), str), f"{label}.next_action は null または文字列にしてください。")
    require(state.get("stop_reason") is None or isinstance(state.get("stop_reason"), str), f"{label}.stop_reason は null または文字列にしてください。")


def validate_compat_context(value: object, label: str) -> None:
    entries = sequence(value, label)
    require(entries, f"{label} は Claude 互換 context disposition の雛形を含めてください。")
    for index, value_entry in enumerate(entries):
        entry_label = f"{label}[{index}]"
        entry = mapping(value_entry, entry_label)
        for key in ("source_type", "path", "sha256", "trust", "applies_to", "status", "disposition", "skip_reason"):
            require(key in entry, f"{entry_label}.{key} が必要です。")
        for key in ("content", "rendered_context", "settings", "raw_content"):
            require(key not in entry, f"{entry_label} に raw payload key `{key}` を含めないでください。")
        require(entry.get("source_type") == "claude", f"{entry_label}.source_type は claude にしてください。")
        require(entry.get("trust") == "untrusted", f"{entry_label}.trust は untrusted にしてください。")
