.PHONY: validate install-dry-run

validate:
	bash scripts/docker-run.sh validate

install-dry-run:
	tmp="$$(mktemp -d)"; \
	trap 'rm -rf "$$tmp"' EXIT; \
	./scripts/install.sh --target "$$tmp" --dry-run
