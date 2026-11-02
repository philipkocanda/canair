# Service-ID enumeration (`canair scan services`)

Status: **PROPOSED**

Prompted by cross-referencing projectgus' `car_hacking` Kona EV work
(`hyundai_kona_ev/kona_service_ids.txt`) against the bundled Ioniq profile. That file is the
output of a per-address sweep that sends every diagnostic service ID to each ECU and records
which ones it answers — a capability canair does not have today.

## The gap

canair's `scan` kinds each probe one *service*: `range` sweeps PIDs/DIDs under a chosen service,
`sessions` probes `0x10` sub-functions, `routines` probes `0x31`/`0x33`, `iocontrol` probes
`0x2F`/`0x30`. Nothing answers the prior question: **which services does this ECU implement at
all?** `discover` tells you *which ECUs* answer; there is no "what can each one do" step between
discovering an ECU and deciding whether `routines`/`iocontrol`/`identity`/`dtc` are even worth
running on it.

That gap bites hardest for **bring-your-own-car**: on a blank profile, a user facing an unknown
responder has to guess which follow-up scans apply. A service map would tell them directly (this
ECU does `0x22` and `0x19` but not `0x2F`, so skip IOControl; it does `0x27`, so reads may be
gated).

On the gateway-isolated Ioniq the payoff is modest — you already exercise your ECUs' services —
so this is a BYO-car feature first, validated against the bundled profile second.

## Why this is a safety problem, not a convenience

A naive sweep sends exactly what `canlib/safety.py` exists to stop. `0x2F`/`0x31` actuate
hardware, `0x11` resets the ECU, `0x2E`/`0x3D` write memory, `0x34`-`0x37` reflash. The Kona log
was a *bench* scan where bricking was free; on a real car a blind `0x00..0xFF` sweep is
unacceptable.

The design pivot: **support is read from the rejection reason, never from running the service.**
A supported-but-unsatisfied service answers with an NRC *other than* serviceNotSupported — the
Kona log is full of `0x13` (incorrectMessageLength), `0x12`/`0x31`, `0x33`
(securityAccessDenied). So each service is probed with the minimal request the ECU bounces
*before acting*, and the verdict comes from the NRC:

| Reply | Verdict |
|---|---|
| positive (`SID+0x40 …`) | present (service ran — only for genuinely read-only SIDs) |
| NRC `0x11` serviceNotSupported | **absent** |
| NRC `0x12`/`0x13`/`0x22`/`0x31`/`0x33`/`0x7F`/… | present, rejected for another reason |
| no reply / timeout | inconclusive |

This is the same inference `sessions_scan` already uses for `0x10` sub-functions, generalized
across SIDs, and it reuses `canlib/uds/parse.py::classify_response` for the NRC taxonomy.

### The tiered policy

- **Default set — read/identify/inspect only.** The services whose *minimal* form has no side
  effect: `0x22`/`0x21`/`0x1A` (reads), `0x19`/`0x17`/`0x18` (DTC status), `0x3E` (TesterPresent),
  `0x10` bare (DiagnosticSessionControl with no sub-function → NRC, no session entered),
  `0x27 01`-style seed request *without* ever sending a key, etc. KWP2000 counterparts chosen by
  protocol.
- **`--include-risky` (confirm-first, car stationary).** Adds the families whose *presence* is
  worth probing but whose probe must be constructed to be refused before acting: `0x2F`/`0x31`
  (send a malformed/incomplete request → incorrectMessageLength, never a valid actuate),
  `0x2E`/`0x3B`/`0x3D`, `0x11` (ECUReset — the riskiest to probe; gated hardest, possibly omitted
  entirely pending review). Mutative-op rules apply: explicit `--yes` escape hatch, same as the
  DTC-clear path.
- **Never sent, even with `--include-risky`.** The programming session (`10 02`/`10 82`) and
  firmware services (`0x34`-`0x38`) — a **hard** refusal via `SafetyBlocked`, reusing
  `BLOCKED_UDS_SERVICES` and `check_session_mode`. The scan must not be a way around the
  blocklist.

The allowlist/blocklist lives in `canlib/safety.py` as data (next to `BLOCKED_UDS_SERVICES`), not
inline in the mode — one home, shared with the enforcement every terminal already routes through.

## Design

