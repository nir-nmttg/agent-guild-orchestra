#!/usr/bin/env python3
"""ギルド規約ルートへ最小ランタイムを導入するスクリプト。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any, Iterable

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

try:
    import yaml  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / 'template'
AGENTS_START = '<!-- agent-guild-orchestra:start -->'
AGENTS_END = '<!-- agent-guild-orchestra:end -->'
EXCLUDE_START = '# agent-guild-orchestra:start'
EXCLUDE_END = '# agent-guild-orchestra:end'
BACKUP_DIRECTORY = '.agent-guild-orchestra-backups'
BACKUP_REL_PATHS = [
    'AGENTS.md',
    '.git/info/exclude',
    '.agents',
    '.codex',
    '.orchestra',
]
SQLITE_STATE_REL_PATH = Path('.orchestra/queue/state.sqlite')
RUNTIME_SCHEMA_VERSION = '4.0'
RUNTIME_SCHEMA_SHA256 = 'a883418f960b28bc84381ed00fe4a9b55eb870e50a41fa26122ace122acb7c7c'
QUEST_RANKS = {'mapmaking', 'errand', 'solo_quest', 'party_quest', 'guild_quest'}
LEGACY_QUEST_RANKS = {'campaign'}
REQUIRED_RUNTIME_TABLES = {
    'queue_metadata',
    'events',
    'quests',
    'requests',
    'commands',
    'assignments',
    'reports',
    'trials',
    'inbox_messages',
}
REQUIRED_RUNTIME_COLUMNS = {
    'queue_metadata': {'key', 'value', 'updated_at'},
    'events': {
        'event_id',
        'timestamp',
        'actor',
        'event_type',
        'entity_type',
        'entity_id',
        'entity_json',
        'operation',
        'workflow_id',
        'structured_data_usage_json',
        'payload_json',
        'event_safety_json',
        'inserted_at',
    },
    'quests': {'quest_id', 'workflow_id', 'rank', 'status', 'payload_json', 'updated_at'},
    'requests': {'request_id', 'quest_id', 'workflow_id', 'status', 'payload_json', 'updated_at'},
    'commands': {'command_id', 'quest_id', 'workflow_id', 'status', 'payload_json', 'updated_at'},
    'assignments': {'assignment_id', 'parent_id', 'worker_id', 'kind', 'workflow_id', 'status', 'payload_json', 'updated_at'},
    'reports': {'report_id', 'worker_id', 'workflow_id', 'decision', 'status', 'payload_json', 'updated_at'},
    'trials': {'trial_id', 'quest_id', 'workflow_id', 'depth', 'status', 'payload_json', 'updated_at'},
    'inbox_messages': {'message_id', 'recipient', 'workflow_id', 'status', 'payload_json', 'created_at'},
}
LEGACY_RUNTIME_TABLES = {'tickets'}
LEGACY_RUNTIME_COLUMNS = {
    'assignments': {'task_id'},
}
RUNTIME_JSON_COLUMNS = {
    'events': ('entity_json', 'structured_data_usage_json', 'payload_json', 'event_safety_json'),
    'quests': ('payload_json',),
    'requests': ('payload_json',),
    'commands': ('payload_json',),
    'assignments': ('payload_json',),
    'reports': ('payload_json',),
    'trials': ('payload_json',),
    'inbox_messages': ('payload_json',),
}
LEGACY_RUNTIME_JSON_KEYS = {
    'safety_checks',
    'requires_human_confirmation',
    'target_path',
    'scale_selected',
    'risk_dimensions',
    'edit_scope',
    'read_scope',
    'quality_profile',
    'review_task',
    'review_assignment',
    'task_id',
    'scout_plan',
    'scout_usage',
    'scout_calls',
    'scout_policy',
    'spark_request',
    'meta' 'cognitive_state',
    'meta' 'cognitive_control',
    'meta' 'cognitive_controller',
    'invoke_' 'meta' 'cognitive_controller',
    'meta' 'cognitive_task_loop',
}
RETIRED_AGENT_VALUES = {
    'advisor',
    'focus_reviewer',
    'integration_owner',
    'party_leader',
    'quest_sentinel',
    'spark',
    'scout',
    'meta' 'cognitive_controller',
}
LEGACY_RUNTIME_STRING_VALUES = {
    'advisor',
    'focus_reviewer',
    'integration_owner',
    'party_leader',
    'quest_sentinel',
    'advisory_consultation',
    'bounded_trial_focus_reviewer',
    'cross_scope_integration_owner',
    'independent_focus_advisor',
    'spark',
    'scout',
    'meta' 'cognitive',
    'meta' 'cognitive_controller',
    'meta' 'cognitive-task-loop',
    'meta' 'cognitive_state',
    'meta' 'cognitive_control',
    'invoke_' 'meta' 'cognitive_controller',
}
EXPECTED_AGENT_SANDBOX_MODES = {
    'adventurer': 'workspace-write',
    'sage': 'read-only',
    'cartographer': 'read-only',
    'courier': 'workspace-write',
    'examiner': 'read-only',
    'guildmaster': 'read-only',
    'inquisitor': 'read-only',
    'artificer': 'workspace-write',
    'warden': 'read-only',
    'captain': 'read-only',
}
EXPECTED_AGENT_MODEL_CONFIGS = {
    'adventurer': ('gpt-5.6-luna', 'max'),
    'sage': ('gpt-5.6-luna', 'max'),
    'cartographer': ('gpt-5.6-sol', 'high'),
    'courier': ('gpt-5.6-luna', 'high'),
    'examiner': ('gpt-5.6-luna', 'max'),
    'guildmaster': ('gpt-5.6-sol', 'xhigh'),
    'inquisitor': ('gpt-5.6-sol', 'xhigh'),
    'artificer': ('gpt-5.6-sol', 'high'),
    'warden': ('gpt-5.6-sol', 'high'),
    'captain': ('gpt-5.6-sol', 'high'),
}
EXPECTED_ORCHESTRA_SKILL_DIRS = {
    'branch-implementation-final-review',
    'browser-research-readonly',
    'communicate-work-estimates',
    'create-skill-candidate-from-gap',
    'explain-clearly',
    'git-branch-from-session',
    'git-rename-unpushed-branch-from-diff',
    'git-split-commits-from-diff',
    'github-pull-request-from-branch',
    'github-safe-push-from-branch',
    'implementation-behavior-verification',
    'open-subrepo-in-vscode',
    'orchestra-instruction-contract-review',
    'orchestra-runtime-security-audit',
    'orchestra-validation-review',
    'pull-request-description-from-branch',
    'quest-awareness-loop',
    'refine-design-plan',
    'repository-design-mapmaking',
    'use-guild-workflow',
}
SOURCE_REQUIRED_REL_PATHS = (
    Path('AGENTS.md'),
    Path('.codex/config.toml'),
    Path('.codex/hooks.json'),
    Path('.codex/hooks/stop_quality_gate.py'),
    Path('.codex/hooks/stop_quality_gate.sh'),
    Path('.agents/orchestra/README.md'),
    Path('.agents/orchestra/config/settings.yaml'),
    Path('.agents/orchestra/dashboard.md'),
    Path('.agents/orchestra/docker/.dockerignore'),
    Path('.agents/orchestra/docker/Dockerfile'),
    Path('.agents/orchestra/docker/requirements.txt'),
    Path('.agents/orchestra/docs/agent-memory.md'),
    Path('.agents/orchestra/instructions/adventurer.md'),
    Path('.agents/orchestra/instructions/sage.md'),
    Path('.agents/orchestra/instructions/cartographer.md'),
    Path('.agents/orchestra/instructions/common.md'),
    Path('.agents/orchestra/instructions/examiner.md'),
    Path('.agents/orchestra/instructions/guildmaster.md'),
    Path('.agents/orchestra/instructions/inquisitor.md'),
    Path('.agents/orchestra/instructions/artificer.md'),
    Path('.agents/orchestra/instructions/captain.md'),
    Path('.agents/orchestra/instructions/warden.md'),
    Path('.agents/orchestra/instructions/session_recovery.md'),
    Path('.agents/orchestra/logs/daily/README.md'),
    Path('.agents/orchestra/queue/README.md'),
    Path('.agents/orchestra/queue/templates/adventurer_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/adventurer_inbox.yaml'),
    Path('.agents/orchestra/queue/templates/adventurer_report.yaml'),
    Path('.agents/orchestra/queue/templates/sage_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/sage_report.yaml'),
    Path('.agents/orchestra/queue/templates/cartographer_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/cartographer_report.yaml'),
    Path('.agents/orchestra/queue/templates/command.yaml'),
    Path('.agents/orchestra/queue/templates/examiner_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/examiner_report.yaml'),
    Path('.agents/orchestra/queue/templates/inquisitor_report.yaml'),
    Path('.agents/orchestra/queue/templates/inquisitor_trial.yaml'),
    Path('.agents/orchestra/queue/templates/warden_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/request.yaml'),
    Path('.agents/orchestra/queue/templates/role_inbox.yaml'),
    Path('.agents/orchestra/scripts/claude_compat.py'),
    Path('.agents/orchestra/scripts/docker_python.sh'),
    Path('.agents/orchestra/scripts/inbox_write.sh'),
    Path('.agents/orchestra/scripts/queue_db.py'),
    Path('.agents/orchestra/scripts/queue_audit.py'),
    Path('.agents/orchestra/scripts/queue_schema.sql'),
    Path('.agents/orchestra/scripts/snapshot_digest.py'),
)
SOURCE_REQUIRED_REL_PATHS += tuple(Path('.codex/agents') / f'{role}.toml' for role in sorted(EXPECTED_AGENT_SANDBOX_MODES))
SOURCE_REQUIRED_REL_PATHS += tuple(Path('.agents/skills') / skill / 'SKILL.md' for skill in sorted(EXPECTED_ORCHESTRA_SKILL_DIRS))
SOURCE_REQUIRED_REL_PATHS += tuple(Path('.agents/skills') / skill / 'agents/openai.yaml' for skill in sorted(EXPECTED_ORCHESTRA_SKILL_DIRS))
SOURCE_REQUIRED_REL_PATHS += (
    Path('.agents/orchestra/skill-candidates/README.md'),
    Path('.agents/skills/create-skill-candidate-from-gap/scripts/validate_skill_candidate.py'),
    Path('.agents/skills/open-subrepo-in-vscode/scripts/open_repositories_in_vscode.py'),
)
REPOSITORIES_REL_PATH = Path('repositories')
ORCHESTRA_SKILL_OWNER = 'agent-guild-orchestra'
TRUSTED_SOURCE_TOP_LEVELS = {'AGENTS.md', '.agents', '.codex'}
UNTRUSTED_SOURCE_PATH_TOKENS = {
    '.aws',
    '.env',
    '.git',
    '.kube',
    '.mcp',
    '.netrc',
    '.npmrc',
    '.orchestra',
    '.pypirc',
    '.ssh',
    'backup',
    'backups',
    'auth',
    'credential',
    'credentials',
    'id_dsa',
    'id_ecdsa',
    'id_ecdsa_sk',
    'id_ed25519',
    'id_ed25519_sk',
    'id_rsa',
    'key',
    'mcp',
    'oauth',
    'password',
    'pem',
    'repositories',
    'secret',
    'secrets',
    'state.sqlite',
    'token',
    'tokens',
}
TRUSTED_SOURCE_RISKY_PATH_EXCEPTIONS = {
    Path('.agents/skills/open-subrepo-in-vscode/scripts/open_repositories_in_vscode.py'),
}
PATH_TERM_RE = re.compile(r'[^a-z0-9]+')
REMOVED_TEMPLATE_REL_PATHS = [
    Path('.codex/agents/spark.toml'),
    Path('.codex/agents/' 'meta' 'cognitive_controller.toml'),
    Path('.codex/agents/advisor.toml'),
    Path('.codex/agents/focus_reviewer.toml'),
    Path('.codex/agents/integration_owner.toml'),
    Path('.codex/agents/party_leader.toml'),
    Path('.codex/agents/quest_sentinel.toml'),
    Path('.agents/orchestra/instructions/advisor.md'),
    Path('.agents/orchestra/instructions/focus_reviewer.md'),
    Path('.agents/orchestra/instructions/integration_owner.md'),
    Path('.agents/orchestra/instructions/party_leader.md'),
    Path('.agents/orchestra/instructions/quest_sentinel.md'),
    Path('.agents/orchestra/queue/templates/advisor_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/advisor_report.yaml'),
    Path('.agents/orchestra/queue/templates/focus_reviewer_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/focus_reviewer_report.yaml'),
    Path('.agents/orchestra/queue/templates/quest_sentinel_assignment.yaml'),
    Path('.agents/orchestra/queue/templates/adventurer_task.yaml'),
    Path('.agents/orchestra/queue/templates/inquisitor_task.yaml'),
    Path('.agents/skills/' 'meta' 'cognitive-task-loop'),
    Path('.agents/skills/explain-for-newcomers'),
    Path('.agents/orchestra/instructions/receptionist.md'),
]
SAGE_DEVELOPER_INSTRUCTION_TOKENS = (
    '一つの focus',
    'evidence refs',
    '実装',
    '品質採否',
    'Ledger',
    '追加 agent',
)
WARDEN_DEVELOPER_INSTRUCTION_TOKENS = (
    '矛盾した根拠',
    '反復失敗',
    'scope drift',
    'blocking unknowns',
    'security review',
    'user approval',
    '実装',
    'Ledger',
    '追加 agent',
)


class JapaneseArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return (
            super()
            .format_help()
            .replace('usage:', '使い方:')
            .replace('optional arguments:', 'オプション:')
            .replace('options:', 'オプション:')
        )

    def format_usage(self) -> str:
        return super().format_usage().replace('usage:', '使い方:')


def parse_args() -> argparse.Namespace:
    parser = JapaneseArgumentParser(
        description='ギルド規約ルートへ最小ランタイムを導入し、直下に repositories/ を用意します。',
        add_help=False,
    )
    parser.add_argument('-h', '--help', action='help', help='このヘルプを表示して終了します。')
    parser.add_argument('--target', required=True, help='導入先のギルド規約ルート。子リポジトリではなく、repositories/ の親を指定します。')
    parser.add_argument('--source', default=str(DEFAULT_SOURCE), help='コピー元の template ディレクトリ。')
    parser.add_argument('--mode', default='copy', choices=['copy'], help='導入モード。現在は copy のみ。')
    parser.add_argument('--dry-run', action='store_true', help='変更を加えず、予定だけ表示します。')
    parser.add_argument('--allow-non-default-source', action='store_true', help='既定以外の source template を明示的に許可します。信頼済み検証用途だけで使います。')
    parser.add_argument('--clean-install', action='store_true', help='ギルド規約ルートの導入済みランタイムを片付けてから再導入します。repositories/ 配下の repo は移動も削除もしません。')
    parser.add_argument('--backup', action='store_true', help='導入前に既存導入物を退避します。')
    parser.add_argument('--allow-clean-install-without-backup', action='store_true', help='非推奨の逃げ道です。clean install をバックアップなしで実行する時だけ指定します。')
    parser.add_argument('--allow-reset-runtime-without-backup', action='store_true', help='非推奨の逃げ道です。動的状態をバックアップなしで初期化する時だけ指定します。')
    parser.add_argument('--reset-runtime', action='store_true', help='Ledger と dashboard の状態も初期値へ戻します。')
    parser.add_argument('--no-git-exclude', action='store_true', help='`.git/info/exclude` を更新しません。')
    return parser.parse_args()


def log(message: str) -> None:
    print(message)


def ensure_directory(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    path.mkdir(parents=True, exist_ok=True)


def map_template_path(rel_path: Path) -> Path:
    posix = rel_path.as_posix()
    if posix == '.agents/orchestra/dashboard.md':
        return Path('.orchestra/dashboard.md')
    if posix == '.agents/orchestra/skill-candidates/README.md':
        return Path('.orchestra/skill-candidates/README.md')
    return rel_path


def is_runtime_state_file(rel_path: Path) -> bool:
    posix = map_template_path(rel_path).as_posix()
    return posix == '.orchestra/dashboard.md'


def validate_target_managed_path(path: Path, target_root: Path) -> None:
    try:
        relative = path.relative_to(target_root)
    except ValueError as exc:
        raise SystemExit(f'導入先の管理対象 path が target root 外です: {path}') from exc
    cursor = target_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SystemExit(f'導入先の管理対象 path に symlink は使えません: {cursor.relative_to(target_root)}')
    try:
        path.resolve(strict=False).relative_to(target_root)
    except ValueError as exc:
        raise SystemExit(f'導入先の管理対象 path が target root 外へ解決されます: {relative}') from exc


def validate_target_write_path(path: Path, target_root: Path) -> None:
    validate_target_managed_path(path, target_root)


def copy_file(src: Path, dst: Path, target_root: Path, dry_run: bool) -> None:
    validate_target_write_path(dst, target_root)
    log(f'copy {src} -> {dst}')
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def write_text(path: Path, content: str, target_root: Path, dry_run: bool) -> None:
    validate_target_write_path(path, target_root)
    log(f'write {path}')
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def remove_path(path: Path, target_root: Path, dry_run: bool) -> None:
    validate_target_managed_path(path, target_root)
    if not path.exists() and not path.is_symlink():
        return
    log(f'remove {path}')
    if dry_run:
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def read_skill_owner(skill_path: Path, unreadable_warning_path: Path | None = None) -> str | None:
    if not skill_path.exists() or not skill_path.is_file():
        return None
    try:
        text = skill_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        if unreadable_warning_path is not None:
            log(f'注意: Skill の所有者情報を UTF-8 として読めないため保持します: {unreadable_warning_path}')
        return None
    if not text.startswith('---\n'):
        return None
    end_marker = text.find('\n---\n', 4)
    if end_marker == -1:
        return None
    frontmatter = text[4:end_marker]
    if yaml is not None:
        try:
            document = yaml.safe_load(frontmatter)
        except yaml.YAMLError:
            document = None
        if isinstance(document, dict):
            skill_metadata = document.get('metadata')
            if isinstance(skill_metadata, dict):
                nested_owner = skill_metadata.get('owner')
                if isinstance(nested_owner, str):
                    return nested_owner
            legacy_owner = document.get('owner')
            if isinstance(legacy_owner, str):
                return legacy_owner
            return None
    in_metadata = False
    nested_owner = None
    legacy_owner = None
    for line in frontmatter.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        key, separator, value = line.partition(':')
        if not separator:
            continue
        normalized_key = key.strip()
        if indent == 0:
            in_metadata = normalized_key == 'metadata' and not value.strip()
            if normalized_key == 'owner':
                legacy_owner = value.strip().strip('"\'')
            continue
        if in_metadata and normalized_key == 'owner':
            nested_owner = value.strip().strip('"\'')
    return nested_owner if nested_owner is not None else legacy_owner


def clean_owner_scoped_skills(target_root: Path, dry_run: bool) -> None:
    skills_root = target_root / '.agents' / 'skills'
    validate_target_managed_path(skills_root, target_root)
    if not skills_root.exists() or not skills_root.is_dir():
        return
    for child in sorted(skills_root.iterdir()):
        validate_target_managed_path(child, target_root)
        skill_path = child / 'SKILL.md' if child.is_dir() else child
        validate_target_managed_path(skill_path, target_root)
        owner = read_skill_owner(skill_path, child.relative_to(target_root))
        if owner == ORCHESTRA_SKILL_OWNER:
            remove_path(child, target_root, dry_run)


def replace_or_append_block(text: str, block: str, start_marker: str, end_marker: str) -> str:
    kept_lines, insertion_index = split_managed_block_text(text, start_marker, end_marker)
    block_text = block.rstrip('\n')
    if insertion_index is None:
        stripped = '\n'.join(kept_lines).rstrip()
        if stripped:
            return stripped + '\n\n' + block_text + '\n'
        return block_text + '\n'
    before = '\n'.join(kept_lines[:insertion_index]).rstrip('\n')
    after = '\n'.join(kept_lines[insertion_index:]).lstrip('\n')
    parts = []
    if before:
        parts.append(before)
    parts.append(block_text)
    if after:
        parts.append(after)
    return '\n\n'.join(parts).rstrip() + '\n'


def strip_managed_blocks(text: str, start_marker: str, end_marker: str) -> str:
    kept_lines, _insertion_index = split_managed_block_text(text, start_marker, end_marker)
    return '\n'.join(kept_lines)


def split_managed_block_text(text: str, start_marker: str, end_marker: str) -> tuple[list[str], int | None]:
    marker_lines = {start_marker, end_marker}
    lines = text.splitlines()
    kept: list[str] = []
    insertion_index: int | None = None
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == start_marker:
            next_marker = index + 1
            while next_marker < len(lines) and lines[next_marker].strip() not in marker_lines:
                next_marker += 1
            if next_marker < len(lines) and lines[next_marker].strip() == end_marker:
                if insertion_index is None:
                    insertion_index = len(kept)
                index = next_marker + 1
            else:
                index += 1
            continue
        if stripped == end_marker:
            index += 1
            continue
        kept.append(lines[index])
        index += 1
    return kept, insertion_index


def remove_block(text: str, start_marker: str, end_marker: str) -> str:
    stripped = strip_managed_blocks(text, start_marker, end_marker).rstrip()
    return stripped + '\n' if stripped else ''


def upsert_text_block(
    path: Path,
    block: str,
    start_marker: str,
    end_marker: str,
    target_root: Path,
    dry_run: bool,
) -> None:
    validate_target_write_path(path, target_root)
    current = path.read_text(encoding='utf-8') if path.exists() else ''
    updated = replace_or_append_block(current, block, start_marker, end_marker)
    if updated != current:
        write_text(path, updated, target_root, dry_run)


def prune_text_block(
    path: Path,
    start_marker: str,
    end_marker: str,
    target_root: Path,
    dry_run: bool,
) -> None:
    validate_target_write_path(path, target_root)
    if not path.exists():
        return
    current = path.read_text(encoding='utf-8')
    updated = remove_block(current, start_marker, end_marker)
    if updated != current:
        write_text(path, updated, target_root, dry_run)


def iter_backup_candidates(target_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for rel in BACKUP_REL_PATHS:
        path = target_root / Path(rel)
        validate_target_managed_path(path, target_root)
        if path.exists() or path.is_symlink():
            candidates.append(path)
    return candidates


def backup_existing(target_root: Path, dry_run: bool) -> None:
    candidates = iter_backup_candidates(target_root)
    if not candidates:
        return

    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup_root = target_root / BACKUP_DIRECTORY / stamp
    validate_target_write_path(backup_root, target_root)
    for path in candidates:
        validate_backup_candidate(path, target_root)
        destination = backup_root / path.relative_to(target_root)
        validate_target_write_path(destination, target_root)
        log(f'backup {path} -> {destination}')
        if dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, destination)
        else:
            shutil.copy2(path, destination)


def validate_backup_candidate(path: Path, target_root: Path) -> None:
    if path.is_symlink():
        raise SystemExit(f'backup 対象に symlink は使えません: {path.relative_to(target_root)}')
    if path.is_dir():
        for child in path.rglob('*'):
            if child.is_symlink():
                raise SystemExit(f'backup 対象に symlink は使えません: {child.relative_to(target_root)}')


def clean_install_target(target_root: Path, prune_git_exclude: bool, dry_run: bool) -> None:
    remove_path(target_root / '.agents' / 'orchestra', target_root, dry_run)
    clean_owner_scoped_skills(target_root, dry_run)
    remove_path(target_root / '.codex', target_root, dry_run)
    clean_runtime_siblings(target_root, dry_run)
    prune_text_block(target_root / 'AGENTS.md', AGENTS_START, AGENTS_END, target_root, dry_run)
    if prune_git_exclude:
        prune_text_block(target_root / '.git' / 'info' / 'exclude', EXCLUDE_START, EXCLUDE_END, target_root, dry_run)


def clean_runtime_siblings(target_root: Path, dry_run: bool) -> None:
    runtime_root = target_root / '.orchestra'
    candidate_root = runtime_root / 'skill-candidates'
    validate_target_managed_path(runtime_root, target_root)
    validate_target_managed_path(candidate_root, target_root)
    if not runtime_root.exists():
        return
    if not runtime_root.is_dir():
        raise SystemExit('導入先の `.orchestra` はdirectoryにしてください。')
    for child in sorted(runtime_root.iterdir()):
        validate_target_managed_path(child, target_root)
        if child.name != 'skill-candidates':
            remove_path(child, target_root, dry_run)


def reset_runtime_state(target_root: Path, dry_run: bool) -> None:
    remove_path(target_root / '.orchestra' / 'queue', target_root, dry_run)
    remove_path(target_root / '.orchestra' / 'dashboard.md', target_root, dry_run)


def runtime_schema_incompatibility_message(database: Path, detail: str) -> str:
    return (
        f'既存 runtime DB を v{RUNTIME_SCHEMA_VERSION} の runtime contract と確認できません: {database} ({detail})\n'
        '4.0より前、v4 metadata だけで物理 schema が一致しない、旧agent ID、または旧 runtime 値を含む `state.sqlite` は保持更新できません。'
        '既存状態を保全して初期化する場合は `--backup --reset-runtime`、'
        '導入物全体を入れ直す場合は `--backup --clean-install` を使ってください。'
    )


def read_runtime_schema_version(database: Path) -> str | None:
    try:
        with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM queue_metadata WHERE key = 'schema_version'"
            ).fetchone()
    except (OSError, sqlite3.DatabaseError) as exc:
        raise SystemExit(runtime_schema_incompatibility_message(database, str(exc))) from exc
    if row is None or row[0] is None:
        return None
    return str(row[0])


def runtime_schema_signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'view', 'trigger')
        ORDER BY type, name, tbl_name
        """
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), ' '.join(str(row[3] or '').split()))
        for row in rows
        if not str(row[1]).startswith('sqlite_')
    )


