"""Structural validation for the v3 static Codex distribution."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

from .core import ROOT, require


CORE_SKILLS = {
    "design-review",
    "verify-change",
    "local-git-operations",
    "github-publish-change",
    "interactive-browser-research",
}
MAINTAINER_SKILLS = {"orchestra-contract-validation", "orchestra-runtime-security-audit"}
OPTIONAL_SKILLS = {"create-skill-candidate-from-gap", "open-subrepo-in-vscode"}
AGENTS = {
    "adventurer": ("gpt-5.6-luna", "max", "workspace-write"),
    "inquisitor": ("gpt-6-astra", "high", "read-only"),
}


def directories(path: Path) -> set[str]:
    return {
        item.name
        for item in path.iterdir()
        if item.is_dir() and not item.is_symlink() and (item / "SKILL.md").is_file()
    }


def validate_version() -> None:
    require((ROOT / "VERSION").read_text(encoding="utf-8").strip() == "3.0.0", "VERSION must be 3.0.0")


def validate_required_paths() -> None:
    required = [
        "template/AGENTS.md",
        "template/.codex/config.toml",
        "template/.codex/agents/adventurer.toml",
        "template/.codex/agents/inquisitor.toml",
        "template/.agents/orchestra/scripts/snapshot_digest.py",
        "template/.agents/orchestra/scripts/git_guard.py",
    ]
    for rel in required:
        require((ROOT / rel).is_file(), f"required distribution file is missing: {rel}")
    agent_files = {path.stem for path in (ROOT / "template/.codex/agents").glob("*.toml")}
    require(agent_files == set(AGENTS), f"template must contain only Adventurer and Inquisitor agents: {agent_files}")
    require(directories(ROOT / "template/.agents/skills") == CORE_SKILLS, "default skill set is not the v3 core five")
    require(directories(ROOT / "maintainer-skills") == MAINTAINER_SKILLS, "maintainer skill package set is incorrect")
    require(directories(ROOT / "optional-skills") == OPTIONAL_SKILLS, "optional skill package set is incorrect")


def validate_codex_config() -> None:
    with (ROOT / "template/.codex/config.toml").open("rb") as handle:
        config = tomllib.load(handle)
    require(set(config) == {"model", "model_reasoning_effort", "model_context_window", "agents", "features"}, "config must keep only distribution-owned settings")
    require(config.get("model") == "gpt-6-astra", "root model must be gpt-6-astra")
    require(config.get("model_reasoning_effort") == "high", "root effort must default to high")
    require(config.get("model_context_window") == 1_000_000, "model context window must default to 1,000,000 tokens")
    require("model_auto_compact_token_limit" not in config, "v3 must not pin the auto-compact threshold")
    agents_config = config.get("agents")
    require(isinstance(agents_config, dict), "config needs an [agents] table")
    require(set(agents_config) == {"enabled", "max_concurrent_threads_per_session"}, "agents config has unrelated settings")
    require(agents_config.get("enabled") is True, "agents.enabled must be true")
    require(agents_config.get("max_concurrent_threads_per_session") == 2, "max concurrent subagent threads must be 2")
    features_config = config.get("features")
    require(
        isinstance(features_config, dict)
        and features_config == {"multi_agent": True, "context_management": {"experimental_mode": True}},
        "multi-agent and experimental context-management features must be enabled explicitly",
    )
    for name, (model, effort, sandbox) in AGENTS.items():
        path = ROOT / "template/.codex/agents" / f"{name}.toml"
        with path.open("rb") as handle:
            value = tomllib.load(handle)
        require(value.get("name") == name, f"{name}.toml name mismatch")
        require(isinstance(value.get("description"), str) and value["description"].strip(), f"{name}.toml needs description")
        require(isinstance(value.get("developer_instructions"), str) and value["developer_instructions"].strip(), f"{name}.toml needs developer_instructions")
        require(value.get("model") == model and value.get("model_reasoning_effort") == effort, f"{name}.toml model pair mismatch")
        require(value.get("sandbox_mode") == sandbox, f"{name}.toml sandbox mismatch")


def validate_no_retired_runtime() -> None:
    forbidden = [
        "template/.orchestra",
        "template/.agents/orchestra/queue",
        "template/.agents/orchestra/config/settings.yaml",
        "template/.agents/orchestra/docker",
        "template/.codex/hooks.json",
        "template/.codex/hooks",
    ]
    for rel in forbidden:
        path = ROOT / rel
        material = path.is_file() or (path.is_dir() and any(item.is_file() for item in path.rglob("*")))
        require(not material, f"retired runtime remains active: {rel}")
    allowed_scripts = {"snapshot_digest.py", "git_guard.py"}
    scripts = {path.name for path in (ROOT / "template/.agents/orchestra/scripts").iterdir() if path.is_file()}
    require(scripts == allowed_scripts, f"unexpected runtime scripts: {scripts}")


def validate_dependencies() -> None:
    requirements = [line.strip() for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    require(requirements == [], "v3 validation and installer must use the Python standard library")


def validate_manifest_parse() -> None:
    value = json.loads((ROOT / "scripts/model_selection_eval.yaml").read_text(encoding="utf-8"))
    require(value.get("schema") == "agent-guild-model-benchmark-v3", "benchmark manifest schema mismatch")
