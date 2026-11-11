# 9. Broadcast CAN frames (optional side-journey)

Steps 1–8 decode **diagnostic** signals. These are the signals that an electronic control unit
(ECU) answers when *asked*, through Unified Diagnostic Services (UDS) request and response
(domain A). But a lot of interesting data is never answered on request. Examples are drive mode,
regeneration, wheel speeds and thermal management. The car **broadcasts** this data continuously
on the internal controller area network (CAN) bus (domain B). On many cars, the Ioniq included,
the On-Board Diagnostics (OBD-II) port is gateway-isolated, so you cannot reach the data by
asking.

This side-journey decodes those broadcast frames. It is optional and *parallel* to the main arc.
You need a **raw frame log**, not a live dongle. The signals land in a `signals/` sidecar, not in
`ecus/`. The *reasoning* is identical: inspect → hypothesize → correlate → define.

> **Concept vs. walkthrough.** This page is the task-first tour. The model, the storage and
> licensing policy, and the byte notation are in
> [Broadcast CAN frames](../concepts/broadcast-frames.md). Read that page for the *why*.

## When you'd do this

- You have a `.blf`, `.asc`, candump or SavvyCAN-GVRET **frame log**. You captured it with a wired
  tool on an internal bus, took it from another vehicle, or got it from someone else.
- Or you have a **DBC** file that describes the broadcast signals of another car. You want to
  cross-check those signals. DBC is a file format that describes the signals in CAN frames.

If you have only the OBD-II port, stay with steps 1–8. You cannot sniff broadcast frames through
an isolated gateway.

## 1. Import the log

`canair import can` copies the log into your profile **verbatim**. It indexes the metadata: frame
count, arbitration IDs and bitrate. High-volume logs stay native. canair does not explode them
into the capture YAML.

```bash
canair import can drive.blf --label "drive fwd/N/rev" --bitrate 500000
canair import can savvycan.csv          # SavvyCAN GVRET auto-detected by header
canair captures can                     # list imported logs
```

You refer to the bytes in a frame as **`0xID:rN`**. `0xID` is the arbitration ID, and `N` is the
byte. This is raw-CAN space. It has no ISO 15765-2 transport (ISO-TP) framing and no protocol
control information (PCI) bytes to skip. This differs from the WiCAN `Bnn` of domain A.

## 2. Analyze: which byte is which signal?

The same three analysis tools as in step 6 have a **`can`** kind. It reads the frame log directly.

**Point `investigate can` at one arbitration ID** to get a ranked per-byte report. For each byte,
the report shows the strongest cross-ID relationship, a linear fit and a unit guess:

```bash
canair investigate can drive.blf --id 0x386
# → 0x386:r0  r=+0.998 vs 0x331:r2  fit y=1.0020·x+0.07   (raw ×1)
#   0x386:r2  r=+0.993 vs 0x331:r4  …
#   0x386:r7  no cross-ID anchor ≥ 0.6     (a counter/checksum byte)
```

**Rank every relationship** in the log at once. Or hunt a specific byte against a reference that
you already know:

```bash
canair correlate can drive.blf --min-r 0.9              # strongest cross-ID pairs
canair hunt can drive.blf --id 0x220 --against 0x386:r0 # which byte of 0x220 tracks 0x386:r0?
```

**Find the same signal broadcast on two IDs.** This is a common manufacturer redundancy. An
example is wheel speed on both `0x386` and `0x331`:

```bash
canair correlate can drive.blf --find-mirrors                  # byte-level mirrors across IDs
canair correlate can drive.blf --find-mirrors --bits           # bit-level
canair correlate can drive.blf --find-mirrors --allow-offset    # also at a different zero/scale
```

Frames that are captured microseconds apart still disagree occasionally. The same quantity is
often broadcast in different units. A mirror therefore needs neither unanimity nor exact equality.
See [mirrors](../concepts/analysis-techniques.md#mirrors-the-same-quantity-reachable-two-ways).

The reasoning is the same as in [step 6](06-analyze.md). A byte that ramps with motion and mirrors
a known wheel-speed frame *is* a wheel speed. Confirm it with a correlation and a plausible fit
before you believe it.

## 3. Define the signal

Broadcast signals use a **linear model**: `physical = raw·scale + offset` over a contiguous bit
range. The model is compatible with DBC. It is deliberately simpler than the freeform expressions
of domain A. Write these signals with `canair signals`. The command is surgical and validated, and
it reverts the edit automatically on failure:

```bash
canair signals upsert powertrain 0x386 WHL_SPD_FL \
    --start-bit 0 --length 14 --byte-order little --scale 0.03125 --unit km/h \
    --source "drive.blf (fetch: …)" --unverified
canair signals list                    # review
canair validate signals                # structural check
```

Keep new signals **`--unverified`** until you confirm them against reality. This is the same
discipline as in [step 7](07-define-and-verify.md). Record where the data came from in `--source`.
Record the supporting evidence in `--notes`. After you confirm a signal,
`canair signals upsert powertrain 0x386 WHL_SPD_FL --verified` flips only that flag. An upsert of
an existing signal changes only the options you pass.

## 4. Interop — import / export DBC

You can bootstrap `signals/` from an existing DBC, or share yours:

```bash
canair import dbc other-car.dbc --bus powertrain --dry-run   # preview
canair import dbc other-car.dbc --bus powertrain             # DBC → signals/
canair export dbc --bus powertrain -o mycar.dbc              # signals/ → DBC
```

canair tags imported DBC signals with their provenance. You can then tell a third-party definition
from one that you verified on your car.

---

That is the broadcast domain end to end. It plugs into the same profile that you built in steps
1–8. A profile can carry both the diagnostic `ecus/` and the broadcast `signals/`. Back to the
main arc: **[8. Share →](08-share.md)**.
