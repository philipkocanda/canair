# Steps 2-3 — orient on the byte and capture event data

Scope: `bix` byte layout orientation and narrated capture (talks to the car: needs approval).

## Step 2 — orient on the byte

```
uv run canair --profile hyundai-ioniq-ae-ev bix -a 62BC030AFF00000000 --ecu IGPM --pid 22BC03
```

```
  subfunction: 2-byte DID — derived from --pid 22BC03 (override with -1/-2)
    B10 | 0x00 |   0x07 |        | [DOOR_RL_OPEN:0] [DOOR_LOCK_RL:1] … [TRUNK_OPEN:7]
    B11 | 0x00 |   0x08 |        | [HOOD_OPEN:0] [SEATBELT_FL:1] [SEATBELT_FR?:2] [ACC2_IGN_ON:5] [IGN2_SWITCH?:6]
```

The fastest per-bit "what's already claimed here" view (`?` suffix =
unverified). B11's gaps at bits 3, 4, 7 are visible at a glance.

> **Subfunction width.** `--pid` derives it (`22xxxx` → 2 bytes, else 1) and says
> so in the header, so the overlay form above needs no `-2`. An explicit `-1`/`-2`
> still wins and is warned about when it contradicts the PID. **Without `--pid`**
> the 1-byte default applies — annotating a `22xxxx` payload bare still needs
> `-2`, or the second DID-echo byte is mislabelled as a data byte and every role
> after it shifts (verified: B03 reads `PID`, B04 becomes a data byte).

## Step 3 — capture data that can attribute a bit

Correlation cannot decode a door bit; **a narrated event log can.** Bits need
captures where you *caused* the transitions and wrote down what you did.

This step talks to the car. Propose the command and the action sequence to the user, and run it
only after the user explicitly approves it (`AGENTS.md` → The car).

```
uv run canair --profile hyundai-ioniq-ae-ev monitor IGPM:22BC03 --save \
    --label "IGPM bitfield events" --state ACC \
    --notes "fob unlock, open/close trunk, open/close pax door, open drv door, open/close hood, fob lock"
```

- Keep the default **`--keep-all`** (or `--keep-changes`) — both record both edges.
  Older `keep:unique` sessions **dropped falling edges**, which makes dwell durations
  unrecoverable there (`--dwell` warns and reports `unknown` classes).
- Exercise **one actuation at a time**, pausing between, and press **`s`** in the
  monitor to note each action. A per-capture note is what `--events` aligns to.
- Cover states: a bit gated by ignition only moves in `ACC`/`RUN`.
- Poll the **suspected mirror ECUs too** (e.g. `"IGPM:22BC03 BCM:22B004"`) so
  cross-ECU mirror detection has co-polled data.
