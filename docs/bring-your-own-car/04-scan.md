# 4. Scan for data

Identity tells you *what* an ECU is. **Scanning** tells you *what the ECU answers*. `canair scan`
sweeps a range of PIDs or DIDs on one ECU and reports which ones return data. This data is the raw
material that you capture and decode.

## Sweep a range

```bash
canair scan range MyECU --range 2100-21FF --save
```

- `range` is the general-purpose kind. A bare `canair scan MyECU` is a shorthand for it.
- `--range START-END` is the DID or PID range in hex, or a comma list of ranges
  (`B000-B01F,B100-B11F`). If you omit it, canair uses a smart default for each ECU. On a UDS
  ECU, the default is every **DID family** (a high byte such as `B4xx`) that the profile shows the
  ECU using. canair reads the families from the `22` and `2F` PIDs of the ECU and from the
  actuators that an IOControl scan found. An ECU often keeps its live-data DIDs in the same few
  families as its actuators. An ECU that you know only by its actuators therefore still gets a
  useful read sweep. `--slots N` limits each family to its first N DIDs.
- `--service SVC` selects the UDS service to probe, for example `read-did` for `22` or
  `live-data` for `21`. `canair scan range --help` lists the presets.
- `--save` records the responders to `captures/`, so canair keeps the hits.
- `--session` and `--wake` enter an extended session and wake a sleeping ECU first. Some ECUs
  answer only under these conditions.

Run `canair scan range --help` for the full flag list. Run `canair scan range` with no ECU for an
interactive wizard.

## Safe discovery of other capabilities

`canair scan` has dedicated, **safe** sub-kinds. They probe what an ECU can *do*. Each one selects
the correct UDS or KWP2000 service automatically:

```bash
canair scan iocontrol MyECU   # discover IOControl actuators (returnControlToECU)
canair scan routines MyECU    # discover diagnostic routines (safe: requestRoutineResults)
canair scan sessions MyECU    # discover which diagnostic session types it supports
```

These probes ask *whether* a capability exists. They do not actuate it.

There is one exception. The IOControl service of KWP2000 leaves its control-option byte to the
manufacturer. The `00` that `scan iocontrol` sends switches an output *on* on at least one Kia
ECU. For this reason, the scan asks for confirmation before it sweeps a KWP2000 ECU. Use `--yes`
to skip the question.

To *trigger* an actuator or a routine, use a separate action that asks for confirmation first (see
[Safety](../concepts/safety.md)).

## Turning hits into a plan

A scan hit means "this DID returns bytes". It does not mean that you know what the bytes mean.
Record what to investigate next as a research lead. Then canair tracks it:

```bash
canair pids add-research MyECU --type decode --target 2101 \
    --status captured --notes "27 bytes, changes while driving"
canair research --summary    # your open reverse-engineering backlog
```

After `scan iocontrol` finds the actuators of an ECU, ask which of their DID families nobody has
read yet. An ECU often keeps its live-data records next to its actuators. Use this check on an ECU
that seemed to have no live data:

```bash
canair research --gaps       # actuator families no 0x22 read has reached, with the scan to run
```

The command uses what the profile already records:

- the `iocontrol:` and `iocontrol_discoveries:` entries
- `scan_log:`
- the targets in `research:`
- the saved `scan range --save` sessions (a sweep that the ECU slept through does not count)

---

Next: **[5. Capture →](05-capture.md)**
