"""Parent-only installation and explicit child retirement in disposable fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install
import cleanup_child


def put(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def tree(root):
    """Include Git metadata, empty directories, modes and symlinks, never follow links."""
    result = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        data = os.readlink(path) if path.is_symlink() else None if path.is_dir() else path.read_bytes()
        result[str(path.relative_to(root))] = (mode, data)
    return result


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], env=install.git_environment(), stderr=subprocess.PIPE).decode().strip()


class ParentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="guild-parent-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.parent = self.base / "asked-root"
        self.parent.mkdir()
        self.children = []
        for name in ("asked_backend", "asked_compose", "asked_frontend"):
            repo = self.parent / "repositories" / name
            repo.mkdir(parents=True)
            git(repo, "init", "-q")
            put(repo, "source.txt", "committed\n")
            git(repo, "add", "source.txt")
            git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
            put(repo, "source.txt", "staged\n")
            git(repo, "add", "source.txt")
            put(repo, "source.txt", "unstaged\n")
            put(repo, "untracked.txt", "untracked\n")
            put(repo, "AGENTS.md", "child instructions\n")
            put(repo, ".codex/config.toml", 'model = "user-model"\n')
            put(repo, ".agents/skills/third-party/SKILL.md", "user skill\n")
            put(repo, ".git/info/exclude", "user-pattern\n")
            git(repo, "config", "guild.fixture", "unchanged")
            self.children.append(repo)
        self.child_before = tree(self.parent / "repositories")

    def run_install(self, *options):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            install.execute(install.parse_args(["--target", str(self.parent), *options]))
        return json.loads(output.getvalue())

    def assert_children_unchanged(self):
        self.assertEqual(tree(self.parent / "repositories"), self.child_before)

    def legacy(self):
        fixture = ROOT / "scripts/validation/fixtures/legacy-v2"
        shutil.copytree(fixture, self.parent, dirs_exist_ok=True)
        (self.parent / ".codex/hooks/stop_quality_gate.sh").chmod(0o755)
        path = self.parent / "AGENTS.md"
        path.write_text(install.AGENTS_START + "\n" + path.read_text().strip() + "\n" + install.AGENTS_END + "\n\nuser rule\n")
        put(self.parent, ".agents/skills/third-party/SKILL.md", "third party\n")
        put(self.parent, ".codex/custom.toml", "custom = true\n")
        put(self.parent, ".orchestra/queue/state.sqlite", "unverified runtime state\n")
        hooks = self.parent / ".codex/hooks.json"
        value = json.loads(hooks.read_text())
        value["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "user-command"})
        hooks.write_text(json.dumps(value))

    def old_child(self, repo):
        # Reconstruct the schema-1 ownership contract without using the installer on a Git root.
        manifest = install.load_manifest(self.parent)
        for name in manifest["files"]:
            destination = put(repo, name, (self.parent / name).read_bytes())
            shutil.copystat(self.parent / name, destination)
        manifest["schema"] = 1
        manifest.pop("layout")
        put(repo, str(install.MANIFEST_REL), json.dumps(manifest))
        return manifest

    def test_fresh_dry_update_and_packages_never_touch_children(self):
        before = tree(self.parent)
        dry = self.run_install("--dry-run", "--with-skill", "create-skill-candidate-from-gap")
        self.assertEqual(tree(self.parent), before)
        self.assertTrue(dry["child_overrides"])
        self.run_install("--with-skill", "create-skill-candidate-from-gap")
        manifest = install.load_manifest(self.parent)
        self.assertEqual(manifest["layout"], "guild-parent")
        self.assertNotIn(".git/info/exclude", manifest["files"])
        with patch.object(install, "write_atomic", side_effect=AssertionError("no-op wrote a file")):
            self.run_install()
        self.run_install("--without-skill", "create-skill-candidate-from-gap")
        self.assertFalse((self.parent / ".agents/skills/create-skill-candidate-from-gap").exists())
        self.assert_children_unchanged()

    def test_custom_parent_config_and_user_agents_are_preserved(self):
        config = put(self.parent, ".codex/config.toml", 'model = "my-model"\n')
        config.chmod(0o600)
        agents = put(self.parent, "AGENTS.md", "User instructions\n")
        agents.chmod(0o600)
        put(self.parent, "AGENTS.override.md", "User override\n")
        before = (config.read_bytes(), config.stat().st_mode)
        plan = self.run_install()
        self.assertEqual(plan["config_mode"], "user-owned")
        self.assertTrue(any("AGENTS.override.md" in warning for warning in plan["warnings"]))
        self.assertTrue(any("Manually reconcile" in step for step in plan["next_steps"]))
        self.run_install()
        self.assertEqual((config.read_bytes(), config.stat().st_mode), before)
        self.assertTrue((self.parent / "AGENTS.md").read_text().startswith("User instructions"))
        self.assertEqual(agents.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(install.InstallError, "collision"):
            self.run_install("--config-mode", "managed")
        self.assert_children_unchanged()

    def test_update_preserves_local_edits_and_rejects_two_sided_changes(self):
        self.run_install()
        local = put(self.parent, ".agents/orchestra/README.md", "local change\n")
        self.run_install()
        self.assertEqual(local.read_text(), "local change\n")
        source = self.base / "distribution/template"
        shutil.copytree(ROOT / "template", source)
        put(source, ".agents/orchestra/README.md", "distribution change\n")
        before = tree(self.parent)
        with self.assertRaisesRegex(install.InstallError, "locally and in distribution"):
            self.run_install("--source", str(source), "--allow-non-default-source")
        self.assertEqual(tree(self.parent), before)
        local.write_text("distribution change\n")
        self.run_install("--source", str(source), "--allow-non-default-source")
        self.assert_children_unchanged()

    def test_v2_retirement_preserves_modified_and_foreign_material(self):
        self.legacy()
        modified = put(self.parent, ".codex/agents/captain.toml", "user modified role\n")
        before = tree(self.parent)
        plan = self.run_install("--dry-run")
        self.assertEqual(tree(self.parent), before)
        self.assertIn(".codex/agents/captain.toml", plan["preserved_legacy_files"])
        plan = self.run_install()
        archive = Path(plan["archive"])
        self.assertTrue((archive / "AGENTS.md").is_file())
        self.assertTrue((archive / ".codex/config.toml").is_file())
        self.assertEqual(modified.read_text(), "user modified role\n")
        self.assertFalse((self.parent / ".agents/skills/explain-clearly/SKILL.md").exists())
        self.assertFalse((self.parent / ".codex/hooks/stop_quality_gate.sh").exists())
        self.assertTrue((self.parent / ".agents/skills/third-party/SKILL.md").is_file())
        self.assertIn("user rule", (self.parent / "AGENTS.md").read_text())
        hooks = json.loads((self.parent / ".codex/hooks.json").read_text())
        self.assertEqual(hooks["hooks"]["Stop"][0]["hooks"], [{"type": "command", "command": "user-command"}])
        self.assertTrue((self.parent / ".orchestra/queue/state.sqlite").is_file())
        self.assert_children_unchanged()

    def test_v2_modified_overlapping_instructions_stop_before_writes(self):
        self.legacy()
        path = self.parent / "AGENTS.md"
        path.write_text(path.read_text().replace("# agent-guild-orchestra", "# User-changed Guild", 1))
        before = tree(self.parent)
        with self.assertRaisesRegex(install.InstallError, "collision"):
            self.run_install()
        self.assertEqual(tree(self.parent), before)

    def test_failure_restores_parent_and_leaves_children_identical(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                if legacy:
                    self.legacy()
                    (self.parent / ".codex/agents").chmod(0o700)
                    (self.parent / install.ARCHIVE_ROOT_REL).mkdir()
                before = tree(self.parent)
                original = install.write_atomic
                def fail_manifest(path, data, **kwargs):
                    original(path, data, **kwargs)
                    if path == self.parent / install.MANIFEST_REL:
                        raise OSError("injected failure")
                with patch.object(install, "write_atomic", side_effect=fail_manifest):
                    with self.assertRaisesRegex(OSError, "injected failure"):
                        self.run_install()
                self.assertEqual(tree(self.parent), before)
                self.assert_children_unchanged()

    def test_rejects_git_roots_nested_targets_symlinks_and_bad_manifest(self):
        nested = self.children[0] / "nested"
        nested.mkdir()
        bare = self.base / "bare"
        bare.mkdir()
        git(bare, "init", "--bare", "-q")
        for path in (self.children[0], nested, bare):
            with self.assertRaisesRegex(install.InstallError, "non-Git parent"):
                install.canonical_guild_root(str(path))
        outside = self.base / "outside"
        outside.mkdir()
        (self.parent / ".codex").symlink_to(outside)
        with self.assertRaisesRegex(install.InstallError, "symlink"):
            self.run_install()
        (self.parent / ".codex").unlink()
        self.run_install()
        manifest = install.load_manifest(self.parent)
        for name in ("README.md", "repositories/asked_backend/source.txt", ".git/info/exclude", "../escape"):
            malformed = json.loads(json.dumps(manifest))
            malformed["files"][name] = {"kind": "file", "sha256": "0" * 64}
            put(self.parent, str(install.MANIFEST_REL), json.dumps(malformed))
            before = tree(self.parent)
            with self.assertRaises(install.InstallError):
                self.run_install()
            self.assertEqual(tree(self.parent), before)

    def test_normal_install_ignores_child_v3_and_explicit_cleanup_preserves_edits(self):
        self.run_install()
        child = self.children[0]
        self.old_child(child)
        put(child, "AGENTS.md", (child / "AGENTS.md").read_text() + "\nmy child rule\n")
        modified = put(child, ".codex/config.toml", "user modifications\n")
        tracked = ".codex/agents/adventurer.toml"
        git(child, "add", tracked)
        git_before = tree(child / ".git")
        full_before = tree(child)
        self.run_install()
        self.assertEqual(tree(child), full_before)
        dry = cleanup_child.execute(self.parent, child, True)
        self.assertEqual(tree(child), full_before)
        self.assertIn({"path": tracked, "action": "preserve-tracked"}, dry["actions"])
        result = cleanup_child.execute(self.parent, child, False)
        self.assertTrue(Path(result["archive"]).is_dir())
        self.assertEqual(tree(child / ".git"), git_before)
        self.assertEqual(modified.read_text(), "user modifications\n")
        self.assertEqual((child / "AGENTS.md").read_text(), "my child rule\n")
        self.assertEqual((child / "AGENTS.md").stat().st_mode & 0o777, 0o644)
        self.assertTrue((child / tracked).exists())
        self.assertFalse((child / ".codex/agents/inquisitor.toml").exists())
        self.assertTrue((child / ".agents/skills/third-party/SKILL.md").exists())
        self.assertTrue((child / install.MANIFEST_REL).exists())

    def test_child_cleanup_failure_restores_every_touched_path(self):
        self.run_install()
        child = self.children[0]
        self.old_child(child)
        before = tree(self.parent)
        original = Path.unlink
        calls = 0
        def fail_unlink(path, *args, **kwargs):
            nonlocal calls
            original(path, *args, **kwargs)
            if path.is_relative_to(child):
                calls += 1
                if calls == 3:
                    raise OSError("injected cleanup failure")
        with patch.object(Path, "unlink", fail_unlink):
            with self.assertRaisesRegex(OSError, "injected cleanup failure"):
                cleanup_child.execute(self.parent, child, False)
        self.assertEqual(tree(self.parent), before)

    def test_parent_install_does_not_touch_linked_worktree_or_external_metadata(self):
        main = self.children[0]
        linked = self.parent / "repositories/linked"
        git(main, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
        before = tree(self.parent / "repositories")
        self.run_install()
        self.run_install()
        self.assertEqual(tree(self.parent / "repositories"), before)

    def test_parent_helpers_still_bind_to_the_actual_child_git_root(self):
        self.run_install()
        script = self.parent / ".agents/orchestra/scripts/snapshot_digest.py"
        result = subprocess.run([sys.executable, str(script), "--repo", str(self.children[0]), "--kind", "working_tree_content", "--scope", "source.txt"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["revision_id"], git(self.children[0], "rev-parse", "HEAD"))
        self.assertEqual(json.loads(result.stdout)["scope_paths"], ["source.txt"])
        rejected = subprocess.run([sys.executable, str(script), "--repo", str(self.parent), "--kind", "revision_only"], capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assert_children_unchanged()

    def test_child_cleanup_retains_mode_changes_and_git_exclude(self):
        self.run_install()
        child = self.children[0]
        manifest = self.old_child(child)
        role = child / ".codex/agents/adventurer.toml"
        role.chmod(0o600)
        exclude = put(child, ".git/info/exclude", install.EXCLUDE_START + "\nold-pattern\n" + install.EXCLUDE_END + "\n")
        manifest["files"][".git/info/exclude"] = {"kind": "exclude_block", "sha256": install.current_hash(child, install.EXCLUDE_REL, "exclude_block")}
        put(child, str(install.MANIFEST_REL), json.dumps(manifest))
        git_before = tree(child / ".git")
        cleanup_child.execute(self.parent, child, False)
        self.assertEqual(role.stat().st_mode & 0o777, 0o600)
        self.assertEqual(tree(child / ".git"), git_before)
        self.assertTrue((child / install.MANIFEST_REL).is_file())

    def test_cleanup_recheck_does_not_overwrite_a_late_user_edit(self):
        self.run_install()
        child = self.children[0]
        self.old_child(child)
        path = child / ".agents/orchestra/README.md"
        original = install.archive_legacy
        def edit_after_preflight(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_text("late user edit\n")
            return result
        with patch.object(install, "archive_legacy", side_effect=edit_after_preflight):
            with self.assertRaisesRegex(install.InstallError, "changed during cleanup"):
                cleanup_child.execute(self.parent, child, False)
        self.assertEqual(path.read_text(), "late user edit\n")
        self.assertTrue((child / ".codex/agents/adventurer.toml").is_file())

    @unittest.skipIf(os.geteuid() == 0, "permission failure requires the normal non-root installer UID")
    def test_unwritable_update_destination_does_not_abort_other_restores(self):
        self.run_install()
        source = self.base / "distribution/template"
        shutil.copytree(ROOT / "template", source)
        for name in (".agents/orchestra/README.md", ".agents/orchestra/scripts/git_guard.py"):
            path = source / name
            path.write_bytes(path.read_bytes() + b"\n# next distribution\n")
        protected = self.parent / ".agents/orchestra/scripts"
        protected.chmod(0o555)
        try:
            before = tree(self.parent)
            with self.assertRaises(PermissionError):
                self.run_install("--source", str(source), "--allow-non-default-source")
            self.assertEqual(tree(self.parent), before)
            self.assert_children_unchanged()
        finally:
            protected.chmod(0o755)

    def test_restore_failure_keeps_host_backup_and_continues_other_restores(self):
        user = put(self.parent, "AGENTS.md", "Private user rule\n")
        user.chmod(0o600)
        original = install.write_atomic
        restoring = False
        def exhausted(path, data, **kwargs):
            nonlocal restoring
            if restoring and path == user:
                raise OSError("injected ENOSPC during restore")
            original(path, data, **kwargs)
            if path == self.parent / install.MANIFEST_REL:
                restoring = True
                raise OSError("injected ENOSPC after final write")
        with patch.object(install, "write_atomic", side_effect=exhausted), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(install.InstallError, "rollback incomplete; recovery backup retained"):
                self.run_install()
        backups = list((self.parent / install.RECOVERY_ROOT_REL).glob("transaction-*"))
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        self.assertEqual((backup / "AGENTS.md").read_text(), "Private user rule\n")
        self.assertEqual((backup / "AGENTS.md").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((backup / "recovery.json").read_text())["target"], str(self.parent))
        self.assertTrue(user.exists())  # Failed restoration must never unlink the destination first.
        self.assertFalse((self.parent / install.MANIFEST_REL).exists())
        self.assertFalse((self.parent / ".codex/agents/adventurer.toml").exists())
        self.assert_children_unchanged()

    def test_sigint_restores_install_and_child_cleanup(self):
        for cleanup in (False, True):
            with self.subTest(cleanup=cleanup):
                if cleanup:
                    self.run_install()
                    target = self.children[0]
                    self.old_child(target)
                    agents = target / "AGENTS.md"
                    agents.write_text(agents.read_text() + "\nuser child rule\n")
                else:
                    target = self.parent
                    put(target, "AGENTS.md", "User rule\n")
                before = tree(self.parent)
                original = install.write_atomic
                interrupted = False
                def interrupt_after_write(path, data, **kwargs):
                    nonlocal interrupted
                    original(path, data, **kwargs)
                    if path == target / "AGENTS.md" and not interrupted:
                        interrupted = True
                        os.kill(os.getpid(), signal.SIGINT)
                with patch.object(install, "write_atomic", side_effect=interrupt_after_write):
                    with self.assertRaises(KeyboardInterrupt):
                        if cleanup:
                            cleanup_child.execute(self.parent, target, False)
                        else:
                            self.run_install()
                self.assertTrue(interrupted)
                self.assertEqual(tree(self.parent), before)

    def test_mode_only_edits_block_updates_and_survive_package_removal(self):
        skill = "create-skill-candidate-from-gap"
        self.run_install("--with-skill", skill)
        role = self.parent / ".codex/agents/adventurer.toml"
        role.chmod(0o600)
        source = self.base / "distribution/template"
        shutil.copytree(ROOT / "template", source)
        shutil.copytree(ROOT / "optional-skills", source.parent / "optional-skills")
        changed = source / ".codex/agents/adventurer.toml"
        changed.write_bytes(changed.read_bytes() + b"\n# next distribution\n")
        before = tree(self.parent)
        with self.assertRaisesRegex(install.InstallError, "locally and in distribution"):
            self.run_install("--source", str(source), "--allow-non-default-source")
        self.assertEqual(tree(self.parent), before)
        skill_file = self.parent / ".agents/skills" / skill / "SKILL.md"
        skill_file.chmod(0o600)
        self.run_install("--without-skill", skill)
        self.assertTrue(skill_file.is_file())
        self.assertEqual(skill_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(role.stat().st_mode & 0o777, 0o600)
        self.assert_children_unchanged()

    def test_recovery_destination_symlink_is_rejected_before_mutation(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.parent / install.RECOVERY_ROOT_REL).symlink_to(outside)
        before = tree(self.parent)
        with self.assertRaisesRegex(install.InstallError, "symlink"):
            self.run_install()
        self.assertEqual(tree(self.parent), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_legacy_wrapper_mode_change_and_interrupted_archive(self):
        self.legacy()
        wrapper = self.parent / ".codex/hooks/stop_quality_gate.sh"
        wrapper.chmod(0o644)  # Removing the historical execute bit is a user edit.
        self.assertNotIn(Path(".codex/hooks/stop_quality_gate.sh"), install.legacy_paths(self.parent))
        before = tree(self.parent)
        copy = shutil.copy2
        def interrupt_copy(src, dst, *args, **kwargs):
            result = copy(src, dst, *args, **kwargs)
            if str(install.ARCHIVE_ROOT_REL) in str(dst):
                raise KeyboardInterrupt()
            return result
        with patch.object(shutil, "copy2", side_effect=interrupt_copy):
            with self.assertRaises(KeyboardInterrupt):
                self.run_install()
        self.assertEqual(tree(self.parent), before)
        self.run_install()
        self.assertTrue(wrapper.is_file())
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o644)

    def test_legacy_optional_script_modes_do_not_change_v3_mode_policy(self):
        names = [rel for rel in install.LEGACY_MODES if ".agents" in rel.parts]
        data = b"# synthetic legacy script fixture\n"
        catalog = {"files": {str(rel): [install.sha256_bytes(data)] for rel in names}, "hook_commands": []}
        with patch.object(install, "legacy_catalog", return_value=catalog):
            for rel in names:
                path = put(self.parent, str(rel), data)
                path.chmod(0o644)
                self.assertIn(rel, install.legacy_paths(self.parent))
                path.chmod(0o755)
                self.assertNotIn(rel, install.legacy_paths(self.parent))
                self.assertFalse(install.locally_modified_mode(path, rel))  # v3 scripts are executable.



if __name__ == "__main__":
    unittest.main()
