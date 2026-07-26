"""Root orchestration E2E trace harnessの決定的検証。"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path

from .core import ROOT, load_yaml, mapping, require, sequence


def _renumber(trace: dict[str, object]) -> None:
    events = sequence(trace.get("events"), "trace.events")
    for index, event in enumerate(events, start=1):
        mapping(event, f"trace.events[{index - 1}]")["seq"] = index


def _must_reject(module: object, manifest: dict[str, object], trace: dict[str, object], label: str, *, require_live: bool = False) -> None:
    try:
        module.validate_trace(manifest, trace, require_live=require_live)
    except module.OrchestrationEvalError:
        return
    require(False, f"Root orchestration trace validatorは{label}を拒否してください。")


def _manifest_must_reject(module: object, manifest: dict[str, object], label: str) -> None:
    try:
        module.validate_manifest(manifest)
    except module.OrchestrationEvalError:
        return
    require(False, f"Root orchestration manifest validatorは{label}を拒否してください。")


def validate_root_orchestration_eval() -> None:
    script_path = ROOT / "scripts/root_orchestration_eval.py"
    manifest_path = ROOT / "scripts/root_orchestration_eval.yaml"
    spec = importlib.util.spec_from_file_location("root_orchestration_eval", script_path)
    require(spec is not None and spec.loader is not None, "root_orchestration_eval.pyをimportできません。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module._load_yaml(manifest_path)
    module.validate_manifest(manifest)

    settings = mapping(load_yaml("template/.agents/orchestra/config/settings.yaml"), "settings")
    model_policy = mapping(settings.get("model_policy"), "settings.model_policy")
    root = mapping(manifest.get("root"), "root_orchestration.root")
    require(root.get("model") == model_policy.get("root_model"), "Root orchestration manifest modelをsettingsと一致させてください。")
    require(
        sequence(root.get("modes"), "root_orchestration.root.modes")
        == sequence(model_policy.get("root_user_selectable_reasoning_efforts"), "settings.model_policy.root_user_selectable_reasoning_efforts"),
        "Root orchestration modeをsettingsのhigh/xhigh/ultraと一致させてください。",
    )
    settings_pairs = mapping(model_policy.get("subagent_pairs"), "settings.model_policy.subagent_pairs")
    trace_pairs = mapping(manifest.get("fixed_pairs"), "root_orchestration.fixed_pairs")
    require(set(settings_pairs) == set(trace_pairs), "Root orchestration fixed pair roleをsettingsと一致させてください。")
    for role, raw_pair in settings_pairs.items():
        pair = mapping(raw_pair, f"settings.model_policy.subagent_pairs.{role}")
        require(
            mapping(trace_pairs.get(role), f"root_orchestration.fixed_pairs.{role}")
            == {"model": pair.get("model"), "effort": pair.get("model_reasoning_effort")},
            f"Root orchestration {role} pairをsettingsと一致させてください。",
        )
    settings_root_session = mapping(settings.get("root_session"), "settings.root_session")
    trace_contract = mapping(manifest.get("trace_contract"), "root_orchestration.trace_contract")
    require(
        "browser_control_tool_observation" in sequence(trace_contract.get("root_allowed_actions"), "trace_contract.root_allowed_actions")
        and "browser_execution" in sequence(trace_contract.get("root_forbidden_actions"), "trace_contract.root_forbidden_actions")
        and "browser_control_tool_observation" in sequence(settings_root_session.get("owns"), "settings.root_session.owns"),
        "Rootのbrowser-control tool例外は専用actionだけを許可し、一般browser実行は禁止してください。",
    )
    require(
        "browser_control_tool_observation"
        not in {
            action
            for actions in mapping(trace_contract.get("role_actions"), "trace_contract.role_actions").values()
            for action in sequence(actions, "trace_contract.role_actions")
        },
        "subagentにbrowser-control toolを許可しないでください。",
    )

    cases = mapping(manifest.get("cases"), "root_orchestration.cases")
    for mode in ("high", "xhigh", "ultra"):
        for case_id in sorted(cases):
            trace = module.build_contract_trace(manifest, case_id, mode)
            module.validate_trace(manifest, trace)

    weakened_case_manifest = copy.deepcopy(manifest)
    weakened_guild = mapping(weakened_case_manifest["cases"]["guild"], "weakened guild case")
    weakened_guild["contract_fixtures"] = ["root_coordination_only.yaml"]
    weakened_guild["expected_top_level_roles"] = []
    weakened_guild["top_level_role_phases"] = []
    weakened_guild["required_role_actions"] = {}
    _manifest_must_reject(module, weakened_case_manifest, "代表case contractの弱体化")

    root_work = module.build_contract_trace(manifest, "solo", "high")
    root_work_events = sequence(root_work["events"], "root_work.events")
    terminal = root_work_events.pop()
    root_work_events.append(
        {
            "seq": 0,
            "actor": "root",
            "depth": 0,
            "action": "implementation",
            "assignment_id": "assignment_solo_adventurer",
        }
    )
    root_work_events.append(terminal)
    _renumber(root_work)
    _must_reject(module, manifest, root_work, "Root直接implementation")

    browser_trace = module.build_contract_trace(manifest, "browser_readonly", "high")
    module.validate_trace(manifest, browser_trace)

    subagent_browser_tool = copy.deepcopy(browser_trace)
    for raw_event in sequence(subagent_browser_tool["events"], "subagent_browser_tool.events"):
        event = mapping(raw_event, "subagent_browser_tool.event")
        if event.get("action") == "browser_control_tool_observation":
            event["actor"] = "cartographer"
            event["depth"] = 1
            break
    _must_reject(module, manifest, subagent_browser_tool, "subagentによるbrowser-control tool呼び出し")

    browser_before_spec = module.build_contract_trace(manifest, "browser_readonly", "xhigh")
    browser_events = sequence(browser_before_spec["events"], "browser_before_spec.events")
    tool_index = next(
        index
        for index, event in enumerate(browser_events)
        if mapping(event, "browser_before_spec.event").get("action") == "browser_control_tool_observation"
    )
    spec_index = next(
        index
        for index, event in enumerate(browser_events)
        if mapping(event, "browser_before_spec.event").get("action") == "browser_allowed_operation_specification"
    )
    browser_events[tool_index], browser_events[spec_index] = browser_events[spec_index], browser_events[tool_index]
    _renumber(browser_before_spec)
    _must_reject(module, manifest, browser_before_spec, "role仕様前のRoot browser-control tool実行")

    early_action = module.build_contract_trace(manifest, "solo", "xhigh")
    early_events = sequence(early_action["events"], "early_action.events")
    terminal = early_events.pop()
    early_events.insert(3, terminal)
    _renumber(early_action)
    _must_reject(module, manifest, early_action, "worker report前のRoot next action")

    pair_drift = module.build_contract_trace(manifest, "solo", "ultra")
    for raw_event in sequence(pair_drift["events"], "pair_drift.events"):
        event = mapping(raw_event, "pair_drift.event")
        if event.get("action") == "assignment_created" and event.get("target_role") == "adventurer":
            event["model"] = "gpt-5.6-sol"
            break
    _must_reject(module, manifest, pair_drift, "固定pair drift")

    invalid_target_binding = module.build_contract_trace(manifest, "mapmaking", "high")
    first_event = mapping(sequence(invalid_target_binding["events"], "invalid_target_binding.events")[0], "invalid_target_binding.event")
    first_event["target_repo_root"] = "repositories/relative-target"
    _must_reject(module, manifest, invalid_target_binding, "相対target repo binding")

    nested_target_binding = module.build_contract_trace(manifest, "mapmaking", "xhigh")
    nested_events = sequence(nested_target_binding["events"], "nested_target_binding.events")
    for raw_event in nested_events:
        event = mapping(raw_event, "nested_target_binding.event")
        if "target_repo_root" in event:
            event["target_repo_root"] = "/eval-guild/repositories/synthetic-target/nested"
    _must_reject(module, manifest, nested_target_binding, "repositories直下でないnested target binding")

    snapshot_binding_drift = module.build_contract_trace(manifest, "solo", "xhigh")
    for raw_event in sequence(snapshot_binding_drift["events"], "snapshot_binding_drift.events"):
        event = mapping(raw_event, "snapshot_binding_drift.event")
        if event.get("action") == "assignment_created":
            event["snapshot_ref"] = "sha256:" + "0" * 64
            break
    _must_reject(module, manifest, snapshot_binding_drift, "assignment snapshot binding drift")

    evidence_binding_drift = module.build_contract_trace(manifest, "sage_focus", "ultra")
    for raw_event in sequence(evidence_binding_drift["events"], "evidence_binding_drift.events"):
        event = mapping(raw_event, "evidence_binding_drift.event")
        if event.get("action") == "report_evidence_gate":
            event["evidence_ref"] = "sha256:" + "f" * 64
            break
    _must_reject(module, manifest, evidence_binding_drift, "report evidence binding drift")

    unauthorized_edge = module.build_contract_trace(manifest, "focused_trial", "high")
    for raw_event in sequence(unauthorized_edge["events"], "unauthorized_edge.events"):
        event = mapping(raw_event, "unauthorized_edge.event")
        if event.get("actor") == "inquisitor" and event.get("action") == "assignment_created":
            event["target_role"] = "sage"
            event["model"] = "gpt-5.6-luna"
            break
    _must_reject(module, manifest, unauthorized_edge, "inquisitor->sage nested edge")

    missing_gate = module.build_contract_trace(manifest, "mapmaking", "high")
    missing_gate["events"] = [
        event
        for event in sequence(missing_gate["events"], "missing_gate.events")
        if mapping(event, "missing_gate.event").get("action") != "report_evidence_gate"
    ]
    _renumber(missing_gate)
    _must_reject(module, manifest, missing_gate, "Root report gate欠落")

    assignment_before_gate = module.build_contract_trace(manifest, "party", "high")
    assignment_before_gate_events = sequence(assignment_before_gate["events"], "assignment_before_gate.events")
    first_gate_index = next(
        index
        for index, event in enumerate(assignment_before_gate_events)
        if mapping(event, "assignment_before_gate.event").get("action") == "report_evidence_gate"
    )
    next_assignment_index = next(
        index
        for index, event in enumerate(assignment_before_gate_events)
        if index > first_gate_index and mapping(event, "assignment_before_gate.event").get("action") == "assignment_created"
    )
    assignment_before_gate_events[first_gate_index], assignment_before_gate_events[next_assignment_index] = (
        assignment_before_gate_events[next_assignment_index],
        assignment_before_gate_events[first_gate_index],
    )
    _renumber(assignment_before_gate)
    _must_reject(module, manifest, assignment_before_gate, "未gate report後の次assignment")

    reversed_role_phases = module.build_contract_trace(manifest, "guild", "xhigh")
    reversed_role_events = sequence(reversed_role_phases["events"], "reversed_role_phases.events")
    preamble = reversed_role_events[:3]
    synthesis_and_terminal = reversed_role_events[-2:]
    body = reversed_role_events[3:-2]
    split_index = next(
        index
        for index, event in enumerate(body)
        if mapping(event, "reversed_role_phases.event").get("target_role") == "captain"
    )
    reversed_role_phases["events"] = preamble + body[split_index:] + body[:split_index] + synthesis_and_terminal
    _renumber(reversed_role_phases)
    _must_reject(module, manifest, reversed_role_phases, "cross-role phase逆順")

    early_synthesis = module.build_contract_trace(manifest, "solo", "high")
    early_synthesis_events = sequence(early_synthesis["events"], "early_synthesis.events")
    synthesis_index = next(
        index
        for index, event in enumerate(early_synthesis_events)
        if mapping(event, "early_synthesis.event").get("action") == "report_synthesis"
    )
    synthesis_event = early_synthesis_events.pop(synthesis_index)
    early_synthesis_events.insert(3, synthesis_event)
    _renumber(early_synthesis)
    _must_reject(module, manifest, early_synthesis, "worker report前のRoot synthesis")

    missing_preamble = module.build_contract_trace(manifest, "solo", "high")
    missing_preamble["events"] = [
        event
        for event in sequence(missing_preamble["events"], "missing_preamble.events")
        if mapping(event, "missing_preamble.event").get("action")
        not in {"target_repo_binding", "authority_check", "snapshot_request"}
    ]
    _renumber(missing_preamble)
    _must_reject(module, manifest, missing_preamble, "Root preamble欠落")

    missing_wait = module.build_contract_trace(manifest, "solo", "xhigh")
    missing_wait["events"] = [
        event
        for event in sequence(missing_wait["events"], "missing_wait.events")
        if mapping(event, "missing_wait.event").get("action") != "agent_wait"
    ]
    _renumber(missing_wait)
    _must_reject(module, manifest, missing_wait, "Root agent_wait欠落")

    premature_trial = module.build_contract_trace(manifest, "focused_trial", "high")
    premature_events = sequence(premature_trial["events"], "premature_trial.events")
    trial_index = next(
        index
        for index, event in enumerate(premature_events)
        if mapping(event, "premature_trial.event").get("action") == "trial_decision"
    )
    trial_event = premature_events.pop(trial_index)
    child_index = next(
        index
        for index, event in enumerate(premature_events)
        if mapping(event, "premature_trial.event").get("actor") == "inquisitor"
        and mapping(event, "premature_trial.event").get("action") == "assignment_created"
    )
    premature_events.insert(child_index, trial_event)
    _renumber(premature_trial)
    _must_reject(module, manifest, premature_trial, "Examiner report gate前のtrial decision")

    reversed_work = module.build_contract_trace(manifest, "solo", "ultra")
    reversed_events = sequence(reversed_work["events"], "reversed_work.events")
    implementation_index = next(
        index
        for index, event in enumerate(reversed_events)
        if mapping(event, "reversed_work.event").get("action") == "implementation"
    )
    validation_index = next(
        index
        for index, event in enumerate(reversed_events)
        if mapping(event, "reversed_work.event").get("action") == "validation_execution"
    )
    reversed_events[implementation_index], reversed_events[validation_index] = (
        reversed_events[validation_index],
        reversed_events[implementation_index],
    )
    _renumber(reversed_work)
    _must_reject(module, manifest, reversed_work, "required role actionの逆順")

    unavailable_fallback = module.build_contract_trace(manifest, "worker_unavailable", "ultra")
    unavailable_events = sequence(unavailable_fallback["events"], "unavailable_fallback.events")
    terminal = unavailable_events.pop()
    unavailable_events.append(
        {
            "seq": 0,
            "actor": "root",
            "depth": 0,
            "action": "repository_exploration",
            "assignment_id": "unavailable_cartographer",
        }
    )
    unavailable_events.append(terminal)
    _renumber(unavailable_fallback)
    _must_reject(module, manifest, unavailable_fallback, "worker不在時のRoot直接fallback")

    synthetic_as_live = module.build_contract_trace(manifest, "sage_focus", "high")
    synthetic_as_live["source"] = "live_recorded"
    _must_reject(module, manifest, synthetic_as_live, "source差替えによるlive provenance偽装", require_live=True)

    evidence = mapping(manifest.get("live_evidence_status"), "root_orchestration.live_evidence_status")
    require(evidence.get("live_execution_completed") is False and evidence.get("empirical_support_claim_allowed") is False, "live未実施時はempirical supportを主張しないでください。")
    live_policy = mapping(manifest.get("live_evidence_policy"), "root_orchestration.live_evidence_policy")
    require(
        sequence(live_policy.get("approved_isolation_wrapper_sha256"), "root_orchestration.approved wrappers") == []
        and sequence(live_policy.get("approved_isolation_profile_sha256"), "root_orchestration.approved profiles") == [],
        "review済みlive実行基盤がない間はRoot orchestration allowlistを空にしてください。",
    )

    with tempfile.TemporaryDirectory() as tmp:
        session_dir = Path(tmp)
        session_manifest = copy.deepcopy(manifest)
        session_policy = mapping(session_manifest["live_evidence_policy"], "session_manifest.live_evidence_policy")
        collector_id = "synthetic-integrity-validator-self-test"
        wrapper_path = session_dir / str(session_policy["wrapper_artifact_filename"])
        profile_path = session_dir / str(session_policy["isolation_profile_filename"])
        wrapper_path.write_bytes(b"reviewed-wrapper-integrity-fixture\n")
        wrapper_hash = hashlib.sha256(wrapper_path.read_bytes()).hexdigest()
        profile_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "collector_id": collector_id,
                    "wrapper_sha256": wrapper_hash,
                    "filesystem_scope": "isolated_eval_workdir_only",
                    "network_scope": "openai_model_service_only",
                    "execution_authenticity": "operator_attested_not_cryptographically_proven",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        session_policy["approved_isolation_wrapper_sha256"] = [wrapper_hash]
        session_policy["approved_isolation_profile_sha256"] = [profile_hash]
        module.validate_manifest(session_manifest)
        trace_digests: dict[str, str] = {}
        for mode in ("high", "xhigh", "ultra"):
            for case_id in sorted(cases):
                trace = module.build_contract_trace(session_manifest, case_id, mode)
                trace["source"] = "live_recorded"
                trace["trace_id"] = f"live_fixture_{mode}_{case_id}"
                filename = f"{mode}__{case_id}.json"
                path = session_dir / filename
                path.write_text(json.dumps(trace, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                trace_digests[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        contract_sha256 = hashlib.sha256(
            json.dumps(session_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "source": "live_recorded",
                    "external_data_acknowledged": True,
                    "collector_id": collector_id,
                    "wrapper_sha256": wrapper_hash,
                    "isolation_profile_sha256": profile_hash,
                    "contract_sha256": contract_sha256,
                    "provenance_guarantee": session_policy["provenance_guarantee"],
                    "traces": trace_digests,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        module.validate_session(session_manifest, session_dir)
        outside_artifacts = session_dir.parent / "outside-artifacts"
        outside_artifacts.mkdir()
        artifact_filenames = [
            "session.json",
            session_policy["wrapper_artifact_filename"],
            session_policy["isolation_profile_filename"],
            *sorted(trace_digests),
        ]
        for filename in artifact_filenames:
            (outside_artifacts / filename).write_bytes((session_dir / filename).read_bytes())

        def require_safety_rejection(label: str, required_error: str) -> None:
            try:
                module.validate_session(session_manifest, session_dir)
            except module.OrchestrationEvalError as exc:
                require(required_error in str(exc), f"{label}をsafety-specific errorで拒否してください: {exc}")
            else:
                require(False, f"Root orchestration session validatorは{label}を内容読取前に拒否してください。")

        def require_symlink_rejection(filename: str, label: str) -> None:
            path = session_dir / filename
            contents = path.read_bytes()
            path.unlink()
            path.symlink_to(outside_artifacts / filename)
            require_safety_rejection(label, "symlinkまたはopen errorを拒否しました")
            path.unlink()
            path.write_bytes(contents)

        def require_special_file_rejection(filename: str, label: str) -> None:
            path = session_dir / filename
            contents = path.read_bytes()
            path.unlink()
            os.mkfifo(path)
            require_safety_rejection(label, "regular fileにしてください")
            path.unlink()
            path.write_bytes(contents)

        require_symlink_rejection("session.json", "session manifest symlink")
        require_special_file_rejection("session.json", "session manifest special file")
        require_symlink_rejection("high__solo.json", "trace symlink")
        require_special_file_rejection("high__solo.json", "trace special file")
        require_symlink_rejection(session_policy["wrapper_artifact_filename"], "wrapper symlink")
        require_special_file_rejection(session_policy["wrapper_artifact_filename"], "wrapper special file")
        require_symlink_rejection(session_policy["isolation_profile_filename"], "isolation profile symlink")
        require_special_file_rejection(session_policy["isolation_profile_filename"], "isolation profile special file")

        session_directory_fd = module._open_session_directory(session_dir)
        try:
            try:
                module._read_session_artifact(session_directory_fd, "../outside-artifacts/session.json", "trace")
            except module.OrchestrationEvalError as exc:
                require("filenameが不正です" in str(exc), "path escapeをsafety-specific errorで拒否してください。")
            else:
                require(False, "Root orchestration session validatorはsession artifact path escapeを拒否してください。")
        finally:
            os.close(session_directory_fd)
        tampered = session_dir / "high__solo.json"
        original_trace = tampered.read_text(encoding="utf-8")
        tampered.write_text(original_trace + " ", encoding="utf-8")
        try:
            module.validate_session(session_manifest, session_dir)
        except module.OrchestrationEvalError:
            pass
        else:
            require(False, "Root orchestration session validatorはtampered trace digestを拒否してください。")
        tampered.write_text(original_trace, encoding="utf-8")
        wrapper_path.write_bytes(wrapper_path.read_bytes() + b"tampered")
        try:
            module.validate_session(session_manifest, session_dir)
        except module.OrchestrationEvalError:
            pass
        else:
            require(False, "Root orchestration session validatorはtampered wrapper artifactを拒否してください。")
