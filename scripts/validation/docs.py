"""Prompt surface、agent config、docs、skills、hook の契約検証。"""

from __future__ import annotations

import contextlib
import io
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

from .core import ROOT, load_yaml, mapping, read, require, sequence, tomllib, yaml


EXPECTED_AGENT_SANDBOX_MODES = {
    "adventurer": "workspace-write",
    "sage": "read-only",
    "cartographer": "read-only",
    "courier": "workspace-write",
    "examiner": "read-only",
    "guildmaster": "read-only",
    "inquisitor": "read-only",
    "artificer": "workspace-write",
    "captain": "read-only",
    "warden": "read-only",
}

EXPECTED_AGENT_MODEL_CONFIGS = {
    "adventurer": ("gpt-5.6-terra", "high"),
    "sage": ("gpt-5.6-luna", "xhigh"),
    "cartographer": ("gpt-5.6-sol", "high"),
    "courier": ("gpt-5.3-codex-spark", "xhigh"),
    "examiner": ("gpt-5.6-terra", "high"),
    "guildmaster": ("gpt-5.6-sol", "xhigh"),
    "inquisitor": ("gpt-5.6-sol", "xhigh"),
    "artificer": ("gpt-5.6-sol", "high"),
    "captain": ("gpt-5.6-sol", "high"),
    "warden": ("gpt-5.6-sol", "high"),
}

RUNTIME_PROSE = (
    "template/AGENTS.md",
    "template/.agents/orchestra/instructions/common.md",
    "template/.agents/orchestra/README.md",
    "README.md",
    "docs/orchestration-runtime.md",
    "docs/guild-quest-lifecycle.md",
    "docs/quest-awareness-runtime.md",
    "docs/customization.md",
    "docs/prompt-recipes.md",
    "docs/agent-deployment.md",
)

QUALITY_REDUCING_TOKENS = (
    "always_guild_intake",
    "confidence_percent",
    "confidence_target_percent",
    "confidence_delta_min_percent",
    "confidence が 75%",
    "confidence が 50%",
    "cost reason",
    "cost_reason_required_always",
    "extra_file_reads",
    "validation_iterations",
)

VSCODE_SKILL_FRONTMATTER_FIELDS = {
    "argument-hint",
    "compatibility",
    "context",
    "description",
    "disable-model-invocation",
    "license",
    "metadata",
    "name",
    "user-invocable",
}

DISPLAY_NAME_TOKEN_FORMS = {
    "from": ("from",),
    "github": ("GitHub",),
    "in": ("in",),
    "vscode": ("VS", "Code"),
}


def _line_count(rel: str) -> int:
    return len(read(rel).splitlines())


def _format_skill_display_name(name: str) -> str:
    words: list[str] = []
    read_only = False
    for token in name.split("-"):
        if token == "readonly":
            read_only = True
            continue
        words.extend(DISPLAY_NAME_TOKEN_FORMS.get(token, (token.capitalize(),)))
    display_name = " ".join(words)
    if read_only:
        display_name += " (" + "Read-Only" + ")"
    if name == "communicate-work-estimates":
        display_name += " (" + "Root-Only" + ")"
    return display_name


def _validate_root_only_work_estimates(description: str, interface: dict[str, object]) -> None:
    require("Root専用" in description, "communicate-work-estimates のfrontmatter descriptionはRoot専用を明記してください。")
    require(
        re.search(r"subagent.{0,40}(?:使わず|使用しない|利用しない)", description) is not None,
        "communicate-work-estimates のfrontmatter descriptionはsubagentが利用しないことを明記してください。",
    )
    for field in ("display_name", "short_description", "default_prompt"):
        value = interface.get(field)
        require(isinstance(value, str) and "Root" in value, f"communicate-work-estimates のUI metadata {field} はRootを見積もり主体として明記してください。")


def _validated_model_policy() -> tuple[str, dict[str, tuple[str, str]]]:
    settings = mapping(load_yaml("template/.agents/orchestra/config/settings.yaml"), "settings.yaml")
    policy = mapping(settings.get("model_policy"), "settings.model_policy")
    require(
        set(policy)
        == {
            "root_model",
            "root_project_local_reasoning_effort",
            "root_user_selectable_reasoning_efforts",
            "fixed_pair_per_subagent",
            "subagent_pairs",
        },
        "settings.model_policy は定義済みのRoot方針とsubagent pairだけを含めてください。",
    )
    root_model = policy.get("root_model")
    require(root_model == "gpt-5.6-sol", "settings.model_policy.root_model は gpt-5.6-sol にしてください。")
    require(
        policy.get("root_project_local_reasoning_effort") == "unset",
        "settings.model_policy はRoot reasoning effortをproject-localで固定しないでください。",
    )
    require(
        sequence(policy.get("root_user_selectable_reasoning_efforts"), "settings.model_policy.root_user_selectable_reasoning_efforts")
        == ["high", "xhigh", "ultra"],
        "settings.model_policy のRoot選択肢は high / xhigh / ultra にしてください。",
    )
    require(policy.get("fixed_pair_per_subagent") is True, "settings.model_policy はsubagentごとの固定pairを要求してください。")

    pairs = mapping(policy.get("subagent_pairs"), "settings.model_policy.subagent_pairs")
    require(set(pairs) == set(EXPECTED_AGENT_MODEL_CONFIGS), "settings.model_policy.subagent_pairs は定義済みの10 roleだけを含めてください。")
    normalized: dict[str, tuple[str, str]] = {}
    for role, expected_pair in EXPECTED_AGENT_MODEL_CONFIGS.items():
        pair = mapping(pairs.get(role), f"settings.model_policy.subagent_pairs.{role}")
        require(set(pair) == {"model", "model_reasoning_effort"}, f"settings.model_policy.subagent_pairs.{role} はmodel/effortの固定pairにしてください。")
        actual_pair = (pair.get("model"), pair.get("model_reasoning_effort"))
        require(actual_pair == expected_pair, f"settings.model_policy.subagent_pairs.{role} は {expected_pair[0]} / {expected_pair[1]} にしてください。")
        normalized[role] = expected_pair
    return str(root_model), normalized


