"""End-to-end installer tests in disposable Git repositories."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .core import ROOT, require


INSTALLER_PATH = ROOT / "scripts/install.py"


def git(repo: Path, *args: str) -> None:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    require(result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}")


def git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    require(result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def new_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir()
    git(repo, "init", "-q")
    return repo


def run_install(repo: Path, *args: str, source: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(INSTALLER_PATH), "--target", str(repo)]
    if source is not None:
        command += ["--source", str(source), "--allow-non-default-source"]
    command += list(args)
    return subprocess.run(command, text=True, capture_output=True)


def tree(repo: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in repo.rglob("*"):
        if ".git" in path.relative_to(repo).parts or path.is_dir():
            continue
        result[path.relative_to(repo).as_posix()] = path.read_bytes()
    return result


def load_installer() -> Any:
    spec = importlib.util.spec_from_file_location("installer_smoke_target", INSTALLER_PATH)
    require(spec is not None and spec.loader is not None, "cannot load installer module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_distribution(parent: Path) -> Path:
    distribution = parent / "distribution"
    distribution.mkdir()
    shutil.copytree(ROOT / "template", distribution / "template")
    for name in ("maintainer-skills", "optional-skills"):
        source = ROOT / name
        if source.exists():
            shutil.copytree(source, distribution / name)
    return distribution


def validate_install_upgrade_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-guild-install-smoke-") as raw:
        temp = Path(raw)
        repo = new_repo(temp, "fresh")
        (repo / "AGENTS.md").write_text("# User rules\n", encoding="utf-8")

        dry = run_install(repo, "--dry-run", "--with-skill", "create-skill-candidate-from-gap")
        require(dry.returncode == 0, dry.stderr)
        dry_plan = json.loads(dry.stdout)
        require(dry_plan["dry_run"] is True and not (repo / ".codex").exists(), "dry-run changed the target")
        require(any(item["path"].endswith("create-skill-candidate-from-gap/SKILL.md") for item in dry_plan["actions"]), "optional Skill missing from plan")

        first = run_install(repo, "--with-skill", "create-skill-candidate-from-gap")
        require(first.returncode == 0, first.stderr)
        require((repo / "AGENTS.md").read_text(encoding="utf-8").startswith("# User rules"), "installer overwrote user AGENTS content")
        manifest_path = repo / ".agents/orchestra/install-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest["distribution_version"] == "3.0.0", "installed manifest version mismatch")
        require(manifest["selected_skills"] == ["create-skill-candidate-from-gap"], "optional Skill selection was not recorded")
        require(manifest["ownership"] == {".codex/config.toml": "managed"}, "fresh install did not persist managed config ownership")

        stable_path = repo / ".agents/orchestra/README.md"
        stable_before = (stable_path.stat().st_ino, stable_path.stat().st_mtime_ns)
        manifest_before = (manifest_path.read_bytes(), manifest_path.stat().st_ino, manifest_path.stat().st_mtime_ns)
        second = run_install(repo)
        require(second.returncode == 0, second.stderr)
        second_actions = json.loads(second.stdout)["actions"]
        require(all(item["action"] == "keep" for item in second_actions), "idempotent update planned changes")
        require((stable_path.stat().st_ino, stable_path.stat().st_mtime_ns) == stable_before, "no-op install rewrote a kept file")
        require(
            (manifest_path.read_bytes(), manifest_path.stat().st_ino, manifest_path.stat().st_mtime_ns) == manifest_before,
            "no-op install rewrote its manifest",
        )
        no_op_module = load_installer()
        no_op_writes: list[Path] = []

        def record_unexpected_write(path: Path, data: bytes) -> None:
            no_op_writes.append(path)
            raise AssertionError(f"no-op install called write_atomic for {path}")

        no_op_module.write_atomic = record_unexpected_write
        with contextlib.redirect_stdout(io.StringIO()):
            no_op_module.execute(no_op_module.parse_args(["--target", str(repo)]))
        require(not no_op_writes, "no-op install attempted an unplanned write")

        managed_rel = next(
            Path(rel) for rel, record in manifest["files"].items()
            if record["kind"] == "file" and rel.endswith("SKILL.md") and "create-skill-candidate" not in rel
        )
        managed_path = repo / managed_rel
        managed_path.write_text(managed_path.read_text(encoding="utf-8") + "\nlocal note\n", encoding="utf-8")
        preserve = run_install(repo)
        require(preserve.returncode == 0, preserve.stderr)
        preserve_actions = json.loads(preserve.stdout)["actions"]
        require({"action": "preserve-local", "path": managed_rel.as_posix()} in preserve_actions, "local-only managed edit was not preserved")

        distribution = copy_distribution(temp)
        changed_source = distribution / "template" / managed_rel
        changed_source.write_text(changed_source.read_text(encoding="utf-8") + "\ndistribution change\n", encoding="utf-8")
        installer_for_conflict = load_installer()
        agents_path = repo / "AGENTS.md"
        agents_block = installer_for_conflict.extract_block(agents_path.read_text(encoding="utf-8"))
        require(agents_block is not None, "fresh install did not write the managed AGENTS block")
        local_agents_block = f"{installer_for_conflict.AGENTS_START}\nlocal managed block\n{installer_for_conflict.AGENTS_END}"
        agents_path.write_text(
            agents_path.read_text(encoding="utf-8").replace(agents_block, local_agents_block),
            encoding="utf-8",
        )
        distribution_agents = distribution / "template/AGENTS.md"
        distribution_agents_text = distribution_agents.read_text(encoding="utf-8")
        distribution_agents_block = (
            f"{installer_for_conflict.AGENTS_START}\n"
            f"{distribution_agents_text.strip()}\n"
            f"{installer_for_conflict.AGENTS_END}"
        )
        distribution_agents.write_text(
            distribution_agents_block.replace(
                distribution_agents_text.strip(),
                "distribution managed block",
            ) + "\n",
            encoding="utf-8",
        )
        conflict = run_install(repo, source=distribution / "template")
        require(conflict.returncode == 2 and "changed locally and in distribution" in conflict.stderr, "two-sided managed conflict was not rejected")
        require(managed_path.read_text(encoding="utf-8").endswith("local note\n"), "conflict path changed the target")
        require("local managed block" in agents_path.read_text(encoding="utf-8"), "AGENTS conflict changed the target")

        # Manual convergence to the new distribution is adopted for both a
        # regular file and the managed AGENTS block, advancing the baseline.
        converged_file = changed_source.read_bytes()
        managed_path.write_bytes(converged_file)
        converged_agents_block = installer_for_conflict.extract_block(distribution_agents.read_text(encoding="utf-8"))
        require(converged_agents_block is not None, "converged distribution AGENTS block is missing")
        agents_path.write_text(
            agents_path.read_text(encoding="utf-8").replace(local_agents_block, converged_agents_block),
            encoding="utf-8",
        )
        converged = run_install(repo, source=distribution / "template")
        require(converged.returncode == 0, converged.stderr)
        converged_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(
            converged_manifest["files"][managed_rel.as_posix()]["sha256"] == installer_for_conflict.sha256_bytes(converged_file),
            "regular-file convergence did not advance the manifest baseline",
        )
        require(
            converged_manifest["files"]["AGENTS.md"]["sha256"] == installer_for_conflict.sha256_bytes(converged_agents_block.encode()),
            "AGENTS convergence did not advance the manifest baseline",
        )
        converged_noop = run_install(repo, source=distribution / "template")
        require(converged_noop.returncode == 0, converged_noop.stderr)
        require(
            all(item["action"] == "keep" for item in json.loads(converged_noop.stdout)["actions"]),
            "converged install was not idempotent",
        )

        # A third, genuinely divergent edit on both sides must remain a
        # conflict after the new baseline has been adopted.
        managed_path.write_bytes(converged_file + b"\nthird local change\n")
        changed_source.write_bytes(converged_file + b"\nthird distribution change\n")
        third_conflict = run_install(repo, source=distribution / "template")
        require(third_conflict.returncode == 2 and "changed locally and in distribution" in third_conflict.stderr, "third divergent edit was not rejected")
        require(managed_path.read_bytes() == converged_file + b"\nthird local change\n", "third conflict changed the target")
        managed_path.write_bytes(converged_file)
        changed_source.write_bytes(converged_file)

        remove_optional = run_install(repo, "--without-skill", "create-skill-candidate-from-gap")
        require(remove_optional.returncode == 0, remove_optional.stderr)
        require(not (repo / ".agents/skills/create-skill-candidate-from-gap").exists(), "explicit optional Skill removal failed")

        legacy = new_repo(temp, "legacy")
        (legacy / ".agents/orchestra/config").mkdir(parents=True)
        (legacy / ".agents/orchestra/config/settings.yaml").write_text("runtime: v2\n", encoding="utf-8")
        (legacy / ".orchestra/queue").mkdir(parents=True)
        (legacy / ".orchestra/queue/state.sqlite").write_bytes(b"legacy-state")
        (legacy / ".orchestra/user-kept").mkdir()
        (legacy / ".orchestra/user-kept/notes.txt").write_text("not old-installer-managed\n", encoding="utf-8")
        (legacy / ".codex/agents").mkdir(parents=True)
        (legacy / ".codex/agents/adventurer.toml").write_text("locally modified v2 agent\n", encoding="utf-8")
        (legacy / ".codex/custom.toml").write_text("user file\n", encoding="utf-8")
        (legacy / ".agents/skills/refine-design-plan").mkdir(parents=True)
        (legacy / ".agents/skills/refine-design-plan/SKILL.md").write_text("locally modified v2 Skill\n", encoding="utf-8")
        (legacy / ".git/info/exclude").write_text(
            "user-pattern\n# agent-guild-orchestra:start\n.agents/orchestra/\n.orchestra/\n# agent-guild-orchestra:end\n",
            encoding="utf-8",
        )
        (legacy / "AGENTS.md").write_text(
            "user preface\n\n<!-- agent-guild-orchestra:start -->\nold managed block\n<!-- agent-guild-orchestra:end -->\n",
            encoding="utf-8",
        )
        rejected = run_install(legacy)
        require(rejected.returncode == 2 and "--major-upgrade" in rejected.stderr, "implicit v2 upgrade was not rejected")
        legacy_dry = run_install(legacy, "--dry-run", "--major-upgrade")
        require(legacy_dry.returncode == 0 and (legacy / ".orchestra/queue/state.sqlite").exists(), "major-upgrade dry-run changed legacy state")
        legacy_plan = json.loads(legacy_dry.stdout)
        require(".orchestra/queue" in legacy_plan["archive_paths"], "legacy state archive was not planned")
        upgraded = run_install(legacy, "--major-upgrade")
        require(upgraded.returncode == 0, upgraded.stderr)
        archive = Path(json.loads(upgraded.stdout)["archive"])
        require((archive / ".orchestra/queue/state.sqlite").read_bytes() == b"legacy-state", "legacy state was not cold archived")
        require((archive / ".codex/agents/adventurer.toml").read_text(encoding="utf-8") == "locally modified v2 agent\n", "hashless modified v2 file was not archived verbatim")
        require((archive / ".agents/skills/refine-design-plan/SKILL.md").is_file(), "legacy managed Skill was not archived")
        require((archive / ".git/info/exclude").is_file(), "legacy Git exclude was not archived")
        require("old managed block" in (archive / "AGENTS.md").read_text(encoding="utf-8"), "legacy AGENTS block was not archived")
        require(not (legacy / ".orchestra/queue").exists(), "legacy queue stayed active")
        require((legacy / ".orchestra/user-kept/notes.txt").is_file(), "unknown runtime sibling was removed")
        require((legacy / ".codex/custom.toml").is_file(), "unknown Codex sibling was removed")
        upgraded_exclude = (legacy / ".git/info/exclude").read_text(encoding="utf-8")
        require(upgraded_exclude.startswith("user-pattern\n"), "legacy Git exclude user pattern was not preserved")
        require("/.agent-guild-orchestra-archives/" in upgraded_exclude, "cold archive root was not added to local Git exclude")
        require(".agents/orchestra/" not in upgraded_exclude and ".orchestra/" not in upgraded_exclude, "broad legacy Git exclude survived")
        require(".agent-guild-orchestra-archives" not in git_output(legacy, "status", "--porcelain", "--untracked-files=all"), "cold archive appeared in normal Git status")
        require((legacy / "AGENTS.md").read_text(encoding="utf-8").startswith("user preface"), "major upgrade lost AGENTS content outside the managed block")
        exclude_before = (upgraded_exclude, (legacy / ".git/info/exclude").stat().st_ino, (legacy / ".git/info/exclude").stat().st_mtime_ns)
        legacy_noop = run_install(legacy)
        require(legacy_noop.returncode == 0, legacy_noop.stderr)
        require(
            ((legacy / ".git/info/exclude").read_text(encoding="utf-8"), (legacy / ".git/info/exclude").stat().st_ino, (legacy / ".git/info/exclude").stat().st_mtime_ns) == exclude_before,
            "idempotent update rewrote the managed local exclude",
        )

        guild_root = temp / "legacy-guild-root"
        target_repo = guild_root / "repositories" / "app"
        target_repo.mkdir(parents=True)
        git(target_repo, "init", "-q")
        sibling_repo = guild_root / "repositories" / "sibling"
        sibling_repo.mkdir()
        (sibling_repo / "user-code.txt").write_text("keep sibling repository\n", encoding="utf-8")
        (guild_root / ".agents/orchestra/config").mkdir(parents=True)
        (guild_root / ".agents/orchestra/config/settings.yaml").write_text("modified legacy config\n", encoding="utf-8")
        (guild_root / ".orchestra/queue").mkdir(parents=True)
        (guild_root / ".orchestra/queue/state.sqlite").write_bytes(b"parent-legacy-state")
        (guild_root / ".codex").mkdir()
        (guild_root / ".codex/custom.toml").write_text("unmanaged parent config\n", encoding="utf-8")
        (guild_root / "AGENTS.md").write_text(
            "parent user rule\n\n<!-- agent-guild-orchestra:start -->\nlegacy parent contract\n<!-- agent-guild-orchestra:end -->\n",
            encoding="utf-8",
        )
        parent_dry = run_install(
            target_repo,
            "--dry-run", "--major-upgrade", "--legacy-root", str(guild_root),
        )
        require(parent_dry.returncode == 0, parent_dry.stderr)
        parent_plan = json.loads(parent_dry.stdout)
        require(parent_plan["legacy_root"] == str(guild_root.resolve()), "two-root migration did not bind the explicit legacy root")
        require((guild_root / ".orchestra/queue/state.sqlite").exists(), "two-root dry-run changed legacy state")
        parent_upgrade = run_install(
            target_repo,
            "--major-upgrade", "--legacy-root", str(guild_root),
        )
        require(parent_upgrade.returncode == 0, parent_upgrade.stderr)
        parent_archive = Path(json.loads(parent_upgrade.stdout)["archive"])
        require((parent_archive / ".orchestra/queue/state.sqlite").read_bytes() == b"parent-legacy-state", "parent legacy state was not archived")
        require((parent_archive / ".agents/orchestra/config/settings.yaml").read_text(encoding="utf-8") == "modified legacy config\n", "modified parent managed file was not archived")
        require(not (guild_root / ".agents/orchestra").exists() and not (guild_root / ".orchestra/queue").exists(), "parent v2 runtime stayed active")
        require((guild_root / "AGENTS.md").read_text(encoding="utf-8") == "parent user rule\n", "parent AGENTS user text was not preserved")
        require((guild_root / ".codex/custom.toml").is_file(), "unmanaged parent config was removed")
        require((sibling_repo / "user-code.txt").read_text(encoding="utf-8") == "keep sibling repository\n", "sibling repository content changed")
        require((target_repo / ".agents/orchestra/install-manifest.json").is_file(), "v3 was not installed into the explicit child Git root")

        multi_root = temp / "legacy-guild-root-two-children"
        multi_root.mkdir()
        child_one = multi_root / "repositories" / "one"
        child_two = multi_root / "repositories" / "two"
        child_one.mkdir(parents=True)
        child_two.mkdir()
        git(child_one, "init", "-q")
        git(child_two, "init", "-q")
        (child_one / "unmanaged.txt").write_text("child one content\n", encoding="utf-8")
        (child_two / "unmanaged.txt").write_text("child two content\n", encoding="utf-8")
        (multi_root / ".agents/orchestra/config").mkdir(parents=True)
        (multi_root / ".agents/orchestra/config/settings.yaml").write_text("parent v2 config\n", encoding="utf-8")
        (multi_root / ".orchestra/queue").mkdir(parents=True)
        (multi_root / ".orchestra/queue/state.sqlite").write_bytes(b"two-child-parent-state")
        (multi_root / "AGENTS.md").write_text(
            "shared parent rules\n<!-- agent-guild-orchestra:start -->\nold parent block\n<!-- agent-guild-orchestra:end -->\n",
            encoding="utf-8",
        )
        one_first = run_install(child_one)
        two_first = run_install(child_two)
        require(one_first.returncode == 0 and two_first.returncode == 0, f"normal child installs failed before parent cleanup: {one_first.stderr} {two_first.stderr}")
        one_manifest_before_cleanup = (child_one / ".agents/orchestra/install-manifest.json").read_bytes()
        two_manifest_before_cleanup = (child_two / ".agents/orchestra/install-manifest.json").read_bytes()
        cleanup = run_install(child_one, "--major-upgrade", "--legacy-root", str(multi_root))
        require(cleanup.returncode == 0, cleanup.stderr)
        cleanup_plan = json.loads(cleanup.stdout)
        require(any("sibling repository" in step for step in cleanup_plan["next_steps"]), "legacy-root cleanup omitted sibling-repository warning")
        require(any("recursively discover" in step for step in cleanup_plan["next_steps"]), "legacy-root cleanup omitted bounded migration guidance")
        require((child_one / ".agents/orchestra/install-manifest.json").read_bytes() == one_manifest_before_cleanup, "final parent cleanup changed child one manifest")
        require((child_two / ".agents/orchestra/install-manifest.json").read_bytes() == two_manifest_before_cleanup, "final parent cleanup changed child two manifest")
        require((child_one / "unmanaged.txt").read_text(encoding="utf-8") == "child one content\n", "final parent cleanup changed child one unmanaged content")
        require((child_two / "unmanaged.txt").read_text(encoding="utf-8") == "child two content\n", "final parent cleanup changed child two unmanaged content")
        require(not (multi_root / ".agents/orchestra").exists() and not (multi_root / ".orchestra/queue").exists(), "final parent cleanup left v2 parent runtime active")

        collision = new_repo(temp, "collision")
        (collision / ".codex").mkdir()
        (collision / ".codex/config.toml").write_text("user_config = true\n", encoding="utf-8")
        collided = run_install(collision)
        require(collided.returncode == 2 and "unmanaged destination collision" in collided.stderr, "unmanaged collision was overwritten")

        config_owned = new_repo(temp, "config-owned")
        config_path = config_owned / ".codex/config.toml"
        config_path.parent.mkdir()
        user_config = b"# user-owned config\nmodel = \"custom\"\n"
        config_path.write_bytes(user_config)
        config_path.chmod(0o600)
        config_mode_before = config_path.stat().st_mode
        owned = run_install(config_owned, "--config-mode", "user-owned")
        require(owned.returncode == 0, owned.stderr)
        require(config_path.read_bytes() == user_config and config_path.stat().st_mode == config_mode_before, "user-owned config was changed on fresh install")
        owned_manifest_path = config_owned / ".agents/orchestra/install-manifest.json"
        owned_manifest = json.loads(owned_manifest_path.read_text(encoding="utf-8"))
        require(owned_manifest["ownership"] == {".codex/config.toml": "user-owned"}, "user-owned config ownership was not persisted")
        require(".codex/config.toml" not in owned_manifest["files"], "user-owned config was recorded as a managed file")
        owned_next_steps = json.loads(owned.stdout)["next_steps"]
        require(any("model = \"gpt-6-astra\"" in step for step in owned_next_steps), "user-owned next_steps omitted required model setting")
        require(any("[features]" in step and "multi_agent = true" in step for step in owned_next_steps), "user-owned next_steps omitted required multi-agent feature setting")
        owned_again = run_install(config_owned)
        require(owned_again.returncode == 0, owned_again.stderr)
        require(config_path.read_bytes() == user_config and config_path.stat().st_mode == config_mode_before, "no-flag user-owned sync changed config bytes or mode")

        switch_conflict = run_install(config_owned, "--config-mode", "managed")
        require(switch_conflict.returncode == 2 and "unmanaged destination collision" in switch_conflict.stderr, "managed switch overwrote a user-owned config collision")
        require(config_path.read_bytes() == user_config and config_path.stat().st_mode == config_mode_before, "managed collision changed user-owned config")
        managed_config = (ROOT / "template/.codex/config.toml").read_bytes()
        config_path.write_bytes(managed_config)
        config_path.chmod(0o600)
        switch_managed = run_install(config_owned, "--config-mode", "managed")
        require(switch_managed.returncode == 0, switch_managed.stderr)
        managed_manifest = json.loads(owned_manifest_path.read_text(encoding="utf-8"))
        require(managed_manifest["ownership"] == {".codex/config.toml": "managed"}, "managed switch did not persist config ownership")
        require(".codex/config.toml" in managed_manifest["files"], "managed switch did not record config")
        managed_mode = config_path.stat().st_mode
        config_bytes_before_user_switch = config_path.read_bytes()
        switch_user = run_install(config_owned, "--config-mode", "user-owned")
        require(switch_user.returncode == 0, switch_user.stderr)
        require(config_path.read_bytes() == config_bytes_before_user_switch and config_path.stat().st_mode == managed_mode, "managed-to-user-owned switch changed config bytes or mode")
        switched_manifest = json.loads(owned_manifest_path.read_text(encoding="utf-8"))
        require(switched_manifest["ownership"] == {".codex/config.toml": "user-owned"}, "managed-to-user-owned switch lost ownership state")
        require(".codex/config.toml" not in switched_manifest["files"], "managed-to-user-owned switch retained config in files")

        malformed_manifest_repo = new_repo(temp, "malformed-manifest")
        initial = run_install(malformed_manifest_repo)
        require(initial.returncode == 0, initial.stderr)
        malformed_path = malformed_manifest_repo / ".agents/orchestra/install-manifest.json"
        valid_manifest = json.loads(malformed_path.read_text(encoding="utf-8"))
        malformed_cases = []
        duplicate_skills = json.loads(json.dumps(valid_manifest))
        duplicate_skills["selected_skills"] = ["duplicate", "duplicate"]
        malformed_cases.append((duplicate_skills, "selected_skills"))
        unsafe_key = json.loads(json.dumps(valid_manifest))
        unsafe_key["files"]["../escape"] = {"kind": "file", "sha256": "0" * 64}
        malformed_cases.append((unsafe_key, "unsafe file key"))
        unknown_kind = json.loads(json.dumps(valid_manifest))
        first_file = next(iter(unknown_kind["files"]))
        unknown_kind["files"][first_file]["kind"] = "unknown"
        malformed_cases.append((unknown_kind, "unknown file kind"))
        non_string_kind = json.loads(json.dumps(valid_manifest))
        first_file = next(iter(non_string_kind["files"]))
        non_string_kind["files"][first_file]["kind"] = ["file"]
        malformed_cases.append((non_string_kind, "non-string file kind"))
        invalid_digest = json.loads(json.dumps(valid_manifest))
        first_file = next(iter(invalid_digest["files"]))
        invalid_digest["files"][first_file]["sha256"] = "not-a-sha256"
        malformed_cases.append((invalid_digest, "invalid sha256"))
        invalid_ownership = json.loads(json.dumps(valid_manifest))
        invalid_ownership["ownership"][".codex/config.toml"] = "shared"
        malformed_cases.append((invalid_ownership, "config ownership"))
        non_string_ownership = json.loads(json.dumps(valid_manifest))
        non_string_ownership["ownership"][".codex/config.toml"] = ["managed"]
        malformed_cases.append((non_string_ownership, "non-string config ownership"))
        for malformed, label in malformed_cases:
            malformed_path.write_text(json.dumps(malformed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            before_rejected_run = tree(malformed_manifest_repo)
            rejected_manifest = run_install(malformed_manifest_repo)
            require(rejected_manifest.returncode == 2 and "installed manifest" in rejected_manifest.stderr, f"{label} was not rejected as an InstallError")
            require(tree(malformed_manifest_repo) == before_rejected_run, f"{label} changed the target")
            malformed_path.write_text(json.dumps(valid_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        unsupported_manifest_repo = new_repo(temp, "unsupported-manifest-path")
        installed = run_install(unsupported_manifest_repo)
        require(installed.returncode == 0, installed.stderr)
        ordinary_readme = unsupported_manifest_repo / "README.md"
        ordinary_readme.write_bytes(b"ordinary user README\n")
        unsupported_path = unsupported_manifest_repo / ".agents/orchestra/install-manifest.json"
        unsupported_manifest = json.loads(unsupported_path.read_text(encoding="utf-8"))
        unsupported_manifest["files"]["README.md"] = {
            "kind": "file",
            "sha256": load_installer().sha256_bytes(ordinary_readme.read_bytes()),
        }
        unsupported_path.write_text(json.dumps(unsupported_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        unsupported_before = tree(unsupported_manifest_repo)
        unsupported_result = run_install(unsupported_manifest_repo)
        require(unsupported_result.returncode == 2 and "unsupported file key" in unsupported_result.stderr, "unsupported manifest path was not rejected")
        require(tree(unsupported_manifest_repo) == unsupported_before, "unsupported manifest path changed the target")

        escaped = new_repo(temp, "symlink")
        outside = temp / "outside"
        outside.mkdir()
        (escaped / ".codex").symlink_to(outside, target_is_directory=True)
        symlink_result = run_install(escaped)
        require(symlink_result.returncode == 2 and "symlink" in symlink_result.stderr, "symlink escape was not rejected")
        require(list(outside.iterdir()) == [], "symlink escape wrote outside the target")

        primary = new_repo(temp, "primary")
        (primary / "README.md").write_text("primary\n", encoding="utf-8")
        git(primary, "add", "README.md")
        git(primary, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "base")
        linked_parent = temp / "separate" / "nested"
        linked_parent.mkdir(parents=True)
        linked = linked_parent / "linked-worktree"
        git(primary, "worktree", "add", "-q", "-b", "linked-fixture", str(linked))
        linked_result = run_install(linked)
        require(linked_result.returncode == 0, f"linked worktree root was rejected: {linked_result.stderr}")
        require((linked / ".agents/orchestra/install-manifest.json").is_file(), "linked worktree installation was incomplete")

        rollback = new_repo(temp, "rollback")
        before = tree(rollback)
        module = load_installer()
        original = module.write_atomic
        calls = 0

        def fail_after_first(path: Path, data: bytes) -> None:
            nonlocal calls
            calls += 1
            original(path, data)
            if calls == 2:
                raise OSError("injected transaction failure")

        module.write_atomic = fail_after_first
        try:
            module.execute(module.parse_args(["--target", str(rollback)]))
            require(False, "injected installer failure did not fail")
        except OSError as exc:
            require("injected transaction failure" in str(exc), "unexpected rollback failure")
        require(tree(rollback) == before, "transaction failure did not restore the target exactly")

        legacy_rollback = new_repo(temp, "legacy-rollback")
        (legacy_rollback / ".orchestra/queue").mkdir(parents=True)
        (legacy_rollback / ".orchestra/queue/state.sqlite").write_bytes(b"rollback-state")
        legacy_exclude = (
            "rollback-user-pattern\n# agent-guild-orchestra:start\n"
            ".agents/orchestra/\n.orchestra/\n# agent-guild-orchestra:end\n"
        )
        (legacy_rollback / ".git/info/exclude").write_text(legacy_exclude, encoding="utf-8")
        rollback_module = load_installer()
        rollback_original = rollback_module.write_atomic

        def fail_manifest(path: Path, data: bytes) -> None:
            if path.as_posix().endswith("/.agents/orchestra/install-manifest.json"):
                raise OSError("injected manifest failure")
            rollback_original(path, data)

        rollback_module.write_atomic = fail_manifest
        try:
            rollback_module.execute(rollback_module.parse_args(["--target", str(legacy_rollback), "--major-upgrade"]))
            require(False, "legacy rollback injection did not fail")
        except OSError as exc:
            require("injected manifest failure" in str(exc), "unexpected legacy rollback failure")
        require((legacy_rollback / ".git/info/exclude").read_text(encoding="utf-8") == legacy_exclude, "rollback did not restore local Git exclude")
        require((legacy_rollback / ".orchestra/queue/state.sqlite").read_bytes() == b"rollback-state", "rollback did not restore legacy runtime")
        archive_root = legacy_rollback / ".agent-guild-orchestra-archives"
        require(not archive_root.exists() or not any(archive_root.iterdir()), "rollback left a partial cold archive")

        external_rollback_root = temp / "external-legacy-rollback"
        external_rollback_target = external_rollback_root / "repositories" / "app"
        external_rollback_target.mkdir(parents=True)
        git(external_rollback_target, "init", "-q")
        (external_rollback_root / ".orchestra/queue").mkdir(parents=True)
        (external_rollback_root / ".orchestra/queue/state.sqlite").write_bytes(b"external-rollback-state")
        (external_rollback_root / "AGENTS.md").write_text(
            "external user rule\n<!-- agent-guild-orchestra:start -->\nold block\n<!-- agent-guild-orchestra:end -->\n",
            encoding="utf-8",
        )
        external_before = tree(external_rollback_root)
        external_module = load_installer()
        external_original = external_module.write_atomic

        def fail_external_manifest(path: Path, data: bytes) -> None:
            if path.as_posix().endswith("/repositories/app/.agents/orchestra/install-manifest.json"):
                raise OSError("injected external migration failure")
            external_original(path, data)

        external_module.write_atomic = fail_external_manifest
        try:
            external_module.execute(
                external_module.parse_args(
                    [
                        "--target", str(external_rollback_target),
                        "--legacy-root", str(external_rollback_root),
                        "--major-upgrade",
                    ]
                )
            )
            require(False, "external migration rollback injection did not fail")
        except OSError as exc:
            require("injected external migration failure" in str(exc), "unexpected external migration rollback failure")
        require(tree(external_rollback_root) == external_before, "two-root rollback did not restore both roots exactly")
        external_archive_root = external_rollback_root / ".agent-guild-orchestra-archives"
        require(not external_archive_root.exists() or not any(external_archive_root.iterdir()), "two-root rollback left a partial archive")
