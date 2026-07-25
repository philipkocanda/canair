# 9. Broadcast CAN frames (optional side-journey)

Steps 1–8 decode **diagnostic** signals — the ones an ECU answers when *asked*
(UDS request/response, domain A). But a lot of the interesting data — drive mode,
regen, wheel speeds, thermal management — is never answered on request. It's
**broadcast** continuously on the internal CAN bus (domain B), and on many cars
(the Ioniq included) the OBD-II port is gateway-isolated, so you can't reach it
by asking.

This side-journey decodes those broadcast frames. It's optional and *parallel*
to the main arc: you need a **raw frame log** rather than a live dongle, and the
signals land in a `signals/` sidecar rather than `ecus/`. The *reasoning* —
inspect → hypothesize → correlate → define — is identical.

> **Concept vs. walkthrough.** This page is the task-first tour. The model,
> storage/licensing policy, and byte notation live in
> [Broadcast CAN frames](../concepts/broadcast-frames.md) — read it for the *why*.

## When you'd do this

- You have a `.blf`/`.asc`/candump/SavvyCAN-GVRET **frame log** (captured with a
  wired tool on an internal bus, from another vehicle, or shared by someone else).
- Or a **DBC** describing another car's broadcast signals you want to cross-check.

If all you have is the OBD-II port, stick to steps 1–8 — you can't sniff
broadcast frames through an isolated gateway.

## 1. Import the log

`canair import can` copies the log into your profile **verbatim** and indexes its
metadata (frame count, arbitration IDs, bitrate). High-volume logs stay native —
they are not exploded into the capture YAML.

```bash
canair import can drive.blf --label "drive fwd/N/rev" --bitrate 500000
canair import can savvycan.csv          # SavvyCAN GVRET auto-detected by header
canair captures can                     # list imported logs
```

Bytes in a frame are referenced as **`0xID:rN`** (arbitration ID `0xID`, byte
`N`) — raw-CAN space, with no ISO-TP framing or PCI bytes to skip (unlike the
WiCAN `Bnn` of domain A).

## 2. Analyze: which byte is which signal?

The same three analysis tools as step 6 have a **`can`** kind that reads the
frame log directly.

**Point `investigate can` at one arbitration ID** to get a ranked per-byte report
— each byte's strongest cross-ID relationship, a linear fit, and a unit guess:

```bash
canair investigate can drive.blf --id 0x386
# → 0x386:r0  r=+0.998 vs 0x331:r2  fit y=1.0020·x+0.07   (raw ×1)
#   0x386:r2  r=+0.993 vs 0x331:r4  …
#   0x386:r7  no cross-ID anchor ≥ 0.6     (a counter/checksum byte)
```

**Rank every relationship** in the log at once, or hunt a specific byte against a
reference you already know:

```bash
canair correlate can drive.blf --min-r 0.9              # strongest cross-ID pairs
canair hunt can drive.blf --id 0x220 --against 0x386:r0 # which byte of 0x220 tracks 0x386:r0?
```

**Find the same signal broadcast on two IDs** — a common manufacturer redundancy
(e.g. wheel speed on both `0x386` and `0x331`):

```bash
canair correlate can drive.blf --find-mirrors          # byte-level mirrors across IDs
canair correlate can drive.blf --find-mirrors --bits   # bit-level
```

The reasoning is the same as [step 6](06-analyze.md): a byte that ramps with
motion and mirrors a known wheel-speed frame *is* a wheel speed; confirm it with
correlation and a plausible fit before you believe it.

## 3. Define the signal

Broadcast signals use a **linear model** (`physical = raw·scale + offset` over a
contiguous bit range) — DBC-compatible, deliberately simpler than domain A's
freeform expressions. Write them with `canair signals` (surgical, validated,
auto-reverted — never hand-edit the sidecar):

```bash
canair signals upsert powertrain 0x386 WHL_SPD_FL \
    --start-bit 0 --length 14 --byte-order little --scale 0.03125 --unit km/h \
    --source "drive.blf (fetch: …)" --unverified
canair signals list                    # review
canair validate signals                # structural check
```

Keep new signals **`--unverified`** until confirmed against reality (same
discipline as [step 7](07-define-and-verify.md)); record where the data came from
in `--source` and the supporting evidence in `--notes`.

## 4. Interop — import / export DBC

Bootstrap `signals/` from an existing DBC, or share yours:

```bash
canair import dbc other-car.dbc --bus powertrain --dry-run   # preview
canair import dbc other-car.dbc --bus powertrain             # DBC → signals/
canair export dbc --bus powertrain -o mycar.dbc              # signals/ → DBC
```

Imported DBC signals are tagged with their provenance so you can tell a
third-party definition from one you verified on your car.

---

That's the broadcast domain end to end. It plugs into the same profile you built
in steps 1–8; a profile can carry both diagnostic `ecus/` and broadcast
`signals/`. Back to the main arc: **[8. Share →](08-share.md)**.