A new **kind** under the existing command, mirroring `sessions`/`routines`/`iocontrol` exactly —
no new top-level command.

### Layering

| Module | Role |
|---|---|
| `canlib/safety.py` | **extend, don't bypass**: add the probe-safe service allowlist + the risky-but-probeable set as data, and a `probe_form(sid, protocol)` helper returning the deliberately-refused request bytes. The hard blocklist already lives here. |
| `canlib/modes/services_scan.py` | new leaf mode, template = `modes/sessions_scan.py`. Walks the service set for one ECU, sends each probe through the `Terminal` surface, classifies replies via `classify_response`, returns records. Protocol (UDS vs KWP2000) auto-selected from `id_protocol`, the same `split_ecus_by_protocol` boundary `scans.py` documents. |
| `canlib/modes/dispatch/scans.py` | add `handle_scan_services`, parallel to the existing handlers. |
| `canlib/commands/scan.py` | add the `services` sub-parser (`add_connection_args` + `finalize_live_parser`); register the selector in `_live/defaults.py::CANAIR_DEFAULTS` and `dispatch/::_DISPATCH` (pinned by `tests/test_dispatch_table.py`). |
| `canlib/uds/layout.py` / `services.py` | reuse for service **names/labels** so output isn't the Kona log's mislabeled single-table guess; no new enum home. |

Transport-agnostic by construction: the mode uses only `Terminal` methods and dispatches through
`dispatch_mode`, so every transport gets it for free (the `dtc`/`sessions` pattern).

### Output & persistence

Results go to a structured per-ECU `supported_services:` block in `ecus/<ecu>.yaml` — the
`sessions:` section (schema `sessions_fields`) is the precedent: a tool-owned list of
`{service, name, verdict, nrc, probed}` written through the surgical editor, schema-validated in
`canlib/schema/`, auto-reverted on failure. `--save` records the raw probe payloads to `captures/`
like the other scans, so the evidence is queryable. `--json` for programmatic use.

### UX

- `canair scan services <ECU>` — one ECU.
- `canair scan services --all` — sweep current responders (the `dtc --all` pattern), skipping
  nothing silently; honour `discover`'s responder set.
- `--include-risky` + `--yes` — widen to the gated families (confirm-first).
- Auto-protocol by `id_protocol`; bare `canair scan services` opens the same wizard shape as the
  other kinds.

## Testing

- `tests/test_services_scan.py`: drive the mode with a fake `Terminal` returning scripted
  positive/NRC/timeout replies; assert the verdict mapping (esp. `0x11` → absent vs any other NRC
  → present) and that names resolve from the shared table.
- Safety: assert the programming session and `0x34`-`0x38` are **never** emitted even under
  `--include-risky --yes` (extend `tests/test_safety.py`); assert risky families require the
  confirm + `--yes`.
- `TestDispatchTransportAgnostic`-style check that it runs through `dispatch_mode`.
- Protocol: a KWP2000 ECU probes the KWP service set and labels it as KWP, not UDS.
- `tests/test_dispatch_table.py` membership/order for the new selector.

## Docs

- Regenerate `docs/reference/cli/scan.md` (`scripts/gen_cli_reference.py`, CI-checked).
- The command *map* line in `README.md` and `AGENTS.md` (`scan` kinds list) gains `services`.
- A short note in the bring-your-own-car docs: run `discover`, then `scan services`, to decide
  which follow-up scans apply.
- New screenshot entry in `docs/screenshots/shots.yaml` if the output view is screenshotted.

## Open questions

1. **`0x11` ECUReset** — is probing it ever safe? A malformed `0x11` *might* still reset on some
   ECUs. Leaning toward omitting it from even the risky set and documenting why.
2. **`0x27` SecurityAccess** — probe presence via a bare seed request (`27 01`) only, and never
   send a key. Confirm the seed request alone has no lockout side effect (some ECUs count failed
   attempts). May need to be read-only-presence via NRC without requesting a seed at all.
3. **Where `supported_services:` sits vs `sessions:`** — one combined "capabilities" section or
   two parallel ones. Lean parallel, matching the one-section-per-scan-kind precedent.
4. **Default service set membership** — settle the exact allowlist against ISO 14229-1 §8 /
   KWP2000, reviewed as a safety artifact, not assembled ad hoc.
