#!/usr/bin/env python3
"""一つのruntime Skill candidateをread-onlyかつfail closedで検証する。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
DENIED = {".env", "auth", "credential", "credentials", "id_rsa", "key", "password", "secret", "secrets", "token", "tokens"}
FILES = (Path("SKILL.md"), Path("agents/openai.yaml"))
SECTIONS = ("## 使う時", "## 入力", "## 手順", "## 出力", "## 安全", "## Promotion gate", "## 停止条件")
TOKENS = ("candidate-only", "external actions denied", "sensitive data denied", "local Git denied", "needs_human", "independent Trial", "置き換えない")
METADATA = (
    '  owner: "human-review-required"', '  scope: "skill-candidate"', '  lifecycle: "needs_human"',
    '  candidate_only_authority: "candidate-only"', '  external_actions: "denied"',
    '  sensitive_data: "denied"', '  local_git: "denied"',
)

class CandidateError(Exception): pass
def fail(message: str) -> None: raise CandidateError(message)
def absolute(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute(): fail("path は絶対 path を明示してください。")
    return Path(os.path.abspath(path))
def sensitive(path: Path) -> None:
    for part in path.parts:
        if set(filter(None, re.split(r"[^a-z0-9]+", part.casefold().strip(".")))) & DENIED:
            fail("secret-like path は内容を読まずに拒否します。")
def directory(path: Path, label: str) -> None:
    try: mode = path.lstat().st_mode
    except FileNotFoundError: fail(f"{label} がありません。")
    if stat.S_ISLNK(mode): fail(f"{label} の symlink は許可しません。")
    if not stat.S_ISDIR(mode): fail(f"{label} は directory にしてください。")
def regular(path: Path, label: str) -> None:
    try: mode = path.lstat().st_mode
    except FileNotFoundError: fail(f"{label} がありません。")
    if stat.S_ISLNK(mode): fail(f"{label} の symlink は許可しません。")
    if not stat.S_ISREG(mode): fail(f"{label} は通常 file にしてください。")
def quoted(line: str, label: str) -> str:
    match = re.fullmatch(r"\s*[A-Za-z_]+:\s*(\"(?:[^\"\\]|\\.)*\")\s*", line)
    if match is None: fail(f"{label} は二重引用符付きの文字列にしてください。")
    try: value = json.loads(match.group(1))
    except json.JSONDecodeError: fail(f"{label} の引用符が不正です。")
    if not value.strip(): fail(f"{label} は空でない文字列にしてください。")
    return value
def artifact_set(candidate: Path) -> None:
    found: list[Path] = []; pending = [candidate]
    while pending:
        current = pending.pop(); directory(current, "candidate directory")
        with os.scandir(current) as scan:
            for entry in sorted(scan, key=lambda item: item.name):
                path = Path(entry.path); rel = path.relative_to(candidate); sensitive(rel); mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode): fail("candidate 内の symlink は許可しません。")
                if stat.S_ISDIR(mode): pending.append(path)
                elif stat.S_ISREG(mode): found.append(rel)
                else: fail("candidate 内の special file は許可しません。")
    if sorted(found) != list(FILES): fail("candidate artifact は SKILL.md と agents/openai.yaml だけにしてください。")
def skill(candidate: Path, name: str) -> None:
    path = candidate / "SKILL.md"; regular(path, "SKILL.md"); text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"): fail("SKILL.md は YAML frontmatter で開始してください。")
    end = text.find("\n---\n", 4)
    if end < 0: fail("SKILL.md の frontmatter を閉じてください。")
    lines = text[4:end].splitlines()
    if len(lines) != 3 + len(METADATA) or lines[0] != f"name: {name}" or lines[2] != "metadata:" or tuple(lines[3:]) != METADATA:
        fail("SKILL.md の frontmatter は candidate-only metadata の固定 schema にしてください。")
    if not lines[1].startswith("description:") or len(quoted(lines[1], "SKILL.md.description")) > 1024: fail("SKILL.md.description は1〜1024文字にしてください。")
    for value in SECTIONS + TOKENS:
        if value not in text: fail(f"SKILL.md に必須 section/token がありません: {value}")
    if len(text.splitlines()) > 500: fail("SKILL.md は500行以下にしてください。")
def openai(candidate: Path, name: str) -> None:
    path = candidate / "agents/openai.yaml"; regular(path, "agents/openai.yaml"); lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 4 or lines[0] != "interface:": fail("agents/openai.yaml は interface の3文字列だけにしてください。")
    values = {}
    for key, line in zip(("display_name", "short_description", "default_prompt"), lines[1:]):
        if not line.startswith(f"  {key}:"): fail(f"agents/openai.yaml.interface.{key} が必要です。")
        values[key] = quoted(line.strip(), f"agents/openai.yaml.interface.{key}")
    if not 25 <= len(values["short_description"]) <= 64: fail("agents/openai.yaml.interface.short_description は25〜64文字にしてください。")
    if f"${name}" not in values["default_prompt"]: fail("agents/openai.yaml.interface.default_prompt は candidate name を明示してください。")
def digest(candidate: Path) -> str:
    result = hashlib.sha256()
    for rel in FILES:
        data = (candidate / rel).read_bytes(); result.update(rel.as_posix().encode()); result.update(b"\0"); result.update(len(data).to_bytes(8, "big")); result.update(data)
    return "sha256:" + result.hexdigest()
def require_git_root(target_repo_root: Path) -> None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(target_repo_root), "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        fail("target_repo_root のGit rootを検証できません。")
    if completed.returncode != 0 or not completed.stdout.strip():
        fail("target_repo_root は実Git rootにしてください。")
    try:
        reported = Path(completed.stdout.strip()).resolve(strict=True)
        canonical_target = target_repo_root.resolve(strict=True)
    except OSError:
        fail("target_repo_root のGit rootを安全に解決できません。")
    if reported != canonical_target:
        fail("target_repo_root は git rev-parse --show-toplevel と一致させてください。")
def validate(guild_root: Path, target_repo_root: Path, candidate: Path) -> str:
    repositories = guild_root / "repositories"; expected_target = repositories / target_repo_root.name
    if target_repo_root != expected_target: fail("target_repo_root は <guild_root>/repositories/<repo> の直接 child にしてください。")
    root = guild_root / ".orchestra" / "skill-candidates"; parent = root / target_repo_root.name
    if candidate.parent != parent: fail("candidate path は <guild_root>/.orchestra/skill-candidates/<target-repo>/<hyphen-name>/ に限定してください。")
    if len(candidate.name) > 64 or NAME_RE.fullmatch(candidate.name) is None: fail("candidate directory name は64文字以下の hyphen-case にしてください。")
    for path, label in ((guild_root,"guild_root"),(repositories,"repositories root"),(target_repo_root,"target_repo_root"),(guild_root/".agents","active skill root"),(guild_root/".agents/skills","active skills root"),(guild_root/".orchestra","runtime root"),(root,"candidate root"),(parent,"target candidate root"),(candidate,"candidate path")):
        sensitive(path); directory(path, label)
    regular(root / "README.md", "candidate root marker")
    active = guild_root / ".agents" / "skills" / candidate.name
    if active.exists() or active.is_symlink(): fail("同名の active Guild Skill が存在するため candidate を拒否します。")
    try:
        canonical = guild_root.resolve(strict=True)
        if target_repo_root.resolve(strict=True) != canonical / "repositories" / target_repo_root.name: fail("target_repo_root が canonical guild containment 外です。")
        if candidate.resolve(strict=True) != canonical / ".orchestra" / "skill-candidates" / target_repo_root.name / candidate.name: fail("candidate path が canonical runtime containment 外です。")
    except OSError: fail("candidate containment を安全に解決できません。")
    require_git_root(target_repo_root)
    artifact_set(candidate); skill(candidate, candidate.name); openai(candidate, candidate.name); return digest(candidate)
def main() -> int:
    parser = argparse.ArgumentParser(description="隔離された一つのruntime Skill candidateを変更せず検証します。")
    parser.add_argument("--guild-root", required=True); parser.add_argument("--target-repo-root", required=True); parser.add_argument("--candidate-path", required=True); args = parser.parse_args()
    try: result = validate(absolute(args.guild_root), absolute(args.target_repo_root), absolute(args.candidate_path))
    except CandidateError as exc: print(f"candidate-validator: error: {exc}", file=sys.stderr); return 1
    except (OSError, UnicodeError): print("candidate-validator: error: candidate を安全に読めません。", file=sys.stderr); return 1
    print(json.dumps({"candidate_content_digest": result, "status": "ok"}, ensure_ascii=False, sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
