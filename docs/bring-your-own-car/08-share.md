# 8. Share

You've got named, verified parameters for your car. Two ways to put them to use:
push them to your WiCAN dongle as an AutoPID profile, and/or contribute the
profile so others with the same vehicle benefit.

## Generate the WiCAN AutoPID JSON

`canair wican autopid write` renders your profile's **verified** parameters into
the JSON format the WiCAN's AutoPID feature consumes, written to the bundle's
`out/autopid.json` (pass `--include-unverified` to also ship in-progress
candidates):

```bash
canair wican autopid write                    # verified-only — ship what you've confirmed
canair wican autopid write --include-unverified  # also include in-progress candidates
```

On a **WiCAN Pro**, you can sync it to the device directly (these are Pro-only):

```bash
canair wican autopid diff      # what would change on the device
canair wican autopid upload    # push the profile to the dongle
```

`out/*.json` is generated — never hand-edit it; regenerate.

## Contribute your profile back

**Please do — this is the single most valuable thing you can do for the
project.** 🎉 Every profile you contribute means the next person with your car
gets a head start instead of starting from zero — that's how canair becomes
useful beyond one vehicle. Contributions are genuinely wanted and warmly
welcomed, whether it's a whole new car, a handful of newly-decoded parameters, or
a fix to an existing one.

### The easy way: `canair contribute`

One command opens the pull request for you — no forking, cloning, or branching by
hand:

```bash
canair contribute            # PR the active profile (definitions + captures)
canair contribute --no-captures   # definitions only (smaller)
canair contribute --diff          # show exactly what would be contributed, then stop
canair contribute --dry-run       # prepare it locally, but don't push/open the PR
```

It copies your profile into a fork of the repo and opens the PR against
`philipkocanda/canair` — and it doesn't matter **where** your profile lives (the
repo, `~/.config/canair/profiles/`, or a `--path` bundle). It prints where it
**reads your profile from** and the **workspace** it stages/pushes from, so
there's no guessing which copy is being shared. Before anything is
shared it:

- runs `canair validate all` (and refuses if it fails),
- scans for anything that could **identify or locate you** — a VIN, an email or
  phone number in a label/note — and asks you to confirm. It looks at both your
  `captures/` and the `identity:` blocks in `ecus/` (where `canair identity`
  records a live VIN read), so a definitions-only `--no-captures` PR is checked
  too. A value you have already masked (`KMHCXXXXXXXXXXXXX`) is recognised as
  redacted and not re-flagged. Per-unit **ECU serials** are deliberately left
  alone wherever they appear — a capture of the serial DID *and* `identity.serial`
  — because they name a module, not you,
- **warns if your profile looks stale** — if it was read from an installed
  snapshot (a bare `canair` reads the frozen `site-packages` copy, not your
  checkout) or if the contribution would *remove* lines already merged upstream
  (curated definitions normally only grow, so a rollback usually means your
  source is behind) — and asks you to confirm before continuing, and
- **asks you to confirm before pushing** and opening the PR (nothing leaves your
  machine until you say yes; `--yes` skips the prompt for scripts/agents).

Captures are append-only evidence, so a contribution only ever *adds* capture
sessions — even a source that's behind upstream on captures won't propose
deleting the sessions it lacks, and a capture log your contribution adds nothing
to is left exactly as it is upstream (so the PR diff stays small and reviewable).

!!! tip "Run it from your own checkout"

    The **workspace** canair prints is a throwaway clone it manages for you
    (under `~/.config/canair/`) — it is a full canair checkout, profiles and all.
    Don't `cd` into it and run canair from there: the profile it would resolve is
    that clone's own copy, which is exactly where this command *writes*. canair
    refuses that case with an explanation, but the fix is always the same — run
    `canair contribute` from your own checkout (or wherever your profile lives).

Not sure what you're about to send? Run `canair contribute --diff` first — it
prepares everything and prints the full diff of `profiles/<your-car>/` vs
upstream, without committing, pushing, or opening a PR.


**One-time setup — the GitHub CLI (`gh`):**

```bash
# install gh
brew install gh                      # macOS (Homebrew)
winget install --id GitHub.cli       # Windows
#   Linux / other: https://github.com/cli/cli#installation

# sign in (opens your browser)
gh auth login
```

`canair contribute` prints these same instructions if `gh` isn't installed or
you're not signed in.

### The manual way (or if you prefer git yourself)

A standard GitHub pull request works too:

1. Fork [`philipkocanda/canair`](https://github.com/philipkocanda/canair) and
   put your profile under `profiles/<your-car>/` (see
   [step 1](01-create-profile.md) — create it there with `--path`).
2. Make sure it's clean and honest:
   - **Validate:** `canair validate all` must pass (including the
     duplicate-signal-name check).
   - **Verify what you claim.** Prefer `--verified` parameters with a `--source`
     recording your evidence; an unverified guess should say so.
   - **Name the vehicle precisely.** `car_model` should pin down
     model/year/market/battery so someone can tell if it matches theirs.
   - **Decide on `captures/`.** They're great evidence but large — include a
     representative subset rather than everything if size is a concern.
   - **Scrub for privacy.** No VINs, addresses, or other identifying/location
     data — the tree is public. Redact a VIN rather than deleting the reading
     (`KMHC` + `X`s keeps the make/model prefix, which is useful and not
     identifying). Per-unit ECU serials are fine: they name a module, not a person.
3. Open a PR. Even a *partial* profile is welcome — a few verified signals beats
   nothing, and others can build on it.

Not ready for a full profile? **Individual PID/parameter contributions are just
as valuable** — a single verified signal, a corrected offset, or a new
`research:` lead all help. And if you find a bug or a rough edge in canair
itself, an issue or PR is appreciated too.

## Keep going

A profile is never really "done" — there are always more unmapped bytes. Use
`canair research --summary` to see your open backlog, `canair coverage` to find
undecoded bytes, and loop back through [capture](05-capture.md) →
[analyze](06-analyze.md) → [define](07-define-and-verify.md) whenever you want to
decode more.

---

← Back to the **[overview](overview.md)**.
