# 8. Share

You've got named, verified parameters for your car. Two ways to put them to use:
push them to your WiCAN dongle as an AutoPID profile, and/or contribute the
profile so others with the same vehicle benefit.

## Generate the WiCAN AutoPID JSON

`canair wican autopid write` renders your profile's **verified** parameters into
the JSON format the WiCAN's AutoPID feature consumes, written to the bundle's
`out/autopid.json` — `out/<variant>/autopid.json` when the profile describes several
[variants](../concepts/variants.md), one device profile per car (pass
`--include-unverified` to also ship in-progress candidates):

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

### Making the device's reads faster

By default the WiCAN's ELM327 co-processor waits out its full ~614 ms ECU budget on
every PID, to be sure no further response frame is coming. If canair has recorded
how many frames a PID answers with — it learns this automatically while you read
the car, and stores it as `response_frames:` in `ecus/` — canair appends that count
to each request by default, so the adapter returns the moment the reply is whole:

```bash
canair wican autopid stats                           # which PIDs have a count (Frames column)
canair wican autopid write                           # counts are emitted into the profile
canair wican autopid write --no-expected-responses   # leave them out
```

This is only safe because canair is strict about which counts it emits: the firmware
cannot recover from a count that is too low, since it would hand back a truncated
response and leave the remainder queued, where it shows up as the *next* PID's
answer. canair therefore only emits a count that the wire confirmed, and never one
for a variable-length response, a request that cannot carry the digit, or a response
longer than 9 frames. PIDs without a confirmed count are simply left as they were,
so this is fine on a partly-decoded profile — read the car some more and re-run to
widen coverage.

### Streaming a signal at its poll rate

The WiCAN publishes AutoPID results to MQTT as one batch snapshot (the latest
value of every signal) on the destination's own timer, which is 10 s unless you
changed it in the device's AutoPID settings. Polling a PID faster does **not**
make the broker see it more often. When you need every sample of one signal (to
average power draw, say), give that signal its own topic with `send_to`:

```bash
canair pids upsert-param BMS 2101 BATTERY_POWER "<expression>" \
    --send-to car/stream/battery_power
canair wican autopid diff      # ~ BATTERY_POWER: send_to (none) → car/stream/battery_power
canair wican autopid upload
```

The device then publishes `{"BATTERY_POWER": <value>, "timestamp": <unix seconds>}`
to that topic **after every successful poll**, at the PID's `period`, alongside the
normal batch. The message is retained, so the broker keeps the last sample. Pass
`--send-to ""` to stop streaming a signal.

- A topic must be unique across shipped signals and must not contain `#` or `+`
  or start or end with `/`. `canair validate pids` checks this.
- Pick a topic that whatever consumes the batch topic does not also subscribe to,
  or the stream gets processed a second time as batch data.
- The payload `timestamp` has whole-second resolution, so stamp samples on
  receipt if you need sub-second spacing.
- The batch destination itself (its topic and `cycle`) is device configuration
  that canair does not manage.

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
`philipkocanda/canair`, wherever your profile lives (the repo, `~/.config/canair/profiles/`, or
a `--path` bundle). It prints where it **reads your profile from** and the **workspace** it
pushes from, so you know which copy is being shared. Before anything is shared it:

- runs `canair validate all` (and refuses if it fails);
- scans for anything that could **identify you** — a VIN, or an email address in a label or
  note — and asks you to confirm. It checks `captures/` and the `identity:` blocks in `ecus/`
  (where `canair identity` records a live VIN), so a `--no-captures` PR is checked too. A
  masked VIN (`KMHCXXXXXXXXXXXXX`) isn't re-flagged, and per-unit **ECU serials** are left alone
  because they name a module, not you;
- **warns if your profile looks stale** — it was read from an installed snapshot (a bare
  `canair` reads the frozen `site-packages` copy, not your checkout), or the contribution would
  *remove* lines already merged upstream — and asks you to confirm;
- **lists every upstream capture session it would replace** with your edited copy (see below),
  and **warns about multi-DID replies stored whole under one DID**, which you can repair first
  with `canair captures split-batched`;
- **asks before pushing** and opening the PR. Nothing leaves your machine until you say yes
  (`--yes` skips the prompt for scripts).

Captures are append-only evidence, so a contribution only *adds* capture sessions. A source
that's behind upstream won't propose deleting the sessions it lacks, and a capture log you add
nothing to stays exactly as upstream, keeping the PR diff small.

The one exception is a session you **edited** after recording (`--set-notes`, `--set-transport`,
`--set-state`, a state back-fill, `migrate-discover`, `split-batched`): it's still the same
recording, so your copy *replaces* upstream's in place. A stale source looks the same (upstream
edited the session after you pulled), so `--diff` and the confirmation list each replacement
with the fields that differ. If you didn't edit it, sync first.

Re-running is the normal way to revise a contribution: the default branch name is
per-day, so a second run on the same day pushes to the branch your pull request
already tracks, and canair reports the **updated PR** rather than failing to open
a second one for it.

!!! tip "Run it from your own checkout"

    The **workspace** canair prints is a throwaway clone under `~/.config/canair/`. Don't `cd`
    into it and run canair from there: the profile it would resolve is the clone's own copy,
    which is exactly where this command *writes*. canair refuses that case; the fix is to run
    `canair contribute` from your own checkout (or wherever your profile lives).

    The clone is rebuilt from upstream on every run, and `--diff` cleans up its preview, so
    nothing staged earlier can ride along in a later PR. When an interrupted or refused run left
    a contribution staged there, canair lists the files and asks before discarding them, after
    copying them to `~/.config/canair/contribute/backups/<timestamp>/` (`--yes` discards without
    asking, still backing up). Your own checkout is never reset: if you point `--repo-dir` at one
    with uncommitted changes under `profiles/<your-car>/`, canair lists them and asks first,
    since everything in that directory is committed.

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
