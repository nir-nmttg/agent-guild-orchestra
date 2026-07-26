#!/usr/bin/env python3
"""Guild-native runtime contract validator."""

from __future__ import annotations

import sys

from validation.basic import (
    validate_active_prose_vocabulary,
    validate_brand_identity,
    validate_dependencies,
    validate_required_paths,
    validate_version,
)
from validation.claude_compat_smoke import validate_claude_compat_smoke
from validation.courier_contract_mutations import validate_courier_contract_mutations
from validation.core import ValidationError
from validation.docs import validate_agents, validate_docs_and_instructions, validate_skills, validate_stop_hook
from validation.golden_quests import validate_golden_quests
from validation.install_smoke import validate_install_upgrade_smoke
from validation.model_selection import validate_model_selection_eval
from validation.queue_templates import validate_queue_templates
from validation.runtime_smoke import validate_queue_db_smoke, validate_sqlite_schema
from validation.root_orchestration import validate_root_orchestration_eval
from validation.settings import validate_settings
from validation.snapshot_digest import validate_snapshot_digest


def main() -> int:
    checks = [
        validate_dependencies,
        validate_required_paths,
        validate_version,
        validate_brand_identity,
        validate_settings,
        validate_queue_templates,
        validate_sqlite_schema,
        validate_queue_db_smoke,
        validate_golden_quests,
        validate_courier_contract_mutations,
        validate_model_selection_eval,
        validate_root_orchestration_eval,
        validate_snapshot_digest,
        validate_install_upgrade_smoke,
        validate_claude_compat_smoke,
        validate_agents,
        validate_active_prose_vocabulary,
        validate_docs_and_instructions,
        validate_skills,
        validate_stop_hook,
    ]
    for check in checks:
        check()
    print("validate: ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"validate: error: {exc}", file=sys.stderr)
        raise SystemExit(1)
