"""Compact settings.yaml の安全・成果契約を検証する。"""

from __future__ import annotations

from .core import load_yaml, mapping, read, require, sequence


def _exact_list(value: object, expected: set[str], label: str) -> None:
    actual = {str(item) for item in sequence(value, label)}
    require(actual == expected, f"{label} が期待値と一致しません: {sorted(actual)}")


def validate_settings() -> None:
    path = "template/.agents/orchestra/config/settings.yaml"
    settings = mapping(load_yaml(path), "settings.yaml")
    require(settings.get("version") == "5.0", "settings.yaml.version は 5.0 にしてください。")

    required_sections = {
        "guild_runtime",
        "intake",
        "paths",
        "guild_law",
        "contracts",
        "snapshot",
        "delegation",
        "model_policy",
        "workers",
        "execution",
        "trial",
        "root_session",
        "ledger",
        "reporting",
        "status",
    }
    require(required_sections <= set(settings), "settings.yaml のcompact contract sectionが不足しています。")

    text = read(path)
    for token in (
        "always_guild_intake",
        "confidence_percent",
        "confidence_target_percent",
        "confidence_delta_min_percent",
        "75%",
        "50%",
        "extra_file_reads",
        "validation_iterations",
        "skip_reason_required: true",
        "cost_reason_required: true",
    ):
        require(token not in text, f"settings.yaml に成果を阻害する旧制約 `{token}` が残っています。")

    intake = mapping(settings["intake"], "settings.intake")
    require(intake.get("mode") == "risk_adaptive", "intake.mode は risk_adaptive にしてください。")
    require(intake.get("clarify_only_when_outcome_changes") is True, "確認は成果が変わる場合だけにしてください。")
    _exact_list(
        intake.get("task_contract_fields"),
        {"objective", "success_criteria", "scope", "authority", "validation"},
        "settings.intake.task_contract_fields",
    )

    law = mapping(settings["guild_law"], "settings.guild_law")
    require(law.get("target_repo_required") is True and law.get("target_repo_escape") == "reject", "target repo境界はfail closedにしてください。")
    require(law.get("preserve_user_changes") is True, "既存ユーザー変更を保持してください。")
    absolute_deny = set(sequence(law.get("absolute_deny"), "settings.guild_law.absolute_deny"))
    require({"secret_access", "credential_access", "auth_data_access", "pii_access"} <= absolute_deny, "secret/PII absolute denyが不足しています。")
    confirmation = set(sequence(law.get("confirmation_required_for"), "settings.guild_law.confirmation_required_for"))
    require({"destructive_operation", "dependency_addition", "migration", "deploy", "authorization_effect", "public_api_compatibility_change"} <= confirmation, "人間確認条件が不足しています。")
    state_changes = mapping(law.get("state_changes"), "settings.guild_law.state_changes")
    read_only_git = mapping(state_changes.get("read_only_git"), "settings.guild_law.state_changes.read_only_git")
    require(read_only_git.get("all_roles_in_assigned_read_scope") is True, "assigned read scope内のread-only Gitは全roleに許可してください。")
    require(read_only_git.get("root_control_plane_repo_evidence_expansion") == "forbidden", "read-only GitでRootのcontrol-plane/evidence境界を広げないでください。")
    courier_local_git = mapping(state_changes.get("courier_local_git"), "settings.guild_law.state_changes.courier_local_git")
    require(courier_local_git.get("write_owner") == "courier" and courier_local_git.get("non_courier_write") == "forbidden", "local Git write ownerはcourierだけにしてください。")
    require(
        sequence(courier_local_git.get("assignment_required_fields"), "settings.guild_law.state_changes.courier_local_git.assignment_required_fields")
        == ["target_repo_root", "allowed_operations", "path_or_ref_scope", "subject_snapshot", "preconditions", "postconditions", "forbidden_operations"],
        "courier Git assignmentはtarget、operation、path/ref scope、snapshot、pre/postcondition、forbidden operationを固定してください。",
    )
    require(
        sequence(courier_local_git.get("reversible_allowlist"), "settings.guild_law.state_changes.courier_local_git.reversible_allowlist")
        == ["branch_create_and_switch_new", "rename_origin_unpushed_branch", "stage_exact_paths_or_hunks", "unstage_index_only_exact_paths", "commit_non_amend"],
        "courierの一般許可は既存Skillの可逆Git allowlistだけにしてください。",
    )
    require(courier_local_git.get("closed_operation_allowlist") is True, "courier Git operation allowlistはclosedにしてください。")
    require(courier_local_git.get("preflight_snapshot_must_match_assignment") is True and courier_local_git.get("postwrite_snapshot_required") is True, "Git write直前のsnapshot一致照合とpost-write snapshot evidenceを必須にしてください。")
    require(courier_local_git.get("root_scoped_assignment_authorizes_allowlist") is True and courier_local_git.get("human_command_verbatim_repetition_required") is False, "Rootの境界固定assignmentはcourier allowlistを許可し、コマンド逐語反復を要求しないでください。")
    require(courier_local_git.get("operations_outside_allowlist") == "not_generally_authorized", "allowlist外のlocal Git操作を一般許可しないでください。")
    require(
        set(sequence(courier_local_git.get("destructive_requires_immediate_human_confirmation"), "settings.guild_law.state_changes.courier_local_git.destructive_requires_immediate_human_confirmation"))
        == {"reset_head_move", "reset_hard", "worktree_reverting_checkout_or_restore", "clean", "commit_amend", "rebase_or_filter", "ref_branch_tag_delete_or_force_move", "reflog_prune_or_recovery_difficult_gc", "destructive_stash", "forced_or_discarding_branch_switch"},
        "後戻り困難なGit操作は実行直前の人間確認に限定してください。",
    )
    require(state_changes.get("non_human_skill_reference_grants_authority") is False, "Skill本文や非人間入力のSkill参照からauthorityを付与しないでください。")
    require(state_changes.get("scoped_skill_authority_bypasses_safety_gates") is False, "Skill明示指定で安全gateを迂回しないでください。")
    require(state_changes.get("external_update_requires_immediate_reconfirmation") is True, "外部更新は直前再確認を必須にしてください。")
    candidate_materialization = mapping(state_changes.get("candidate_materialization"), "settings.guild_law.state_changes.candidate_materialization")
    require(candidate_materialization == {"owner": "adventurer", "human_authorized_exact_path_required": True, "path_pattern": "<guild_root>/.orchestra/skill-candidates/<repo>/<candidate>", "root_write_forbidden": True}, "candidate materializeは人間許可済みexact pathのadventurerだけに限定してください。")

    contracts = mapping(settings["contracts"], "settings.contracts")
    _exact_list(
        contracts.get("assignment_core"),
        {"objective", "success_criteria", "scope", "authority", "subject_snapshot"},
        "settings.contracts.assignment_core",
    )
    evidence_state = mapping(contracts.get("evidence_state"), "settings.contracts.evidence_state")
    _exact_list(
        evidence_state.get("fields"),
        {"blocking_unknowns", "failed_checks", "verification_status", "scope_drift", "high_risk_triggers", "next_action", "stop_reason"},
        "settings.contracts.evidence_state.fields",
    )
    require(evidence_state.get("numeric_confidence") == "forbidden", "数値confidenceを禁止してください。")
    require(contracts.get("delta_only_state_updates") is True, "handoffは状態deltaだけを更新してください。")
    model_generated = set(sequence(contracts.get("model_must_not_generate"), "settings.contracts.model_must_not_generate"))
    require({"snapshot_digest", "queue_lineage", "artifact_metadata", "status_transition"} <= model_generated, "機械生成へ移すfieldが不足しています。")

    snapshot = mapping(settings["snapshot"], "settings.snapshot")
    require(snapshot.get("helper") == ".agents/orchestra/scripts/snapshot_digest.py", "snapshot helperが不正です。")
    require(snapshot.get("digest_version") == "agent-guild-orchestra-snapshot-v1" and snapshot.get("mismatch") == "stale_evidence", "snapshot mismatchをfail closedにしてください。")
    require(snapshot.get("explicit_untracked_only") is True and snapshot.get("stage_state_excluded_from_digest") is True, "snapshot safety contractが不足しています。")

    delegation = mapping(settings["delegation"], "settings.delegation")
    require(delegation.get("root_spawns_top_level_agents") is True and delegation.get("top_level_owner") == "root", "top-level agentの起動ownerをRootに限定してください。")
    require(delegation.get("max_depth") == 2 and delegation.get("max_threads") == 64, "delegationはmax_depth=2/max_threads=64にしてください。")
    nested = mapping(delegation.get("allowed_nested_callers"), "settings.delegation.allowed_nested_callers")
    require(set(nested) == {"inquisitor"} and set(sequence(nested.get("inquisitor"), "settings.delegation.allowed_nested_callers.inquisitor")) == {"examiner"}, "nested delegationはinquisitor→examinerだけにしてください。")
    terminal_roles = set(sequence(delegation.get("terminal_roles"), "settings.delegation.terminal_roles"))
    require(terminal_roles == {"cartographer", "guildmaster", "captain", "adventurer", "artificer", "examiner", "sage", "warden", "courier"}, "inquisitor以外のcustom agentをterminalにしてください。")
    for key in ("child_scope_must_narrow", "child_authority_must_narrow", "child_snapshot_must_match", "parent_waits_and_synthesizes"):
        require(delegation.get(key) is True, f"delegation.{key} はtrueにしてください。")
    require(delegation.get("recursive_fanout_beyond_depth_2") == "forbidden", "depth 2を超えるrecursive fan-outを禁止してください。")
    require(delegation.get("runtime_identity_acl") is False, "queue lineageをidentity-backed runtime ACLと表現しないでください。")
    require(delegation.get("nested_edge_enforcement") == "policy_only" and delegation.get("trial_lineage_validation") == "mechanical", "nested edge policyとTrial lineage機械検証を区別してください。")
    require(delegation.get("write_role_children_forbidden") is True and delegation.get("approval_does_not_grant_authority") is True, "write role child禁止とapproval/authority分離が必要です。")
    require(delegation.get("max_threads_or_parallel_is_cost_hard_cap") is False, "max_threads/max_parallelをcost hard capと表現しないでください。")
    avoid = set(sequence(delegation.get("avoid_when"), "settings.delegation.avoid_when"))
    require({"extra_planning_or_review_for_trivial_task", "multi_agent_fanout_for_single_ordered_chain", "shared_mutable_scope"} <= avoid, "過剰なplanning/review/fanoutの抑止条件が不足しています。")
    sage = mapping(delegation.get("sage"), "settings.delegation.sage")
    require(sage.get("trigger") == "concrete_independent_focus" and sage.get("use_by_default") is False, "sageは具体的focusがある時だけ使ってください。")
    require(sage.get("unused_reason_required") is False and sage.get("read_only") is True and sage.get("decision_authority") is False, "sageの軽量terminal contractが不正です。")
    sentinel = mapping(delegation.get("warden"), "settings.delegation.warden")
    require(sentinel.get("routine_use") is False and sentinel.get("read_only") is True and sentinel.get("decision_authority") is False, "Wardenは例外的terminal診断に限定してください。")

    model_policy = mapping(settings["model_policy"], "settings.model_policy")
    require(
        set(model_policy)
        == {
            "root_model",
            "root_project_local_reasoning_effort",
            "root_user_selectable_reasoning_efforts",
            "fixed_pair_per_subagent",
            "subagent_pairs",
        },
        "settings.model_policy はRoot方針と固定subagent pairだけを含めてください。",
    )
    require(model_policy.get("root_model") == "gpt-5.6-sol", "Root modelはgpt-5.6-solにしてください。")
    require(model_policy.get("root_project_local_reasoning_effort") == "unset", "Root reasoning effortをproject-localで固定しないでください。")
    require(
        sequence(model_policy.get("root_user_selectable_reasoning_efforts"), "settings.model_policy.root_user_selectable_reasoning_efforts")
        == ["high", "xhigh", "ultra"],
        "Root reasoning effortは利用者がhigh/xhigh/ultraから選べるようにしてください。",
    )
    require(model_policy.get("fixed_pair_per_subagent") is True, "subagentはroleごとの固定model/effort pairにしてください。")
    expected_pairs = {
        "cartographer": ("gpt-5.6-sol", "high"),
        "guildmaster": ("gpt-5.6-sol", "xhigh"),
        "captain": ("gpt-5.6-sol", "high"),
        "adventurer": ("gpt-5.6-terra", "high"),
        "artificer": ("gpt-5.6-sol", "high"),
        "inquisitor": ("gpt-5.6-sol", "xhigh"),
        "examiner": ("gpt-5.6-terra", "high"),
        "sage": ("gpt-5.6-luna", "xhigh"),
        "warden": ("gpt-5.6-sol", "high"),
        "courier": ("gpt-5.3-codex-spark", "xhigh"),
    }
    pairs = mapping(model_policy.get("subagent_pairs"), "settings.model_policy.subagent_pairs")
    require(set(pairs) == set(expected_pairs), "settings.model_policy.subagent_pairs は定義済みの10 roleだけにしてください。")
    for role, expected_pair in expected_pairs.items():
        pair = mapping(pairs.get(role), f"settings.model_policy.subagent_pairs.{role}")
        require(set(pair) == {"model", "model_reasoning_effort"}, f"settings.model_policy.subagent_pairs.{role} schemaが不正です。")
        require(
            (pair.get("model"), pair.get("model_reasoning_effort")) == expected_pair,
            f"settings.model_policy.subagent_pairs.{role} は {expected_pair[0]} / {expected_pair[1]} にしてください。",
        )

    workers = mapping(settings["workers"], "settings.workers")
    expected_parallel = {
        "cartographer": 2,
        "guildmaster": 1,
        "captain": 2,
        "adventurer": 32,
        "artificer": 1,
        "inquisitor": 2,
        "examiner": 3,
        "sage": 3,
        "warden": 1,
        "courier": 1,
    }
    require(set(workers) == set(expected_parallel), "workersは定義済みの10 roleだけにしてください。")
    actual_parallel: dict[str, int] = {}
    for role, expected in expected_parallel.items():
        worker = mapping(workers.get(role), f"settings.workers.{role}")
        max_parallel = worker.get("max_parallel")
        require(isinstance(max_parallel, int) and not isinstance(max_parallel, bool) and max_parallel >= 1, f"workers.{role}.max_parallel は1以上にしてください。")
        require(max_parallel == expected, f"workers.{role}.max_parallel は{expected}にしてください。")
        actual_parallel[role] = max_parallel
    role_total = sum(actual_parallel.values())
    non_adventurer_total = sum(value for role, value in actual_parallel.items() if role != "adventurer")
    max_threads = delegation.get("max_threads")
    require(role_total == 48 and role_total <= max_threads, "全roleのmax_parallel合計は48かつglobal max_threads=64以下にしてください。")
    require(non_adventurer_total == 16, "非adventurer roleのmax_parallel合計は16にしてください。")
    require(actual_parallel["adventurer"] == 32, "workers.adventurer.max_parallel は32にしてください。")
    require(mapping(workers["adventurer"], "settings.workers.adventurer").get("candidate_materialization") == "human_authorized_exact_path_only", "adventurerのcandidate materialize authorityをexact pathに限定してください。")
    require(max_threads - role_total == 16, "global max_threads=64に対して未割当headroom 16を残してください。")
    focus = mapping(workers["examiner"], "settings.workers.examiner")
    require(set(sequence(focus.get("allowed_callers"), "settings.workers.examiner.allowed_callers")) == {"inquisitor"}, "examiner callerをinquisitorに限定してください。")
    require(focus.get("terminal_worker") is True, "examinerをterminal leafにしてください。")
    require(focus.get("per_trial_policy_cap") == 3 and focus.get("required_by_default") is False, "examinerは任意かつ1 Trialあたりpolicy cap 3にしてください。")
    for key in ("implementation_authority", "decision_authority", "severity_authority", "synthesis_authority", "ledger_authority", "git_authority", "external_action_authority"):
        require(focus.get(key) is False, f"examiner.{key} はfalseにしてください。")
    inquisitor = mapping(workers["inquisitor"], "settings.workers.inquisitor")
    require(set(sequence(inquisitor.get("allowed_child_roles"), "settings.workers.inquisitor.allowed_child_roles")) == {"examiner"}, "inquisitor childをexaminerに限定してください。")
    require(inquisitor.get("nested_delegation_trigger") == "risk_triggered_concrete_single_focus", "inquisitor nested delegation triggerが不正です。")
    for key in ("waits_for_child_reports", "verifies_child_lineage_and_evidence", "owns_severity_disposition_and_final_decision"):
        require(inquisitor.get(key) is True, f"inquisitor.{key} はtrueにしてください。")

    execution = mapping(settings["execution"], "settings.execution")
    require(execution.get("bounded_worker") == "adventurer" and execution.get("cross_scope_integration_worker") == "artificer", "bounded実装とcross-scope統合を分離してください。")
    require(execution.get("shared_artifact_single_owner") is True and execution.get("integration_barrier_required_for_parallel_mutation") is True, "並列mutationの所有権契約が不足しています。")
    require(execution.get("fixed_read_or_validation_counts") is False, "固定read/test回数を禁止してください。")

    trial = mapping(settings["trial"], "settings.trial")
    _exact_list(
        trial.get("common_checks"),
        {"success_criteria", "scope", "authority", "safety", "validation_evidence"},
        "settings.trial.common_checks",
    )
    conditional = mapping(trial.get("conditional_checks"), "settings.trial.conditional_checks")
    require({"architecture", "security", "data_compatibility", "performance", "accessibility", "operations"} <= set(conditional), "change-type別Trial checkが不足しています。")
    independent = set(sequence(trial.get("independent_trial_required_when"), "settings.trial.independent_trial_required_when"))
    require({"high_risk", "shared_contract", "security_sensitive", "migration", "validation_failed", "important_unknown_remains"} <= independent, "独立Trial triggerが不足しています。")
    reviewers = mapping(trial.get("examiners"), "settings.trial.examiners")
    require(reviewers.get("unused_reason_required") is False and reviewers.get("cost_reason_required") is False, "reviewer未使用/costの定型理由を要求しないでください。")
    require(reviewers.get("multiple_examiners_require_focus_split") is True and reviewers.get("final_decision_owner") == "inquisitor", "複数reviewerのfocus分割とdecision ownerが不正です。")

    root = mapping(settings["root_session"], "settings.root_session")
    require(root.get("control_plane_only") is True, "Rootはcontrol-plane onlyにしてください。")
    require(
        set(sequence(root.get("owns"), "settings.root_session.owns"))
        == {
            "intake",
            "target_repo_binding",
            "authority_check",
            "snapshot_request",
            "direct_assignment",
            "agent_wait",
            "browser_control_tool_observation",
            "report_evidence_gate",
            "next_action",
            "report_synthesis",
        },
        "Rootのcontrol-plane責務が不正です。",
    )
    require(
        set(sequence(root.get("allowed_observations"), "settings.root_session.allowed_observations"))
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
    require(set(sequence(root.get("delegated_work"), "settings.root_session.delegated_work")) == delegated_work, "Rootの委譲対象が不正です。")
    require(
        set(sequence(root.get("forbids"), "settings.root_session.forbids"))
        == {"repository_exploration", "implementation", "validation_execution", "browser_execution", "debugging", "review_evidence_generation", "trial_acceptance", "ledger_write"},
        "Rootのauthority separationが不正です。",
    )
    require(root.get("report_required_before_next_action") is True, "Rootはworker reportを待ってから次actionを判断してください。")
    require(root.get("worker_unavailable_outcome") == "needs_human", "worker不在時にRootが直接fallbackしないでください。")
    require(
        root.get("ultra_mode") == "proactive_delegation_within_fixed_topology",
        "Root Ultraは固定topology内のproactive delegationに限定してください。",
    )

    ledger = mapping(settings["ledger"], "settings.ledger")
    require({"raw_log", "raw_discussion", "secret", "pii"} <= set(sequence(ledger.get("never_record"), "settings.ledger.never_record")), "Ledger deny fieldが不足しています。")
    reporting = mapping(settings["reporting"], "settings.reporting")
    require(reporting.get("lead_with_outcome") is True and reporting.get("fixed_template") is False, "outcome-firstで固定templateなしにしてください。")

    status = mapping(settings["status"], "settings.status")
    expected_status = {
        "quest": {"drafted", "active", "needs_human", "blocked", "done", "cancelled"},
        "request": {"drafted", "queued", "accepted", "cancelled"},
        "command": {"drafted", "issued", "active", "done", "cancelled"},
        "assignment": {"idle", "active", "needs_human", "blocked", "done", "failed"},
        "report": {"draft", "recorded", "accepted", "needs_changes"},
        "trial": {"idle", "active", "needs_human", "blocked", "done"},
        "message": {"unread", "read", "archived"},
    }
    require(set(status) == set(expected_status), "settings.statusは全entityのcanonical enumだけを定義してください。")
    for entity, allowed in expected_status.items():
        require(set(sequence(status.get(entity), f"settings.status.{entity}")) == allowed, f"settings.status.{entity}がruntime enumと一致しません。")
    require({"required_artifact", "evidence", "caveats", "next_steps"} <= set(sequence(reporting.get("retain"), "settings.reporting.retain")), "最終成果の必須情報が不足しています。")