def validate_audit_english_path_guard() -> None:
    spec = importlib.util.spec_from_file_location("audit_english_guard", ROOT / "scripts/audit_english.py")
    require(spec is not None and spec.loader is not None, "scripts/audit_english.py をimportできません。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for rel in ("docs/credentials.md", "docs/secrets.md", "docs/tokens.md", "docs/api_key.txt", "docs/auth.json"):
        try:
            module.ensure_not_sensitive_path(ROOT / rel)
        except SystemExit:
            continue
        require(False, f"audit_english.py はsecret-like pathを読む前に拒否してください: {rel}")

    for rel in ("docs/keyboard-shortcuts.md", "docs/authoring-guide.md"):
        try:
            module.ensure_not_sensitive_path(ROOT / rel)
        except SystemExit as exc:
            require(False, f"audit_english.py が無害なpathを拒否しました: {rel}: {exc}")

    original_root = module.ROOT
    with tempfile.TemporaryDirectory() as tmp:
        fake_root = Path(tmp) / "repo"
        fake_root.mkdir()
        external = Path(tmp) / "external.txt"
        external.write_text("external\n", encoding="utf-8")
        link = fake_root / "docs" / "keyboard-shortcuts.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(external)
        module.ROOT = fake_root
        try:
            try:
                module.ensure_not_sensitive_path(link)
            except SystemExit:
                pass
            else:
                require(False, "audit_english.py はsymlinkを読む前に拒否してください。")
        finally:
            module.ROOT = original_root


def validate_agents() -> None:
    require(tomllib is not None, "TOML検証にはtomllib/tomliが必要です。")
    expected_root_model, expected_agent_model_configs = _validated_model_policy()
    agent_paths = sorted((ROOT / "template/.codex/agents").glob("*.toml"))
    actual = {path.stem for path in agent_paths}
    require(actual == set(EXPECTED_AGENT_SANDBOX_MODES), "custom agent一覧が期待値と一致しません: " + ", ".join(sorted(actual)))

    for path in agent_paths:
        rel = str(path.relative_to(ROOT))
        raw = path.read_text(encoding="utf-8")
        data = tomllib.loads(raw)
        role = path.stem
        for key in ("name", "description", "model", "model_reasoning_effort", "sandbox_mode", "developer_instructions"):
            require(key in data, f"{rel} に {key} が必要です。")
        require(data["name"] == role, f"{rel} のnameはfilenameと一致させてください。")
        require(data["sandbox_mode"] == EXPECTED_AGENT_SANDBOX_MODES[role], f"{rel} のsandbox_modeが不正です。")
        require((data["model"], data["model_reasoning_effort"]) == expected_agent_model_configs[role], f"{rel} のmodel/effortが不正です。")
        features = mapping(data.get("features"), f"{rel}.features")
        expected_multi_agent = role == "inquisitor"
        require(features.get("multi_agent") is expected_multi_agent, f"{rel} のmulti_agentはinquisitorだけtrueにしてください。")
        developer = str(data["developer_instructions"])
        require(len(developer.splitlines()) <= 18, f"{rel} のdeveloper_instructionsをrole固有の18行以内にしてください。")
        require("instructions/common.md" not in developer and "config/settings.yaml" not in developer, f"{rel} はcommon/settingsを常時再読込しないでください。")
        for token in QUALITY_REDUCING_TOKENS:
            require(token not in developer, f"{rel} に旧制約 `{token}` が残っています。")

    adventurer_developer = str(tomllib.loads(read("template/.codex/agents/adventurer.toml"))["developer_instructions"])
    for token in ("人間が許可", "assignment", "exact `.orchestra/skill-candidates/<repo>/<candidate>/`", "新規materialize"):
        require(token in adventurer_developer, f"adventurer roleのcandidate materialize契約に `{token}` が必要です。")
    agents_contract = read("template/AGENTS.md")
    require("exact `<guild_root>/.orchestra/skill-candidates/<repo>/<candidate>/`" in agents_contract and "`adventurer`が新規materialize" in agents_contract, "AGENTS.mdのcandidate materialize owner/path契約が不足しています。")
    candidate_skill = read("template/.agents/skills/create-skill-candidate-from-gap/SKILL.md")
    require("materialize owner" in candidate_skill and "`adventurer` owner" in candidate_skill and "exact candidate path" in candidate_skill, "candidate Skillのowner/path契約が不足しています。")
    settings = mapping(load_yaml("template/.agents/orchestra/config/settings.yaml"), "settings.yaml")
    candidate_materialization = mapping(mapping(mapping(settings.get("guild_law"), "settings.guild_law").get("state_changes"), "settings.guild_law.state_changes").get("candidate_materialization"), "settings.guild_law.state_changes.candidate_materialization")
    require(candidate_materialization.get("owner") == "adventurer" and candidate_materialization.get("human_authorized_exact_path_required") is True and candidate_materialization.get("root_write_forbidden") is True, "settingsのcandidate materialize owner/path/Root境界が不足しています。")

    config = tomllib.loads(read("template/.codex/config.toml"))
    require(config.get("model") == expected_root_model, "Root templateのmodelはSolにしてください。")
    require("model_reasoning_effort" not in config, "Root templateでreasoning effortを固定しないでください。")
    require(config.get("sandbox_mode") == "read-only", "Root sandboxはread-onlyにしてください。")
    require(config.get("approval_policy") == "on-request" and config.get("approvals_reviewer") == "auto_review", "Root approval contractが不正です。")
    require(config.get("web_search") == "cached" and config.get("allow_login_shell") is False, "Root web/shell contractが不正です。")
    require(mapping(config.get("sandbox_workspace_write"), "config.sandbox_workspace_write").get("network_access") is True, "workspace-write networkは有効にしてください。")
    agents = mapping(config.get("agents"), "config.agents")
    require(agents.get("max_threads") == 64 and agents.get("max_depth") == 2, "agent concurrencyはmax_threads=64/max_depth=2にしてください。")
    require(agents.get("job_max_runtime_seconds") == 2400, "agent job runtimeは2400秒にしてください。")


def _validate_parallelism_doc(rel: str, text: str) -> None:
    allocation_contracts = {
        "docs/agent-deployment.md": (
            "非adventurer合計16の計48",
            "global 64との差16",
        ),
        "docs/orchestration-runtime.md": (
            "role別上限の合計は48",
            "うち非adventurerは16",
            "globalとの差16",
        ),
    }
    require(rel in allocation_contracts, f"{rel} のrole配分検証契約が未定義です。")
    required = (
        "adventurer.max_parallel=32",
        "max_threads=64",
        "未割当headroom",
        *allocation_contracts[rel],
    )
    require(all(token in text for token in required), f"{rel} にtotal 48 / non-adventurer 16 / 未割当headroom 16の契約が必要です。")
    require(
        all(old not in text for old in ("adventurer.max_parallel=48", "adventurer.max_parallel=64", "非adventurerの16枠を予約", "追加スロット制限を設けません")),
        f"{rel} に旧adventurer占有・残余割当契約を残さないでください。",
    )


def _markdown_h2_section(rel: str, text: str, heading: str) -> str:
    marker = f"## {heading}\n"
    require(text.count(marker) == 1, f"{rel} にH2 section `{heading}` を1つ定義してください。")
    start = text.index(marker)
    end = text.find("\n## ", start + len(marker))
    return text[start:] if end == -1 else text[start:end]


def _validate_current_deployment_docs() -> None:
    readme = _markdown_h2_section("README.md", read("README.md"), "仕組み")
    for token in ("`sage`はLuna/xhigh", "`inquisitor`はSol/xhigh"):
        require(token in readme, f"README.md の現行deployment説明に `{token}` が必要です。")

    changelog = _markdown_h2_section("CHANGELOG.md", read("CHANGELOG.md"), "[Unreleased]")
    for token in (
        "`sage`をLuna/xhigh",
        "`inquisitor`をSol/xhigh",
        "`job_max_runtime_seconds`を1800秒から2400秒へ延長",
    ):
        require(token in changelog, f"CHANGELOG.md のUnreleased契約に `{token}` が必要です。")

    deployment_text = read("docs/agent-deployment.md")
    configuration = _markdown_h2_section("docs/agent-deployment.md", deployment_text, "Configuration")
    require("job_max_runtime_seconds = 2400" in configuration, "agent-deployment.md の現行job runtimeは2400秒にしてください。")
    deployment_pairs = _markdown_h2_section("docs/agent-deployment.md", deployment_text, "Deployment role pairs")
    for row in (
        "| `sage` | `gpt-5.6-luna` | `read-only` | `xhigh` |",
        "| `inquisitor` | `gpt-5.6-sol` | `read-only` | `xhigh` |",
    ):
        require(row in deployment_pairs, f"agent-deployment.md の現行role pair表に `{row}` が必要です。")
    for token in ("`sage`はLuna/xhigh", "`inquisitor`はSol/xhigh"):
        require(token in deployment_pairs, f"agent-deployment.md の現行pair説明に `{token}` が必要です。")

    selection_text = read("docs/model-selection-evaluation.md")
    fixed_matrix = _markdown_h2_section("docs/model-selection-evaluation.md", selection_text, "固定マトリクス")
    for row in (
        "| `sage` | `gpt-5.6-luna` | `xhigh` |",
        "| `inquisitor` | `gpt-5.6-sol` | `xhigh` |",
        "| `inquisitor` | Sol `xhigh` / Sol `high` |",
        "| `sage` | Luna `xhigh` / Terra `xhigh` / Sol `xhigh`",
        "別dimensionの診断用effort challengerはLuna `high`",
    ):
        require(row in fixed_matrix, f"model-selection-evaluation.md の現行固定マトリクスに `{row}` が必要です。")
    for token in ("`sage`のみxhighでLuna/Terra/Solを比べます", "`inquisitor`はSol/xhighをdeploymentへ固定"):
        require(token in selection_text, f"model-selection-evaluation.md の現行evaluation説明に `{token}` が必要です。")


def validate_docs_and_instructions() -> None:
    require(_line_count("template/AGENTS.md") <= 125, "AGENTS.mdを125行以内のcompact kernelにしてください。")
    require(_line_count("template/.agents/orchestra/instructions/common.md") <= 55, "common.mdを55行以内にしてください。")
    require(_line_count("template/.agents/orchestra/config/settings.yaml") <= 240, "settings.yamlを240行以内の機械契約にしてください。")

    agents = read("template/AGENTS.md")
    for token in ("target_repo_root", "success_criteria", "State changes", "Evidence-based control", "Rootだけ", "artificer", "stale_evidence", "結論を先"):
        require(token in agents, f"AGENTS.md のcompact kernelに `{token}` が必要です。")
    for token in QUALITY_REDUCING_TOKENS:
        require(token not in agents, f"AGENTS.md に旧制約 `{token}` が残っています。")
    for token in (
        "対象repoを読まない回答・説明",
        "Rootはcoordinationとjudgeに専念",
        "browser-control toolだけは担当roleの仕様を実行して観測事実を記録するRoot例外",
        "Rootだけがtop-level custom agent",
        "`inquisitor`だけがdepth 2の`examiner`",
        "`high`、`xhigh`、`ultra`",
        "Rootは返されたevidenceをsuccess criteriaとsnapshotへ照合して次actionを決めます",
        "exact `<guild_root>/.orchestra/skill-candidates/<repo>/<candidate>/`",
        "`adventurer`が新規materialize",
        "Rootは書き込まずcoordination-only",
    ):
        require(token in agents, f"AGENTS.md にRoot coordination-only契約 `{token}` が必要です。")
    for stale_root_contract in (
        "説明とread-only調査は不要なQuestを作らずRootが直接進めます",
        "read-only fast pathはRootが継続できる",
        "Rootが変更に直接対応する検証を実行",
        "Rootが`examiner`を直接起動",
    ):
        require(stale_root_contract not in agents, f"AGENTS.md に旧Root直接作業契約 `{stale_root_contract}` を戻さないでください。")

    common = read("template/.agents/orchestra/instructions/common.md")
    require("custom agentの起動時promptへ重ねて読み込みません" in common, "common.mdは常時promptではないことを明記してください。")
    require("数値confidence" in common and "要求しません" in common, "common.mdは数値confidenceを要求しないでください。")
    for token in (
        "担当roleの完了を待ってevidenceをgate",
        "browser-control toolは例外としてRootだけが",
        "Rootへreportを返します",
    ):
        require(token in common, f"common.md にRoot/worker report loop `{token}` が必要です。")

    for rel in (
        "template/.agents/skills/browser-research-readonly/SKILL.md",
        "template/.agents/skills/implementation-behavior-verification/SKILL.md",
    ):
        text = read(rel)
        require("browser-control toolを呼ばない" in text, f"{rel} はsubagentのbrowser-control tool禁止を明記してください。")
        require("objective・URL・authority・許可操作" in text, f"{rel} はRoot browser handoffを仕様化してください。")
        require("Root" in text and "観測事実" in text, f"{rel} はRootの観測事実記録を明記してください。")

    deployment = read("docs/agent-deployment.md")
    runtime = read("docs/orchestration-runtime.md")
    for rel, text in (("docs/agent-deployment.md", deployment), ("docs/orchestration-runtime.md", runtime)):
        _validate_parallelism_doc(rel, text)
    _validate_current_deployment_docs()

    role_paths = sorted((ROOT / "template/.agents/orchestra/instructions").glob("*.md"))
    for path in role_paths:
        rel = str(path.relative_to(ROOT))
        require(len(path.read_text(encoding="utf-8").splitlines()) <= 65, f"{rel} を65行以内のrole referenceにしてください。")

    for rel in RUNTIME_PROSE:
        text = read(rel)
        for token in QUALITY_REDUCING_TOKENS:
            require(token not in text, f"{rel} に旧制約 `{token}` が残っています。")

    use_case_root = ROOT / "docs/use-cases"
    use_case_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(use_case_root.glob("*.md")))
    for token in QUALITY_REDUCING_TOKENS:
        require(token not in use_case_text, f"docs/use-cases に旧制約 `{token}` が残っています。")
    for token in ("evidence_state", "artificer", "risk-triggered", "例外"):
        require(token in use_case_text, f"docs/use-cases は新設計 `{token}` を説明してください。")

    quest_control = read("docs/quest-awareness-runtime.md")
    for token in ("evidence_state", "blocking_unknowns", "failed_checks", "scope_drift", "high_risk_triggers", "数値confidenceを使わ"):
        require(token in quest_control, f"quest-awareness-runtime.md に `{token}` が必要です。")

    validate_audit_english_path_guard()


