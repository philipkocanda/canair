# Move the docs site from MkDocs Material to Zensical

Status: **PROPOSED**. No repo file changed yet. The findings below come from trial builds in a
throwaway copy of `docs/`, `mkdocs.yml` and `scripts/` (Zensical 0.1.1, Python 3.15.2, macOS).
Nothing ran on CI's Linux runner and nobody opened the result in a browser.

Paths are relative to the repo root. Point at code by file and symbol: line numbers drift.

## Why

- Material for MkDocs is in its last maintenance window. Critical fixes and security updates
  end on **2027-05-05**. After that date nothing patches the engine that builds our site.
- `plans/2026-07-24-documentation-strategy.md` ("Toolchain: the MkDocs 2.0 / Zensical split")
  decided not to migrate. It named one trigger: "we want a Material 10 / Zensical feature",
  and others. The facts behind that decision have changed in two ways:
  - The decision table says canair has **0 hooks**. The tree now has **3**
    (`scripts/mkdocs_hooks/`). The table is out of date.
  - Zensical 0.1.x is now the "dependable release line" (announced for 2026-11-05) and it
    reads `mkdocs.yml` directly.
- Material 9.8.0 is already the locked version. We have about six months to move without
  pressure. Moving later costs the same, so the only reason to start now is to avoid a
  deadline.

## Findings

### The easy part: the config and content build unchanged

`zensical build -f mkdocs.yml` on the current tree takes about 1 second. MkDocs takes about
14 seconds (mostly the explorer data hook). Checked in the trial build:

- All nav pages render. `exclude_docs`, `pymdownx.snippets` with `auto_append`, `abbr`
  (the tooltip text is in the HTML), `def_list`, `tabbed`, `toc.permalink` and the
  `!!python/name:` fence format all work with no change.
- The `mermaid` fence still emits `<div class="pele-diagram">`, so Pele still has its target.
- The 8 warnings in the trial build were **all** "page does not exist" links to
  `profiles/index.md`, a page that a hook generates. Zensical found no other broken link
  or anchor. `zensical build --strict` exits 1 on a warning, like `mkdocs --strict`.
- `window.document$` exists in Zensical's JS bundle, so `pele-init.js` and `explorer.js`
  should still remount after an instant navigation. Not tested in a browser.
- `zensical-ui` and the other dependencies resolve on Python 3.15.2.

### The hard part: `hooks:` is unsupported

Zensical lists `hooks` as an unsupported `mkdocs.yml` setting and says nothing about a date.
Without the hooks the build is **silently wrong**: it ignores them and makes no sign.
Three hook files do four jobs:

| Hook | What it does | Needs |
|---|---|---|
| `profiles_index.py` | Renders `profiles/index.md` as a virtual page | a generated page |
| `profiles_index.py` | Fills the `generated-stats` block and the meta description of 3 tour pages | page text and metadata |
| `explorer.py` | Writes `assets/explorer/data/**.json` (11 MB, 1 index + per-profile + per-session files) | generated files |
| `schemas.py` | Writes `schema/<name>.json` (7 editor schemas) | generated files |

Zensical's native `macros` plugin covers the first two jobs. In the trial build a macro in a
local Python module did both of these:

- It replaced `{{ stats('ae') }}` with a Markdown table row that the page then rendered.
- It set `env.page.meta["description"]`, and `<meta name="description">` carried the value.

The module can live under `scripts/` if `module_name` is a path:
`module_name: scripts/docs_site/macros` worked. A dotted name
(`scripts.docs_site.macros`) failed with `ModuleNotFoundError`.

Macros cannot write files. Zensical creates a new macro environment for every page, so a
once-per-build side effect has no safe place there. The JSON files need a separate step.

No page text contains `{{` or `{%`, so Jinja rendering on every page is safe.

### Other differences that matter

- The default theme variant is `modern`. `theme.variant: classic` keeps the Material look,
  and `docs/assets/layout.css` (`.md-grid`, `.md-typeset`) is written for it.
- The search index moves from `search/search_index.json` to `search.json`. Nothing in the
  repo reads the old path.
- `mkdocs gh-deploy` does not exist. We do not use it: `docs.yml` uploads a Pages artifact.
- `--site-dir` does not exist. Set `site_dir: site` in the config.
- Material's red MkDocs-2.0 notice goes away, so `NO_MKDOCS_2_WARNING` can go.
- Zensical is 0.x software from a small team. Expect breaking changes between minors.

## Decisions

These are my defaults. Say so if you disagree with one.

1. **Move now**, ahead of the May 2027 deadline, in one change set. The trial is clean and
   the deadline gives us room to fix surprises.
