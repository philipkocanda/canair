# Capture file schema — Ioniq CAN reverse engineering

Human-readable companion. The **authoritative, machine-checked** schema is
`canlib/schema/captures_schema.json` (JSON Schema, draft 2020-12); validate with
`canair validate captures`. Capture files are written/edited **only** by the
`canlib.captures` helpers (via `canlib/capture_io.py`) — never by hand.

On disk each capture file is **JSON** (`captures/YYYY-MM-DD.json`) — it parses
far faster than YAML (see `plans/2026-07-27-captures-json-storage.md`). A profile
created before the cutover is converted once with `canair captures migrate`.
Each file holds one date.

## Structure

```json
{
  "sessions": [
    {
      "date": "YYYY-MM-DD",
      "label": "short description",
      "vehicle_states": ["ready", "parked"],
      "notes": "optional session-level notes",
      "captures": [ { "…capture entry…": "" } ]
    }
  ]
}
```

- `date` — required, ISO 8601.
- `label` — required.
- `vehicle_states` — optional list of power-state tokens (vocabulary from
  `states.yaml`; soft-validated).
- `notes` — optional, session-level.

## Capture entry fields

Required (all capture types):

- `ecu` — ECU CAN **response** address as a hex string (e.g. `"0x7EC"`), or
  `"broadcast"` for multi-ECU discovery scans. Tools resolve it to the short
  name via the `ecus/` registry.
- `pid` — PID/DID string (e.g. `"2101"`, `"22BC03"`, `"2FBC2D03"`,
  `"22BC1D-BC41"`).

Optional:

- `payload` — reassembled UDS response payload (SID-first, ISO-TP PCI stripped).
- `notes` — capture notes (may be empty `""`).
- `label` — short description of what was tested (recommended for
  experiments/scans).
- `time` — capture timestamp `HH:MM:SS[.fff]` (added by `--save`).

> Decoded parameter values are **not** stored — they're derived data,
> regenerated on demand from `payload` + the PID definitions in `ecus/`. Use
> `canair decode` / `canair captures` to view decoded values.

## `scan_results` structure

```json
{
  "responding": [
    { "did": "BC21", "response": "7E00", "notes": "optional per-DID note" }
  ],
  "rejected": "summary of non-responding DIDs (optional)",
  "notes": "optional scan-level notes"
}
```

## Deprecated fields (do NOT use in new captures)

- `ecu_tx`, `ecu_rx`, `ecu_name` — replaced by `ecu` (response address, resolved
  to the short name via `ecus/`).
- `decoded` — no longer stored; decoded values are regenerated on demand.
