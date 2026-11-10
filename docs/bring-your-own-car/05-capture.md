# 5. Capture

Decoding needs **data under known conditions**. One reading tells you almost nothing. A byte shows
its meaning only when you watch it change while the car does something. A capture records payloads
and tags them with *what the car was doing*. The [analysis](06-analyze.md) tools then have data to
work with.

## Save what you read

Add `--save` to any read to record it with context:

```bash
canair read MyECU:2101 --save --label "highway 100 km/h" --state driving
```

- `--label` is a free-text description of the moment ("plugged in, charging").
- `--state` is the vehicle power state (`driving`, `charging`, `ready`, `sleep`, …). canair can
  suggest the state from the decoded values. See
  [Vehicle states](../concepts/vehicle-states.md).
- `--notes` is anything else that you want to remember.

`--save` works with `read`, `monitor`, `scan` and `discover`. canair **journals** each save while
the data streams. A session that crashes or disconnects loses no data. To recover leftovers, run
`canair captures uds --recover`.

## No device on hand? Import a reading

You may have a payload from another source: a forum post, a GitHub issue, or a reading that
someone took with another tool. `canair import uds` records it in your profile. It uses the same
machinery as `--save`. The result looks the same as a capture from a device, and you can decode it
immediately:

```bash
canair import uds CLU:22B002=62B002E0000000FFB7008D08000000 \
    --label "Odometer" --state acc2 --notes "Verified 36104 km on dash"
```

Each capture has the form `ECU:PID=PAYLOAD`:

- `ECU` is the short name of the ECU or its hex TX ID.
- `PID` is the PID or DID.
- `PAYLOAD` is the reassembled UDS payload, with the SID first.

Pass several captures to group them in one session. canair resolves the ECU address and rejects
payloads that are not hex. It warns if the SID and DID echo of the payload does not match the PID
(a misfiled frame). This is how canair onboards community readings without a live bus.

### Import a whole frame log

Someone may have logged the diagnostic bus while *their* tool polled the car. Examples are a
Bluetooth dongle that runs an app with custom PIDs, or a workshop tester. The person then shared
the log. The [Kona EV logs](https://github.com/projectgus/hyundai-kona-ev-can-logs) are one such
public (CC0) corpus. Every reply in a log is a capture that `canair read --save` would have
recorded. Extract them all at once:

```bash
canair import uds --from-log dongle-polling.csv --bus 1 \
    --label "Dongle polling, READY" --state ready --date 2023-01-12 \
    --until-ts 2565 --dry-run          # preview; drop --dry-run to write
```

canair reads the log with the same readers as `import can`: GVRET `.csv`, `.asc`, `.blf`, candump
`.log` and `.trc`. Each positive reply to a data read (`22 DID`, or `21 LID` on KWP2000) from an
ECU in the profile becomes one capture. The capture keeps its own timestamp. canair files it
under what the *reply* says it answers.

canair counts and reports everything that it leaves out. It never guesses. It leaves out:

- multi-frame replies with a lost frame (canair also notes these in the `quality` of the session)
- replies to batched requests (`22 D1 D2 …`), which canair cannot split safely
- negative responses, unless you pass `--keep-nrc`
- replies from CAN IDs that are not in the profile (the report names them. Run `canair ecu add`
  for them, then run the import again)

A log has no absolute clock, unless someone recorded it on the wall clock. `--date` and `--time`
stamp the *first* reply. The other replies follow the timing of the log.

`--from-ts` and `--until-ts` (in the timestamps of the log) cut away a stretch that is known to be
bad. An example is the point where a capture tool starts to drop messages.

canair does not guess states from the log. Pass `--state`, or run
`canair captures uds --backfill-states` afterwards.

canair refuses to import the same log twice. It supports only normal addressing. It does not
support the extended-addressing profiles whose frames carry a target-address byte before the PCI
byte.

## Capture *contrast*, not just data

The most useful thing you can do is capture the **same PID in different conditions**. Some
examples:

- A byte that is constant while parked but ramps while you accelerate is a torque or speed
  candidate.
- A byte that flips only when you lock the doors is a body signal.

Plan runs that create contrast:

- Driving: accelerate, cruise, brake, coast.
- Charging: unplugged → plugged → charging → full.
- Body: lock and unlock, lights on and off, doors open and closed.

Also capture a reference signal that you *already* understand, next to the unknown signal. Examples
are GPS-confirmed speed or a known speed PID. The reference makes [`hunt`](06-analyze.md) and
correlation work.

## Review what you've collected

```bash
canair captures uds --sessions       # table of contents: when, what state, which ECUs
canair captures uds MyECU --summary  # stats per PID
canair captures uds MyECU:2101 --diff  # byte-level diff across captures
```

A bare `canair captures MyECU 2101` still works. It is a shorthand for `canair captures uds …`
(the diagnostic domain). `canair captures can` lists the imported raw broadcast-CAN frame logs.

!!! note
    **Never edit the capture files in `captures/` by hand.** `--save` and `import uds` write
    them, and canair manages them. See [Captures & states](../concepts/captures-and-states.md).

!!! tip "Captures are shareable evidence"
    Well-labelled captures help the whole project. Others use them as raw material to decode and
    cross-check signals on the same car. Consider
    [contributing](08-share.md#contribute-your-profile-back) a representative subset with your
    profile.

---

Next: **[6. Analyze →](06-analyze.md)**