def canonical_runtime_schema_signature(schema_file: Path | None = None) -> tuple[tuple[str, str, str, str], ...]:
    path = schema_file or (DEFAULT_SOURCE / '.agents' / 'orchestra' / 'scripts' / 'queue_schema.sql')
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != RUNTIME_SCHEMA_SHA256:
        raise SystemExit(f'queue_schema.sql SHA-256 がv{RUNTIME_SCHEMA_VERSION} contractと一致しません: {digest}')
    try:
        with sqlite3.connect(':memory:') as expected:
            expected.executescript(content.decode('utf-8'))
            return runtime_schema_signature(expected)
    except (UnicodeDecodeError, sqlite3.DatabaseError) as exc:
        raise SystemExit(f'canonical queue_schema.sqlを検証できません: {exc}') from exc


def collect_runtime_signature_errors(connection: sqlite3.Connection, schema_file: Path | None = None) -> list[str]:
    expected = canonical_runtime_schema_signature(schema_file)
    actual = runtime_schema_signature(connection)
    if actual == expected:
        return []
    expected_by_name = {(item[0], item[1]): item for item in expected}
    actual_by_name = {(item[0], item[1]): item for item in actual}
    missing = sorted(f'{kind}:{name}' for kind, name in expected_by_name.keys() - actual_by_name.keys())
    unexpected = sorted(f'{kind}:{name}' for kind, name in actual_by_name.keys() - expected_by_name.keys())
    changed = sorted(
        f'{kind}:{name}'
        for kind, name in expected_by_name.keys() & actual_by_name.keys()
        if expected_by_name[(kind, name)] != actual_by_name[(kind, name)]
    )
    details = []
    if missing:
        details.append('不足=' + ','.join(missing))
    if unexpected:
        details.append('未知=' + ','.join(unexpected))
    if changed:
        details.append('定義差分=' + ','.join(changed))
    return ['canonical table/index/constraint signature不一致: ' + '; '.join(details)]


