"""Documentation and Skill package consistency checks."""

from __future__ import annotations

import re
from pathlib import Path

from .basic import CORE_SKILLS, MAINTAINER_SKILLS, OPTIONAL_SKILLS
from .core import ROOT, require


LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def validate_local_links() -> None:
    documents = [*(ROOT.glob("*.md")), *(ROOT / "docs").rglob("*.md")]
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            raw = match.group(1).split("#", 1)[0]
            if not raw or "://" in raw or raw.startswith("mailto:"):
                continue
            destination = (document.parent / raw).resolve()
            require(destination.exists(), f"broken local link in {document.relative_to(ROOT)}: {raw}")


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    require(lines and lines[0] == "---", f"{path.relative_to(ROOT)} needs YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        require(False, f"{path.relative_to(ROOT)} has unterminated frontmatter")
        raise AssertionError
    result: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip().strip('"')
    return result


def validate_skill_packages() -> None:
    roots = [
        (ROOT / "template/.agents/skills", CORE_SKILLS),
        (ROOT / "maintainer-skills", MAINTAINER_SKILLS),
        (ROOT / "optional-skills", OPTIONAL_SKILLS),
    ]
    for package_root, expected in roots:
        for name in expected:
            skill = package_root / name / "SKILL.md"
            require(skill.is_file(), f"missing Skill: {skill.relative_to(ROOT)}")
            metadata = frontmatter(skill)
            require(metadata.get("name") == name, f"Skill name/path mismatch: {skill.relative_to(ROOT)}")
            require(bool(metadata.get("description")), f"Skill description is missing: {skill.relative_to(ROOT)}")


def validate_docs() -> None:
    validate_local_links()
    validate_skill_packages()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    require("3.0.0" in readme, "README must identify release 3.0.0")
    require((ROOT / "docs/migration-v3.md").is_file(), "v3 migration guide is missing")
    require((ROOT / "docs/model-selection-evaluation.md").is_file(), "evaluation protocol is missing")
