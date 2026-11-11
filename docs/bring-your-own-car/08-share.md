# 8. Share

You now have named, verified signals for your car. You can use them in two ways. You can push them
to your WiCAN dongle as an AutoPID profile. You can contribute the profile, so that others with the
same vehicle benefit. You can also do both.

## Generate the WiCAN AutoPID JSON

`canair wican autopid write` renders the **verified** signals of your profile into the JSON that
the AutoPID feature of the WiCAN reads. It writes one file per firmware generation to the `out/`
directory of the bundle. When the profile describes several [variants](../concepts/variants.md),
it writes to `out/<variant>/`, with one device profile per car:

| File | For | What it is |
|---|---|---|
| `autopid-v4.json` | WiCAN firmware 4.x | the vehicle profile, in the upstream source format (signals grouped per PID) |
| `autopid-v6.json` | WiCAN firmware v6, from `6.0.0-beta.3` | the AutoPID table that v6 takes, plus the `vehicle` and `specific_init` device settings of the car |

Pass `--include-unverified` to also ship in-progress candidates. canair then writes
`autopid-v4-unverified.json` and `autopid-v6-unverified.json` instead, so the verified-only files
stay as they are. `--firmware 4` or `--firmware 6` writes only one of the two files. `--out PATH`
needs `--firmware`.

The bundled profiles carry both builds. They are regenerated on every release. canair leaves out
the unverified v6 table of a car while one of its candidates does not fit v6. An example is a
signal name that is longer than the 31 bytes the firmware allows.

```bash
canair wican autopid write                    # verified-only — ship what you've confirmed
canair wican autopid write --include-unverified  # verified + in-progress candidates
canair wican autopid write --firmware 6 --out my-car.json  # just the v6 table, elsewhere
```

The two files carry the same parameter identifiers (PIDs) and the same expressions. From
`6.0.0-beta.3`, v6 counts the bytes of a reply exactly as 4.x does. Firmware `6.0.0-beta.1` and
`6.0.0-beta.2` count them differently, so neither file is right for those versions. Update the
firmware. Two things in the v6 file differ from 4.x:

- **No per-signal MQTT stream.** v6 has no `send_to`, so canair leaves it out and says which
  signals had one. The nearest equivalent is a v6 rule on `autopid.param` → `mqtt.publish`. It
  fires when the value changes, not on every poll. MQTT is the Message Queuing Telemetry
  Transport protocol.
- **Signal bounds filter readings.** The device drops a reading outside the `min`/`max` of a
  signal, as 4.x already did.

### Sync the profile to the device

On firmware 4.x and a **WiCAN Pro**, you can sync the profile to the device directly. These
commands are Pro-only:

```bash
canair wican autopid diff      # what would change on the device
canair wican autopid upload    # push the profile to the dongle
```

canair cannot upload to firmware v6 yet. Loading `autopid-v6.json` **replaces the PIDs already on
the device**, your own PIDs included. To load the file by hand:

1. Drop the file on the **Automate** page of the device web UI. The page loads it as the whole
   AutoPID table.
2. Set the `vehicle` and `specific_init` AutoPID settings of the device to the values in the file.
   They apply after the restart that the web UI offers.
3. Turn AutoPID polling on. It is off on a fresh v6 device.

`out/` is generated. Never hand-edit it. Regenerate it instead.

### Making the device's reads faster

By default, the ELM327 co-processor of the WiCAN waits out its full ~614 ms budget for the
electronic control unit (ECU) on every PID. It does this to be sure that no further response frame
is coming. canair can record how many frames a PID answers with. It learns this count
automatically while you read the car, and it stores the count as `response_frames:` in `ecus/`.
By default, canair appends that count to each request. The adapter then returns as soon as the
reply is whole:

```bash
canair wican autopid stats                           # which PIDs have a count (Frames column)
canair wican autopid write                           # counts are emitted into the profile
canair wican autopid write --no-expected-responses   # leave them out
```

This is safe only because canair is strict about which counts it emits. The firmware cannot
recover from a count that is too low. It hands back a truncated response and leaves the remainder
queued. The remainder then shows up as the *next* PID's answer. canair therefore emits only a
count that the wire confirmed. It never emits a count for any of these:

- a variable-length response
- a request that cannot carry the digit
- a response longer than 9 frames

canair leaves PIDs without a confirmed count as they were. This is fine on a partly decoded
profile. Read the car some more and run the command again to widen coverage.

A PID whose answer legitimately changes length has no count that is always right. Mark it with
`canair pids set-pid-variable-length ECU PID true`. canair then never asks the adapter for a count
on that PID. This saves the failed attempt and retry that would otherwise happen each session.
canair also never records a count for it. canair never sets this flag on its own. A session that
merely sees two lengths clears a stored count instead.

