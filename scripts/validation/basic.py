"""repository 全体の基本検証。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from .core import ROOT, read, require, tomllib, yaml
from .rules import ACTIVE_PROSE_DRIFT_TERMS, ACTIVE_PROSE_PATHS, REQUIRED_PATHS


LEGACY_FULL_BRAND_RE = re.compile("codex" + r"[-_ ]guild", re.IGNORECASE)
LEGACY_UPPER_ACRONYM_RE = re.compile(r"(?<![A-Za-z0-9_])" + ("C" + "GO") + r"(?![A-Za-z0-9_])")
LEGACY_UPPER_EVAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])" + ("C" + "GO") + r"_EVAL(?:_[A-Z0-9_]+)?(?![A-Za-z0-9_])"
)
LEGACY_LOWER_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_])" + ("c" + "go") + r"[-_](?:snapshot|detached|timeout|eval)"
)
BRAND_SCAN_EXTENSIONS = {".json", ".md", ".py", ".sh", ".sql", ".toml", ".txt", ".yaml", ".yml"}
BRAND_SCAN_FILENAMES = {"Dockerfile", "LICENSE", "Makefile", "VERSION", ".dockerignore", ".gitignore"}
BRAND_SCAN_ROOTS = (".github", "docs", "scripts", "template")
BRAND_SENSITIVE_COMPONENTS = {
    ".aws",
    ".azure",
    ".env",
    ".envrc",
    ".kube",
    ".mcp",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
    "envrc",
    "id_dsa",
    "id_ecdsa",
    "id_ecdsa_sk",
    "id_ed25519",
    "id_ed25519_sk",
    "id_rsa",
    "netrc",
    "npmrc",
    "pypirc",
    "kubeconfig",
}
BRAND_ALWAYS_SENSITIVE_TERMS = {"credential", "credentials", "password", "passwords", "secret", "secrets"}
BRAND_CONTEXT_SENSITIVE_TERMS = {"auth", "key", "keys", "oauth", "token", "tokens"}
BRAND_EXACT_SENSITIVE_CONTAINERS = {"credential", "credentials", "secret", "secrets"}
BRAND_SENSITIVE_DATA_SUFFIXES = {".cfg", ".conf", ".ini", ".json", ".toml", ".txt", ".yaml", ".yml"}
BRAND_SECRET_SUFFIXES = {".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
BRAND_PII_RE = re.compile(r"(?:^|[-_.])(pii|personal[-_]?data|customer[-_]?export)(?:$|[-_.])", re.IGNORECASE)
BRAND_SERVICE_ACCOUNT_RE = re.compile(r"(?:^|[-_.])service[-_]?accounts?(?:$|[-_.])", re.IGNORECASE)
BRAND_ANSIBLE_VAULT_RE = re.compile(r"(?:^|[-_.])ansible[-_]vault(?:$|[-_.])", re.IGNORECASE)
BRAND_KUBECONFIG_RE = re.compile(r"(?:^|[-_.])kubeconfig(?:$|[-_.])", re.IGNORECASE)
REQUIRED_ROOT_BRAND_COVERAGE = {"LICENSE.ja.md", "SUPPORT.md", "THIRD_PARTY_NOTICES.md", "requirements.txt"}


def _has_legacy_brand(text: str) -> bool:
    return any(
        pattern.search(text) is not None
        for pattern in (LEGACY_FULL_BRAND_RE, LEGACY_UPPER_ACRONYM_RE, LEGACY_UPPER_EVAL_RE, LEGACY_LOWER_IDENTIFIER_RE)
    )


def _is_sensitive_brand_path(rel: str) -> bool:
    parts = [part.casefold() for part in rel.split("/")]
    for index, part in enumerate(parts):
        path_part = PurePosixPath(part)
        suffixes = {suffix.casefold() for suffix in path_part.suffixes}
        split_terms = {term for term in re.split(r"[^a-z0-9]+", part.strip(".")) if term}
        if (
            any(
                part.startswith(component)
                and (len(part) == len(component) or not part[len(component)].isalnum())
                for component in BRAND_SENSITIVE_COMPONENTS
            )
            or bool(suffixes & (BRAND_SECRET_SUFFIXES | {".tfstate"}))
            or part in BRAND_EXACT_SENSITIVE_CONTAINERS
            or (bool(split_terms & BRAND_ALWAYS_SENSITIVE_TERMS) and bool(suffixes & BRAND_SENSITIVE_DATA_SUFFIXES))
            or (bool(split_terms & BRAND_CONTEXT_SENSITIVE_TERMS) and bool(suffixes & BRAND_SENSITIVE_DATA_SUFFIXES))
            or BRAND_PII_RE.search(part) is not None
            or (
                BRAND_SERVICE_ACCOUNT_RE.search(part) is not None
                and (part in {"service-account", "service_account"} or bool(suffixes & BRAND_SENSITIVE_DATA_SUFFIXES))
            )
            or (
                BRAND_ANSIBLE_VAULT_RE.search(part) is not None
                and (part in {"ansible-vault", "ansible_vault"} or bool(suffixes & BRAND_SENSITIVE_DATA_SUFFIXES))
            )
            or BRAND_KUBECONFIG_RE.search(part) is not None
        ):
            return True
        if index + 1 < len(parts) and part == ".docker" and parts[index + 1] == "config.json":
            return True
        if index + 2 < len(parts) and part == ".config" and parts[index + 1 : index + 3] == ["gh", "hosts.yml"]:
            return True
    return False


def _brand_tree_paths(root: Path) -> list[Path]:
    pending = [root]
    paths: list[Path] = []
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir())
        except OSError as exc:
            require(False, f"旧ブランド監査でdirectoryを列挙できません: {directory}: {exc}")
        for path in children:
            paths.append(path)
            rel = path.relative_to(ROOT).as_posix()
            if path.is_symlink() or _is_sensitive_brand_path(rel):
                continue
            if path.is_dir():
                pending.append(path)
    return paths


def validate_brand_identity() -> None:
    legacy_lower = "c" + "go"
    legacy_upper = "C" + "GO"
    sensitive_legacy_path = "docs/" + legacy_lower + "-snapshot-secret.txt"
    for text in (
        "Codex" + " Guild",
        "CODEX" + "_GUILD_WRAPPER",
        legacy_upper,
        legacy_upper + "_EVAL_WORKDIR",
        legacy_lower + "-snapshot-v1",
        legacy_lower + "-detached-child-probe",
        legacy_lower + "-timeout-cleanup-probe",
        legacy_lower + "-eval-workdir",
        legacy_lower + "_snapshot_v1",
        legacy_lower + "_eval_workdir",
    ):
        require(_has_legacy_brand(text), f"旧ブランド検出patternが既知identifierを検出できません: {text}")
    for text in (
        "OpenAI Codex integration",
        "CODEX_HOOK_PAYLOAD",
        legacy_lower + " interoperability",
        "runtime uses " + legacy_lower,
        "s" + legacy_lower + "-wrapper",
        "my" + legacy_lower + "-snapshot",
        legacy_upper + "_ENABLED",
    ):
        require(not _has_legacy_brand(text), f"正当なCodex/Go表現を旧ブランドとして扱わないでください: {text}")
    require(
        _has_legacy_brand(sensitive_legacy_path) and _is_sensitive_brand_path(sensitive_legacy_path),
        "機密らしいpathはpathnameの旧ブランドだけを検出し、contentを読まないでください。",
    )
    for rel in (
        ".env.yaml",
        "service-account.json",
        "service_account.json",
        "id_rsa.txt",
        "id_ed25519.pub.bak",
        ".envrc",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "client-secret.pem",
        "customer-export.csv",
        "passwords.yaml",
        "api-keys.json",
        "keys.txt",
        "private-keys.yaml",
        "service-accounts.json",
        "prod-service-account.json",
        "gcp_service_account.json",
        "client.pem.txt",
        "cert.p12.json",
        "store.pfx.txt",
        "signing.jks.yaml",
        "store.keystore.txt",
        "client.pem.bak.txt",
        ".docker/config.json",
        ".config/gh/hosts.yml",
        ".azure/azureProfile.json",
        "kubeconfig",
        "kubeconfig.yaml",
        "prod-kubeconfig.json",
        "terraform.tfstate",
        "terraform.tfstate.json",
        "prod-ansible-vault.yml",
        "gcp_ansible_vault.json",
        "secrets/README.md",
        "credentials/README.md",
        "auth.json",
        "oauth-config.yaml",
        "token-store.json",
    ):
        require(_is_sensitive_brand_path(rel), f"secret-like pathをcontent読取前に拒否してください: {rel}")
    for rel in (
        "LICENSE.ja.md",
        "SUPPORT.md",
        "THIRD_PARTY_NOTICES.md",
        "requirements.txt",
        "docs/keyboard-shortcuts.md",
        "docs/keynote.md",
        "docs/authoring-guide.md",
        "docs/service-accounting.md",
        "docs/accountability.md",
        "docs/auth-flow.md",
        "docs/oauth-guide.md",
        "docs/keys-and-values.md",
        "docs/token-budget.md",
        "scripts/token_count.py",
        "docs/auth/flow.md",
        "docs/secrets-management.md",
        "docs/password-policy.md",
        "docs/service-account-guide.md",
        "docs/ansible-vault-guide.md",
    ):
        require(not _is_sensitive_brand_path(rel), f"通常のdoc pathをsecret-likeとして除外しないでください: {rel}")

    root_paths = list(ROOT.iterdir())
    covered_root_text_files = {
        path.name
        for path in root_paths
        if not path.is_symlink()
        and path.is_file()
        and (path.suffix in BRAND_SCAN_EXTENSIONS or path.name in BRAND_SCAN_FILENAMES)
    }
    require(REQUIRED_ROOT_BRAND_COVERAGE <= covered_root_text_files, "root直下の既存text fileを旧ブランド監査対象にしてください。")

    paths = list(root_paths)
    for rel in BRAND_SCAN_ROOTS:
        scan_root = ROOT / rel
        require(not scan_root.is_symlink() and scan_root.is_dir(), f"旧ブランド監査rootが通常directoryではありません: {rel}")
        paths.extend(_brand_tree_paths(scan_root))

    matches: list[str] = []
    for path in sorted(paths):
        rel = path.relative_to(ROOT)
        if _has_legacy_brand(rel.as_posix()):
            matches.append(str(rel))
        if _is_sensitive_brand_path(rel.as_posix()):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix not in BRAND_SCAN_EXTENSIONS and path.name not in BRAND_SCAN_FILENAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            require(False, f"旧ブランド監査で {rel} を読めません: {exc}")
        if _has_legacy_brand(text):
            matches.append(str(rel))
    require(not matches, "旧ブランド由来の名前が残っています: " + ", ".join(matches))


def validate_active_prose_vocabulary() -> None:
    paths = list(ACTIVE_PROSE_PATHS)
    skills_root = ROOT / "template/.agents/skills"
    if skills_root.exists():
        paths.extend(str(path.relative_to(ROOT)) for path in sorted(skills_root.glob("*/SKILL.md")))

    for rel in paths:
        text = read(rel)
        for token in ACTIVE_PROSE_DRIFT_TERMS:
            require(token not in text, f"{rel} に Guild 命名から外れる表現 `{token}` が残っています。")


def validate_dependencies() -> None:
    missing = []
    if yaml is None:
        missing.append("PyYAML")
    if tomllib is None:
        missing.append("tomllib/tomli")
    require(not missing, "検証に必要な依存がありません: " + ", ".join(missing))


def validate_required_paths() -> None:
    missing = [path for path in REQUIRED_PATHS if not (ROOT / path).exists()]
    require(not missing, "不足している必須ファイルがあります: " + ", ".join(missing))


def validate_version() -> None:
    version = read("VERSION").strip()
    parts = version.split(".")
    require(len(parts) == 3 and all(part.isdecimal() for part in parts), "VERSION は MAJOR.MINOR.PATCH 形式にしてください。")
    require(version == "2.0.0", "この release contract の VERSION は 2.0.0 にしてください。")
    require(
        f"現在のバージョンは`{version}`" in read("README.md"),
        "README.md の現在バージョンを VERSION と同期してください。",
    )
