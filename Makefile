# Convenience wrappers around the project's generators and gates. Everything
# runs through `uv run` so it uses the pinned toolchain (see pyproject.toml).
# CI and the pre-commit hooks call these same scripts directly.

# Material for MkDocs prints a red MkDocs-2.0 advocacy notice on every mkdocs
# invocation (stderr, exit 0 — it never fails a build). It is not actionable
# here: canair uses no third-party plugins and no theme overrides, and
# mkdocs-material already pins mkdocs<2, so for us it is pure log noise.
# Revisit if MkDocs 1.x takes a security issue or Material 9.x stops building.
# Rationale: plans/2026-07-24-documentation-strategy.md.
export NO_MKDOCS_2_WARNING := 1

.PHONY: help setup lfs-check docs docs-serve screenshots screenshots-check gen gen-check check

help:
	@echo "Targets:"
	@echo "  setup              Install Git LFS, deps and the git hooks (run once per clone)"
	@echo "  screenshots        Regenerate the doc screenshots + animations (needs freeze + vhs)"
	@echo "  screenshots-check  Verify screenshots are current (no render; used by CI)"
	@echo "  gen                Regenerate all committed generated docs (CLI reference, screenshots)"
	@echo "  gen-check          Verify all committed generated docs are current"
	@echo "  docs               Build the docs site (mkdocs --strict)"
	@echo "  docs-serve         Serve the docs locally with live reload"
	@echo "  lfs-check          Verify every Git LFS pointer has its object on the server"
	@echo "  check              Lint, type-check, validate, and run tests"

# One-time per-clone bootstrap: sync the pinned toolchain and install the git
# hooks that mirror the CI gates (ruff/ty on commit, Conventional Commits on the
# message, generated-artifact currency on push). Skipping the hooks is how a
# locally-avoidable failure reaches CI, so this bundles them into one command.
#
# `git lfs install` comes first on purpose: it installs git-lfs's own pre-push hook
# (the upload of large files), and `pre-commit install` then keeps that hook and chains
# it rather than overwriting it. The other order loses the upload, so a push publishes
# commits whose LFS objects never arrive and the next pull elsewhere fails with a 404.
# `--force` is what makes a re-run work: without it git-lfs refuses to touch a pre-push
# hook that pre-commit already generated, and fails. pre-commit then moves the
# freshly written LFS hook aside again, so repeating this is harmless.
setup:
	git lfs install --force
	uv sync
	uv run pre-commit install --install-hooks

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
	uv run python scripts/gen_screenshots.py

gen-check:
	uv run python scripts/gen_cli_reference.py --check
	uv run python scripts/gen_screenshots.py --check

docs:
	uv run mkdocs build --strict --site-dir site

docs-serve:
	uv run mkdocs serve

lfs-check:
	uv run python scripts/check_lfs_objects.py

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest -q
