# Contributing to canair

Thank you for helping. canair becomes more useful with every car and signal that people
share. **Contributions are wanted.**

## Contribute a profile or PIDs (most wanted)

This is the highest-value contribution. It needs no code changes. Reverse engineer your car,
then share the result.

1. Build a profile with the
   [Bring your own car](https://philipkocanda.github.io/canair/bring-your-own-car/overview/)
   walkthrough, or decode more of an existing profile.
2. Run `canair validate all`. It must pass.
3. Run `canair contribute`. It opens the pull request for you. Install the
   [GitHub CLI](https://github.com/cli/cli#installation) (`gh`) and run `gh auth login` once.

A partial contribution is welcome. A few verified signals, a corrected offset or a new `research:`
lead all help. The full steps are in
[Contributing](https://philipkocanda.github.io/canair/contributing/).

## Report a bug or request a feature

Open a [GitHub issue](https://github.com/philipkocanda/canair/issues) and pick
the matching form (bug, feature, or profile) — it prompts for the details that
help most: the command you ran, what you expected, and what happened.

## Contribute code

Changes to the `src/canair/` package are welcome. The full dev guide — setup, hooks,
the pre-PR checks, commit messages, and the screenshot pipeline — is
[docs/development](https://philipkocanda.github.io/canair/development/).
Engineering guidelines (transport contract, adding subcommands, testing,
docs-upkeep policy) are in `.claude/skills/contributing-code/SKILL.md` (the
profile-contribution guidance — `canair contribute`, PII/security scrubbing —
lives in `.claude/skills/contributing-profiles/SKILL.md`). Before opening a code
PR, run from the repo root:

```bash
make install                                        # once per clone: deps + hooks + CLI (uv 0.13+)
uv run pytest -q                                    # parallel by default; -n0 for one file
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair validate all                          # if you touched profile data
uv run python scripts/gen/gen_cli_reference.py --check   # if you changed a command's flags
```

The `pre-commit` hooks mirror the CI gates (ruff format/check + `ty` on commit,
Conventional Commits on the commit message, the generated-artifact currency
checks on push), so a clean local run means a green CI.

### Commit messages

canair uses [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): summary`. Releases are built from these subjects, and the `commit-msg` hook
enforces the format. See
[docs/development/commit-messages.md](docs/development/commit-messages.md) for the types and the
version bump rules.

Update the docs and README in the same PR for any user-facing change (see the
README ↔ `docs/` policy in `AGENTS.md`).

## License

By contributing, you agree your contributions are licensed like the rest of the
project: code under the [MIT License](LICENSE), and anything under `profiles/`
under [CC BY 4.0](profiles/LICENSE). Both let anyone reuse your work as long as
they credit it. There is no separate contributor licensing agreement (CLA) or
sign-off; submitting a pull request is your agreement.

Only contribute material you have the right to license this way — in
particular, do not copy in third-party data under an incompatible licence (see
`profiles/hyundai-kona-os-ev/profile.yaml` for how a CC BY-SA source is cited
rather than copied).

## Code of conduct

Be kind, assume good faith, and help newcomers — see
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
