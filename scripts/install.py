#!/usr/bin/env python3
"""Install the Agent Guild Orchestra Codex template into one Git repository.

The installer deliberately has no orchestration runtime. It stages a static
distribution, validates every destination before writing, records hashes from
the deployed files, and restores the previous tree if any write fails.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "template"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
AGENTS_START = "<!-- agent-guild-orchestra:start -->"
AGENTS_END = "<!-- agent-guild-orchestra:end -->"
EXCLUDE_START = "# agent-guild-orchestra:start"
EXCLUDE_END = "# agent-guild-orchestra:end"
MANIFEST_REL = Path(".agents/orchestra/install-manifest.json")
ARCHIVE_ROOT_REL = Path(".agent-guild-orchestra-archives")
EXCLUDE_REL = Path(".git/info/exclude")
ARCHIVE_EXCLUDE_BLOCK = (
    f"{EXCLUDE_START}\n"
    "/.agent-guild-orchestra-archives/\n"
    f"{EXCLUDE_END}"
)
MANIFEST_SCHEMA = 1

LEGACY_AGENT_NAMES = {
    "adventurer", "artificer", "captain", "cartographer", "courier",
    "examiner", "guildmaster", "inquisitor", "sage", "warden",
}
LEGACY_SKILL_NAMES = {
    "branch-implementation-final-review", "browser-research-readonly",
    "communicate-work-estimates", "create-skill-candidate-from-gap",
    "explain-clearly", "git-branch-from-session",
    "git-rename-unpushed-branch-from-diff", "git-split-commits-from-diff",
    "github-pull-request-from-branch", "github-safe-push-from-branch",
    "implementation-behavior-verification", "open-subrepo-in-vscode",
    "orchestra-instruction-contract-review", "orchestra-runtime-security-audit",
    "orchestra-validation-review", "pull-request-description-from-branch",
    "quest-awareness-loop", "refine-design-plan",
    "repository-design-mapmaking", "use-guild-workflow",
}


class InstallError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Codex用Agent Guild OrchestraテンプレートをGitリポジトリへ導入します。"
    )
    parser.add_argument("--target", help="導入先となる実Git root")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="検証済みtemplate directory")
    parser.add_argument("--allow-non-default-source", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="変更予定をJSONで表示し、書き込みません")
    parser.add_argument("--major-upgrade", action="store_true", help="v2導入物をcold archiveしてv3へ更新します")
    parser.add_argument(
        "--legacy-root",
        help="v2がGit repository外の旧Guild rootにある場合、そのabsolute pathを明示します",
    )
    parser.add_argument(
        "--with-skill", action="append", default=[], metavar="NAME",
        help="maintainer-skills/ または optional-skills/ のskillを明示的に追加します",
    )
    parser.add_argument(
        "--without-skill", action="append", default=[], metavar="NAME",
        help="以前に選択した追加skillを管理対象から外します",
    )
    parser.add_argument("--list-skills", action="store_true", help="追加可能なskill名を表示して終了します")
    return parser.parse_args(argv)


def safe_rel(value: str | Path) -> Path:
    raw = str(value)
    if "\x00" in raw or "\\" in raw:
        raise InstallError(f"unsafe relative path: {value}")
    rel = PurePosixPath(raw.replace(os.sep, "/"))
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise InstallError(f"unsafe relative path: {value}")
    return Path(*rel.parts)


def iter_files(root: Path) -> Iterable[tuple[Path, Path]]:
    if not root.is_dir() or root.is_symlink():
        raise InstallError(f"source directory is missing or unsafe: {root}")
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.relative_to(root).parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise InstallError(f"source symlink is not allowed: {path}")
        if path.is_file():
            yield path.relative_to(root), path


def package_catalog(source: Path) -> dict[str, tuple[str, Path]]:
    catalog: dict[str, tuple[str, Path]] = {}
    distribution_root = source.parent
    for category in ("maintainer-skills", "optional-skills"):
        package_root = distribution_root / category
        if not package_root.exists():
            continue
        for item in sorted(package_root.iterdir()):
            if item.is_dir() and not item.is_symlink() and (item / "SKILL.md").is_file():
                if item.name in catalog:
                    raise InstallError(f"duplicate packaged skill: {item.name}")
                catalog[item.name] = (category, item)
    return catalog


def canonical_git_root(target_arg: str) -> Path:
    target = Path(target_arg).expanduser()
    if target.is_symlink():
        raise InstallError("target itself may not be a symlink")
    target = target.resolve()
    if not target.is_dir():
        raise InstallError(f"target directory does not exist: {target}")
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise InstallError("target must be an existing Git working-tree root")
    root = Path(result.stdout.strip()).resolve()
    if root != target:
        raise InstallError(f"target must be the canonical Git root: {root}")
    return target


def canonical_legacy_root(legacy_arg: str, target: Path) -> Path:
    raw = Path(legacy_arg).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise InstallError("legacy root must be an explicit absolute path without parent traversal")
    if raw.is_symlink():
        raise InstallError("legacy root itself may not be a symlink")
    root = raw.resolve()
    if not root.is_dir():
        raise InstallError(f"legacy root directory does not exist: {root}")
    if (root / ".git").exists():
        raise InstallError("--legacy-root is for the non-Git v2 Guild root; use --target alone for an in-repository v2 install")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise InstallError("target Git root must be nested beneath the explicit legacy root") from exc
    if target == root:
        raise InstallError("legacy root and target Git root must be distinct")
    return root


def validate_destination(target: Path, rel: Path) -> Path:
    rel = safe_rel(rel)
    cursor = target
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise InstallError(f"managed path crosses a symlink: {rel}")
    try:
        cursor.resolve(strict=False).relative_to(target)
    except ValueError as exc:
        raise InstallError(f"managed path escapes target: {rel}") from exc
    return cursor


def extract_block(text: str) -> str | None:
    start = text.find(AGENTS_START)
    end = text.find(AGENTS_END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < start or text.find(AGENTS_START, start + 1) >= 0 or text.find(AGENTS_END, end + 1) >= 0:
        raise InstallError("AGENTS.md has malformed or duplicate managed markers")
    return text[start : end + len(AGENTS_END)]


def strip_exclude_block(text: str) -> str:
    start = text.find(EXCLUDE_START)
    end = text.find(EXCLUDE_END)
    if start < 0 and end < 0:
        return text
    if start < 0 or end < start or text.find(EXCLUDE_START, start + 1) >= 0 or text.find(EXCLUDE_END, end + 1) >= 0:
        raise InstallError(".git/info/exclude has malformed or duplicate legacy markers")
    result = text[:start] + text[end + len(EXCLUDE_END) :]
    return result.strip("\n") + ("\n" if result.strip("\n") else "")


def extract_exclude_block(text: str) -> str | None:
    start = text.find(EXCLUDE_START)
    end = text.find(EXCLUDE_END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < start or text.find(EXCLUDE_START, start + 1) >= 0 or text.find(EXCLUDE_END, end + 1) >= 0:
        raise InstallError(".git/info/exclude has malformed or duplicate managed markers")
    return text[start : end + len(EXCLUDE_END)]


def replace_exclude_block(existing: str, managed: str) -> str:
    old = extract_exclude_block(existing)
    managed = managed.strip() + "\n"
    if old is None:
        prefix = existing.rstrip()
        return (prefix + "\n" if prefix else "") + managed
    start = existing.index(old)
    return existing[:start] + managed.rstrip() + existing[start + len(old) :]


def replace_block(existing: str, managed: str) -> str:
    old = extract_block(existing)
    managed = managed.strip() + "\n"
    if old is None:
        prefix = existing.rstrip()
        return (prefix + "\n\n" if prefix else "") + managed
    start = existing.index(old)
    return existing[:start] + managed.rstrip() + existing[start + len(old) :]


def strip_agents_block(text: str) -> str:
    old = extract_block(text)
    if old is None:
        return text
    start = text.index(old)
    result = text[:start] + text[start + len(old) :]
    return result.strip("\n") + ("\n" if result.strip("\n") else "")


def load_manifest(target: Path) -> dict[str, object] | None:
    path = validate_destination(target, MANIFEST_REL)
    if not path.exists():
        return None
    if not path.is_file():
        raise InstallError(f"manifest is not a regular file: {MANIFEST_REL}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read installed manifest: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA or not isinstance(value.get("files"), dict):
        raise InstallError("installed manifest is unsupported; review it before changing the installation")
    return value


def v2_evidence(target: Path) -> bool:
    agents = target / "AGENTS.md"
    if agents.is_file():
        try:
            if extract_block(agents.read_text(encoding="utf-8")) is not None:
                return True
        except (OSError, UnicodeError, InstallError):
            return True
    exclude = target / ".git/info/exclude"
    if exclude.is_file():
        try:
            if EXCLUDE_START in exclude.read_text(encoding="utf-8") or EXCLUDE_END in exclude.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeError):
            return True
    return any(
        (target / rel).exists()
        for rel in (
            ".agents/orchestra/config/settings.yaml",
            ".agents/orchestra/queue",
            ".codex/hooks/stop_quality_gate.py",
            ".orchestra/queue/state.sqlite",
        )
    )


def legacy_paths(target: Path) -> list[Path]:
    values = [Path(".agents/orchestra"), Path(".orchestra/queue"), Path(".orchestra/dashboard.md")]
    values += [Path(".codex/agents") / f"{name}.toml" for name in sorted(LEGACY_AGENT_NAMES)]
    values += [Path(".agents/skills") / name for name in sorted(LEGACY_SKILL_NAMES)]
    values += [Path(".codex/config.toml"), Path(".codex/hooks.json"), Path(".codex/hooks")]
    agents = target / "AGENTS.md"
    if agents.is_file() and extract_block(agents.read_text(encoding="utf-8")) is not None:
        values.append(Path("AGENTS.md"))
    exclude = target / ".git/info/exclude"
    if exclude.is_file():
        exclude_text = exclude.read_text(encoding="utf-8")
        if EXCLUDE_START in exclude_text or EXCLUDE_END in exclude_text:
            values.append(Path(".git/info/exclude"))
    result = [rel for rel in values if validate_destination(target, rel).exists()]
    for rel in result:
        path = validate_destination(target, rel)
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_symlink():
                    raise InstallError(f"legacy managed tree contains a symlink and needs manual review: {child.relative_to(target)}")
    return result


def current_hash(target: Path, rel: Path, kind: str) -> str | None:
    path = validate_destination(target, rel)
    if kind == "agents_block":
        if not path.is_file():
            return None
        block = extract_block(path.read_text(encoding="utf-8"))
        return sha256_bytes(block.encode()) if block is not None else None
    if kind == "exclude_block":
        if not path.is_file():
            return None
        block = extract_exclude_block(path.read_text(encoding="utf-8"))
        return sha256_bytes(block.encode()) if block is not None else None
    if not path.is_file() or path.is_symlink():
        return None
    return sha256_file(path)


def desired_mode(rel: Path) -> int:
    return 0o755 if "scripts" in rel.parts and rel.suffix in {".py", ".sh"} else 0o644


def managed_kind(rel: Path) -> str:
    if rel == Path("AGENTS.md"):
        return "agents_block"
    if rel == EXCLUDE_REL:
        return "exclude_block"
    return "file"


def build_candidate(source: Path, selected: set[str], catalog: dict[str, tuple[str, Path]]) -> dict[Path, bytes]:
    candidate: dict[Path, bytes] = {}
    for rel, path in iter_files(source):
        rel = safe_rel(rel)
        if rel == Path("AGENTS.md"):
            continue
        allowed = (
            rel == Path(".codex/config.toml")
            or (len(rel.parts) == 3 and rel.parts[:2] == (".codex", "agents") and rel.suffix == ".toml")
            or rel == Path(".agents/orchestra/README.md")
            or (len(rel.parts) == 4 and rel.parts[:3] == (".agents", "orchestra", "scripts") and rel.suffix == ".py")
            or (len(rel.parts) >= 4 and rel.parts[:2] == (".agents", "skills"))
        )
        if not allowed:
            raise InstallError(f"unexpected template distribution path: {rel}")
        if rel in candidate:
            raise InstallError(f"duplicate source destination: {rel}")
        candidate[rel] = path.read_bytes()
    for name in sorted(selected):
        if name not in catalog:
            raise InstallError(f"unknown packaged skill: {name}")
        for child, path in iter_files(catalog[name][1]):
            rel = Path(".agents/skills") / name / child
            if rel in candidate:
                raise InstallError(f"packaged skill collides with template: {rel}")
            candidate[rel] = path.read_bytes()
    agents_source = source / "AGENTS.md"
    if not agents_source.is_file() or agents_source.is_symlink():
        raise InstallError("template/AGENTS.md is required and must be a regular file")
    source_text = agents_source.read_text(encoding="utf-8")
    block = extract_block(source_text)
    if block is None:
        block = f"{AGENTS_START}\n{source_text.strip()}\n{AGENTS_END}"
    candidate[Path("AGENTS.md")] = block.encode()
    return candidate


class Transaction:
    def __init__(self, target: Path, touched: Iterable[Path]):
        self.target = target
        self.temp = Path(tempfile.mkdtemp(prefix="agent-guild-orchestra-transaction-"))
        self.existed: set[Path] = set()
        self.existing_dirs: set[Path] = {Path(".")}
        for rel in set(touched):
            for parent in rel.parents:
                if parent == Path("."):
                    break
                if validate_destination(target, parent).is_dir():
                    self.existing_dirs.add(parent)
        try:
            for rel in sorted(set(touched)):
                src = validate_destination(target, rel)
                if src.exists():
                    self.existed.add(rel)
                    dst = self.temp / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.is_dir():
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
        except Exception:
            self.close()
            raise

    def restore(self, touched: Iterable[Path]) -> None:
        for rel in sorted(set(touched), key=lambda p: len(p.parts), reverse=True):
            dst = validate_destination(self.target, rel)
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            if rel in self.existed:
                src = self.temp / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
        parents = {parent for rel in set(touched) for parent in rel.parents if parent != Path(".")}
        for rel in sorted(parents, key=lambda p: len(p.parts), reverse=True):
            path = validate_destination(self.target, rel)
            if rel not in self.existing_dirs and path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    def close(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)


def archive_legacy(target: Path, rels: list[Path]) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = validate_destination(target, ARCHIVE_ROOT_REL)
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.mkdtemp(prefix=f"v2-to-v3-{stamp}-", dir=archive_root))
    try:
        for rel in rels:
            src = validate_destination(target, rel)
            if not src.exists():
                continue
            dst = archive / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        metadata = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "paths": [p.as_posix() for p in rels]}
        (archive / "archive.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return archive
    except Exception:
        shutil.rmtree(archive, ignore_errors=True)
        try:
            archive_root.rmdir()
        except OSError:
            pass
        raise


def remove_existing(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def stage_candidate(candidate: dict[Path, bytes]) -> Path:
    stage = Path(tempfile.mkdtemp(prefix="agent-guild-orchestra-stage-"))
    try:
        for rel, data in candidate.items():
            path = stage / safe_rel(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def deactivate_legacy(root: Path, rels: list[Path], *, replacing_in_same_target: bool) -> None:
    for rel in sorted(rels, key=lambda path: len(path.parts), reverse=True):
        destination = validate_destination(root, rel)
        if rel == EXCLUDE_REL:
            if replacing_in_same_target:
                # The narrow v3 archive exclusion is installed below.
                continue
            remaining = strip_exclude_block(destination.read_text(encoding="utf-8"))
            if remaining:
                write_atomic(destination, remaining.encode())
            else:
                remove_existing(destination)
        elif rel == Path("AGENTS.md"):
            if replacing_in_same_target:
                # Candidate block replacement below preserves user content.
                continue
            remaining = strip_agents_block(destination.read_text(encoding="utf-8"))
            if remaining:
                write_atomic(destination, remaining.encode())
            else:
                remove_existing(destination)
        else:
            remove_existing(destination)


def plan_install(
    args: argparse.Namespace,
) -> tuple[Path, Path | None, dict[str, object], dict[Path, bytes], list[dict[str, str]], list[Path], dict[str, object]]:
    target = canonical_git_root(args.target)
    if args.legacy_root and not args.major_upgrade:
        raise InstallError("--legacy-root requires --major-upgrade")
    external_legacy_root = canonical_legacy_root(args.legacy_root, target) if args.legacy_root else None
    source = Path(args.source).expanduser().resolve()
    if source != DEFAULT_SOURCE.resolve() and not args.allow_non_default_source:
        raise InstallError("non-default source requires --allow-non-default-source")
    catalog = package_catalog(source)
    manifest = load_manifest(target)
    previous_selected = set(manifest.get("selected_skills", [])) if manifest else set()
    selected = (previous_selected | set(args.with_skill)) - set(args.without_skill)
    candidate = build_candidate(source, selected, catalog)
    target_legacy = manifest is None and v2_evidence(target)
    if target_legacy and external_legacy_root is not None:
        raise InstallError("v2 evidence exists in both target and --legacy-root; migrate one explicit root at a time")
    migration_root = external_legacy_root or (target if target_legacy else None)
    if external_legacy_root is not None and not v2_evidence(external_legacy_root):
        raise InstallError("--legacy-root does not contain recognizable v2 managed evidence")
    if migration_root is not None and not args.major_upgrade:
        raise InstallError("v2 installation detected; rerun --dry-run --major-upgrade, then perform the explicit major upgrade")
    if args.major_upgrade and migration_root is None:
        raise InstallError("--major-upgrade requires recognizable v2 managed evidence")
    if args.major_upgrade and manifest is not None and external_legacy_root is None:
        raise InstallError("--major-upgrade is only for a hashless v2 installation")
    agents_path = target / "AGENTS.md"
    if agents_path.exists():
        if not agents_path.is_file() or agents_path.is_symlink():
            raise InstallError("AGENTS.md must be a regular file")
        extract_block(agents_path.read_text(encoding="utf-8"))
    previous_files = manifest.get("files", {}) if manifest else {}
    if not isinstance(previous_files, dict):
        raise InstallError("installed manifest files map is invalid")
    actions: list[dict[str, str]] = []
    legacy_rels = legacy_paths(migration_root) if migration_root is not None else []
    if external_legacy_root is not None:
        for rel in [*legacy_rels, ARCHIVE_ROOT_REL]:
            managed_root = validate_destination(external_legacy_root, rel)
            try:
                target.relative_to(managed_root)
            except ValueError:
                continue
            raise InstallError(f"target Git root may not be nested inside legacy managed/archive path: {rel}")
    previous_exclude = previous_files.get(EXCLUDE_REL.as_posix())
    if target_legacy or (isinstance(previous_exclude, dict) and previous_exclude.get("kind") == "exclude_block"):
        candidate[EXCLUDE_REL] = ARCHIVE_EXCLUDE_BLOCK.encode()
    if migration_root is not None:
        archive_root = validate_destination(migration_root, ARCHIVE_ROOT_REL)
        if archive_root.exists() and not archive_root.is_dir():
            raise InstallError(f"archive root is not a directory: {ARCHIVE_ROOT_REL}")
    for rel, data in candidate.items():
        kind = managed_kind(rel)
        destination = validate_destination(target, rel)
        desired_hash = sha256_bytes(data)
        previous = previous_files.get(rel.as_posix())
        if isinstance(previous, dict):
            previous_hash = previous.get("sha256")
            previous_kind = previous.get("kind")
            actual_hash = current_hash(target, rel, str(previous_kind))
            if actual_hash != previous_hash and desired_hash != previous_hash:
                raise InstallError(f"managed file changed locally and in distribution: {rel}")
            if actual_hash == desired_hash:
                action = "keep"
            elif actual_hash != previous_hash and desired_hash == previous_hash:
                action = "preserve-local"
            else:
                action = "update"
        elif rel == Path("AGENTS.md") and destination.is_file() and extract_block(destination.read_text(encoding="utf-8")) is None:
            action = "create"
        elif destination.exists() and not target_legacy:
            actual_hash = current_hash(target, rel, kind)
            if actual_hash != desired_hash:
                raise InstallError(f"unmanaged destination collision: {rel}")
            action = "adopt-identical"
        else:
            covered = migration_root == target and any(rel == item or item in rel.parents for item in legacy_rels)
            action = "replace-legacy" if target_legacy and covered else "create"
        actions.append({"action": action, "path": rel.as_posix()})
    candidate_paths = set(candidate)
    for rel_text, old in previous_files.items():
        rel = safe_rel(rel_text)
        if rel in candidate_paths or not isinstance(old, dict):
            continue
        actual_hash = current_hash(target, rel, str(old.get("kind")))
        action = "remove" if actual_hash == old.get("sha256") else "preserve-local-removed"
        actions.append({"action": action, "path": rel.as_posix()})
    result_manifest: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "distribution_version": VERSION,
        "installed_at": (
            manifest["installed_at"]
            if manifest is not None and isinstance(manifest.get("installed_at"), str) and manifest["installed_at"]
            else dt.datetime.now(dt.timezone.utc).isoformat()
        ),
        "selected_skills": sorted(selected),
        "files": {},
    }
    actions_by_path = {Path(item["path"]): item["action"] for item in actions}
    planned_files: dict[str, dict[str, str]] = {}
    for rel, data in sorted(candidate.items()):
        action = actions_by_path[rel]
        if action == "preserve-local":
            previous = previous_files.get(rel.as_posix())
            assert isinstance(previous, dict)
            planned_files[rel.as_posix()] = {
                "kind": str(previous["kind"]),
                "sha256": str(previous["sha256"]),
            }
        else:
            planned_files[rel.as_posix()] = {
                "kind": managed_kind(rel),
                "sha256": sha256_bytes(data),
            }
    result_manifest["files"] = planned_files
    manifest_data = (json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n").encode()
    manifest_path = validate_destination(target, MANIFEST_REL)
    if not manifest_path.exists():
        manifest_action = "create"
    elif manifest_path.read_bytes() == manifest_data:
        manifest_action = "keep"
    else:
        manifest_action = "update"
    actions.append({"action": manifest_action, "path": MANIFEST_REL.as_posix()})
    return target, migration_root, result_manifest, candidate, actions, legacy_rels, previous_files


def execute(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    if args.list_skills:
        print(json.dumps({name: category for name, (category, _) in package_catalog(source).items()}, ensure_ascii=False, indent=2))
        return 0
    if not args.target:
        raise InstallError("--target is required unless --list-skills is used")
    target, migration_root, result_manifest, candidate, actions, legacy_rels, previous_files = plan_install(args)
    plan = {
        "target": str(target), "version": VERSION, "dry_run": args.dry_run,
        "major_upgrade": bool(legacy_rels), "archive_paths": [p.as_posix() for p in legacy_rels],
        "legacy_root": str(migration_root) if migration_root is not None else None,
        "legacy_actions": [
            {
                "action": (
                    "archive-and-strip-managed-block"
                    if rel in {Path("AGENTS.md"), EXCLUDE_REL}
                    else "archive-and-remove-managed"
                ),
                "path": rel.as_posix(),
            }
            for rel in legacy_rels
        ],
        "actions": actions,
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    changed = {Path(item["path"]) for item in actions if item["action"] in {"create", "update", "remove", "replace-legacy"}}
    target_legacy_rels = set(legacy_rels) if migration_root == target else set()
    external_legacy_rels = set(legacy_rels) if migration_root is not None and migration_root != target else set()
    touched = set(changed) | target_legacy_rels
    stage = stage_candidate(candidate)
    try:
        transaction = Transaction(target, touched)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    legacy_transaction: Transaction | None = None
    if external_legacy_rels:
        assert migration_root is not None
        try:
            legacy_transaction = Transaction(migration_root, external_legacy_rels)
        except Exception:
            transaction.close()
            shutil.rmtree(stage, ignore_errors=True)
            raise
    archive: Path | None = None
    try:
        if legacy_rels:
            assert migration_root is not None
            archive = archive_legacy(migration_root, legacy_rels)
            deactivate_legacy(migration_root, legacy_rels, replacing_in_same_target=migration_root == target)
        actions_by_path = {Path(item["path"]): item["action"] for item in actions}
        for rel, desired in sorted(candidate.items()):
            desired = (stage / rel).read_bytes()
            action = actions_by_path[rel]
            destination = validate_destination(target, rel)
            if action == "preserve-local":
                continue
            if action in {"keep", "adopt-identical"}:
                continue
            if rel == Path("AGENTS.md"):
                existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
                managed = desired.decode("utf-8")
                write_atomic(destination, replace_block(existing, managed).encode())
                block = extract_block(destination.read_text(encoding="utf-8"))
                assert block is not None
                os.chmod(destination, desired_mode(rel))
            elif rel == EXCLUDE_REL:
                existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
                write_atomic(destination, replace_exclude_block(existing, desired.decode("utf-8")).encode())
                block = extract_exclude_block(destination.read_text(encoding="utf-8"))
                assert block is not None
            else:
                write_atomic(destination, desired)
                os.chmod(destination, desired_mode(rel))
        for item in actions:
            if item["action"] == "remove":
                removed = validate_destination(target, Path(item["path"]))
                remove_existing(removed)
                parent = removed.parent
                while parent != target:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        if actions_by_path[MANIFEST_REL] in {"create", "update"}:
            write_atomic(
                validate_destination(target, MANIFEST_REL),
                (json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n").encode(),
            )
    except Exception:
        transaction.restore(touched)
        if legacy_transaction is not None:
            legacy_transaction.restore(external_legacy_rels)
        if archive is not None:
            shutil.rmtree(archive, ignore_errors=True)
            try:
                archive.parent.rmdir()
            except OSError:
                pass
        raise
    finally:
        transaction.close()
        if legacy_transaction is not None:
            legacy_transaction.close()
        shutil.rmtree(stage, ignore_errors=True)
    plan["archive"] = str(archive) if archive else None
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return execute(parse_args(argv))
    except (InstallError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
