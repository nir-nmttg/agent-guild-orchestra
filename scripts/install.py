#!/usr/bin/env python3
"""Install the Agent Guild Orchestra Codex template into one non-Git parent workspace.

The installer deliberately has no orchestration runtime. It prepares a static
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
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "template"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
AGENTS_START = "<!-- agent-guild-orchestra:start -->"
AGENTS_END = "<!-- agent-guild-orchestra:end -->"
EXCLUDE_START = "# agent-guild-orchestra:start"
EXCLUDE_END = "# agent-guild-orchestra:end"
MANIFEST_REL = Path(".agents/orchestra/install-manifest.json")
CONFIG_REL = Path(".codex/config.toml")
ARCHIVE_ROOT_REL = Path(".agent-guild-orchestra-archives")
RECOVERY_ROOT_REL = Path(".agent-guild-orchestra-recovery")
EXCLUDE_REL = Path(".git/info/exclude")
MANIFEST_SCHEMA = 2
LAYOUT = "guild-parent"
CONFIG_MODES = {"managed", "user-owned"}
MANAGED_KINDS = {"file", "agents_block", "exclude_block"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
LEGACY_MODES = {
    Path(".codex/hooks/stop_quality_gate.sh"): 0o755,
    Path(".agents/skills/create-skill-candidate-from-gap/scripts/validate_skill_candidate.py"): 0o644,
    Path(".agents/skills/open-subrepo-in-vscode/scripts/open_repositories_in_vscode.py"): 0o644,
}



class InstallError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Codex用Agent Guild Orchestraを非Gitの親ディレクトリへ導入します。"
    )
    parser.add_argument("--target", help="共通設定を配置する非Gitの親ディレクトリ")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="検証済みtemplate directory")
    parser.add_argument("--allow-non-default-source", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="変更予定をJSONで表示し、書き込みません")
    parser.add_argument("--major-upgrade", action="store_true", help="旧v2の認識済み配布物を退避して更新します（自動検出も行います）")
    parser.add_argument(
        "--config-mode", choices=sorted(CONFIG_MODES), default=None,
        help=".codex/config.tomlをmanagedまたはuser-ownedとして扱います（省略時は既存設定を継承）",
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


def git_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def canonical_git_root(target_arg: str) -> Path:
    target = Path(target_arg).expanduser()
    if target.is_symlink():
        raise InstallError("target itself may not be a symlink")
    target = target.resolve()
    if not target.is_dir():
        raise InstallError(f"target directory does not exist: {target}")
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
        env=git_environment(), text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise InstallError("target must be an existing Git working-tree root")
    root = Path(result.stdout.strip()).resolve()
    if root != target:
        raise InstallError(f"target must be the canonical Git root: {root}")
    return target


def canonical_guild_root(target_arg: str) -> Path:
    raw = Path(target_arg).expanduser()
    if raw.is_symlink():
        raise InstallError("configuration root itself may not be a symlink")
    target = raw.resolve()
    if not target.is_dir():
        raise InstallError(f"configuration root must be an existing directory: {target}")
    env = git_environment()
    result = subprocess.run(["git", "-C", str(target), "rev-parse", "--git-dir"],
                            env=env, text=True, capture_output=True)
    inside_git = any((ancestor / ".git").exists() or (ancestor / ".git").is_symlink() for ancestor in (target, *target.parents))
    if result.returncode == 0 or inside_git:
        raise InstallError("--target must be a non-Git parent outside all Git working trees; code repositories belong under repositories/")
    if (target / "repositories").is_symlink():
        raise InstallError("repositories/ may not be a symlink")
    return target


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


def extract_exclude_block(text: str) -> str | None:
    start = text.find(EXCLUDE_START)
    end = text.find(EXCLUDE_END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < start or text.find(EXCLUDE_START, start + 1) >= 0 or text.find(EXCLUDE_END, end + 1) >= 0:
        raise InstallError(".git/info/exclude has malformed or duplicate managed markers")
    return text[start : end + len(EXCLUDE_END)]


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
    if not isinstance(value, dict):
        raise InstallError("installed manifest is unsupported; review it before changing the installation")
    validate_manifest(value)
    return value


def validate_manifest(value: dict[str, object]) -> None:
    """Validate every manifest field that affects an installation decision."""
    if type(value.get("schema")) is not int or value.get("schema") not in {1, MANIFEST_SCHEMA}:
        raise InstallError("installed manifest has unsupported schema")

    if value["schema"] == MANIFEST_SCHEMA and value.get("layout") != LAYOUT:
        raise InstallError("installed manifest layout must be guild-parent")

    selected = value.get("selected_skills")
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise InstallError("installed manifest selected_skills must be a list of strings")
    if len(selected) != len(set(selected)):
        raise InstallError("installed manifest selected_skills must not contain duplicates")

    files = value.get("files")
    if not isinstance(files, dict):
        raise InstallError("installed manifest files must be an object")
    for rel_text, record in files.items():
        if not isinstance(rel_text, str):
            raise InstallError("installed manifest file keys must be strings")
        try:
            rel = safe_rel(rel_text)
        except InstallError as exc:
            raise InstallError(f"installed manifest has unsafe file key: {rel_text}") from exc
        if not is_managed_destination(rel) and not (value["schema"] == 1 and rel == EXCLUDE_REL):
            raise InstallError(f"installed manifest has unsupported file key: {rel}")
        if not isinstance(record, dict):
            raise InstallError(f"installed manifest record is invalid: {rel}")
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in MANAGED_KINDS or kind != managed_kind(rel):
            raise InstallError(f"installed manifest has unknown file kind for {rel}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise InstallError(f"installed manifest has invalid sha256 for {rel}")

    ownership = value.get("ownership")
    if ownership is not None:
        if not isinstance(ownership, dict) or set(ownership) != {CONFIG_REL.as_posix()}:
            raise InstallError("installed manifest ownership must name only .codex/config.toml")
        mode = ownership.get(CONFIG_REL.as_posix())
        if not isinstance(mode, str) or mode not in CONFIG_MODES:
            raise InstallError("installed manifest config ownership must be managed or user-owned")
        if mode == "user-owned" and CONFIG_REL.as_posix() in files:
            raise InstallError("user-owned config may not appear in the managed files map")


def manifest_config_mode(manifest: dict[str, object] | None) -> str:
    if manifest is None:
        return "managed"
    ownership = manifest.get("ownership")
    if ownership is None:
        # Manifests written before config ownership was introduced managed the
        # distributed config whenever it appeared in the files map.
        return "managed"
    assert isinstance(ownership, dict)
    mode = ownership[CONFIG_REL.as_posix()]
    assert isinstance(mode, str)
    return mode


def legacy_catalog() -> dict:
    return json.loads((ROOT / "scripts/legacy-v2-files.json").read_text(encoding="utf-8"))


def cleaned_legacy_hooks(path: Path) -> bytes | None:
    """Remove exact historical Guild commands, retaining third-party hook entries."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        before = json.dumps(value, sort_keys=True)
        known = set(legacy_catalog()["hook_commands"])
        for event, groups in list(value.get("hooks", {}).items()):
            if groups == []:
                continue
            kept = []
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    kept.append(group)
                    continue
                commands = [hook for hook in group["hooks"] if not (
                    isinstance(hook, dict) and isinstance(hook.get("command"), str)
                    and sha256_bytes(hook["command"].encode()) in known
                )]
                if commands == group["hooks"]:
                    kept.append(group)
                elif commands:
                    kept.append({**group, "hooks": commands})
            if kept:
                value["hooks"][event] = kept
            else:
                del value["hooks"][event]
        if json.dumps(value, sort_keys=True) == before:
            return None
        if value == {"hooks": {}}:
            return b""
        return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return None


