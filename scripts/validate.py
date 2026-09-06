#!/usr/bin/env python3
"""Validate the v3 distribution with structural and behavioral checks."""

from __future__ import annotations

import sys

from validation.basic import (
    validate_codex_config,
    validate_dependencies,
    validate_manifest_parse,
    validate_no_retired_runtime,
    validate_required_paths,
    validate_version,
)
from validation.core import ValidationError
from validation.docs import validate_docs
from validation.install_smoke import validate_install_upgrade_smoke
from validation.model_selection import validate_model_selection_eval
from validation.runtime_smoke import validate_runtime_smoke


def main() -> int:
    checks = [
        validate_dependencies,
        validate_version,
        validate_required_paths,
        validate_codex_config,
        validate_no_retired_runtime,
        validate_manifest_parse,
        validate_docs,
        validate_runtime_smoke,
        validate_install_upgrade_smoke,
        validate_model_selection_eval,
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
