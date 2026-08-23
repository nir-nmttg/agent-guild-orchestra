"""installer の upgrade / compatibility smoke 検証。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from .core import ROOT, require
from .rules import LEDGER_TABLES

AGENTS_START = "<!-- agent-guild-orchestra:start -->"
AGENTS_END = "<!-- agent-guild-orchestra:end -->"
EXCLUDE_START = "# agent-guild-orchestra:start"
EXCLUDE_END = "# agent-guild-orchestra:end"
BACKUP_DIRECTORY = ".agent-guild-orchestra-backups"
RUNTIME_SCHEMA_VERSION = "4.0"
ROOT_MODEL_CONTEXT_WINDOW = 1_050_000
SUBAGENT_AUTO_COMPACT_TOKEN_LIMIT = 200_000

READ_ONLY_AGENT_ROLES = (
    "sage",
    "cartographer",
    "examiner",
    "guildmaster",
    "inquisitor",
    "warden",
    "captain",
)
EXPECTED_AGENT_FILES = {
    "adventurer.toml",
    "sage.toml",
    "cartographer.toml",
    "courier.toml",
    "examiner.toml",
    "guildmaster.toml",
    "inquisitor.toml",
    "artificer.toml",
    "captain.toml",
    "warden.toml",
}
EXPECTED_UPDATED_AGENT_PAIRS = {
    "adventurer": ("gpt-5.6-luna", "max"),
    "sage": ("gpt-5.6-luna", "max"),
    "examiner": ("gpt-5.6-luna", "max"),
    "courier": ("gpt-5.6-luna", "high"),
    "inquisitor": ("gpt-5.6-sol", "xhigh"),
}
EXPECTED_SKILL_DIRS = {
    "branch-implementation-final-review",
    "browser-research-readonly",
    "communicate-work-estimates",
    "create-skill-candidate-from-gap",
    "explain-clearly",
    "git-branch-from-session",
    "git-rename-unpushed-branch-from-diff",
    "git-split-commits-from-diff",
    "github-pull-request-from-branch",
    "github-safe-push-from-branch",
    "implementation-behavior-verification",
    "open-subrepo-in-vscode",
    "orchestra-instruction-contract-review",
    "orchestra-runtime-security-audit",
    "orchestra-validation-review",
    "pull-request-description-from-branch",
    "quest-awareness-loop",
    "refine-design-plan",
    "repository-design-mapmaking",
    "use-guild-workflow",
}
VSCODE_HELPER_REL = Path(".agents/skills/open-subrepo-in-vscode/scripts/open_repositories_in_vscode.py")
CANDIDATE_VALIDATOR_REL = Path(".agents/skills/create-skill-candidate-from-gap/scripts/validate_skill_candidate.py")
INSTALLED_OLD_TERM_TOKENS = (
    "fa" "ble",
    "meta" "cognitive",
    "meta-" "recognition",
    "meta " "recognition",
    "meta_" "recognition",
    "メタ認" "知",
    "メタ認" "識",
    "cognitive_" "failure_memory",
)


def _load_installer_module() -> object:
    spec = importlib.util.spec_from_file_location("agent_guild_orchestra_installer", ROOT / "scripts/install.py")
    require(spec is not None and spec.loader is not None, "install.py helper を読み込めません。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = _load_installer_module()


def _run_install(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/install.py"), *(str(arg) for arg in args)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _assert_release_documentation_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deployment = (ROOT / "docs/deployment-patterns.md").read_text(encoding="utf-8")
    agent_deployment = (ROOT / "docs/agent-deployment.md").read_text(encoding="utf-8")
    model_selection = (ROOT / "docs/model-selection-evaluation.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for label, text in (("README", readme), ("deployment patterns", deployment), ("agent deployment", agent_deployment)):
        require("--backup" in text, f"{label} は clean install の backup 要件を文書化してください。")
        require("--allow-clean-install-without-backup" in text, f"{label} は clean install の明示的な backup なし escape hatch を文書化してください。")
    for label, text in (("README", readme), ("agent deployment", agent_deployment), ("model selection", model_selection), ("CHANGELOG", changelog)):
        require("30" in text and "synthetic" in text, f"{label} は30件のdeterministic synthetic contract traceを明記してください。")
        require("negative" in text and "mutation" in text, f"{label} はnegative/mutation traceを明記してください。")
        require("live" in text and ("未取得" in text or "未検証" in text), f"{label} はlive real-model fan-out matrixが未検証であることを明記してください。")
        require("27" not in text, f"{label} に旧27 trace参照を残さないでください。")


def _installed_text_paths(target: Path) -> list[Path]:
    text_suffixes = {".json", ".md", ".py", ".sh", ".sql", ".toml", ".txt", ".yaml", ".yml"}
    return sorted(
        path
        for path in target.rglob("*")
        if path.is_file() and (path.name == "AGENTS.md" or path.suffix in text_suffixes)
    )


def _assert_installed_surface(target: Path, allowed_extra_skills: set[str] | None = None) -> None:
    require((target / "AGENTS.md").exists(), "install.py は AGENTS.md を生成してください。")
    require((target / ".codex/config.toml").exists(), "install.py は .codex/config.toml を導入してください。")
    root_config = INSTALLER.read_toml_document(target / ".codex/config.toml")
    require(root_config.get("model") == "gpt-5.6-sol", "install.py はRoot modelをSolにしてください。")
    require(
        root_config.get("model_context_window") == ROOT_MODEL_CONTEXT_WINDOW,
        f"install.py はRootのfull supported model_context_windowを{ROOT_MODEL_CONTEXT_WINDOW}にしてください。",
    )
    require("model_auto_compact_token_limit" not in root_config, "install.py はRootへsubagent用early compactionを出力しないでください。")
    require("model_reasoning_effort" not in root_config, "install.py はRoot reasoning effortをproject-localに出力しないでください。")
    agents_config = root_config.get("agents")
    require(
        isinstance(agents_config, dict) and agents_config.get("job_max_runtime_seconds") == 2400,
        "install.py は導入先のagents.job_max_runtime_secondsを2400秒にしてください。",
    )
    agent_dir = target / ".codex/agents"
    actual_agents = {path.name for path in agent_dir.glob("*.toml")}
    require(actual_agents == EXPECTED_AGENT_FILES, ".codex/agents の導入 file set が期待値と一致しません: " + ", ".join(sorted(actual_agents)))
    for role, expected_pair in EXPECTED_UPDATED_AGENT_PAIRS.items():
        agent = INSTALLER.read_toml_document(agent_dir / f"{role}.toml")
        require(
            (agent.get("model"), agent.get("model_reasoning_effort")) == expected_pair,
            f"install.py は導入先の{role} model/effortを{expected_pair[0]}/{expected_pair[1]}にしてください。",
        )
    for agent_path in sorted(agent_dir.glob("*.toml")):
        agent = INSTALLER.read_toml_document(agent_path)
        require(
            agent.get("model_auto_compact_token_limit") == SUBAGENT_AUTO_COMPACT_TOKEN_LIMIT,
            f"install.py は導入先の{agent_path.stem}へ{SUBAGENT_AUTO_COMPACT_TOKEN_LIMIT}のearly compactionを設定してください。",
        )
        require(
            "model_context_window" not in agent,
            f"install.py は導入先の{agent_path.stem}でmodel_context_windowを固定しないでください。",
        )
    skill_dir = target / ".agents/skills"
    actual_skills = {path.name for path in skill_dir.iterdir() if path.is_dir()}
    expected_skills = EXPECTED_SKILL_DIRS | (allowed_extra_skills or set())
    require(actual_skills == expected_skills, ".agents/skills の導入 directory set が期待値と一致しません: " + ", ".join(sorted(actual_skills)))
    for skill_name in sorted(EXPECTED_SKILL_DIRS):
        skill_path = skill_dir / skill_name / "SKILL.md"
        require(
            INSTALLER.read_skill_owner(skill_path) == "agent-guild-orchestra",
            f"{skill_path.relative_to(target)} の owner は agent-guild-orchestra にしてください。",
        )
        openai_yaml = skill_dir / skill_name / "agents/openai.yaml"
        require(openai_yaml.is_file(), f"{openai_yaml.relative_to(target)} を導入してください。")
    installed_vscode_helper = target / VSCODE_HELPER_REL
    source_vscode_helper = ROOT / "template" / VSCODE_HELPER_REL
    require(installed_vscode_helper.is_file(), f"{VSCODE_HELPER_REL} を導入してください。")
    require(
        installed_vscode_helper.read_bytes() == source_vscode_helper.read_bytes(),
        "install.py は VS Code launch helper を template と byte-identical に導入してください。",
    )
    installed_candidate_validator = target / CANDIDATE_VALIDATOR_REL
    source_candidate_validator = ROOT / "template" / CANDIDATE_VALIDATOR_REL
    require(installed_candidate_validator.is_file(), f"{CANDIDATE_VALIDATOR_REL} を導入してください。")
    require(
        installed_candidate_validator.read_bytes() == source_candidate_validator.read_bytes(),
        "install.py は Skill candidate validator を template と byte-identical に導入してください。",
    )
    installed_candidate_root = target / ".orchestra/skill-candidates"
    source_candidate_root = ROOT / "template/.agents/orchestra/skill-candidates"
    require((installed_candidate_root / "README.md").is_file(), ".orchestra/skill-candidates/README.md を導入してください。")
    require(
        (installed_candidate_root / "README.md").read_bytes() == (source_candidate_root / "README.md").read_bytes(),
        "install.py は candidate root marker を template と byte-identical に導入してください。",
    )
    require((skill_dir / "quest-awareness-loop/SKILL.md").exists(), "install.py は quest-awareness-loop skill を導入してください。")
    require((target / ".agents/orchestra/docs/agent-memory.md").exists(), "install.py は agent-memory runtime artifact を導入してください。")
    require((target / ".orchestra/dashboard.md").exists(), "install.py は dashboard runtime artifact を .orchestra/dashboard.md に導入してください。")
    require(not (target / ".agents/orchestra/dashboard.md").exists(), "install.py は dashboard を static .agents/orchestra へ導入しないでください。")
    database = target / ".orchestra/queue/state.sqlite"
    require(database.exists(), "install.py は .orchestra/queue/state.sqlite を初期化してください。")
    with sqlite3.connect(database) as connection:
        schema_version = connection.execute("SELECT value FROM queue_metadata WHERE key = 'schema_version'").fetchone()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    require(schema_version is not None and schema_version[0] == RUNTIME_SCHEMA_VERSION, "install.py は runtime DB schema_version を 4.0 にしてください。")
    missing_tables = sorted(LEDGER_TABLES - tables)
    require(not missing_tables, "install.py が初期化した runtime DB に不足 table があります: " + ", ".join(missing_tables))
    for path in _installed_text_paths(target):
        try:
            text = path.read_text(encoding="utf-8").casefold()
        except UnicodeDecodeError:
            continue
        for token in INSTALLED_OLD_TERM_TOKENS:
            require(token not in text, f"install.py の導入結果に旧語彙 `{token}` が残っています: {path.relative_to(target)}")


def _assert_agents_block_idempotent(target: Path, expect_update_position: bool = True) -> None:
    agents = (target / "AGENTS.md").read_text(encoding="utf-8")
    require(agents.count(AGENTS_START) == 1 and agents.count(AGENTS_END) == 1, "install.py は AGENTS.md 管理 marker を各 1 個だけにしてください。")
    require("既存ユーザー規約" in agents, "install.py は AGENTS.md の管理ブロック外の既存内容を保持してください。")
    require("中間の既存内容" in agents, "install.py は重複管理ブロック間の AGENTS.md 既存内容を保持してください。")
    require("末尾の既存内容" in agents, "install.py は AGENTS.md 管理ブロック後の既存内容を保持してください。")
    require(
        agents.index("既存ユーザー規約") < agents.index("中間の既存内容") < agents.index("末尾の既存内容"),
        "install.py は AGENTS.md の管理ブロック外の既存内容順序を保持してください。",
    )
    if expect_update_position:
        require(
            agents.index("既存ユーザー規約") < agents.index(AGENTS_START) < agents.index("中間の既存内容"),
            "install.py は AGENTS.md の最初の complete 管理ブロック位置で更新してください。",
        )
    else:
        require(
            agents.index("末尾の既存内容") < agents.index(AGENTS_START),
            "install.py --clean-install は pruning 後の既存内容を保持してから AGENTS.md 管理ブロックを再導入してください。",
        )


def _assert_git_exclude_block_idempotent(target: Path, expect_update_position: bool = True) -> None:
    exclude = (target / ".git/info/exclude").read_text(encoding="utf-8")
    require(exclude.count(EXCLUDE_START) == 1 and exclude.count(EXCLUDE_END) == 1, "install.py は .git/info/exclude 管理 marker を各 1 個だけにしてください。")
    require(f"{BACKUP_DIRECTORY}/" in exclude, "install.py は現行 backup directory を除外してください。")
    require("existing-before" in exclude, "install.py は .git/info/exclude の管理ブロック前の既存内容を保持してください。")
    require("existing-between" in exclude, "install.py は .git/info/exclude の重複管理ブロック間の既存内容を保持してください。")
    require("existing-after" in exclude, "install.py は .git/info/exclude の管理ブロック後の既存内容を保持してください。")
    require(
        exclude.index("existing-before") < exclude.index("existing-between") < exclude.index("existing-after"),
        "install.py は .git/info/exclude の管理ブロック外の既存内容順序を保持してください。",
    )
    if expect_update_position:
        require(
            exclude.index("existing-before") < exclude.index(EXCLUDE_START) < exclude.index("existing-between"),
            "install.py は .git/info/exclude の最初の complete 管理ブロック位置で更新してください。",
        )
    else:
        require(
            exclude.index("existing-after") < exclude.index(EXCLUDE_START),
            "install.py --clean-install は pruning 後の既存内容を保持してから .git/info/exclude 管理ブロックを再導入してください。",
        )


def _assert_token_order(text: str, tokens: tuple[str, ...], label: str) -> None:
    cursor = -1
    for token in tokens:
        index = text.find(token, cursor + 1)
        require(index != -1, f"{label} に `{token}` が見つかりません。")
        require(index > cursor, f"{label} の `{token}` の順序が不正です。")
        cursor = index


def _assert_managed_block_helper_matrix() -> None:
    block = f"{AGENTS_START}\n新しい管理ブロック\n{AGENTS_END}\n"
    cases = (
        (
            "空ファイル",
            "",
            ("新しい管理ブロック",),
            (),
        ),
        (
            "通常テキストのみ",
            "before\n",
            ("before", "新しい管理ブロック"),
            ("before",),
        ),
        (
            "孤立 start のみ",
            f"before\n{AGENTS_START}\norphan body\n",
            ("before", "orphan body", "新しい管理ブロック"),
            ("before", "orphan body"),
        ),
        (
            "孤立 end のみ",
            f"{AGENTS_END}\nbefore\nafter\n",
            ("before", "after", "新しい管理ブロック"),
            ("before", "after"),
        ),
        (
            "単一 complete block",
            f"before\n{AGENTS_START}\nold managed\n{AGENTS_END}\nafter\n",
            ("before", "新しい管理ブロック", "after"),
            ("before", "after"),
        ),
        (
            "中間テキスト付き重複 complete block",
            f"before\n{AGENTS_START}\nold managed 1\n{AGENTS_END}\nbetween\n{AGENTS_START}\nold managed 2\n{AGENTS_END}\nafter\n",
            ("before", "新しい管理ブロック", "between", "after"),
            ("before", "between", "after"),
        ),
        (
            "隣接 complete block",
            f"before\n{AGENTS_START}\nold managed 1\n{AGENTS_END}\n{AGENTS_START}\nold managed 2\n{AGENTS_END}\nafter\n",
            ("before", "新しい管理ブロック", "after"),
            ("before", "after"),
        ),
        (
            "入れ子 duplicate marker",
            f"before\n{AGENTS_START}\n{AGENTS_START}\nold nested\n{AGENTS_END}\n{AGENTS_END}\nafter\n",
            ("before", "新しい管理ブロック", "after"),
            ("before", "after"),
        ),
        (
            "先頭孤立 end と末尾孤立 start",
            f"{AGENTS_END}\nbefore\n{AGENTS_START}\nold managed\n{AGENTS_END}\nafter\n{AGENTS_START}\n",
            ("before", "新しい管理ブロック", "after"),
            ("before", "after"),
        ),
    )
    for label, text, replace_tokens, prune_tokens in cases:
        replaced = INSTALLER.replace_or_append_block(text, block, AGENTS_START, AGENTS_END)
        require(replaced.count(AGENTS_START) == 1 and replaced.count(AGENTS_END) == 1, f"{label}: replace は marker を 1 組にしてください。")
        require("old managed" not in replaced and "old nested" not in replaced, f"{label}: replace は古い管理ブロック本文を残さないでください。")
        _assert_token_order(replaced, replace_tokens, f"{label}: replace")

        pruned = INSTALLER.remove_block(text, AGENTS_START, AGENTS_END)
        require(AGENTS_START not in pruned and AGENTS_END not in pruned, f"{label}: prune は marker を残さないでください。")
        require("old managed" not in pruned and "old nested" not in pruned, f"{label}: prune は古い管理ブロック本文を残さないでください。")
        if prune_tokens:
            _assert_token_order(pruned, prune_tokens, f"{label}: prune")
        else:
            require(pruned == "", f"{label}: prune 後は空にしてください。")


def _write_duplicate_agents(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "AGENTS.md").write_text(
        f"{AGENTS_END}\n"
        "既存ユーザー規約\n\n"
        f"{AGENTS_START}\n"
        "古い complete block 1\n"
        f"{AGENTS_END}\n"
        "中間の既存内容\n"
        f"{AGENTS_START}\n"
        f"{AGENTS_START}\n"
        "古い二重化ブロック\n"
        f"{AGENTS_END}\n"
        f"{AGENTS_END}\n"
        "末尾の既存内容\n"
        f"{AGENTS_START}\n",
        encoding="utf-8",
    )


def _write_duplicate_git_exclude(target: Path) -> None:
    exclude = target / ".git/info/exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(
        f"{EXCLUDE_END}\n"
        "existing-before\n"
        f"{EXCLUDE_START}\n"
        "old exclude block 1\n"
        f"{EXCLUDE_END}\n"
        "existing-between\n"
        f"{EXCLUDE_START}\n"
        f"{EXCLUDE_START}\n"
        "old exclude block 2\n"
        f"{EXCLUDE_END}\n"
        f"{EXCLUDE_END}\n"
        "existing-after\n"
        f"{EXCLUDE_START}\n",
        encoding="utf-8",
    )


def _assert_incompatible_runtime_install_rejected(target: Path, label: str) -> None:
    existing_database = target / ".orchestra/queue/state.sqlite"
    existing_dashboard = target / ".orchestra/dashboard.md"
    existing_dashboard.write_text(f"legacy dashboard {label}\n", encoding="utf-8")

    incompatible = _run_install("--target", target, "--mode", "copy")
    incompatible_output = incompatible.stdout + incompatible.stderr
    require(incompatible.returncode != 0, f"install.py は既存 incompatible runtime DB の通常 install を拒否してください: {label}")
    for token in ("--backup", "--reset-runtime", "--clean-install", "--backup --clean-install"):
        require(token in incompatible_output, f"install.py の既存 incompatible runtime DB 拒否 message は `{token}` を示してください: {label}")
    require(existing_database.exists(), f"install.py の拒否時は既存 runtime DB を削除しないでください: {label}")
    require(
        existing_dashboard.read_text(encoding="utf-8") == f"legacy dashboard {label}\n",
        f"install.py の拒否時は既存 dashboard を変更しないでください: {label}",
    )
    require(not (target / "AGENTS.md").exists(), f"install.py の既存 incompatible runtime DB 拒否時は AGENTS.md を書かないでください: {label}")
    require(
        not (target / ".agents").exists() and not (target / ".codex").exists(),
        f"install.py の既存 incompatible runtime DB 拒否時は static runtime を書かないでください: {label}",
    )
    require(not (target / "repositories").exists(), f"install.py の既存 incompatible runtime DB 拒否時は repositories/ を作らないでください: {label}")


def validate_install_upgrade_smoke() -> None:
    _assert_release_documentation_contract()
    _assert_managed_block_helper_matrix()

    with tempfile.TemporaryDirectory() as tmp:
        skill_root = Path(tmp)
        standard_skill = skill_root / "standard.md"
        standard_skill.write_text(
            "---\nname: standard\ndescription: standard\nmetadata:\n  owner: agent-guild-orchestra\n  scope: target-repository-workflow\n---\n",
            encoding="utf-8",
        )
        legacy_skill = skill_root / "legacy.md"
        legacy_skill.write_text(
            "---\nname: legacy\ndescription: legacy\nowner: agent-guild-orchestra\nscope: target-repository-workflow\n---\n",
            encoding="utf-8",
        )
        canonical_third_party = skill_root / "canonical-third-party.md"
        canonical_third_party.write_text(
            "---\nname: canonical-third-party\ndescription: canonical owner wins\nowner: agent-guild-orchestra\nmetadata:\n  owner: third-party\n  scope: target-repository-workflow\n---\n",
            encoding="utf-8",
        )
        canonical_orchestra = skill_root / "canonical-orchestra.md"
        canonical_orchestra.write_text(
            "---\nname: canonical-orchestra\ndescription: canonical owner wins\nowner: third-party\nmetadata:\n  owner: agent-guild-orchestra\n  scope: target-repository-workflow\n---\n",
            encoding="utf-8",
        )
        invalid_utf8_skill = skill_root / "invalid-utf8.md"
        invalid_utf8_skill.write_bytes(b"---\nmetadata:\n  owner: third-party\n---\n\xff\x00preserve\n")
        original_yaml = INSTALLER.yaml
        try:
            for parser_name, parser in (("PyYAML", original_yaml), ("fallback", None)):
                INSTALLER.yaml = parser
                require(
                    INSTALLER.read_skill_owner(standard_skill) == "agent-guild-orchestra",
                    f"install.py の{parser_name} parserはmetadata.ownerを読めるようにしてください。",
                )
                require(
                    INSTALLER.read_skill_owner(legacy_skill) == "agent-guild-orchestra",
                    f"install.py の{parser_name} parserは旧top-level ownerを後方互換で読めるようにしてください。",
                )
                require(
                    INSTALLER.read_skill_owner(canonical_third_party) == "third-party"
                    and INSTALLER.read_skill_owner(canonical_orchestra) == "agent-guild-orchestra",
                    f"install.py の{parser_name} parserはmetadata.ownerを旧top-level ownerより優先してください。",
                )
                require(
                    INSTALLER.read_skill_owner(invalid_utf8_skill) is None,
                    f"install.py の{parser_name} parserは非UTF-8 Skillのownerを不明として扱ってください。",
                )

                clean_target = (skill_root / f"clean-{parser_name.casefold()}").resolve()
                keep_skill = clean_target / ".agents/skills/keep/SKILL.md"
                remove_skill = clean_target / ".agents/skills/remove/SKILL.md"
                keep_skill.parent.mkdir(parents=True)
                remove_skill.parent.mkdir(parents=True)
                keep_skill.write_text(canonical_third_party.read_text(encoding="utf-8"), encoding="utf-8")
                remove_skill.write_text(canonical_orchestra.read_text(encoding="utf-8"), encoding="utf-8")
                INSTALLER.clean_owner_scoped_skills(clean_target, dry_run=False)
                require(keep_skill.exists(), f"install.py の{parser_name} clean-install cleanupはcanonical third-party Skillを保持してください。")
                require(not remove_skill.exists(), f"install.py の{parser_name} clean-install cleanupはcanonical orchestra Skillを除去してください。")

            for error_type in (PermissionError, OSError):
                failed_clean_target = (skill_root / f"unreadable-{error_type.__name__}").resolve()
                owned_skill = failed_clean_target / ".agents/skills/owned/SKILL.md"
                owned_skill.parent.mkdir(parents=True)
                owned_skill.write_text(
                    "---\nname: owned\nmetadata:\n  owner: agent-guild-orchestra\n---\n",
                    encoding="utf-8",
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output), patch.object(Path, "read_text", side_effect=error_type("synthetic owner read failure")):
                    try:
                        INSTALLER.clean_owner_scoped_skills(failed_clean_target, dry_run=False)
                    except error_type:
                        pass
                    else:
                        require(False, f"install.py は{error_type.__name__}をowner不明へ変換せず伝播してください。")
                require(owned_skill.exists(), f"install.py は{error_type.__name__}時にowner Skillを削除しないでください。")
                require("remove" not in output.getvalue(), f"install.py は{error_type.__name__}時に削除成功を出力しないでください。")
        finally:
            INSTALLER.yaml = original_yaml

    original_yaml = INSTALLER.yaml
    try:
        INSTALLER.yaml = None
        INSTALLER.validate_settings_release_contract(ROOT / "template")
        fallback_root_model, fallback_pairs = INSTALLER.load_model_policy(ROOT / "template")
    finally:
        INSTALLER.yaml = original_yaml
    require(fallback_root_model == "gpt-5.6-sol", "install.py のPyYAMLなしfallbackでもRoot model policyを読めるようにしてください。")
    require(
        all(fallback_pairs.get(role) == pair for role, pair in EXPECTED_UPDATED_AGENT_PAIRS.items()),
        "install.py のPyYAMLなしfallbackでも固定subagent pairを保持してください。",
    )

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        dry_run = _run_install("--target", target, "--mode", "copy", "--dry-run")
        require(dry_run.returncode == 0, "install.py --dry-run が失敗しました: " + dry_run.stderr)
        require(not target.exists(), "install.py --dry-run は target に書き込まないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "template"
        target = Path(tmp) / "guild"
        shutil.copytree(ROOT / "template", source)
        non_default = _run_install("--target", target, "--source", source, "--mode", "copy", "--dry-run")
        require(non_default.returncode != 0 and "--allow-non-default-source" in (non_default.stdout + non_default.stderr), "install.py は非 default source を明示許可なしで拒否してください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        _write_duplicate_agents(target)
        _write_duplicate_git_exclude(target)
        legacy_paths = [
            target / ".codex/agents/spark.toml",
            target / ".codex/agents/" / ("meta" "cognitive_controller.toml"),
            target / ".codex/agents/advisor.toml",
            target / ".codex/agents/focus_reviewer.toml",
            target / ".codex/agents/integration_owner.toml",
            target / ".codex/agents/party_leader.toml",
            target / ".codex/agents/quest_sentinel.toml",
            target / ".agents/orchestra/instructions/advisor.md",
            target / ".agents/orchestra/instructions/focus_reviewer.md",
            target / ".agents/orchestra/instructions/integration_owner.md",
            target / ".agents/orchestra/instructions/party_leader.md",
            target / ".agents/orchestra/instructions/quest_sentinel.md",
            target / ".agents/orchestra/queue/templates/advisor_assignment.yaml",
            target / ".agents/orchestra/queue/templates/advisor_report.yaml",
            target / ".agents/orchestra/queue/templates/focus_reviewer_assignment.yaml",
            target / ".agents/orchestra/queue/templates/focus_reviewer_report.yaml",
            target / ".agents/orchestra/queue/templates/quest_sentinel_assignment.yaml",
            target / ".agents/orchestra/queue/templates/adventurer_task.yaml",
            target / ".agents/orchestra/queue/templates/inquisitor_task.yaml",
            target / ".agents/skills" / ("meta" "cognitive-task-loop") / "SKILL.md",
            target / ".agents/skills/explain-for-newcomers/SKILL.md",
        ]
        for path in legacy_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("legacy: true\n", encoding="utf-8")
        legacy_explanation_skill = target / ".agents/skills/explain-for-newcomers/SKILL.md"
        legacy_explanation_skill.write_text(
            "---\nname: explain-for-newcomers\ndescription: legacy explanation skill\nmetadata:\n  owner: agent-guild-orchestra\n  scope: response-explanation-workflow\n---\n",
            encoding="utf-8",
        )
        third_party_skill = target / ".agents/skills/third-party-example/SKILL.md"
        third_party_skill.parent.mkdir(parents=True, exist_ok=True)
        third_party_skill.write_text(
            "---\nname: third-party-example\ndescription: user-owned third-party skill\nmetadata:\n  owner: third-party\n  scope: example\n---\n",
            encoding="utf-8",
        )

        installed = _run_install("--target", target, "--mode", "copy")
        require(installed.returncode == 0, "install.py の通常 install smoke が失敗しました: " + installed.stderr)
        _assert_installed_surface(target, {"third-party-example"})
        _assert_agents_block_idempotent(target)
        _assert_git_exclude_block_idempotent(target)
        runtime_candidate = target / ".orchestra/skill-candidates/demo/keep/SKILL.md"
        runtime_candidate.parent.mkdir(parents=True)
        runtime_candidate.write_text("candidate\n", encoding="utf-8")

        root_config_path = target / ".codex/config.toml"
        for effort in ("high", "xhigh", "ultra"):
            root_config = root_config_path.read_text(encoding="utf-8")
            root_config_path.write_text(
                root_config.replace('model = "gpt-5.6-sol"', f'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "{effort}"', 1),
                encoding="utf-8",
            )
            reinstalled = _run_install("--target", target, "--mode", "copy")
            require(reinstalled.returncode == 0, f"install.py のRoot {effort}再install smokeが失敗しました: " + reinstalled.stderr)
            require(
                "model_reasoning_effort" not in INSTALLER.read_toml_document(root_config_path),
                f"install.py の通常再installは既存Root {effort} effortをproject-local configから除去してください。",
            )
        _assert_installed_surface(target, {"third-party-example"})
        _assert_agents_block_idempotent(target)
        _assert_git_exclude_block_idempotent(target)
        remaining = [str(path.relative_to(target)) for path in legacy_paths if path.exists()]
        require(not remaining, "install.py は削除済み旧 template を prune してください: " + ", ".join(remaining))
        require(third_party_skill.exists(), "install.py はunrelated third-party Skillを保持してください。")
        require(runtime_candidate.read_text(encoding="utf-8") == "candidate\n", "install.py の通常再導入は runtime sibling candidate を保持してください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        existing_database = target / ".orchestra/queue/state.sqlite"
        existing_database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(existing_database) as connection:
            connection.executescript((ROOT / "template/.agents/orchestra/scripts/queue_schema.sql").read_text(encoding="utf-8"))
            connection.execute("INSERT INTO queue_metadata(key, value) VALUES('schema_version', '3.0')")
            connection.commit()

        _assert_incompatible_runtime_install_rejected(target, "旧schema_version=3.0")
        with sqlite3.connect(existing_database) as connection:
            schema_version = connection.execute("SELECT value FROM queue_metadata WHERE key = 'schema_version'").fetchone()
        require(schema_version is not None and schema_version[0] == "3.0", "install.py の拒否時は旧v3 runtime DB を変更しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        existing_database = target / ".orchestra/queue/state.sqlite"
        existing_database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(existing_database) as connection:
            connection.execute("CREATE TABLE queue_metadata(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            connection.execute("INSERT INTO queue_metadata(key, value, updated_at) VALUES('schema_version', '4.0', 'legacy')")
            connection.execute("CREATE TABLE assignments(task_id TEXT PRIMARY KEY, status TEXT)")
            connection.commit()

        _assert_incompatible_runtime_install_rejected(target, "v4 physical schema mismatch")
        with sqlite3.connect(existing_database) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            columns = {row[1] for row in connection.execute("PRAGMA table_info(assignments)")}
        require("assignments" in tables and "task_id" in columns, "install.py の拒否時は既存 physical schema を変更しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        existing_database = target / ".orchestra/queue/state.sqlite"
        existing_database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(existing_database) as connection:
            connection.executescript((ROOT / "template/.agents/orchestra/scripts/queue_schema.sql").read_text(encoding="utf-8"))
            connection.execute("INSERT INTO queue_metadata(key, value) VALUES('schema_version', '4.0')")
            connection.execute("ALTER TABLE quests ADD COLUMN raw_log TEXT")
            connection.execute("CREATE TABLE unsafe_runtime_payload(secret TEXT)")
            connection.commit()

        _assert_incompatible_runtime_install_rejected(target, "v4 unexpected physical schema")
        with sqlite3.connect(existing_database) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            columns = {row[1] for row in connection.execute("PRAGMA table_info(quests)")}
        require("unsafe_runtime_payload" in tables and "raw_log" in columns, "install.py の拒否時は未知 schema を変更しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        existing_database = target / ".orchestra/queue/state.sqlite"
        existing_database.parent.mkdir(parents=True, exist_ok=True)
        canonical_schema = (ROOT / "template/.agents/orchestra/scripts/queue_schema.sql").read_text(encoding="utf-8")
        type_drift_schema = canonical_schema.replace("  status TEXT NOT NULL,", "  status INTEGER,", 1)
        require(type_drift_schema != canonical_schema, "runtime schema type drift fixtureを構築できません。")
        with sqlite3.connect(existing_database) as connection:
            connection.executescript(type_drift_schema)
            connection.execute("INSERT INTO queue_metadata(key, value) VALUES('schema_version', '4.0')")
            connection.commit()

        _assert_incompatible_runtime_install_rejected(target, "v4 column type/not-null drift")
        with sqlite3.connect(existing_database) as connection:
            quest_status = next(row for row in connection.execute("PRAGMA table_info(quests)") if row[1] == "status")
        require(quest_status[2] == "INTEGER" and quest_status[3] == 0, "install.py の拒否時はtype/not-null drift DBを変更しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        existing_database = target / ".orchestra/queue/state.sqlite"
        existing_database.parent.mkdir(parents=True, exist_ok=True)
        old_runtime_value = "integration_owner"
        with sqlite3.connect(existing_database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript((ROOT / "template/.agents/orchestra/scripts/queue_schema.sql").read_text(encoding="utf-8"))
            connection.execute("INSERT INTO queue_metadata(key, value) VALUES('schema_version', '4.0')")
            connection.execute(
                "INSERT INTO quests(quest_id, workflow_id, rank, status, payload_json) VALUES(?, ?, ?, ?, ?)",
                ("quest_legacy_value", "workflow_legacy_value", "solo_quest", "active", json.dumps({"control_decision": old_runtime_value})),
            )
            connection.commit()

        _assert_incompatible_runtime_install_rejected(target, "v4 legacy runtime value")
        with sqlite3.connect(existing_database) as connection:
            payload = connection.execute("SELECT payload_json FROM quests WHERE quest_id = 'quest_legacy_value'").fetchone()
        require(payload is not None and json.loads(payload[0]).get("control_decision") == old_runtime_value, "install.py の拒否時は既存 runtime 値を変更しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        _write_duplicate_agents(target)
        _write_duplicate_git_exclude(target)
        existing_database = target / ".orchestra/queue/state.sqlite"
        existing_database.parent.mkdir(parents=True, exist_ok=True)
        existing_database.write_bytes(b"legacy runtime state")
        stale_queue_file = target / ".orchestra/queue/stale.txt"
        stale_queue_file.write_text("stale runtime state\n", encoding="utf-8")
        (target / ".orchestra/dashboard.md").write_text("legacy dashboard\n", encoding="utf-8")
        clean_candidate = target / ".orchestra/skill-candidates/demo/keep/SKILL.md"
        clean_candidate.parent.mkdir(parents=True)
        clean_candidate.write_text("candidate\n", encoding="utf-8")
        valid_external_skill = target / ".agents/skills/valid-external/SKILL.md"
        valid_external_skill.parent.mkdir(parents=True)
        valid_external_skill.write_text(
            "---\nname: valid-external\nmetadata:\n  owner: third-party\n---\n",
            encoding="utf-8",
        )
        invalid_utf8_external_skill = target / ".agents/skills/non-utf8-external/SKILL.md"
        invalid_utf8_external_skill_bytes = b"---\nmetadata:\n  owner: third-party\n---\n\xff\x00preserve\n"
        invalid_utf8_external_skill.parent.mkdir(parents=True)
        invalid_utf8_external_skill.write_bytes(invalid_utf8_external_skill_bytes)
        invalid_utf8_external_asset = target / ".agents/skills/non-utf8-external/resources/blob.bin"
        invalid_utf8_external_asset_bytes = b"\x00\xffexternal-skill-asset\n"
        invalid_utf8_external_asset.parent.mkdir()
        invalid_utf8_external_asset.write_bytes(invalid_utf8_external_asset_bytes)
        legacy_owned_skill = target / ".agents/skills/legacy-owned/SKILL.md"
        legacy_owned_skill.parent.mkdir()
        legacy_owned_skill.write_text(
            "---\nname: legacy-owned\nowner: agent-guild-orchestra\n---\n",
            encoding="utf-8",
        )
        unknown_runtime_file = target / ".orchestra/legacy-runtime.txt"
        unknown_runtime_file.write_text("legacy\n", encoding="utf-8")
        unknown_runtime_child = target / ".orchestra/unknown-runtime/cache.bin"
        unknown_runtime_child.parent.mkdir()
        unknown_runtime_child.write_bytes(b"legacy")

        clean_dry_run = _run_install("--target", target, "--mode", "copy", "--clean-install", "--dry-run")
        clean_dry_run_output = clean_dry_run.stdout + clean_dry_run.stderr
        require(clean_dry_run.returncode == 0, "install.py --clean-install --dry-run が失敗しました: " + clean_dry_run.stderr)
        require("remove" in clean_dry_run_output and ".orchestra" in clean_dry_run_output, "install.py --clean-install --dry-run は runtime state の削除計画を表示してください。")
        require("init sqlite" in clean_dry_run_output and "state.sqlite" in clean_dry_run_output, "install.py --clean-install --dry-run は runtime DB 再初期化計画を表示してください。")
        require("既存状態を保持" not in clean_dry_run_output, "install.py --clean-install --dry-run は既存 runtime state を保持すると表示しないでください。")

        clean_without_backup = _run_install("--target", target, "--mode", "copy", "--clean-install")
        clean_without_backup_output = clean_without_backup.stdout + clean_without_backup.stderr
        require(clean_without_backup.returncode != 0, "install.py --clean-install は backup なしの非 dry-run を拒否してください。")
        require("--backup" in clean_without_backup_output and "--allow-clean-install-without-backup" in clean_without_backup_output, "install.py --clean-install の拒否 message は backup と clean-install 専用 escape flag を示してください。")
        require(stale_queue_file.exists() and legacy_owned_skill.exists(), "install.py --clean-install の拒否時は既存導入物を削除しないでください。")

        clean_installed = _run_install("--target", target, "--mode", "copy", "--clean-install", "--backup")
        clean_installed_output = clean_installed.stdout + clean_installed.stderr
        require(clean_installed.returncode == 0, "install.py --clean-install --backup smoke が失敗しました: " + clean_installed.stderr)
        require(
            "Skill の所有者情報を UTF-8 として読めないため保持します: .agents/skills/non-utf8-external" in clean_installed_output,
            "install.py --clean-install は非UTF-8 external Skillの保全理由だけを警告してください。",
        )
        require(
            "preserve" not in clean_installed_output and "\x00" not in clean_installed_output,
            "install.py --clean-install の非UTF-8 Skill警告は内容やraw bytesを出力しないでください。",
        )
        _assert_installed_surface(target, {"valid-external", "non-utf8-external"})
        _assert_agents_block_idempotent(target, expect_update_position=False)
        _assert_git_exclude_block_idempotent(target, expect_update_position=False)
        require(clean_candidate.read_text(encoding="utf-8") == "candidate\n", "install.py --clean-install は runtime sibling candidate を保持してください。")
        require(valid_external_skill.exists(), "install.py --clean-install はvalid external owner Skillを保持してください。")
        require(
            invalid_utf8_external_skill.read_bytes() == invalid_utf8_external_skill_bytes
            and invalid_utf8_external_asset.read_bytes() == invalid_utf8_external_asset_bytes,
            "install.py --clean-install は非UTF-8 external Skill directoryの全内容をbyte-for-byte保持してください。",
        )
        require(not legacy_owned_skill.exists(), "install.py --clean-install はvalid legacy orchestra owner Skillを除去してください。")
        require(not unknown_runtime_file.exists() and not unknown_runtime_child.exists(), "install.py --clean-install は skill-candidates 以外の未知runtime siblingを除去してください。")
        require(not stale_queue_file.exists(), "install.py --clean-install は既存queue内容を除去して初期化してください。")
        require((target / ".orchestra/dashboard.md").read_bytes() == (ROOT / "template/.agents/orchestra/dashboard.md").read_bytes(), "install.py --clean-install はdashboardを初期状態へ戻してください。")
        backups = sorted(path for path in (target / BACKUP_DIRECTORY).iterdir() if path.is_dir())
        require(backups and any((backup / ".orchestra/queue/stale.txt").exists() for backup in backups), "install.py --clean-install --backup は削除前の runtime evidence を退避してください。")

        opt_out_queue_file = target / ".orchestra/queue/opt-out-stale.txt"
        opt_out_queue_file.write_text("opt-out runtime state\n", encoding="utf-8")
        backup_count = len(backups)
        clean_with_escape = _run_install("--target", target, "--mode", "copy", "--clean-install", "--allow-clean-install-without-backup")
        require(clean_with_escape.returncode == 0, "install.py --clean-install --allow-clean-install-without-backup smoke が失敗しました: " + clean_with_escape.stderr)
        require(not opt_out_queue_file.exists(), "install.py --clean-install の明示的 backup なし escape hatch は既存 runtime state を初期化してください。")
        require(len([path for path in (target / BACKUP_DIRECTORY).iterdir() if path.is_dir()]) == backup_count, "install.py --clean-install の明示的 backup なし escape hatch は新規 backup を作成しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "guild"
        installed = _run_install("--target", target, "--mode", "copy")
        require(installed.returncode == 0, "install.py の reset-runtime 事前 install が失敗しました: " + installed.stderr)
        stale_queue_file = target / ".orchestra/queue/stale.txt"
        stale_queue_file.write_text("stale runtime state\n", encoding="utf-8")
        dashboard = target / ".orchestra/dashboard.md"
        dashboard.write_text("edited dashboard\n", encoding="utf-8")

        reset_dry_run = _run_install("--target", target, "--mode", "copy", "--reset-runtime", "--dry-run")
        reset_dry_run_output = reset_dry_run.stdout + reset_dry_run.stderr
        require(reset_dry_run.returncode == 0, "install.py --reset-runtime --dry-run が失敗しました: " + reset_dry_run.stderr)
        require("remove" in reset_dry_run_output and ".orchestra/queue" in reset_dry_run_output, "install.py --reset-runtime --dry-run は runtime queue の削除計画を表示してください。")
        require("remove" in reset_dry_run_output and "dashboard.md" in reset_dry_run_output, "install.py --reset-runtime --dry-run は dashboard の削除計画を表示してください。")
        require("init sqlite" in reset_dry_run_output and "state.sqlite" in reset_dry_run_output, "install.py --reset-runtime --dry-run は runtime DB 再初期化計画を表示してください。")
        require(stale_queue_file.exists(), "install.py --reset-runtime --dry-run は runtime queue を削除しないでください。")
        require(dashboard.read_text(encoding="utf-8") == "edited dashboard\n", "install.py --reset-runtime --dry-run は dashboard を変更しないでください。")

        reset_without_backup = _run_install("--target", target, "--mode", "copy", "--reset-runtime")
        reset_without_backup_output = reset_without_backup.stdout + reset_without_backup.stderr
        require(reset_without_backup.returncode != 0, "install.py --reset-runtime は backup なしの非 dry-run を拒否してください。")
        require("--backup" in reset_without_backup_output and "--allow-reset-runtime-without-backup" in reset_without_backup_output, "install.py --reset-runtime の拒否 message は backup と escape flag を示してください。")
        require(stale_queue_file.exists(), "install.py --reset-runtime の拒否時は runtime queue を削除しないでください。")

        reset_with_backup = _run_install("--target", target, "--mode", "copy", "--reset-runtime", "--backup")
        require(reset_with_backup.returncode == 0, "install.py --reset-runtime --backup smoke が失敗しました: " + reset_with_backup.stderr)
        _assert_installed_surface(target)
        require(not stale_queue_file.exists(), "install.py --reset-runtime --backup は古い runtime queue 内容を削除してください。")
        backup_root = target / BACKUP_DIRECTORY
        backups = sorted(path for path in backup_root.iterdir() if path.is_dir())
        require(backups and any((backup / ".orchestra/queue/stale.txt").exists() for backup in backups), "install.py --reset-runtime --backup は既存 runtime state を退避してください。")

        stale_queue_file = target / ".orchestra/queue/stale.txt"
        stale_queue_file.write_text("stale runtime state again\n", encoding="utf-8")
        reset_with_escape = _run_install("--target", target, "--mode", "copy", "--reset-runtime", "--allow-reset-runtime-without-backup")
        require(reset_with_escape.returncode == 0, "install.py --reset-runtime --allow-reset-runtime-without-backup smoke が失敗しました: " + reset_with_escape.stderr)
        _assert_installed_surface(target)
        require(not stale_queue_file.exists(), "install.py --reset-runtime --allow-reset-runtime-without-backup は古い runtime queue 内容を削除してください。")

    for rel in (".agents", ".codex", ".orchestra"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "guild"
            target.mkdir()
            external = root / "external"
            external.mkdir()
            (target / rel).symlink_to(external, target_is_directory=True)

            backup_symlink = _run_install("--target", target, "--mode", "copy", "--backup")
            output = backup_symlink.stdout + backup_symlink.stderr
            require(backup_symlink.returncode != 0 and "symlink" in output and rel in output, f"install.py --backup は {rel} symlink を追跡せず拒否してください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external = root / "external.txt"
        external.write_text("external\n", encoding="utf-8")
        link = target / ".orchestra/queue/external-link"
        link.parent.mkdir(parents=True)
        link.symlink_to(external)

        backup_nested_symlink = _run_install("--target", target, "--mode", "copy", "--backup")
        output = backup_nested_symlink.stdout + backup_nested_symlink.stderr
        require(backup_nested_symlink.returncode != 0 and "symlink" in output and "external-link" in output, "install.py --backup は backup 対象配下の symlink を追跡せず拒否してください。")

    for symlink_parent_rel in (".git", ".git/info"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "guild"
            target.mkdir()
            external_git = root / "external-git"
            external_exclude = external_git / "info" / "exclude"
            external_exclude.parent.mkdir(parents=True)
            external_exclude.write_text("external exclude\n", encoding="utf-8")
            link = target / symlink_parent_rel
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(external_git if symlink_parent_rel == ".git" else external_git / "info", target_is_directory=True)

            backup_git_parent_symlink = _run_install("--target", target, "--mode", "copy", "--backup")
            output = backup_git_parent_symlink.stdout + backup_git_parent_symlink.stderr
            require(backup_git_parent_symlink.returncode != 0 and "symlink" in output and symlink_parent_rel in output, f"install.py --backup は {symlink_parent_rel} symlink parent 経由で .git/info/exclude を読まないでください。")
            require(external_exclude.read_text(encoding="utf-8") == "external exclude\n", f"install.py --backup は {symlink_parent_rel} symlink parent の外部 exclude を変更しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external = root / "external-readme.md"
        external.write_text("external readme\n", encoding="utf-8")
        link = target / ".agents/orchestra/README.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(external)

        destination_symlink = _run_install("--target", target, "--mode", "copy")
        output = destination_symlink.stdout + destination_symlink.stderr
        require(destination_symlink.returncode != 0 and "symlink" in output and ".agents/orchestra/README.md" in output, "install.py は通常 install の destination file symlink を拒否してください。")
        require(external.read_text(encoding="utf-8") == "external readme\n", "install.py は destination symlink の外部実体を書き換えないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external = root / "external-codex"
        external.mkdir()
        (target / ".codex").symlink_to(external, target_is_directory=True)

        destination_parent_symlink = _run_install("--target", target, "--mode", "copy")
        output = destination_parent_symlink.stdout + destination_parent_symlink.stderr
        require(destination_parent_symlink.returncode != 0 and "symlink" in output and ".codex" in output, "install.py は通常 install の destination parent symlink を拒否してください。")
        require(not (external / "config.toml").exists(), "install.py は destination parent symlink の外部 dir へ書き込まないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external = root / "external-agents.md"
        external.write_text("external agents\n", encoding="utf-8")
        (target / "AGENTS.md").symlink_to(external)

        destination_agents_symlink = _run_install("--target", target, "--mode", "copy")
        output = destination_agents_symlink.stdout + destination_agents_symlink.stderr
        require(destination_agents_symlink.returncode != 0 and "symlink" in output and "AGENTS.md" in output, "install.py は AGENTS.md symlink を読まず書かず拒否してください。")
        require(external.read_text(encoding="utf-8") == "external agents\n", "install.py は AGENTS.md symlink の外部実体を書き換えないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external = root / "external-agents"
        sentinel = external / "orchestra" / "keep.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("keep\n", encoding="utf-8")
        (target / ".agents").symlink_to(external, target_is_directory=True)

        clean_parent_symlink = _run_install("--target", target, "--mode", "copy", "--clean-install", "--allow-clean-install-without-backup")
        output = clean_parent_symlink.stdout + clean_parent_symlink.stderr
        require(clean_parent_symlink.returncode != 0 and "symlink" in output and ".agents" in output, "install.py --clean-install は symlink parent 経由の削除を拒否してください。")
        require(sentinel.read_text(encoding="utf-8") == "keep\n", "install.py --clean-install は symlink parent の外部 dir を削除しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external = root / "external-orchestra"
        sentinel = external / "queue" / "stale.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("stale\n", encoding="utf-8")
        (target / ".orchestra").symlink_to(external, target_is_directory=True)

        reset_parent_symlink = _run_install("--target", target, "--mode", "copy", "--reset-runtime", "--allow-reset-runtime-without-backup")
        output = reset_parent_symlink.stdout + reset_parent_symlink.stderr
        require(reset_parent_symlink.returncode != 0 and "symlink" in output and ".orchestra" in output, "install.py --reset-runtime は symlink parent 経由の runtime 削除を拒否してください。")
        require(sentinel.read_text(encoding="utf-8") == "stale\n", "install.py --reset-runtime は symlink parent の外部 runtime を削除しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        existing = target / ".agents" / "orchestra" / "README.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("existing\n", encoding="utf-8")
        external_backups = root / "external-backups"
        external_backups.mkdir()
        (target / BACKUP_DIRECTORY).symlink_to(external_backups, target_is_directory=True)

        backup_destination_symlink = _run_install("--target", target, "--mode", "copy", "--backup")
        output = backup_destination_symlink.stdout + backup_destination_symlink.stderr
        require(backup_destination_symlink.returncode != 0 and "symlink" in output and BACKUP_DIRECTORY in output, "install.py --backup は backup destination symlink を拒否してください。")
        require(not any(external_backups.iterdir()), "install.py --backup は backup destination symlink の外部 dir へ書き込まないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external_queue = root / "external-queue"
        external_queue.mkdir()
        queue_link = target / ".orchestra" / "queue"
        queue_link.parent.mkdir(parents=True)
        queue_link.symlink_to(external_queue, target_is_directory=True)

        runtime_parent_symlink = _run_install("--target", target, "--mode", "copy")
        output = runtime_parent_symlink.stdout + runtime_parent_symlink.stderr
        require(runtime_parent_symlink.returncode != 0 and "symlink" in output and ".orchestra/queue" in output, "install.py は SQLite runtime parent symlink を拒否してください。")
        require(not (external_queue / "state.sqlite").exists(), "install.py は SQLite runtime parent symlink の外部 dir へ DB を作成しないでください。")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "guild"
        target.mkdir()
        external_database = root / "external-state.sqlite"
        external_database.write_text("external db\n", encoding="utf-8")
        state_link = target / ".orchestra" / "queue" / "state.sqlite"
        state_link.parent.mkdir(parents=True)
        state_link.symlink_to(external_database)

        runtime_file_symlink = _run_install("--target", target, "--mode", "copy")
        output = runtime_file_symlink.stdout + runtime_file_symlink.stderr
        require(runtime_file_symlink.returncode != 0 and "symlink" in output and "state.sqlite" in output, "install.py は SQLite runtime file symlink を読まず書かず拒否してください。")
        require(external_database.read_text(encoding="utf-8") == "external db\n", "install.py は SQLite runtime file symlink の外部実体を変更しないでください。")

    def run_with_mutated_source(mutation: str, mutate: object) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "template"
            target = Path(tmp) / "guild"
            shutil.copytree(ROOT / "template", source)
            mutate(source)
            result = _run_install("--target", target, "--source", source, "--allow-non-default-source", "--mode", "copy", "--dry-run")
            require(result.returncode != 0, f"install.py は不正な source template を拒否してください: {mutation}")
            return result

    def run_with_allowed_mutated_source(mutation: str, mutate: object) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "template"
            target = Path(tmp) / "guild"
            shutil.copytree(ROOT / "template", source)
            mutate(source)
            result = _run_install("--target", target, "--source", source, "--allow-non-default-source", "--mode", "copy", "--dry-run")
            require(result.returncode == 0, f"install.py は許可される source template を拒否しないでください: {mutation}: " + result.stderr)
            return result

    missing_sage = run_with_mutated_source("missing sage.toml", lambda source: (source / ".codex/agents/sage.toml").unlink())
    require("sage.toml" in (missing_sage.stdout + missing_sage.stderr), "install.py の sage 不足拒否 message は sage.toml を示してください。")

    missing_examiner = run_with_mutated_source("missing examiner.toml", lambda source: (source / ".codex/agents/examiner.toml").unlink())
    require("examiner.toml" in (missing_examiner.stdout + missing_examiner.stderr), "install.py の examiner 不足拒否 message は examiner.toml を示してください。")

    def pin_root_reasoning_effort(source: Path) -> None:
        path = source / ".codex/config.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace('model = "gpt-5.6-sol"', 'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "high"', 1),
            encoding="utf-8",
        )

    pinned_root_effort = run_with_mutated_source("Root reasoning effort pinned", pin_root_reasoning_effort)
    require("reasoning effort" in (pinned_root_effort.stdout + pinned_root_effort.stderr), "install.py preflightはRootのproject-local effort指定を拒否してください。")

    def drift_root_context_window(source: Path) -> None:
        path = source / ".codex/config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("model_context_window = 1_050_000", "model_context_window = 400_000", 1), encoding="utf-8")

    invalid_root_context = run_with_mutated_source("Root context window drift", drift_root_context_window)
    require(
        "model_context_window" in (invalid_root_context.stdout + invalid_root_context.stderr)
        and "1050000" in (invalid_root_context.stdout + invalid_root_context.stderr),
        "install.py preflightはRootのfull supported context window driftを拒否してください。",
    )

    def drift_subagent_compaction(source: Path) -> None:
        path = source / ".codex/agents/adventurer.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("model_auto_compact_token_limit = 200_000", "model_auto_compact_token_limit = 400_000", 1), encoding="utf-8")

    invalid_subagent_compaction = run_with_mutated_source("subagent compaction drift", drift_subagent_compaction)
    require(
        "model_auto_compact_token_limit" in (invalid_subagent_compaction.stdout + invalid_subagent_compaction.stderr)
        and "200000" in (invalid_subagent_compaction.stdout + invalid_subagent_compaction.stderr),
        "install.py preflightはsubagent early-compaction driftを拒否してください。",
    )

    def downgrade_settings_version(source: Path) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace('version: "5.0"', 'version: "4.0"', 1), encoding="utf-8")

    old_settings_contract = run_with_mutated_source("settings version 4.0", downgrade_settings_version)
    require("settings.yaml.version" in (old_settings_contract.stdout + old_settings_contract.stderr) and "5.0" in (old_settings_contract.stdout + old_settings_contract.stderr), "install.py preflightは旧settings 4.0を拒否してください。")

    def disable_root_control_plane_contract(source: Path) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("  control_plane_only: true", "  control_plane_only: false", 1), encoding="utf-8")

    stale_root_contract = run_with_mutated_source("Root direct work fallback", disable_root_control_plane_contract)
    require("control-plane" in (stale_root_contract.stdout + stale_root_contract.stderr), "install.py preflightはcoordination-onlyでないRoot contractを拒否してください。")

    def drift_settings_adventurer_pair(source: Path) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        old = "    adventurer: {model: gpt-5.6-luna, model_reasoning_effort: max}"
        new = "    adventurer: {model: gpt-5.6-sol, model_reasoning_effort: max}"
        path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")

    stale_model_policy = run_with_mutated_source("settings adventurer pair drift", drift_settings_adventurer_pair)
    require("adventurer" in (stale_model_policy.stdout + stale_model_policy.stderr) and "gpt-5.6-luna" in (stale_model_policy.stdout + stale_model_policy.stderr), "install.py preflightはsettings model pair driftを拒否してください。")

    def drift_queue_schema_contract(source: Path) -> None:
        path = source / ".agents/orchestra/scripts/queue_schema.sql"
        path.write_text(path.read_text(encoding="utf-8").replace("  status TEXT NOT NULL,", "  status INTEGER,", 1), encoding="utf-8")

    stale_queue_schema = run_with_mutated_source("queue schema type drift", drift_queue_schema_contract)
    require("queue_schema.sql SHA-256" in (stale_queue_schema.stdout + stale_queue_schema.stderr), "install.py preflightはcanonical queue schema driftを拒否してください。")

    def enable_focus_recursive_agents(source: Path) -> None:
        path = source / ".codex/agents/examiner.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("multi_agent = false", "multi_agent = true"), encoding="utf-8")

    recursive_focus = run_with_mutated_source("examiner multi-agent enabled", enable_focus_recursive_agents)
    require("multi_agent" in (recursive_focus.stdout + recursive_focus.stderr), "install.py は examiner の recursive multi-agent capability を拒否してください。")

    def disable_inquisitor_nested_agents(source: Path) -> None:
        path = source / ".codex/agents/inquisitor.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("multi_agent = true", "multi_agent = false"), encoding="utf-8")

    missing_nested_capability = run_with_mutated_source("inquisitor multi-agent disabled", disable_inquisitor_nested_agents)
    require("inquisitor" in (missing_nested_capability.stdout + missing_nested_capability.stderr) and "multi_agent" in (missing_nested_capability.stdout + missing_nested_capability.stderr), "install.py は inquisitor の nested capability 不足を拒否してください。")

    def enable_unauthorized_nested_agents(source: Path) -> None:
        path = source / ".codex/agents/adventurer.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("multi_agent = false", "multi_agent = true"), encoding="utf-8")

    unauthorized_nested_capability = run_with_mutated_source("adventurer multi-agent enabled", enable_unauthorized_nested_agents)
    require("adventurer" in (unauthorized_nested_capability.stdout + unauthorized_nested_capability.stderr) and "multi_agent" in (unauthorized_nested_capability.stdout + unauthorized_nested_capability.stderr), "install.py はinquisitor以外のnested capabilityを拒否してください。")

    def set_max_depth(source: Path, depth: int) -> None:
        path = source / ".codex/config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("max_depth = 2", f"max_depth = {depth}"), encoding="utf-8")

    for invalid_depth in (1, 3):
        invalid_max_depth = run_with_mutated_source(f"max_depth {invalid_depth}", lambda source, depth=invalid_depth: set_max_depth(source, depth))
        require("max_depth=2" in (invalid_max_depth.stdout + invalid_max_depth.stderr), f"install.py は max_depth={invalid_depth} を拒否してください。")

    def restore_legacy_job_runtime(source: Path) -> None:
        path = source / ".codex/config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("job_max_runtime_seconds = 2400", "job_max_runtime_seconds = 1800"), encoding="utf-8")

    invalid_job_runtime = run_with_mutated_source("legacy job runtime 1800", restore_legacy_job_runtime)
    require("job_max_runtime_seconds" in (invalid_job_runtime.stdout + invalid_job_runtime.stderr) and "2400" in (invalid_job_runtime.stdout + invalid_job_runtime.stderr), "install.py は2400秒以外のjob runtimeを拒否してください。")

    def set_adventurer_parallelism(source: Path, value: int) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        text = path.read_text(encoding="utf-8")
        marker = "  adventurer:\n"
        before, adventurer = text.split(marker, 1)
        adventurer = adventurer.replace("    max_parallel: 32", f"    max_parallel: {value}", 1)
        path.write_text(before + marker + adventurer, encoding="utf-8")

    for invalid_parallel in (31, 48, 64):
        invalid_adventurer = run_with_mutated_source(
            f"adventurer max_parallel {invalid_parallel}",
            lambda source, value=invalid_parallel: set_adventurer_parallelism(source, value),
        )
        require(
            "adventurer.max_parallel" in (invalid_adventurer.stdout + invalid_adventurer.stderr)
            and "32" in (invalid_adventurer.stdout + invalid_adventurer.stderr),
            f"install.py は adventurer.max_parallel={invalid_parallel} を拒否してください。",
        )

    destroyed_headroom = run_with_mutated_source(
        "destroy unallocated headroom",
        lambda source: set_adventurer_parallelism(source, 33),
    )
    require(
        "adventurer.max_parallel" in (destroyed_headroom.stdout + destroyed_headroom.stderr)
        and "32" in (destroyed_headroom.stdout + destroyed_headroom.stderr),
        "install.py は adventurer を増やして未割当headroomを破壊する source を拒否してください。",
    )

    def reallocate_reserved_role(source: Path) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        text = path.read_text(encoding="utf-8")
        text = text.replace("  cartographer:\n    role: mapmaking_specialist\n    max_parallel: 2", "  cartographer:\n    role: mapmaking_specialist\n    max_parallel: 3", 1)
        text = text.replace("  adventurer:\n    role: bounded_implementation_owner\n    max_parallel: 32", "  adventurer:\n    role: bounded_implementation_owner\n    max_parallel: 31", 1)
        path.write_text(text, encoding="utf-8")

    reallocated_roles = run_with_mutated_source("reallocated role reservations", reallocate_reserved_role)
    require(
        "cartographer.max_parallel" in (reallocated_roles.stdout + reallocated_roles.stderr),
        "install.py は合計48でもrole別配分を変えた source を拒否してください。",
    )

    def remove_adventurer_parallelism(source: Path) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        text = path.read_text(encoding="utf-8")
        marker = "  adventurer:\n"
        before, adventurer = text.split(marker, 1)
        adventurer = adventurer.replace("    max_parallel: 32\n", "", 1)
        path.write_text(before + marker + adventurer, encoding="utf-8")

    missing_adventurer_limit = run_with_mutated_source("missing adventurer max_parallel", remove_adventurer_parallelism)
    require(
        "adventurer.max_parallel" in (missing_adventurer_limit.stdout + missing_adventurer_limit.stderr),
        "install.py は adventurer.max_parallel が明示されていない source を拒否してください。",
    )

    def enable_focus_decision_authority(source: Path) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        text = path.read_text(encoding="utf-8")
        marker = "  examiner:\n"
        before, focus = text.split(marker, 1)
        focus = focus.replace("    decision_authority: false", "    decision_authority: true", 1)
        path.write_text(before + marker + focus, encoding="utf-8")

    authority_focus = run_with_mutated_source("examiner authority expanded", enable_focus_decision_authority)
    require("examiner" in (authority_focus.stdout + authority_focus.stderr), "install.py は examiner settings authority 拡張を拒否してください。")

    def add_focus_caller(source: Path) -> None:
        path = source / ".agents/orchestra/config/settings.yaml"
        text = path.read_text(encoding="utf-8")
        marker = "  examiner:\n"
        before, focus = text.split(marker, 1)
        focus = focus.replace("    allowed_callers:\n      - inquisitor", "    allowed_callers:\n      - inquisitor\n      - root", 1)
        path.write_text(before + marker + focus, encoding="utf-8")

    caller_focus = run_with_mutated_source("examiner caller expanded", add_focus_caller)
    require("allowed_callers" in (caller_focus.stdout + caller_focus.stderr), "install.py は examiner caller 拡張を拒否してください。")

    missing_controller = run_with_mutated_source("missing warden.toml", lambda source: (source / ".codex/agents/warden.toml").unlink())
    require("warden.toml" in (missing_controller.stdout + missing_controller.stderr), "install.py の warden 不足拒否 message は warden.toml を示してください。")

    missing_quest_awareness_skill = run_with_mutated_source("missing quest-awareness-loop skill", lambda source: shutil.rmtree(source / ".agents/skills/quest-awareness-loop"))
    require("quest-awareness-loop" in (missing_quest_awareness_skill.stdout + missing_quest_awareness_skill.stderr), "install.py の quest-awareness-loop 不足拒否 message は skill 名を示してください。")

    def add_empty_unexpected_skill_directory(source: Path) -> None:
        (source / ".agents/skills/empty-skill/agents").mkdir(parents=True)

    run_with_allowed_mutated_source("empty unexpected Skill directory", add_empty_unexpected_skill_directory)

    def add_unexpected_skill_file(source: Path) -> None:
        path = source / ".agents/skills/unexpected-skill/README.md"
        path.parent.mkdir()
        path.write_text("material\n", encoding="utf-8")

    unexpected_skill_file = run_with_mutated_source("通常fileを含む未知Skill directory", add_unexpected_skill_file)
    require(
        "unexpected: unexpected-skill" in (unexpected_skill_file.stdout + unexpected_skill_file.stderr),
        "install.py は file を含む unexpected Skill directory を明示的に拒否してください。",
    )

    def add_unexpected_skill_manifest(source: Path) -> None:
        path = source / ".agents/skills/unexpected-manifest/SKILL.md"
        path.parent.mkdir()
        path.write_text("---\nname: unexpected-manifest\n---\n", encoding="utf-8")

    unexpected_skill_manifest = run_with_mutated_source("SKILL.mdを含む未知Skill directory", add_unexpected_skill_manifest)
    require(
        "unexpected: unexpected-manifest" in (unexpected_skill_manifest.stdout + unexpected_skill_manifest.stderr),
        "install.py は SKILL.md を含む unexpected Skill directory を明示的に拒否してください。",
    )

    def add_unexpected_skill_symlink(source: Path) -> None:
        path = source / ".agents/skills/unexpected-symlink/linked-config.toml"
        path.parent.mkdir()
        path.symlink_to(source / ".codex/config.toml")

    unexpected_skill_symlink = run_with_mutated_source("symlinkを含む未知Skill directory", add_unexpected_skill_symlink)
    require(
        "symlink" in (unexpected_skill_symlink.stdout + unexpected_skill_symlink.stderr),
        "install.py は symlink を含む unexpected Skill directory を拒否してください。",
    )

    missing_skill_openai = run_with_mutated_source(
        "missing skill openai metadata",
        lambda source: (source / ".agents/skills/use-guild-workflow/agents/openai.yaml").unlink(),
    )
    require("openai.yaml" in (missing_skill_openai.stdout + missing_skill_openai.stderr), "install.py の Skill openai metadata 不足拒否 message は openai.yaml を示してください。")

    missing_vscode_helper = run_with_mutated_source(
        "missing VS Code launch helper",
        lambda source: (source / VSCODE_HELPER_REL).unlink(),
    )
    require(
        "open_repositories_in_vscode.py" in (missing_vscode_helper.stdout + missing_vscode_helper.stderr),
        "install.py の VS Code launch helper 不足拒否 message は helper file を示してください。",
    )

    missing_runtime_doc = run_with_mutated_source("missing runtime memory doc", lambda source: (source / ".agents/orchestra/docs/agent-memory.md").unlink())
    require("agent-memory.md" in (missing_runtime_doc.stdout + missing_runtime_doc.stderr), "install.py の runtime docs 不足拒否 message は agent-memory.md を示してください。")

    missing_dashboard = run_with_mutated_source("missing dashboard", lambda source: (source / ".agents/orchestra/dashboard.md").unlink())
    require("dashboard.md" in (missing_dashboard.stdout + missing_dashboard.stderr), "install.py の dashboard 不足拒否 message は dashboard.md を示してください。")

    missing_queue_schema = run_with_mutated_source("missing queue schema", lambda source: (source / ".agents/orchestra/scripts/queue_schema.sql").unlink())
    require("queue_schema.sql" in (missing_queue_schema.stdout + missing_queue_schema.stderr), "install.py の queue schema 不足拒否 message は queue_schema.sql を示してください。")

    missing_snapshot_helper = run_with_mutated_source("missing snapshot digest helper", lambda source: (source / ".agents/orchestra/scripts/snapshot_digest.py").unlink())
    require("snapshot_digest.py" in (missing_snapshot_helper.stdout + missing_snapshot_helper.stderr), "install.py の snapshot helper 不足拒否 message は snapshot_digest.py を示してください。")

    missing_stop_hook = run_with_mutated_source("missing stop hook", lambda source: (source / ".codex/hooks/stop_quality_gate.sh").unlink())
    require("stop_quality_gate.sh" in (missing_stop_hook.stdout + missing_stop_hook.stderr), "install.py の hook 不足拒否 message は stop_quality_gate.sh を示してください。")

    missing_queue_template = run_with_mutated_source("missing queue template", lambda source: (source / ".agents/orchestra/queue/templates/inquisitor_trial.yaml").unlink())
    require("inquisitor_trial.yaml" in (missing_queue_template.stdout + missing_queue_template.stderr), "install.py の queue template 不足拒否 message は inquisitor_trial.yaml を示してください。")

    missing_warden_assignment = run_with_mutated_source("missing warden assignment template", lambda source: (source / ".agents/orchestra/queue/templates/warden_assignment.yaml").unlink())
    require("warden_assignment.yaml" in (missing_warden_assignment.stdout + missing_warden_assignment.stderr), "install.py の warden assignment template 不足拒否 message は warden_assignment.yaml を示してください。")

    missing_focus_assignment = run_with_mutated_source("missing examiner assignment template", lambda source: (source / ".agents/orchestra/queue/templates/examiner_assignment.yaml").unlink())
    require("examiner_assignment.yaml" in (missing_focus_assignment.stdout + missing_focus_assignment.stderr), "install.py の examiner assignment template 不足拒否 message は examiner_assignment.yaml を示してください。")

    missing_inbox_script = run_with_mutated_source("missing inbox helper", lambda source: (source / ".agents/orchestra/scripts/inbox_write.sh").unlink())
    require("inbox_write.sh" in (missing_inbox_script.stdout + missing_inbox_script.stderr), "install.py の inbox helper 不足拒否 message は inbox_write.sh を示してください。")

    for role in READ_ONLY_AGENT_ROLES:
        def make_role_writable(source: Path, role: str = role) -> None:
            path = source / ".codex" / "agents" / f"{role}.toml"
            path.write_text(path.read_text(encoding="utf-8").replace('sandbox_mode = "read-only"', 'sandbox_mode = "workspace-write"'), encoding="utf-8")

        writable_role = run_with_mutated_source(f"{role} not read-only", make_role_writable)
        output = writable_role.stdout + writable_role.stderr
        require("sandbox_mode" in output and f"{role}.toml" in output, f"install.py の {role} sandbox 拒否 message は sandbox_mode と role file を示してください。")

    def add_source_symlink(source: Path) -> None:
        (source / ".codex" / "secrets").symlink_to(source / ".codex" / "config.toml")

    source_symlink = run_with_mutated_source("source symlink", add_source_symlink)
    require("symlink" in (source_symlink.stdout + source_symlink.stderr), "install.py は source symlink を拒否してください。")

    def add_mcp_path(source: Path) -> None:
        path = source / ".mcp" / "server.json"
        path.parent.mkdir()
        path.write_text("{}", encoding="utf-8")

    mcp_path = run_with_mutated_source("unexpected mcp path", add_mcp_path)
    require(".mcp" in (mcp_path.stdout + mcp_path.stderr), "install.py は source 内の MCP path を拒否してください。")

    def add_git_path(source: Path) -> None:
        path = source / ".git" / "config"
        path.parent.mkdir()
        path.write_text("[core]\n", encoding="utf-8")

    git_path = run_with_mutated_source("unexpected git path", add_git_path)
    require(".git" in (git_path.stdout + git_path.stderr), "install.py は source 内の .git path を拒否してください。")

    def add_runtime_state(source: Path) -> None:
        path = source / ".orchestra" / "queue" / "state.sqlite"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"sqlite")

    runtime_state = run_with_mutated_source("unexpected runtime state", add_runtime_state)
    require(".orchestra" in (runtime_state.stdout + runtime_state.stderr), "install.py は source 内の runtime state を拒否してください。")

    def add_repositories_path(source: Path) -> None:
        path = source / "repositories" / "demo" / "README.md"
        path.parent.mkdir(parents=True)
        path.write_text("# demo\n", encoding="utf-8")

    repositories_path = run_with_mutated_source("unexpected repositories path", add_repositories_path)
    require("repositories" in (repositories_path.stdout + repositories_path.stderr), "install.py は source 内の repositories/ を拒否してください。")

    def disable_source_network(source: Path) -> None:
        path = source / ".codex" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("network_access = true", "network_access = false"), encoding="utf-8")

    source_network = run_with_mutated_source("source network disabled", disable_source_network)
    require("network_access" in (source_network.stdout + source_network.stderr), "install.py は source config の network_access=false を拒否してください。")

    def add_mcp_server_config(source: Path) -> None:
        path = source / ".codex" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + "\n[mcp_servers.example]\ncommand = \"example\"\n", encoding="utf-8")

    mcp_server_config = run_with_mutated_source("source mcp server config", add_mcp_server_config)
    require("MCP" in (mcp_server_config.stdout + mcp_server_config.stderr), "install.py は source config の MCP server を拒否してください。")

    def add_api_key_path(source: Path) -> None:
        path = source / ".codex" / "api_key.txt"
        path.write_text("redacted\n", encoding="utf-8")

    api_key_path = run_with_mutated_source("unexpected api key path", add_api_key_path)
    require("key" in (api_key_path.stdout + api_key_path.stderr), "install.py は source 内の key path を拒否してください。")

    def add_auth_path(source: Path) -> None:
        path = source / ".codex" / "auth.json"
        path.write_text("{}", encoding="utf-8")

    auth_path = run_with_mutated_source("unexpected auth path", add_auth_path)
    require("auth" in (auth_path.stdout + auth_path.stderr), "install.py は source 内の auth path を拒否してください。")

    def add_dot_token_variant(source: Path, name: str) -> None:
        path = source / ".agents" / "orchestra" / name
        path.write_text("redacted\n", encoding="utf-8")

    for name, expected in (
        (".env.local", ".env"),
        (".envrc", ".env"),
        (".env-local", ".env"),
        (".env_local", ".env"),
        (".aws-config", ".aws"),
        ("id_ed25519.bak", "id_ed25519"),
        ("id_rsa.pub", "id_rsa"),
        ("state.sqlite.bak", "state.sqlite"),
    ):
        dot_token_variant = run_with_mutated_source(
            f"unexpected source token variant path {name}",
            lambda source, name=name: add_dot_token_variant(source, name),
        )
        require(expected in (dot_token_variant.stdout + dot_token_variant.stderr), f"install.py は source 内の {name} path を拒否してください。")

    def add_harmless_keyword_substrings(source: Path) -> None:
        docs_dir = source / ".agents" / "orchestra" / "docs"
        (docs_dir / "keyboard-shortcuts.md").write_text("keyboard\n", encoding="utf-8")
        (docs_dir / "authoring-guide.md").write_text("authoring\n", encoding="utf-8")
        (docs_dir / "environment-guide.md").write_text("environment\n", encoding="utf-8")

    run_with_allowed_mutated_source("harmless keyword substrings", add_harmless_keyword_substrings)

    def add_ssh_key_path(source: Path) -> None:
        path = source / ".codex" / ".ssh" / "id_ed25519"
        path.parent.mkdir()
        path.write_text("redacted\n", encoding="utf-8")

    ssh_key_path = run_with_mutated_source("unexpected ssh key path", add_ssh_key_path)
    require(".ssh" in (ssh_key_path.stdout + ssh_key_path.stderr), "install.py は source 内の SSH key path を拒否してください。")

    def add_pem_path(source: Path) -> None:
        path = source / ".codex" / "private.pem"
        path.write_text("redacted\n", encoding="utf-8")

    pem_path = run_with_mutated_source("unexpected pem path", add_pem_path)
    require("pem" in (pem_path.stdout + pem_path.stderr), "install.py は source 内の pem path を拒否してください。")
