# WiCAN firmware v6: generating and syncing a compatible AutoPID profile

Status: **PHASE 1 IMPLEMENTED** (2026-10-31); Phases 2 and 3 proposed. Every finding comes from
reading the source and from running canair's own generator offline. Nothing here has run on a v6
device yet.

This is Phase 2 item 3 of `plans/2026-10-20-wican-fw-v6-compatibility.md`. That plan's §6 records
the format change and the data-loss trap. This plan works out what canair has to emit, and how to
upload it safely.

## TL;DR

- **The decoding carries over unchanged.** From `6.0.0-beta.3` on, v6 indexes a reply the same
  way 4.x does, so canair's WiCAN `Bnn` expressions are correct without translation. All 1,271
  expressions the bundled profiles can ship pass v6's own expression checker, compiled and run on
  the host. No field overflows a v6 buffer.
- **Only the container changes.** canair emits a 4.x car (`car_model` + `init` + `pids[]` with
  `pid`/`pid_init` and string `period`). v6 wants a table document (`groups` + `pids` + `filters`)
  with `name`/`cmd`/`init`/`rxheader`/`type`/numeric `period_ms`. The car-level `init` is no longer
  in the table at all: it becomes the device's `specific_init`.
- **The upload needs care that the 4.x one did not:**
  - `PUT /api/autopid/config` replaces the device's whole table, so the upload must read, merge,
    write and then read back.
  - The table belongs to whichever car the device's vehicle store has as current.
  - AutoPID polling is off by default.
  - Firmware `6.0.0-beta.1`/`beta.2` (and every alfa) index bytes differently and must be refused.
- **One feature is lost:** per-signal `send_to` MQTT streaming has no table field on v6. It would
  map onto a rules-engine rule, and that is follow-up work.

## Source basis

| Tree | Ref | Note |
|---|---|---|
| `meatpi-components` | `aa4c2b3` (2026-10-31) | `components/autopid`, `expression_parser`, `web_ui_v2`. Newer than any release |
| `wican-fw` | `v6.0.0p-beta.5` (2026-10-27) | latest v6 release; the tag does not pin a components commit |
| `wican-fw` (4.x) | `v4.52p` = `fab54ad` | the local `wican-fw/` checkout, for 4.x comparisons |

Paths prefixed `mpc/` are relative to `meatpi-components/components/`. **The release does not
pin the components commit**, and AutoPID changed almost daily in October. So re-check each
citation against the beta that is actually flashed (open question Q1 of the compatibility plan).

## What canair emits today

`canlib/wican/autopid_profile.py` produces two shapes:

1. **`generate_profile()`**, the file `autopid write` writes (`out/[<variant>/]autopid.json`).
   This is the *upstream source* format: `{car_model, init, pids: [{pid_init, pid, enabled,
   period: "5000", parameters: {NAME: expression}, send_to?}], can_filters: []}`.
2. **`to_device_format()`**, the body `autopid upload` POSTs to 4.x `/store_car_data`. This is
   `{cars: [{car_model, init, pids: [{pid_init, pid, enabled, parameters: [{name, expression,
   unit, class, period, min, max, type, send_to}]}]}]}`, with every number written as a string.

`pid_init` carries `ATSH<tx>;ATFCSH<fc>;` plus, when any shipped ECU needs pinning,
`ATCRA<rx>;`, and optionally `1003;`. The car `init` is the profile's `init` (for example
`ATSP6;ATS0;ATAL;ATST96;`). When a profile pins addressing, it also gets the flow-control mode-1
chain `ATFCSH…;ATFCSD…;ATFCSM1;` (`with_flow_control`). `pid` may end in an expected-response
frame-count digit (`request_with_count`).

## What v6 accepts

### The table document (`PUT /api/autopid/config`)

The shape is in `mpc/autopid/README.md:528-544`. The parser is `ap_config_parse`
(`mpc/autopid/autopid_config.c:272-510`):

```json
{"groups": [{"name": "default", "enabled": true, "period_ms": 1000}],
 "pids": [{"name": "BMS_2101", "type": "specific", "cmd": "21019",
           "init": "ATSH7E4;ATFCSH7E4", "rxheader": "7EC", "group": "default",
           "period_ms": 2500, "enabled": true,
           "parameters": [{"name": "SOC_BMS", "expression": "B9/2", "unit": "%",
                           "class": "battery", "min": 0, "max": 100}]}],
 "filters": []}
```

