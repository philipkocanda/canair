# 3. Read identity

Once ECUs are [registered](02-discover-ecus.md), `canair identity` reads an
ECU's standard identity data — part number, hardware/software version, serial
number, and VIN — and decodes it for display. It queries UDS (`22 F1xx`) or
KWP2000 (`1A 8x/9x`) automatically based on what the ECU speaks.

```bash
canair identity MyECU
canair identity 770              # by address instead of name
canair identity MyECU --session  # some ECUs only answer in an extended session
canair identity BMS --protocol kwp   # force KWP2000 for a powertrain ECU
```

`canair identity` is a **read** — it displays what it finds, it doesn't edit
`ecus/`. To capture identity for every ECU as part of the discovery sweep, use
`canair discover --identify` (see [step 2](02-discover-ecus.md)).

## Why it's worth doing early

Identity tells you *what each ECU actually is*, which lets you rename the
placeholder ECU entries meaningfully (`Unknown-770` → `IGPM`) and cross-reference
part numbers against other vehicles' known signal maps — a huge head start on
[analysis](06-analyze.md). Cross-referencing a shared part number with another
car's public PID data is one of the fastest ways to seed hypotheses.

## Recording curated identity fields

Anything you want to *store* about an ECU beyond the raw decoded DIDs — notes, a
description — goes through the validated editor rather than hand-editing YAML:

```bash
canair pids set-identity MyECU notes "Body control module; mirrors IGPM door bits"
```

---

Next: **[4. Scan for data →](04-scan.md)**
