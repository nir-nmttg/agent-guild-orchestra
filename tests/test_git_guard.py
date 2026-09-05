from __future__ import annotations

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

    def contract(self, repo: Path, operation: str, snapshot: dict[str, object], *, paths: list[str] | None = None, **scope: object) -> dict[str, object]:
        path_scope: dict[str, object] = {"paths": paths or [], **scope}
        return {
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
            committed = git_guard.apply(contract)
            self.assertEqual(committed["evidence"]["committed_paths"], sorted([old, new]))
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
