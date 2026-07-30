# Contributing to canair

Thanks for helping! canair gets more useful with every car and signal people
share — **contributions are genuinely wanted.**

## Contribute a profile or PIDs (most wanted 🎉)

The highest-value contribution, and it needs no code changes: reverse-engineer
your car and share the result.

1. Build a profile with the [Bring your own car](https://philipkocanda.github.io/canair/bring-your-own-car/overview/)
   walkthrough (or decode more of an existing one).
2. Make it clean: `canair validate all` must pass; prefer `--verified` params
   with a `--source`; set `car_model` precisely; include a representative subset
   of `captures/`.
3. Share it with **`canair contribute`** — one command that opens the pull
   request for you (no manual fork/clone/branch), wherever your profile is
   stored. It validates and scans for VIN/PII first, then opens the PR via the
   [GitHub CLI](https://github.com/cli/cli#installation) (`gh` — install it and
   run `gh auth login` once). Prefer doing it by hand? A normal PR putting your
   profile under `profiles/<your-car>/` works too.

**Partial is welcome** — a few verified signals, a corrected offset, or a new
`research:` lead all help. See
[docs/contributing](https://github.com/philipkocanda/canair/blob/main/docs/contributing/index.md)
and [Share your profile](https://github.com/philipkocanda/canair/blob/main/docs/bring-your-own-car/08-share.md).

## Report a bug or request a feature

Open a [GitHub issue](https://github.com/philipkocanda/canair/issues) and pick
the matching form (bug, feature, or profile) — it prompts for the details that
help most: the command you ran, what you expected, and what happened.

## Contribute code

Changes to the `canlib/` package are welcome. Engineering guidelines (transport
contract, adding subcommands, testing, docs-upkeep policy) are in
`.claude/skills/contributing-code/SKILL.md` (the profile-contribution guidance —
`canair contribute`, PII/security scrubbing — lives in
`.claude/skills/contributing-profiles/SKILL.md`). Before opening a code PR, run
from the repo root:

```bash
uv run pre-commit install --install-hooks           # once per clone: enable git hooks
uv run pre-commit install --hook-type pre-push
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair validate all                          # if you touched profile data
uv run python scripts/gen_cli_reference.py --check   # if you changed a command's flags
```

The `pre-commit` hooks mirror the CI gates (ruff format/check + `ty` on commit,
the generated-artifact currency checks on push), so a clean local run means a
green CI.

Update the docs and README in the same PR for any user-facing change (see the
README ↔ `docs/` policy in `AGENTS.md`).

## License

By contributing, you agree your contributions are released into the public
domain under [The Unlicense](LICENSE), like the rest of the project. This is a
deliberate choice — there is no separate contributor licensing agreement (CLA)
or sign-off; contributing is dedicating your work to the public domain.

## Code of conduct

Be kind, assume good faith, and help newcomers — see
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
