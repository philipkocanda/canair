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
2. **Put it in the repo** — create the profile under `profiles/<your-car>/` (use
   `canair profile create <name> --path profiles/<name>`) so it's tracked in git.
3. **Make it clean and honest:**
   - `canair validate all` must pass (schema + duplicate-signal-name checks).
   - Prefer `--verified` parameters with a `--source` recording your evidence;
     mark genuine guesses `--unverified`.
   - `car_model` should pin down model/year/market/battery so others can tell if
     it matches theirs.
   - Include a representative subset of `captures/` as evidence (they can be
     large — you don't have to ship everything).
4. **Open a pull request** against
   [`philipkocanda/canair`](https://github.com/philipkocanda/canair).

**Partial is welcome.** A handful of verified signals, a corrected byte offset,
or a new `research:` lead all help — you don't need a "finished" profile.

See [Share your profile](../bring-your-own-car/08-share.md) for the full detail.

## Report a bug or request a feature

Open a [GitHub issue](https://github.com/philipkocanda/canair/issues). Include
what you ran, what you expected, and what happened (a `--json` dump or the exact
command helps). Rough edges in the CLI or docs are fair game too.

## Contribute code

Changes to canair itself (the `canlib/` package) are welcome. The engineering
guidelines — the transport contract, how to add a subcommand, testing, and the
"keep docs & README current" policy — live in the agent skill at
`.claude/skills/contributing/SKILL.md`, which doubles as the human contributor
guide.

Quick check before you open a code PR (run from the repo root):

```bash
uv run pytest -q                                    # tests
uv run ruff check . && uv run ruff format --check .  # lint + format
uv run ty check                                     # type check (canlib/)
uv run canair validate all                          # if you touched profile data
uv run python scripts/gen_cli_reference.py --check   # if you changed a command's flags
```

If your change adds, removes, or alters a user-facing capability, update the
docs and README in the same PR (see the README ↔ `docs/` policy in `AGENTS.md`).

## Code of conduct

Be kind and constructive. This is a hobbyist project for people curious about
their cars — assume good faith, and help newcomers.