def collect_runtime_schema_errors(connection: sqlite3.Connection, schema_file: Path | None = None) -> list[str]:
    errors: list[str] = []
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    legacy_tables = sorted(LEGACY_RUNTIME_TABLES & tables)
    if legacy_tables:
        errors.append('旧 table が残っています: ' + ', '.join(legacy_tables))
    managed_tables = {table for table in tables if not table.startswith('sqlite_')}
    unexpected_tables = sorted(managed_tables - REQUIRED_RUNTIME_TABLES - LEGACY_RUNTIME_TABLES)
    if unexpected_tables:
        errors.append('未知 table が残っています: ' + ', '.join(unexpected_tables))
    missing_tables = sorted(REQUIRED_RUNTIME_TABLES - tables)
    if missing_tables:
        errors.append('不足 table: ' + ', '.join(missing_tables))
    for table, required_columns in REQUIRED_RUNTIME_COLUMNS.items():
        if table not in tables:
            continue
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            errors.append(f'{table} の不足 column: ' + ', '.join(missing_columns))
        legacy_columns = sorted(LEGACY_RUNTIME_COLUMNS.get(table, set()) & columns)
        if legacy_columns:
            errors.append(f'{table} の旧 column: ' + ', '.join(legacy_columns))
        unexpected_columns = sorted(columns - required_columns - LEGACY_RUNTIME_COLUMNS.get(table, set()))
        if unexpected_columns:
            errors.append(f'{table} の未知 column: ' + ', '.join(unexpected_columns))
    errors.extend(collect_runtime_signature_errors(connection, schema_file))
    return errors


