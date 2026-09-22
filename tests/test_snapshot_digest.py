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

    def test_bracketed_scope_is_literal_and_tracks_untracked_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            scope = "app/blog/[slug]"
            owned, decoy = f"{scope}/page.tsx", "app/blog/s/page.tsx"
            for relative in (owned, decoy):
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("baseline\n", encoding="utf-8")
            git(repo, "--literal-pathspecs", "add", "--", owned, decoy)
            git(repo, "commit", "--quiet", "-m", "route baseline")
            before = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[scope])
            (repo / owned).write_text("owned change\n", encoding="utf-8")
            changed = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[scope])
            self.assertNotEqual(before["snapshot_id"], changed["snapshot_id"])
            (repo / decoy).write_text("outside change\n", encoding="utf-8")
            self.assertEqual(changed, snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[scope]))
            added = f"{scope}/loading.tsx"
            (repo / added).write_text("new file\n", encoding="utf-8")
            with self.assertRaisesRegex(snapshot_digest.SnapshotError, "実 untracked path 集合"):
                snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[scope])
            complete = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[scope], untracked_paths=[added])
            (repo / added).write_text("changed new file\n", encoding="utf-8")
            updated = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[scope], untracked_paths=[added])
            self.assertNotEqual(complete["snapshot_id"], updated["snapshot_id"])

    def test_bracketed_paths_keep_symlink_and_path_restrictions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            directory = repo / "app/[slug]"
            directory.mkdir(parents=True)
            outside = base / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            link = directory / "page.tsx"
            link.symlink_to(outside)
            with self.assertRaisesRegex(snapshot_digest.SnapshotError, "安全に開けません"):
                snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["app/[slug]"], untracked_paths=["app/[slug]/page.tsx"])
            git(repo, "--literal-pathspecs", "add", "--", "app/[slug]/page.tsx")
            with self.assertRaisesRegex(snapshot_digest.SnapshotError, "symlink"):
                snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["app/[slug]"])
            git(repo, "commit", "--quiet", "-m", "symlink fixture")
            link.unlink()
            with self.assertRaisesRegex(snapshot_digest.SnapshotError, "tracked symlink"):
                snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["app/[slug]"])
            for path in ("app/[slug]/../outside.txt", "app/[slug]/.git/config", "app/[slug]/.env.local", "app/[slug]/*.tsx", "app/[slug]/?.tsx", "app/[slug]/{a,b}.tsx", ":(glob)app/[slug]/page.tsx"):
                with self.subTest(path=path), self.assertRaises(snapshot_digest.SnapshotError):
                    snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[path])

    def test_bracketed_directory_cannot_be_replaced_by_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = self.make_repo(base)
            owned = "app/[slug]/page.tsx"
            path = repo / owned
            path.parent.mkdir(parents=True)
            path.write_text("tracked route\n", encoding="utf-8")
            git(repo, "--literal-pathspecs", "add", "--", owned)
            git(repo, "commit", "--quiet", "-m", "route baseline")
            outside = base / "outside"
            outside.mkdir()
            (outside / "page.tsx").write_text("outside\n", encoding="utf-8")
            path.unlink()
            path.parent.rmdir()
            path.parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(snapshot_digest.SnapshotError, "symlink path または ancestor"):
                snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=[owned])

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