def legacy_paths(target: Path) -> list[Path]:
    result = []
    for name, hashes in legacy_catalog()["files"].items():
        rel = Path(name)
        path = validate_destination(target, rel)
        if path.is_file() and not locally_modified_mode(path, rel, legacy=True) and current_hash(target, rel, managed_kind(rel)) in hashes:
            result.append(rel)
    hooks = validate_destination(target, Path(".codex/hooks.json"))
    if hooks.is_file() and not locally_modified_mode(hooks, Path(".codex/hooks.json")) and cleaned_legacy_hooks(hooks) is not None:
        result.append(Path(".codex/hooks.json"))
    return sorted(set(result))


def legacy_preserved(target: Path, removed: list[Path]) -> list[str]:
    names = list(legacy_catalog()["files"]) + [".orchestra/queue/state.sqlite", ".orchestra/queue/state.sqlite-wal", ".orchestra/queue/state.sqlite-shm", ".orchestra/dashboard.md"]
    return [name for name in names
            if Path(name) not in removed and (target / name).is_file()]


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


def locally_modified_mode(path: Path, rel: Path, *, legacy: bool = False) -> bool:
    # AGENTS is a shared file: only its managed block is owned, never its mode.
    expected = LEGACY_MODES.get(rel, desired_mode(rel)) if legacy else desired_mode(rel)
    return rel != Path("AGENTS.md") and path.is_file() and stat.S_IMODE(path.stat().st_mode) != expected


