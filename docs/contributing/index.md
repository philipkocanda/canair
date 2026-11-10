# Contributing

canair needs contributions. **Vehicle profiles and decoded signals** are the most wanted. Every
profile or PID that you share lets the next owner of the same car start ahead, not from zero. This
page is the detailed version. The
[`CONTRIBUTING.md`](https://github.com/philipkocanda/canair/blob/main/CONTRIBUTING.md) file in the
repository root has the same information in brief.

## Contribute a profile or PIDs (most wanted)

This contribution has the highest value. It needs no change to the code of canair.

1. **Do the work.** Follow the [Bring your own car](../bring-your-own-car/overview.md) journey to
   build a profile for your car. You can also decode more of an existing profile.
2. **Make the profile clean and honest:**

    - `canair validate all` must pass (schema and duplicate-signal-name checks).
    - Prefer `--verified` signals with a `--source` that records your evidence. Mark real guesses
      `--unverified`.
    - Let `car_model` identify the model, year, market and battery. Then others can see if it
      matches their car.
    - Include a representative subset of `captures/` as evidence. Captures can be large, so you
      do not have to ship all of them.

3. **Share it with `canair contribute`.** One command opens the pull request for you, wherever
   your profile is stored. The command does these steps:

    - It validates the profile.
    - It scans your captures *and* the `ecus/` identity blocks for a VIN or other personal data.
    - It opens the pull request through the [GitHub CLI](https://github.com/cli/cli#installation).

    Install `gh` and run `gh auth login` once. If `gh` is missing, `canair contribute` prints these
    steps. Use `--no-captures` for a smaller pull request that has definitions only. Use `--dry-run`
    to preview. If you prefer git, a standard pull request that puts your profile under
    `profiles/<your-car>/` works as well.

**A partial contribution is welcome.** A few verified signals, a corrected byte offset, or a new
`research:` lead all help. You do not need a finished profile.

See [Share your profile](../bring-your-own-car/08-share.md) for the full detail.

## Report a bug or request a feature

Open a [GitHub issue](https://github.com/philipkocanda/canair/issues) and pick the matching form:
bug, feature or profile. The form asks what you ran, what you expected, and what happened. A
`--json` dump or the exact command helps. You can also report rough edges in the CLI or the docs.

## Contribute code

Changes to canair itself (the `src/canair/` package) are welcome.
[Development](../development/index.md) describes the development setup, the checks to run before
a pull request, the commit-message format, and the screenshot pipeline.

## Code of conduct

Be kind and constructive. This is a hobby project for people who are curious about their cars.
Assume good faith, and help newcomers. The full text is in
[`CODE_OF_CONDUCT.md`](https://github.com/philipkocanda/canair/blob/main/CODE_OF_CONDUCT.md).
