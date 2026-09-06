from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "template/.agents/orchestra/scripts"
sys.path.insert(0, str(SCRIPTS))
import git_guard  # noqa: E402
import snapshot_digest  # noqa: E402


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False, env=env)
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


class GitGuardTests(unittest.TestCase):
    def make_repo(self, directory: Path) -> Path:
        repo = directory / "repo"
        repo.mkdir()
        git(repo, "init", "--quiet")
        git(repo, "config", "user.name", "Git Guard Fixture")
        git(repo, "config", "user.email", "git-guard@example.invalid")
        (repo / "src").mkdir()
        (repo / "src/owned.txt").write_text("before\n", encoding="utf-8")
        (repo / "src/unrelated.txt").write_text("before\n", encoding="utf-8")
        git(repo, "add", "src/owned.txt", "src/unrelated.txt")
        git(repo, "commit", "--quiet", "-m", "baseline")
        return repo

    def make_staged_rename(self, directory: Path) -> tuple[Path, str, str]:
        repo = self.make_repo(directory)
        old = repo / "src/rename-old.txt"
        new = repo / "src/rename-new.txt"
        old.write_text("rename fixture\n", encoding="utf-8")
        git(repo, "add", "src/rename-old.txt")
        git(repo, "commit", "--quiet", "-m", "rename baseline")
        git(repo, "mv", "src/rename-old.txt", "src/rename-new.txt")
        return repo, "src/rename-old.txt", "src/rename-new.txt"

    def contract(
        self,
        repo: Path,
        operation: str,
        snapshot: dict[str, object],
        *,
        paths: list[str] | None = None,
        expected_index_tree: str | None = None,
        **scope: object,
    ) -> dict[str, object]:
        path_scope: dict[str, object] = {"paths": paths or [], **scope}
        contract: dict[str, object] = {
            "type": "assignment",
            "schema_version": "1.0",
            "id": "git-assignment-1",
            "target_repo_root": str(repo),
            "allowed_operations": [operation],
            "path_or_ref_scope": path_scope,
            "subject_snapshot": snapshot,
            "preconditions": {"target_repo_root_confirmed": True, "preflight_snapshot_matches_assignment": True},
            "postconditions": {},
            "forbidden_operations": ["push", "reset", "commit_amend", "rebase", "clean"],
        }
        if operation == "commit_non_amend":
            contract["expected_index_tree"] = (
                git_guard.index_tree(repo) if expected_index_tree is None else expected_index_tree
            )
        return contract

    def test_exact_stage_commit_and_index_only_unstage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("after\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            stage_contract = self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=["src/owned.txt"])
            staged = git_guard.apply(stage_contract)
            self.assertEqual(staged["evidence"]["staged_paths"], ["src/owned.txt"])
            self.assertIn("after", git(repo, "show", ":src/owned.txt"))

            unstage_snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            unstage_contract = self.contract(repo, "unstage_index_only_exact_paths", unstage_snapshot, paths=["src/owned.txt"])
            unstage = git_guard.apply(unstage_contract)
            self.assertEqual(unstage["evidence"]["staged_paths_after"], [])
            self.assertEqual((repo / "src/owned.txt").read_text(encoding="utf-8"), "after\n")

            git(repo, "add", "src/owned.txt")
            commit_snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            commit_contract = self.contract(repo, "commit_non_amend", commit_snapshot, paths=["src/owned.txt"], message="guarded commit")
            committed = git_guard.apply(commit_contract)
            self.assertEqual(committed["operation"], "commit_non_amend")
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_malicious_patch_and_unrelated_staged_path_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("after\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            malicious = "diff --git a/src/owned.txt b/../outside.txt\n--- a/src/owned.txt\n+++ b/../outside.txt\n@@ -1 +1 @@\n-before\n+after\n"
            contract = self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=["src/owned.txt"], patch=malicious)
            with self.assertRaisesRegex(git_guard.GitGuardError, "path|scope"):
                git_guard.apply(contract)
            self.assertEqual(git(repo, "diff", "--cached", "--name-only"), "")

            (repo / "src/unrelated.txt").write_text("unrelated change\n", encoding="utf-8")
            git(repo, "add", "src/unrelated.txt")
            staged = git_guard.apply(self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=["src/owned.txt"]))
            self.assertEqual(staged["evidence"]["staged_paths"], ["src/owned.txt", "src/unrelated.txt"])
            self.assertEqual(git(repo, "diff", "--cached", "--name-only").splitlines(), ["src/owned.txt", "src/unrelated.txt"])

            commit_snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            with self.assertRaisesRegex(git_guard.GitGuardError, "unrelated"):
                git_guard.apply(self.contract(repo, "commit_non_amend", commit_snapshot, paths=["src/owned.txt"], message="must reject B"))

    def test_new_untracked_path_stage_and_unstage_keep_worktree_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            path = repo / "src/new.txt"
            path.write_text("new content\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/new.txt"],
                untracked_paths=["src/new.txt"],
            )
            stage = git_guard.apply(self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=["src/new.txt"]))
            self.assertEqual(stage["postwrite_snapshot"]["untracked_paths"], [])
            self.assertEqual(path.read_text(encoding="utf-8"), "new content\n")

            unstage_snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/new.txt"],
                untracked_paths=[],
            )
            unstage = git_guard.apply(self.contract(repo, "unstage_index_only_exact_paths", unstage_snapshot, paths=["src/new.txt"]))
            self.assertEqual(unstage["postwrite_snapshot"]["untracked_paths"], ["src/new.txt"])
            self.assertEqual(path.read_text(encoding="utf-8"), "new content\n")

    def test_nested_untracked_directory_stages_only_the_authorized_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "new/nested").mkdir(parents=True)
            owned, unrelated = "new/nested/owned.txt", "new/nested/unrelated.txt"
            (repo / owned).write_text("owned\n", encoding="utf-8")
            (repo / unrelated).write_text("unrelated\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(
                repo, kind="working_tree_content", scope_paths=[owned], untracked_paths=[owned],
            )
            staged = git_guard.apply(self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=[owned]))
            self.assertEqual(staged["evidence"]["staged_paths"], [owned])
            commit_snapshot = snapshot_digest.compute_snapshot(
                repo, kind="working_tree_content", scope_paths=[owned], untracked_paths=[],
            )
            committed = git_guard.apply(self.contract(
                repo, "commit_non_amend", commit_snapshot, paths=[owned], message="add nested file",
            ))
            self.assertEqual(committed["evidence"]["committed_paths"], [owned])
            self.assertEqual(git(repo, "ls-files", "--others", "--exclude-standard").splitlines(), [unrelated])
            self.assertEqual((repo / unrelated).read_text(encoding="utf-8"), "unrelated\n")

    def test_partial_stage_commit_uses_reviewed_index_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            git(repo, "commit", "--quiet", "-m", "expand fixture")
            (repo / "src/owned.txt").write_text("one changed\ntwo\nthree\nfour changed\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            patch = (
                "diff --git a/src/owned.txt b/src/owned.txt\n"
                "--- a/src/owned.txt\n"
                "+++ b/src/owned.txt\n"
                "@@ -1,2 +1,2 @@\n"
                "-one\n"
                "+one changed\n"
                " two\n"
            )
            staged = git_guard.apply(
                self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=["src/owned.txt"], patch=patch)
            )
            self.assertEqual(staged["evidence"]["staged_paths"], ["src/owned.txt"])
            expected_tree = git_guard.index_tree(repo)
            commit_snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            committed = git_guard.apply(
                self.contract(
                    repo,
                    "commit_non_amend",
                    commit_snapshot,
                    paths=["src/owned.txt"],
                    expected_index_tree=expected_tree,
                    message="partial staged commit",
                )
            )
            self.assertEqual(committed["evidence"]["commit_tree"], expected_tree)
            self.assertEqual(git(repo, "show", "HEAD:src/owned.txt"), "one changed\ntwo\nthree\nfour\n")
            self.assertIn("src/owned.txt", git(repo, "status", "--porcelain"))

    def test_index_replacement_after_review_is_rejected_without_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("reviewed B\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            expected_tree = git_guard.index_tree(repo)
            (repo / "src/owned.txt").write_text("tampered C\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            (repo / "src/owned.txt").write_text("reviewed B\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(
                repo,
                "commit_non_amend",
                snapshot,
                paths=["src/owned.txt"],
                expected_index_tree=expected_tree,
                message="must reject replaced index",
            )
            before_head = git(repo, "rev-parse", "HEAD")
            with self.assertRaisesRegex(git_guard.GitGuardError, "index tree|expected_index_tree"):
                git_guard.apply(contract)
            self.assertEqual(git(repo, "rev-parse", "HEAD"), before_head)
            self.assertEqual(git(repo, "show", ":src/owned.txt"), "tampered C\n")
            self.assertEqual((repo / "src/owned.txt").read_text(encoding="utf-8"), "reviewed B\n")

    def test_index_replacement_during_commit_window_commits_bound_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("reviewed B\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            expected_tree = git_guard.index_tree(repo)
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(
                repo,
                "commit_non_amend",
                snapshot,
                paths=["src/owned.txt"],
                expected_index_tree=expected_tree,
                message="commit bound tree",
            )
            original_index_tree = git_guard._index_tree
            swapped = False

            def replace_after_validation(index_root: Path) -> str:
                nonlocal swapped
                tree = original_index_tree(index_root)
                if not swapped:
                    swapped = True
                    (repo / "src/owned.txt").write_text("tampered C\n", encoding="utf-8")
                    git(repo, "add", "src/owned.txt")
                    (repo / "src/owned.txt").write_text("reviewed B\n", encoding="utf-8")
                return tree

            with mock.patch.object(git_guard, "_index_tree", side_effect=replace_after_validation):
                committed = git_guard.apply(contract)
            self.assertEqual(committed["evidence"]["commit_tree"], expected_tree)
            self.assertEqual(git(repo, "rev-parse", "HEAD^{tree}"), expected_tree + "\n")
            self.assertEqual(git(repo, "show", "HEAD:src/owned.txt"), "reviewed B\n")
            self.assertEqual(git(repo, "show", ":src/owned.txt"), "tampered C\n")

    def test_subject_revision_drift_after_preflight_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("reviewed\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            expected_tree = git_guard.index_tree(repo)
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(
                repo,
                "commit_non_amend",
                snapshot,
                paths=["src/owned.txt"],
                expected_index_tree=expected_tree,
                message="must reject revision drift",
            )
            old_head = git(repo, "rev-parse", "HEAD").strip()
            base_tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
            original_preflight = git_guard._preflight_snapshot
            drifted_head: str | None = None

            def drift_after_preflight(index_root: Path, expected: dict[str, object]) -> dict[str, object]:
                nonlocal drifted_head
                result = original_preflight(index_root, expected)
                drifted_head = git(repo, "commit-tree", base_tree, "-p", old_head, "-m", "external drift").strip()
                git(repo, "update-ref", "HEAD", drifted_head, old_head)
                return result

            with mock.patch.object(git_guard, "_preflight_snapshot", side_effect=drift_after_preflight):
                with self.assertRaisesRegex(git_guard.GitGuardError, "HEAD|snapshot"):
                    git_guard.apply(contract)
            self.assertIsNotNone(drifted_head)
            self.assertEqual(git(repo, "rev-parse", "HEAD").strip(), drifted_head)
            self.assertEqual(git(repo, "show", "HEAD:src/owned.txt"), "before\n")

    def test_update_ref_does_not_follow_replaced_symbolic_branch_ref(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("reviewed\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(repo, "commit_non_amend", snapshot, paths=["src/owned.txt"], message="fixed ref")
            old_head = git(repo, "rev-parse", "HEAD").strip()
            branch_ref = git(repo, "symbolic-ref", "HEAD").strip()
            git(repo, "branch", "unowned-branch", old_head)
            original_git = git_guard._git
            redirected = False

            def replace_ref_before_update(root: Path, args: list[str], **kwargs: object) -> bytes:
                nonlocal redirected
                if args[:2] == ["update-ref", "--no-deref"] and not redirected:
                    redirected = True
                    git(repo, "symbolic-ref", branch_ref, "refs/heads/unowned-branch")
                return original_git(root, args, **kwargs)

            with mock.patch.object(git_guard, "_git", side_effect=replace_ref_before_update):
                committed = git_guard.apply(contract)
            self.assertTrue(redirected)
            self.assertEqual(git(repo, "rev-parse", "refs/heads/unowned-branch").strip(), old_head)
            self.assertEqual(git(repo, "rev-parse", "HEAD").strip(), committed["evidence"]["commit"])

    def test_ignore_submodules_does_not_hide_scope_external_gitlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            old_head = git(repo, "rev-parse", "HEAD").strip()
            base_tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
            next_commit = git(repo, "commit-tree", base_tree, "-p", old_head, "-m", "gitlink second revision").strip()
            git(repo, "update-index", "--add", "--cacheinfo", f"160000,{old_head},vendor/module")
            git(repo, "commit", "--quiet", "-m", "gitlink baseline")
            git(repo, "config", "diff.ignoreSubmodules", "all")
            git(repo, "update-index", "--cacheinfo", f"160000,{next_commit},vendor/module")
            (repo / "src/owned.txt").write_text("reviewed\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            self.assertEqual(git_guard._staged_paths(repo), {"src/owned.txt", "vendor/module"})
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(
                repo,
                "commit_non_amend",
                snapshot,
                paths=["src/owned.txt"],
                message="must reject extra gitlink",
            )
            before_head = git(repo, "rev-parse", "HEAD").strip()
            with self.assertRaisesRegex(git_guard.GitGuardError, "unrelated.*vendor/module"):
                git_guard.apply(contract)
            self.assertEqual(git(repo, "rev-parse", "HEAD").strip(), before_head)

    def test_index_tree_cli_returns_machine_issued_value(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("staged\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            command = [
                sys.executable,
                str(SCRIPTS / "git_guard.py"),
                "index-tree",
                "--repo",
                str(repo),
            ]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["expected_index_tree"], git_guard.index_tree(repo))

    def test_commit_requires_reviewed_index_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("staged\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(repo, "commit_non_amend", snapshot, paths=["src/owned.txt"])
            del contract["expected_index_tree"]
            with self.assertRaisesRegex(git_guard.GitGuardError, "expected_index_tree"):
                git_guard.apply(contract)

    def test_rename_new_only_commit_scope_refuses_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, old, new = self.make_staged_rename(Path(raw))
            snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[new], untracked_paths=[])
            contract = self.contract(repo, "commit_non_amend", snapshot, paths=[new], message="rename new endpoint only")
            before_head = git(repo, "rev-parse", "HEAD")
            before_index = git(repo, "diff", "--cached", "--name-status", "--no-renames")
            with self.assertRaisesRegex(git_guard.GitGuardError, "unrelated"):
                git_guard.apply(contract)
            self.assertEqual(git(repo, "rev-parse", "HEAD"), before_head)
            self.assertEqual(git(repo, "diff", "--cached", "--name-status", "--no-renames"), before_index)
            self.assertEqual(git_guard._staged_paths(repo), {old, new})

    def test_rename_both_endpoints_commit_and_report_both(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, old, new = self.make_staged_rename(Path(raw))
            snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[old, new], untracked_paths=[])
            contract = self.contract(repo, "commit_non_amend", snapshot, paths=[old, new], message="rename both endpoints")
            expected_tree = contract["expected_index_tree"]
            committed = git_guard.apply(contract)
            self.assertEqual(committed["evidence"]["committed_paths"], sorted([old, new]))
            self.assertEqual(committed["evidence"]["commit_tree"], expected_tree)
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_rename_new_only_unstage_preserves_old_staged_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, old, new = self.make_staged_rename(Path(raw))
            snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[new], untracked_paths=[])
            contract = self.contract(repo, "unstage_index_only_exact_paths", snapshot, paths=[new])
            unstage = git_guard.apply(contract)
            self.assertEqual(unstage["evidence"]["staged_paths_after"], [old])
            self.assertEqual(git(repo, "diff", "--cached", "--name-status", "--no-renames"), f"D\t{old}\n")

    def test_rename_both_endpoints_unstage_removes_staged_rename(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, old, new = self.make_staged_rename(Path(raw))
            snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[old, new], untracked_paths=[])
            contract = self.contract(repo, "unstage_index_only_exact_paths", snapshot, paths=[old, new])
            unstage = git_guard.apply(contract)
            self.assertEqual(unstage["evidence"]["staged_paths_before"], sorted([old, new]))
            self.assertEqual(unstage["evidence"]["staged_paths_after"], [])
            self.assertEqual(git(repo, "diff", "--cached", "--name-status", "--no-renames"), "")

    def test_linked_worktree_operation_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            linked = base / "linked"
            git(repo, "worktree", "add", "--quiet", "--detach", str(linked), "HEAD")
            try:
                snapshot = snapshot_digest.compute_snapshot(linked, kind="revision_only")
                linked_git, _common_git = snapshot_digest.resolve_git_directories(linked)
                (linked_git / "MERGE_HEAD").write_text("synthetic\n", encoding="utf-8")
                contract = self.contract(repo, "branch_create_and_switch_new", snapshot, new_branch="codex/marker", base_ref="HEAD")
                contract["target_repo_root"] = str(linked)
                with self.assertRaisesRegex(git_guard.GitGuardError, "merge/rebase/cherry-pick"):
                    git_guard.apply(contract)
            finally:
                git(repo, "worktree", "remove", "--force", str(linked))

    def test_commit_uses_synthetic_global_identity_without_logging_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            git(repo, "config", "--unset", "user.name")
            git(repo, "config", "--unset", "user.email")
            home = base / "home"
            home.mkdir()
            (home / ".gitconfig").write_text(
                "[user]\n\tname = Synthetic Global\n\temail = synthetic-global@example.invalid\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["HOME"] = str(home)
            environment.pop("XDG_CONFIG_HOME", None)
            with mock.patch.dict(os.environ, environment, clear=True):
                (repo / "src/owned.txt").write_text("global identity\n", encoding="utf-8")
                git(repo, "add", "src/owned.txt")
                snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
                committed = git_guard.apply(self.contract(repo, "commit_non_amend", snapshot, paths=["src/owned.txt"], message="global identity"))
            self.assertEqual(committed["operation"], "commit_non_amend")
            self.assertEqual(git(repo, "show", "-s", "--format=%an|%ae"), "Synthetic Global|synthetic-global@example.invalid\n")

    def test_commit_tree_explicitly_skips_gpg_signing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            marker = base / "gpg-invoked"
            gpg = base / "fake-gpg"
            gpg.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n", encoding="utf-8")
            gpg.chmod(0o755)
            git(repo, "config", "commit.gpgsign", "true")
            git(repo, "config", "gpg.program", str(gpg))
            (repo / "src/owned.txt").write_text("signed skip\n", encoding="utf-8")
            git(repo, "add", "src/owned.txt")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            committed = git_guard.apply(
                self.contract(repo, "commit_non_amend", snapshot, paths=["src/owned.txt"], message="skip signing")
            )
            self.assertEqual(committed["operation"], "commit_non_amend")
            self.assertFalse(marker.exists())

    def test_stage_rejects_repository_content_filter_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            (repo / "src/owned.txt").write_text("after\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=["src/owned.txt"])
            marker = base / "content-filter-ran"
            converter = base / "content-filter"
            converter.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n", encoding="utf-8")
            converter.chmod(0o755)
            (repo / ".gitattributes").write_text("src/*.txt filter=fixture\n", encoding="utf-8")
            git(repo, "config", "filter.fixture.clean", str(converter))
            git(repo, "config", "filter.fixture.smudge", str(converter))

            with self.assertRaisesRegex(git_guard.GitGuardError, "content filter/process"):
                git_guard.apply(contract)
            self.assertFalse(marker.exists())
            git(repo, "config", "--remove-section", "filter.fixture")
            self.assertEqual(git(repo, "diff", "--cached", "--name-only"), "")

    def test_forbidden_operation_and_branch_guards_are_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            clean = snapshot_digest.compute_snapshot(repo, kind="revision_only")
            forbidden = self.contract(repo, "push", clean)
            forbidden["allowed_operations"] = ["push"]
            with self.assertRaisesRegex(git_guard.GitGuardError, "allowlist|許可"):
                git_guard.apply(forbidden)

            created = git_guard.apply(self.contract(repo, "branch_create_and_switch_new", clean, new_branch="codex/guarded", base_ref="HEAD"))
            self.assertEqual(created["evidence"]["branch"], "codex/guarded")
            renamed_snapshot = snapshot_digest.compute_snapshot(repo, kind="revision_only")
            renamed = git_guard.apply(self.contract(repo, "rename_origin_unpushed_branch", renamed_snapshot, current_branch="codex/guarded", new_branch="codex/renamed"))
            self.assertEqual(renamed["evidence"]["branch"], "codex/renamed")


if __name__ == "__main__":
    unittest.main()