def managed_kind(rel: Path) -> str:
    if rel == Path("AGENTS.md"):
        return "agents_block"
    if rel == EXCLUDE_REL:
        return "exclude_block"
    return "file"


def is_managed_destination(rel: Path) -> bool:
    """Return whether a relative path belongs to the installer surface."""
    if rel in {Path("AGENTS.md"), CONFIG_REL, Path(".agents/orchestra/README.md")}:
        return True
    if len(rel.parts) == 3 and rel.parts[:2] == (".codex", "agents"):
        return rel.suffix == ".toml"
    if len(rel.parts) == 4 and rel.parts[:3] == (".agents", "orchestra", "scripts"):
        return rel.suffix == ".py"
    return len(rel.parts) >= 4 and rel.parts[:2] == (".agents", "skills")


def build_candidate(
    source: Path,
    selected: set[str],
    catalog: dict[str, tuple[str, Path]],
    *,
    config_mode: str = "managed",
) -> dict[Path, bytes]:
    candidate: dict[Path, bytes] = {}
    for rel, path in iter_files(source):
        rel = safe_rel(rel)
        if rel == Path("AGENTS.md"):
            continue
        if rel == CONFIG_REL and config_mode == "user-owned":
            continue
        if not is_managed_destination(rel):
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
    def __init__(self, target: Path, touched: Iterable[Path], *, recovery_root: Path | None = None):
        self.target = target
        self.retain = False
        self.backup_root = validate_destination(recovery_root or target, RECOVERY_ROOT_REL)
        self.backup_root_existed = self.backup_root.exists()
        self.backup_root.mkdir(mode=0o700, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="transaction-", dir=self.backup_root))
        self.existed: set[Path] = set()
        self.existing_dirs: set[Path] = {Path(".")}
        self.dir_modes: dict[Path, int] = {}
        touched = sorted(set(touched))
        try:
            for rel in touched:
                for parent in rel.parents:
                    if parent == Path("."):
                        break
                    if validate_destination(target, parent).is_dir():
                        self.existing_dirs.add(parent)
                        self.dir_modes[parent] = stat.S_IMODE((target / parent).stat().st_mode)
                src = validate_destination(target, rel)
                if src.exists():
                    if not src.is_file():
                        raise InstallError(f"transaction destination must be a regular file: {rel}")
                    self.existed.add(rel)
                    dst = self.temp / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            # Recovery is deliberately a cold backup, not an active runtime or
            # auto-replay journal. It survives Docker --rm on the host parent.
            (self.temp / "recovery.json").write_text(json.dumps({
                "target": str(target),
                "paths": [{"path": str(rel), "existed": rel in self.existed} for rel in touched],
                "directory_modes": {str(rel): mode for rel, mode in self.dir_modes.items()},
            }, indent=2) + "\n", encoding="utf-8")
        except BaseException:
            self.close()
            raise

    def assert_unchanged(self, rel: Path) -> None:
        path = validate_destination(self.target, rel)
        backup = self.temp / rel
        if rel in self.existed:
            if not path.is_file() or path.read_bytes() != backup.read_bytes() or path.stat().st_mode != backup.stat().st_mode:
                raise InstallError(f"destination changed during installation: {rel}")
        elif path.exists():
            raise InstallError(f"destination appeared during installation: {rel}")

    def restore(self, touched: Iterable[Path]) -> None:
        # Set this before any restore work, including a second interruption.
        self.retain = True
        errors = []
        touched = set(touched)
        for rel in sorted(touched, key=lambda p: (-len(p.parts), str(p))):
            try:
                dst = validate_destination(self.target, rel)
                if rel in self.existed:
                    src = self.temp / rel
                    data, mode = src.read_bytes(), stat.S_IMODE(src.stat().st_mode)
                    if dst.is_file() and dst.read_bytes() == data and stat.S_IMODE(dst.stat().st_mode) == mode:
                        continue  # A failed atomic write may have changed nothing.
                    write_atomic(dst, data, mode=mode)
                elif dst.exists():
                    if not dst.is_file():
                        raise InstallError(f"restore destination is no longer a file: {rel}")
                    dst.unlink()
            except Exception as exc:
                errors.append(f"{rel}: {exc}")
        parents = {parent for rel in set(touched) for parent in rel.parents if parent != Path(".")}
        for rel in sorted(parents, key=lambda p: len(p.parts), reverse=True):
            try:
                path = validate_destination(self.target, rel)
                if rel not in self.existing_dirs and path.is_dir():
                    path.rmdir()
            except OSError:
                pass  # Never remove an unexpected occupant to prune a directory.
            except InstallError as exc:
                errors.append(f"{rel}: {exc}")
        for rel, mode in self.dir_modes.items():
            try:
                path = validate_destination(self.target, rel)
                if path.is_dir() and stat.S_IMODE(path.stat().st_mode) != mode:
                    path.chmod(mode)
            except Exception as exc:
                errors.append(f"{rel}: {exc}")
        if errors:
            raise InstallError(f"rollback incomplete; recovery backup retained at {self.temp}: " + "; ".join(errors))
        self.retain = False

    def close(self) -> None:
        if self.retain:
            print(f"Recovery backup retained: {self.temp}", file=sys.stderr)
            return
        shutil.rmtree(self.temp, ignore_errors=True)
        if not self.backup_root_existed:
            try:
                self.backup_root.rmdir()
            except OSError:
                pass


