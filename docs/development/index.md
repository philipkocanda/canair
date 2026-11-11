# Development

This section is for people who change **canair itself**: the `src/canair/` package (the
command-line interface (CLI), transports, modes, library code and tests). To contribute a *vehicle
profile* or decoded signals, you need none of this. See [Contributing](../contributing/index.md).
That is the most wanted contribution.

The engineering guidelines live in the agent skill at `.claude/skills/contributing-code/SKILL.md`.
They cover the transport contract, how to add a subcommand, test expectations and the "keep docs &
README current" policy. The skill also serves as the guide for human contributors. The mechanics of
profile contribution are in `.claude/skills/contributing-profiles/SKILL.md`. They cover
`canair contribute` and the scrub for personally identifiable information (PII) and security keys.

## Run the working tree, not an installed copy

Always run and test with `uv run canair …` from the repo root. Never use a globally installed bare
`canair`. `uv run canair` runs your working-tree code and reads the bundled `profiles/` of the repo
live. A bare `canair` runs a frozen snapshot from install time. For the reason, see
[During development](../concepts/profiles.md#during-development-which-canair-sees-your-edits).

## Enable the git hooks (once per clone)

The hooks run the fast continuous integration (CI) gates automatically:

- On each commit, they run `ruff format`, `ruff check`, `ty`, the import contracts, the structure
  tests and the skill-frontmatter validation.
- On each commit message, they run the [Conventional Commits](commit-messages.md) check.
- On each push, they run the checks that generated artifacts are current.

```bash
make install                                 # Git LFS + hooks + uv sync + `canair` tool (alias: setup)
# or, without make:
git lfs install --force                      # first: see the note below
uv run pre-commit install --install-hooks
```

`make install` first runs `scripts/dev/check_toolchain.sh`. The script stops when uv is older than
0.13 or would use a pre-release Python. The clone pins Python 3.15, and an older uv resolves that
pin to an old alpha. Without make, run `uv self update`. Then check that `uv python list 3.15`
offers a release, not an alpha.

A clean local run means that CI passes. CI remains the final gate.

!!! warning "Run `git lfs install --force` before `pre-commit install`"
    Git Large File Storage (LFS) stores large profile files, such as raw Controller Area Network
    (CAN) logs under `captures/can/`. The `pre-push` hook of git-lfs uploads them. If you
    install that hook first, `pre-commit install` chains it. Otherwise `pre-commit install`
    replaces it. Without the upload, `git push` succeeds but publishes pointers whose files never
    reach the server. The next `git pull` on another machine then fails with
    `Object does not exist on the server`. The `--force` flag lets a re-run succeed once the hook
    of pre-commit is in place.

    Two more guards exist. The `lfs-push` pre-push hook uploads the objects itself. CI runs
    `scripts/check/check_lfs_objects.py` (`make lfs-check` locally). It flags any pointer whose
    object is missing from the server.

## Checks before you open a code PR

Run these from the repo root:

```bash
uv run pytest -q                                    # tests (parallel; ~14s)
uv run ruff check . && uv run ruff format --check .  # lint + format
uv run ty check                                     # type check (src/canair/)
uv run lint-imports                                 # layer and boundary contracts
uv run canair validate all                          # if you touched profile data
uv run python scripts/check/validate_skills.py             # if you touched a skill's frontmatter
uv run python scripts/check/doc_paths.py                   # if you moved or renamed code a document names
uv run python scripts/gen/gen_cli_reference.py --check   # if you changed a command's flags
uv run python scripts/gen/gen_screenshots.py --check     # if you changed screenshotted command output
```

You do not need to regenerate the counts of the [bundled profiles](../profiles/index.md). The
docs build (`scripts/docs_site/content.py`) computes them from `profiles/*/ecus/` each time it
builds the site.

The [profile explorer](../profiles/explorer.md) works the same way. The build exports its data
(`src/canair/interop/explorer/export.py`, written out by `scripts/docs_site/data.py`).
`docs/assets/explorer/explorer.{js,css}` only render that data. To change what the explorer shows,
change the exporter. The exporter has unit tests. Check the JavaScript by hand in a browser.

The suite runs in parallel by default (`-n auto --dist loadscope`, set in `pyproject.toml`). To
iterate on a single file or debug one failure, pass `-n0`. Starting workers costs more than a small
selection saves. Serial output also stays ordered instead of interleaved:

```bash
uv run pytest -n0 tests/safety/test_safety.py -k some_case
```

If you have no car and no dongle, you can still test the full transport stack. See
[Offline testing with ELM327-Emulator](offline-testing.md).

If you changed `import dbc`, `export dbc` or the `signals/` model, run the opt-in real-world
round-trip of the CAN database (DBC) format. It fetches
[commaai/opendbc](https://github.com/commaai/opendbc) into the gitignored `references/can/` and
expects `problems: 0`:

```bash
uv run python scripts/check/check_dbc_corpus.py --fetch
```

## Diagrams and page layout

Write a diagram as a fenced `mermaid` block. GitHub draws the blocks in the README with
Mermaid. The docs site draws them with [Pele](https://pele.run), a small renderer from Obsidian.
Pele reads Mermaid syntax but lays the diagram out in its own way, so check each diagram in the
built site (`make docs-serve`).

- The configuration is in `mkdocs.yml` (the `mermaid` fence). The class is `pele-diagram`, not
  `mermaid`, because Material loads Mermaid.js for every `.mermaid` element.
- `docs/assets/pele/pele-init.js` loads the bundle only on pages that have a diagram. It sets
  `directionBreakpoint` so that a wide `flowchart LR` becomes top to bottom in the page column.
  To keep a diagram horizontal, make the comment `%% pele: horizontal` its first line. Keep such
  a diagram narrow, about 850 px or less, so that its labels stay readable.
- `docs/assets/pele/pele.css` maps the Pele colors and fonts to the Material theme. A light or
  dark switch recolors the diagrams without a new render.
- `docs/assets/pele/vendor/` holds the pinned bundle (Pele 1.13.0, MIT license). To update it,
  download `dist/pele.global.js` from the `pele` package on npm, and replace the file.
- `docs/assets/layout.css` widens the page for tables, diagrams and code. Paragraphs, lists and
  headings keep a line length of about 43 rem, which is easier to read.

## Keep the docs current

If your change adds, removes or alters a user-facing capability, update the docs and the README in
the same pull request (PR). See the README ↔ `docs/` policy in `AGENTS.md`. The docs screenshots
are **generated**, not hand-made. See [Documentation screenshots](screenshots.md).

## Also in this section

- [Architecture](architecture.md): the layers, where new code goes, and the checks that keep the
  structure.
- Designs that span several packages, each with the rules that a change must keep:
    - [response frame counts](frame-counts.md)
    - [link health](link-health.md) (fallback, reconnect, desync recovery)
    - [variants and ECU revisions](variants-and-revisions.md) (an ECU is an electronic control
      unit)
    - [layered profiles and write targets](layered-profiles.md)
    - [the contribute pipeline](contribute-pipeline.md)
- [Commit messages](commit-messages.md): the Conventional Commits format that drives the version
  bump and the changelog.
- [Documentation screenshots](screenshots.md): how canair generates, checks and regenerates the
  SVG and GIF assets of the docs.
- [Offline testing (emulator)](offline-testing.md): drive canair with no dongle and no car.
