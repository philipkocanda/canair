# 4. Scan for data

Identity tells you *what* an ECU is; **scanning** tells you *what it answers*.
`canair scan` sweeps a range of PIDs/DIDs on one ECU and reports which ones
return data — the raw material you'll capture and decode.

## Sweep a range

```bash
canair scan range MyECU --range 2100-21FF --save
```

- `range` is the general-purpose kind (a bare `canair scan MyECU` is shorthand
  for it).
- `--range START-END` — the DID/PID range in hex. Omit it for a smart per-ECU
  default.
- `--service SVC` — which UDS service to probe (e.g. `read-did` for `22`,
  `live-data` for `21`). Presets are listed in `canair scan range --help`.
- `--save` — record responders to `captures/` so the hits are preserved.
- `--session` / `--wake` — enter an extended session / wake a sleeping ECU first,
  for ECUs that only answer under those conditions.

Run `canair scan range --help` for the full flag list, or `canair scan range`
with no ECU for an interactive wizard.

## Safe discovery of other capabilities

`canair scan` has dedicated, **safe** sub-kinds for probing what an ECU can *do*,
each auto-selecting the right UDS/KWP2000 service:

```bash
canair scan iocontrol MyECU   # discover IOControl actuators (safe: returnControlToECU)
canair scan routines MyECU    # discover diagnostic routines (safe: requestRoutineResults)
canair scan sessions MyECU    # discover which diagnostic session types it supports
```

These are read-only probes — they ask *whether* a capability exists, they don't
actuate anything. Actually *triggering* an actuator or routine is a separate,
confirm-first action (see [Safety](../concepts/safety.md)).

## Turning hits into a plan

A scan hit means "this DID returns bytes," not "you know what they mean." Record
what to investigate next as a research lead so it's tracked:

```bash
canair pids add-research MyECU --type decode --target 2101 \
    --status captured --notes "27 bytes, changes while driving"
canair research --summary    # your open reverse-engineering backlog
```

---

Next: **[5. Capture →](05-capture.md)**