Parse rules that matter to a generator:

- **Unknown top-level keys are ignored, so a 4.x body is accepted as an empty table.** The
  route answers `200 {"ok":true}` and wipes the device's PIDs (compatibility plan §6).
- **Strings are copied with `snprintf` into fixed buffers, so overlong values are silently
  truncated** (`copy_str`, `autopid_config.c:143-154`). The limits, including the terminating NUL
  byte (`mpc/autopid/autopid_private.h:55-62`), are:

  | Field | Buffer (bytes) |
  |---|---|
  | names | 32 |
  | `cmd` | 24 |
  | `init` | 96 |
  | `rxheader` | 16 |
  | `expression` | 96 |
  | `unit` | 16 |
  | `class` | 24 |

  The limits count bytes, not characters: `°C` is 3 bytes.
- **Numbers must be JSON numbers.** `get_num` ignores a string and falls back to the default
  (`:156-161`). So `"period_ms": "5000"` silently becomes `0`, which means "inherit the group's
  period" (1000 ms by default), and string `min`/`max` become "no bound".
- `name` and `cmd` are required on a PID. A parameter needs a `name` and an expression that
  passes `expression_parser_check`. **Parameter names must be unique across the whole table**,
  including rows canair does not own (`:487-498`). PID names need not be unique.
- `type` is `std`, `specific` or anything else, which means `custom` (`:396-398`). Every row's
  `group` must name a declared group; `default` always exists.
