# Contributing

canair thrives on contributions — **especially vehicle profiles and decoded
signals.** Every profile or PID you share means the next person with the same car
starts ahead instead of from zero. This page is the friendly version; the
repo-root [`CONTRIBUTING.md`](https://github.com/philipkocanda/canair/blob/main/CONTRIBUTING.md)
is the same information in brief.

## Contribute a profile or PIDs (most wanted)

This is the highest-value contribution and needs no changes to canair's code.

1. **Do the work** — follow the [Bring your own car](../bring-your-own-car/overview.md)
   journey to build a profile for your car (or decode more of an existing one).
2. **Make it clean and honest:**
   - `canair validate all` must pass (schema + duplicate-signal-name checks).
   - Prefer `--verified` parameters with a `--source` recording your evidence;
     mark genuine guesses `--unverified`.
   - `car_model` should pin down model/year/market/battery so others can tell if
     it matches theirs.
   - Include a representative subset of `captures/` as evidence (they can be
     large — you don't have to ship everything).
3. **Share it with `canair contribute`** — one command opens the pull request
   for you (no manual fork/clone/branch), no matter where your profile is stored.
   It runs `canair validate all`, scans for VIN/serial/PII, then opens the PR via
   the [GitHub CLI](https://github.com/cli/cli#installation) (`gh` — install it
   and run `gh auth login` once; `canair contribute` prints these steps if it's
   missing). Use `--no-captures` for a smaller, definitions-only PR, or
   `--dry-run` to preview. Prefer git yourself? A standard PR putting your
   profile under `profiles/<your-car>/` works just as well.

**Partial is welcome.** A handful of verified signals, a corrected byte offset,
or a new `research:` lead all help — you don't need a "finished" profile.

See [Share your profile](../bring-your-own-car/08-share.md) for the full detail.

## Report a bug or request a feature

Open a [GitHub issue](https://github.com/philipkocanda/canair/issues) and pick
the matching form (bug, feature, or profile). It prompts for what you ran, what
you expected, and what happened (a `--json` dump or the exact command helps).
Rough edges in the CLI or docs are fair game too.

## Contribute code

Changes to canair itself (the `canlib/` package) are welcome. The engineering
guidelines — the transport contract, how to add a subcommand, testing, and the
"keep docs & README current" policy — live in the agent skill at
`.claude/skills/contributing-code/SKILL.md`, which doubles as the human
contributor guide. (The profile-contribution mechanics — `canair contribute`,
the PII/security scrub — are in `.claude/skills/contributing-profiles/SKILL.md`.)

Always run and test with `uv run canair …` from the repo root (never a
globally-installed bare `canair`) — that runs your working-tree code and reads
the repo's bundled `profiles/` live, whereas a bare `canair` runs a frozen
install-time snapshot. See
[During development](../concepts/profiles.md#during-development-which-canair-sees-your-edits)
for why.

**Enable the git hooks once per clone** so the fast CI gates run automatically —
`ruff format`/`ruff check`/`ty` on each commit, and the generated-artifact
currency checks on each push:

```bash
uv run pre-commit install --install-hooks   # commit-stage hooks
uv run pre-commit install --hook-type pre-push
```

Quick check before you open a code PR (run from the repo root):

```bash
uv run pytest -q                                    # tests (parallel; ~14s)
uv run ruff check . && uv run ruff format --check .  # lint + format
uv run ty check                                     # type check (canlib/)
uv run canair validate all                          # if you touched profile data
uv run python scripts/gen_cli_reference.py --check   # if you changed a command's flags
uv run python scripts/gen_screenshots.py --check     # if you changed screenshotted command output
```

The suite runs in parallel by default (`-n auto --dist loadscope`, set in
`pyproject.toml`). When iterating on a single file or debugging one failure, pass
`-n0` — spinning up workers costs more than a small selection saves, and serial
output stays ordered instead of interleaved:

```bash
uv run pytest -n0 tests/test_foo.py -k some_case
```

If your change adds, removes, or alters a user-facing capability, update the
docs and README in the same PR (see the README ↔ `docs/` policy in `AGENTS.md`).

## Documentation screenshots

The docs embed SVG screenshots and animated GIFs of the CLI in action, all
**generated** from the manifest at `docs/screenshots/shots.yaml` — you never
craft or maintain them by hand. Static command output is rendered with
[`freeze`](https://github.com/charmbracelet/freeze) (SVG); interactive TUI and
montage clips with [`vhs`](https://github.com/charmbracelet/vhs) (GIF). Every
asset is captured against the bundled, read-only `ioniq-2017` profile with **no
device attached**, so it's reproducible on any machine and contains no
owner-specific data.

```bash
brew install charmbracelet/tap/freeze vhs   # one-time: the render tools
make screenshots                             # regenerate everything
make screenshots-only ONLY="bus decode-plot" # regenerate a subset
make screenshots-check                        # verify assets present + commands still run
```

`--check` (run by CI and the pre-push hook) is deliberately light: it needs
neither `freeze` nor `vhs`, and never byte-compares images (they aren't
reproducible). It verifies every manifest asset exists, flags orphans, and runs
each screenshotted command device-free — so a renamed command or dropped flag
fails the check and tells you to regenerate. **When you change the output of a
screenshotted command, re-render and commit the updated asset.** To add a shot,
append an entry to `shots.yaml` (a `rich` command or an `anim` tape) and
regenerate. Do **not** screenshot views that surface free-text capture
notes/labels (e.g. `captures --sessions`) — those can leak PII into public docs.

A few `anim` assets are marked **`live: true`** — recordings of the `monitor` TUI
polling a *real* car. These are non-reproducible, so the default `make
screenshots` skips them and `--check` only verifies the file is present. Re-record
one manually when a vehicle is reachable:

```bash
python3 scripts/gen_screenshots.py --only monitor-bms   # needs a live car + a configured device
```

## Code of conduct

Be kind and constructive. This is a hobbyist project for people curious about
their cars — assume good faith, and help newcomers. The full text is in
[`CODE_OF_CONDUCT.md`](https://github.com/philipkocanda/canair/blob/main/CODE_OF_CONDUCT.md).