2. **Hard cut, no dual-engine period.** A dual period needs `mkdocs-macros-plugin` on the
   MkDocs side too, and doubles CI. Rollback is `git revert`: `uv.lock` history still has
   Material 9.8.0.
3. **Theme variant `classic`.** It keeps today's look and the CSS. Moving to `modern` is a
   separate, visual decision.
4. **Keep `mkdocs.yml`.** Zensical says it will read it for as long as we want. The
   `zensical.toml` move is not needed and the `!!python/name:` fence tag has no obvious TOML
   form. Revisit when Zensical ships a converter.
5. **Generated JSON goes into gitignored paths under `docs/`**, written by a pre-build step,
   so `zensical serve` and `zensical build` both see it.
6. **Pin `zensical>=0.1.1,<0.2`.** A routine `uv lock --upgrade` must not pick the next
   minor without a decision. This copies the cap that `mkdocs-material<10` gave us.

## Design

### Code layout

Replace `scripts/mkdocs_hooks/` with `scripts/docs_site/`:

- `content.py`: the pure rendering code moved from `profiles_index.py`
  (`profile_stats`, `render_index`, `render_stats_block`, `fill_stats_block`,
  `page_description`, `known_as_cell`, and the rest). It imports nothing from MkDocs.
  Its error type is a local `DocsContentError`, not `PluginError`.
- `macros.py`: `define_env(env)`. It registers `bundled_profiles_index()` and
  `profile_stats_rows(name)`, and sets `env.page.meta["description"]` for the dedicated
  pages. It never caches: `zensical serve` rebuilds in one process and must see new data.
- `data.py`: writes the explorer JSON and the schema JSON into
  `docs/assets/explorer/data/` and `docs/schema/`. It deletes both directories first, so a
  removed profile leaves no stale file. The two directories are gitignored.
  `python -m scripts.docs_site.data` is the entry point (a small `__main__` guard).

`scripts/` is not a shipped package and not a layer, so the layering contracts do not apply.
Run `make structure` anyway after the move (the test files and `doc_paths.py` name these
paths).

### Page changes

- `docs/profiles/index.md` becomes a committed page: the title and one line,
  `{{ bundled_profiles_index() }}`. GitHub readers see a macro call on this one page.
  Today there is no committed copy at all, so this is the smaller change.
- In each of the three tour pages, replace the `BEGIN`/`END generated-stats` placeholder
  row with `{{ profile_stats_rows("<profile>") }}`. Drop the table-glue comment hack
  described in `render_stats_block`: the macro output replaces the text before the table is
  parsed, so the comment markers are not needed.
- `macros` settings: `on_error_fail: true` and `on_undefined: strict`. The defaults insert
  an error message into the page and let the build pass. That would hide a broken macro.

### Config changes (`mkdocs.yml`)

- Delete the `hooks:` block.
- Add `site_dir: site`.
- Add `macros` under `plugins:` with `module_name: scripts/docs_site/macros`.
- Add `theme.variant: classic`.
- Add `watch:` entries for `profiles`, `src/canair/interop/explorer` and `scripts/docs_site`.
  A change there triggers a rebuild. The rebuild does not rerun the data step, so
  `make docs-serve` documents that a restart refreshes the JSON.
- Update the comment on the `mermaid` fence: it says "Material loads Mermaid.js". Confirm
  that Zensical behaves the same before keeping the claim.

### Dependencies, Make, CI

- `pyproject.toml`: in the `docs` group, replace `mkdocs-material>=9.5,<10` and its long
  comment with `zensical>=0.1.1,<0.2` and a short comment that points at this plan.
  Run `uv lock` and read the diff.
- `Makefile`: delete the `NO_MKDOCS_2_WARNING` block. `docs` runs the data step, then
  `zensical build --strict`. `docs-serve` runs the data step, then `zensical serve`.
  Update the `help` line.
- `.github/workflows/ci.yml`: change the docs step to the same two commands. Drop the env
  var and its comment.
- `.github/workflows/docs.yml`: same two commands. In `paths:` replace
  `scripts/mkdocs_hooks/**` with `scripts/docs_site/**`. Fix the comments that name the old
  directory. The Pages artifact path is `site`, unchanged.
- `.gitignore`: add `docs/assets/explorer/data/` and `docs/schema/`.

### Tests

Three test modules load the hook file and call `pytest.importorskip("mkdocs")`:

- `tests/scripts/test_profiles_index_hook.py`
- `tests/interop/explorer/test_explorer_export.py` (one test near line 65)
- `tests/profile/test_also_known_as.py` (one test near line 102)

