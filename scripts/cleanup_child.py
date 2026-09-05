#!/usr/bin/env python3
"""Explicit, hash-checked retirement of an earlier repository-local v3 install."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import install


def tracked(repo: Path, rel: Path) -> bool:
    result = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--", rel.as_posix()],
                            env=install.git_environment(), check=True, capture_output=True)
    return bool(result.stdout)


def plan_cleanup(parent: Path, child: Path) -> tuple[dict, dict]:
    parent = install.canonical_guild_root(str(parent))
    parent_manifest = install.load_manifest(parent)
    if parent_manifest is None or parent_manifest.get("layout") != install.LAYOUT:
        raise install.InstallError("install the shared parent first")
    child = install.canonical_git_root(str(child))
    if not child.is_relative_to(parent / "repositories"):
        raise install.InstallError("--child must be an explicit Git root under the parent's repositories/")
    manifest = install.load_manifest(child)
    if manifest is None or manifest.get("schema") != 1 or manifest.get("distribution_version") != "3.0.0":
        raise install.InstallError("child has no recognized repository-local v3 manifest")
    actions = []
    for name, record in sorted(manifest["files"].items()):
        rel = Path(name)
        path = install.validate_destination(child, rel)
        if rel.parts[0] == ".git":
            action = "preserve-git-metadata"
        elif tracked(child, rel):
            action = "preserve-tracked"
        elif not path.exists():
            action = "absent"
        elif path.is_file() and path.stat().st_mode & 0o777 != install.desired_mode(rel):
            action = "preserve-modified-mode"
        elif install.current_hash(child, rel, record["kind"]) != record["sha256"]:
            action = "preserve-modified"
        else:
            action = "strip-managed-block" if rel == Path("AGENTS.md") else "remove-unchanged"
        actions.append({"path": name, "action": action})
    remaining = any(item["action"].startswith("preserve-") for item in actions)
    manifest_action = "preserve-manifest" if remaining or tracked(child, install.MANIFEST_REL) else "remove-unchanged"
    actions.append({"path": str(install.MANIFEST_REL), "action": manifest_action})
    return {"target": str(parent), "child": str(child), "actions": actions,
            "warnings": ["This explicit cleanup changes only verified, untracked distribution files. Modified/tracked files and user-owned config remain and can conflict with shared settings. Git index/config and ignore rules are never changed."]}, manifest


def execute(parent: Path, child: Path, dry_run: bool) -> dict:
    plan, manifest = plan_cleanup(parent, child)
    parent, child = Path(plan["target"]), Path(plan["child"])
    plan["dry_run"] = dry_run
    if dry_run:
        return plan
    touched = [Path(item["path"]) for item in plan["actions"]
               if item["action"] in {"remove-unchanged", "strip-managed-block"}]
    if not touched:
        return plan
    # Recheck classification immediately before backing up or deleting anything.
    if plan_cleanup(parent, child)[0]["actions"] != plan["actions"] or install.load_manifest(child) != manifest:
        raise install.InstallError("child installation changed during preflight; rerun dry-run")
    transaction = install.Transaction(child, touched)
    archive = None
    mutated = []
    archive_root_existed = (parent / install.ARCHIVE_ROOT_REL).exists()
    try:
        archive = install.archive_legacy(parent, [], label="child-v3-to-parent")
        (archive / "archive.json").write_text(json.dumps({
            "kind": "child-v3-cleanup", "child": str(child),
            "paths": [str(rel) for rel in touched],
        }, indent=2) + "\n")
        for rel in touched:
            original = install.validate_destination(child, rel)
            backup = archive / "child" / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, backup)
            if tracked(child, rel):
                raise install.InstallError(f"child path became tracked during cleanup: {rel}")
            if rel != install.MANIFEST_REL:
                record = manifest["files"][str(rel)]
                if install.current_hash(child, rel, record["kind"]) != record["sha256"] or original.stat().st_mode & 0o777 != install.desired_mode(rel):
                    raise install.InstallError(f"child path changed during cleanup: {rel}")
            elif install.load_manifest(child) != manifest:
                raise install.InstallError("child manifest changed during cleanup")
            # Refresh the rollback copy, including user text outside an AGENTS block.
            shutil.copy2(original, transaction.temp / rel)
            mutated.append(rel)
            if rel == Path("AGENTS.md"):
                remaining = install.strip_agents_block(original.read_text(encoding="utf-8"))
                if remaining:
                    install.write_atomic(original, remaining.encode())
                else:
                    original.unlink()
            else:
                original.unlink()
            directory = original.parent
            while directory != child:
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent
    except Exception:
        transaction.restore(mutated)
        if archive:
            shutil.rmtree(archive)
            if not archive_root_existed:
                try:
                    archive.parent.rmdir()
                except OSError:
                    pass
        raise
    finally:
        transaction.close()
    plan["archive"] = str(archive)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path, help="installed non-Git parent")
    parser.add_argument("--child", required=True, type=Path, help="one explicit old v3 child Git root")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.target, args.child, args.dry_run), ensure_ascii=False, indent=2))
        return 0
    except (install.InstallError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"cleanup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
