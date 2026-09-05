from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "template/.agents/orchestra/scripts"
sys.path.insert(0, str(SCRIPTS))
import git_guard  # noqa: E402
import snapshot_digest  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
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
            with self.assertRaisesRegex(git_guard.GitGuardError, "unrelated"):
                git_guard.apply(self.contract(repo, "stage_exact_paths_or_hunks", snapshot, paths=["src/owned.txt"]))

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
