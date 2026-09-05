.PHONY: validate install-dry-run

PYTHON ?= python3

validate:
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" scripts/validate.py

install-dry-run:
	tmp="$$(mktemp -d)"; \
	trap 'rm -rf "$$tmp"' EXIT; \
	git -C "$$tmp" init -q; \
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" scripts/install.py --target "$$tmp" --dry-run
