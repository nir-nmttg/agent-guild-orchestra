"""Evidence-based golden Quest contract validation."""

from __future__ import annotations

from .core import ROOT, load_yaml, mapping, require, sequence


GOLDEN_ROOT = "scripts/validation/fixtures/golden_quests"
EXPECTED_FIXTURES = {
    "sage_dialogue_same_focus_stop.yaml",
    "sage_owner_synthesis.yaml",
    "claude_helper_only_disposition.yaml",
    "courier_explicit_git_postcondition.yaml",
    "evidence_state_blocked_contract.yaml",
    "evidence_state_contradictory_evidence.yaml",
    "evidence_state_failed_check.yaml",
    "evidence_state_memory_prevention_artifact.yaml",
    "evidence_state_scope_drift.yaml",
    "evidence_state_security_trigger.yaml",
    "evidence_state_unverified_outcome.yaml",
    "examiner_bounded_contract.yaml",
    "focused_trial_risk_triggered_review.yaml",
    "focused_trial_revision_bound_input.yaml",
    "guild_quest_routing.yaml",
    "ledger_injection_negative.yaml",
    "mapmaking_readonly_no_edit.yaml",
    "party_integration_barrier_stable_revision.yaml",
    "root_coordination_only.yaml",
    "safety_approval_scope_absolute_deny.yaml",
    "safety_gate_needs_human.yaml",
    "warden_evidence_trigger.yaml",
    "solo_small_fix_no_git.yaml",
}


def _fixture(rel_name: str) -> dict[str, object]:
    document = mapping(load_yaml(f"{GOLDEN_ROOT}/{rel_name}"), rel_name)
    require(document.get("id") == rel_name.removesuffix(".yaml"), f"{rel_name}.id は filename と一致させてください。")
    require(document.get("fixture_mode") == "static_contract_example", f"{rel_name}.fixture_mode が不正です。")
    require(isinstance(document.get("summary"), str) and document["summary"], f"{rel_name}.summary が必要です。")
    require("input" in document and "expected" in document, f"{rel_name} は input / expected が必要です。")
    return document


def _expected(rel_name: str) -> dict[str, object]:
    return mapping(_fixture(rel_name).get("expected"), f"{rel_name}.expected")


def _authority(value: object, label: str) -> dict[str, object]:
    authority = mapping(value, label)
    require(set(authority) == {"read", "edit", "validate", "local_git", "external_actions"}, f"{label} は authority 5項目を持つ必要があります。")
    for key, item in authority.items():
        require(isinstance(item, bool), f"{label}.{key} は bool にしてください。")
    return authority


def _forbidden(value: object, label: str) -> set[str]:
    forbidden = mapping(value, label)
    require(forbidden, f"{label} は空にできません。")
    for key, item in forbidden.items():
        require(item is True, f"{label}.{key} は true にしてください。")
    return set(forbidden)


_CANONICAL_SNAPSHOT_FIELDS = {
    "digest_version",
    "kind",
    "revision_id",
    "base_ref",
    "head_ref",
    "diff_hash",
    "dirty_state",
    "scope_paths",
    "untracked_paths",
    "snapshot_id",
}


def _canonical_snapshot(value: object, label: str, *, postwrite: bool = False) -> dict[str, object]:
    snapshot = mapping(value, label)
    require(set(snapshot) == _CANONICAL_SNAPSHOT_FIELDS, f"{label} はhelper canonical snapshot fieldsだけを持つ必要があります。")
    require(snapshot.get("digest_version") == "agent-guild-orchestra-snapshot-v1", f"{label}.digest_version が不正です。")
    require(snapshot.get("kind") == "working_tree_content", f"{label}.kind が不正です。")
    require(isinstance(snapshot.get("revision_id"), str) and isinstance(snapshot.get("base_ref"), str), f"{label} はrevision/baseを持つ必要があります。")
    require(snapshot.get("head_ref") is None, f"{label}.head_ref はworking tree snapshotではnullにしてください。")
    require(snapshot.get("dirty_state") in {"clean", "dirty"}, f"{label}.dirty_state が不正です。")
    require(snapshot.get("snapshot_id") == snapshot.get("diff_hash"), f"{label}.snapshot_id はworking tree diff_hashと一致してください。")
    sequence(snapshot.get("scope_paths"), f"{label}.scope_paths")
    untracked_paths = sequence(snapshot.get("untracked_paths"), f"{label}.untracked_paths")
    if not postwrite:
        require(untracked_paths == [], f"{label}.untracked_paths はassignmentで明示した空listにしてください。")
    return snapshot


