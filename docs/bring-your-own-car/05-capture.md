# 5. Capture

Decoding needs **data under known conditions**. A single reading tells you almost
nothing; a byte only reveals itself when you watch it change as the car does
something. Capturing records payloads — tagged with *what the car was doing* — so
the [analysis](06-analyze.md) tools have something to work with.

## Save what you read

Add `--save` to any read to record it, with context:

```bash
canair query MyECU:2101 --save --label "highway 100 km/h" --state driving
```

- `--label` — a free-text description of the moment ("plugged in, charging").
- `--state` — the vehicle power state (`driving`, `charging`, `ready`, `sleep`,
  …). canair can auto-suggest this from decoded values; see
  [Captures & states](../concepts/captures-and-states.md).
- `--notes` — anything else worth remembering.

`--save` works with `query`, `scan`, `discover`, and live `--monitor`. Saves are
**journaled** as they stream, so a crashed or disconnected session is never
lost — recover leftovers with `canair captures --recover`.

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
canair captures --sessions       # table of contents: when, what state, which ECUs
canair captures MyECU --summary  # stats per PID
canair captures MyECU:2101 --diff  # byte-level diff across captures
```

!!! note
    Capture files under `captures/` are **never hand-edited** — they're written
    by `--save` and managed by canair. See
    [Captures & states](../concepts/captures-and-states.md).

!!! tip "Captures are shareable evidence"
    Well-labelled captures are valuable to the whole project — they're the raw
    material others use to decode and cross-check signals on the same car.
    Consider [contributing](08-share.md#contribute-your-profile-back)
    a representative subset alongside your profile.

---

Next: **[6. Analyze →](06-analyze.md)**
