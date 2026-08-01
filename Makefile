# Convenience wrappers around the project's generators and gates. Everything
# runs through `uv run` so it uses the pinned toolchain (see pyproject.toml).
# CI and the pre-commit hooks call these same scripts directly.

.PHONY: help docs docs-serve screenshots screenshots-check gen gen-check check

help:
	@echo "Targets:"
	@echo "  screenshots        Regenerate the doc screenshots + animations (needs freeze + vhs)"
	@echo "  screenshots-check  Verify screenshots are current (no render; used by CI)"
	@echo "  gen                Regenerate all generated docs (CLI reference, profiles index, screenshots)"
	@echo "  gen-check          Verify all generated docs are current"
	@echo "  docs               Build the docs site (mkdocs --strict)"
	@echo "  docs-serve         Serve the docs locally with live reload"
	@echo "  check              Lint, type-check, validate, and run tests"

screenshots:
	uv run python scripts/gen_screenshots.py

# Regenerate a subset:  make screenshots-only ONLY="bus ecu decode-plot"
.PHONY: screenshots-only
screenshots-only:
	uv run python scripts/gen_screenshots.py --only $(ONLY)

screenshots-check:
	uv run python scripts/gen_screenshots.py --check

gen:
	uv run python scripts/gen_cli_reference.py
	uv run python scripts/gen_profiles_index.py
	uv run python scripts/gen_screenshots.py

gen-check:
	uv run python scripts/gen_cli_reference.py --check
	uv run python scripts/gen_profiles_index.py --check
	uv run python scripts/gen_screenshots.py --check

docs:
	uv run mkdocs build --strict --site-dir site

docs-serve:
	uv run mkdocs serve

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest -q
