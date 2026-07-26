#!/usr/bin/env python3
"""Root high/xhigh/ultra の記録済みmulti-agent traceをfail-closedで検証する。"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # host検証ではRuby標準YAMLへfallbackする
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "scripts/root_orchestration_eval.yaml"
GOLDEN_ROOT = ROOT / "scripts/validation/fixtures/golden_quests"
ROOT_MODES = {"high", "xhigh", "ultra"}
NAMED_ROLES = {
    "cartographer",
    "guildmaster",
    "captain",
    "adventurer",
    "artificer",
    "inquisitor",
    "examiner",
    "sage",
    "warden",
    "courier",
}
EXPECTED_PAIRS = {
    "cartographer": {"model": "gpt-5.6-sol", "effort": "high"},
    "guildmaster": {"model": "gpt-5.6-sol", "effort": "xhigh"},
    "captain": {"model": "gpt-5.6-sol", "effort": "high"},
    "adventurer": {"model": "gpt-5.6-terra", "effort": "high"},
    "artificer": {"model": "gpt-5.6-sol", "effort": "high"},
    "inquisitor": {"model": "gpt-5.6-sol", "effort": "xhigh"},
    "examiner": {"model": "gpt-5.6-terra", "effort": "high"},
    "sage": {"model": "gpt-5.6-luna", "effort": "xhigh"},
    "warden": {"model": "gpt-5.6-sol", "effort": "high"},
    "courier": {"model": "gpt-5.3-codex-spark", "effort": "xhigh"},
}
EXPECTED_CASES = {
    "mapmaking": {
        "contract_fixtures": ["root_coordination_only.yaml", "mapmaking_readonly_no_edit.yaml"],
        "expected_top_level_roles": ["cartographer"],
        "top_level_role_phases": [["cartographer"]],
        "expected_nested_edges": [],
        "required_role_actions": {"cartographer": ["repository_exploration"]},
        "required_root_actions": [],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
    "solo": {
        "contract_fixtures": ["root_coordination_only.yaml", "solo_small_fix_no_git.yaml"],
        "expected_top_level_roles": ["adventurer"],
        "top_level_role_phases": [["adventurer"]],
        "expected_nested_edges": [],
        "required_role_actions": {"adventurer": ["repository_exploration", "implementation", "validation_execution"]},
        "required_root_actions": [],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
    "party": {
        "contract_fixtures": ["root_coordination_only.yaml", "party_integration_barrier_stable_revision.yaml"],
        "expected_top_level_roles": ["captain", "adventurer", "artificer"],
        "top_level_role_phases": [["captain"], ["adventurer"], ["artificer"]],
        "expected_nested_edges": [],
        "required_role_actions": {
            "captain": ["execution_design"],
            "adventurer": ["implementation", "validation_execution"],
            "artificer": ["integration", "validation_execution"],
        },
        "required_root_actions": [],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
    "focused_trial": {
        "contract_fixtures": ["root_coordination_only.yaml", "focused_trial_risk_triggered_review.yaml"],
        "expected_top_level_roles": ["inquisitor"],
        "top_level_role_phases": [["inquisitor"]],
        "expected_nested_edges": ["inquisitor->examiner"],
        "required_role_actions": {
            "inquisitor": ["review_evidence_generation", "trial_decision"],
            "examiner": ["review_evidence_generation"],
        },
        "required_root_actions": [],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
    "safety": {
        "contract_fixtures": ["root_coordination_only.yaml", "safety_gate_needs_human.yaml"],
        "expected_top_level_roles": ["inquisitor"],
        "top_level_role_phases": [["inquisitor"]],
        "expected_nested_edges": [],
        "required_role_actions": {"inquisitor": ["review_evidence_generation", "trial_decision"]},
        "required_root_actions": [],
        "terminal_root_action": "needs_human",
        "worker_unavailable_role": None,
    },
    "guild": {
        "contract_fixtures": ["root_coordination_only.yaml", "guild_quest_routing.yaml"],
        "expected_top_level_roles": ["guildmaster", "captain"],
        "top_level_role_phases": [["guildmaster"], ["captain"]],
        "expected_nested_edges": [],
        "required_role_actions": {"guildmaster": ["guild_strategy"], "captain": ["execution_design"]},
        "required_root_actions": [],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
    "warden_control": {
        "contract_fixtures": ["root_coordination_only.yaml", "warden_evidence_trigger.yaml"],
        "expected_top_level_roles": ["warden"],
        "top_level_role_phases": [["warden"]],
        "expected_nested_edges": [],
        "required_role_actions": {"warden": ["diagnosis"]},
        "required_root_actions": [],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
    "sage_focus": {
        "contract_fixtures": ["root_coordination_only.yaml", "sage_owner_synthesis.yaml"],
        "expected_top_level_roles": ["sage"],
        "top_level_role_phases": [["sage"]],
        "expected_nested_edges": [],
        "required_role_actions": {"sage": ["advice"]},
        "required_root_actions": [],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
    "worker_unavailable": {
        "contract_fixtures": ["root_coordination_only.yaml"],
        "expected_top_level_roles": [],
        "top_level_role_phases": [],
        "expected_nested_edges": [],
        "required_role_actions": {},
        "required_root_actions": [],
        "terminal_root_action": "needs_human",
        "worker_unavailable_role": "cartographer",
    },
    "browser_readonly": {
        "contract_fixtures": ["root_coordination_only.yaml", "mapmaking_readonly_no_edit.yaml"],
        "expected_top_level_roles": ["cartographer"],
        "top_level_role_phases": [["cartographer"]],
        "expected_nested_edges": [],
        "required_role_actions": {
            "cartographer": [
                "browser_planning",
                "browser_allowed_operation_specification",
                "browser_evidence_interpretation",
            ]
        },
        "required_root_actions": ["browser_control_tool_observation"],
        "terminal_root_action": "next_action",
        "worker_unavailable_role": None,
    },
}
BASE_EVENT_KEYS = {"seq", "actor", "depth", "action"}
_SESSION_ATTESTATION = object()


class OrchestrationEvalError(RuntimeError):
    """manifestまたはtraceが契約に違反した。"""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OrchestrationEvalError(f"{label} はmappingにしてください。")
    return value


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise OrchestrationEvalError(f"{label} はlistにしてください。")
    return value


def _string_set(value: object, label: str) -> set[str]:
    values = _sequence(value, label)
    if any(not isinstance(item, str) or not item for item in values) or len(values) != len(set(values)):
        raise OrchestrationEvalError(f"{label} は重複のない文字列listにしてください。")
    return set(values)


def _require_sha256_ref(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise OrchestrationEvalError(f"{label} はsha256:<64 lowercase hex>にしてください。")
    digest = value.removeprefix("sha256:")
    if any(character not in "0123456789abcdef" for character in digest):
        raise OrchestrationEvalError(f"{label} はsha256:<64 lowercase hex>にしてください。")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        if yaml is not None:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            ruby = shutil.which("ruby")
            if ruby is None:
                raise OrchestrationEvalError("manifest parseにはPyYAMLまたはRuby標準YAMLが必要です。")
            result = subprocess.run(
                [
                    ruby,
                    "-rjson",
                    "-ryaml",
                    "-e",
                    "print JSON.generate(YAML.safe_load(File.read(ARGV[0]), permitted_classes: [], aliases: false))",
                    str(path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise OrchestrationEvalError(f"manifest parseに失敗しました: {result.stderr.strip()}")
            value = json.loads(result.stdout)
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationEvalError(f"manifestを読めません: {path}: {exc}") from exc
    except Exception as exc:
        if yaml is not None and isinstance(exc, yaml.YAMLError):
            raise OrchestrationEvalError(f"manifest parseに失敗しました: {exc}") from exc
        raise
    return _mapping(value, "manifest")


def validate_manifest(manifest: dict[str, Any]) -> None:
    expected_top_keys = {
        "version",
        "suite",
        "root",
        "topology",
        "fixed_pairs",
        "trace_contract",
        "cases",
        "live_evidence_status",
        "live_evidence_policy",
    }
    if set(manifest) != expected_top_keys or manifest.get("version") != "1.0":
        raise OrchestrationEvalError("manifestはversion 1.0の定義済みsectionだけを含めてください。")

    suite = _mapping(manifest["suite"], "suite")
    if suite != {
        "kind": "recorded_end_to_end_multi_agent_trace",
        "trace_sources": ["synthetic_contract_self_test", "live_recorded"],
        "live_model_invocation_in_runner": False,
    }:
        raise OrchestrationEvalError("suiteは記録済みE2E trace検証とlive実行の分離を明示してください。")

    root = _mapping(manifest["root"], "root")
    if root != {
        "model": "gpt-5.6-sol",
        "modes": ["high", "xhigh", "ultra"],
        "project_local_effort_pinned": False,
        "control_plane_only": True,
    }:
        raise OrchestrationEvalError("RootはSol・high/xhigh/ultra・未pin・control-plane onlyにしてください。")

    topology = _mapping(manifest["topology"], "topology")
    if set(topology) != {"max_depth", "top_level_caller", "allowed_nested_edges", "terminal_roles"}:
        raise OrchestrationEvalError("topology fieldが不正です。")
    if topology.get("max_depth") != 2 or topology.get("top_level_caller") != "root":
        raise OrchestrationEvalError("topologyはRoot depth 0 / max depth 2にしてください。")
    nested = _mapping(topology.get("allowed_nested_edges"), "topology.allowed_nested_edges")
    if set(nested) != {"inquisitor"} or _string_set(nested.get("inquisitor"), "topology.allowed_nested_edges.inquisitor") != {"examiner"}:
        raise OrchestrationEvalError("nested edgeはinquisitor->examinerだけにしてください。")
    if _string_set(topology.get("terminal_roles"), "topology.terminal_roles") != NAMED_ROLES - {"inquisitor"}:
        raise OrchestrationEvalError("inquisitor以外のnamed roleをterminalにしてください。")

    pairs = _mapping(manifest["fixed_pairs"], "fixed_pairs")
    if set(pairs) != NAMED_ROLES:
        raise OrchestrationEvalError("fixed_pairsは10 roleを完全に定義してください。")
    for role, expected in EXPECTED_PAIRS.items():
        if _mapping(pairs.get(role), f"fixed_pairs.{role}") != expected:
            raise OrchestrationEvalError(f"fixed_pairs.{role}がdeployment pairと一致しません。")

    contract = _mapping(manifest["trace_contract"], "trace_contract")
    expected_contract_keys = {
        "root_required_preamble",
        "root_allowed_actions",
        "root_forbidden_actions",
        "role_actions",
        "every_assignment_requires_report",
        "every_top_level_assignment_requires_agent_wait",
        "required_role_actions_are_ordered",
        "trial_decision_after_nested_report_gate",
        "top_level_report_requires_root_gate",
        "nested_report_requires_parent_gate",
        "report_synthesis_required_before_terminal",
        "root_next_action_after_report_gate",
        "worker_unavailable_forbids_root_fallback",
        "ultra_preserves_topology_and_pairs",
    }
    if set(contract) != expected_contract_keys:
        raise OrchestrationEvalError("trace_contract fieldが不正です。")
    if _sequence(contract.get("root_required_preamble"), "trace_contract.root_required_preamble") != [
        "target_repo_binding",
        "authority_check",
        "snapshot_request",
    ]:
        raise OrchestrationEvalError("Root preambleはtarget/authority/snapshotの順に固定してください。")
    allowed = _string_set(contract.get("root_allowed_actions"), "trace_contract.root_allowed_actions")
    forbidden = _string_set(contract.get("root_forbidden_actions"), "trace_contract.root_forbidden_actions")
    expected_root_allowed = {
        "target_repo_binding",
        "authority_check",
        "snapshot_request",
        "assignment_created",
        "worker_unavailable",
        "agent_wait",
        "browser_control_tool_observation",
        "report_evidence_gate",
        "next_action",
        "needs_human",
        "report_synthesis",
    }
    if allowed & forbidden or allowed != expected_root_allowed:
        raise OrchestrationEvalError("Root allowed/forbidden actionが不正です。")
    expected_root_forbidden = {
        "repository_exploration",
        "implementation",
        "validation_execution",
        "browser_execution",
        "debugging",
        "review_evidence_generation",
        "trial_acceptance",
        "ledger_write",
    }
    if forbidden != expected_root_forbidden:
        raise OrchestrationEvalError("Root直接作業の禁止actionが不足しています。")
    role_actions = _mapping(contract.get("role_actions"), "trace_contract.role_actions")
    if set(role_actions) != NAMED_ROLES:
        raise OrchestrationEvalError("trace_contract.role_actionsは10 roleを定義してください。")
    for role, raw_actions in role_actions.items():
        actions = _string_set(raw_actions, f"trace_contract.role_actions.{role}")
        if "report_recorded" not in actions:
            raise OrchestrationEvalError(f"{role}はreport_recordedを許可してください。")
        if role != "inquisitor" and "assignment_created" in actions:
            raise OrchestrationEvalError("nested assignmentはinquisitorだけに許可してください。")
        if {"browser_control_tool_observation", "browser_execution"} & actions:
            raise OrchestrationEvalError("subagentにbrowser-control toolまたはbrowser実行を許可しないでください。")
    browser_role_actions = {"browser_planning", "browser_allowed_operation_specification", "browser_evidence_interpretation"}
    for role, raw_actions in role_actions.items():
        actions = _string_set(raw_actions, f"trace_contract.role_actions.{role}")
        if actions & browser_role_actions and role not in {"cartographer", "adventurer"}:
            raise OrchestrationEvalError("browserの計画・仕様化・解釈はcartographer/adventurerだけに許可してください。")
    for field in (
        "every_assignment_requires_report",
        "every_top_level_assignment_requires_agent_wait",
        "required_role_actions_are_ordered",
        "trial_decision_after_nested_report_gate",
        "top_level_report_requires_root_gate",
        "nested_report_requires_parent_gate",
        "report_synthesis_required_before_terminal",
        "root_next_action_after_report_gate",
        "worker_unavailable_forbids_root_fallback",
        "ultra_preserves_topology_and_pairs",
    ):
        if contract.get(field) is not True:
            raise OrchestrationEvalError(f"trace_contract.{field} はtrueにしてください。")

    cases = _mapping(manifest["cases"], "cases")
    required_cases = {
        "mapmaking",
        "solo",
        "party",
        "focused_trial",
        "safety",
        "guild",
        "warden_control",
        "sage_focus",
        "worker_unavailable",
        "browser_readonly",
    }
    if set(cases) != required_cases:
        raise OrchestrationEvalError("casesはRoot orchestrationのbrowser例外を含む10代表caseと一致させてください。")
    if cases != EXPECTED_CASES:
        raise OrchestrationEvalError("casesは10代表caseのfixture、role phase、required action、outcomeをexactに維持してください。")
    for case_id, raw_case in cases.items():
        case = _mapping(raw_case, f"cases.{case_id}")
        if set(case) != {
            "contract_fixtures",
            "expected_top_level_roles",
            "top_level_role_phases",
            "expected_nested_edges",
            "required_role_actions",
            "required_root_actions",
            "terminal_root_action",
            "worker_unavailable_role",
        }:
            raise OrchestrationEvalError(f"cases.{case_id} fieldが不正です。")
        fixtures = _string_set(case.get("contract_fixtures"), f"cases.{case_id}.contract_fixtures")
        if "root_coordination_only.yaml" not in fixtures:
            raise OrchestrationEvalError(f"cases.{case_id}はroot_coordination_only fixtureへ結合してください。")
        for fixture in fixtures:
            if not (GOLDEN_ROOT / fixture).is_file():
                raise OrchestrationEvalError(f"cases.{case_id}のfixtureがありません: {fixture}")
        top_roles = _string_set(case.get("expected_top_level_roles"), f"cases.{case_id}.expected_top_level_roles")
        if not top_roles <= NAMED_ROLES - {"examiner"}:
            raise OrchestrationEvalError(f"cases.{case_id}に不正なtop-level roleがあります。")
        phases = _sequence(case.get("top_level_role_phases"), f"cases.{case_id}.top_level_role_phases")
        flattened_phases: list[str] = []
        for phase_index, raw_phase in enumerate(phases):
            phase = _sequence(raw_phase, f"cases.{case_id}.top_level_role_phases[{phase_index}]")
            if not phase or any(not isinstance(role, str) or role not in top_roles for role in phase):
                raise OrchestrationEvalError(f"cases.{case_id}のtop-level role phaseが不正です。")
            flattened_phases.extend(phase)
        if flattened_phases != case.get("expected_top_level_roles") or len(flattened_phases) != len(set(flattened_phases)):
            raise OrchestrationEvalError(f"cases.{case_id}のrole phaseはexpected top-level roleの順序付きpartitionにしてください。")
        edges = _string_set(case.get("expected_nested_edges"), f"cases.{case_id}.expected_nested_edges")
        if not edges <= {"inquisitor->examiner"}:
            raise OrchestrationEvalError(f"cases.{case_id}に不正なnested edgeがあります。")
        required_actions = _mapping(case.get("required_role_actions"), f"cases.{case_id}.required_role_actions")
        allowed_case_roles = top_roles | ({"examiner"} if "inquisitor->examiner" in edges else set())
        if set(required_actions) != allowed_case_roles:
            raise OrchestrationEvalError(f"cases.{case_id}.required_role_actionsはcase roleと一致させてください。")
        for role, raw_actions in required_actions.items():
            actions = _string_set(raw_actions, f"cases.{case_id}.required_role_actions.{role}")
            if not actions <= _string_set(role_actions[role], f"trace_contract.role_actions.{role}") - {"report_recorded", "assignment_created", "child_report_evidence_gate"}:
                raise OrchestrationEvalError(f"cases.{case_id}の{role} required actionがrole contract外です。")
        required_root_actions = _sequence(case.get("required_root_actions"), f"cases.{case_id}.required_root_actions")
        if any(action != "browser_control_tool_observation" for action in required_root_actions):
            raise OrchestrationEvalError(f"cases.{case_id}のRoot例外actionが不正です。")
        if case_id == "browser_readonly" and required_root_actions != ["browser_control_tool_observation"]:
            raise OrchestrationEvalError("browser_readonly caseはRoot browser-control tool例外を必須にしてください。")
        if case_id != "browser_readonly" and required_root_actions:
            raise OrchestrationEvalError("browser-control tool例外はbrowser_readonly caseだけにしてください。")
        if case.get("terminal_root_action") not in {"next_action", "needs_human"}:
            raise OrchestrationEvalError(f"cases.{case_id}.terminal_root_actionが不正です。")
        unavailable = case.get("worker_unavailable_role")
        if unavailable is not None and unavailable not in NAMED_ROLES:
            raise OrchestrationEvalError(f"cases.{case_id}.worker_unavailable_roleが不正です。")
        if unavailable is not None and (top_roles or required_actions or case.get("terminal_root_action") != "needs_human"):
            raise OrchestrationEvalError("worker unavailable caseはassignmentなしでneeds_humanにしてください。")

    evidence = _mapping(manifest["live_evidence_status"], "live_evidence_status")
    if evidence != {
        "live_execution_completed": False,
        "completed_matrix": [],
        "required_matrix": "all_modes_x_all_cases",
        "external_reviewed_execution_required": True,
        "empirical_support_claim_allowed": False,
    }:
        raise OrchestrationEvalError("live evidence未取得をfail-closedで明示してください。")
    live_policy = _mapping(manifest["live_evidence_policy"], "live_evidence_policy")
    if set(live_policy) != {
        "external_data_ack_required",
        "approved_isolation_wrapper_sha256",
        "approved_isolation_profile_sha256",
        "session_manifest_filename",
        "wrapper_artifact_filename",
        "isolation_profile_filename",
        "digest_algorithm",
        "provenance_guarantee",
    }:
        raise OrchestrationEvalError("live_evidence_policy fieldが不正です。")
    if (
        live_policy.get("external_data_ack_required") is not True
        or live_policy.get("session_manifest_filename") != "session.json"
        or live_policy.get("wrapper_artifact_filename") != "collector-wrapper.bin"
        or live_policy.get("isolation_profile_filename") != "isolation-attestation.json"
        or live_policy.get("digest_algorithm") != "sha256"
        or live_policy.get("provenance_guarantee")
        != "artifact_integrity_and_operator_attestation_not_execution_authenticity"
    ):
        raise OrchestrationEvalError("live evidenceはack、session manifest、SHA-256 provenanceを必須にしてください。")
    for field in ("approved_isolation_wrapper_sha256", "approved_isolation_profile_sha256"):
        values = _string_set(live_policy.get(field), f"live_evidence_policy.{field}")
        if any(len(value) != 64 or any(character not in "0123456789abcdef" for character in value) for value in values):
            raise OrchestrationEvalError(f"live_evidence_policy.{field}はlowercase SHA-256 listにしてください。")


def _expected_event_keys(action: str) -> set[str]:
    if action == "target_repo_binding":
        return BASE_EVENT_KEYS | {"target_repo_root"}
    if action == "authority_check":
        return BASE_EVENT_KEYS | {"authority_ref"}
    if action == "snapshot_request":
        return BASE_EVENT_KEYS | {"snapshot_ref"}
    if action == "assignment_created":
        return BASE_EVENT_KEYS | {
            "assignment_id",
            "target_role",
            "model",
            "effort",
            "parent_assignment_id",
            "target_repo_root",
            "authority_ref",
            "snapshot_ref",
        }
    if action == "worker_unavailable":
        return BASE_EVENT_KEYS | {"target_role"}
    if action == "agent_wait":
        return BASE_EVENT_KEYS | {"assignment_id"}
    if action == "report_recorded":
        return BASE_EVENT_KEYS | {"assignment_id", "report_id", "snapshot_ref", "evidence_ref"}
    if action in {"report_evidence_gate", "child_report_evidence_gate"}:
        return BASE_EVENT_KEYS | {"report_id", "snapshot_ref", "evidence_ref"} | (
            {"assignment_id"} if action == "child_report_evidence_gate" else set()
        )
    if action == "browser_allowed_operation_specification":
        return BASE_EVENT_KEYS | {"assignment_id", "browser_plan_ref"}
    if action == "browser_control_tool_observation":
        return BASE_EVENT_KEYS | {"assignment_id", "browser_plan_ref", "observation_ref"}
    if action == "browser_evidence_interpretation":
        return BASE_EVENT_KEYS | {"assignment_id", "observation_ref"}
    if action in {
        "repository_exploration",
        "implementation",
        "validation_execution",
        "browser_planning",
        "debugging",
        "review_evidence_generation",
        "guild_strategy",
        "execution_design",
        "integration",
        "trial_decision",
        "advice",
        "diagnosis",
        "ledger_write",
        "local_git_write",
    }:
        return BASE_EVENT_KEYS | {"assignment_id"}
    return BASE_EVENT_KEYS


def validate_trace(
    manifest: dict[str, Any],
    trace: dict[str, Any],
    *,
    require_live: bool = False,
    _session_attestation: object | None = None,
) -> None:
    validate_manifest(manifest)
    if set(trace) != {"version", "trace_id", "source", "case_id", "root_model", "root_mode", "events"}:
        raise OrchestrationEvalError("trace fieldが不正です。")
    if trace.get("version") != "1.0" or not isinstance(trace.get("trace_id"), str) or not trace["trace_id"]:
        raise OrchestrationEvalError("trace version/trace_idが不正です。")
    if trace.get("source") not in {"synthetic_contract_self_test", "live_recorded"}:
        raise OrchestrationEvalError("trace.sourceが不正です。")
    if require_live and trace.get("source") != "live_recorded":
        raise OrchestrationEvalError("session matrixにはlive_recorded traceが必要です。")
    if trace.get("source") == "live_recorded" and _session_attestation is not _SESSION_ATTESTATION:
        raise OrchestrationEvalError("live traceは単体では証跡になりません。validate-sessionを使用してください。")
    if trace.get("root_model") != "gpt-5.6-sol" or trace.get("root_mode") not in ROOT_MODES:
        raise OrchestrationEvalError("trace Root model/modeが不正です。")
    cases = _mapping(manifest["cases"], "cases")
    case_id = trace.get("case_id")
    if case_id not in cases:
        raise OrchestrationEvalError("trace.case_idがmanifestにありません。")
    case = _mapping(cases[case_id], f"cases.{case_id}")
    expected_top_roles = _string_set(case["expected_top_level_roles"], "case.expected_top_level_roles")
    expected_edges = _string_set(case["expected_nested_edges"], "case.expected_nested_edges")
    contract = _mapping(manifest["trace_contract"], "trace_contract")
    root_allowed = _string_set(contract["root_allowed_actions"], "root_allowed_actions")
    root_forbidden = _string_set(contract["root_forbidden_actions"], "root_forbidden_actions")
    role_actions = {
        role: _string_set(value, f"role_actions.{role}")
        for role, value in _mapping(contract["role_actions"], "role_actions").items()
    }
    events = _sequence(trace.get("events"), "trace.events")
    if not events:
        raise OrchestrationEvalError("trace.eventsを空にできません。")

    assignments: dict[str, dict[str, Any]] = {}
    reports: dict[str, dict[str, Any]] = {}
    root_gates: set[str] = set()
    root_gate_seq: dict[str, int] = {}
    parent_gates: set[str] = set()
    top_roles: set[str] = set()
    top_role_order: list[str] = []
    nested_edges: set[str] = set()
    seen_actions: dict[str, list[str]] = defaultdict(list)
    seen_root_actions: list[str] = []
    browser_specs: dict[str, str] = {}
    browser_observations: dict[str, str] = {}
    unavailable_roles: list[str] = []
    terminal_seen = False
    synthesis_seen = False
    target_repo_root: str | None = None
    authority_ref: str | None = None
    snapshot_ref: str | None = None

    required_preamble = _sequence(contract["root_required_preamble"], "trace_contract.root_required_preamble")
    if [
        _mapping(event, f"trace.events[{index}]").get("action")
        for index, event in enumerate(events[: len(required_preamble)])
    ] != required_preamble:
        raise OrchestrationEvalError("traceはRoot target_repo_binding/authority_check/snapshot_requestから開始してください。")

    for expected_seq, raw_event in enumerate(events, start=1):
        event = _mapping(raw_event, f"trace.events[{expected_seq - 1}]")
        if event.get("seq") != expected_seq:
            raise OrchestrationEvalError("trace event seqは1から連続させてください。")
        actor = event.get("actor")
        depth = event.get("depth")
        action = event.get("action")
        if not isinstance(actor, str) or not isinstance(action, str) or not isinstance(depth, int) or isinstance(depth, bool):
            raise OrchestrationEvalError(f"trace event {expected_seq}のactor/depth/actionが不正です。")
        if set(event) != _expected_event_keys(action):
            raise OrchestrationEvalError(f"trace event {expected_seq} action={action}のfieldが不正です。")
        if terminal_seen:
            raise OrchestrationEvalError("Root terminal action後にeventを追加しないでください。")

        if actor == "root":
            if depth != 0 or action in root_forbidden or action not in root_allowed:
                raise OrchestrationEvalError(f"Rootはaction `{action}` を直接実行できません。")
            if action in required_preamble and expected_seq > len(required_preamble):
                raise OrchestrationEvalError("Root preamble actionを後続eventで重複させないでください。")
            ungated_top_reports = [
                report_id
                for report_id, report in reports.items()
                if report["depth"] == 1 and report_id not in root_gates
            ]
            if action != "report_evidence_gate" and ungated_top_reports:
                raise OrchestrationEvalError("Rootは未gateのtop-level reportを残して次actionへ進めません。")
            if action == "target_repo_binding":
                value = event.get("target_repo_root")
                path = Path(str(value)) if isinstance(value, str) else Path()
                if (
                    target_repo_root is not None
                    or not isinstance(value, str)
                    or not path.is_absolute()
                    or ".." in path.parts
                    or path.parent.name != "repositories"
                    or not path.name
                ):
                    raise OrchestrationEvalError("target_repo_bindingは絶対pathの<guild>/repositories/<repo>を一度だけ記録してください。")
                target_repo_root = value
            elif action == "authority_check":
                if authority_ref is not None:
                    raise OrchestrationEvalError("authority_checkを重複させないでください。")
                authority_ref = _require_sha256_ref(event.get("authority_ref"), "authority_check.authority_ref")
            elif action == "snapshot_request":
                if snapshot_ref is not None:
                    raise OrchestrationEvalError("snapshot_requestを重複させないでください。")
                snapshot_ref = _require_sha256_ref(event.get("snapshot_ref"), "snapshot_request.snapshot_ref")
            elif action == "assignment_created":
                assignment_id = event.get("assignment_id")
                target_role = event.get("target_role")
                if not isinstance(assignment_id, str) or not assignment_id or assignment_id in assignments:
                    raise OrchestrationEvalError("top-level assignment_idが不正です。")
                if target_role not in NAMED_ROLES - {"examiner"} or event.get("parent_assignment_id") is not None:
                    raise OrchestrationEvalError("Rootはnamed top-level roleだけを直接assignしてください。")
                if {"model": event.get("model"), "effort": event.get("effort")} != EXPECTED_PAIRS[target_role]:
                    raise OrchestrationEvalError(f"{target_role} assignment pairが固定deployment値と一致しません。")
                if (
                    event.get("target_repo_root") != target_repo_root
                    or event.get("authority_ref") != authority_ref
                    or event.get("snapshot_ref") != snapshot_ref
                ):
                    raise OrchestrationEvalError("top-level assignmentはRoot target/authority/snapshot bindingを参照してください。")
                assignments[assignment_id] = {
                    "role": target_role,
                    "depth": 1,
                    "parent": None,
                    "reported": False,
                    "report_id": None,
                    "waited": False,
                    "created_seq": expected_seq,
                    "target_repo_root": target_repo_root,
                    "authority_ref": authority_ref,
                    "snapshot_ref": snapshot_ref,
                }
                top_roles.add(str(target_role))
                top_role_order.append(str(target_role))
            elif action == "agent_wait":
                assignment_id = event.get("assignment_id")
                assignment = assignments.get(str(assignment_id))
                if assignment is None or assignment["depth"] != 1 or assignment["reported"] or assignment["waited"]:
                    raise OrchestrationEvalError("Root agent_waitは未完了top-level assignmentごとに一度だけ記録してください。")
                assignment["waited"] = True
            elif action == "browser_control_tool_observation":
                assignment_id = str(event.get("assignment_id"))
                assignment = assignments.get(assignment_id)
                if (
                    assignment is None
                    or assignment["depth"] != 1
                    or assignment["role"] not in {"cartographer", "adventurer"}
                    or not assignment["waited"]
                    or assignment["reported"]
                    or browser_specs.get(assignment_id) != event.get("browser_plan_ref")
                    or assignment_id in browser_observations
                ):
                    raise OrchestrationEvalError("Root browser-control toolは待機中assignmentのrole仕様どおりに一度だけ実行してください。")
                browser_observations[assignment_id] = _require_sha256_ref(
                    event.get("observation_ref"), "browser_control_tool_observation.observation_ref"
                )
                seen_root_actions.append(action)
            elif action == "worker_unavailable":
                target_role = event.get("target_role")
                if target_role not in NAMED_ROLES:
                    raise OrchestrationEvalError("worker_unavailable target_roleが不正です。")
                unavailable_roles.append(str(target_role))
            elif action == "report_evidence_gate":
                report_id = event.get("report_id")
                report = reports.get(str(report_id))
                if report is None or report["depth"] != 1:
                    raise OrchestrationEvalError("Rootは記録済みtop-level reportだけをgateしてください。")
                if event.get("snapshot_ref") != report["snapshot_ref"] or event.get("evidence_ref") != report["evidence_ref"]:
                    raise OrchestrationEvalError("Root report gateはreportのsnapshot/evidence refをexactに参照してください。")
                if str(report_id) in root_gates:
                    raise OrchestrationEvalError("Root report gateを重複させないでください。")
                root_gates.add(str(report_id))
                root_gate_seq[str(report_id)] = expected_seq
            elif action in {"next_action", "needs_human", "report_synthesis"}:
                if any(not assignment["reported"] for assignment in assignments.values()):
                    raise OrchestrationEvalError("Rootは全assignment report前に次actionへ進めません。")
                for report_id, report in reports.items():
                    required_gate = root_gates if report["depth"] == 1 else parent_gates
                    if report_id not in required_gate:
                        raise OrchestrationEvalError("Rootはreport gate完了前に次actionへ進めません。")
                if action == "report_synthesis":
                    top_assignment_count = len(
                        [assignment for assignment in assignments.values() if assignment["depth"] == 1]
                    )
                    nested_assignment_count = len(
                        [assignment for assignment in assignments.values() if assignment["depth"] == 2]
                    )
                    if (
                        synthesis_seen
                        or top_roles != expected_top_roles
                        or top_assignment_count != len(expected_top_roles)
                        or nested_edges != expected_edges
                        or nested_assignment_count != len(expected_edges)
                    ):
                        raise OrchestrationEvalError("Root report_synthesisは全case assignment/report gate完了後に一度だけ実行してください。")
                    synthesis_seen = True
                if action in {"next_action", "needs_human"}:
                    if assignments and not synthesis_seen:
                        raise OrchestrationEvalError("Root terminal actionの前にreport_synthesisが必要です。")
                    terminal_seen = True
            continue

        if actor not in NAMED_ROLES or depth not in {1, 2}:
            raise OrchestrationEvalError("trace actorはRootまたはdepth内のnamed roleにしてください。")
        if action not in role_actions[actor]:
            raise OrchestrationEvalError(f"{actor}にaction `{action}` は許可されていません。")

        if action == "assignment_created":
            assignment_id = event.get("assignment_id")
            parent_assignment_id = event.get("parent_assignment_id")
            target_role = event.get("target_role")
            parent = assignments.get(str(parent_assignment_id))
            if actor != "inquisitor" or depth != 1 or target_role != "examiner" or parent is None or parent["role"] != "inquisitor":
                raise OrchestrationEvalError("nested assignmentはdepth 1 inquisitorからexaminerだけにしてください。")
            if not isinstance(assignment_id, str) or not assignment_id or assignment_id in assignments:
                raise OrchestrationEvalError("nested assignment_idが不正です。")
            if {"model": event.get("model"), "effort": event.get("effort")} != EXPECTED_PAIRS["examiner"]:
                raise OrchestrationEvalError("examiner assignment pairが固定deployment値と一致しません。")
            if (
                event.get("target_repo_root") != parent["target_repo_root"]
                or event.get("authority_ref") != parent["authority_ref"]
                or event.get("snapshot_ref") != parent["snapshot_ref"]
            ):
                raise OrchestrationEvalError("nested assignmentは親のtarget/authority/snapshot bindingを維持してください。")
            if not parent["waited"]:
                raise OrchestrationEvalError("inquisitorはRootのagent_wait開始後にだけchildをassignできます。")
            assignments[assignment_id] = {
                "role": "examiner",
                "depth": 2,
                "parent": parent_assignment_id,
                "reported": False,
                "report_id": None,
                "waited": True,
                "created_seq": expected_seq,
                "target_repo_root": parent["target_repo_root"],
                "authority_ref": parent["authority_ref"],
                "snapshot_ref": parent["snapshot_ref"],
            }
            nested_edges.add("inquisitor->examiner")
            continue

        assignment_id = event.get("assignment_id")
        assignment = assignments.get(str(assignment_id))
        if assignment is None or assignment["role"] != actor or assignment["depth"] != depth:
            raise OrchestrationEvalError(f"{actor} eventのassignment lineageが不正です。")
        if depth == 1 and not assignment["waited"]:
            raise OrchestrationEvalError(f"{actor}はRoot agent_wait前にwork/reportを開始できません。")
        if assignment["reported"]:
            raise OrchestrationEvalError(f"{actor}はreport後にwork eventを追加できません。")
        if action == "report_recorded":
            report_id = event.get("report_id")
            if not isinstance(report_id, str) or not report_id or report_id in reports:
                raise OrchestrationEvalError("report_idが不正です。")
            evidence_ref = _require_sha256_ref(event.get("evidence_ref"), f"{actor}.report.evidence_ref")
            if event.get("snapshot_ref") != assignment["snapshot_ref"]:
                raise OrchestrationEvalError("report snapshot_refはassignment snapshotと一致させてください。")
            reports[report_id] = {
                "assignment_id": assignment_id,
                "role": actor,
                "depth": depth,
                "snapshot_ref": assignment["snapshot_ref"],
                "evidence_ref": evidence_ref,
            }
            assignment["reported"] = True
            assignment["report_id"] = report_id
        elif action == "child_report_evidence_gate":
            report_id = str(event.get("report_id"))
            report = reports.get(report_id)
            if actor != "inquisitor" or report is None or report["depth"] != 2:
                raise OrchestrationEvalError("nested reportは親inquisitorだけがgateしてください。")
            if event.get("snapshot_ref") != report["snapshot_ref"] or event.get("evidence_ref") != report["evidence_ref"]:
                raise OrchestrationEvalError("nested report gateはchild reportのsnapshot/evidence refをexactに参照してください。")
            child_assignment = assignments[report["assignment_id"]]
            if child_assignment["parent"] != assignment_id:
                raise OrchestrationEvalError("nested report gateのparent assignmentが不正です。")
            if report_id in parent_gates:
                raise OrchestrationEvalError("nested report gateを重複させないでください。")
            parent_gates.add(report_id)
        elif action == "browser_allowed_operation_specification":
            if actor not in {"cartographer", "adventurer"} or str(assignment_id) in browser_specs:
                raise OrchestrationEvalError("browser許可操作仕様はcartographer/adventurerがassignmentごとに一度だけ記録してください。")
            browser_specs[str(assignment_id)] = _require_sha256_ref(
                event.get("browser_plan_ref"), f"{actor}.browser_allowed_operation_specification.browser_plan_ref"
            )
            seen_actions[actor].append(action)
        elif action == "browser_evidence_interpretation":
            if browser_observations.get(str(assignment_id)) != event.get("observation_ref"):
                raise OrchestrationEvalError("browser観測の解釈はRoot記録済みの同一observationだけを使ってください。")
            seen_actions[actor].append(action)
        else:
            if action == "trial_decision" and "inquisitor->examiner" in case["expected_nested_edges"]:
                child_assignments = [
                    child
                    for child in assignments.values()
                    if child["parent"] == assignment_id and child["role"] == "examiner"
                ]
                if len(child_assignments) != 1 or any(
                    not child["reported"] or child["report_id"] not in parent_gates for child in child_assignments
                ):
                    raise OrchestrationEvalError("inquisitor trial_decisionはExaminer reportの親gate後に実行してください。")
            seen_actions[actor].append(action)

    top_assignments = [assignment for assignment in assignments.values() if assignment["depth"] == 1]
    if (
        top_roles != expected_top_roles
        or top_role_order != case["expected_top_level_roles"]
        or len(top_assignments) != len(expected_top_roles)
        or nested_edges != expected_edges
        or len([assignment for assignment in assignments.values() if assignment["depth"] == 2]) != len(expected_edges)
    ):
        raise OrchestrationEvalError("trace topologyがcaseのtop-level role/nested edgeと一致しません。")
    completed_phase_roles: list[str] = []
    assignments_by_role = {assignment["role"]: assignment for assignment in top_assignments}
    for phase_index, raw_phase in enumerate(_sequence(case["top_level_role_phases"], "case.top_level_role_phases")):
        phase = _sequence(raw_phase, f"case.top_level_role_phases[{phase_index}]")
        if completed_phase_roles:
            previous_gate_seq = max(
                root_gate_seq[str(assignments_by_role[role]["report_id"])]
                for role in completed_phase_roles
            )
            if any(assignments_by_role[str(role)]["created_seq"] <= previous_gate_seq for role in phase):
                raise OrchestrationEvalError("後続role phaseは全先行phaseのreport gate完了後にassignしてください。")
        completed_phase_roles.extend(str(role) for role in phase)
    for role, required in _mapping(case["required_role_actions"], "case.required_role_actions").items():
        required_order = _sequence(required, f"case.required_role_actions.{role}")
        cursor = 0
        for action in seen_actions[role]:
            if cursor < len(required_order) and action == required_order[cursor]:
                cursor += 1
        if cursor != len(required_order):
            raise OrchestrationEvalError(f"traceに{role}のrequired actionが指定順で揃っていません。")
    required_root_actions = _sequence(case["required_root_actions"], "case.required_root_actions")
    cursor = 0
    for action in seen_root_actions:
        if cursor < len(required_root_actions) and action == required_root_actions[cursor]:
            cursor += 1
    if cursor != len(required_root_actions) or len(seen_root_actions) != len(required_root_actions):
        raise OrchestrationEvalError("traceにRoot browser-control tool例外actionが契約どおり揃っていません。")
    if any(not assignment["reported"] for assignment in assignments.values()):
        raise OrchestrationEvalError("全assignmentにreportが必要です。")
    if any(assignment["depth"] == 1 and not assignment["waited"] for assignment in assignments.values()):
        raise OrchestrationEvalError("全top-level assignmentにRoot agent_waitが必要です。")
    for report_id, report in reports.items():
        expected_gates = root_gates if report["depth"] == 1 else parent_gates
        if report_id not in expected_gates:
            raise OrchestrationEvalError("top-level/nested reportのowner gateが不足しています。")
    unavailable = case.get("worker_unavailable_role")
    if unavailable is None and unavailable_roles:
        raise OrchestrationEvalError("通常caseにworker_unavailable eventを含めないでください。")
    if unavailable is not None and unavailable_roles != [unavailable]:
        raise OrchestrationEvalError("worker unavailable caseは指定roleだけを記録してください。")
    if synthesis_seen != bool(expected_top_roles):
        raise OrchestrationEvalError("通常caseはRoot report_synthesisを一度、worker unavailable caseは0回にしてください。")
    last = _mapping(events[-1], "trace.events[-1]")
    if last.get("actor") != "root" or last.get("action") != case.get("terminal_root_action"):
        raise OrchestrationEvalError("trace最後のRoot actionがcase outcomeと一致しません。")


def build_contract_trace(manifest: dict[str, Any], case_id: str, mode: str) -> dict[str, Any]:
    """validator自身の正例に使う、最小の契約traceを決定的に組み立てる。"""
    validate_manifest(manifest)
    if mode not in ROOT_MODES:
        raise OrchestrationEvalError("modeはhigh/xhigh/ultraにしてください。")
    cases = _mapping(manifest["cases"], "cases")
    if case_id not in cases:
        raise OrchestrationEvalError("未知caseです。")
    case = _mapping(cases[case_id], f"cases.{case_id}")
    events: list[dict[str, Any]] = []

    def sha256_ref(label: str) -> str:
        return "sha256:" + hashlib.sha256(f"{mode}:{case_id}:{label}".encode("utf-8")).hexdigest()

    def add(actor: str, depth: int, action: str, **extra: Any) -> None:
        events.append({"seq": len(events) + 1, "actor": actor, "depth": depth, "action": action, **extra})

    target_repo_root = "/eval-guild/repositories/synthetic-target"
    authority_ref = sha256_ref("authority")
    snapshot_ref = sha256_ref("snapshot")
    add("root", 0, "target_repo_binding", target_repo_root=target_repo_root)
    add("root", 0, "authority_check", authority_ref=authority_ref)
    add("root", 0, "snapshot_request", snapshot_ref=snapshot_ref)
    unavailable = case.get("worker_unavailable_role")
    if unavailable is not None:
        add("root", 0, "worker_unavailable", target_role=unavailable)
        add("root", 0, "needs_human")
    else:
        required_actions = _mapping(case["required_role_actions"], "case.required_role_actions")
        for role in _sequence(case["expected_top_level_roles"], "case.expected_top_level_roles"):
            assignment_id = f"assignment_{case_id}_{role}"
            pair = EXPECTED_PAIRS[role]
            add(
                "root",
                0,
                "assignment_created",
                assignment_id=assignment_id,
                target_role=role,
                model=pair["model"],
                effort=pair["effort"],
                parent_assignment_id=None,
                target_repo_root=target_repo_root,
                authority_ref=authority_ref,
                snapshot_ref=snapshot_ref,
            )
            add("root", 0, "agent_wait", assignment_id=assignment_id)
            if role == "inquisitor" and "inquisitor->examiner" in case["expected_nested_edges"]:
                child_assignment_id = f"assignment_{case_id}_examiner"
                examiner_pair = EXPECTED_PAIRS["examiner"]
                add(
                    "inquisitor",
                    1,
                    "assignment_created",
                    assignment_id=child_assignment_id,
                    target_role="examiner",
                    model=examiner_pair["model"],
                    effort=examiner_pair["effort"],
                    parent_assignment_id=assignment_id,
                    target_repo_root=target_repo_root,
                    authority_ref=authority_ref,
                    snapshot_ref=snapshot_ref,
                )
                for action in required_actions.get("examiner", []):
                    add("examiner", 2, action, assignment_id=child_assignment_id)
                child_report_id = f"report_{case_id}_examiner"
                child_evidence_ref = sha256_ref("evidence:examiner")
                add(
                    "examiner",
                    2,
                    "report_recorded",
                    assignment_id=child_assignment_id,
                    report_id=child_report_id,
                    snapshot_ref=snapshot_ref,
                    evidence_ref=child_evidence_ref,
                )
                add(
                    "inquisitor",
                    1,
                    "child_report_evidence_gate",
                    assignment_id=assignment_id,
                    report_id=child_report_id,
                    snapshot_ref=snapshot_ref,
                    evidence_ref=child_evidence_ref,
                )
            browser_plan_ref: str | None = None
            for action in required_actions.get(role, []):
                if action == "browser_allowed_operation_specification":
                    browser_plan_ref = sha256_ref(f"browser-plan:{role}")
                    add(role, 1, action, assignment_id=assignment_id, browser_plan_ref=browser_plan_ref)
                    if "browser_control_tool_observation" in _sequence(
                        case["required_root_actions"], "case.required_root_actions"
                    ):
                        add(
                            "root",
                            0,
                            "browser_control_tool_observation",
                            assignment_id=assignment_id,
                            browser_plan_ref=browser_plan_ref,
                            observation_ref=sha256_ref(f"browser-observation:{role}"),
                        )
                elif action == "browser_evidence_interpretation":
                    if browser_plan_ref is None:
                        raise OrchestrationEvalError("browser解釈の前に許可操作仕様が必要です。")
                    add(
                        role,
                        1,
                        action,
                        assignment_id=assignment_id,
                        observation_ref=sha256_ref(f"browser-observation:{role}"),
                    )
                else:
                    add(role, 1, action, assignment_id=assignment_id)
            report_id = f"report_{case_id}_{role}"
            evidence_ref = sha256_ref(f"evidence:{role}")
            add(
                role,
                1,
                "report_recorded",
                assignment_id=assignment_id,
                report_id=report_id,
                snapshot_ref=snapshot_ref,
                evidence_ref=evidence_ref,
            )
            add(
                "root",
                0,
                "report_evidence_gate",
                report_id=report_id,
                snapshot_ref=snapshot_ref,
                evidence_ref=evidence_ref,
            )
        add("root", 0, "report_synthesis")
        add("root", 0, str(case["terminal_root_action"]))

    trace = {
        "version": "1.0",
        "trace_id": f"synthetic_{mode}_{case_id}",
        "source": "synthetic_contract_self_test",
        "case_id": case_id,
        "root_model": "gpt-5.6-sol",
        "root_mode": mode,
        "events": events,
    }
    validate_trace(manifest, trace)
    return trace


def _load_trace(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationEvalError(f"{label} artifactのJSONを読めません: {exc}") from exc
    return _mapping(value, label)


def _load_trace_file(path: Path) -> dict[str, Any]:
    try:
        return _load_trace(path.read_bytes(), str(path))
    except OSError as exc:
        raise OrchestrationEvalError(f"traceを読めません: {path}: {exc}") from exc


def _open_session_directory(session_dir: Path) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise OrchestrationEvalError("session artifact検証にはO_NOFOLLOWを使えるplatformが必要です。")
    directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(session_dir, directory_flags)
    except OSError as exc:
        raise OrchestrationEvalError(f"session directoryを安全に開けません: {exc}") from exc
    try:
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            raise OrchestrationEvalError("session directoryがdirectoryではありません。")
    except OSError as exc:
        os.close(directory_fd)
        raise OrchestrationEvalError(f"session directoryを安全に検査できません: {exc}") from exc
    except OrchestrationEvalError:
        os.close(directory_fd)
        raise
    return directory_fd


def _read_session_artifact(session_directory_fd: int, filename: str, label: str) -> bytes:
    """同じdescriptorからdirect-childのregular artifactを一度だけ読む。"""
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise OrchestrationEvalError(f"{label} artifact filenameが不正です。")
    if not hasattr(os, "O_NOFOLLOW"):
        raise OrchestrationEvalError("session artifact検証にはO_NOFOLLOWを使えるplatformが必要です。")
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(filename, file_flags, dir_fd=session_directory_fd)
    except OSError as exc:
        raise OrchestrationEvalError(f"{label} artifactのsymlinkまたはopen errorを拒否しました: {exc}") from exc
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OrchestrationEvalError(f"{label} artifactはregular fileにしてください。")
            return stream.read()
    except OSError as exc:
        raise OrchestrationEvalError(f"{label} artifactを安全に読めません: {exc}") from exc


def validate_session(manifest: dict[str, Any], session_dir: Path) -> None:
    validate_manifest(manifest)
    cases = _mapping(manifest["cases"], "cases")
    expected = {f"{mode}__{case_id}.json" for mode in sorted(ROOT_MODES) for case_id in sorted(cases)}
    policy = _mapping(manifest["live_evidence_policy"], "live_evidence_policy")
    session_filename = str(policy["session_manifest_filename"])
    profile_filename = str(policy["isolation_profile_filename"])
    session_directory_fd = _open_session_directory(session_dir)
    try:
        actual = {
            filename
            for filename in os.listdir(session_directory_fd)
            if filename.endswith(".json") and filename not in {session_filename, profile_filename}
        }
        if actual != expected:
            raise OrchestrationEvalError(
                f"session trace matrixが不足または過剰です: expected={sorted(expected)}, actual={sorted(actual)}"
            )
        session = _load_trace(
            _read_session_artifact(session_directory_fd, session_filename, "session manifest"),
            "session manifest",
        )
        _validate_session_artifacts(manifest, policy, expected, session_directory_fd, session)
    except OSError as exc:
        raise OrchestrationEvalError(f"session directoryを安全に読めません: {exc}") from exc
    finally:
        os.close(session_directory_fd)


def _validate_session_artifacts(
    manifest: dict[str, Any],
    policy: dict[str, Any],
    expected: set[str],
    session_directory_fd: int,
    session: dict[str, Any],
) -> None:
    profile_filename = str(policy["isolation_profile_filename"])
    if set(session) != {
        "version",
        "source",
        "external_data_acknowledged",
        "collector_id",
        "wrapper_sha256",
        "isolation_profile_sha256",
        "contract_sha256",
        "provenance_guarantee",
        "traces",
    }:
        raise OrchestrationEvalError("live session manifest fieldが不正です。")
    if session.get("version") != "1.0" or session.get("source") != "live_recorded":
        raise OrchestrationEvalError("live session provenanceが不正です。")
    if session.get("provenance_guarantee") != policy.get("provenance_guarantee"):
        raise OrchestrationEvalError("live sessionはartifact integrityとoperator attestationを超える保証を主張できません。")
    if policy.get("external_data_ack_required") is True and session.get("external_data_acknowledged") is not True:
        raise OrchestrationEvalError("live sessionには外部model送信の明示ackが必要です。")
    if not isinstance(session.get("collector_id"), str) or not session["collector_id"].strip():
        raise OrchestrationEvalError("live session collector_idが必要です。")
    wrapper_sha256 = session.get("wrapper_sha256")
    profile_sha256 = session.get("isolation_profile_sha256")
    wrapper_bytes = _read_session_artifact(
        session_directory_fd,
        str(policy["wrapper_artifact_filename"]),
        "wrapper",
    )
    profile_bytes = _read_session_artifact(session_directory_fd, profile_filename, "isolation profile")
    if hashlib.sha256(wrapper_bytes).hexdigest() != wrapper_sha256:
        raise OrchestrationEvalError("live session wrapper artifactのSHA-256がsession manifestと一致しません。")
    if hashlib.sha256(profile_bytes).hexdigest() != profile_sha256:
        raise OrchestrationEvalError("live session isolation profile artifactのSHA-256がsession manifestと一致しません。")
    if wrapper_sha256 not in _string_set(policy.get("approved_isolation_wrapper_sha256"), "approved wrapper hashes"):
        raise OrchestrationEvalError("live session wrapper hashがapproved allowlistにありません。")
    if profile_sha256 not in _string_set(policy.get("approved_isolation_profile_sha256"), "approved profile hashes"):
        raise OrchestrationEvalError("live session isolation profile hashがapproved allowlistにありません。")
    profile = _load_trace(profile_bytes, "isolation profile")
    if profile != {
        "version": "1.0",
        "collector_id": session["collector_id"],
        "wrapper_sha256": wrapper_sha256,
        "filesystem_scope": "isolated_eval_workdir_only",
        "network_scope": "openai_model_service_only",
        "execution_authenticity": "operator_attested_not_cryptographically_proven",
    }:
        raise OrchestrationEvalError("live session isolation profile artifactが定義済みoperator attestationと一致しません。")
    contract_sha256 = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if session.get("contract_sha256") != contract_sha256:
        raise OrchestrationEvalError("live session contract SHA-256が現在manifestと一致しません。")
    trace_digests = _mapping(session.get("traces"), "live session traces")
    if set(trace_digests) != expected:
        raise OrchestrationEvalError("live session trace digest一覧がrequired matrixと一致しません。")
    for filename in sorted(expected):
        trace_bytes = _read_session_artifact(session_directory_fd, filename, "trace")
        digest = hashlib.sha256(trace_bytes).hexdigest()
        if trace_digests.get(filename) != digest:
            raise OrchestrationEvalError(f"{filename}のSHA-256がsession manifestと一致しません。")
        trace = _load_trace(trace_bytes, filename)
        mode, case_name = filename.removesuffix(".json").split("__", 1)
        if trace.get("root_mode") != mode or trace.get("case_id") != case_name:
            raise OrchestrationEvalError(f"{filename}のmode/case provenanceがfilenameと一致しません。")
        validate_trace(manifest, trace, require_live=True, _session_attestation=_SESSION_ATTESTATION)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Root orchestration E2E trace contract")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="manifestと全mode/caseのsynthetic contract self-testを検証する")
    sub.add_parser("plan", help="live記録に必要なmode/case matrixを表示する")
    trace = sub.add_parser("validate-trace", help="単一のsynthetic contract traceを検証する")
    trace.add_argument("--trace", type=Path, required=True)
    session = sub.add_parser("validate-session", help="all_modes_x_all_casesのlive trace matrixを検証する")
    session.add_argument("--session-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = _load_yaml(args.manifest.expanduser().resolve())
    validate_manifest(manifest)
    if args.command == "validate":
        for mode in sorted(ROOT_MODES):
            for case_id in sorted(_mapping(manifest["cases"], "cases")):
                build_contract_trace(manifest, case_id, mode)
        print("root-orchestration-eval: contract ok")
    elif args.command == "plan":
        for mode in sorted(ROOT_MODES):
            for case_id in sorted(_mapping(manifest["cases"], "cases")):
                print(f"{mode}__{case_id}.json")
    elif args.command == "validate-trace":
        validate_trace(manifest, _load_trace_file(args.trace))
        print("root-orchestration-eval: trace ok")
    else:
        validate_session(manifest, args.session_dir)
        print("root-orchestration-eval: live session matrix ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OrchestrationEvalError as exc:
        print(f"root-orchestration-eval: error: {exc}", file=sys.stderr)
        raise SystemExit(1)
