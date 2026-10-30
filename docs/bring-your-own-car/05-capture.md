# 5. Capture

Decoding needs **data under known conditions**. A single reading tells you almost
nothing; a byte only reveals itself when you watch it change as the car does
something. Capturing records payloads — tagged with *what the car was doing* — so
the [analysis](06-analyze.md) tools have something to work with.

## Save what you read

Add `--save` to any read to record it, with context:

```bash
canair read MyECU:2101 --save --label "highway 100 km/h" --state driving
```

- `--label` — a free-text description of the moment ("plugged in, charging").
- `--state` — the vehicle power state (`driving`, `charging`, `ready`, `sleep`,
  …). canair can auto-suggest this from decoded values; see
  [Captures & states](../concepts/captures-and-states.md).
- `--notes` — anything else worth remembering.

`--save` works with `read`, `monitor`, `scan`, and `discover`. Saves are
**journaled** as they stream, so a crashed or disconnected session is never
lost — recover leftovers with `canair captures uds --recover`.

## No device on hand? Import a reading

Got a payload from elsewhere — a forum post, a GitHub issue, a reading someone
took on another tool — and want it in your profile? `canair import uds`
records it through the same machinery as `--save`, so it's indistinguishable from
a device-recorded capture and immediately decodable:

```bash
canair import uds CLU:22B002=62B002E0000000FFB7008D08000000 \
    --label "Odometer" --state acc2 --notes "Verified 36104 km on dash"
```

Each capture is `ECU:PID=PAYLOAD` (ECU short name or hex TX id; the PID/DID; the
reassembled UDS payload, SID-first). Pass several to group them into one session.
canair resolves the ECU address, rejects non-hex payloads, and warns if the
payload's SID/DID echo doesn't match the PID (a misfiled frame). This is how
community-contributed readings get onboarded without a live bus.

### Import a whole frame log

Someone may have logged the diagnostic bus while *their* tool polled the car (a
Bluetooth dongle running an app's custom PIDs, a workshop tester) and shared the
log — the [Kona EV logs](https://github.com/projectgus/hyundai-kona-ev-can-logs)
are one such public (CC0) corpus. Every reply in it is a capture
`canair read --save` would have recorded, so extract them in one go:

```bash
canair import uds --from-log dongle-polling.csv --bus 1 \
    --label "Dongle polling, READY" --state ready --date 2023-01-12 \
    --until-ts 2565 --dry-run          # preview; drop --dry-run to write
```

The log is read with the same readers as `import can` (GVRET `.csv`, `.asc`,
`.blf`, candump `.log`, `.trc`). Each positive reply to a data read (`22 DID`,
or `21 LID` on KWP2000) from an ECU in the profile becomes one capture with its
own timestamp, filed by what the *reply* says it answers. What is left out is
counted and reported, never guessed:

- multi-frame replies with a lost frame (also noted in the session's `quality`);
- replies to batched requests (`22 D1 D2 …`), which cannot be split safely;
- negative responses, unless you pass `--keep-nrc`;
- replies from CAN ids not in the profile (the report names them — `canair ecu
  add` them and re-run).

A log has no absolute clock unless it was recorded on the wall clock, so `--date`
and `--time` stamp the *first* reply and the rest follow the log's own timing.
`--from-ts`/`--until-ts` (in the log's timestamps) cut away a stretch that is
known to be bad, such as where a capture tool starts dropping messages. States are
not guessed from the log: pass `--state`, or run
`canair captures uds --backfill-states` afterwards. Importing the same log twice
is refused. Only normal addressing is supported (not the extended-addressing
profiles whose frames carry a target-address byte before the PCI byte).

## Capture *contrast*, not just data

The single most useful thing you can do is capture the **same PID in different
conditions**. A byte that's constant while parked but ramps while accelerating is
a torque/speed candidate; one that flips only when you lock the doors is a body
signal. Plan runs that create contrast:

- Driving: accelerate, cruise, brake, coast.
- Charging: unplugged → plugged → charging → full.
- Body: lock/unlock, lights on/off, doors open/closed.

Capture a reference signal you *already* understand alongside the unknown (e.g.
GPS-confirmed speed, or a known speed PID) — that reference is what makes
[`hunt`](06-analyze.md) and correlation work.

## Review what you've collected

```bash
canair captures uds --sessions       # table of contents: when, what state, which ECUs
canair captures uds MyECU --summary  # stats per PID
canair captures uds MyECU:2101 --diff  # byte-level diff across captures
```

A bare `canair captures MyECU 2101` still works — it's shorthand for
`canair captures uds …` (the diagnostic domain). `canair captures can` lists
imported raw broadcast-CAN frame logs instead.

!!! note
    Capture files under `captures/` are **never hand-edited** — they're written
    by `--save` / `import uds` and managed by canair. See
    [Captures & states](../concepts/captures-and-states.md).

!!! tip "Captures are shareable evidence"
    Well-labelled captures are valuable to the whole project — they're the raw
    material others use to decode and cross-check signals on the same car.
    Consider [contributing](08-share.md#contribute-your-profile-back)
    a representative subset alongside your profile.

---

Next: **[6. Analyze →](06-analyze.md)**
