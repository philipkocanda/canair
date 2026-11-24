# Submitting with canair contribute

The `canair contribute` PR flow: pre-flight steps, modes, and --profile.

## Submit — `canair contribute` (alias `canair share`)

**`canair contribute` opens the pull request for you** — no manual
fork/clone/branch/push, and it works regardless of where the profile is stored
(bundled repo, `~/.config/canair/profiles/`, or a `--path` bundle). Check
`canair contribute --help` for the current flags; the shape of the flow:

1. Runs `canair validate all` and **refuses a broken profile**.
2. Runs the **PII pre-flight** (`src/canair/contribute/pii.py`) — VIN identity DIDs, VIN-shaped
   payloads, the curated `ecus/` `identity.vin`, and email/VIN text in
   labels/notes/`car_model` — and requires you to confirm before continuing.
   Per-unit ECU serials are deliberately *not* flagged, in captures or in
   `identity:`: they name a module, not a person. Neither are phone numbers —
   there is no digit-run check, because it cannot be told apart from a part
   number, a DID range or payload hex. **You** are the backstop for those.
3. Runs the **staleness / self-collision guards** and asks you to confirm past
   each: an *installed-snapshot* warning when the profile was read from a frozen
   `site-packages`/`uv tool` copy (a bare `canair`, not `uv run canair` from a
   checkout), a *rollback* warning when the contribution would remove committed
   upstream lines from curated definitions (which normally only grow — a rollback
   usually means your source is behind, so sync it first), and a *workspace
   self-collision* refusal when you are running from **inside** the managed
   contribution clone canair stages into (its bundled copy of the profile is the
   copy destination, so there is nothing to contribute — re-run from your own
   checkout). Captures are append-only and are overlaid onto upstream, so a
   behind source never proposes deleting upstream sessions, and a capture log
   your contribution adds nothing to is left untouched. A session you edited
   (`--set-*`, a back-fill, `migrate-discover`, `split-batched`) replaces
   upstream's copy of the same recording; each replacement is listed and needs
   confirmation (a stale source looks the same), and unsplit multi-DID replies in
   sessions you add are flagged with the `captures split-batched` remedy.
4. Copies the profile into a managed fork checkout, commits, pushes to your fork,
   and opens the PR via the **GitHub CLI (`gh`)**. When `gh` is missing or
   unauthenticated it prints install + `gh auth login` steps (and the manual
   equivalent) and changes nothing. The managed checkout is rebuilt from upstream
   first, so nothing an earlier run left there is staged; a `--repo-dir` you
   supplied is never reset, but uncommitted changes it holds under
   `profiles/<name>/` are listed for you to confirm, because they would be
   committed too. **Re-running is how you revise a contribution** — the same-day
   branch already has a pull request, and pushing to it reports an *updated PR*
   rather than an error.

Useful modes (verify against `--help`): `--no-captures` (definitions-only PR),
`--diff` (show the diff that would be submitted, then stop),
`--dry-run` (prepare the branch and commit locally; no push or PR),
`--no-logs` (leave out the scrubbed `canair logs` excerpt the PR body carries by default),
`--discard-local` (drop the changes the PR holds from your canair checkout once it is open),
`--yes` (non-interactive, for agents/CI), `--json` (emit the PR URL + findings), `--profile NAME`
to target a specific profile.

**Prefer doing it by hand?** A normal PR putting the bundle under
`profiles/<your-car>/` works just as well — but you still owe the same
validation + PII/security scrub the tool would have run.

> **When submitting a *specific* profile, always pass `--profile NAME`** (and on
> any authoring command that writes into a profile). Without it, writes/PRs
> target whatever `default_profile` resolves to — not necessarily the car you
> mean. This is exactly how signals once landed in the wrong profile.