def iter_runtime_json_keys(value: Any, path: str = '$') -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f'{path}.{key}'
            findings.append((child_path, key))
            findings.extend(iter_runtime_json_keys(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(iter_runtime_json_keys(item, f'{path}[{index}]'))
    return findings


def iter_runtime_json_values(value: Any, path: str = '$') -> list[tuple[str, Any]]:
    findings: list[tuple[str, Any]] = [(path, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            findings.extend(iter_runtime_json_values(item, f'{path}.{key}'))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(iter_runtime_json_values(item, f'{path}[{index}]'))
    return findings


def collect_runtime_json_value_errors(parsed: Any, label: str) -> list[str]:
    errors: list[str] = []
    for json_path, key in iter_runtime_json_keys(parsed):
        if key in LEGACY_RUNTIME_JSON_KEYS or key in LEGACY_RUNTIME_STRING_VALUES:
            errors.append(f'{label}{json_path[1:]}: 廃止済み key `{key}` が残っています')
    for json_path, item in iter_runtime_json_values(parsed):
        if isinstance(item, str) and item in LEGACY_RUNTIME_STRING_VALUES:
            errors.append(f'{label}{json_path[1:]}: 廃止済み runtime 値 `{item}` が残っています')
    return errors


def collect_runtime_value_errors(connection: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    rows = connection.execute("SELECT DISTINCT rank FROM quests WHERE rank IS NOT NULL").fetchall()
    for row in rows:
        rank = row[0]
        if rank in QUEST_RANKS:
            continue
        if rank in LEGACY_QUEST_RANKS:
            errors.append(f'旧 Quest Rank が残っています: {rank} -> guild_quest')
        else:
            errors.append(f'未定義 Quest Rank が残っています: {rank}')
    for table, columns in RUNTIME_JSON_COLUMNS.items():
        rows = connection.execute(f'SELECT rowid, {", ".join(columns)} FROM {table}').fetchall()
        for row in rows:
            rowid = row[0]
            for index, column in enumerate(columns, start=1):
                raw = row[index]
                try:
                    parsed = json.loads(raw)
                except (TypeError, json.JSONDecodeError) as exc:
                    errors.append(f'{table}[rowid={rowid}].{column}: JSON を読めません: {exc}')
                    continue
                errors.extend(collect_runtime_json_value_errors(parsed, f'{table}[rowid={rowid}].{column}'))
    retired_agent_column_checks = (
        ('events', 'event_id', 'actor'),
        ('assignments', 'assignment_id', 'worker_id'),
        ('reports', 'report_id', 'worker_id'),
        ('inbox_messages', 'message_id', 'recipient'),
    )
    for table, id_column, value_column in retired_agent_column_checks:
        rows = connection.execute(f'SELECT {id_column}, {value_column} FROM {table}').fetchall()
        for row in rows:
            value = row[1]
            if isinstance(value, str) and value in RETIRED_AGENT_VALUES:
                errors.append(f'{table}[{row[0]}].{value_column}: 廃止済み agent 値 `{value}` が残っています')
    return errors


def validate_runtime_physical_schema(database: Path, schema_file: Path) -> None:
    try:
        with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
            errors = collect_runtime_schema_errors(connection, schema_file)
            if not errors:
                errors.extend(collect_runtime_value_errors(connection))
    except (OSError, sqlite3.DatabaseError) as exc:
        raise SystemExit(runtime_schema_incompatibility_message(database, str(exc))) from exc
    if errors:
        raise SystemExit(runtime_schema_incompatibility_message(database, '; '.join(errors)))


def validate_existing_runtime_schema(source_root: Path, target_root: Path, reset_runtime: bool, clean_install: bool) -> None:
    database = target_root / SQLITE_STATE_REL_PATH
    validate_target_managed_path(database, target_root)
    if reset_runtime or clean_install:
        return
    if not database.exists():
        return
    schema_version = read_runtime_schema_version(database)
    if schema_version != RUNTIME_SCHEMA_VERSION:
        detail = 'schema_version is missing' if schema_version is None else f'schema_version={schema_version!r}'
        raise SystemExit(runtime_schema_incompatibility_message(database, detail))
    validate_runtime_physical_schema(database, source_root / '.agents' / 'orchestra' / 'scripts' / 'queue_schema.sql')


def initialize_sqlite_runtime(source_root: Path, target_root: Path, reset_runtime: bool, dry_run: bool) -> None:
    destination = target_root / SQLITE_STATE_REL_PATH
    validate_target_write_path(destination, target_root)
    if destination.exists() and not reset_runtime:
        log(f'既存状態を保持 {destination}')
        return

    log(f'init sqlite {destination}')
    if dry_run:
        return

    schema_path = source_root / '.agents' / 'orchestra' / 'scripts' / 'queue_schema.sql'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destination) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA journal_mode = WAL')
        connection.executescript(schema_path.read_text(encoding='utf-8'))
        errors = collect_runtime_schema_errors(connection, schema_path)
        if errors:
            raise SystemExit(runtime_schema_incompatibility_message(destination, '; '.join(errors)))
        connection.execute(
            '''
            INSERT INTO queue_metadata(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
            ''',
            (RUNTIME_SCHEMA_VERSION,),
        )


def iter_template_files(source_root: Path) -> Iterable[Path]:
    for path in sorted(source_root.rglob('*')):
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_file():
            yield path


def copy_template_files(
    source_root: Path,
    target_root: Path,
    reset_runtime: bool,
    dry_run: bool,
) -> None:
    for src in iter_template_files(source_root):
        rel = src.relative_to(source_root)
        if rel.as_posix() == 'AGENTS.md':
            continue
        dst = target_root / map_template_path(rel)
        validate_target_write_path(dst, target_root)
        if dst.exists() and is_runtime_state_file(rel) and not reset_runtime:
            log(f'既存状態を保持 {dst}')
            continue
        copy_file(src, dst, target_root, dry_run)


def prune_removed_template_files(source_root: Path, target_root: Path, dry_run: bool) -> None:
    for rel in REMOVED_TEMPLATE_REL_PATHS:
        source_path = source_root / rel
        if source_path.exists() and (
            not source_path.is_dir() or source_directory_has_material(source_path)
        ):
            continue
        remove_path(target_root / rel, target_root, dry_run)


def strip_limited_toml_comment(value: str) -> str:
    in_quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == '\\' and in_quote == '"':
            escaped = True
            continue
        if char in ("'", '"'):
            if in_quote == char:
                in_quote = None
            elif in_quote is None:
                in_quote = char
            continue
        if char == '#' and in_quote is None:
            return value[:index].strip()
    return value.strip()


def parse_limited_toml_scalar(raw_value: str) -> object:
    value = strip_limited_toml_comment(raw_value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    normalized = value.replace('_', '')
    if normalized.isdecimal():
        return int(normalized)
    if value == 'true':
        return True
    if value == 'false':
        return False
    return value


def parse_limited_toml(toml_path: Path) -> dict[str, object]:
    document: dict[str, object] = {}
    current: dict[str, object] = document
    lines = toml_path.read_text(encoding='utf-8').splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('[') and stripped.endswith(']') and stripped.count('[') == 1:
            section = stripped[1:-1].strip()
            current = document
            for part in section.split('.'):
                nested = current.setdefault(part, {})
                if not isinstance(nested, dict):
                    raise SystemExit(f'{toml_path} の [{section}] は mapping にしてください。')
                current = nested
            continue
        key, separator, rest = stripped.partition('=')
        if not separator:
            continue
        key = key.strip()
        value = rest.strip()
        if value.startswith('"""'):
            value = value[3:]
            collected: list[str] = []
            if '"""' in value:
                before, _marker, _after = value.partition('"""')
                collected.append(before)
            else:
                collected.append(value)
                while index < len(lines):
                    next_line = lines[index]
                    index += 1
                    if '"""' in next_line:
                        before, _marker, _after = next_line.partition('"""')
                        collected.append(before)
                        break
                    collected.append(next_line)
            current[key] = '\n'.join(collected)
        else:
            current[key] = parse_limited_toml_scalar(value)
    return document


def read_toml_document(toml_path: Path) -> dict[str, object]:
    if not toml_path.exists() or not toml_path.is_file():
        raise SystemExit(f'TOML template が見つかりません: {toml_path}')
    if tomllib is not None:
        try:
            return tomllib.loads(toml_path.read_text(encoding='utf-8'))
        except tomllib.TOMLDecodeError as exc:  # type: ignore[union-attr]
            raise SystemExit(f'{toml_path} の TOML parse に失敗しました: {exc}') from exc
    return parse_limited_toml(toml_path)


def parse_limited_settings_scalar(raw_value: str) -> object:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    if value in {'true', 'false'}:
        return value == 'true'
    if value.isdecimal():
        return int(value)
    if len(value) >= 2 and value[0] == '[' and value[-1] == ']':
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_limited_settings_scalar(item) for item in inner.split(',')]
    if len(value) >= 2 and value[0] == '{' and value[-1] == '}':
        inner = value[1:-1].strip()
        if not inner:
            return {}
        result: dict[str, object] = {}
        for item in inner.split(','):
            key, separator, nested_value = item.partition(':')
            if not separator or not key.strip() or not nested_value.strip():
                raise SystemExit('settings.yaml の inline mapping が不正です。')
            result[key.strip()] = parse_limited_settings_scalar(nested_value)
        return result
    return value


def read_settings_install_subset(settings_path: Path) -> dict[str, object]:
    delegation: dict[str, object] = {}
    workers: dict[str, dict[str, object]] = {}
    model_policy: dict[str, object] = {}
    root_session: dict[str, object] = {}
    settings_version: object = None
    section: str | None = None
    worker_key: str | None = None
    model_policy_subsection: str | None = None

    for line in settings_path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        stripped = line.strip()

        if indent == 0:
            key, separator, rest = stripped.partition(':')
            if key == 'version' and separator and rest.strip():
                settings_version = parse_limited_settings_scalar(rest)
            section = key if separator and not rest.strip() and key in {'delegation', 'model_policy', 'root_session', 'workers'} else None
            worker_key = None
            model_policy_subsection = None
            continue

        if section == 'delegation' and indent == 2:
            key, separator, rest = stripped.partition(':')
            if separator:
                delegation[key] = parse_limited_settings_scalar(rest)
            continue

        if section == 'root_session' and indent == 2:
            key, separator, rest = stripped.partition(':')
            if separator:
                root_session[key] = parse_limited_settings_scalar(rest)
            continue

        if section == 'model_policy':
            if indent == 2:
                key, separator, rest = stripped.partition(':')
                model_policy_subsection = key if separator and not rest.strip() else None
                if separator:
                    model_policy[key] = {} if model_policy_subsection else parse_limited_settings_scalar(rest)
                continue
            if indent == 4 and model_policy_subsection:
                key, separator, rest = stripped.partition(':')
                subsection = model_policy.get(model_policy_subsection)
                if separator and isinstance(subsection, dict):
                    subsection[key] = parse_limited_settings_scalar(rest)
                continue

        if section == 'workers':
            if indent == 2:
                key, separator, rest = stripped.partition(':')
                worker_key = key if separator and not rest.strip() else None
                if worker_key:
                    workers.setdefault(worker_key, {})
                continue
            if indent == 4 and worker_key:
                key, separator, rest = stripped.partition(':')
                if separator:
                    workers[worker_key][key] = parse_limited_settings_scalar(rest)
            continue

    return {
        'version': settings_version,
        'delegation': delegation,
        'model_policy': model_policy,
        'root_session': root_session,
        'workers': workers,
    }


def read_settings(source_root: Path) -> dict[str, object]:
    settings_path = source_root / '.agents' / 'orchestra' / 'config' / 'settings.yaml'
    if yaml is None:
        return read_settings_install_subset(settings_path)
    try:
        document = yaml.safe_load(settings_path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise SystemExit(f'settings.yaml の YAML parse に失敗しました: {exc}') from exc
    if not isinstance(document, dict):
        raise SystemExit('settings.yaml は mapping にしてください。')
    return document


def load_model_policy(source_root: Path) -> tuple[str, dict[str, tuple[str, str]]]:
    settings = read_settings(source_root)
    policy = settings.get('model_policy')
    if not isinstance(policy, dict):
        raise SystemExit('settings.yaml model_policy は mapping にしてください。')
    expected_policy_keys = {
        'root_model',
        'root_project_local_reasoning_effort',
        'root_user_selectable_reasoning_efforts',
        'fixed_pair_per_subagent',
        'subagent_pairs',
    }
    if set(policy) != expected_policy_keys:
        raise SystemExit('settings.yaml model_policy は定義済みのRoot方針とsubagent pairだけを含めてください。')

    root_model = policy.get('root_model')
    if root_model != 'gpt-5.6-sol':
        raise SystemExit('settings.yaml model_policy.root_model は gpt-5.6-sol にしてください。')
    if policy.get('root_project_local_reasoning_effort') != 'unset':
        raise SystemExit('settings.yaml model_policy はRoot reasoning effortをproject-localで固定しないでください。')
    if policy.get('root_user_selectable_reasoning_efforts') != ['high', 'xhigh', 'ultra']:
        raise SystemExit('settings.yaml model_policy のRoot選択肢は high / xhigh / ultra にしてください。')
    if policy.get('fixed_pair_per_subagent') is not True:
        raise SystemExit('settings.yaml model_policy はsubagentごとの固定pairを要求してください。')

    pairs = policy.get('subagent_pairs')
    if not isinstance(pairs, dict) or set(pairs) != set(EXPECTED_AGENT_MODEL_CONFIGS):
        raise SystemExit('settings.yaml model_policy.subagent_pairs は定義済みの10 roleだけを含めてください。')
    normalized: dict[str, tuple[str, str]] = {}
    for role, expected_pair in EXPECTED_AGENT_MODEL_CONFIGS.items():
        pair = pairs.get(role)
        if not isinstance(pair, dict) or set(pair) != {'model', 'model_reasoning_effort'}:
            raise SystemExit(f'settings.yaml model_policy.subagent_pairs.{role} はmodel/effortの固定pairにしてください。')
        actual_pair = (pair.get('model'), pair.get('model_reasoning_effort'))
        if actual_pair != expected_pair:
            raise SystemExit(
                f'settings.yaml model_policy.subagent_pairs.{role} は '
                f'{expected_pair[0]} / {expected_pair[1]} にしてください。'
            )
        normalized[role] = expected_pair
    return root_model, normalized


def validate_settings_release_contract(source_root: Path) -> None:
    settings = read_settings(source_root)
    if settings.get('version') != '5.0':
        raise SystemExit('settings.yaml.version は major release contract 5.0 にしてください。')
    root = settings.get('root_session')
    if not isinstance(root, dict):
        raise SystemExit('settings.yaml root_session は mapping にしてください。')
    expected_keys = {
        'control_plane_only',
        'owns',
        'allowed_observations',
        'delegated_work',
        'forbids',
        'report_required_before_next_action',
        'worker_unavailable_outcome',
        'ultra_mode',
    }
    if set(root) != expected_keys:
        raise SystemExit('settings.yaml root_session は5.0のcoordination-only fieldだけを含めてください。')

    def require_exact_string_set(field: str, expected: set[str]) -> None:
        value = root.get(field)
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) for item in value)
            or len(value) != len(set(value))
            or set(value) != expected
        ):
            raise SystemExit(f'settings.yaml root_session.{field} が5.0 contractと一致しません。')

    owns = {
        'intake',
        'target_repo_binding',
        'authority_check',
        'snapshot_request',
        'direct_assignment',
        'agent_wait',
        'browser_control_tool_observation',
        'report_evidence_gate',
        'next_action',
        'report_synthesis',
    }
    observations = {'target_repo_identity', 'git_status', 'snapshot_helper', 'queue_state', 'browser_observation_facts'}
    delegated_work = {
        'repository_exploration',
        'implementation',
        'validation_execution',
        'browser_planning',
        'browser_allowed_operation_specification',
        'browser_evidence_interpretation',
        'debugging',
        'review_evidence_generation',
    }
    require_exact_string_set('owns', owns)
    require_exact_string_set('allowed_observations', observations)
    require_exact_string_set('delegated_work', delegated_work)
    require_exact_string_set('forbids', {'repository_exploration', 'implementation', 'validation_execution', 'browser_execution', 'debugging', 'review_evidence_generation', 'trial_acceptance', 'ledger_write'})
    if root.get('control_plane_only') is not True or root.get('report_required_before_next_action') is not True:
        raise SystemExit('settings.yaml root_session はcontrol-plane onlyかつworker report待機を必須にしてください。')
    if root.get('worker_unavailable_outcome') != 'needs_human':
        raise SystemExit('settings.yaml root_session はworker不在時にRootが直接fallbackせずneeds_humanにしてください。')
    if root.get('ultra_mode') != 'proactive_delegation_within_fixed_topology':
        raise SystemExit('settings.yaml root_session.ultra_mode は固定topology内のproactive delegationに限定してください。')


def load_worker_roles(source_root: Path) -> dict[str, dict[str, int]]:
    settings = read_settings(source_root)
    delegation = settings.get('delegation')
    if not isinstance(delegation, dict):
        raise SystemExit('settings.yaml delegation は mapping にしてください。')
    max_threads = delegation.get('max_threads')
    if max_threads != 64:
        raise SystemExit('settings.yaml delegation.max_threads は 64 にしてください。')
    workers = settings.get('workers')
    if not isinstance(workers, dict):
        raise SystemExit('settings.yaml workers は mapping にしてください。')
    expected_roles = {
        'cartographer': 2,
        'guildmaster': 1,
        'captain': 2,
        'adventurer': 32,
        'artificer': 1,
        'inquisitor': 2,
        'examiner': 3,
        'sage': 3,
        'warden': 1,
        'courier': 1,
    }
    if set(workers) != set(expected_roles):
        raise SystemExit('settings.yaml workers は定義済みの 10 role だけを含めてください。')

    result: dict[str, dict[str, int]] = {}
    for role, expected_max_parallel in expected_roles.items():
        value = workers.get(role)
        if not isinstance(value, dict):
            raise SystemExit(f'settings.yaml workers.{role} は mapping にしてください。')
        max_parallel = value.get('max_parallel')
        if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or max_parallel < 1:
            raise SystemExit(f'settings.yaml workers.{role}.max_parallel は 1 以上の整数にしてください。')
        if max_parallel != expected_max_parallel:
            raise SystemExit(f'settings.yaml workers.{role}.max_parallel は {expected_max_parallel} にしてください。')
        result[role] = {'max_parallel': max_parallel}
    role_total = sum(worker['max_parallel'] for worker in result.values())
    non_adventurer_total = sum(worker['max_parallel'] for role, worker in result.items() if role != 'adventurer')
    headroom = max_threads - role_total
    if role_total != 48 or role_total > max_threads:
        raise SystemExit('settings.yaml workers の max_parallel 合計は 48 かつ delegation.max_threads=64 以下にしてください。')
    if non_adventurer_total != 16:
        raise SystemExit('settings.yaml workers の非adventurer max_parallel 合計は 16 にしてください。')
    if result['adventurer']['max_parallel'] != 32:
        raise SystemExit('settings.yaml workers.adventurer.max_parallel は 32 にしてください。')
    if headroom != 16:
        raise SystemExit('settings.yaml workers は global 64 に対して未割当 headroom 16 を残してください。')
    return result


def validate_examiner_worker_contract(source_root: Path) -> None:
    settings_path = source_root / '.agents' / 'orchestra' / 'config' / 'settings.yaml'
    lines = settings_path.read_text(encoding='utf-8').splitlines()
    in_workers = False
    in_focus = False
    block: list[str] = []
    for line in lines:
        if line == 'workers:':
            in_workers = True
            continue
        if in_workers and line and not line.startswith(' '):
            break
        if in_workers and line == '  examiner:':
            in_focus = True
            continue
        if in_focus and line.startswith('  ') and not line.startswith('    '):
            break
        if in_focus:
            block.append(line.strip())
    if not block:
        raise SystemExit('settings.yaml workers.examiner block が必要です。')
    required_lines = {
        'implementation_authority: false',
        'decision_authority: false',
        'severity_authority: false',
        'synthesis_authority: false',
        'ledger_authority: false',
        'git_authority: false',
        'external_action_authority: false',
        '- inquisitor',
    }
    missing = sorted(required_lines - set(block))
    forbidden_true = sorted(line for line in block if line.endswith('_authority: true'))
    allowed_callers: set[str] = set()
    if 'allowed_callers:' in block:
        index = block.index('allowed_callers:') + 1
        while index < len(block) and block[index].startswith('- '):
            allowed_callers.add(block[index][2:].strip())
            index += 1
    caller_enforcement_valid = any(line.startswith('caller_enforcement:') and 'policy-only' in line and 'runtime ACL' in line for line in block)
    required_delegation_lines = {
        'root_spawns_top_level_agents: true',
        'top_level_owner: root',
        'max_depth: 2',
        'max_threads: 64',
        'terminal_roles: [cartographer, guildmaster, captain, adventurer, artificer, examiner, sage, warden, courier]',
        'child_scope_must_narrow: true',
        'child_authority_must_narrow: true',
        'child_snapshot_must_match: true',
        'parent_waits_and_synthesizes: true',
        'recursive_fanout_beyond_depth_2: forbidden',
        'runtime_identity_acl: false',
        'nested_edge_enforcement: policy_only',
        'trial_lineage_validation: mechanical',
        'write_role_children_forbidden: true',
        'approval_does_not_grant_authority: true',
        'max_threads_or_parallel_is_cost_hard_cap: false',
        'inquisitor: [examiner]',
        'per_trial_policy_cap: 3',
        'required_by_default: false',
    }
    delegation_missing = sorted(required_delegation_lines - {line.strip() for line in lines})
    if missing or forbidden_true or allowed_callers != {'inquisitor'} or not caller_enforcement_valid or delegation_missing:
        raise SystemExit(
            'settings.yaml の nested delegation / examiner caller / authority contract が不正です: '
            + ', '.join(missing + forbidden_true + delegation_missing or [f'allowed_callers={sorted(allowed_callers)}, caller_enforcement={caller_enforcement_valid}'])
        )


def validate_codex_agent_preflight(source_root: Path) -> None:
    validate_settings_release_contract(source_root)
    validate_examiner_worker_contract(source_root)
    expected_root_model, expected_agent_model_configs = load_model_policy(source_root)
    config_path = source_root / '.codex' / 'config.toml'
    config_text = config_path.read_text(encoding='utf-8')
    config = read_toml_document(config_path)
    required_config_values = {
        'model': expected_root_model,
        'sandbox_mode': 'read-only',
        'approval_policy': 'on-request',
        'approvals_reviewer': 'auto_review',
        'web_search': 'cached',
        'allow_login_shell': False,
    }
    for key, expected in required_config_values.items():
        if config.get(key) != expected:
            raise SystemExit(f'template/.codex/config.toml の既定 {key} は {expected} にしてください。')
    if 'model_reasoning_effort' in config:
        raise SystemExit('template/.codex/config.toml でRoot reasoning effortを固定しないでください。')
    if 'model_context_window' in config:
        raise SystemExit('model_context_window は model catalog に追随させ、Root config で固定しないでください。')
    sandbox_workspace_write = config.get('sandbox_workspace_write')
    if not isinstance(sandbox_workspace_write, dict) or sandbox_workspace_write.get('network_access') is not True:
        raise SystemExit('template/.codex/config.toml の sandbox_workspace_write.network_access は true にしてください。')
    for token in ('"*secret*"', '"*token*"', '"*credential*"', '"*password*"', '"*key*"', '"*auth*"'):
        if token not in config_text:
            raise SystemExit('template/.codex/config.toml の shell_environment_policy.exclude に secret deny glob が不足しています。')
    if 'mcp_servers' in config or '[mcp' in config_text.casefold():
        raise SystemExit('template/.codex/config.toml に MCP server 設定を含めないでください。')
    agents_config = config.get('agents')
    if not isinstance(agents_config, dict):
        raise SystemExit('template/.codex/config.toml の [agents] が必要です。')
    if agents_config.get('max_depth') != 2 or agents_config.get('max_threads') != 64:
        raise SystemExit('template/.codex/config.toml の agents は max_depth=2 / max_threads=64 にしてください。')
    if agents_config.get('job_max_runtime_seconds') != 2400:
        raise SystemExit('template/.codex/config.toml の agents.job_max_runtime_seconds は 2400 にしてください。')

    agents_dir = source_root / '.codex' / 'agents'
    expected_agent_files = {f'{role}.toml' for role in EXPECTED_AGENT_SANDBOX_MODES}
    actual_agent_files = {path.name for path in agents_dir.glob('*.toml')}
    if actual_agent_files != expected_agent_files:
        missing = sorted(expected_agent_files - actual_agent_files)
        unexpected = sorted(actual_agent_files - expected_agent_files)
        details = []
        if missing:
            details.append('missing: ' + ', '.join(missing))
        if unexpected:
            details.append('unexpected: ' + ', '.join(unexpected))
        raise SystemExit('template/.codex/agents の agent file set が期待値と一致しません: ' + '; '.join(details))
    for role, expected_sandbox in EXPECTED_AGENT_SANDBOX_MODES.items():
        agent_path = agents_dir / f'{role}.toml'
        agent = read_toml_document(agent_path)
        if agent.get('name') != role:
            raise SystemExit(f'template/.codex/agents/{role}.toml の name は {role} にしてください。')
        if agent.get('sandbox_mode') != expected_sandbox:
            raise SystemExit(f'template/.codex/agents/{role}.toml の sandbox_mode は {expected_sandbox} にしてください。')
        expected_model, expected_effort = expected_agent_model_configs[role]
        if agent.get('model') != expected_model:
            raise SystemExit(f'template/.codex/agents/{role}.toml の model は {expected_model} にしてください。')
        if agent.get('model_reasoning_effort') != expected_effort:
            raise SystemExit(f'template/.codex/agents/{role}.toml の model_reasoning_effort は {expected_effort} にしてください。')
        features = agent.get('features')
        expected_multi_agent = role == 'inquisitor'
        if not isinstance(features, dict) or features.get('multi_agent') is not expected_multi_agent:
            raise SystemExit(
                f'template/.codex/agents/{role}.toml の features.multi_agent は '
                f'{str(expected_multi_agent).lower()} にしてください。'
            )

    sage_path = source_root / '.codex' / 'agents' / 'sage.toml'
    sage = read_toml_document(sage_path)
    if sage.get('sandbox_mode') != 'read-only':
        raise SystemExit('template/.codex/agents/sage.toml の sandbox_mode は read-only にしてください。')
    developer_instructions = sage.get('developer_instructions')
    if not isinstance(developer_instructions, str):
        raise SystemExit('template/.codex/agents/sage.toml の developer_instructions が必要です。')
    missing_tokens = [token for token in SAGE_DEVELOPER_INSTRUCTION_TOKENS if token not in developer_instructions]
    if missing_tokens:
        raise SystemExit(
            'template/.codex/agents/sage.toml の developer_instructions に sage 契約が不足しています: '
            + ', '.join(missing_tokens)
        )

    examiner_path = source_root / '.codex' / 'agents' / 'examiner.toml'
    examiner = read_toml_document(examiner_path)
    if examiner.get('sandbox_mode') != 'read-only':
        raise SystemExit('template/.codex/agents/examiner.toml の sandbox_mode は read-only にしてください。')
    focus_features = examiner.get('features')
    if not isinstance(focus_features, dict) or focus_features.get('multi_agent') is not False:
        raise SystemExit('template/.codex/agents/examiner.toml の features.multi_agent は false にしてください。')
    examiner_instructions = examiner.get('developer_instructions')
    if not isinstance(examiner_instructions, str):
        raise SystemExit('template/.codex/agents/examiner.toml の developer_instructions が必要です。')
    examiner_tokens = (
        '単一focus',
        'read-only',
        '採否',
        '重大度決定',
        '同一subject snapshot',
        'stale evidence',
        '追加 agent',
    )
    examiner_missing = [token for token in examiner_tokens if token not in examiner_instructions]
    if examiner_missing:
        raise SystemExit(
            'template/.codex/agents/examiner.toml の developer_instructions に bounded reviewer 契約が不足しています: '
            + ', '.join(examiner_missing)
        )

    inquisitor_path = source_root / '.codex' / 'agents' / 'inquisitor.toml'
    inquisitor = read_toml_document(inquisitor_path)
    inquisitor_features = inquisitor.get('features')
    if not isinstance(inquisitor_features, dict) or inquisitor_features.get('multi_agent') is not True:
        raise SystemExit('template/.codex/agents/inquisitor.toml の features.multi_agent は true にしてください。')
    inquisitor_instructions = inquisitor.get('developer_instructions')
    if not isinstance(inquisitor_instructions, str):
        raise SystemExit('template/.codex/agents/inquisitor.toml の developer_instructions が必要です。')
    inquisitor_tokens = ('risk-triggered', '単一focus', 'examiner', '完全一致', '最大3', '完了を待ち', 'lineage', '重大度', '再帰fan-out', 'depth 2', 'runtime identity ACL')
    inquisitor_missing = [token for token in inquisitor_tokens if token not in inquisitor_instructions]
    if inquisitor_missing:
        raise SystemExit(
            'template/.codex/agents/inquisitor.toml の developer_instructions に nested review 契約が不足しています: '
            + ', '.join(inquisitor_missing)
        )

    warden_path = source_root / '.codex' / 'agents' / 'warden.toml'
    warden = read_toml_document(warden_path)
    if warden.get('sandbox_mode') != 'read-only':
        raise SystemExit('template/.codex/agents/warden.toml の sandbox_mode は read-only にしてください。')
    warden_instructions = warden.get('developer_instructions')
    if not isinstance(warden_instructions, str):
        raise SystemExit('template/.codex/agents/warden.toml の developer_instructions が必要です。')
    warden_missing = [token for token in WARDEN_DEVELOPER_INSTRUCTION_TOKENS if token not in warden_instructions]
    if warden_missing:
        raise SystemExit(
            'template/.codex/agents/warden.toml の developer_instructions に warden 契約が不足しています: '
            + ', '.join(warden_missing)
        )


def validate_source_file_set_preflight(source_root: Path) -> None:
    missing = sorted(str(rel) for rel in SOURCE_REQUIRED_REL_PATHS if not (source_root / rel).is_file())
    if missing:
        raise SystemExit('source template に必須 file が不足しています: ' + ', '.join(missing))

    skills_root = source_root / '.agents' / 'skills'
    actual_skills = source_skill_directory_names(skills_root)
    if actual_skills != EXPECTED_ORCHESTRA_SKILL_DIRS:
        missing_skills = sorted(EXPECTED_ORCHESTRA_SKILL_DIRS - actual_skills)
        unexpected_skills = sorted(actual_skills - EXPECTED_ORCHESTRA_SKILL_DIRS)
        details = []
        if missing_skills:
            details.append('missing: ' + ', '.join(missing_skills))
        if unexpected_skills:
            details.append('unexpected: ' + ', '.join(unexpected_skills))
        raise SystemExit('source template の .agents/skills directory set が期待値と一致しません: ' + '; '.join(details))


def source_skill_directory_names(skills_root: Path) -> set[str]:
    if not skills_root.is_dir():
        return set()
    return {
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (
            path.name in EXPECTED_ORCHESTRA_SKILL_DIRS or source_directory_has_material(path)
        )
    }


def source_directory_has_material(directory: Path) -> bool:
    """directoryに、空directory以外の内容があるかを返します。"""
    for path in directory.rglob('*'):
        if path.is_symlink() or not path.is_dir():
            return True
    return False


def preflight_source_template(source_root: Path) -> None:
    validate_source_file_set_preflight(source_root)
    canonical_runtime_schema_signature(source_root / '.agents' / 'orchestra' / 'scripts' / 'queue_schema.sql')
    load_worker_roles(source_root)
    validate_codex_agent_preflight(source_root)


def validate_source_tree_trust(source_root: Path, allow_non_default_source: bool) -> None:
    if source_root != DEFAULT_SOURCE.resolve() and not allow_non_default_source:
        raise SystemExit('既定以外の `--source` は明示許可が必要です。信頼済み template の場合だけ `--allow-non-default-source` を併用してください。')
    for path in source_root.rglob('*'):
        rel = path.relative_to(source_root)
        if path.is_symlink():
            raise SystemExit(f'source template に symlink は使えません: {rel}')
        if rel.parts and rel.parts[0] not in TRUSTED_SOURCE_TOP_LEVELS:
            raise SystemExit(f'source template に許可外の top-level path が含まれています: {rel}')
        risky = risky_source_path_tokens(rel)
        if risky:
            raise SystemExit(f'source template に秘密情報または外部 tool 連携を疑う path が含まれています: {rel} ({", ".join(risky)})')


def risky_source_path_tokens(rel: Path) -> list[str]:
    if rel in TRUSTED_SOURCE_RISKY_PATH_EXCEPTIONS:
        return []
    parts = {part.casefold() for part in rel.parts}
    split_terms = {
        term
        for part in parts
        for term in PATH_TERM_RE.split(part.strip('.'))
        if term
    }
    risky: list[str] = []
    for token in sorted(UNTRUSTED_SOURCE_PATH_TOKENS):
        normalized = token.casefold()
        if normalized in parts:
            risky.append(token)
        elif any(token_matches_path_part(normalized, part) for part in parts):
            risky.append(token)
        elif normalized.isalnum() and normalized in split_terms:
            risky.append(token)
    return risky


def token_matches_path_part(normalized_token: str, part: str) -> bool:
    if not part.startswith(normalized_token):
        return False
    if normalized_token.startswith('.'):
        return True
    if len(part) == len(normalized_token):
        return True
    return not part[len(normalized_token)].isalnum()


def ensure_repositories_root(target_root: Path, dry_run: bool) -> None:
    path = target_root / REPOSITORIES_REL_PATH
    validate_target_write_path(path, target_root)
    ensure_directory(path, dry_run)


def update_git_exclude(target_root: Path, enabled: bool, dry_run: bool) -> None:
    exclude_path = target_root / '.git' / 'info' / 'exclude'
    if not enabled:
        return
    validate_target_managed_path(exclude_path, target_root)
    if not exclude_path.parent.exists():
        return

    lines = [
        EXCLUDE_START,
        '.agents/orchestra/',
        '.codex/',
        '.orchestra/',
        f'{BACKUP_DIRECTORY}/',
    ]
    lines.append(EXCLUDE_END)
    block = '\n'.join(lines) + '\n'
    upsert_text_block(exclude_path, block, EXCLUDE_START, EXCLUDE_END, target_root, dry_run)


def update_agents_md(target_root: Path, source_root: Path, dry_run: bool) -> None:
    body = (source_root / 'AGENTS.md').read_text(encoding='utf-8').rstrip()
    block = f'{AGENTS_START}\n{body}\n{AGENTS_END}\n'
    upsert_text_block(target_root / 'AGENTS.md', block, AGENTS_START, AGENTS_END, target_root, dry_run)


def validate_target(target_root: Path) -> None:
    if target_root == ROOT:
        raise SystemExit('この配布元リポジトリ自身をギルド規約ルートにはできません。動作確認は一時ディレクトリか別のギルド規約ルートを指定してください。')
    if target_root == Path(target_root.anchor).resolve() or target_root == Path.home().resolve():
        raise SystemExit('導入先が広すぎます。専用のギルド規約ルートを指定してください。')
    if any(part == REPOSITORIES_REL_PATH.name for part in target_root.parts):
        raise SystemExit('導入先は子リポジトリや repositories/ ではなく、その親のギルド規約ルートを指定してください。')


def main() -> int:
    args = parse_args()
    if args.mode != 'copy':
        raise SystemExit('copy モードのみ対応しています。')

    source_root = Path(args.source).expanduser().resolve()
    target_root = Path(args.target).expanduser().resolve()

    if not source_root.exists() or not source_root.is_dir():
        raise SystemExit(f'コピー元が見つかりません: {source_root}')

    validate_target(target_root)
    validate_source_tree_trust(source_root, args.allow_non_default_source)
    preflight_source_template(source_root)
    validate_existing_runtime_schema(source_root, target_root, args.reset_runtime, args.clean_install)
    if args.clean_install and not args.backup and not args.dry_run and not args.allow_clean_install_without_backup:
        raise SystemExit('`--clean-install` は既存の導入物、Ledger、dashboard、queue を削除または初期化します。通常は `--backup` を併用してください。バックアップなしで進める場合だけ `--allow-clean-install-without-backup` を明示してください。`repositories/` 配下は保持します。')
    if args.reset_runtime and not args.clean_install and not args.backup and not args.dry_run and not args.allow_reset_runtime_without_backup:
        raise SystemExit('`--reset-runtime` は既存の Ledger/dashboard 状態を削除します。通常は `--backup` を併用してください。バックアップなしで進める場合だけ `--allow-reset-runtime-without-backup` を明示してください。')
    ensure_directory(target_root, args.dry_run)
    ensure_repositories_root(target_root, args.dry_run)

    if source_root != DEFAULT_SOURCE.resolve():
        log('注意: 既定以外の `--source` を明示許可付きで使っています。信頼済み template だけを指定してください。')

    log('注意: この導入はギルド規約ルートの `.agents/orchestra/`、`.codex/`、`.orchestra/` を作成または更新し、`repositories/` を用意します。Codex の protected path 上で実行する場合は承認が必要になることがあります。')
    if not args.no_git_exclude:
        log('注意: Git ルートでは `.git/info/exclude` も更新します。避ける場合は `--no-git-exclude` を指定してください。')

    if args.backup:
        backup_existing(target_root, args.dry_run)

    if args.clean_install:
        clean_install_target(target_root, not args.no_git_exclude, args.dry_run)
    elif args.reset_runtime:
        reset_runtime_state(target_root, args.dry_run)

    reset_runtime_state_requested = args.reset_runtime or args.clean_install
    copy_template_files(
        source_root,
        target_root,
        reset_runtime_state_requested,
        args.dry_run,
    )
    prune_removed_template_files(source_root, target_root, args.dry_run)
    update_agents_md(target_root, source_root, args.dry_run)
    initialize_sqlite_runtime(source_root, target_root, reset_runtime_state_requested, args.dry_run)

    update_git_exclude(target_root, not args.no_git_exclude, args.dry_run)

    if args.dry_run:
        log('dry-run: 変更は書き込んでいません。')
    else:
        log('完了: ギルド規約ルートへの最小ランタイム導入が終わりました。子リポジトリは repositories/<repo> に配置してください。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