- The `cmd` and `init` strings pass through the EEPROM guard (the check that keeps the ELM327
  chip's non-volatile memory from wearing out). `ATSP`→`ATTP` and `ATM1`→`ATM0`. `ATPP`, `ATSD`,
  `ATCV` and `STWBR` refuse the whole table. **`ATH0`, `ATS0` and `ATE1` are stripped**
  (`ap_init_sanitize`, `:170-185`).
- Table bounds: 512 PIDs, 2,048 parameters across the whole table, 256 per PID, and a 256 KB body
  (`autopid_private.h:42-46`, `autopid_http.c:60`). An oversize table is refused whole.

### Where the car-level init goes

The table has no car `init`. Its v6 home is the `specific` type's init chain. The runner sends
that chain once on every switch to a `specific` row, ahead of each row's own `init`
(`mpc/autopid/autopid_runner.c:401-411`). There are two ways to set it:

- **The vehicle store entry**: `PUT /api/autopid/vehicles/<key>` with
  `{"profile": "<car_model>", "specific_init": "…"}`. This applies live when that car is current
  (`README.md:837-841`).
- **The settings fallback**: `specific_init` and `vehicle` in `/api/settings/autopid`. These take
  effect only after `POST /api/settings/submit` and a reboot (`autopid_settings.c:42-62`).

The v6 web UI's importer uses exactly this split: rows → table, `car_model` →
`vehicle`/`profile`, `init` → `specific_init` (`mpc/web_ui_v2/web/index.html:1911-1927`, `:3216`,
and `setup.js:1750`).

### Frame of reference, firmware by firmware

| Firmware | `B0` is | canair's `Bnn` |
|---|---|---|
| 4.x | the first byte after the CAN id, ISO-TP framing bytes (PCI, Protocol Control Information) counted | correct |
| v6 alfa, `6.0.0-beta.1`, `beta.2` | the service echo, PCI stripped (headers off) | **wrong**: one byte off on a single-frame reply, more on multi-frame |
| `6.0.0-beta.3`+ | the first byte after the CAN id, PCI counted (headers on again, issue #1014) | correct |

Sources: `README.md:571-594`, `:968-990`, and `ap_resp_frames` in `autopid_resp.c`. Filters
index the frame data (`B0` = first data byte) on every generation.

`/api/info` reports `api_level: 6` on every v6 build (`mpc/api_http/api_http_system.c:57`).
**Only `fw_version` can tell beta.2 from beta.3.**

### Behaviour that changes what a definition means

- **Bounds filter readings.** On a `custom`/`specific` row, a decoded value outside `min`/`max`
  (beyond a 1e-6 tolerance) is **dropped**. A `std` row clamps instead (`README.md:546-569`). 4.x
  did the same with canair's *string* bounds, since it `atof`'d them
  (`wican-fw/components/autopid/autopid_config.c:157-167`, `autopid.c:147-163`). So emitting
  numbers keeps today's behaviour, while emitting strings on v6 would quietly lose it. 371 of the
  467 signals defined for the Ioniq `28kwh` car carry bounds.
- **The PID is the scheduling unit.** `period_ms` is per PID. `0` inherits the group's period
  (`README.md:571-576`). canair's per-PID `period` maps one to one.
- **The chip runs with headers and spaces on** (`ATS1;ATH1;ATST96;ATTP<p>;ATSH7DF;ATCRA`),
  re-asserted after every ELM327 app session and after any type change that followed a chain that
  moved the chip (`README.md:463-485`). The `ATS0` in canair's car init is dropped, which is
  harmless. 4.x AutoPID also ran headers on, so persisted `response_frames` counts were already
  being exercised under headers on.
- **The receive filter has a field of its own.** A row's `rxheader` is sent as `ATCRA<rx>` after
  its `init`. A row without one gets a bare `ATCRA` *only if the previous row set one*
  (`autopid_runner.c:427-441`). canair currently puts `ATCRA` inside `pid_init`, which v6 reads
  as a chain that moved the chip. That still works, but `rxheader` is the idiomatic form, and it
  also feeds the 29-bit reply hint (`ap_runner_reply_hint`, `:102-120`).
- **Cross-talk guard.** A reply whose service/identifier echo doesn't match `cmd` is discarded.
  A trailing count digit is ignored by pair alignment (`autopid_resp.c:116-170`). This is
  stricter than 4.x, and it also turns a queued undercount tail into a failed poll instead of a
  wrong value.
- **The table follows the current car.** `PUT` writes `config.json` *and* the current
  vehicle-store car's file (`autopid_config_save`, `autopid_config.c:618-631`). When the device
  meets an unknown car, it snapshots the live table into the previous car's entry and gives the
  new car only its standard rows, all disabled (`README.md:808-817`, `:881-894`). An upload made
  on the bench (simulator or no car) therefore follows the *bench* entry, and the real car comes
  up empty. The firmware authors hit exactly this.
- **Polling is off by default.** `enabled` defaults to false, and `specific_enabled` to true
  (`autopid_settings.c:42-45`). A correct table on a fresh device reads nothing.
- **No per-signal MQTT.** The parser reads no `send_to`/`type: MQTT_Topic`. The nearest v6
  equivalent is an `event_manager` rule: `on: autopid.param, match: {param}, do: mqtt.publish`.
  That fires **on change**, rate-limited by `min_event_interval_ms`, not on every poll
  (`mpc/event_manager/README.md:63-65`, `:105`).

## Measured: the bundled profiles against v6

All profiles were generated offline with `--include-unverified`, the widest output, at the
current `HEAD`. The 1,271 expressions were checked with v6's `expression_parser_check`, compiled
for the host (`mpc/expression_parser/expression_parser.c` plus a 10-line `esp_err.h` shim).

| Profile | PIDs | Signals | Longest name | Longest expression | Most signals per PID | Body size |
|---|---|---|---|---|---|---|
| Ioniq AE EV (`28kwh`) | 27 | 367 | 30 B | 52 | 33 | 64 KB |
| Ioniq AE EV (`38kwh`) | 23 | 220 | 28 B | 52 | 24 | 39 KB |
| Leaf ZE1 (`40kwh`) | 44 | 164 | 21 B | 69 | 98 | 31 KB |
| Xpeng G6 | 11 | 194 | 10 B | 20 | 150 | 32 KB |
| Kona OS EV | 15 | 162 | 26 B | 36 | 32 | 28 KB |

Results:

- **Every expression passes.** The highest byte referenced is `B227`, inside the 256-byte frame
  buffer (`AP_FRAMES_MAX`).
- **No string reaches its buffer.** The tightest margin is the Ioniq's 30-byte parameter name
  against 31 usable bytes. `init` reaches at most 27 of 95, and the car init at most 54 of 95.
- canair's evaluator and v6's grammar agree on the shared forms: `Bn`, `Bn:k`, `Sn`, `[Bx:By]`,
  `[Sx:Sy]`, `V`, the operator precedence, and that neither accepts `Sn:k`. v6 additionally
  rejects some forms canair's evaluator might accept:
  - an operator followed by unary minus (`3*-2`),
  - inverted ranges,
  - bit indexes above 7,
  - spans over 8 bytes.

  No bundled expression uses any of these today, so nothing guards against one appearing.

## Design

The work comes in three phases, each shippable on its own. Phase 1 needs no device.

### Phase 1 — `autopid write` emits a v6 table too (offline) — implemented

**1a. One shipping decision, two renderers.** `canlib/wican/autopid_profile.py::ship_profile`
decides what ships (status, verified-only, revisions, duplicate names, `send_to` topics, the
count digit, each PID's addressing) into a `ShippedProfile`. `render_v4` turns that into the 4.x
vehicle profile, whose output is byte-identical to before (a test pins the committed Ioniq
`28kwh` build). `canlib/wican/autopid_v6.py::to_v6` turns the same object into the v6 table:

| v6 | from |
|---|---|
| `name` | `<ECU>_<PID>` without the count digit, e.g. `BMS_2101` |
| `type` | `"specific"` (D1) |
| `cmd` | the request, count digit included |
| `init` | `ATSH…;ATFCSH…` (+ `1003`), without a trailing `;`. No `ATCRA` (moved to `rxheader`) |
| `rxheader` | the pinned reply id; `""` for an unpinned or auto-filter ECU (the runner restores the auto filter) |
| `group` | `"default"` (D2) |
| `period_ms` | the PID's period as a number, at least 1 |
| `enabled` | `true` |
| parameter `unit`/`class`/`min`/`max` | as for 4.x, but `min`/`max` as **numbers**, omitted when unset or unparseable |

**1b. Refused at generation, never truncated.** The limits live once in `autopid_v6.py`
(`FIELD_BYTES`, `SPECIFIC_INIT_BYTES`, `VEHICLE_BYTES` and the table bounds, as byte lengths).
Generation fails, listing every problem at once, when:

- a field would overflow its buffer;
- a PID would exceed 256 parameters, or the table 512 PIDs, 2,048 parameters or 256 KB;
- a chain would carry an EEPROM write (`ATPP` other than `ATPPS`, `ATSD`, `ATCV`, `STWBR`,
  `STSAVCAL`);
- a period is not a positive number;
- an expression is one v6's checker refuses (1c).

Truncating a name would rename a Home Assistant entity, and can create a collision. `ATH0`,
`ATS0` and `ATE1` are dropped from every v6 chain, as the firmware drops them, so the file shows
what actually runs.

**1c. Expression grammar parity.** `canlib/expression/lint.py::firmware_grammar_problems`
reports the forms v6's checker refuses but canair's evaluator decodes:

- an inverted range,
- a range over 8 bytes,
- a bit index over 7,
- an operand left without an operator (`B3 B4`).

Unary minus, the remaining difference, canair's evaluator already refuses. The verdicts were taken
from v6's own checker, compiled on the host. `validate pids` reports these as errors **for shipped
signals only** (status `active`/`slow`, enabled). The bundled profiles' unshipped ASCII identity
ranges (`[B4:B22]`) are not numeric device expressions.

**1d. CLI.** `canair wican autopid write` writes both files, `out/[<variant>/]autopid-v4.json`
and `autopid-v6.json`; `--firmware 4|6` writes one, and `--out PATH` needs it. The v6 file is the
PUT body plus two top-level keys the firmware's parser ignores (D3):

```json
{"vehicle": "…", "specific_init": "…", "groups": [], "pids": […], "filters": []}
```

`send_to` **warns and is dropped** from the v6 file, naming the signals and the rules-engine
alternative (D4). Every bundled car carries both files, and a test checks that.

**1e. Loading it by hand.** The v6 web UI accepts a dropped file in three shapes
(`index.html:3252-3260`):

- **A file with a `cars` list** fills the profile picker.
- **A single 4.x car** (`{car_model, init, pids}` with array parameters, which is
  `to_device_format()["cars"][0]`). It is staged like a picked profile: it replaces only the
  `specific` rows, keeps the user's standard/custom rows, and sets `vehicle` + `specific_init`.
  But it loses every period (`period_ms: 0`, so 1,000 ms) and every bound (string `min`/`max`).
- **Anything else is taken as a config document.** It replaces the whole table, including the
  user's own rows. `autopid-v6.json` loads this way.

The 4.x source-format file is none of these: its parameters are a dict, and the importer calls
`.map` on them, so it is rejected. `docs/bring-your-own-car/08-share.md` documents the v6 path
and its cost.

**Verification.** Every bundled car's `autopid-v6.json` was parsed by v6's own
`ap_config_parse` at `aa4c2b3`, compiled on the host with cJSON 1.7.18, and accepted with every
row, period and init intact. That was a one-off check, not a test: the suite cannot build C.

### Phase 2 — `autopid upload` / `download` / `diff` on v6

**2a. The firmware gate** (`canlib/wican/firmware.py`). The verdict for the `autopid-sync`
surface becomes version-aware:

| Firmware | Verdict |
|---|---|
| v6 `≥ 6.0.0-beta.3` | supported, with a "beta, unverified on a device" note until Q-A below is answered |
| v6 alfa, `beta.1`, `beta.2` | **refused**: byte indexes are off by one or more; upgrade |
| v6 `gated="auth"` / `"untrusted"` | **refused** with the remedy. Basic-auth support is a separate change |

This needs a v6 version parser for `6.0.0`, `6.0.0-beta.3` and `6.0.0-alfa.7` (pre-release words
are spelled `alfa`/`beta`). The 4.x path is untouched.

**2b. Upload is read → merge → write → verify** (`canlib/wican/autopid_v6_sync.py`, which does
the HTTP; the merge itself is a pure function in `autopid_v6.py`). The steps, in order:

1. **Current car.** `GET /api/autopid/vehicles` and show the current entry (name, key, whether
   `pending_profile`). With no current car, or one whose `profile` names another car, stop and ask
   (`--yes` passes). Never identify the car by VIN in output: the VIN is personally identifiable
   information (PII).
2. **Read.** `GET /api/autopid/config`.
3. **Merge.** Drop the existing `specific` rows (canair owns them, D1). Keep `std`/`custom` rows,
   all groups and all filters. Append canair's rows. Error on a parameter name shared with a kept
   row, listing both. The device would refuse the table anyway, and renaming would silently
   change an entity.
4. **Write.** `PUT /api/autopid/config`. Treat a non-200 as failure and show the firmware's error
   string, which names the entry.
5. **Verify.** Read back with `GET /api/autopid/config`. Compare canair's rows field by field
   after normalisation, and fail loudly on any difference. **Never trust the 200**: §6 of the
   compatibility plan shows a 200 can mean "wiped".
6. **Car init.** `PUT /api/autopid/vehicles/<key>` with `{profile, specific_init}`, which applies
   live. With no store entry, stage `vehicle`/`specific_init` in `/api/settings/autopid` and say a
   restart applies it. canair does not submit settings or reboot unless asked (D5).
7. **Report** `enabled`/`specific_enabled` from `GET /api/settings/autopid`. If polling is off,
   say so and how to enable it; do not flip it unasked (D5).

`--reboot` stays skipped on v6, which is already the policy.

**2c. Download and diff.** `normalize_device_profile` learns the v6 document. Only `specific`
rows count, grouped by `(init + rxheader, cmd)`. Periods and bounds are compared too, since both
now change behaviour. `show_diff` already works on the grouped form.

**2d. Optional pre-flight, `upload --test`.** `POST /api/autopid/test` with `{type: "specific",
cmd, init, rxheader, expressions: [...]}` for each PID (`mpc/autopid/autopid_http_test.c:55-134`)
shows the transcript and decoded values before the write. It parks the poller and needs the car
awake, so it stays opt-in.

### Phase 3 — follow-ups (separate plans)

- **`send_to` on v6.** Generate `event_manager` rules (`autopid.param` → `mqtt.publish`). The
  rule semantics are on-change, not every poll, so `plans/2026-10-18-per-signal-mqtt-streaming.md`
  needs a v6 section on whether on-change is good enough for a mean-power window.
- **Groups.** Map canair's `@groups` or period tiers onto v6 groups. These can be switched at
  runtime by an `autopid.group` rule action, for example polling `@charging` only while charging.
  But a v6 PID belongs to exactly one group, and canair's groups overlap.
- **Filters.** Emit the broadcast `signals/` (domain B) as v6 passive `filters`. Note the
  frame-data byte indexing and the chip-holding monitor window (`README.md:606-634`).

## Decisions

- **D1. Row type `specific`, all of them canair's.** This matches the web UI importer, so a
  profile picked in the UI and a canair upload replace each other cleanly. The cost: a user's
  hand-made `specific` rows are replaced. The alternative is a tag key on each row (unknown keys
  survive, since the file is stored verbatim), but the UI would still replace tagged rows on its
  next import. Recommend `specific`, plus a diff warning when a non-canair-looking `specific` row
  is about to go.
- **D2. One group (`default`), exact per-PID periods.** Groups come later (Phase 3).
- **D3. The v6 file is the config document plus `vehicle` and `specific_init` keys.** The
  firmware's parser ignores the two extra keys, so the file is a valid PUT body as it stands, and
  the car's two settings travel with it. A wrapper with a `car_model` key would be worse: the web
  UI would stage it as a 4.x car and import no PIDs. Dropped into the web UI, the file loads as a
  config document, which replaces the user's whole table; the docs say so.
- **D4. `send_to` on v6 warns and drops**, rather than refusing generation. The rest of the
  profile is useful without it.
- **D5. canair never enables polling or submits settings on its own.** It reports and gives the
  command. A later `--enable` flag can opt in.

## Tests

- `tests/test_autopid_v6.py`, all pure:
  - the field mapping on a fixture profile;
  - `period_ms`, `min` and `max` are numbers;
  - `rxheader` is split out and `init` has no `ATCRA`;
  - the 4.x outputs are byte-identical before and after;
  - each limit is refused at limit + 1 byte (with a UTF-8 unit case);
  - EEPROM-command refusal;
  - the merge keeps `std`/`custom` rows, groups and filters;
  - duplicate names against kept rows are refused;
  - read-back comparison catches a truncated field.
- `tests/test_expression_lint.py`: the four v6-rejected forms, from v6's vectors.
- `tests/test_wican_firmware.py`: the version parser and the per-version `autopid-sync` verdicts.
- Sync tests over a stubbed device (the suite already forbids non-loopback sockets):
  - the 200-but-wiped case fails verification;
  - no current car stops the upload;
  - polling-off is reported.

## Docs

- `docs/getting-started/connect-device.md`: the v6 table row for `wican autopid` changes from
  "Refused" to the version-split verdict. Also add the bench-upload vehicle-store warning and the
  "polling is off by default" note.
- `docs/concepts/wican-byte-index.md`: one paragraph saying v6 `≥ beta.3` uses the same
  frame of reference, and that the earlier betas do not.
- The `docs/reference/cli/wican*.md` pages are regenerated for `--firmware` (and `--test`).
- `.claude/skills/wican-hardware-and-protocol`: the v6 AutoPID facts that apply beyond this
  feature (headers-on baseline, cross-talk guard, table follows the current car).

## Open questions (need a v6 device)

- **Q-A. Does a canair table poll correctly on `beta.5`?** Upload the Ioniq `28kwh` table, then
  compare `GET /api/autopid/data` against a `canair read --save` of the same PIDs at the same
  moment. This is the acceptance test for Phase 2.
- **Q-B. Does the expected-response digit hold under the v6 runner?** Watch for failed polls on
  digit-bearing PIDs in `GET /api/autopid` stats. 4.x AutoPID ran headers on too, so it should;
  but v6's `obd_chip_send` has no queue in front of the chip.
- **Q-C. Does `1003` in a row's `init` behave?** It is a non-AT element. The chain sender
  forwards it, but whether the session's reply confuses the next request's parse is untested.
  This affects only ECUs with `session: true`.
- **Q-D. Is `class: "none"` harmless?** canair emits it for signals without an `ha_class`. What
  does v6's Home Assistant discovery do with it, compared with an empty string?
- **Q-E. Does the Ioniq's first contact create a store entry?** It depends on whether the car
  answers `0100`/`0902`. If not, the device runs the table on the no-car prelude, and step 1 of the
  upload must handle "no current car" as the normal case for this car.