def archive_legacy(target: Path, rels: list[Path], *, label: str = "v2-to-v3") -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = validate_destination(target, ARCHIVE_ROOT_REL)
    archive_root_existed = archive_root.exists()
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.mkdtemp(prefix=f"{label}-{stamp}-", dir=archive_root))
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
    except BaseException:
        shutil.rmtree(archive, ignore_errors=True)
        try:
            if not archive_root_existed:
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


def write_atomic(path: Path, data: bytes, *, mode: int | None = None) -> None:
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchmod(handle.fileno(), mode)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def deactivate_legacy(root: Path, rels: list[Path], transaction: Transaction, mutated: set[Path]) -> None:
    for rel in sorted(rels, key=lambda path: len(path.parts), reverse=True):
        destination = validate_destination(root, rel)
        if rel == Path("AGENTS.md"):
            continue  # Managed-block replacement below retains all user text.
        transaction.assert_unchanged(rel)
        mutated.add(rel)
        if rel == Path(".codex/hooks.json"):
            cleaned = cleaned_legacy_hooks(destination)
            if cleaned:
                write_atomic(destination, cleaned)
                continue
        remove_existing(destination)
        parent = destination.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def distribution_config_snippet(source: Path) -> str:
    config_path = source / CONFIG_REL
    if not config_path.is_file() or config_path.is_symlink():
        raise InstallError("template .codex/config.toml is required for the user-owned guidance")
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InstallError(f"cannot read template .codex/config.toml: {exc}") from exc
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # User-owned mode never consumes the source config as an install
        # artifact. Keep guidance useful even for a caller-provided source
        # whose config cannot be parsed as TOML.
        return text.strip()

    # Keep user-owned guidance focused on the settings that determine this
    # distribution's orchestration behavior, while taking their values from
    # the actual source config instead of duplicating defaults here.
    root_keys = ("model", "model_context_window")
    agents = parsed.get("agents")
    features = parsed.get("features")
    context_management = features.get("context_management") if isinstance(features, dict) else None
    if (
        all(key in parsed for key in root_keys)
        and isinstance(agents, dict)
        and all(key in agents for key in ("enabled", "max_concurrent_threads_per_session"))
        and isinstance(features, dict)
        and "multi_agent" in features
        and isinstance(context_management, dict)
        and "experimental_mode" in context_management
    ):
        required_values = [parsed[key] for key in root_keys] + [
            agents[key] for key in ("enabled", "max_concurrent_threads_per_session")
        ] + [features["multi_agent"], context_management["experimental_mode"]]
        if not (
            isinstance(required_values[0], str)
            and isinstance(required_values[1], int)
            and not isinstance(required_values[1], bool)
            and isinstance(required_values[2], bool)
            and isinstance(required_values[3], int)
            and not isinstance(required_values[3], bool)
            and isinstance(required_values[4], bool)
            and isinstance(required_values[5], bool)
        ):
            return text.strip()

        def toml_literal(value: object) -> str:
            if isinstance(value, str):
                return json.dumps(value, ensure_ascii=False)
            if value is True:
                return "true"
            if value is False:
                return "false"
            if isinstance(value, int) and not isinstance(value, bool):
                return str(value)
            raise InstallError("template .codex/config.toml required settings have unsupported values")

        return "\n".join(
            [
                *(f"{key} = {toml_literal(parsed[key])}" for key in root_keys),
                "",
                "[agents]",
                *(f"{key} = {toml_literal(agents[key])}" for key in ("enabled", "max_concurrent_threads_per_session")),
                "",
                "[features]",
                f"multi_agent = {toml_literal(features['multi_agent'])}",
                "",
                "[features.context_management]",
                f"experimental_mode = {toml_literal(context_management['experimental_mode'])}",
            ]
        )
    return text.strip()


