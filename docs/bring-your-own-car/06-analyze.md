# 6. Analyze

You have captures; now find out **which byte is which signal**. canair's analysis
tools work over your captured data — no live car needed — and range from
"explain this whole PID" to "which exact byte tracks speed."

Start broad, then narrow.

## Start here: investigate a PID

`canair investigate` is the one-shot "tell me everything about this PID." For
every varying data byte it reports whether a parameter already maps it, how well
it separates across vehicle states, and its strongest relationship to a
co-captured signal:

```bash
canair investigate MyECU 2101 --state driving
canair investigate MyECU 2101 --bits     # also rank individual toggling bits (body signals)
canair investigate MyECU 2101 --events   # edge timeline (door/lock/hood-style events)
```

## Find where a known signal lives

If you have a *reference* signal (a known PID, or a GPS-confirmed value),
`canair hunt` sweeps every byte offset and interpretation on the target PID,
correlates each against the reference, and reports the best fit with a
physical-unit guess:

```bash
canair hunt MyECU:2102 --against MyECU:2101:SPEED_KMH
```

## Rank every relationship in a drive

`canair correlate` time-aligns *all* co-captured signals across a session and
ranks the strongest cross-signal relationships — the "show me everything
interesting in this drive" entry point:

```bash
canair correlate --overlap --state driving   # which ECU:PIDs share timeline?
canair correlate --state driving              # rank the strongest pairs
```

## Test a value, per-parameter

`canair decode` is value-centric: value ranges, stats, correlation, and an
interactive plot for one PID's parameters. You can test a candidate expression
**without editing any YAML**:

```bash
canair decode MyECU 2101 --stats
canair decode MyECU 2101 --plot                  # interactive signal explorer
canair decode MyECU 2101 --try "SPEED_KMH=[B12]" # test a hypothesis against captures
```

## The mindset

Think like the ECU's systems engineer: *what would this module need to measure
and report?* Signals come in physically sensible units and ranges, cluster by the
ECU's job, and are laid out in orderly blocks. Let that generate hypotheses — and
be rigorous: confirm a byte interpretation with data (range, distribution,
correlation, plausibility across states), not because it "looks about right."

Byte offsets are the classic trap — WiCAN, ISO-TP, and Torque all count bytes
differently. See [Byte indexing](../concepts/byte-indexing.md) and use
`canair bix --annotate` to map a raw payload.

---

Next: **[7. Define & verify →](07-define-and-verify.md)**
