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

Changes to canair itself (the `canlib/` package) are welcome — the dev setup, the
checks to run before a PR, commit-message format, and the screenshot pipeline all
live in [Development](../development/index.md).

## Code of conduct

Be kind and constructive. This is a hobbyist project for people curious about
their cars — assume good faith, and help newcomers. The full text is in
[`CODE_OF_CONDUCT.md`](https://github.com/philipkocanda/canair/blob/main/CODE_OF_CONDUCT.md).