def validate_golden_quests() -> None:
    root = ROOT / GOLDEN_ROOT
    require(root.exists(), "golden Quest fixture directory が必要です。")
    actual = {path.name for path in root.glob("*.yaml")}
    require(actual == EXPECTED_FIXTURES, "golden Quest fixture 一覧が期待値と一致しません: " + ", ".join(sorted(actual)))

    # Root is a control-plane judge. Target-repository work always returns through a worker report.
    root_coordination = _expected("root_coordination_only.yaml")
    require(root_coordination.get("root_model") == "gpt-5.6-sol", "Root model は gpt-5.6-sol に固定してください。")
    root_effort = mapping(root_coordination.get("root_reasoning_effort"), "root_coordination.root_reasoning_effort")
    require(root_effort.get("project_local_pin") is False, "Root effortをproject-localに固定しないでください。")
    require(
        set(sequence(root_effort.get("user_selectable_modes"), "root_coordination.root_reasoning_effort.user_selectable_modes"))
        == {"high", "xhigh", "ultra"},
        "Root effort mode は high / xhigh / ultra にしてください。",
    )
    control_plane = mapping(root_coordination.get("control_plane"), "root_coordination.control_plane")
    require(control_plane.get("control_plane_only") is True, "Rootをcontrol-plane onlyにしてください。")
    require(
        set(sequence(control_plane.get("allowed_observations"), "root_coordination.control_plane.allowed_observations"))
        == {"target_repo_identity", "git_status", "snapshot_helper", "queue_state", "browser_observation_facts"},
        "Rootの直接観測はcontrol-plane状態だけにしてください。",
    )
    delegated_work = {
        "repository_exploration",
        "implementation",
        "validation_execution",
        "browser_planning",
        "browser_allowed_operation_specification",
        "browser_evidence_interpretation",
        "debugging",
        "review_evidence_generation",
    }
    require(
        set(sequence(control_plane.get("delegated_work"), "root_coordination.control_plane.delegated_work")) == delegated_work,
        "対象repositoryの作業はworkerへ委譲してください。",
    )
    require(
        {"repository_exploration", "implementation", "validation_execution", "browser_execution", "debugging", "review_evidence_generation", "trial_acceptance", "ledger_write"}
        == _forbidden(control_plane.get("forbidden_root_work"), "root_coordination.control_plane.forbidden_root_work"),
        "Rootの直接作業禁止集合が不正です。",
    )
    require(control_plane.get("report_required_before_next_action") is True, "Rootはworker report前に次actionへ進まないでください。")
    require(control_plane.get("worker_unavailable_outcome") == "needs_human", "worker不在時はRoot直接fallbackでなくneeds_humanにしてください。")
    browser_exception = mapping(control_plane.get("browser_control_tool_exception"), "root_coordination.control_plane.browser_control_tool_exception")
    require(
        browser_exception
        == {
            "root_executes_only": True,
            "subagent_tool_calls": False,
            "required_handoff": ["objective", "url", "authority", "allowed_operations"],
            "root_records": "browser_observation_facts",
        },
        "browser-control toolはRoot限定の仕様実行・観測記録例外にしてください。",
    )
    routing = mapping(root_coordination.get("routing_cases"), "root_coordination.routing_cases")
    require(
        mapping(routing.get("repository_read_only"), "root_coordination.routing_cases.repository_read_only")
        == {"worker_id": "cartographer", "root_executes": False, "report_required": True},
        "read-only repository調査はcartographerへ委譲してください。",
    )
    require(
        mapping(routing.get("bounded_mutation"), "root_coordination.routing_cases.bounded_mutation")
        == {"worker_id": "adventurer", "root_executes": False, "report_required": True},
        "bounded mutationはadventurerへ委譲してください。",
    )
    require(
        mapping(routing.get("browser_readonly"), "root_coordination.routing_cases.browser_readonly")
        == {"worker_id": "cartographer", "root_executes_browser_control_tool": True, "report_required": True},
        "browser read-onlyはcartographerの仕様とRoot限定tool実行を分離してください。",
    )
    require(
        mapping(routing.get("worker_unavailable"), "root_coordination.routing_cases.worker_unavailable")
        == {"outcome": "needs_human", "root_direct_fallback": False},
        "worker不在時にRootが対象repository作業を代行しないでください。",
    )
    ultra = mapping(root_coordination.get("ultra_mode"), "root_coordination.ultra_mode")
    require(
        ultra
        == {
            "proactive_delegation": True,
            "authority_unchanged": True,
            "role_topology_unchanged": True,
            "subagent_pair_overrides_allowed": False,
            "report_gate_unchanged": True,
        },
        "Ultraは委譲の積極性だけを変え、authority/topology/fixed pair/report gateを変えないでください。",
    )

    # Read-only mapmaking stays safe without forcing a ceremonial sage pass.
    mapmaking = _expected("mapmaking_readonly_no_edit.yaml")
    require(mapmaking.get("rank") == "mapmaking" and mapmaking.get("worker_id") == "cartographer", "mapmaking fixture identity が不正です。")
    require(_authority(mapmaking.get("authority"), "mapmaking.authority") == {"read": True, "edit": False, "validate": False, "local_git": False, "external_actions": False}, "mapmaking は read-only にしてください。")
    require(mapmaking.get("sage_required") is False, "mapmaking は具体的な独立 focus がない sage 起動を必須にしないでください。")
    _forbidden(mapmaking.get("forbidden"), "mapmaking.forbidden")

    # A small verified change can finish without a 12-gate form.
    solo = _expected("solo_small_fix_no_git.yaml")
    require(solo.get("rank") == "solo_quest" and solo.get("worker_id") == "adventurer", "solo fixture identity が不正です。")
    solo_authority = _authority(solo.get("authority"), "solo.authority")
    require(solo_authority["edit"] is True and solo_authority["validate"] is True and solo_authority["local_git"] is False and solo_authority["external_actions"] is False, "solo authority が不正です。")
    handoff = mapping(solo.get("handoff_sufficiency"), "solo.handoff_sufficiency")
    handoff_core = set(sequence(handoff.get("owner_to_trial_ready_requires"), "solo.handoff_sufficiency.owner_to_trial_ready_requires"))
    require({"objective", "success_criteria", "scope", "authority", "evidence_state", "validation_evidence", "risks", "base_snapshot", "result_snapshot"} <= handoff_core, "solo handoff core が不足しています。")
    self_check = mapping(solo.get("self_check"), "solo.self_check")
    require(self_check.get("completion_allowed") is True and self_check.get("verification_status") == "verified", "solo completion は verified evidence を必要とします。")
    for key in ("risk_triggers", "blocking_unknowns", "failed_checks"):
        require(sequence(self_check.get(key), f"solo.self_check.{key}") == [], f"solo.self_check.{key} は空にしてください。")
    require(self_check.get("scope_drift") is False, "solo completion で scope drift を許可しないでください。")
    _forbidden(solo.get("forbidden"), "solo.forbidden")

    # Integration and Trial remain bound to stable, machine-generated revisions.
    party_doc = _fixture("party_integration_barrier_stable_revision.yaml")
    party_input = mapping(party_doc.get("input"), "party.input")
    party = mapping(party_doc.get("expected"), "party.expected")
    barrier = mapping(party.get("integration_barrier"), "party.integration_barrier")
    require(barrier.get("required") is True and barrier.get("all_required_reports_complete") is True and barrier.get("single_artificer_required") is True, "party integration barrier が不足しています。")
    require(party_input.get("artificer") == "artificer", "party artificerは専用workerにしてください。")
    require(barrier.get("artificer") == party_input.get("artificer") and barrier.get("integration_before_barrier_allowed") is False, "party artificer/barrier が不正です。")
    stable = mapping(party.get("stable_snapshot"), "party.stable_snapshot")
    require(stable.get("revision_id") == party_input.get("final_revision_id") and stable.get("diff_hash") == party_input.get("final_diff_hash"), "party stable snapshot が input と一致しません。")
    for key in ("trial_bound_to_revision", "ledger_bound_to_revision", "revision_change_invalidates_trial", "revision_change_reopens_barrier"):
        require(stable.get(key) is True, f"party.stable_snapshot.{key} は true にしてください。")
    _forbidden(party.get("forbidden"), "party.forbidden")

    revision_doc = _fixture("focused_trial_revision_bound_input.yaml")
    revision_input = mapping(revision_doc.get("input"), "revision.input")
    revision = mapping(revision_doc.get("expected"), "revision.expected")
    binding = mapping(revision.get("revision_binding"), "revision.revision_binding")
    for key in ("base_ref", "head_ref", "revision_id", "diff_hash", "subject_assignment_ids", "subject_report_ids", "changed_files"):
        require(binding.get(key) == revision_input.get(key), f"revision binding {key} が input と一致しません。")
    require(binding.get("stale_evidence_outcome") == "stop" and binding.get("revision_change_requires_rerun") is True, "stale Trial evidence は停止・再実行してください。")
    _forbidden(revision.get("forbidden"), "revision.forbidden")

    # Independent review is risk-triggered, bounded, read-only and non-decision.
    risk_trial = _expected("focused_trial_risk_triggered_review.yaml")
    require(risk_trial.get("trial_depth") == "focused_trial" and risk_trial.get("worker_id") == "inquisitor", "risk-triggered Trial identity が不正です。")
    topology = mapping(risk_trial.get("nested_topology"), "risk_trial.nested_topology")
    require(
        topology == {
            "root_depth": 0,
            "parent_role": "inquisitor",
            "parent_depth": 1,
            "child_role": "examiner",
            "child_depth": 2,
            "max_depth": 2,
            "parent_waits_and_synthesizes": True,
            "child_terminal": True,
            "child_snapshot_same_helper_object": True,
            "child_target_same_as_parent": True,
            "child_preserves_parent_deny_and_safety": True,
        },
        "nested Trial topologyはRoot(depth0)→Inquisitor(depth1)→Examiner(depth2)に限定してください。",
    )
    examiner_assignment = mapping(risk_trial.get("examiner_assignment"), "risk_trial.examiner_assignment")
    require(examiner_assignment == {"worker_id": "examiner", "risk_trigger": "security", "focus": "authorization_boundary", "read_only": True, "terminal_worker": True}, "reviewer assignment は concrete risk focus に限定してください。")
    require(set(sequence(risk_trial.get("handoff_core"), "risk_trial.handoff_core")) == {"objective", "success_criteria", "scope", "authority", "evidence_state", "snapshot", "risks"}, "Trial handoff core が不正です。")
    _forbidden(risk_trial.get("forbidden"), "risk_trial.forbidden")

    reviewer_doc = _fixture("examiner_bounded_contract.yaml")
    reviewer_input = mapping(reviewer_doc.get("input"), "reviewer.input")
    reviewer = mapping(reviewer_doc.get("expected"), "reviewer.expected")
    require(reviewer.get("worker_id") == "examiner" and reviewer.get("assignment_owner") == "inquisitor", "examiner identity が不正です。")
    require(reviewer.get("risk_trigger_required") is True and reviewer_input.get("risk_trigger") and reviewer.get("concrete_focus_required") is True and reviewer_input.get("focus"), "examiner は risk trigger と concrete focus が必要です。")
    for key in ("read_only_required", "owner_synthesis_required", "finding_disposition_required", "recursive_multi_agent_disabled"):
        require(reviewer.get(key) is True, f"reviewer.{key} は true にしてください。")
    require(reviewer.get("caller_enforcement") == "policy_only", "examiner caller edgeはpolicy-onlyにしてください。")
    require(reviewer.get("trial_lineage_validation") == "mechanical", "examiner Trial lineageは機械検証してください。")
    require(reviewer.get("runtime_identity_acl") is False and reviewer.get("event_actor_is_identity_backed_caller") is False, "queue lineageやevent.actorをidentity-backed runtime ACLと表現しないでください。")
    require(reviewer.get("child_snapshot_must_equal_parent_trial") is True, "examiner child snapshotは親Trialと完全一致させてください。")
    for key in (
        "child_target_must_equal_parent_trial",
        "child_deny_and_safety_must_preserve_parent",
        "child_authority_must_equal_parent_trial",
        "examiner_summary_required",
        "examiner_evidence_refs_required",
        "evidence_item_id_unique_within_trial",
        "final_summary_required",
        "final_evidence_refs_required",
        "final_disposition_exactly_once",
        "accepted_decision_forbids_unresolved",
    ):
        require(reviewer.get(key) is True, f"reviewer.{key} は true にしてください。")
    require(set(sequence(reviewer.get("evidence_item_schema"), "reviewer.evidence_item_schema")) == {"id", "summary"}, "examiner evidence item schemaはstable id/summaryにしてください。")
    require(set(sequence(reviewer.get("report_binding_required"), "reviewer.report_binding_required")) == {"examiner_assignment", "inquisitor_trial", "quest", "workflow", "subject_snapshot"}, "examiner report bindingが不足しています。")
    require(reviewer.get("caller_lineage_check_normalized_by_queue") is True and reviewer.get("final_report_requires_all_examiner_reports") is True, "examiner reportの機械lineage正規化とfinal completeness gateが必要です。")
    assignment_snapshot = mapping(reviewer_input.get("assignment_snapshot"), "reviewer.assignment_snapshot")
    require(assignment_snapshot == mapping(reviewer_input.get("matching_report_snapshot"), "reviewer.matching_snapshot"), "matching reviewer snapshot が不正です。")
    require(assignment_snapshot != mapping(reviewer_input.get("mismatched_report_snapshot"), "reviewer.mismatched_snapshot"), "mismatched reviewer snapshot を区別してください。")
    _forbidden(reviewer.get("forbidden"), "reviewer.forbidden")

    # Sage is optional, evidence-driven and never owns the decision.
    sage = _expected("sage_dialogue_same_focus_stop.yaml")
    require(sage.get("worker_id") == "sage" and sage.get("decision_authority") is False and sage.get("terminal_worker") is True, "sage authority が不正です。")
    policy = mapping(sage.get("evidence_policy"), "sage.evidence_policy")
    require(policy.get("concrete_focus_required") is True, "sage は concrete focus がある時だけ使ってください。")
    require({"no_new_evidence_added", "blocking_unknowns_unchanged", "authority_or_boundary_would_expand"} <= set(sequence(policy.get("stop_when"), "sage.evidence_policy.stop_when")), "sage evidence stop conditions が不足しています。")
    _forbidden(sage.get("forbidden"), "sage.forbidden")

    sage_synthesis_doc = _fixture("sage_owner_synthesis.yaml")
    sage_input = mapping(sage_synthesis_doc.get("input"), "sage_synthesis.input")
    sage_synthesis = mapping(sage_synthesis_doc.get("expected"), "sage_synthesis.expected")
    owner = mapping(sage_synthesis.get("owner_synthesis"), "sage_synthesis.owner_synthesis")
    require(owner.get("required") is True and owner.get("owner_worker_id") == sage_input.get("owner_worker_id") and owner.get("final_decision_by_owner_only") is True, "sage owner synthesis が不正です。")
    require(owner.get("sage_judgment_is_not_owner_decision") is True and owner.get("evidence_verification_required") is True, "sage finding は owner 検証を必要とします。")
    _forbidden(sage_synthesis.get("forbidden"), "sage_synthesis.forbidden")

    # Warden reacts to evidence triggers, never to a self-scored percentage.
    warden = _expected("warden_evidence_trigger.yaml")
    require(warden.get("worker_id") == "warden" and warden.get("decision_authority") is False and warden.get("terminal_worker") is True, "warden authority が不正です。")
    require({"scope_drift", "security"} <= set(sequence(warden.get("triggers_present"), "warden.triggers_present")), "warden evidence triggers が不足しています。")
    require("numeric_confidence" in sequence(warden.get("triggers_absent"), "warden.triggers_absent"), "numeric confidence を warden trigger にしないでください。")
    require(mapping(warden.get("output_contract"), "warden.output_contract") == {"evidence_state_only": True}, "warden output は evidence state だけにしてください。")
    _forbidden(warden.get("forbidden"), "warden.forbidden")

    blocked = _expected("evidence_state_blocked_contract.yaml")
    require(blocked.get("next_action") == "reconstruct_task_contract" and blocked.get("completion_allowed") is False and blocked.get("speculative_editing_allowed") is False, "blocking evidence は contract 再構成まで停止してください。")
    _forbidden(blocked.get("forbidden"), "blocked.forbidden")

    unverified = _expected("evidence_state_unverified_outcome.yaml")
    require(unverified.get("next_action") == "verify_target_behavior" and unverified.get("completion_allowed") is False, "important unknown は検証まで completion を止めてください。")
    require(unverified.get("warden_required") is False, "通常の未検証事項だけで warden を必須にしないでください。")
    _forbidden(unverified.get("forbidden"), "unverified.forbidden")

    failed = _expected("evidence_state_failed_check.yaml")
    require(failed.get("next_action") == "address_first_failure_then_rerun" and failed.get("first_failure_recorded") is True and failed.get("rerun_same_check_required") is True, "failed check handling が不正です。")
    _forbidden(failed.get("forbidden"), "failed.forbidden")

    scope = _expected("evidence_state_scope_drift.yaml")
    require(scope.get("scope_drift") is True and scope.get("pause_required") is True and scope.get("next_action") == "restate_scope_and_check_goal_relevance", "scope drift handling が不正です。")
    _forbidden(scope.get("forbidden"), "scope.forbidden")

    security = _expected("evidence_state_security_trigger.yaml")
    require("security" in sequence(security.get("high_risk_triggers"), "security.high_risk_triggers"), "security risk trigger が必要です。")
    require(security.get("next_action") == "invoke_security_review" and security.get("security_review_owner") == "inquisitor" and security.get("new_worker_allowed") is False, "security review route が不正です。")
    _forbidden(security.get("forbidden"), "security.forbidden")

    contradictory = _expected("evidence_state_contradictory_evidence.yaml")
    require(contradictory.get("next_action") == "revise_plan_from_evidence" and contradictory.get("original_assumption_invalidated") is True, "contradictory evidence は plan を更新してください。")
    _forbidden(contradictory.get("forbidden"), "contradictory.forbidden")

    memory = _expected("evidence_state_memory_prevention_artifact.yaml")
    require(memory.get("prevention_artifact_required") is True and memory.get("write_authority") == "courier_ledger_only", "memory persistence は prevention artifact と authority が必要です。")
    require({"explicit_memory_persistence_authority", "sanitized_summary_only", "ledger_disposition_recorded"} <= set(sequence(memory.get("memory_write_requires"), "memory.memory_write_requires")), "memory persistence requirements が不足しています。")
    _forbidden(memory.get("forbidden"), "memory.forbidden")

    # Safety, Git and untrusted-input boundaries remain unchanged and fail closed.
    safety = _expected("safety_gate_needs_human.yaml")
    require(safety.get("trial_depth") == "safety_gate" and safety.get("outcome") == "needs_human", "safety gate は needs_human で停止してください。")
    safety_authority = _authority(safety.get("authority"), "safety.authority")
    require(not any(safety_authority[key] for key in ("edit", "validate", "local_git", "external_actions")), "safety gate は read 以外を禁止してください。")
    require({"deploy", "secret"} <= set(sequence(safety.get("human_confirmation_required"), "safety.human_confirmation_required")), "safety gate confirmation が不足しています。")
    _forbidden(safety.get("forbidden"), "safety.forbidden")

    approval_doc = _fixture("safety_approval_scope_absolute_deny.yaml")
    approval_input = mapping(approval_doc.get("input"), "approval.input")
    human = mapping(approval_input.get("human_approval"), "approval.input.human_approval")
    approval = mapping(approval_doc.get("expected"), "approval.expected")
    approval_scope = mapping(approval.get("approval_scope"), "approval.approval_scope")
    require(approval_scope.get("authorized_actions") == human.get("actions") and approval_scope.get("target") == human.get("target") and approval_scope.get("revision") == human.get("revision"), "approval scope は action/target/revision に固定してください。")
    absolute = mapping(approval.get("absolute_deny"), "approval.absolute_deny")
    require(absolute.get("applies_even_with_human_approval") is True and {"secret_access", "pii_access"} <= set(sequence(absolute.get("operations"), "approval.absolute_deny.operations")), "secret/PII absolute deny を保持してください。")
    _forbidden(approval.get("forbidden"), "approval.forbidden")

    courier_doc = _fixture("courier_explicit_git_postcondition.yaml")
    courier_input = mapping(courier_doc.get("input"), "courier.input")
    courier = mapping(courier_doc.get("expected"), "courier.expected")
    require(_authority(courier.get("authority"), "courier.authority") == {"read": True, "edit": False, "validate": False, "local_git": True, "external_actions": False}, "courier authority が不正です。")
    auth = mapping(courier.get("authorization"), "courier.authorization")
    require(auth.get("source") == "root_scoped_courier_assignment" and auth.get("skill_name") == "git-split-commits-from-diff", "courier Git authority はRootの境界固定assignmentと既存Git Skillに固定してください。")
    require(auth.get("local_git_write_owner") == "courier" and auth.get("command_verbatim_repetition_required") is False, "courierだけをlocal Git write ownerとし、コマンド逐語反復を要求しないでください。")
    require(auth.get("target_repo_root") == courier_input.get("target_repo_root"), "courier Git authority の target_repo_root はRoot assignmentに固定してください。")
    path_scope = mapping(auth.get("path_or_ref_scope"), "courier.authorization.path_or_ref_scope")
    require(path_scope.get("paths") == courier_input.get("allowed_paths"), "courier Git authority のpath scopeはassignmentに固定してください。")
    input_snapshot = _canonical_snapshot(courier_input.get("subject_snapshot"), "courier.input.subject_snapshot")
    snapshot = _canonical_snapshot(auth.get("subject_snapshot"), "courier.authorization.subject_snapshot")
    require(snapshot == input_snapshot, "courier Git authorityのcanonical snapshotはinput assignmentと完全一致してください。")
    closed_allowlist = {"branch_create_and_switch_new", "rename_origin_unpushed_branch", "stage_exact_paths_or_hunks", "unstage_index_only_exact_paths", "commit_non_amend"}
    require(set(sequence(auth.get("allowed_operations"), "courier.authorization.allowed_operations")) <= closed_allowlist and auth.get("allowed_operations") == ["stage_exact_paths_or_hunks", "commit_non_amend"], "courier Git authorityはclosedな可逆allowlistのassignment操作に限定してください。")
    require(sequence(auth.get("required_assignment_fields"), "courier.authorization.required_assignment_fields") == ["target_repo_root", "allowed_operations", "path_or_ref_scope", "subject_snapshot", "preconditions", "postconditions", "forbidden_operations"], "courier assignmentの必須境界が不足しています。")
    require(auth.get("outside_allowlist") == "not_generally_authorized" and auth.get("closed_operation_allowlist") is True and auth.get("safety_gates_bypassed") is False, "allowlist外の一般許可や安全gate迂回を許可しないでください。")
    preconditions = mapping(courier.get("preconditions"), "courier.preconditions")
    required_preconditions = {"target_repo_root_confirmed", "branch_confirmed", "git_status_inspected", "staged_diff_inspected", "unrelated_changes_excluded", "snapshot_helper_reissued_same_kind_base_scope", "preflight_snapshot_matches_assignment"}
    require(
        set(preconditions) == required_preconditions and all(value is True for value in preconditions.values()),
        "courierは最初のGit write直前に同一kind/base/scopeのhelper snapshot完全一致を確認してください。",
    )
    preflight_snapshot = _canonical_snapshot(courier.get("preflight_snapshot"), "courier.preflight_snapshot")
    require(preflight_snapshot == input_snapshot, "courier preflight canonical snapshotはinput/authorization snapshotと完全一致してください。")
    postconditions = mapping(courier.get("postconditions"), "courier.postconditions")
    required_postconditions = {"git_status_checked", "commit_hash_recorded", "committed_paths_match_scope", "remaining_changes_reported", "branch_recorded", "upstream_validation_result_recorded", "ledger_event_disposition_recorded", "committed_diff_matches_accepted_hash", "external_state_unchanged", "postwrite_untracked_from_postwrite_state"}
    require(set(postconditions) == required_postconditions and all(value is True for value in postconditions.values()), "courier postconditionはclosedな必須集合で、critical booleanをすべてtrueにしてください。")
    postwrite_snapshot = _canonical_snapshot(courier.get("postwrite_snapshot"), "courier.postwrite_snapshot", postwrite=True)
    require(postwrite_snapshot.get("scope_paths") == input_snapshot.get("scope_paths") and postwrite_snapshot.get("base_ref") == postwrite_snapshot.get("revision_id"), "post-write snapshotはworking_tree_content kindで、scopeを保持しpost-write revision/base lineageを満たしてください。")
    destructive = mapping(courier.get("destructive_operations"), "courier.destructive_operations")
    require(
        set(sequence(destructive.get("requires_immediate_human_confirmation"), "courier.destructive_operations.requires_immediate_human_confirmation"))
        == {"reset_head_move", "reset_hard", "worktree_reverting_checkout_or_restore", "clean", "commit_amend", "rebase_or_filter", "ref_branch_tag_delete_or_force_move", "reflog_prune_or_recovery_difficult_gc", "destructive_stash", "forced_or_discarding_branch_switch"}
        and sequence(destructive.get("authorized_in_this_assignment"), "courier.destructive_operations.authorized_in_this_assignment") == [],
        "後戻り困難なGit操作は実行直前の人間確認なしにassignmentで許可しないでください。",
    )
    require({"push", "pull_request", "destructive_git", "unrelated_stage", "root_or_other_role_local_git_write", "general_branch_switch", "broad_stage_pathspec"} <= _forbidden(courier.get("forbidden"), "courier.forbidden"), "courier forbidden setが不足しています。")
    negative = mapping(courier.get("negative_cases"), "courier.negative_cases")
    stale = mapping(negative.get("stale_snapshot"), "courier.negative_cases.stale_snapshot")
    require(mapping(stale.get("preflight"), "courier.negative_cases.stale_snapshot.preflight") == {"same_kind_base_scope": True, "snapshot_matches_assignment": False} and stale.get("outcome") == "stale_evidence" and stale.get("git_write_executed") is False, "stale snapshotはGit write前に停止してください。")
    wrong_owner = mapping(negative.get("wrong_owner"), "courier.negative_cases.wrong_owner")
    require(wrong_owner == {"assignment_owner": "adventurer", "outcome": "reject_wrong_owner", "git_write_executed": False}, "courier以外のGit write ownerを拒否してください。")
    outside = mapping(negative.get("outside_allowlist_operation"), "courier.negative_cases.outside_allowlist_operation")
    require(outside == {"requested_operation": "merge", "outcome": "reject_outside_allowlist", "git_write_executed": False}, "allowlist外operationを拒否してください。")
    destructive_switch = mapping(negative.get("destructive_switch_without_reconfirmation"), "courier.negative_cases.destructive_switch_without_reconfirmation")
    require(destructive_switch == {"requested_command": "git switch --discard-changes existing-branch", "immediate_human_confirmation": False, "outcome": "needs_human", "git_write_executed": False}, "破壊的switchには直前人間確認が必要です。")
    external = mapping(negative.get("external_update_without_reconfirmation"), "courier.negative_cases.external_update_without_reconfirmation")
    require(external == {"requested_operation": "push", "immediate_human_reconfirmation": False, "outcome": "needs_human", "external_action_executed": False}, "外部更新には直前再確認が必要です。")
    canonical_mismatch = mapping(negative.get("canonical_snapshot_mismatch"), "courier.negative_cases.canonical_snapshot_mismatch")
    require(canonical_mismatch.get("input_snapshot_id") == input_snapshot.get("snapshot_id") and canonical_mismatch.get("authorization_snapshot_id") != input_snapshot.get("snapshot_id") and canonical_mismatch.get("outcome") == "stale_evidence" and canonical_mismatch.get("git_write_executed") is False, "canonical snapshot mismatchはstale_evidenceとしてGit write前に拒否してください。")
    target_not_confirmed = mapping(negative.get("target_repo_not_confirmed"), "courier.negative_cases.target_repo_not_confirmed")
    require(target_not_confirmed == {"target_repo_root_confirmed": False, "outcome": "reject_preconditions", "git_write_executed": False}, "target_repo_root_confirmed=falseを拒否してください。")
    paths_mismatch = mapping(negative.get("committed_paths_scope_mismatch"), "courier.negative_cases.committed_paths_scope_mismatch")
    require(paths_mismatch == {"committed_paths_match_scope": False, "outcome": "reject_postconditions", "git_write_executed": False}, "committed_paths_match_scope=falseを拒否してください。")
    external_changed = mapping(negative.get("external_state_changed"), "courier.negative_cases.external_state_changed")
    require(external_changed == {"external_state_unchanged": False, "outcome": "reject_postconditions", "git_write_executed": False}, "external_state_unchanged=falseを拒否してください。")

    ledger = _expected("ledger_injection_negative.yaml")
    require(ledger.get("outcome") == "reject_untrusted_instruction", "Ledger instruction injection を拒否してください。")
    _forbidden(ledger.get("forbidden"), "ledger.forbidden")

    claude = _expected("claude_helper_only_disposition.yaml")
    require(claude.get("trust") == "untrusted" and claude.get("access_path") == "helper_only" and claude.get("authority_granted") is False, "Claude context は helper-only untrusted data にしてください。")
    _forbidden(claude.get("forbidden"), "claude.forbidden")

    guild = _expected("guild_quest_routing.yaml")
    require(guild.get("rank") == "guild_quest" and guild.get("strategy_owner") == "guildmaster", "guild routing identity が不正です。")
    require({"broad_impact", "multiple_parties", "safety_judgment"} <= set(sequence(guild.get("routing_reasons"), "guild.routing_reasons")), "guild routing reasons が不足しています。")
    _forbidden(guild.get("forbidden"), "guild.forbidden")
