"""Exercise host launchers without requiring Docker inside the test container."""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DockerLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="orchestra-launcher-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.parent = self.base / 'parent with space, and "quote"'
        self.repo = self.parent / "repositories/app"
        self.repo.mkdir(parents=True)
        self.git(self.repo, "init", "-q")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.capture = self.base / "docker-args.json"
        docker = self.bin / "docker"
        docker.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1] == 'build': print('sha256:test-image')\n"
            "elif sys.argv[1] == 'run':\n"
            "    Path(os.environ['DOCKER_TEST_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
            "    print('{}')\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        python = self.bin / "python3"
        python.write_text("#!/bin/sh\necho 'host Python must not run' >&2\nexit 97\n", encoding="utf-8")
        python.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.defpath}", DOCKER_TEST_CAPTURE=str(self.capture))

    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def launch(self, script: str, *args: str, success: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["bash", str(ROOT / "scripts" / script), *args], env=self.env, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def mounts(self) -> dict[str, bool]:
        args = json.loads(self.capture.read_text())
        result = {}
        for index, value in enumerate(args):
            if value == "--mount":
                fields = next(csv.reader([args[index + 1]]))
                options = dict(field.split("=", 1) for field in fields if "=" in field)
                self.assertEqual(options["source"], options["target"])
                result[options["source"]] = "readonly" in fields
        return result

    def test_install_and_sync_use_container_python_and_preserve_arguments(self) -> None:
        for script in ("install.sh", "sync.sh"):
            with self.subTest(script=script):
                self.launch(script, f"--target={self.parent}", "--config-mode", "user-owned", "--with-skill", "orchestra-contract-validation")
                args = json.loads(self.capture.read_text())
                self.assertEqual(args[args.index("python3") + 1], str(ROOT / "scripts/install.py"))
                self.assertEqual(args[-2:], ["--target", str(self.parent)])
                self.assertIn("user-owned", args)
                self.assertIn(f"{os.getuid()}:{os.getgid()}", args)
                self.assertFalse(self.mounts()[str(self.parent)])
                self.assertTrue(self.mounts()[str(self.parent / "repositories")])
                self.assertTrue(self.mounts()[str(ROOT)])

    def test_dry_run_mounts_parent_and_children_read_only(self) -> None:
        self.launch("install.sh", "--target", str(self.parent), "--dry-run")
        self.assertTrue(self.mounts()[str(self.parent)])
        self.assertTrue(self.mounts()[str(self.parent / "repositories")])
        self.assertFalse((self.parent / "AGENTS.md").exists())

    def test_cleanup_linked_worktree_mounts_git_metadata_read_only(self) -> None:
        self.git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "fixture")
        linked = self.parent / "repositories/linked"
        self.git(self.repo, "worktree", "add", "--detach", "-q", str(linked), "HEAD")
        self.launch("cleanup-child.sh", "--target", str(self.parent), "--child", str(linked))
        mounts = self.mounts()
        self.assertFalse(mounts[str(linked)])
        self.assertTrue(mounts[str(linked / ".git")])
        self.assertTrue(mounts[str(self.repo / ".git")])
        self.assertTrue(mounts[self.git(linked, "rev-parse", "--absolute-git-dir")])

    def test_git_target_and_symlink_are_rejected_before_docker(self) -> None:
        result = self.launch("install.sh", "--target", str(self.repo), success=False)
        self.assertIn("non-Git parent", result.stderr)
        link = self.base / "alias"
        link.symlink_to(self.parent, target_is_directory=True)
        for spelling in (str(link), f"{link}/", f"{link}/."):
            result = self.launch("install.sh", "--target", spelling, success=False)
            self.assertIn("symlink", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_missing_path_and_child_outside_parent_are_rejected(self) -> None:
        result = self.launch("install.sh", "--target", "--dry-run", success=False)
        self.assertIn("requires a directory", result.stderr)
        result = self.launch("cleanup-child.sh", "--target", str(self.parent), "--child", str(self.base), success=False)
        self.assertIn("explicit parent/repositories/", result.stderr)

    def test_validation_needs_no_host_python_or_write_mount(self) -> None:
        self.launch("docker-run.sh", "validate")
        self.assertEqual(self.mounts(), {str(ROOT): True})


if __name__ == "__main__":
    unittest.main()
