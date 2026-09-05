"""Run the stateless guard test suite and CLI discovery smoke."""

from __future__ import annotations

import subprocess
import sys

from .core import ROOT, require


def validate_runtime_smoke() -> None:
    suite = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-p", "test_*.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    require(suite.returncode == 0, f"guard test suite failed:\n{suite.stdout}\n{suite.stderr}")
    for name in ("boundary_guard.py", "snapshot_digest.py", "git_guard.py"):
        result = subprocess.run(
            [sys.executable, str(ROOT / "template/.agents/orchestra/scripts" / name), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(result.returncode == 0 and "usage:" in result.stdout, f"{name} CLI help failed: {result.stderr}")
