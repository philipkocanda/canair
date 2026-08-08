# Development

This section is for people changing **canair itself** (the `canlib/` package —
CLI, transports, modes, library code, tests). Contributing a *vehicle profile* or
decoded signals needs none of this — that's [Contributing](../contributing/index.md),
and it's the most wanted contribution.

The engineering guidelines — the transport contract, how to add a subcommand,
test expectations, and the "keep docs & README current" policy — live in the agent
skill at `.claude/skills/contributing-code/SKILL.md`, which doubles as the human
contributor guide. (The profile-contribution mechanics — `canair contribute`, the
PII/security scrub — are in `.claude/skills/contributing-profiles/SKILL.md`.)

## Run the working tree, not an installed copy

Always run and test with `uv run canair …` from the repo root (never a
globally-installed bare `canair`) — that runs your working-tree code and reads the
repo's bundled `profiles/` live, whereas a bare `canair` runs a frozen
install-time snapshot. See
[During development](../concepts/profiles.md#during-development-which-canair-sees-your-edits)
for why.

## Enable the git hooks (once per clone)

The hooks run the fast CI gates automatically — `ruff format`/`ruff check`/`ty`/skill-frontmatter
validation on each commit, the [Conventional Commits](commit-messages.md) check on each commit
message, and the generated-artifact currency checks on each push:

```bash
uv run pre-commit install --install-hooks   # installs all three hook stages
```

A clean local run means a green CI; CI stays the hard gate.

## Checks before you open a code PR

Run from the repo root:

```bash
uv run pytest -q                                    # tests (parallel; ~14s)
uv run ruff check . && uv run ruff format --check .  # lint + format
uv run ty check                                     # type check (canlib/)
uv run canair validate all                          # if you touched profile data
uv run python scripts/validate_skills.py             # if you touched a skill's frontmatter
uv run python scripts/gen_cli_reference.py --check   # if you changed a command's flags
uv run python scripts/gen_profiles_index.py --check  # if a profile's headline counts changed
uv run python scripts/gen_screenshots.py --check     # if you changed screenshotted command output
```

The suite runs in parallel by default (`-n auto --dist loadscope`, set in
`pyproject.toml`). When iterating on a single file or debugging one failure, pass
`-n0` — spinning up workers costs more than a small selection saves, and serial
output stays ordered instead of interleaved:

```bash
uv run pytest -n0 tests/test_foo.py -k some_case
```

No car and no dongle? You can still exercise the full transport stack — see
[Offline testing with ELM327-Emulator](offline-testing.md).

## Keep the docs current

If your change adds, removes, or alters a user-facing capability, update the docs
and README in the same PR (see the README ↔ `docs/` policy in `AGENTS.md`). Docs
screenshots are **generated**, not hand-made — see
[Documentation screenshots](screenshots.md).

## Also in this section

- [Commit messages](commit-messages.md) — the Conventional Commits format that
  drives the version bump and the changelog.
- [Documentation screenshots](screenshots.md) — how the docs' SVG/GIF assets are
  generated, checked, and regenerated.
- [Offline testing (emulator)](offline-testing.md) — drive canair with no dongle
  and no car.
