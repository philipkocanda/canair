# 3. Read identity

Once ECUs are [registered](02-discover-ecus.md), `canair identity` reads an
ECU's standard identity data — part number, hardware/software version, serial
number, and VIN — and decodes it for display. It queries UDS (`22 F1xx`) or
KWP2000 (`1A 8x/9x`) automatically based on what the ECU speaks (see
[ECU protocols & PID prefixes](../concepts/ecu-protocols.md)).

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
canair pids rm-identity  MyECU sw_version   # drop a field you filed wrongly
```

The field name must be one the schema declares (`canlib/schema/pids_schema.yaml`,
`identity_fields`) — a typo is a validation **error** and the edit is reverted, so
a misfiled `sofware:` can't sit in the profile unnoticed.

### One reading, one field

Several of those fields are near-synonyms (`firmware` / `fw_version` /
`sw_version` / `sw_id`, and `hw_version` / `hw_sw`) because every marque names its
identity DIDs differently. Pick the one that matches what the ECU actually
reported and **don't mirror the value into a second field** — the copy becomes
dead data that later readers mistake for independent evidence. `canair validate
pids` warns when two synonymous fields hold the same value and points you at
`rm-identity`.

---

Next: **[4. Scan for data →](04-scan.md)**
