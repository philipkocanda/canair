# Releasing canair

canair follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR** — incompatible CLI/behaviour or profile/capture-schema changes.
- **MINOR** — new commands/features, backwards compatible.
- **PATCH** — backwards-compatible bug fixes.

The version is single-sourced from `pyproject.toml` (`[project].version`) and
surfaced at runtime as `canlib.__version__` (read via `importlib.metadata`) and
`canair --version`. There is no second copy to keep in sync.

## Release checklist

Run everything with `uv run …` from the repo root.

1. **Green tree.** All gates must pass (these mirror the CI workflow):

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run canair validate all
   uv run pytest -q
   ```

2. **Bump the version** in `pyproject.toml`:

   ```bash
   uv run canair --version   # sanity-check the current value first
   ```

   Edit `[project].version`, then re-sync so the installed metadata matches:

   ```bash
   uv sync
   uv run canair --version   # confirm it now reports the new version
   ```

3. **Update `CHANGELOG.md`.** Move items out of `[Unreleased]` into a new
   `[X.Y.Z] - YYYY-MM-DD` section, and refresh the compare/tag links at the
   bottom of the file.

4. **Commit** the version bump + changelog together:

   ```bash
   git add pyproject.toml uv.lock CHANGELOG.md
   git commit -m "Release vX.Y.Z"
   ```

5. **Tag** an annotated release and push:

   ```bash
   git tag -a vX.Y.Z -m "canair vX.Y.Z"
   git push origin main --follow-tags
   ```

6. **(Optional) GitHub release.** Cut release notes from the changelog:

   ```bash
   gh release create vX.Y.Z --title "canair vX.Y.Z" --notes-file - <<'EOF'
   <paste the CHANGELOG section>
   EOF
   ```

## Notes

- Tags are `vX.Y.Z` (with the `v` prefix); the `pyproject.toml` version is the
  bare `X.Y.Z`.
- If you build a wheel, `hatchling` reads the version straight from
  `pyproject.toml`, so no extra step is needed.