def validate_skills() -> None:
    skills_root = ROOT / "template/.agents/skills"
    require(skills_root.exists(), "template/.agents/skills が必要です。")
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        rel = str(skill_md.relative_to(ROOT))
        text = skill_md.read_text(encoding="utf-8")
        require(text.startswith("---\n"), f"{rel} はfrontmatterで始めてください。")
        end_marker = text.find("\n---\n", 4)
        require(end_marker != -1, f"{rel} のfrontmatterを `---` で閉じてください。")
        require(yaml is not None, "Skill frontmatter検証にはPyYAMLが必要です。")
        try:
            document = yaml.safe_load(text[4:end_marker])  # type: ignore[union-attr]
        except yaml.YAMLError as exc:  # type: ignore[union-attr]
            require(False, f"{rel} のfrontmatterをYAMLとして読めません: {exc}")
        frontmatter = mapping(document, f"{rel} frontmatter")
        require(all(isinstance(key, str) for key in frontmatter), f"{rel} のfrontmatter keyは文字列にしてください。")
        unexpected_fields = sorted(set(frontmatter) - VSCODE_SKILL_FRONTMATTER_FIELDS)
        require(
            not unexpected_fields,
            f"{rel} にVS Code Agent Skills schema非対応のfrontmatter fieldがあります: " + ", ".join(unexpected_fields),
        )

        name = frontmatter.get("name")
        description = frontmatter.get("description")
        require(isinstance(name, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is not None, f"{rel}.name はhyphen-caseにしてください。")
        require(len(name) <= 64 and name == skill_md.parent.name, f"{rel}.name は64文字以内で親directory名と一致させてください。")
        require(isinstance(description, str) and 1 <= len(description) <= 1024, f"{rel}.description は1〜1024文字にしてください。")

        skill_metadata = mapping(frontmatter.get("metadata"), f"{rel}.metadata")
        require(
            all(isinstance(key, str) and isinstance(value, str) for key, value in skill_metadata.items()),
            f"{rel}.metadata は文字列key/valueのmappingにしてください。",
        )
        require(skill_metadata.get("owner") == "agent-guild-orchestra", f"{rel}.metadata.owner は agent-guild-orchestra にしてください。")
        require(isinstance(skill_metadata.get("scope"), str) and bool(skill_metadata["scope"]), f"{rel}.metadata.scope を空でない文字列にしてください。")

        for key in ("argument-hint", "license"):
            if key in frontmatter:
                require(isinstance(frontmatter[key], str) and bool(frontmatter[key]), f"{rel}.{key} は空でない文字列にしてください。")
        if "compatibility" in frontmatter:
            compatibility = frontmatter["compatibility"]
            require(isinstance(compatibility, str) and 1 <= len(compatibility) <= 500, f"{rel}.compatibility は1〜500文字にしてください。")
        if "context" in frontmatter:
            require(frontmatter["context"] == "fork", f"{rel}.context はVS Codeが対応する `fork` にしてください。")
        for key in ("disable-model-invocation", "user-invocable"):
            if key in frontmatter:
                require(isinstance(frontmatter[key], bool), f"{rel}.{key} はboolにしてください。")

        openai_yaml = skill_md.parent / "agents/openai.yaml"
        require(openai_yaml.is_file(), f"{rel} には agents/openai.yaml が必要です。")
        openai_rel = str(openai_yaml.relative_to(ROOT))
        openai_document = mapping(load_yaml(openai_rel), openai_rel)
        interface = mapping(openai_document.get("interface"), f"{openai_rel}.interface")
        for key in ("display_name", "short_description", "default_prompt"):
            require(
                isinstance(interface.get(key), str) and bool(interface[key].strip()),
                f"{openai_rel}.interface.{key} は空でない文字列にしてください。",
            )
        expected_display_name = _format_skill_display_name(name)
        require(
            interface["display_name"] == expected_display_name,
            f"{openai_rel}.interface.display_name はskill directory名から導出した正規表示名と完全一致させてください。",
        )
        short_description = interface["short_description"]
        require(25 <= len(short_description) <= 64, f"{openai_rel}.interface.short_description は25〜64文字にしてください。")
        require(
            f"${name}" in interface["default_prompt"],
            f"{openai_rel}.interface.default_prompt は `${name}` を明示してください。",
        )
        if name == "communicate-work-estimates":
            _validate_root_only_work_estimates(description, interface)
        for section in ("## 使う時", "## 入力", "## 手順", "## 出力", "## 安全", "## 停止条件"):
            require(section in text, f"{rel} に {section} が必要です。")
        require("未信頼" in text or "外部入力" in text or "秘密" in text, f"{rel} は安全境界を明記してください。")
        for token in QUALITY_REDUCING_TOKENS:
            require(token not in text, f"{rel} に旧制約 `{token}` が残っています。")

    control_skill = read("template/.agents/skills/quest-awareness-loop/SKILL.md")
    for token in ("evidence_state", "blocking_unknowns", "failed_checks", "scope drift", "contradictory evidence"):
        require(token in control_skill, f"quest-awareness-loop skillに `{token}` が必要です。")
    guild_skill = read("template/.agents/skills/use-guild-workflow/SKILL.md")
    require("risk-adaptive" in guild_skill and "fast path" in guild_skill, "use-guild-workflowはrisk-adaptive fast pathを説明してください。")
    estimate_skill = read("template/.agents/skills/communicate-work-estimates/SKILL.md")
    for token in (
        "開始時",
        "subagentへ委任",
        "subagentはこのSkillを使わず、見積もり生成・通知・更新をしません",
        "critical path",
        "残り時間",
        "増加だけでなく",
    ):
        require(token in estimate_skill, f"communicate-work-estimates skillに `{token}` が必要です。")
    _validate_skill_candidate_helper()
    _validate_vscode_launch_skill()


def _load_skill_candidate_helper() -> object:
    helper = ROOT / "template/.agents/skills/create-skill-candidate-from-gap/scripts/validate_skill_candidate.py"
    spec = importlib.util.spec_from_file_location("validate_skill_candidate", helper)
    require(spec is not None and spec.loader is not None, "Skill candidate validator を読み込めません。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_skill_candidate_helper() -> None:
    """隔離candidateの正負contractを一時directoryだけで検証する。"""
    module = _load_skill_candidate_helper()
    template_candidate_root = ROOT / "template/.agents/orchestra/skill-candidates/README.md"
    require(template_candidate_root.is_file(), "template は candidate root marker を含めてください。")

    def make_candidate(guild: Path, target: Path, name: str = "example-candidate") -> Path:
        candidate = guild / ".orchestra/skill-candidates" / target.name / name
        candidate.mkdir()
        (candidate / "agents").mkdir()
        (candidate / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: \"隔離 candidate の検証用 Skill です。\"\n"
            "metadata:\n"
            "  owner: \"human-review-required\"\n"
            "  scope: \"skill-candidate\"\n"
            "  lifecycle: \"needs_human\"\n"
            "  candidate_only_authority: \"candidate-only\"\n"
            "  external_actions: \"denied\"\n"
            "  sensitive_data: \"denied\"\n"
            "  local_git: \"denied\"\n"
            "---\n\n"
            "# candidate\n\n"
            "## 使う時\n\n"
            "- validate\n\n"
            "## 入力\n\n"
            "- sanitized input\n\n"
            "## 手順\n\n"
            "1. validate\n\n"
            "## 出力\n\n"
            "- result\n\n"
            "## 安全\n\n"
            "- candidate-only; external actions denied; sensitive data denied; local Git denied\n\n"
            "## Promotion gate\n\n"
            "- needs_human; independent Trial; structural validation は置き換えない\n\n"
            "## 停止条件\n\n"
            "- complete\n",
            encoding="utf-8",
        )
        (candidate / "agents/openai.yaml").write_text(
            "interface:\n"
            "  display_name: \"Example Candidate\"\n"
            "  short_description: \"隔離された候補を決定的に検証するためのSkillです\"\n"
            f"  default_prompt: \"${name} を使い、候補を検証してください。\"\n",
            encoding="utf-8",
        )
        return candidate

    def must_reject(guild: Path, target: Path, candidate: Path, label: str) -> None:
        try:
            module.validate(guild, target, candidate)
        except module.CandidateError:
            return
        require(False, f"Skill candidate validator は {label} を拒否してください。")

    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        target = guild / "repositories/demo"
        target.mkdir(parents=True)
        candidate_root = guild / ".orchestra/skill-candidates"
        candidate_root.mkdir(parents=True)
        (candidate_root / "README.md").write_bytes(template_candidate_root.read_bytes())
        (candidate_root / target.name).mkdir()
        (guild / ".agents/skills").mkdir(parents=True)
        candidate = make_candidate(guild, target)
        must_reject(guild, target, candidate, "non-Git target")
        initialized = subprocess.run(["git", "init", "-q", str(target)], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        require(initialized.returncode == 0, "Skill candidate validatorの正例Git rootを初期化できません。")
        module.validate(guild, target, candidate)

        name_mismatch = make_candidate(guild, target, "name-mismatch")
        (name_mismatch / "SKILL.md").write_text(
            (name_mismatch / "SKILL.md").read_text(encoding="utf-8").replace("name: name-mismatch", "name: wrong-name", 1),
            encoding="utf-8",
        )
        must_reject(guild, target, name_mismatch, "directory/name mismatch")

        missing_metadata = make_candidate(guild, target, "missing-metadata")
        (missing_metadata / "SKILL.md").write_text(
            (missing_metadata / "SKILL.md").read_text(encoding="utf-8").replace('  lifecycle: "needs_human"\n', "", 1),
            encoding="utf-8",
        )
        must_reject(guild, target, missing_metadata, "missing candidate metadata")

        missing_openai = make_candidate(guild, target, "missing-openai")
        (missing_openai / "agents/openai.yaml").unlink()
        must_reject(guild, target, missing_openai, "missing openai metadata")

        invalid_openai = make_candidate(guild, target, "invalid-openai")
        (invalid_openai / "agents/openai.yaml").write_text("interface:\n", encoding="utf-8")
        must_reject(guild, target, invalid_openai, "invalid openai metadata")

        escaped = Path(tmp) / "outside-candidate"
        escaped.mkdir()
        must_reject(guild, target, escaped, "candidate path escape")

        symlinked = candidate_root / target.name / "symlinked-candidate"
        symlinked.symlink_to(candidate, target_is_directory=True)
        must_reject(guild, target, symlinked, "symlink candidate")

        collision = make_candidate(guild, target, "active-collision")
        (guild / ".agents/skills/active-collision").mkdir()
        must_reject(guild, target, collision, "active Guild Skill collision")

        denied = candidate_root / target.name / "secret-candidate"
        must_reject(guild, target, denied, "secret-like candidate path")

        extra = make_candidate(guild, target, "extra-payload")
        (extra / "payload.txt").write_text("no\n", encoding="utf-8")
        must_reject(guild, target, extra, "extra candidate payload")

        marker = candidate_root / "README.md"
        marker.unlink()
        must_reject(guild, target, candidate, "missing candidate marker")
        marker.mkdir()
        must_reject(guild, target, candidate, "nonregular candidate marker")
        marker.rmdir()
        marker.write_bytes(template_candidate_root.read_bytes())

        nested_target = guild / "repositories/nested/demo"
        nested_target.mkdir(parents=True)
        nested_initialized = subprocess.run(["git", "init", "-q", str(nested_target)], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        require(nested_initialized.returncode == 0, "Skill candidate validatorの負例nested Git rootを初期化できません。")
        must_reject(guild, nested_target, candidate, "non-direct-child target")

        agents = guild / ".agents"
        agents_real = guild / "agents-real"
        agents.rename(agents_real)
        agents.symlink_to(agents_real, target_is_directory=True)
        must_reject(guild, target, candidate, "intermediate active-skill symlink")
        agents.unlink()
        agents_real.rename(agents)

        intermediate = guild / ".orchestra"
        moved = guild / "runtime-real"
        intermediate.rename(moved)
        intermediate.symlink_to(moved, target_is_directory=True)
        must_reject(guild, target, candidate, "intermediate runtime symlink")

    skill_text = read("template/.agents/skills/create-skill-candidate-from-gap/SKILL.md")
    for token in ("repeated independent evidence", "stable prevention artifact", "stable I/O", "deterministic validation", "target helper-issued snapshot", "candidate_content_digest", "`adventurer` owner", "Rootはtarget、authority、assignment、reportを調整するだけ"):
        require(token in skill_text, f"create-skill-candidate-from-gap に `{token}` が必要です。")


def _load_vscode_launch_helper() -> object:
    helper = ROOT / "template/.agents/skills/open-subrepo-in-vscode/scripts/open_repositories_in_vscode.py"
    spec = importlib.util.spec_from_file_location("open_repositories_in_vscode", helper)
    require(spec is not None and spec.loader is not None, "VS Code launch helper を読み込めません。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_vscode_launch_skill() -> None:
    """fakeだけでhelperを検証し、ここではVS Codeを起動しない。"""
    skill = read("template/.agents/skills/open-subrepo-in-vscode/SKILL.md")
    for token in (
        "Root だけ",
        "sandbox escalation",
        "人間承認",
        "launch_request_accepted",
        "plan_id",
        "approved_plan_mismatch",
        'visual_confirmation: "unknown"',
        "open -a",
        "shell interpolation",
        "relative pathは拒否",
        "PATH上の`code`はdiscovery hint",
        "launcher_unavailable",
    ):
        require(token in skill, f"open-subrepo-in-vscode skillに `{token}` が必要です。")
    require("courier" not in skill.casefold(), "open-subrepo-in-vscode は GUI 起動をcourierへ委譲してはいけません。")
    helper = _load_vscode_launch_helper()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        guild = root / "guild"
        repositories = guild / "repositories"
        repository = repositories / "demo"
        repository.mkdir(parents=True)
        launcher = root / "code"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o700)

        canonical_guild, canonical_repositories = helper.validate_roots(guild, repositories)
        require(canonical_guild == guild.resolve() and canonical_repositories == repositories.resolve(), "VS Code helper は実在する guild/repositories 実パスを受け付けてください。")
        for relative_guild, relative_repositories in ((Path("guild"), repositories), (guild, Path("repositories"))):
            try:
                helper.validate_roots(relative_guild, relative_repositories)
            except helper.TargetValidationError:
                pass
            else:
                require(False, "VS Code helper はrelative pathを拒否してください。")
        for invalid in (guild, repository, root / "missing"):
            try:
                helper.validate_roots(guild, invalid)
            except helper.TargetValidationError:
                pass
            else:
                require(False, f"VS Code helper は不正 target を拒否してください: {invalid}")

        escaped_guild = root / "escaped-guild"
        escaped_guild.mkdir()
        outside = root / "outside"
        outside.mkdir()
        (escaped_guild / "repositories").symlink_to(outside, target_is_directory=True)
        try:
            helper.validate_roots(escaped_guild, escaped_guild / "repositories")
        except helper.TargetValidationError as exc:
            require(str(exc) == "repositories_root_symlink", "VS Code helper は repositories symlink escape を拒否してください。")
        else:
            require(False, "VS Code helper は repositories symlink escape を拒否してください。")

        bundled = root / "Visual Studio Code.app/Contents/Resources/app/bin/code"
        bundled.parent.mkdir(parents=True)
        bundled.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        bundled.chmod(0o700)
        selected = helper.select_launcher(which=lambda _: None, system="Darwin", bundled_paths=(bundled,))
        require(selected == bundled.resolve(), "VS Code helper はPATHにcodeがないmacOSでbundled CLIを選んでください。")
        untrusted = root / "untrusted-code"
        untrusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        untrusted.chmod(0o700)
        selected_with_untrusted_path = helper.select_launcher(which=lambda _: str(untrusted), system="Darwin", bundled_paths=(bundled,))
        require(selected_with_untrusted_path == bundled.resolve(), "VS Code helper は固定bundleと異なるPATH launcherを無視してください。")
        require(helper.select_launcher(which=lambda _: str(untrusted), system="Darwin", bundled_paths=()) is None, "VS Code helper は未知PATH launcherだけの環境をlauncher_unavailableにしてください。")
        trusted_path_link = root / "trusted-code"
        trusted_path_link.symlink_to(bundled)
        selected_with_trusted_path = helper.select_launcher(which=lambda _: str(trusted_path_link), system="Darwin", bundled_paths=(bundled,))
        require(selected_with_trusted_path == bundled.resolve(), "VS Code helper は固定bundleと同一実体のPATH launcherを受け付けてください。")

        planned = helper.plan_launch(guild, repositories, launcher=launcher)
        require(planned["status"] == "approval_required", "VS Code helper のplanはapproval_requiredを返してください。")
        require(isinstance(planned["plan_id"], str) and bool(planned["plan_id"]), "VS Code helper のplanは承認対象のidentityを返してください。")
        require(planned["argv"] == [str(launcher.resolve()), "-n", str(repositories.resolve())], "VS Code helper は正確な `code -n repositories` argvを作ってください。")
        require(planned["launcher_identity"] == helper._launcher_identity(launcher), "VS Code helper のplanは承認対象のlauncher identityを返してください。")
        require(planned["visual_confirmation"] == "unknown", "VS Code helper のplanは視覚的成功を主張してはいけません。")

        original_select_launcher = helper.select_launcher
        helper.select_launcher = lambda: launcher
        try:
            plan_stdout = io.StringIO()
            with contextlib.redirect_stdout(plan_stdout):
                plan_exit = helper.main([
                    "--guild-root", str(guild),
                    "--repositories-root", str(repositories),
                    "--plan",
                ])
        finally:
            helper.select_launcher = original_select_launcher
        cli_plan = json.loads(plan_stdout.getvalue())
        require(plan_exit == 0 and cli_plan["status"] == "approval_required", "VS Code helper のCLI --planは承認用planを返してください。")
        require(cli_plan["guild_root"] == str(guild.resolve()) and cli_plan["repositories_root"] == str(repositories.resolve()), "VS Code helper のCLI --planはcanonical targetを表示してください。")
        require(cli_plan["launcher"] == str(launcher.resolve()) and cli_plan["launcher_identity"] == planned["launcher_identity"], "VS Code helper のCLI --planはlauncher identity/pathを表示してください。")
        require(cli_plan["argv"] == planned["argv"] and cli_plan["plan_id"] == planned["plan_id"], "VS Code helper のCLI --planは正確なargvと決定的なplan_idを表示してください。")

        original_execute_approved_launch = helper.execute_approved_launch
        helper.select_launcher = lambda: launcher
        helper.execute_approved_launch = lambda *args, **kwargs: dict(planned, status="approved_plan_mismatch")
        try:
            diagnostic_stdout = io.StringIO()
            with contextlib.redirect_stdout(diagnostic_stdout):
                diagnostic_exit = helper.main([
                    "--guild-root", str(guild),
                    "--repositories-root", str(repositories),
                    "--execute",
                    "--approved-plan-id", str(planned["plan_id"]),
                ])
        finally:
            helper.select_launcher = original_select_launcher
            helper.execute_approved_launch = original_execute_approved_launch
        cli_diagnostic = json.loads(diagnostic_stdout.getvalue())
        require(diagnostic_exit == 1 and cli_diagnostic["status"] == "approved_plan_mismatch", "VS Code helper のCLI execute mismatchを成功にしてはいけません。")
        for key in ("guild_root", "repositories_root", "launcher", "launcher_identity", "argv"):
            require(cli_diagnostic[key] == "<redacted>", f"VS Code helper の通常CLI診断は `{key}` をredactionしてください。")
        require(str(guild.resolve()) not in diagnostic_stdout.getvalue() and str(launcher.resolve()) not in diagnostic_stdout.getvalue(), "VS Code helper の通常CLI診断はapproval専用のローカル値を露出してはいけません。")

        calls: list[object] = []
        def should_not_run(*args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            raise AssertionError("plan mode は subprocess を実行してはいけません")
        original_run = helper.subprocess.run
        helper.subprocess.run = should_not_run
        try:
            no_subprocess_plan = helper.plan_launch(guild, repositories, launcher=launcher)
        finally:
            helper.subprocess.run = original_run
        require(no_subprocess_plan["status"] == "approval_required" and not calls, "VS Code helper のplan modeはsubprocessを呼んではいけません。")

        class Result:
            def __init__(self, returncode: int) -> None:
                self.returncode = returncode

        def zero_runner(argv: list[str], *, check: bool) -> Result:
            calls.append((argv, check))
            return Result(0)
        approved_plan_id = planned["plan_id"]
        accepted = helper.execute_launch(planned, approved_plan_id, runner=zero_runner)
        require(calls == [([str(launcher.resolve()), "-n", str(repositories.resolve())], False)], "VS Code helper はshellなしで計画済みargvだけを一回実行してください。")
        require(accepted["status"] == "launch_request_accepted" and accepted["visual_confirmation"] == "unknown", "VS Code helper はexit 0をlaunch request acceptanceだけとして報告してください。")

        rejected = helper.execute_launch(planned, approved_plan_id, runner=lambda argv, *, check: Result(23))
        require(rejected["status"] == "launch_failed" and rejected["exit_code"] == 23, "VS Code helper はnonzero launcher resultを成功にしてはいけません。")

        mismatch_calls: list[object] = []
        mismatch = helper.execute_launch(planned, "not-the-approved-plan", runner=lambda *args, **kwargs: mismatch_calls.append((args, kwargs)))
        require(mismatch["status"] == "approved_plan_mismatch" and not mismatch_calls, "VS Code helper はidentity mismatch時にrunnerを呼んではいけません。")

        missing_id_calls: list[object] = []
        missing_id = helper.execute_launch(planned, None, runner=lambda *args, **kwargs: missing_id_calls.append((args, kwargs)))
        require(missing_id["status"] == "approved_plan_id_required" and not missing_id_calls, "VS Code helper は承認identity未指定時にrunnerを呼んではいけません。")

        guild_a = root / "guild-a"
        guild_b = root / "guild-b"
        (guild_a / "repositories").mkdir(parents=True)
        (guild_b / "repositories").mkdir(parents=True)
        guild_link = root / "guild-link"
        guild_link.symlink_to(guild_a, target_is_directory=True)
        first_plan = helper.plan_launch(guild_link, guild_link / "repositories", launcher=launcher)
        guild_link.unlink()
        guild_link.symlink_to(guild_b, target_is_directory=True)
        retarget_calls: list[object] = []
        retargeted = helper.execute_approved_launch(
            guild_link,
            guild_link / "repositories",
            first_plan["plan_id"],
            launcher=launcher,
            runner=lambda *args, **kwargs: retarget_calls.append((args, kwargs)),
        )
        require(retargeted["status"] == "approved_plan_mismatch" and not retarget_calls, "VS Code helper は二段階実行中のguild symlink再targetを拒否してください。")


def validate_stop_hook() -> None:
    text = read("template/.codex/hooks/stop_quality_gate.py")
    require("Quest" in text and "Trial" in text and "Risk" in text, "stop_quality_gate.py はQuest/Trial/Riskの要約を促してください。")
    hooks = read("template/.codex/hooks.json")
    shell = read("template/.codex/hooks/stop_quality_gate.sh")
    require("stop_quality_gate.sh" in hooks, "hooks.json はstop_quality_gate.shを呼び出してください。")
    require("python3" not in hooks and "/usr/bin/env python" not in hooks, "hooks.json はhost Pythonを直接探索しないでください。")
    require("valid_root()" in hooks and "*/repositories/*" in hooks and "$1/repositories" in hooks, "hooks.json のroot guardが不足しています。")
    require("docker image inspect" in shell and "AGENT_GUILD_ORCHESTRA_DOCKER_SKIP_BUILD=1" in shell, "Stop hookはcold buildせず既存imageを使ってください。")
    strict_env = "AGENT_GUILD_ORCHESTRA_STOP_QUALITY_STRICT"
    legacy_strict_env = "CODEX" + "_STOP_QUALITY_STRICT"
    require(strict_env in text and strict_env in shell, "Stop hookはAgent Guild固有のstrict環境変数を使ってください。")
    require(legacy_strict_env not in text and legacy_strict_env not in shell, "Stop hookに旧strict環境変数を残さないでください。")
    for rel in ("scripts/docker_python.sh", "template/.agents/orchestra/scripts/docker_python.sh"):
        runner = read(rel)
        require("CODEX_HOOK_PAYLOAD" in runner, f"{rel} はCodex hook payloadをコンテナへ渡してください。")
        require(strict_env in runner and legacy_strict_env not in runner, f"{rel} はAgent Guild固有のstrict環境変数だけを渡してください。")