After mkdocs leaves the lock these tests would **skip without a failure**. That hides the
coverage loss. Change them to import `scripts.docs_site.content`, drop the `importorskip`
and swap `PluginError` for `DocsContentError`. Add one test that calls `define_env` with a
fake env and checks that the macros return the expected text and set the description.

## Phases

Each phase is a separate commit on `main`. Commit type is `build(docs)` or `docs`, as
`docs/development/commit-messages.md` describes.

1. **Make the content code engine-free.** Create `scripts/docs_site/content.py` from
   `profiles_index.py`. Leave the three hooks as thin wrappers that call it and raise
   `PluginError` from the new error. Move and fix the tests. MkDocs still builds the site, so
   this commit is green and reviewable alone.
2. **Switch the engine.** One commit, because MkDocs and Zensical cannot share the macro
   pages: add `macros.py` and `data.py`, change the pages, the config, the lock, the
   Makefile and both workflows, delete `scripts/mkdocs_hooks/`.
3. **Clean up the words.** Update every text that names the old setup:
   - `docs/development/index.md` ("An MkDocs hook ..." and the explorer paragraph, plus the
     "Material loads Mermaid.js" bullet if the check above says it no longer holds).
   - The header comment in `docs/assets/explorer/explorer.js`.
   - Docstrings in `src/canair/interop/explorer/__init__.py` and the comment on the schema
     table in `src/canair/core/paths.py` ("mkdocs `site_url`" and the hook path).
   - The "Toolchain" section of `plans/2026-07-24-documentation-strategy.md`: mark its
     "do not migrate" decision as superseded by this plan and correct the hooks row.
   - Run `uv run python scripts/check/doc_paths.py`.
4. **Post-merge check.** Watch the first `docs.yml` run on `main`. Open the live site and
   run the checklist below. Set this plan's status to DONE with the date.

## Verification checklist

Automated (phase 2 must pass all of these before the commit lands):

- `make docs` exits 0 with `--strict`.
- The page list of `site/` equals the list from the last MkDocs build, apart from the known
  differences (search file name, theme asset names, `sitemap.xml.gz`). The trial build
  compared 145 files and found only those.
- `site/schema/*.json` has 7 files. `site/assets/explorer/data/index.json` lists every
  bundled profile.
- `site/profiles/index.html` has one row per bundled profile and each tour page shows the
  same counts as `canair validate pids --stats`.
- The `<meta name="description">` of each tour page contains the "also known as" names.
- `uv run pytest -q`, `make structure`, `uv run ruff check .` and
  `uv run ruff format --check .` pass.

By hand in a browser (`make docs-serve`). I could not run these:

- Pele diagrams on `concepts/architecture` and `bring-your-own-car/overview`, in light and
  dark mode, before and after an instant navigation.
- The profile explorer: bus view, a session plot, and the profile switcher.
- Search: type a known term and a car alias such as "Classic Ioniq".
- The palette toggle, tabs, copy-code buttons and tooltips on abbreviations.
- The wide layout from `layout.css` on a table-heavy page.

## Risks

- **A hook-like setting fails silently.** Zensical ignores unknown plugin settings and
  unsupported plugins without a warning. Mitigation: `on_error_fail: true`, and the CI
  check that the profile index has one row per profile.
- **Stale generated data in `serve`.** The data step runs once at start. Mitigation: the
  Makefile comment, and the `watch:` entries do not claim to regenerate.
- **A 0.x break.** Mitigation: the `<0.2` cap and a committed `uv.lock`.
- **Instant navigation or Pele behaves differently.** Mitigation: the manual checklist.
  If `navigation.instant` breaks the explorer, drop the feature. The site still works.
- **Rollback.** `git revert` the phase 2 commit. `uv lock` then restores Material 9.8.0.
  That version is supported until 2027-05-05.

## Out of scope

- Moving to `zensical.toml`.
- The `modern` theme variant, new navigation, or a redesign.
- Zensical's other native plugins (social cards, `llmstxt`, `rss`, `redirects`).
  Adding `llmstxt` may suit an agent-heavy repo later. That is a separate plan.
- Changing what the explorer or the profile index shows.

## Open questions

- Does Zensical pick up a `docs/` file that the data step writes **after** `serve` started?
  The `watch:` list says only paths it watches trigger a rebuild. Test this in phase 2.
  The answer decides whether `watch:` can include `docs/assets/explorer/data`.
- Does `on_undefined: strict` fail the build on a misspelled macro name? I read the setting
  in the docs but did not test its effect. Test it in phase 2 with a deliberate typo.
