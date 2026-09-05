"""Check the public installer CLI against a disposable non-Git parent.

Transactional, migration and full child-tree invariants live in test_parent_install.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

from .core import ROOT, require


def validate_install_upgrade_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="guild-parent-cli-") as raw:
        target = Path(raw).resolve()
        for options in (["--dry-run"], [], []):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/install.py"),
                                     "--target", str(target), *options], capture_output=True, text=True)
            require(result.returncode == 0, result.stderr)
            plan = json.loads(result.stdout)
            require(plan["layout"] == "guild-parent", "installer lost shared-parent layout")
            if options:
                require(not list(target.iterdir()), "dry-run wrote files")
        require(all(item["action"] == "keep" for item in plan["actions"]), "sync is not idempotent")