### Streaming a signal at its poll rate

The WiCAN publishes AutoPID results to MQTT as one batch snapshot, which holds the latest value of
every signal. It publishes on the timer of the destination. The timer is 10 s unless you changed
it in the AutoPID settings of the device. Polling a PID faster does **not** make the broker see it
more often. When you need every sample of one signal (to average power draw, say), give that
signal its own topic with `send_to`:

```bash
canair pids upsert-param BMS 2101 BATTERY_POWER "<expression>" \
    --send-to car/stream/battery_power
canair wican autopid diff      # ~ BATTERY_POWER: send_to (none) → car/stream/battery_power
canair wican autopid upload
```

The device then publishes `{"BATTERY_POWER": <value>, "timestamp": <unix seconds>}` to that topic
**after every successful poll**, at the `period` of the PID, alongside the normal batch. The
message is retained, so the broker keeps the last sample. Pass `--send-to ""` to stop streaming a
signal.

- A topic must be unique across shipped signals. It must not contain `#` or `+`, and it must not
  start or end with `/`. `canair validate pids` checks this.
- Pick a topic that the consumer of the batch topic does not also subscribe to. Otherwise the
  consumer processes the stream a second time as batch data.
- The payload `timestamp` has whole-second resolution. Stamp samples on receipt if you need
  sub-second spacing.
- The batch destination itself (its topic and `cycle`) is device configuration. canair does not
  manage it.

## Contribute your profile back

**Please contribute your profile. This is the most valuable thing you can do for the project.**
Every profile you contribute gives the next person with your car a head start. This is how canair
becomes useful beyond one vehicle. Contributions are welcome in any size: a whole new car, a
handful of newly decoded signals, or a fix to an existing profile.

### The easy way: `canair contribute`

One command opens the pull request (PR) for you. You do not fork, clone or branch by hand:

```bash
canair contribute            # PR the active profile (definitions + captures)
canair contribute --no-captures   # definitions only (smaller)
canair contribute --diff          # show exactly what would be contributed, then stop
canair contribute --dry-run       # prepare it locally, but don't push/open the PR
canair contribute --no-logs       # leave the scrubbed diagnostics log out of the PR
```

The command copies your profile into a fork of the repo and opens the PR against
`philipkocanda/canair`. This works wherever your profile lives: the repo,
`~/.config/canair/profiles/`, or a `--path` bundle. canair prints where it **reads your profile
from** and the **workspace** it pushes from, so you know which copy it shares. Before it shares
anything, canair does these checks:

- It runs `canair validate all` and refuses if the check fails.
- It scans for anything that could **identify you**: a vehicle identification number (VIN), or an
  email address in a label or note. It asks you to confirm. It checks `captures/` and the
  `identity:` blocks in `ecus/`, where `canair identity` records a live VIN. A `--no-captures` PR
  therefore gets checked too. canair does not flag a masked VIN (`KMHCXXXXXXXXXXXXX`) again. It
  leaves per-unit **ECU serials** alone, because they name a module, not you.
- It **leaves out every reply to a
  [sensitive PID](../concepts/captures-and-states.md#sensitive-pids-and-signals)** (such as the
  EVCCID of the CCM), whatever your captures hold.
- It **warns if your profile looks stale** and asks you to confirm. A profile looks stale in two
  cases:

    - canair read it from an installed snapshot. A bare `canair` reads the frozen `site-packages`
      copy, not your checkout.
    - The contribution would *remove* lines that upstream already merged.

- It **lists every upstream capture session that it would replace** with your edited copy (see
  below). It also **warns about replies to several data identifiers (DIDs) that are stored whole
  under one DID**. Repair these first with `canair captures split-batched`.
- It **asks before it pushes** and opens the PR. Nothing leaves your machine until you say yes.
  `--yes` skips the prompt for scripts.

Captures are append-only evidence, so a contribution only *adds* capture sessions. A source that
is behind upstream does not propose to delete the sessions it lacks. A capture log that you add
nothing to stays exactly as upstream. This keeps the PR diff small.

The one exception is a session you **edited** after recording. Edits include `--set-notes`,
`--set-transport`, `--set-state`, a state back-fill, `migrate-discover` and `split-batched`. The
session is still the same recording, so your copy *replaces* the upstream copy in place.

A stale source looks the same, because upstream edited the session after you pulled. For this
reason, `--diff` and the confirmation list each replacement with the fields that differ. If you
did not edit the session, sync first.

Run the command again to revise a contribution. The default branch name is per-day. A second run
on the same day pushes to the branch that your pull request already tracks. canair reports the
**updated PR** and does not fail to open a second one. To get a separate PR on the same day, name
a new branch with `--branch`.

canair generates the PR description from what the contribution adds. The description lists the
definition files that the contribution changes. For the new and replaced capture sessions, it
lists:

- the car variant and ECU revisions that the sessions were tagged with
- the ECUs and vehicle states that the sessions cover
- the session and capture counts
- the link quality (transport errors, resyncs, disconnects)

A re-run regenerates the description. `--dry-run` prints it, and `--body` replaces it.

The description also carries entries of the diagnostics log (`canair logs`) that canair wrote
while the contributed sessions were recording. The window runs from shortly before the first
capture of each session to shortly after its last capture. A maintainer can then see what your
link did. canair leaves out the entries of other sessions. A PR that adds no sessions gets no
entries.

canair replaces device addresses, local paths, email addresses and VINs with placeholders. It caps
the excerpt to fit the PR description. Check the excerpt in the `--dry-run` output if you want to
be sure. Pass `--no-logs` to leave it out.

!!! tip "Run it from your own checkout"

    The **workspace** that canair prints is a throwaway clone under `~/.config/canair/`. Do not
    `cd` into it and run canair from there. The profile that canair would resolve is the copy
    inside the clone, which is exactly where this command *writes*. canair refuses that case. Run
    `canair contribute` from your own checkout (or from wherever your profile lives) instead.

    canair rebuilds the clone from upstream on every run. Every run cleans up what it staged
    there, so nothing staged earlier can ride along in a later PR. An interrupted run may leave a
    contribution staged there. Then canair lists the files and asks before it discards them. It
    first copies them to `~/.config/canair/contribute/backups/<timestamp>/`. `--yes` discards the
    files without asking and still backs them up.

    canair never resets your own checkout. Suppose you point `--repo-dir` at a checkout with
    uncommitted changes under `profiles/<your-car>/`. canair then lists the changes and asks
    first, because everything in that directory is committed.

!!! tip "Recording in a canair checkout? Drop what the PR carries"

    Your profile may live in a clone of canair (`profiles/<your-car>/`). Then the captures and
    edits that you contribute stay uncommitted there, and pulling the merged PR would conflict
    with them. Once the PR is open, canair lists the changes that the PR holds exactly. It asks
    whether to discard them. It backs them up to `~/.config/canair/contribute/backups/` first, and
    it leaves alone anything that the PR does not hold.

    `--discard-local` and `--keep-local` answer without asking. With `--yes` or `--json` and
    neither flag, canair keeps the changes. canair never touches a profile outside a canair
    checkout (such as `~/.config/canair/profiles/`).

Not sure what you are about to send? Run `canair contribute --diff` first. It prepares everything
and prints the full diff of `profiles/<your-car>/` against upstream. It does not commit, push or
open a PR.

**One-time setup — the GitHub CLI (`gh`):**

```bash
# install gh
brew install gh                      # macOS (Homebrew)
winget install --id GitHub.cli       # Windows
#   Linux / other: https://github.com/cli/cli#installation

# sign in (opens your browser)
gh auth login
```

`canair contribute` prints these same instructions if `gh` is not installed or you are not signed
in.

### The manual way (or if you prefer git yourself)

A standard GitHub pull request works too:

1. Fork [`philipkocanda/canair`](https://github.com/philipkocanda/canair). Put your profile under
   `profiles/<your-car>/`. See [step 1](01-create-profile.md) and create it there with `--path`.
2. Make sure the profile is clean and honest:

    - **Validate.** `canair validate all` must pass. This includes the duplicate-signal-name
      check.
    - **Verify what you claim.** Prefer `--verified` signals with a `--source` that records your
      evidence. An unverified guess should say so.
    - **Name the vehicle precisely.** `car_model` should pin down the model, year, market and
      battery, so that someone can tell if it matches their car.
    - **Decide on `captures/`.** Captures are good evidence but large. If size is a concern,
      include a representative subset and not everything.
    - **Scrub for privacy.** The tree is public, so include no VINs, addresses or other
      identifying or location data. Redact a VIN and do not delete the reading. `KMHC` followed
      by `X`s keeps the make and model prefix, which is useful and not identifying. Per-unit ECU
      serials are fine, because they name a module, not a person.

3. Open a PR. Even a *partial* profile is welcome. A few verified signals are better than nothing,
   and others can build on them.

Not ready for a full profile? **Individual PID and signal contributions are just as valuable.** A
single verified signal, a corrected offset or a new `research:` lead all help. If you find a bug
or a rough edge in canair itself, an issue or PR is also welcome.

## Keep going

A profile is never really "done". There are always more unmapped bytes. Use these commands:

- `canair research --summary` shows your open backlog.
- `canair coverage` finds undecoded bytes.

Then loop back through [capture](05-capture.md) → [analyze](06-analyze.md) →
[define](07-define-and-verify.md) whenever you want to decode more.

---

← Back to the **[overview](overview.md)**.
