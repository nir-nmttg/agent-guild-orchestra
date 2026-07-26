"""runtime Ledger と queue DB の smoke 検証。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import ROOT, python_string_set_constant, read, require, require_tokens
from .rules import LEDGER_TABLES


def validate_sqlite_schema() -> None:
    schema = read("template/.agents/orchestra/scripts/queue_schema.sql")
    require("assignment_id TEXT NOT NULL PRIMARY KEY" in schema, "assignments table は assignment_id を primary key にしてください。")
    require("task_id" not in schema, "SQLite schema に旧 column `task_id` を戻さないでください。")
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(schema)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = sorted(LEDGER_TABLES - tables)
    require(not missing, "SQLite schema に不足 table があります: " + ", ".join(missing))


def _run_python(script: Path, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(arg) for arg in args)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _init_target_repo(path: Path) -> None:
    path.mkdir(parents=True)
    init = _run_git(path, "init", "--initial-branch=main")
    require(init.returncode == 0, "runtime smoke target repoのgit initに失敗しました: " + init.stderr)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "other.py").write_text("OTHER = 2\n", encoding="utf-8")
    add = _run_git(path, "add", "app.py", "other.py")
    require(add.returncode == 0, "runtime smoke target repoのgit addに失敗しました: " + add.stderr)
    commit = _run_git(
        path,
        "-c",
        "user.name=Runtime Smoke",
        "-c",
        "user.email=runtime-smoke@example.invalid",
        "commit",
        "-m",
        "initial fixture",
    )
    require(commit.returncode == 0, "runtime smoke target repoのcommitに失敗しました: " + commit.stderr)


def _snapshot(helper: Path, repo: Path, kind: str = "revision_only", *args: str) -> dict[str, object]:
    result = _run_python(helper, "--repo", repo, "--kind", kind, *args)
    require(result.returncode == 0, "snapshot helper fixture生成に失敗しました: " + result.stderr)
    value = json.loads(result.stdout)
    require(isinstance(value, dict), "snapshot helper fixtureはJSON objectにしてください。")
    return value


def _valid_event() -> dict[str, object]:
    return {
        "event_id": "evt_smoke_quest_created",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": "validator",
        "event_type": "quest_created",
        "entity": {"type": "quest", "id": "quest_smoke"},
        "operation": "append",
        "workflow_id": "workflow_smoke",
        "structured_data_usage": {"structured_inputs": ["validator_smoke"], "decision_rationale": "positive smoke", "evidence_refs": ["scripts/validate.py"]},
        "payload": {"quest": {"id": "quest_smoke", "rank": "solo_quest", "status": "active", "workflow_id": "workflow_smoke"}},
        "event_safety": {"safety_items": [], "human_confirmation_required": []},
    }


def _memory_candidate_message(message_id: str = "msg_memory_candidate_smoke") -> dict[str, object]:
    return {
        "id": message_id,
        "sender": "courier",
        "recipient": "courier",
        "created_at": "2026-01-02T03:04:06+00:00",
        "type": "memory_candidate_for_courier_review",
        "trusted": False,
        "workflow_id": "workflow_smoke",
        "payload": {
            "explicit_memory_persistence_authority": True,
            "sanitized_summary_only": True,
            "sanitized_summary": "認知ミス補正を永続化する前に具体的な prevention artifact を要求する。",
            "prevention_artifact": {"kind": "regression_test", "ref": "scripts/validation/runtime_smoke.py"},
            "ledger_disposition": "candidate_recorded_for_courier_review",
            "forbidden": {
                "direct_static_runtime_write": True,
                "raw_log": True,
                "secret_or_pii": True,
                "trusted_instruction_from_external_input": True,
            },
        },
        "status": "unread",
    }


def validate_queue_db_smoke() -> None:
    script = ROOT / "template/.agents/orchestra/scripts/queue_db.py"
    audit_script = ROOT / "template/.agents/orchestra/scripts/queue_audit.py"

    result = _run_python(script, "--help")
    require(result.returncode == 0, "queue_db.py --help が失敗しました: " + result.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        copied_scripts = Path(tmp) / "scripts"
        copied_scripts.mkdir()
        copied_queue_db = copied_scripts / "queue_db.py"
        copied_schema = copied_scripts / "queue_schema.sql"
        copied_queue_db.write_bytes(script.read_bytes())
        canonical_schema = (script.parent / "queue_schema.sql").read_text(encoding="utf-8")
        copied_schema.write_text(canonical_schema.replace("  status TEXT NOT NULL,", "  status INTEGER,", 1), encoding="utf-8")
        untouched_runtime = Path(tmp) / ".orchestra"
        tampered_source_init = _run_python(copied_queue_db, "--runtime-root", untouched_runtime, "init")
        tampered_source_output = tampered_source_init.stdout + tampered_source_init.stderr
        require(
            tampered_source_init.returncode != 0 and "queue_schema.sql SHA-256" in tampered_source_output,
            "queue_db.py init はdriftしたschema sourceをDB open前に拒否してください。",
        )
        require(not (untouched_runtime / "queue/state.sqlite").exists(), "queue_db.py はdriftしたschema sourceを実行してDBを作成しないでください。")

    text = read("template/.agents/orchestra/scripts/queue_db.py")
    require_tokens(
        text,
        ("mode=ro", "record-event", "add-inbox-message", "dump", "REQUIRED_TABLES", "LEGACY_JSON_KEYS", "RETIRED_AGENT_VALUES", "LEGACY_RUNTIME_STRING_VALUES", "MEMORY_CANDIDATE_MESSAGE_TYPE", "explicit_memory_persistence_authority", "sanitized_summary_only", "prevention_artifact", "ledger_disposition"),
        "queue_db.py",
    )
    audit_text = read("template/.agents/orchestra/scripts/queue_audit.py")
    require_tokens(audit_text, ("REQUIRED_TABLES", "LEGACY_COLUMNS", "LEGACY_JSON_KEYS", "RETIRED_AGENT_VALUES", "LEGACY_RUNTIME_STRING_VALUES", "MEMORY_CANDIDATE_MESSAGE_TYPE", "explicit_memory_persistence_authority", "sanitized_summary_only", "prevention_artifact", "ledger_disposition"), "queue_audit.py")

    db_legacy_keys = python_string_set_constant("template/.agents/orchestra/scripts/queue_db.py", "LEGACY_JSON_KEYS")
    audit_legacy_keys = python_string_set_constant("template/.agents/orchestra/scripts/queue_audit.py", "LEGACY_JSON_KEYS")
    install_legacy_keys = python_string_set_constant("scripts/install.py", "LEGACY_RUNTIME_JSON_KEYS")
    require(db_legacy_keys == audit_legacy_keys == install_legacy_keys, "旧 JSON key 一覧を queue_db.py / queue_audit.py / install.py で一致させてください。")

    db_retired_values = python_string_set_constant("template/.agents/orchestra/scripts/queue_db.py", "RETIRED_AGENT_VALUES")
    audit_retired_values = python_string_set_constant("template/.agents/orchestra/scripts/queue_audit.py", "RETIRED_AGENT_VALUES")
    install_retired_values = python_string_set_constant("scripts/install.py", "RETIRED_AGENT_VALUES")
    require(db_retired_values == audit_retired_values == install_retired_values, "廃止済み agent 値を queue_db.py / queue_audit.py / install.py で一致させてください。")
    renamed_agent_ids = {"advisor", "focus_reviewer", "integration_owner", "party_leader", "quest_sentinel"}
    require(renamed_agent_ids <= db_retired_values, "改名前のagent IDを既存runtimeでfail closedにしてください。")

    db_legacy_values = python_string_set_constant("template/.agents/orchestra/scripts/queue_db.py", "LEGACY_RUNTIME_STRING_VALUES")
    audit_legacy_values = python_string_set_constant("template/.agents/orchestra/scripts/queue_audit.py", "LEGACY_RUNTIME_STRING_VALUES")
    install_legacy_values = python_string_set_constant("scripts/install.py", "LEGACY_RUNTIME_STRING_VALUES")
    require(db_legacy_values == audit_legacy_values == install_legacy_values, "廃止済み runtime 値を queue_db.py / queue_audit.py / install.py で一致させてください。")
    renamed_contract_values = {
        "advisory_consultation",
        "bounded_trial_focus_reviewer",
        "cross_scope_integration_owner",
        "independent_focus_advisor",
    }
    require(renamed_contract_values <= db_legacy_values, "改名前のrole/kind値を既存runtimeでfail closedにしてください。")

    inbox_script = ROOT / "template/.agents/orchestra/scripts/inbox_write.sh"
    docker_runner = ROOT / "template/.agents/orchestra/scripts/docker_python.sh"
    stop_hook_shell = ROOT / "template/.codex/hooks/stop_quality_gate.sh"
    require("warden" in read("template/.agents/orchestra/scripts/inbox_write.sh"), "inbox_write.sh は warden を送受信 role として許可してください。")
    for executable_path in (script, audit_script, inbox_script, docker_runner, stop_hook_shell):
        require(executable_path.stat().st_mode & 0o111, f"{executable_path.relative_to(ROOT)} の executable bit を維持してください。")
    for shell_path in (inbox_script, docker_runner, stop_hook_shell):
        shell = subprocess.run(["bash", "-n", str(shell_path)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        require(shell.returncode == 0, f"{shell_path.relative_to(ROOT)} の shell syntax check が失敗しました: " + shell.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        guild_root = Path(tmp) / "guild"
        runtime_root = guild_root / ".orchestra"
        target_repo = guild_root / "repositories/example-app"
        snapshot_helper = ROOT / "template/.agents/orchestra/scripts/snapshot_digest.py"
        _init_target_repo(target_repo)
        sibling_repo = guild_root / "repositories/sibling-app"
        sibling_clone = _run_git(guild_root, "clone", "--no-local", str(target_repo), str(sibling_repo))
        require(sibling_clone.returncode == 0, "runtime smoke sibling repoのcloneに失敗しました: " + sibling_clone.stderr)
        revision_snapshot = _snapshot(snapshot_helper, target_repo)
        app_result_snapshot = _snapshot(snapshot_helper, target_repo, "working_tree_content", "--scope", "app.py")
        other_result_snapshot = _snapshot(snapshot_helper, target_repo, "working_tree_content", "--scope", "other.py")

        init = _run_python(script, "--runtime-root", runtime_root, "init")
        require(init.returncode == 0, "queue_db.py init が失敗しました: " + init.stderr)

        recorded = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(_valid_event()))
        require(recorded.returncode == 0, "queue_db.py record-event の smoke が失敗しました: " + recorded.stderr)

        mismatched_identity_event = json.loads(json.dumps(_valid_event()))
        mismatched_identity_event["event_id"] = "evt_smoke_mismatched_entity"
        mismatched_identity_event["entity"]["id"] = "quest_other"
        mismatched_identity = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(mismatched_identity_event))
        require(mismatched_identity.returncode != 0 and "canonical id" in mismatched_identity.stderr, "queue_db.py はentity.idとpayload idの不一致を拒否してください。")

        mismatched_workflow_event = json.loads(json.dumps(_valid_event()))
        mismatched_workflow_event["event_id"] = "evt_smoke_mismatched_workflow"
        mismatched_workflow_event["payload"]["quest"]["workflow_id"] = "workflow_other"
        mismatched_workflow = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(mismatched_workflow_event))
        require(mismatched_workflow.returncode != 0 and "workflow_id" in mismatched_workflow.stderr, "queue_db.py はevent/payload workflow不一致を拒否してください。")

        owner_assignment_event = {
            "event_id": "evt_assignment_owner_smoke",
            "timestamp": "2026-01-02T03:04:03+00:00",
            "actor": "validator",
            "event_type": "assignment_created",
            "entity": {"type": "assignment", "id": "assignment_owner_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["assignment"], "decision_rationale": "owner assignment smoke", "evidence_refs": []},
            "payload": {
                "assignment": {
                    "id": "assignment_owner_smoke",
                    "quest_id": "quest_smoke",
                    "worker_id": "adventurer",
                    "role": "bounded_implementation_owner",
                    "terminal_worker": True,
                    "objective": "app.pyのbounded変更を検証可能な形で完了する",
                    "success_criteria": ["app.pyの対象条件を満たす"],
                    "owned_scope": {"read": ["app.py"], "edit": ["app.py"], "validate": ["app.py"]},
                    "integration_barrier": None,
                    "authority": {"read": True, "edit": True, "validate": True, "local_git": False, "external_actions": False},
                    "boundaries": {"target_repo_root": str(target_repo), "read_deny": [], "edit_deny": [], "safety_items": []},
                    "subject_snapshot": revision_snapshot,
                    "status": "done",
                }
            },
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        owner_assignment = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(owner_assignment_event))
        require(owner_assignment.returncode == 0, "queue_db.py は成功条件とowned scope付きowner assignmentを受け付けてください: " + owner_assignment.stderr)

        second_owner_event = json.loads(json.dumps(owner_assignment_event))
        second_owner_event["event_id"] = "evt_assignment_owner_second_smoke"
        second_owner_event["entity"]["id"] = "assignment_owner_second_smoke"
        second_owner = second_owner_event["payload"]["assignment"]
        second_owner["id"] = "assignment_owner_second_smoke"
        second_owner["objective"] = "other.pyのbounded変更を検証可能な形で完了する"
        second_owner["owned_scope"] = {"read": ["other.py"], "edit": ["other.py"], "validate": ["other.py"]}
        second_owner_result = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(second_owner_event))
        require(second_owner_result.returncode == 0, "queue_db.py second owner assignment fixtureの記録に失敗しました: " + second_owner_result.stderr)

        warden_assignment_event = {
            "event_id": "evt_assignment_warden_smoke",
            "timestamp": "2026-01-02T03:04:04+00:00",
            "actor": "validator",
            "event_type": "assignment_created",
            "entity": {"type": "assignment", "id": "assignment_warden_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["assignment"], "decision_rationale": "warden assignment linkage smoke", "evidence_refs": ["scripts/validation/runtime_smoke.py"]},
            "payload": {
                "assignment": {
                    "id": "assignment_warden_smoke",
                    "quest_id": "quest_smoke",
                    "owner_assignment_id": "assignment_owner_smoke",
                    "worker_id": "warden",
                    "role": "exceptional_control_diagnostician",
                    "kind": "evidence_state_monitor",
                    "terminal_worker": True,
                    "control_trigger": "security",
                    "objective": "security triggerの矛盾と停止条件を診断する",
                    "evidence_required": ["trigger根拠と次の最小行動"],
                    "authority": {
                        "read": True,
                        "edit": False,
                        "validate": False,
                        "local_git": False,
                        "external_actions": False,
                    },
                    "boundaries": {
                        "target_repo_root": str(target_repo),
                        "read_deny": [],
                        "edit_deny": [],
                        "safety_items": [],
                    },
                    "subject_snapshot": revision_snapshot,
                    "status": "idle",
                }
            },
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        warden_assignment = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(warden_assignment_event))
        require(warden_assignment.returncode == 0, "queue_db.py は owner/control trigger 付き warden assignment を受け付けてください: " + warden_assignment.stderr)

        invalid_warden_owner_event = json.loads(json.dumps(warden_assignment_event))
        invalid_warden_owner_event["event_id"] = "evt_assignment_warden_missing_owner"
        invalid_warden_owner_event["entity"]["id"] = "assignment_warden_missing_owner"
        invalid_warden_owner_event["payload"]["assignment"]["id"] = "assignment_warden_missing_owner"
        invalid_warden_owner_event["payload"]["assignment"].pop("owner_assignment_id")
        invalid_warden_owner = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_warden_owner_event))
        require(invalid_warden_owner.returncode != 0 and "owner_assignment_id" in invalid_warden_owner.stderr, "queue_db.py は owner assignment なしの warden assignment を拒否してください。")

        invalid_warden_trigger_event = json.loads(json.dumps(warden_assignment_event))
        invalid_warden_trigger_event["event_id"] = "evt_assignment_warden_missing_trigger"
        invalid_warden_trigger_event["entity"]["id"] = "assignment_warden_missing_trigger"
        invalid_warden_trigger_event["payload"]["assignment"]["id"] = "assignment_warden_missing_trigger"
        invalid_warden_trigger_event["payload"]["assignment"].pop("control_trigger")
        invalid_warden_trigger = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_warden_trigger_event))
        require(invalid_warden_trigger.returncode != 0 and "control_trigger" in invalid_warden_trigger.stderr, "queue_db.py は control_trigger なしの warden assignment を拒否してください。")

        invalid_authority_event = json.loads(json.dumps(warden_assignment_event))
        invalid_authority_event["event_id"] = "evt_assignment_missing_authority"
        invalid_authority_event["entity"]["id"] = "assignment_missing_authority"
        invalid_authority_event["payload"]["assignment"]["id"] = "assignment_missing_authority"
        invalid_authority_event["payload"]["assignment"].pop("authority")
        invalid_authority = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_authority_event))
        require(invalid_authority.returncode != 0 and "authority" in invalid_authority.stderr, "queue_db.py は machine-bound authority なしの assignment を拒否してください。")

        invalid_edit_event = json.loads(json.dumps(warden_assignment_event))
        invalid_edit_event["event_id"] = "evt_assignment_readonly_edit"
        invalid_edit_event["entity"]["id"] = "assignment_readonly_edit"
        invalid_edit_event["payload"]["assignment"]["id"] = "assignment_readonly_edit"
        invalid_edit_event["payload"]["assignment"]["authority"]["edit"] = True
        invalid_edit = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_edit_event))
        require(invalid_edit.returncode != 0 and "read-only" in invalid_edit.stderr, "queue_db.py は read-only worker の edit authority を拒否してください。")

        invalid_snapshot_event = json.loads(json.dumps(warden_assignment_event))
        invalid_snapshot_event["event_id"] = "evt_assignment_missing_snapshot"
        invalid_snapshot_event["entity"]["id"] = "assignment_missing_snapshot"
        invalid_snapshot_event["payload"]["assignment"]["id"] = "assignment_missing_snapshot"
        invalid_snapshot_event["payload"]["assignment"].pop("subject_snapshot")
        invalid_snapshot = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_snapshot_event))
        require(invalid_snapshot.returncode != 0 and "subject_snapshot" in invalid_snapshot.stderr, "queue_db.py は helper-generated snapshot なしの assignment を拒否してください。")

        invalid_snapshot_id_event = json.loads(json.dumps(warden_assignment_event))
        invalid_snapshot_id_event["event_id"] = "evt_assignment_invalid_snapshot_id"
        invalid_snapshot_id_event["entity"]["id"] = "assignment_invalid_snapshot_id"
        invalid_snapshot_id_event["payload"]["assignment"]["id"] = "assignment_invalid_snapshot_id"
        invalid_snapshot_id_event["payload"]["assignment"]["subject_snapshot"]["snapshot_id"] = "sha256:invented"
        invalid_snapshot_id = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_snapshot_id_event))
        require(invalid_snapshot_id.returncode != 0 and "helper形式" in invalid_snapshot_id.stderr, "queue_db.py は不正形式のsnapshot idを拒否してください。")

        invented_snapshot_event = json.loads(json.dumps(warden_assignment_event))
        invented_snapshot_event["event_id"] = "evt_assignment_invented_snapshot"
        invented_snapshot_event["entity"]["id"] = "assignment_invented_snapshot"
        invented_snapshot_event["payload"]["assignment"]["id"] = "assignment_invented_snapshot"
        invented_snapshot_event["payload"]["assignment"]["subject_snapshot"]["snapshot_id"] = "sha256:" + "c" * 64
        invented_snapshot = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invented_snapshot_event))
        require(invented_snapshot.returncode != 0 and "helper出力" in invented_snapshot.stderr, "queue_db.py は形式だけ正しい自己申告snapshotを拒否してください。")

        missing_objective_event = json.loads(json.dumps(warden_assignment_event))
        missing_objective_event["event_id"] = "evt_assignment_missing_objective"
        missing_objective_event["entity"]["id"] = "assignment_missing_objective"
        missing_objective_event["payload"]["assignment"]["id"] = "assignment_missing_objective"
        missing_objective_event["payload"]["assignment"].pop("objective")
        missing_objective = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(missing_objective_event))
        require(missing_objective.returncode != 0 and "objective" in missing_objective.stderr, "queue_db.py は成果条件のないassignmentを拒否してください。")

        trial_event = {
            "event_id": "evt_trial_focus_smoke",
            "timestamp": "2026-01-02T03:04:04+00:00",
            "actor": "validator",
            "event_type": "trial_recorded",
            "entity": {"type": "trial", "id": "trial_focus_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["trial"], "decision_rationale": "focus lineage smoke", "evidence_refs": []},
            "payload": {"trial": {
                "id": "trial_focus_smoke",
                "quest_id": "quest_smoke",
                "worker_id": "inquisitor",
                "role": "trial_lead",
                "depth": "focused_trial",
                "objective": "完了済みbounded resultのauthorization focusを判定する",
                "success_criteria": ["authorization focusの根拠とdecisionを返す"],
                "subject_assignment_ids": ["assignment_owner_smoke"],
                "subject_report_ids": [],
                "subject_snapshot": revision_snapshot,
                "authority": {"read": True, "edit": False, "validate": True, "local_git": False, "external_actions": False},
                "boundaries": {
                    "target_repo_root": str(target_repo),
                    "read_deny": ["private/**"],
                    "edit_deny": ["**"],
                    "safety_items": ["no_secret_access"],
                },
                "status": "active",
            }},
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        trial_result = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(trial_event))
        require(trial_result.returncode == 0, "queue_db.py Trial fixtureの記録に失敗しました: " + trial_result.stderr)

        invalid_trial_worker_event = json.loads(json.dumps(trial_event))
        invalid_trial_worker_event["event_id"] = "evt_trial_invalid_worker"
        invalid_trial_worker_event["entity"]["id"] = "trial_invalid_worker"
        invalid_trial_worker_event["payload"]["trial"]["id"] = "trial_invalid_worker"
        invalid_trial_worker_event["payload"]["trial"]["worker_id"] = "sage"
        invalid_trial_worker = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_trial_worker_event))
        require(invalid_trial_worker.returncode != 0 and "inquisitor" in invalid_trial_worker.stderr, "queue_db.py はinquisitor以外のTrial ownerを拒否してください。")

        focus_assignment_event = {
            "event_id": "evt_assignment_focus_smoke",
            "timestamp": "2026-01-02T03:04:04+00:00",
            "actor": "validator",
            "event_type": "assignment_created",
            "entity": {"type": "assignment", "id": "assignment_focus_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["assignment", "trial"], "decision_rationale": "verified Trial lineage smoke", "evidence_refs": ["trial_focus_smoke"]},
            "payload": {"assignment": {
                "id": "assignment_focus_smoke",
                "quest_id": "quest_smoke",
                "trial_id": "trial_focus_smoke",
                "worker_id": "examiner",
                "owner_worker_id": "inquisitor",
                "role": "bounded_trial_examiner",
                "terminal_worker": True,
                "objective": "authorization focusの根拠を独立確認する",
                "risk_trigger": "security",
                "focus": "authorization before write",
                "evidence_required": ["file/lineと再現可能な根拠"],
                "caller_lineage": {"required_parent_role": "inquisitor", "trial_owner_worker_id": "inquisitor", "trial_ref": "trial_focus_smoke", "verification": None},
                "authority": {"read": True, "edit": False, "validate": True, "local_git": False, "external_actions": False},
                "boundaries": {
                    "target_repo_root": str(target_repo),
                    "read_deny": ["private/**"],
                    "edit_deny": ["**"],
                    "safety_items": ["no_secret_access", "no_external_action"],
                },
                "subject_snapshot": revision_snapshot,
                "status": "done",
            }},
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        focus_assignment = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(focus_assignment_event))
        require(focus_assignment.returncode == 0, "queue_db.py は実在Trialと一致するfocus assignmentを受け付けてください: " + focus_assignment.stderr)

        missing_risk_event = json.loads(json.dumps(focus_assignment_event))
        missing_risk_event["event_id"] = "evt_assignment_focus_missing_risk"
        missing_risk_event["entity"]["id"] = "assignment_focus_missing_risk"
        missing_risk_event["payload"]["assignment"]["id"] = "assignment_focus_missing_risk"
        missing_risk_event["payload"]["assignment"].pop("risk_trigger")
        missing_risk = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(missing_risk_event))
        require(missing_risk.returncode != 0 and "risk_trigger" in missing_risk.stderr, "queue_db.py はrisk_triggerなしのexaminer assignmentを拒否してください。")

        dropped_safety_event = json.loads(json.dumps(focus_assignment_event))
        dropped_safety_event["event_id"] = "evt_assignment_focus_dropped_safety"
        dropped_safety_event["entity"]["id"] = "assignment_focus_dropped_safety"
        dropped_safety_event["payload"]["assignment"]["id"] = "assignment_focus_dropped_safety"
        dropped_safety_event["payload"]["assignment"]["boundaries"]["safety_items"] = []
        dropped_safety = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(dropped_safety_event))
        require(dropped_safety.returncode != 0 and "safety_items" in dropped_safety.stderr, "queue_db.py は親Trialのsafety boundaryを落とすexaminer assignmentを拒否してください。")

        sibling_target_event = json.loads(json.dumps(focus_assignment_event))
        sibling_target_event["event_id"] = "evt_assignment_focus_sibling_target"
        sibling_target_event["entity"]["id"] = "assignment_focus_sibling_target"
        sibling_target_event["payload"]["assignment"]["id"] = "assignment_focus_sibling_target"
        sibling_target_event["payload"]["assignment"]["boundaries"]["target_repo_root"] = str(sibling_repo)
        sibling_target = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(sibling_target_event))
        require(sibling_target.returncode != 0 and "target_repo_root" in sibling_target.stderr, "queue_db.py は親Trialと異なるtargetのexaminer assignmentを拒否してください。")

        wrong_caller_event = json.loads(json.dumps(focus_assignment_event))
        wrong_caller_event["event_id"] = "evt_assignment_focus_wrong_caller"
        wrong_caller_event["entity"]["id"] = "assignment_focus_wrong_caller"
        wrong_caller_event["payload"]["assignment"]["id"] = "assignment_focus_wrong_caller"
        wrong_caller_event["payload"]["assignment"]["caller_lineage"]["required_parent_role"] = "root"
        wrong_caller = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(wrong_caller_event))
        require(wrong_caller.returncode != 0 and "caller_lineage" in wrong_caller.stderr, "queue_db.py はinquisitor以外のexaminer caller lineageを拒否してください。")

        wrong_owner_event = json.loads(json.dumps(focus_assignment_event))
        wrong_owner_event["event_id"] = "evt_assignment_focus_wrong_owner"
        wrong_owner_event["entity"]["id"] = "assignment_focus_wrong_owner"
        wrong_owner_event["payload"]["assignment"]["id"] = "assignment_focus_wrong_owner"
        wrong_owner_event["payload"]["assignment"]["owner_worker_id"] = "root"
        wrong_owner = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(wrong_owner_event))
        require(wrong_owner.returncode != 0 and "owner_worker_id" in wrong_owner.stderr, "queue_db.py はinquisitor以外のexaminer ownerを拒否してください。")

        invalid_focus_event = json.loads(json.dumps(focus_assignment_event))
        invalid_focus_event["event_id"] = "evt_assignment_focus_fake_trial"
        invalid_focus_event["entity"]["id"] = "assignment_focus_fake_trial"
        invalid_focus_event["payload"]["assignment"]["id"] = "assignment_focus_fake_trial"
        invalid_focus_event["payload"]["assignment"]["trial_id"] = "trial_missing"
        invalid_focus_event["payload"]["assignment"]["caller_lineage"]["trial_ref"] = "trial_missing"
        invalid_focus = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_focus_event))
        require(invalid_focus.returncode != 0 and "Trial" in invalid_focus.stderr, "queue_db.py は自己申告だけのexaminer lineageを拒否してください。")

        inquisitor_report_event = {
            "event_id": "evt_report_inquisitor_smoke",
            "timestamp": "2026-01-02T03:04:04+00:00",
            "actor": "validator",
            "event_type": "report_recorded",
            "entity": {"type": "report", "id": "report_inquisitor_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["report", "trial"], "decision_rationale": "Trial decision lineage smoke", "evidence_refs": ["trial_focus_smoke"]},
            "payload": {"report": {
                "id": "report_inquisitor_smoke",
                "quest_id": "quest_smoke",
                "trial_id": "trial_focus_smoke",
                "worker_id": "inquisitor",
                "target_repo_root": str(target_repo),
                "status": "recorded",
                "decision": "accept",
                "trial_depth": "focused_trial",
                "subject_snapshot": revision_snapshot,
                "examiner_reports": [],
                "summary": "authorization focusを統合した",
                "evidence_refs": ["app.py:1"],
                "finding_dispositions": {"adopted": [], "rejected": [], "unresolved": []},
            }},
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        incomplete_inquisitor_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(inquisitor_report_event))
        require(incomplete_inquisitor_report.returncode != 0 and "完了examiner report" in incomplete_inquisitor_report.stderr, "queue_db.py はexaminer report欠落中のinquisitor finalを拒否してください。")

        second_trial_event = json.loads(json.dumps(trial_event))
        second_trial_event["event_id"] = "evt_trial_focus_second_smoke"
        second_trial_event["entity"]["id"] = "trial_focus_second_smoke"
        second_trial_event["payload"]["trial"]["id"] = "trial_focus_second_smoke"
        second_trial_result = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(second_trial_event))
        require(second_trial_result.returncode == 0, "queue_db.py second Trial fixtureの記録に失敗しました: " + second_trial_result.stderr)

        examiner_report_event = {
            "event_id": "evt_report_examiner_smoke",
            "timestamp": "2026-01-02T03:04:04+00:00",
            "actor": "validator",
            "event_type": "report_recorded",
            "entity": {"type": "report", "id": "report_examiner_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["report", "assignment", "trial"], "decision_rationale": "examiner report lineage smoke", "evidence_refs": ["trial_focus_smoke"]},
            "payload": {"report": {
                "id": "report_examiner_smoke",
                "quest_id": "quest_smoke",
                "trial_id": "trial_focus_smoke",
                "assignment_id": "assignment_focus_smoke",
                "worker_id": "examiner",
                "owner_worker_id": "inquisitor",
                "terminal_worker": True,
                "decision_authority": False,
                "severity_authority": False,
                "risk_trigger": "security",
                "focus": "authorization before write",
                "subject_snapshot": revision_snapshot,
                "caller_lineage_check": {"required_parent_role": "root", "trial_owner_worker_id": "root", "trial_ref": "self_claim", "verified": False, "status": "unverifiable"},
                "snapshot_check": {"start_match": False, "report_match": False, "status": "stale_evidence"},
                "summary": "authorization boundaryに要修正候補がある",
                "finding_candidates": [{"id": "finding.authorization", "summary": "write前のauthorization checkを確認する"}],
                "evidence_refs": ["app.py:1"],
                "important_unknowns": [{"id": "unknown.coverage", "summary": "全呼出経路のcoverageは未確認"}],
                "residual_risks": ["未確認経路が残る"],
                "status": "recorded",
            }},
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }

        wrong_assignment_report_event = json.loads(json.dumps(examiner_report_event))
        wrong_assignment_report_event["event_id"] = "evt_report_examiner_wrong_assignment"
        wrong_assignment_report_event["entity"]["id"] = "report_examiner_wrong_assignment"
        wrong_assignment_report_event["payload"]["report"]["id"] = "report_examiner_wrong_assignment"
        wrong_assignment_report_event["payload"]["report"]["assignment_id"] = "assignment_missing"
        wrong_assignment_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(wrong_assignment_report_event))
        require(wrong_assignment_report.returncode != 0 and "assignment" in wrong_assignment_report.stderr, "queue_db.py はwrong examiner assignment reportを拒否してください。")

        wrong_trial_report_event = json.loads(json.dumps(examiner_report_event))
        wrong_trial_report_event["event_id"] = "evt_report_examiner_wrong_trial"
        wrong_trial_report_event["entity"]["id"] = "report_examiner_wrong_trial"
        wrong_trial_report_event["payload"]["report"]["id"] = "report_examiner_wrong_trial"
        wrong_trial_report_event["payload"]["report"]["trial_id"] = "trial_focus_second_smoke"
        wrong_trial_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(wrong_trial_report_event))
        require(wrong_trial_report.returncode != 0 and "assignment lineage" in wrong_trial_report.stderr, "queue_db.py は別Trialのexaminer reportを拒否してください。")

        stale_examiner_report_event = json.loads(json.dumps(examiner_report_event))
        stale_examiner_report_event["event_id"] = "evt_report_examiner_stale_snapshot"
        stale_examiner_report_event["entity"]["id"] = "report_examiner_stale_snapshot"
        stale_examiner_report_event["payload"]["report"]["id"] = "report_examiner_stale_snapshot"
        stale_examiner_report_event["payload"]["report"]["subject_snapshot"] = other_result_snapshot
        stale_examiner_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(stale_examiner_report_event))
        require(stale_examiner_report.returncode != 0 and "snapshot" in stale_examiner_report.stderr, "queue_db.py はstale examiner report snapshotを拒否してください。")

        missing_evidence_report_event = json.loads(json.dumps(examiner_report_event))
        missing_evidence_report_event["event_id"] = "evt_report_examiner_missing_evidence"
        missing_evidence_report_event["entity"]["id"] = "report_examiner_missing_evidence"
        missing_evidence_report_event["payload"]["report"]["id"] = "report_examiner_missing_evidence"
        missing_evidence_report_event["payload"]["report"]["evidence_refs"] = []
        missing_evidence_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(missing_evidence_report_event))
        require(missing_evidence_report.returncode != 0 and "evidence_refs" in missing_evidence_report.stderr, "queue_db.py はevidenceなしのexaminer reportを拒否してください。")

        examiner_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(examiner_report_event))
        require(examiner_report.returncode == 0, "queue_db.py はassignment/Trialへbindしたexaminer reportを受け付けてください: " + examiner_report.stderr)

        inquisitor_report_event["payload"]["report"]["examiner_reports"] = ["report_examiner_smoke"]
        undispositioned_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(inquisitor_report_event))
        require(undispositioned_report.returncode != 0 and "exactly once" in undispositioned_report.stderr, "queue_db.py は未処置examiner finding/unknownのあるinquisitor finalを拒否してください。")

        inquisitor_report_event["payload"]["report"]["finding_dispositions"] = {
            "adopted": ["finding.authorization"],
            "rejected": ["unknown.coverage"],
            "unresolved": [],
        }
        inquisitor_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(inquisitor_report_event))
        require(inquisitor_report.returncode == 0, "queue_db.py はTrialにbindしたinquisitor reportを受け付けてください: " + inquisitor_report.stderr)

        invalid_inquisitor_report_event = json.loads(json.dumps(inquisitor_report_event))
        invalid_inquisitor_report_event["event_id"] = "evt_report_inquisitor_fake_trial"
        invalid_inquisitor_report_event["entity"]["id"] = "report_inquisitor_fake_trial"
        invalid_inquisitor_report_event["payload"]["report"]["id"] = "report_inquisitor_fake_trial"
        invalid_inquisitor_report_event["payload"]["report"]["trial_id"] = "trial_missing"
        invalid_inquisitor_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_inquisitor_report_event))
        require(invalid_inquisitor_report.returncode != 0 and "Trial" in invalid_inquisitor_report.stderr, "queue_db.py は参照Trialのないinquisitor reportを拒否してください。")

        upstream_report_event = {
            "event_id": "evt_report_upstream_smoke",
            "timestamp": "2026-01-02T03:04:04+00:00",
            "actor": "validator",
            "event_type": "report_recorded",
            "entity": {"type": "report", "id": "report_upstream_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["report"], "decision_rationale": "integration barrier smoke", "evidence_refs": []},
            "payload": {"report": {
                "id": "report_upstream_smoke",
                "quest_id": "quest_smoke",
                "assignment_id": "assignment_owner_smoke",
                "worker_id": "adventurer",
                "target_repo_root": str(target_repo),
                "base_snapshot": revision_snapshot,
                "result_snapshot": app_result_snapshot,
                "status": "recorded",
            }},
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        upstream_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(upstream_report_event))
        require(upstream_report.returncode == 0, "queue_db.py upstream report fixtureの記録に失敗しました: " + upstream_report.stderr)

        second_report_event = json.loads(json.dumps(upstream_report_event))
        second_report_event["event_id"] = "evt_report_upstream_second_smoke"
        second_report_event["entity"]["id"] = "report_upstream_second_smoke"
        second_report_event["payload"]["report"]["id"] = "report_upstream_second_smoke"
        second_report_event["payload"]["report"]["assignment_id"] = "assignment_owner_second_smoke"
        second_report_event["payload"]["report"]["result_snapshot"] = other_result_snapshot
        second_report = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(second_report_event))
        require(second_report.returncode == 0, "queue_db.py second upstream report fixtureの記録に失敗しました: " + second_report.stderr)

        integration_command_event = {
            "event_id": "evt_command_integration_smoke",
            "timestamp": "2026-01-02T03:04:04+00:00",
            "actor": "validator",
            "event_type": "command_created",
            "entity": {"type": "command", "id": "cmd_integration_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["command", "reports"], "decision_rationale": "required report setを統合前に固定", "evidence_refs": []},
            "payload": {"command": {
                "id": "cmd_integration_smoke",
                "quest_id": "quest_smoke",
                "status": "issued",
                "integration_contract": {
                    "artificer": "artificer",
                    "mutation_barrier_required": True,
                    "required_assignment_ids": ["assignment_owner_smoke", "assignment_owner_second_smoke"],
                    "required_report_refs": ["report_upstream_smoke", "report_upstream_second_smoke"],
                    "integration_scope": {
                        "read": ["app.py", "other.py"],
                        "edit": ["app.py"],
                        "validate": ["app.py", "other.py"],
                    },
                },
            }},
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        integration_command = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(integration_command_event))
        require(integration_command.returncode == 0, "queue_db.py integration command fixtureの記録に失敗しました: " + integration_command.stderr)

        integration_assignment_event = json.loads(json.dumps(owner_assignment_event))
        integration_assignment_event["event_id"] = "evt_assignment_integration_smoke"
        integration_assignment_event["entity"]["id"] = "assignment_integration_smoke"
        integration_payload = integration_assignment_event["payload"]["assignment"]
        integration_payload["id"] = "assignment_integration_smoke"
        integration_payload["worker_id"] = "artificer"
        integration_payload["role"] = "cross_scope_artificer"
        integration_payload["objective"] = "完了済みbounded resultを共有契約へ統合する"
        integration_payload["owned_scope"] = {
            "read": ["app.py", "other.py"],
            "edit": ["app.py"],
            "validate": ["app.py", "other.py"],
        }
        integration_payload["integration_barrier"] = {
            "status": "complete",
            "mutation_stopped": True,
            "contract_ref": "cmd_integration_smoke",
            "upstream_report_refs": ["report_upstream_smoke", "report_upstream_second_smoke"],
        }
        integration_assignment = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(integration_assignment_event))
        require(integration_assignment.returncode == 0, "queue_db.py は完了reportに裏付けられたintegration barrierを受け付けてください: " + integration_assignment.stderr)

        invalid_integration_event = json.loads(json.dumps(integration_assignment_event))
        invalid_integration_event["event_id"] = "evt_assignment_integration_fake_report"
        invalid_integration_event["entity"]["id"] = "assignment_integration_fake_report"
        invalid_integration_event["payload"]["assignment"]["id"] = "assignment_integration_fake_report"
        invalid_integration_event["payload"]["assignment"]["integration_barrier"] = {
            "status": "complete",
            "mutation_stopped": True,
            "contract_ref": "cmd_integration_smoke",
            "upstream_report_refs": ["report_upstream_smoke"],
        }
        invalid_integration = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_integration_event))
        require(invalid_integration.returncode != 0 and "required_report_refs" in invalid_integration.stderr, "queue_db.py はrequired reportを省いた不完全barrierを拒否してください。")

        message = {
            "id": "msg_valid_smoke",
            "sender": "validator",
            "recipient": "adventurer",
            "created_at": "2026-01-02T03:04:05+00:00",
            "type": "message",
            "trusted": False,
            "workflow_id": "workflow_smoke",
            "payload": {"summary": "positive smoke"},
            "status": "unread",
        }
        inbox = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(message))
        require(inbox.returncode == 0, "queue_db.py add-inbox-message の smoke が失敗しました: " + inbox.stderr)

        trusted_message = dict(message)
        trusted_message["id"] = "msg_invalid_trusted_generic"
        trusted_message["trusted"] = True
        trusted_inbox = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(trusted_message))
        require(trusted_inbox.returncode != 0 and "trusted" in trusted_inbox.stderr, "queue_db.py は generic inbox message の trusted=true を拒否してください。")

        memory_message = _memory_candidate_message()
        memory_inbox = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(memory_message))
        require(memory_inbox.returncode == 0, "queue_db.py は正しい memory candidate envelope を受け付けてください: " + memory_inbox.stderr)

        invalid_memory_message = _memory_candidate_message("msg_invalid_memory_candidate")
        invalid_payload = invalid_memory_message["payload"]
        require(isinstance(invalid_payload, dict), "memory candidate smoke payload は dict にしてください。")
        invalid_payload["explicit_memory_persistence_authority"] = False
        invalid_memory = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(invalid_memory_message))
        require(invalid_memory.returncode != 0 and "explicit_memory_persistence_authority" in invalid_memory.stderr, "queue_db.py は authority 不足の memory candidate を拒否してください。")

        empty_artifact_message = _memory_candidate_message("msg_invalid_memory_candidate_empty_artifact")
        empty_artifact_payload = empty_artifact_message["payload"]
        require(isinstance(empty_artifact_payload, dict), "empty artifact memory candidate payload は dict にしてください。")
        empty_artifact_payload["prevention_artifact"] = {}
        empty_artifact = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(empty_artifact_message))
        require(empty_artifact.returncode != 0 and "prevention_artifact" in empty_artifact.stderr, "queue_db.py は空の prevention_artifact を拒否してください。")

        invalid_recipient_message = _memory_candidate_message("msg_invalid_memory_candidate_recipient")
        invalid_recipient_message["recipient"] = "adventurer"
        invalid_recipient = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(invalid_recipient_message))
        require(invalid_recipient.returncode != 0 and "recipient" in invalid_recipient.stderr, "queue_db.py は courier 宛ではない memory candidate を拒否してください。")

        invalid_sender_message = _memory_candidate_message("msg_invalid_memory_candidate_sender")
        invalid_sender_message["sender"] = "adventurer"
        invalid_sender = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(invalid_sender_message))
        require(invalid_sender.returncode != 0 and "sender" in invalid_sender.stderr, "queue_db.py は courier sender ではない memory candidate を拒否してください。")

        invalid_trusted_message = _memory_candidate_message("msg_invalid_memory_candidate_trusted")
        invalid_trusted_message["trusted"] = True
        invalid_trusted = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(invalid_trusted_message))
        require(invalid_trusted.returncode != 0 and "trusted" in invalid_trusted.stderr, "queue_db.py は trusted=true の memory candidate を拒否してください。")

        alias_payload = _memory_candidate_message("msg_invalid_memory_candidate_alias")["payload"]
        alias_message = {
            "id": "msg_invalid_memory_candidate_alias",
            "sender": "courier",
            "recipient": "courier",
            "created_at": "2026-01-02T03:04:07+00:00",
            "type": "message",
            "trusted": False,
            "workflow_id": "workflow_smoke",
            "payload": {"memory_candidate": alias_payload},
            "status": "unread",
        }
        invalid_alias = _run_python(script, "--runtime-root", runtime_root, "add-inbox-message", json.dumps(alias_message))
        require(invalid_alias.returncode != 0 and "memory_candidate_for_courier_review" in invalid_alias.stderr, "queue_db.py は generic memory_candidate alias を拒否してください。")

        invalid_memory_event = {
            "event_id": "evt_invalid_memory_candidate_safety",
            "timestamp": "2026-01-02T03:04:07+00:00",
            "actor": "courier",
            "event_type": "inbox_message_added",
            "entity": {"type": "message", "id": "msg_invalid_memory_candidate_safety"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["message"], "decision_rationale": "memory_candidate 安全性検証", "evidence_refs": []},
            "payload": _memory_candidate_message("msg_invalid_memory_candidate_safety"),
            "event_safety": {"safety_items": [], "human_confirmation_required": []},
        }
        invalid_memory_event_result = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_memory_event))
        require(invalid_memory_event_result.returncode != 0 and "memory_candidate_gate" in invalid_memory_event_result.stderr, "queue_db.py record-event は memory_candidate の safety_metadata 不足を拒否してください。")

        invalid_memory_actor_event = {
            "event_id": "evt_invalid_memory_candidate_actor",
            "timestamp": "2026-01-02T03:04:08+00:00",
            "actor": "adventurer",
            "event_type": "inbox_message_added",
            "entity": {"type": "message", "id": "msg_invalid_memory_candidate_actor"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["message"], "decision_rationale": "memory_candidate actor 境界検証", "evidence_refs": []},
            "payload": _memory_candidate_message("msg_invalid_memory_candidate_actor"),
            "event_safety": {
                "safety_items": [
                    "memory_candidate_for_courier_review",
                    "explicit_memory_persistence_authority",
                    "sanitized_summary_only",
                    "prevention_artifact_required",
                    "ledger_disposition_recorded",
                ],
                "human_confirmation_required": [],
            },
        }
        invalid_memory_actor = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_memory_actor_event))
        require(invalid_memory_actor.returncode != 0 and "actor" in invalid_memory_actor.stderr, "queue_db.py record-event は courier actor ではない memory candidate を拒否してください。")

        invalid_memory_non_message_event = {
            "event_id": "evt_invalid_memory_candidate_non_message",
            "timestamp": "2026-01-02T03:04:09+00:00",
            "actor": "courier",
            "event_type": "quest_updated",
            "entity": {"type": "quest", "id": "quest_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["quest"], "decision_rationale": "memory_candidate entity 境界検証", "evidence_refs": []},
            "payload": _memory_candidate_message("msg_invalid_memory_candidate_non_message"),
            "event_safety": {
                "safety_items": [
                    "memory_candidate_for_courier_review",
                    "explicit_memory_persistence_authority",
                    "sanitized_summary_only",
                    "prevention_artifact_required",
                    "ledger_disposition_recorded",
                ],
                "human_confirmation_required": [],
            },
        }
        invalid_memory_non_message = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_memory_non_message_event))
        require(invalid_memory_non_message.returncode != 0 and "message" in invalid_memory_non_message.stderr, "queue_db.py record-event は non-message entity の memory candidate payload を拒否してください。")

        invalid_nested_memory_event = {
            "event_id": "evt_invalid_nested_memory_candidate",
            "timestamp": "2026-01-02T03:04:10+00:00",
            "actor": "courier",
            "event_type": "quest_updated",
            "entity": {"type": "quest", "id": "quest_smoke"},
            "operation": "append",
            "workflow_id": "workflow_smoke",
            "structured_data_usage": {"structured_inputs": ["quest"], "decision_rationale": "nested memory_candidate 境界検証", "evidence_refs": []},
            "payload": {"quest": {"id": "quest_smoke", "memory_candidate": _memory_candidate_message("msg_invalid_nested_memory_candidate")["payload"]}},
            "event_safety": {
                "safety_items": [
                    "memory_candidate_for_courier_review",
                    "explicit_memory_persistence_authority",
                    "sanitized_summary_only",
                    "prevention_artifact_required",
                    "ledger_disposition_recorded",
                ],
                "human_confirmation_required": [],
            },
        }
        invalid_nested_memory = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_nested_memory_event))
        require(invalid_nested_memory.returncode != 0 and "memory_candidate_for_courier_review" in invalid_nested_memory.stderr, "queue_db.py record-event は nested memory_candidate payload を拒否してください。")

        database = runtime_root / "queue" / "state.sqlite"
        with sqlite3.connect(database) as connection:
            quest_count = connection.execute("SELECT COUNT(*) FROM quests WHERE quest_id = 'quest_smoke'").fetchone()[0]
            inbox_count = connection.execute("SELECT COUNT(*) FROM inbox_messages WHERE message_id = 'msg_valid_smoke'").fetchone()[0]
            memory_count = connection.execute("SELECT COUNT(*) FROM inbox_messages WHERE message_id = 'msg_memory_candidate_smoke'").fetchone()[0]
            safety = connection.execute("SELECT event_safety_json FROM events WHERE event_id = 'evt_message_msg_memory_candidate_smoke'").fetchone()[0]
        require(quest_count == 1 and inbox_count == 1 and memory_count == 1, "queue_db.py smoke の DB 反映件数が不正です。")
        require("memory_candidate_for_courier_review" in safety and "ledger_disposition_recorded" in safety, "queue_db.py は memory candidate の safety metadata を記録してください。")

        audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        require(audit.returncode == 0, "queue_audit.py の smoke が失敗しました: " + (audit.stderr or audit.stdout))

        with sqlite3.connect(database) as connection:
            original_report_payload = connection.execute(
                "SELECT payload_json FROM reports WHERE report_id = 'report_examiner_smoke'"
            ).fetchone()[0]
            original_event_payload = connection.execute(
                "SELECT payload_json FROM events WHERE event_id = 'evt_report_examiner_smoke'"
            ).fetchone()[0]
            bad_report_payload = json.loads(original_report_payload)
            bad_report_payload["owner_worker_id"] = "root"
            bad_event_payload = json.loads(original_event_payload)
            bad_event_payload["report"]["owner_worker_id"] = "root"
            connection.execute(
                "UPDATE reports SET payload_json = ? WHERE report_id = 'report_examiner_smoke'",
                (json.dumps(bad_report_payload),),
            )
            connection.execute(
                "UPDATE events SET payload_json = ? WHERE event_id = 'evt_report_examiner_smoke'",
                (json.dumps(bad_event_payload),),
            )
            connection.commit()
        wrong_owner_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        require(wrong_owner_audit.returncode != 0 and "owner_worker_id" in wrong_owner_audit.stdout, "queue_audit.py はinquisitor以外のexaminer report ownerを拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE reports SET payload_json = ? WHERE report_id = 'report_examiner_smoke'",
                (original_report_payload,),
            )
            connection.execute(
                "UPDATE events SET payload_json = ? WHERE event_id = 'evt_report_examiner_smoke'",
                (original_event_payload,),
            )
            connection.commit()

        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE events SET entity_id = ? WHERE event_id = 'evt_smoke_quest_created'", ("quest_other",))
            connection.commit()
        bad_entity_identity_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        require(bad_entity_identity_audit.returncode != 0 and "payload id" in bad_entity_identity_audit.stdout, "queue_audit.py はevent entity/payload id不一致を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE events SET entity_id = ? WHERE event_id = 'evt_smoke_quest_created'", ("quest_smoke",))
            connection.execute("UPDATE events SET workflow_id = ? WHERE event_id = 'evt_smoke_quest_created'", ("workflow_other",))
            connection.commit()
        bad_workflow_identity_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        require(bad_workflow_identity_audit.returncode != 0 and "workflow_id" in bad_workflow_identity_audit.stdout, "queue_audit.py はevent/payload workflow不一致を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE events SET workflow_id = ? WHERE event_id = 'evt_smoke_quest_created'", ("workflow_smoke",))
            connection.commit()

        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE assignments SET status = ? WHERE assignment_id = 'assignment_owner_smoke'", ("active",))
            connection.commit()
        bad_materialized_status_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        require(bad_materialized_status_audit.returncode != 0 and "materialized value" in bad_materialized_status_audit.stdout, "queue_audit.py はassignment row/payload status driftを拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE assignments SET status = ? WHERE assignment_id = 'assignment_owner_smoke'", ("done",))
            connection.commit()

        bad_generic_trusted_payload = dict(message)
        bad_generic_trusted_payload["id"] = "msg_bad_audit_trusted_generic"
        bad_generic_trusted_payload["trusted"] = True
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO inbox_messages(message_id, recipient, workflow_id, status, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    bad_generic_trusted_payload["id"],
                    bad_generic_trusted_payload["recipient"],
                    bad_generic_trusted_payload["workflow_id"],
                    bad_generic_trusted_payload["status"],
                    json.dumps(bad_generic_trusted_payload),
                    bad_generic_trusted_payload["created_at"],
                ),
            )
            connection.commit()
        bad_generic_trusted_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        bad_generic_trusted_output = bad_generic_trusted_audit.stdout + bad_generic_trusted_audit.stderr
        require(bad_generic_trusted_audit.returncode != 0 and "trusted" in bad_generic_trusted_output, "queue_audit.py は generic inbox message の trusted=true を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM inbox_messages WHERE message_id = ?", (bad_generic_trusted_payload["id"],))
            connection.commit()

        bad_audit_payload = _memory_candidate_message("msg_bad_audit_memory_candidate")
        bad_payload = bad_audit_payload["payload"]
        require(isinstance(bad_payload, dict), "bad audit memory candidate payload は dict にしてください。")
        bad_payload["raw_log"] = "生ログは永続化しない"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO inbox_messages(message_id, recipient, workflow_id, status, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    bad_audit_payload["id"],
                    bad_audit_payload["recipient"],
                    bad_audit_payload["workflow_id"],
                    bad_audit_payload["status"],
                    json.dumps(bad_audit_payload),
                    bad_audit_payload["created_at"],
                ),
            )
            connection.commit()
        bad_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        bad_audit_output = bad_audit.stdout + bad_audit.stderr
        require(bad_audit.returncode != 0 and "raw_log" in bad_audit_output, "queue_audit.py は raw_log を含む memory candidate を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM inbox_messages WHERE message_id = ?", (bad_audit_payload["id"],))
            connection.commit()

        bad_audit_recipient = _memory_candidate_message("msg_bad_audit_memory_candidate_recipient")
        bad_audit_recipient["recipient"] = "adventurer"
        bad_audit_sender = _memory_candidate_message("msg_bad_audit_memory_candidate_sender")
        bad_audit_sender["sender"] = "adventurer"
        bad_audit_trusted = _memory_candidate_message("msg_bad_audit_memory_candidate_trusted")
        bad_audit_trusted["trusted"] = True
        bad_audit_alias_payload = _memory_candidate_message("msg_bad_audit_memory_candidate_alias")["payload"]
        bad_audit_alias = {
            "id": "msg_bad_audit_memory_candidate_alias",
            "sender": "courier",
            "recipient": "courier",
            "created_at": "2026-01-02T03:04:06+00:00",
            "type": "message",
            "trusted": False,
            "workflow_id": "workflow_smoke",
            "payload": {"memory_candidate": bad_audit_alias_payload},
            "status": "unread",
        }
        for bad_scope_payload, expected_token in (
            (bad_audit_recipient, "recipient"),
            (bad_audit_sender, "sender"),
            (bad_audit_trusted, "trusted"),
            (bad_audit_alias, "memory_candidate_for_courier_review"),
        ):
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "INSERT INTO inbox_messages(message_id, recipient, workflow_id, status, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        bad_scope_payload["id"],
                        bad_scope_payload["recipient"],
                        bad_scope_payload["workflow_id"],
                        bad_scope_payload["status"],
                        json.dumps(bad_scope_payload),
                        bad_scope_payload["created_at"],
                    ),
                )
                connection.commit()
            bad_scope_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
            bad_scope_audit_output = bad_scope_audit.stdout + bad_scope_audit.stderr
            require(bad_scope_audit.returncode != 0 and expected_token in bad_scope_audit_output, f"queue_audit.py は scope 不正の memory candidate を拒否してください: {expected_token}")
            with sqlite3.connect(database) as connection:
                connection.execute("DELETE FROM inbox_messages WHERE message_id = ?", (bad_scope_payload["id"],))
                connection.commit()

        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE events SET actor = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'", ("adventurer",))
            connection.commit()
        bad_actor_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        bad_actor_output = bad_actor_audit.stdout + bad_actor_audit.stderr
        require(bad_actor_audit.returncode != 0 and "actor" in bad_actor_output, "queue_audit.py は courier actor ではない memory candidate event を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE events SET actor = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'", ("courier",))
            connection.commit()

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE events SET event_type = ?, entity_type = ?, entity_id = ?, payload_json = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'",
                (
                    "quest_updated",
                    "quest",
                    "quest_smoke",
                    json.dumps({"quest": {"id": "quest_smoke", "memory_candidate": _memory_candidate_message("msg_bad_audit_nested_memory_candidate")["payload"]}}),
                ),
            )
            connection.commit()
        bad_nested_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        bad_nested_output = bad_nested_audit.stdout + bad_nested_audit.stderr
        require(bad_nested_audit.returncode != 0 and "memory_candidate_for_courier_review" in bad_nested_output, "queue_audit.py は nested memory_candidate event を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE events SET event_type = ?, entity_type = ?, entity_id = ?, payload_json = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'",
                ("inbox_message_added", "message", "msg_memory_candidate_smoke", json.dumps(memory_message)),
            )
            connection.commit()

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE events SET event_type = ?, entity_type = ?, entity_id = ?, payload_json = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'",
                ("quest_updated", "quest", "quest_smoke", json.dumps(_memory_candidate_message("msg_bad_audit_memory_candidate_non_message"))),
            )
            connection.commit()
        bad_entity_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        bad_entity_output = bad_entity_audit.stdout + bad_entity_audit.stderr
        require(bad_entity_audit.returncode != 0 and "message entity" in bad_entity_output, "queue_audit.py は non-message entity の memory candidate event を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE events SET event_type = ?, entity_type = ?, entity_id = ?, payload_json = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'",
                ("inbox_message_added", "message", "msg_memory_candidate_smoke", json.dumps(memory_message)),
            )
            connection.commit()

        bad_empty_artifact_payload = _memory_candidate_message("msg_bad_audit_empty_artifact")
        bad_empty_body = bad_empty_artifact_payload["payload"]
        require(isinstance(bad_empty_body, dict), "bad audit empty artifact payload は dict にしてください。")
        bad_empty_body["prevention_artifact"] = {}
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO inbox_messages(message_id, recipient, workflow_id, status, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    bad_empty_artifact_payload["id"],
                    bad_empty_artifact_payload["recipient"],
                    bad_empty_artifact_payload["workflow_id"],
                    bad_empty_artifact_payload["status"],
                    json.dumps(bad_empty_artifact_payload),
                    bad_empty_artifact_payload["created_at"],
                ),
            )
            connection.commit()
        bad_empty_artifact_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        bad_empty_artifact_output = bad_empty_artifact_audit.stdout + bad_empty_artifact_audit.stderr
        require(bad_empty_artifact_audit.returncode != 0 and "prevention_artifact" in bad_empty_artifact_output, "queue_audit.py は空の prevention_artifact を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM inbox_messages WHERE message_id = ?", (bad_empty_artifact_payload["id"],))
            connection.commit()

        malformed_audit_payload = _memory_candidate_message("msg_malformed_audit_memory_candidate")
        malformed_audit_payload.pop("payload")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO inbox_messages(message_id, recipient, workflow_id, status, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    malformed_audit_payload["id"],
                    malformed_audit_payload["recipient"],
                    malformed_audit_payload["workflow_id"],
                    malformed_audit_payload["status"],
                    json.dumps(malformed_audit_payload),
                    malformed_audit_payload["created_at"],
                ),
            )
            connection.commit()
        malformed_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        malformed_audit_output = malformed_audit.stdout + malformed_audit.stderr
        require(malformed_audit.returncode != 0 and "explicit_memory_persistence_authority" in malformed_audit_output, "queue_audit.py は payload 欠落の memory candidate を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM inbox_messages WHERE message_id = ?", (malformed_audit_payload["id"],))
            connection.commit()

        malformed_event_payload = _memory_candidate_message("msg_malformed_event_memory_candidate")
        malformed_event_payload.pop("payload")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE events SET payload_json = ?, event_safety_json = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'",
                [json.dumps(malformed_event_payload), json.dumps({"safety_items": [], "human_confirmation_required": []})],
            )
            connection.commit()
        malformed_event_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        malformed_event_output = malformed_event_audit.stdout + malformed_event_audit.stderr
        require(malformed_event_audit.returncode != 0 and "memory_candidate_gate" in malformed_event_output, "queue_audit.py は payload 欠落 memory candidate event の safety metadata 不足を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE events SET payload_json = ?, event_safety_json = ? WHERE event_id = 'evt_message_msg_memory_candidate_smoke'",
                [
                    json.dumps(memory_message),
                    json.dumps(
                        {
                            "safety_items": [
                                "memory_candidate_for_courier_review",
                                "explicit_memory_persistence_authority",
                                "sanitized_summary_only",
                                "prevention_artifact_required",
                                "ledger_disposition_recorded",
                            ],
                            "human_confirmation_required": [],
                        }
                    ),
                ],
            )
            connection.commit()

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE quests SET payload_json = ? WHERE quest_id = 'quest_smoke'",
                [json.dumps({"quest": {"id": "quest_smoke", "meta" "cognitive_state": {"confidence_percent": 70}}})],
            )
            connection.commit()
        legacy_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        legacy_output = legacy_audit.stdout + legacy_audit.stderr
        require(legacy_audit.returncode != 0 and "廃止済み" in legacy_output, "queue_audit.py は旧 runtime JSON key を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE quests SET payload_json = ? WHERE quest_id = 'quest_smoke'",
                [json.dumps({"quest": {"id": "quest_smoke", "control_decision": "invoke_" "meta" "cognitive_controller"}})],
            )
            connection.commit()
        legacy_value_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        legacy_value_output = legacy_value_audit.stdout + legacy_value_audit.stderr
        require(legacy_value_audit.returncode != 0 and "廃止済み" in legacy_value_output, "queue_audit.py は旧 runtime string value を拒否してください。")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE quests SET payload_json = ? WHERE quest_id = 'quest_smoke'",
                [json.dumps({"quest": {"id": "quest_smoke", "rank": "solo_quest", "status": "active", "workflow_id": "workflow_smoke"}})],
            )
            connection.commit()

        invalid_event = _valid_event()
        invalid_event["event_id"] = "evt_invalid_operation"
        invalid_event["operation"] = "merge"
        invalid = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(invalid_event))
        require(invalid.returncode != 0 and "operation" in invalid.stderr, "queue_db.py は invalid operation を拒否してください。")

    with tempfile.TemporaryDirectory() as tmp:
        runtime_root = Path(tmp) / ".orchestra"
        database = runtime_root / "queue" / "state.sqlite"
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY)")
            connection.execute("CREATE TABLE assignments(task_id TEXT PRIMARY KEY, status TEXT)")
            connection.commit()

        legacy_init = _run_python(script, "--runtime-root", runtime_root, "init")
        output = legacy_init.stdout + legacy_init.stderr
        require(legacy_init.returncode != 0 and ("tickets" in output or "task_id" in output), "queue_db.py init は旧物理 schema を拒否してください。")

    with tempfile.TemporaryDirectory() as tmp:
        runtime_root = Path(tmp) / ".orchestra"
        database = runtime_root / "queue" / "state.sqlite"
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as connection:
            connection.executescript(read("template/.agents/orchestra/scripts/queue_schema.sql"))
            connection.execute("INSERT INTO queue_metadata(key, value) VALUES('schema_version', '3.0')")
            connection.commit()

        old_version_init = _run_python(script, "--runtime-root", runtime_root, "init")
        old_version_init_output = old_version_init.stdout + old_version_init.stderr
        require(
            old_version_init.returncode != 0 and "schema_version" in old_version_init_output and "4.0" in old_version_init_output,
            "queue_db.py init は物理schemaが一致しても旧v3 metadataを拒否してください。",
        )
        old_version_dump = _run_python(script, "--runtime-root", runtime_root, "dump", "quests")
        old_version_dump_output = old_version_dump.stdout + old_version_dump.stderr
        require(
            old_version_dump.returncode != 0 and "schema_version" in old_version_dump_output,
            "queue_db.py dump は旧v3 metadataを拒否してください。",
        )
        old_version_write = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(_valid_event()))
        old_version_write_output = old_version_write.stdout + old_version_write.stderr
        require(
            old_version_write.returncode != 0 and "schema_version" in old_version_write_output,
            "queue_db.py record-event は旧v3 metadataを拒否してください。",
        )
        require(
            not any(database.with_name(database.name + suffix).exists() for suffix in ("-wal", "-shm")),
            "queue_db.py は旧v3 metadata DBをwrite openする前に拒否してください。",
        )
        with sqlite3.connect(database) as connection:
            schema_version = connection.execute("SELECT value FROM queue_metadata WHERE key = 'schema_version'").fetchone()
            event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        require(schema_version is not None and schema_version[0] == "3.0", "queue_db.py は拒否した旧v3 metadataを変更しないでください。")
        require(event_count == 0, "queue_db.py は旧v3 metadata DBへeventを書き込まないでください。")
        old_version_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        old_version_audit_output = old_version_audit.stdout + old_version_audit.stderr
        require(
            old_version_audit.returncode != 0 and "schema_version" in old_version_audit_output and "4.0" in old_version_audit_output and "3.0" in old_version_audit_output,
            "queue_audit.py は旧v3 metadataを拒否してください。",
        )
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM queue_metadata WHERE key = 'schema_version'")
            connection.commit()
        missing_version_init = _run_python(script, "--runtime-root", runtime_root, "init")
        missing_version_init_output = missing_version_init.stdout + missing_version_init.stderr
        require(
            missing_version_init.returncode != 0 and "schema_version" in missing_version_init_output,
            "queue_db.py init はmetadata欠落DBを暗黙初期化せず拒否してください。",
        )
        with sqlite3.connect(database) as connection:
            schema_version = connection.execute("SELECT value FROM queue_metadata WHERE key = 'schema_version'").fetchone()
        require(schema_version is None, "queue_db.py は拒否したmetadata欠落DBへschema_versionを挿入しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        runtime_root = Path(tmp) / ".orchestra"
        database = runtime_root / "queue" / "state.sqlite"
        database.parent.mkdir(parents=True)
        canonical_schema = read("template/.agents/orchestra/scripts/queue_schema.sql")
        type_drift_schema = canonical_schema.replace("  status TEXT NOT NULL,", "  status INTEGER,", 1)
        require(type_drift_schema != canonical_schema, "queue schema type drift fixtureを構築できません。")
        with sqlite3.connect(database) as connection:
            connection.executescript(type_drift_schema)
            connection.execute("INSERT INTO queue_metadata(key, value) VALUES('schema_version', '4.0')")
            connection.commit()

        type_drift_init = _run_python(script, "--runtime-root", runtime_root, "init")
        type_drift_output = type_drift_init.stdout + type_drift_init.stderr
        require(
            type_drift_init.returncode != 0 and "signature" in type_drift_output and "quests" in type_drift_output,
            "queue_db.py init は同名columnのtype/not-null driftを拒否してください。",
        )
        type_drift_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        type_drift_audit_output = type_drift_audit.stdout + type_drift_audit.stderr
        require(
            type_drift_audit.returncode != 0 and "signature" in type_drift_audit_output and "quests" in type_drift_audit_output,
            "queue_audit.py は同名columnのtype/not-null driftを拒否してください。",
        )
        with sqlite3.connect(database) as connection:
            quest_status = next(row for row in connection.execute("PRAGMA table_info(quests)") if row[1] == "status")
        require(quest_status[2] == "INTEGER" and quest_status[3] == 0, "schema drift拒否時にDB定義を変更しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        runtime_root = Path(tmp) / ".orchestra"
        database = runtime_root / "queue" / "state.sqlite"
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as connection:
            connection.executescript(read("template/.agents/orchestra/scripts/queue_schema.sql"))
            connection.execute("INSERT INTO queue_metadata(key, value) VALUES('schema_version', '4.0')")
            connection.execute("DROP INDEX idx_events_entity")
            connection.commit()

        index_drift_dump = _run_python(script, "--runtime-root", runtime_root, "dump", "events")
        index_drift_output = index_drift_dump.stdout + index_drift_dump.stderr
        require(
            index_drift_dump.returncode != 0 and "signature" in index_drift_output and "idx_events_entity" in index_drift_output,
            "queue_db.py read path はcanonical index欠落を拒否してください。",
        )
        index_drift_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        index_drift_audit_output = index_drift_audit.stdout + index_drift_audit.stderr
        require(
            index_drift_audit.returncode != 0 and "signature" in index_drift_audit_output and "idx_events_entity" in index_drift_audit_output,
            "queue_audit.py はcanonical index欠落を拒否してください。",
        )

    with tempfile.TemporaryDirectory() as tmp:
        runtime_root = Path(tmp) / ".orchestra"
        init = _run_python(script, "--runtime-root", runtime_root, "init")
        require(init.returncode == 0, "queue_db.py init の未知 schema smoke 準備が失敗しました: " + init.stderr)
        database = runtime_root / "queue" / "state.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("ALTER TABLE quests ADD COLUMN raw_log TEXT")
            connection.execute("CREATE TABLE unsafe_runtime_payload(secret TEXT)")
            connection.commit()
        for suffix in ("-wal", "-shm"):
            sidecar = database.with_name(database.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

        unexpected_init = _run_python(script, "--runtime-root", runtime_root, "init")
        unexpected_init_output = unexpected_init.stdout + unexpected_init.stderr
        require(
            unexpected_init.returncode != 0 and ("raw_log" in unexpected_init_output or "unsafe_runtime_payload" in unexpected_init_output),
            "queue_db.py init は未知 table/column を含む schema を拒否してください。",
        )
        unexpected_dump = _run_python(script, "--runtime-root", runtime_root, "dump", "quests")
        unexpected_dump_output = unexpected_dump.stdout + unexpected_dump.stderr
        require(
            unexpected_dump.returncode != 0 and ("raw_log" in unexpected_dump_output or "unsafe_runtime_payload" in unexpected_dump_output),
            "queue_db.py dump は未知 table/column を含む schema を拒否してください。",
        )
        unexpected_write = _run_python(script, "--runtime-root", runtime_root, "record-event", json.dumps(_valid_event()))
        unexpected_write_output = unexpected_write.stdout + unexpected_write.stderr
        require(
            unexpected_write.returncode != 0 and ("raw_log" in unexpected_write_output or "unsafe_runtime_payload" in unexpected_write_output),
            "queue_db.py record-event は未知 table/column を含む schema を拒否してください。",
        )
        require(
            not any(database.with_name(database.name + suffix).exists() for suffix in ("-wal", "-shm")),
            "queue_db.py record-event は schema mismatch DB を write open する前に拒否してください。",
        )
        unexpected_audit = _run_python(audit_script, "--runtime-root", runtime_root, "--static-root", ROOT / "template/.agents/orchestra", "--json")
        unexpected_audit_output = unexpected_audit.stdout + unexpected_audit.stderr
        require(
            unexpected_audit.returncode != 0 and ("raw_log" in unexpected_audit_output or "unsafe_runtime_payload" in unexpected_audit_output),
            "queue_audit.py は未知 table/column を含む schema を拒否してください。",
        )
