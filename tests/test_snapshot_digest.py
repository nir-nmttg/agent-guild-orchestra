from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "template/.agents/orchestra/scripts"
sys.path.insert(0, str(SCRIPTS))
import snapshot_digest  # noqa: E402


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False, env=env)
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


class SnapshotDigestTests(unittest.TestCase):
    def make_repo(self, directory: Path) -> Path:
        repo = directory / "repo"
        repo.mkdir()
        git(repo, "init", "--quiet")
        git(repo, "config", "user.name", "Snapshot Fixture")
        git(repo, "config", "user.email", "snapshot@example.invalid")
        (repo / "src").mkdir()
        (repo / "src/owned.txt").write_text("before\n", encoding="utf-8")
        git(repo, "add", "src/owned.txt")
        git(repo, "commit", "--quiet", "-m", "baseline")
        return repo

    def test_content_digest_is_stage_independent_and_rejects_host_injection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("after\n", encoding="utf-8")
            before = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src"], untracked_paths=[])
            git(repo, "add", "src/owned.txt")
            after = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src"], untracked_paths=[])
            self.assertEqual(before, after)

            fake_bin = Path(raw) / "fake-bin"
            fake_bin.mkdir()
            marker = Path(raw) / "fake-git-was-run"
            fake = fake_bin / "git"
            fake.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
            fake.chmod(0o755)
            injected = dict(os.environ)
            injected.update(
                {
                    "PATH": str(fake_bin),
                    "GIT_EXTERNAL_DIFF": "must-not-run",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.fsmonitor",
                    "GIT_CONFIG_VALUE_0": "must-not-run",
                    "GIT_OBJECT_DIRECTORY": str(Path(raw) / "outside-objects"),
                    "GIT_TRACE": str(Path(raw) / "trace"),
                }
            )
            command = [sys.executable, str(SCRIPTS / "snapshot_digest.py"), "--repo", str(repo), "--kind", "working_tree_content", "--scope", "src"]
            result = subprocess.run(command, text=True, capture_output=True, check=False, env=injected)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((Path(raw) / "trace").exists())

    def test_repository_content_filter_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            marker = base / "content-filter-ran"
            converter = base / "content-filter"
            converter.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n", encoding="utf-8")
            converter.chmod(0o755)
            (repo / ".gitattributes").write_text("src/*.txt filter=fixture\n", encoding="utf-8")
            git(repo, "config", "filter.fixture.clean", str(converter))
            git(repo, "config", "filter.fixture.smudge", str(converter))

            with self.assertRaisesRegex(snapshot_digest.SnapshotError, "content filter/process"):
                snapshot_digest.compute_snapshot(
                    repo,
                    kind="working_tree_content",
                    scope_paths=["src"],
                    untracked_paths=[],
                )
            self.assertFalse(marker.exists())
            git(repo, "config", "--remove-section", "filter.fixture")
            (repo / ".gitattributes").write_text("src/*.txt text eol=lf\n", encoding="utf-8")
            benign = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src"],
                untracked_paths=[],
            )
            self.assertEqual(benign["kind"], "working_tree_content")

    def test_index_filter_attribute_is_rejected_when_worktree_copy_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            attributes = repo / ".gitattributes"
            attributes.write_text("src/*.txt filter=fixture\n", encoding="utf-8")
            git(repo, "add", ".gitattributes")
            git(repo, "commit", "--quiet", "-m", "attribute fixture")
            attributes.unlink()

            with self.assertRaisesRegex(snapshot_digest.SnapshotError, "index.*content filter"):
                snapshot_digest.compute_snapshot(
                    repo,
                    kind="working_tree_content",
                    scope_paths=["src"],
                    untracked_paths=[],
                )

    def test_canonical_root_and_path_escape_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            alias = base / "repo-alias"
            alias.symlink_to(repo, target_is_directory=True)
            with self.assertRaises(snapshot_digest.SnapshotError):
                snapshot_digest.compute_snapshot(alias, kind="revision_only")

            outside = base / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            (repo / "link").symlink_to(outside)
            with self.assertRaises(snapshot_digest.SnapshotError):
                snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src"], untracked_paths=["link"])

            (repo / ".env").write_text("redacted\n", encoding="utf-8")
            with self.assertRaises(snapshot_digest.SnapshotError):
                snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src"], untracked_paths=[".env"])

    def test_source_named_credentials_is_allowed_while_env_stays_protected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            credentials = repo / "src/credentials.py"
            credentials.write_text("def load_credentials():\n    return {}\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/credentials.py"],
                untracked_paths=["src/credentials.py"],
            )
            self.assertEqual(snapshot["untracked_paths"], ["src/credentials.py"])
            (repo / ".env").write_text("synthetic\n", encoding="utf-8")
            with self.assertRaises(snapshot_digest.SnapshotError):
                snapshot_digest.compute_snapshot(
                    repo,
                    kind="working_tree_content",
                    scope_paths=[".env"],
                    untracked_paths=[".env"],
                )

    def test_stale_content_changes_digest_and_cli_emits_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            first = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src"], untracked_paths=[])
            (repo / "src/owned.txt").write_text("changed\n", encoding="utf-8")
            second = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src"], untracked_paths=[])
            self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
            output = subprocess.run(
                [sys.executable, str(SCRIPTS / "snapshot_digest.py"), "--repo", str(repo), "--kind", "revision_only"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(output.returncode, 1)
            self.assertIn("revision_only", output.stderr)

    def test_ordinary_and_linked_worktree_roots_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            primary_parent = base / "primary"
            primary_parent.mkdir()
            repo = self.make_repo(primary_parent)
            ordinary = snapshot_digest.compute_snapshot(repo, kind="revision_only")

            linked_parent = base / "separate" / "nested"
            linked_parent.mkdir(parents=True)
            linked = linked_parent / "linked-worktree"
            git(repo, "worktree", "add", "--detach", str(linked), "HEAD")
            try:
                linked_snapshot = snapshot_digest.compute_snapshot(linked, kind="revision_only")
            finally:
                # This only removes the temporary fixture's linked worktree;
                # production callers never receive a destructive operation.
                git(repo, "worktree", "remove", "--force", str(linked))
            self.assertEqual(linked_snapshot["revision_id"], ordinary["revision_id"])
            self.assertEqual(linked_snapshot["kind"], "revision_only")


if __name__ == "__main__":
    unittest.main()