def build_next_steps(target: Path, config_mode: str, *, config_snippet: str | None = None) -> list[str]:
    steps = [
        "Installation places shared files only in the non-Git parent; it does not activate Codex.",
        f"Open and trust the parent directory in Codex, then start a fresh local task there: {target}.",
        f"CLI startup: codex --cd {target}",
        "Keep the session cwd at the parent. Pass an explicit child Git root to coding tasks and all Git/snapshot helpers.",
        "Verify gpt-6-astra with user-selected effort, 1,000,000 context, experimental context management, core Skills, adventurer (gpt-5.6-luna/max) and inquisitor (gpt-6-astra/xhigh).",
        "Starting Codex directly inside a child Git repository is not the supported shared-config entry point: Git boundaries can stop parent discovery.",
        "Review child AGENTS.override.md/AGENTS.md and local settings before working there. A child config is not silently merged into a parent-started session; child-started sessions may load it instead.",
    ]
    if config_mode == "user-owned":
        steps.append("The existing parent .codex/config.toml was preserved byte-for-byte. Manually reconcile these distribution settings and any legacy role, hook or permission settings before activation:\n" + (config_snippet or ""))
    return steps


def child_overrides(target: Path) -> list[str]:
    """Report only direct child override paths; never read or rewrite their contents."""
    repositories = target / "repositories"
    if not repositories.is_dir() or repositories.is_symlink():
        return []
    result = []
    for child in sorted(repositories.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        for name in ("AGENTS.md", "AGENTS.override.md", ".codex/config.toml", ".codex/agents", ".agents/skills", str(MANIFEST_REL)):
            path = child / name
            if path.exists() or path.is_symlink():
                result.append(str(path.relative_to(target)))
    return result


def plan_install(
    args: argparse.Namespace,
) -> tuple[Path, Path | None, dict[str, object], dict[Path, bytes], list[dict[str, str]], list[Path], dict[str, object]]:
    target = canonical_guild_root(args.target)
    source = Path(args.source).expanduser().resolve()
    if source != DEFAULT_SOURCE.resolve() and not args.allow_non_default_source:
        raise InstallError("non-default source requires --allow-non-default-source")
    catalog = package_catalog(source)
    manifest = load_manifest(target)
    if manifest is not None and manifest["schema"] != MANIFEST_SCHEMA:
        raise InstallError("repository-local manifest cannot be updated as a parent; use cleanup-child for the old child installation")
    previous_selected = set(manifest.get("selected_skills", [])) if manifest else set()
    legacy_rels = legacy_paths(target) if manifest is None else []
    target_legacy = bool(legacy_rels)
    previous_config_mode = manifest_config_mode(manifest)
    if manifest is None and (target / CONFIG_REL).exists() and CONFIG_REL not in legacy_rels:
        previous_config_mode = "user-owned"
    config_mode = args.config_mode or previous_config_mode
    if config_mode not in CONFIG_MODES:
        raise InstallError("config mode must be managed or user-owned")
    selected = (previous_selected | set(args.with_skill)) - set(args.without_skill)
    candidate = build_candidate(source, selected, catalog, config_mode=config_mode)
    migration_root = target if target_legacy else None
    agents_path = target / "AGENTS.md"
    if agents_path.exists():
        if not agents_path.is_file() or agents_path.is_symlink():
            raise InstallError("AGENTS.md must be a regular file")
        extract_block(agents_path.read_text(encoding="utf-8"))
    previous_files = manifest.get("files", {}) if manifest else {}
    if not isinstance(previous_files, dict):
        raise InstallError("installed manifest files map is invalid")
    actions: list[dict[str, str]] = []
    if config_mode == "user-owned":
        legacy_rels = [rel for rel in legacy_rels if rel != CONFIG_REL]
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
            mode_changed = locally_modified_mode(destination, rel)
            if actual_hash == desired_hash:
                # A manual edit may have brought the destination exactly to
                # the new distribution. Adopt that converged state before
                # checking whether both sides diverged from the old baseline.
                action = "keep"
            elif (actual_hash != previous_hash or mode_changed) and desired_hash != previous_hash:
                raise InstallError(f"managed file changed locally and in distribution: {rel}")
            elif actual_hash != previous_hash or mode_changed:
                action = "preserve-local"
            else:
                action = "update"
        elif rel == Path("AGENTS.md") and destination.is_file() and extract_block(destination.read_text(encoding="utf-8")) is None:
            action = "create"
        elif destination.exists() and rel not in legacy_rels:
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
        if rel == CONFIG_REL and config_mode == "user-owned":
            # Switching ownership removes the config from the manifest while
            # deliberately leaving the user's bytes and mode untouched.
            continue
        if rel in candidate_paths or not isinstance(old, dict):
            continue
        actual_hash = current_hash(target, rel, str(old.get("kind")))
        path = validate_destination(target, rel)
        action = "remove" if actual_hash == old.get("sha256") and not locally_modified_mode(path, rel) else "preserve-local-removed"
        actions.append({"action": action, "path": rel.as_posix()})
    result_manifest: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "layout": LAYOUT,
        "distribution_version": VERSION,
        "installed_at": (
            manifest["installed_at"]
            if manifest is not None and isinstance(manifest.get("installed_at"), str) and manifest["installed_at"]
            else dt.datetime.now(dt.timezone.utc).isoformat()
        ),
        "selected_skills": sorted(selected),
        "ownership": {CONFIG_REL.as_posix(): config_mode},
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
    config_mode = manifest_config_mode(result_manifest)
    config_snippet = distribution_config_snippet(source) if config_mode == "user-owned" else None
    plan = {
        "target": str(target), "layout": LAYOUT, "version": VERSION, "dry_run": args.dry_run,
        "child_overrides": child_overrides(target),
        "preserved_legacy_files": legacy_preserved(target, legacy_rels) if migration_root else [],
        "major_upgrade": bool(legacy_rels), "archive_paths": [p.as_posix() for p in legacy_rels],
        "legacy_root": str(migration_root) if migration_root is not None else None,
        "config_mode": config_mode,
        "warnings": ["Child files, Git index/config and ignore rules are never modified by install or sync."] + (
            ["Parent AGENTS.override.md takes precedence over the installed AGENTS.md; reconcile it before activation."]
            if (target / "AGENTS.override.md").exists() or (target / "AGENTS.override.md").is_symlink() else []
        ),
        "next_steps": build_next_steps(
            target,
            config_mode,
            config_snippet=config_snippet,
        ),
        "legacy_actions": [
            {
                "action": (
                    "archive-and-strip-managed-block"
                    if rel == Path("AGENTS.md")
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
    touched = changed | set(legacy_rels)
    if not touched:
        plan["archive"] = None
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    transaction = Transaction(target, touched)
    archive: Path | None = None
    archive_root_existed = (target / ARCHIVE_ROOT_REL).exists()
    mutated: set[Path] = set()
    try:
        # Re-plan before the first write using the already prepared candidate bytes.
        repeated = plan_install(args)
        repeated[2]["installed_at"] = result_manifest["installed_at"]
        if repeated[2:6] != (result_manifest, candidate, actions, legacy_rels):
            raise InstallError("installation changed during preflight; rerun dry-run")
        if legacy_rels:
            archive = archive_legacy(target, legacy_rels)
            deactivate_legacy(target, legacy_rels, transaction, mutated)
        actions_by_path = {Path(item["path"]): item["action"] for item in actions}
        for rel, desired in sorted(candidate.items()):
            action = actions_by_path[rel]
            destination = validate_destination(target, rel)
            if action == "preserve-local":
                continue
            if action in {"keep", "adopt-identical"}:
                continue
            if rel not in mutated:
                transaction.assert_unchanged(rel)
                mutated.add(rel)
            if rel == Path("AGENTS.md"):
                existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
                managed = desired.decode("utf-8")
                write_atomic(destination, replace_block(existing, managed).encode())
                block = extract_block(destination.read_text(encoding="utf-8"))
                assert block is not None
            else:
                write_atomic(destination, desired, mode=desired_mode(rel))
        for item in actions:
            if item["action"] == "remove":
                rel = Path(item["path"])
                transaction.assert_unchanged(rel)
                mutated.add(rel)
                removed = validate_destination(target, rel)
                remove_existing(removed)
                parent = removed.parent
                while parent != target:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        if actions_by_path[MANIFEST_REL] in {"create", "update"}:
            transaction.assert_unchanged(MANIFEST_REL)
            mutated.add(MANIFEST_REL)
            write_atomic(
                validate_destination(target, MANIFEST_REL),
                (json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n").encode(),
            )
    except BaseException:
        transaction.restore(mutated)
        if archive is not None:
            shutil.rmtree(archive, ignore_errors=True)
            try:
                if not archive_root_existed:
                    archive.parent.rmdir()
            except OSError:
                pass
        raise
    finally:
        transaction.close()
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
