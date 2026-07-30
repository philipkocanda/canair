<!--
Thanks for contributing! Delete the section that doesn't apply and fill in the
other. Most PRs only need one.
-->

## What & why

<!-- One or two sentences: what does this change and why? Link any issue. -->

---

### Profile / PID contribution

- [ ] `uv run canair validate all` passes for the profile
- [ ] Verified params have a `--source`; genuine guesses are marked `--unverified`
- [ ] `car_model` pins down model / year / market / battery
- [ ] A representative subset of `captures/` is included as evidence
- [ ] No VIN / ECU serials / personal data (the `canair contribute` PII scan covers this — confirm for manual PRs)

### Code contribution

Run from the repo root:

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run ty check`
- [ ] `uv run canair validate all` (if you touched profile data)
- [ ] `uv run python scripts/gen_cli_reference.py --check` (if you changed a command's flags)
- [ ] Docs and README updated for any user-facing change (README ↔ `docs/` policy in `AGENTS.md`)

---

By submitting this pull request I agree my contribution is released into the
public domain under [The Unlicense](../blob/main/LICENSE).
