# Changelog

All notable changes to **canair** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Live monitor: clearer `s` / `n` recording keys.** The save modal is bigger
  and each action now explains itself and confirms the outcome:
  - **`s` labels the recording** while `--save` is active (every payload is
    already journaled and written on exit, so `s` only sets label/state/notes —
    it no longer looks like a disk write that isn't). Without `--save`, `s` is a
    one-off write of the payloads captured so far, as before.
  - **`n` finishes the current `--save` session and starts a fresh one** —
    writing the current session to a capture file *now*, then reopening a new
    labelled recording. The modals carry explicit titles/captions and the status
    line shows `s label` / `n new-session` while recording.
- **Live monitor: parameter highlight now tracks the *decoded* value, not the
  raw byte.** A parameter's name/value cells are highlighted only when its
  interpreted value actually changes — a byte flip that leaves the value
  unchanged (a bit the param doesn't map, or a sub-resolution change that rounds
  to the same value) still flashes the *byte* in the hex line but no longer
  falsely flags the parameter.

## [1.9.0] - 2026-07-30

### Added

- **`canair contribute` — one-command pull requests for profiles.** Sharing a
  reverse-engineered profile upstream no longer needs the manual fork/clone/
  branch/push dance. `canair contribute` copies the **active** profile into a
  managed fork checkout and opens a PR against `philipkocanda/canair` via the
  GitHub CLI (`gh`), which also handles a friendly browser-based sign-in
  (`gh auth login`). It works **regardless of where the profile is stored** —
  bundled in the repo, in `~/.config/canair/profiles/`, or an arbitrary
  `--path` bundle — because the destination is always `profiles/<name>/` inside
  the fork, so both a brand-new profile and edits to an existing one Just Work.
  - Runs `canair validate all` first (refuses to contribute a broken profile).
  - **Privacy pre-flight** (`canlib/pii.py`): scans for likely
    personally-identifiable / location data — VIN & ECU-serial identity DIDs,
    VIN-shaped capture payloads, and emails/phone-ish text in labels/notes/
    `car_model` — and requires confirmation before sharing.
  - Captures are included by default (with a size guard); `--no-captures` ships
    definitions only. `--dry-run` prepares the branch+commit without pushing,
    `--yes` skips prompts (agents/CI), `--json` emits the PR URL, `--repo-dir`
    uses an existing checkout instead of the managed fork. Aliased as
    `canair share`. When `gh` is missing/unauthenticated it prints install +
    login instructions and the equivalent manual steps.
  - The copy is an **overlay**: it replaces only the managed members
    (`ecus/`, `signals/`, `profile.yaml`, states, buses, and `captures/` when
    included) onto the destination, leaving unmanaged members (generated
    `out/`, `references/`, logs, and `captures/` when excluded) untouched — so a
    definitions-only PR touches only the definitions instead of showing every
    upstream capture as deleted.

- **Community health files and contribution guidance.** GitHub issue forms
  (bug / feature / profile-contribution), a pull-request template with
  profile/code checklists, a `CODE_OF_CONDUCT.md`, and an explicit
  public-domain (no CLA/DCO) statement in `CONTRIBUTING.md`. The contributing
  guidance is also split into two focused skills — `contributing-code` (the
  tool-engineering contract) and `contributing-profiles` (the
  upstream-contribution mechanics and profile quality bar) — so each task loads
  only what it needs.

- **Configurable physical-value bands for the reference-free scan
  (`canair hunt --physical` / `canair investigate`).** The plausibility scan that
  flags a raw byte whose scaled value lands in a named physical range (HV pack
  volts, mains RMS/peak, line frequency, 12 V rail) was hardwired to a ~400 V EV
  on a 230 V / 50 Hz grid, so on an 800 V architecture or a non-EU grid it
  silently failed to flag the real signal. Bands are now overridable across two
  axes, each owned where its truth lives:
  - **Vehicle axis** — a profile's new **`physical_bands:`** block overrides
    `hv_pack` / `rail_12v` by key (or adds a custom band). Declare only what
    differs, e.g. `physical_bands: { hv_pack: [450, 850] }` for an 800 V pack.
    Validated by `canair validate` (2-element `[low, high]`, `low < high`).
  - **Grid axis** — a new user-config **`grid_region`** key
    (`EU`/`UK`/`US`/`JP`/`CN`/`AU`, case-insensitive) selects the mains-voltage
    and line-frequency bands, since the same car charges on different grids by
    region. The first physical scan with no region set offers to set one (a
    one-time TTY prompt, or a single stderr note when piped).

  Precedence per band: `physical_bands` (final say) > `grid_region` preset >
  built-in default. The bundled `ioniq-2017` profile is unaffected (defaults
  reproduce the previous five bands). See
  `plans/2026-07-29-configurable-physical-bands.md`.

- **Capture sessions record the canair `version` that wrote them.** Every
  recorded/imported session now carries a `version` field (the tool version at
  save time), stamped once at the single save choke point (`save_session`) so it
  covers every path — `query`/`monitor --save`, one-shot `scan`/`raw`/`discover`,
  and device-free `import uds` — and journal-recovered sessions alike. It's
  provenance for debugging a capture issue traced to a specific release, sitting
  alongside `transport`/`quality`. Surfaced in `canair captures uds --sessions`
  (a `· vX.Y.Z ·` tag) and its `--json`. Sessions recorded before this change
  simply have no `version` (the field is optional).
- **Per-ECU `wake:` block — profile-declared wake rituals for fast-sleeping
  ECUs.** Some modules (e.g. a Smart Key Module) power their CAN transceiver
  only briefly and sleep again within a second or two, so a single `10 01` wake
  races the sleep timer and follow-up reads return NO DATA. An ECU can now
  declare, in profile data, how to rouse it: `method: rapid_read` fires a cheap
  prime request densely (short per-prime timeout, `attempts`/`interval_ms`
  tunable) to hold the transceiver awake, then opens a session. Honoured by
  `session <ECU> --wake` on **both** transports (`slcan-tcp` and `wican-ws`).
  Edit it with the new **`canair pids set-wake ECU --method … [--prime-pid
  --attempts --interval-ms --sleep-timer-ms --session-mode --notes]`** (surgical,
  validated). Resolver in `canlib/wake.py`; schema `wake_fields` /
  `valid_wake_methods`. The bundled `ioniq-2017` SKM declares a `rapid_read`
  ritual (verified on-car).
- **`skm_wakeup` profile quirk gates the Ioniq SKM relay-wake command.** The
  `skm-wake` relay procedure (rouse the SKM, then close an ACC/IGN power relay
  via IOControl) uses Ioniq-specific relay DIDs/addresses, so it is now refused
  for profiles that don't declare the `skm_wakeup` capability under `quirks:`.
  Merely *reading* a fast-sleeping ECU is the make-neutral per-ECU `wake:` block
  above — no quirk needed. The bundled `ioniq-2017` profile declares the quirk.

- **`multi_did_max` profile setting** caps how many service-22 DIDs are combined
  into one multi-DID batch request (default `3`; per-ECU `multi_did_max`
  overrides). Previously the cap was hard-coded to 3 in two places; it is now a
  single, validated, per-profile/per-ECU knob. The bundled `ioniq-2017` profile
  raises it to `6` after on-car verification that IGPM answers all 7 of its
  BC01–BC07 DIDs in one multi-frame request with no truncation.
- **`multi_did: true` on the Ioniq SCC, ESC, and MFC ECUs** — verified on-device
  that they answer service-22 multi-DID batch requests (`22 D1 D2 …`) and split
  cleanly, so the monitor now batches their consecutive DIDs (IGPM was already
  batched; BCM/CLU/EPS/HVAC/SKM reject batching and stay single-read).

- **`canair query` prints a hint to use `canair monitor`** when run on an
  interactive terminal — `query` is a one-shot read, while `canair monitor`
  gives a live, continuously-refreshing view of the same query steps. The hint
  goes to stderr and is suppressed when piped or with `--json`, so machine
  output stays clean.

### Fixed

- **`canair monitor` now stops promptly on Ctrl-C / `q`** instead of hanging for
  seconds. The raw (`slcan-tcp`) monitor's pipelined poll runs in a background
  thread; on quit the in-flight poll is now interrupted at once (rather than
  waiting out every pending ECU's timeout, which `asyncio` then joined at
  shutdown). The `--save` journal is still reconciled on the way out, so no data
  is lost.

- **Live sessions now shut down gracefully on `SIGTERM`** (`kill` / `pkill -f
  canair`), the same clean path as Ctrl-C — closing the terminal, reconciling
  any `--save` journal, and releasing the device connection — instead of the
  default abrupt terminate. This means killing an orphaned/stuck session (e.g.
  one that another run just `--force`'d past) frees the WiCAN's single
  connection without a device reboot. The monitor handles `SIGTERM` and `SIGINT`
  identically.

- **`--force` now warns when it steals the device lock from a still-running
  process.** An orphaned/stuck canair session (e.g. a wedged `canair scan`)
  keeps the WiCAN's single connection open even after `--force` takes the lock,
  so the new session's connection can then time out. The heads-up names the PID
  and suggests clearing it (`kill <pid>` / `pkill -f canair`) rather than
  rebooting the device. No warning when the previous holder is already dead
  (its socket is gone too) or is the current process.

- **Live commands now handle connection failures and mid-session drops
  gracefully on both transports — never a bare traceback**, and never lose
  `--save` data. Transport-error handling is centralized in one classifier
  (`canlib/transport/errors.py`) used at connection setup *and* during a running
  session (monitor / scan / query), so a device that's silent, refuses the port,
  or drops mid-session (peer close / reset / broken pipe) always produces a
  clean, actionable message + a proper exit code. Highlights:
  - The message **states what actually happened** (timed out / connection
    refused / connection dropped by the device / no route to host /
    name-resolution failure) rather than a generic "didn't respond".
  - The raw (`slcan-tcp`) pre-flight now **says whether the device is otherwise
    online**: if its HTTP config API (port 80) responds but the SLCAN data port
    doesn't, it reports the device is up and its SLCAN socket is wedged (a reboot
    usually clears it) — versus a genuine offline/host/VPN problem.
  - A `--save` session that drops mid-run is **pointed at recovery** — its data
    is safe in the write-ahead journal (`canair captures uds --recover`).
  - The raw path previously caught only `ConnectionError` (so a socket
    `OSError`/`TimeoutError` or a python-can `CanError` from a peer-close
    tracebacked); it now catches the full transport-failure set, matching the
    WebSocket path. The raw live-monitor path is guarded too.

- **Raw (`slcan-tcp`) commands fail fast with an actionable alert when the
  device's SLCAN data port is unreachable** (the pre-flight above), instead of
  blowing out `socket.create_connection`'s timeout deep in the stack.

## [1.8.1] - 2026-07-29

### Added

- **`canair monitor` TUI enhancements** — deselect, an errors/log overlay, and
  byte→parameter change-highlighting:
  - **`esc` clears the parameter-selection cursor** (deselect), so `↑`/`↓` resume
    plain scrolling after selecting a row for in-place editing.
  - **`l` opens a scrollable errors/diagnostics log overlay** — a live view of the
    central `canair logs` event log (dropped/stale ISO-TP frames, timeouts, bus,
    decode, and internal errors), colour-coded by category.
  - **A changed payload byte now also highlights the parameter row(s) it decodes
    to**, with the same coverage-coloured background the byte itself gets
    (verified→`dark_green`, unverified→`dark_goldenrod`), linking a byte flash to
    the value it produced.

- **Extended (mixed) 11-bit and functional-TX addressing support** — the
  remaining multi-vehicle addressing gaps from
  `plans/2026-07-28-multi-vehicle-support.md`:
  - A new `normal_extended_11bit` addressing mode (ISO-TP extended/mixed 11-bit):
    an 11-bit header plus a per-ECU **target-address extension byte** carried in
    the ISO-TP payload, with a tester **source address** (default `0xF1`). Covers
    the BMW `0x6F1` tester scheme (i3/528i/M340d/Mini) and PSA/Stellantis
    diagnostics. Set per-ECU `addressing: {mode: normal_extended_11bit,
    target_address: 0x12}`.
  - A per-ECU **flow-control address override** (`addressing.fc_id`) for
    functional-TX / physical-RX ECUs (Renault, Mitsubishi Outlander): the request
    goes to the functional broadcast id but ISO-TP flow control is redirected to
    the ECU's physical request id (can-isotp otherwise sends it to the functional
    TX). Implemented as a thin stack subclass in a new
    `canlib/transport/isotp_stack.py`.
  - **`canair pids set-addressing ECU [--mode --target-address --source-address
    --fc-id --rx-id]`** — a surgical, validated editor for the make-specific
    addressing knobs. `canair ecu add` gains the same
    `--mode`/`--target-address`/`--source-address`/`--fc-id` flags so a 29-bit or
    extended-addressing ECU can be seeded offline in one atomic write.
  - **Negative `addressing.rx_offset`** is accepted (PSA/Stellantis `-0x20`), and
    the offline-registration id range widened to 29-bit ids.
  - Internally, `(tx, rx, mode, …)` is consolidated into a single resolved
    `EcuAddress` bundle (`canlib/addressing.py::resolve_ecu_address`) threaded
    through the raw transport, replacing the parallel per-ECU rx/mode maps.

- **`canair profile` (bare, no subcommand) is now an interactive picker on a
  TTY** — an arrow-key selector over the discovered profiles (↑/↓ move, Enter
  sets it as `default_profile`, q/Esc cancels). Piped or non-interactive it
  falls back to the plain list, so scripting is unaffected. Built on a new
  reusable `canlib.tui.select_from_list` helper.

### Changed

- **`canair profile show` now reports every bundle component** — it previously
  omitted `can_buses.yaml`, the `signals/` broadcast maps, the raw-CAN log store
  (`captures/can/`), and the `references/` directory. All are now listed
  alongside `ecus/`, `profile.yaml`, `captures/`, `vehicle_states.yaml`, and
  `out/`, each with a presence/summary line.

- **`notes` fields auto-format consistently** when written by the tooling
  (`canair pids`, `canair ecu add`, `discover --register`, identity/scan-log
  writes): a short note stays inline, a longer/multi-line one becomes a
  word-wrapped folded (`>-`) block. Both the text-based and ruamel-based writers
  share one policy (`canlib/yaml_rt.py`), and folding wraps only the note — it
  never reflows the rest of the file. Pass plain strings; no need to hand-format
  YAML block scalars.
- **QUERY mini-language PID matching is now boundary-anchored** (prefix *or*
  suffix) instead of substring-anywhere. A PID token matches when the stored PID
  *starts with* or *ends with* it, so `ECU:22` still selects the `22xxxx` service
  DIDs (prefix) and `ECU:BC03` still selects the stored `22BC03` regardless of
  service byte (suffix) — but a token that appears only in the *middle* of a PID
  no longer matches. Affects `captures`/`decode`/`correlate` selectors.

### Fixed

- `canair query`/`monitor` now accept an explicit `0x`-prefixed hex TX-id as a
  bare selector (e.g. `query 0x770`); a bare `770` stays rejected as ambiguous
  with a PID. The space-vs-colon guard is unified in one shared helper
  (`canlib.query.looks_like_pid`) so the query-step parser and the capture query
  hint no longer diverge.

## [1.8.0] - 2026-07-29

### Added

- **29-bit diagnostic addressing** — profiles can now declare a CAN addressing
  mode under `addressing.mode`: `normal_11bit` (default), `normal_29bit`,
  `normal_fixed_29bit` (the ISO `0x18DA{target}{tester}` convention used by
  Ford/VAG and many non-Hyundai makes), or `extended_29bit`. A per-ECU
  `addressing.mode` overrides the profile default. The raw (`slcan-tcp`) transport
  builds each ECU's ISO-TP stack for its mode (extended CAN frames transmit with
  the 29-bit flag), `canair discover` sweeps a 29-bit target-address range and
  computes 29-bit response IDs, and `tx_id`/`rx_id` accept full 29-bit values when
  a 29-bit mode is in effect. The Ioniq (11-bit) is unchanged. See
  `plans/2026-07-28-multi-vehicle-support.md` (Phase 3).
- **Profile `quirks:` list** — make-specific behavior toggles a profile opts
  into. The first is `hk_f1xx_minus_one` (Hyundai/Kia identity DIDs answer one
  less than requested, `22F188` → `62F187`); echo validation now tolerates that
  off-by-one only when a profile declares the quirk, so a genuinely misfiled frame
  on a make-neutral profile is flagged instead of silently accepted. The bundled
  `ioniq-2017` profile opts in. (Phase 4.)
- **`canair states`** — list and edit a profile's vehicle operating-state
  vocabulary (`vehicle_states.yaml`), the state-axis analogue of `canair bus`. A
  bare `canair states` lists each declared state with its description, whether
  it's auto-suggested (has a `when:` predicate), and how many `ecus/` entries
  reference it, and surfaces undeclared tokens (`--json` for machine output). The
  edit subcommands `add`/`rm`/`rename`/`set-description`/`set-predicate` modify
  the file surgically (comment-preserving, re-validated, reverted on failure via
  `canlib/states_edit.py`) — no more hand-editing `vehicle_states.yaml`.

### Changed

- **ISO-TP padding is profile-driven** — the byte used to strip/split padded
  multi-DID responses now comes from `isotp.tx_padding` (default `0xAA`) instead
  of assuming Hyundai's `0xAA`, so a make padding with `0x00`/`0xCC` batches
  correctly. (Phase 4.)

- **Vehicle-state names are now an UPPERCASE controlled vocabulary** (like the
  CAN-bus segment codes) — `SLEEP/PLUGGED/ACC/ACC2/READY/CHARGING` — plus a new
  **`ALL`** meta-token meaning "applicable/readable in every state" (the state
  analogue of the `ALL` CAN-bus code; documentary, no predicate). A state name is
  a single alphanumeric word (`DEEP SLEEP` → `DEEPSLEEP`). Input is normalized to
  uppercase everywhere, so any casing on `--state`/`--prereq`/`--states` is
  accepted, and validators compare tokens case-insensitively. `vehicle_states:`
  fields in the per-ECU files render as compact inline flow lists
  (`[SLEEP, PLUGGED]`) for readability. Bundled profiles were migrated; migrate a
  legacy lowercase/block-style profile with
  `scripts/migrate_states_uppercase.py` (historical captures keep their free-text
  state and normalize on read).

## [1.7.0] - 2026-07-29

### Added

- **Profile-driven CAN response addressing** — the diagnostic response address
  is no longer a hardcoded `TX + 8`. A profile can set `addressing.rx_offset` in
  `profile.yaml` (e.g. `0x80` for XPeng, where request `0x704` → response
  `0x784`), and an individual ECU can override it with an explicit `rx_id` in its
  `ecus/*.yaml`. Resolution precedence: per-ECU `rx_id` → profile
  `addressing.rx_offset` → the conventional `0x08`. Centralized in
  `canlib/addressing.py` and threaded through the ECU registry, the raw
  (`slcan-tcp`) transport, discovery, and capture-reference resolution;
  `canair validate` type-checks both new fields. The bundled `ioniq-2017`
  profile is unchanged (defaults reproduce `TX + 8`). Part of
  `plans/2026-07-28-multi-vehicle-support.md` (Phase 2).
- **`canair ecu add --rx-id`** — set a per-ECU CAN response-address override when
  registering an ECU offline (for a single ECU whose response addr doesn't follow
  the profile's `addressing.rx_offset`).
- **Bundled `xpeng-g6` seed profile** — a device-free profile transcribed from the
  upstream WiCAN community profile (all PIDs `draft`/unverified), demonstrating
  non-`+8` addressing (`addressing.rx_offset: 0x80`, request `0x704` → response
  `0x784`) and serving as its regression fixture.
- **CAN bus `bitrate`** — `can_buses.yaml` bus entries take an optional
  `bitrate` field (segment bus speed in bit/s). `canair bus` renders it as a
  `SPEED` column (e.g. `500 kbit/s`) and includes it in `--json`;
  `canair validate can-buses` errors on a non-positive-integer value; exposed as
  `BusDef.bitrate` on the loader. The bundled `ioniq-2017` profile now records
  the Hyundai/Ioniq figures (P-CAN/C-CAN/MM-CAN/H-CAN/D-CAN = 500 kbit/s,
  B-CAN = 100 kbit/s).
- **Diagnostic CAN (`D-CAN`) segment** added to the bundled `ioniq-2017`
  `can_buses.yaml` vocabulary — the D-CAN bus exposed on the OBD-II port for
  UDS/KWP2000 requests (500 kbit/s).
- **`canair captures migrate-rx`** — rename the persisted capture field `ecu` →
  `rx` in a profile's capture files (at the capture level and inside
  `scan_results.responding[]`). Idempotent, `--dry-run`/`--json` supported.
- **`canlib/capture_types.py`** — `TypedDict`s for the on-disk capture shapes
  (`CaptureFile`/`CaptureSession`/`CaptureRecord`/`ScanResults`/`RespondingEntry`/
  `Quality`), mirroring `captures_schema.json`. The session builders and the
  capture I/O/journal are now typed against them (enforced by `ty`).

### Changed

- **`canair validate captures` only prints files that have something to report.**
  Clean capture files no longer emit an `OK` line — a profile with hundreds of
  files would otherwise bury the actual warnings/errors. The final
  `All N files valid.` / total-errors summary still reports the full count.
  Repeated soft warnings within a file are also grouped: each distinct warning
  message prints once per file as `⚠ <message> — N captures:` followed by a
  capped, indented list of locations (`(+N more)` when truncated). Applies to
  every capture lint (missing-time, echo mismatch, non-hex, quality, state
  vocabulary) and to the `--strict` errors. Warning/error counts are unchanged.
- **CAN bus segment codes renamed to `*-CAN` identifiers** (Hyundai/Kia):
  `B`/`P`/`C`/`M`/`H`/`D` → `B-CAN`/`P-CAN`/`C-CAN`/`MM-CAN`/`H-CAN`/`D-CAN`, and
  the gateway code `All` → `ALL` (uppercase, to match the `*-CAN` style). The
  old single letters were hard to grep for; the new forms are unambiguous. The
  bundled `ioniq-2017` `can_buses.yaml` vocabulary and every ECU `can_bus:` list
  are updated. **`canair pids set-can-bus` now writes the `can_bus:` list in the
  readable flow (inline) form** — `can_bus: [B-CAN, P-CAN]` instead of a
  multi-line block list; existing block-style lists are rewritten to flow style
  in place. Bus naming is per-profile vocabulary, so other profiles are
  unaffected.
- **`canair bus` counts an `ALL`-tagged gateway on every segment.** An ECU on
  the gateway code `ALL` bridges every declared bus, so it is now counted on each
  segment (including the diagnostic bus) rather than only on a standalone `ALL`
  row — e.g. the Ioniq IGPM now shows on D-CAN. A footnote reports how many
  gateway ECUs were fanned out, and `--json` gains a `gateway_ecus` count. The
  rule lives in `can_buses.expand_bus_membership` (`ALL` matched
  case-insensitively).
- **The persisted capture field `ecu` is renamed to `rx`.** It holds the ECU CAN
  *response* address (RX = request TX + 8), not an ECU name — the old name
  collided conceptually with the resolved short name (`"BMS"`) that the in-memory
  loader exposes under `ecu`. Bundled capture files are migrated; readers accept
  the legacy `ecu` key as a fallback (via `capture_io.capture_rx`), so
  un-migrated files and stale journals still load. Run `canair captures
  migrate-rx` to rename a profile's files. The in-memory loaded-entry key `ecu`
  (resolved short name) is unchanged.

## [1.6.0] - 2026-07-28

### Changed

- **The live monitor is now its own top-level `canair monitor` command**
  (promoted out of `canair query --monitor`). It takes the same positional query
  steps (`canair monitor BMS:2101`, cross-ECU `"VCU:2101 BMS:2101"`) plus
  `--interval SECONDS` (poll period, default 5.0 — adjustable live in the TUI with
  `=`/`-`), the keep-modes (`--keep-unique` default, `--keep-all`, `--keep N`),
  and `--save`/`--label`/`--state`/`--notes` recording. On a TTY it opens the
  scrollable Textual monitor; piped it polls silently until Ctrl+C.

  **Breaking:** `canair query` no longer has a `--monitor` flag (nor the
  monitor-only `--keep-*`/`--rulers` options); passing `--monitor` now errors with
  a pointer to `canair monitor`. `query`'s own `--save` and metadata flags are
  unchanged. Move any `canair query --monitor …` invocation to `canair monitor …`.

## [1.5.1] - 2026-07-28

### Added

- **`canair pids add-pid ECU PID`** — create a bare, parameter-less PID (a
  discovery/identity placeholder like a not-yet-decoded `21F2`) so a bare-ECU
  query (`canair query ECU`) polls the page. A bare-ECU query polls only
  *defined* PIDs, so an undiscovered page must be seeded here first. Defaults to
  `draft` (swept/queryable but not shipped to the device), scaffolds the `pids:`
  section if the ECU has none, and refuses an existing PID. Surgical,
  comment-preserving, schema-validated + auto-reverted.
- **`canair captures merge-driver`** — a git merge driver for the append-only
  dated capture files (`captures/YYYY-MM-DD.json`). Two machines that record on
  the same day each append a different session to the same file's tail, which
  git's line-based merge can't reconcile (the near-identical record boilerplate
  misaligns the diff and splits conflicts *inside* records). The driver resolves
  this class automatically by **unioning the two sides' session lists** (shared
  history collapses, disjoint appends are kept, a genuine divergent edit still
  falls back to conflict markers). Wired via a `.gitattributes` rule
  (`profiles/*/captures/*.json merge=canair-captures`); run
  **`canair captures merge-driver --install`** once per clone to register the
  `[merge "canair-captures"]` stanza in `.git/config` (git never loads a driver
  command from a tracked file, so until a clone installs it merges simply fall
  back to markers). Pure union in `canlib/captures_merge.py`.

### Changed

- **`canair pids upsert-param` echoes a capture-range sanity-check.** After a
  successful write it decodes the new expression against the PID's existing
  captures and prints the resulting value range — so a wrong byte offset (e.g. a
  WiCAN `Bnn` landing on an ISO-TP PCI framing byte) surfaces immediately as a
  nonsensical `constant`/error instead of being silently persisted. It never
  fails the write.
- **`canair wican mode set` aligns the config transport** to the new device
  mode (`slcan` → `slcan-tcp`, `elm327` → `wican-ws`), printing the `old -> new`
  change (or reporting it is already aligned) — closing the foot-gun of
  switching device mode and then hitting the wrong backend. Modes with no
  request/response transport are left untouched; `--no-transport` opts out. The
  usage line now surfaces the valid mode choices.
- **`canair config set` gives before→after feedback** (`key: old -> new`, or
  `already … (unchanged)` with no rewrite), validates enum keys
  (`transport.type`/`wican_model`) up front with a clear `valid: …` error, warns
  on an unrecognized key, and gains a richer `--help` with worked examples.

### Fixed

- **`canair pids` no longer leaves an empty `parameters:` block.** Removing a
  PID's last parameter dropped the map to an empty `parameters:` that parses to
  `None` and fails the schema; the now-empty block is dropped entirely, leaving a
  valid identity-only PID.

## [1.5.0] - 2026-07-28

### Added

- **`canair bus`** — read-only list of the active profile's physical CAN bus
  segments (`can_buses.yaml`): each code with its human name, description, and
  per-bus ECU count; flags undeclared codes + unbussed ECUs; prints the source
  file path. The read-only companion to `canair pids set-can-bus`.
- **`canair ecu <ECU> pids`** — a compact per-PID view listing every defined PID
  with its latest *decoded* state (never raw hex), pointing at
  `canair captures`/`canair decode` for full history (`--json`).
- **`canair logs`** — view the central, size-rotated diagnostics event log
  where transport faults (dropped/stale ISO-TP frames, timeouts, bus/decode
  errors) and internal exceptions are collated. `-n` tails the last N lines,
  `--path`/`--json`/`--clear`.
- **Transport diagnostics + capture provenance.** A per-exchange outcome tally
  (drops/stale/no_data/bus/decode, classified via `classify_response`) is
  attached as `.diag` on both terminals and the raw UDS client. The live
  `--monitor` status line gains a **`drops`/`err`** health indicator and
  **`captured/uniq`** counters, and each recorded `--save` session stores its
  acquisition **transport** and a **quality** footprint (surfaced in
  `captures --sessions` and validated — a degraded-transport session now soft-
  warns in `validate captures`).
- **First-class `variable_length` PID field.** Some PIDs legitimately return a
  variable number of trailing bytes, so a shorter payload is not evidence of a
  truncated read. Declare that intent with
  **`canair pids set-pid-variable-length ECU PID {true|false}`** (schema-
  validated; `false` clears it) so the truncation guard doesn't flag them.

### Changed

- **`canair decode` accepts the shared mini-language QUERY** (`BMS:2101`,
  `BMS` = all its PIDs, cross-ECU `"MCU:2102 VCU:2101"`; the two-token
  `BMS 2101` still works). The value-range/`--compact`/`--json` views support
  multi-PID; the single-PID analysis modes
  (`--corr`/`--plot`/`--stats`/`--discriminate`/`--find-mirrors`/`--try`/
  `--dump-bytes`) require one PID.
- **`canair captures` default list view is capped** at the most recent
  `--limit N` captures (default 50; `--limit 0` = no cap) with a loud footer
  reporting hidden history, so a bare `captures BMS 2102` can't blow the context
  window (truncation surfaced in `--json` too). `--latest` is now a plain flag
  that reads its ECU/PID from the QUERY.
- **Renamed `states.yaml` → `vehicle_states.yaml`** (a legacy `states.yaml` is
  still read as a fallback so existing profiles keep working).
- **`correlate` and `decode --corr` gain a `--method` cheat sheet** in `--help`
  (pearson/spearman/cramers_v/mutual_info — which coefficient when).

### Fixed

- **Truncated multi-frame ISO-TP reads are rejected at capture time.**
  Multi-frame reads over the ELM327 (`wican-ws`) terminal occasionally dropped a
  consecutive frame, yielding a payload short of the declared length whose bytes
  were misaligned after the gap (the -35 °C `LDC_TEMP` case). `parse_uds_response`
  now compares the reassembly to the ISO-TP First-Frame declared length and
  rejects a short read (`truncated ISO-TP: got N, declared M`) so `--save` never
  stores it. Generic — no per-PID length table.
- **`canair captures uds --delete`** — remove captures matching a QUERY via
  canair's own helpers (refuses a bare `--delete`, `--dry-run` preview,
  interactive confirm unless `--yes`) instead of hand-editing `captures/`.
- **`wican-ws` pre-flight reachability check** — a clear alert instead of a
  silent hang when the device is unreachable.
- **`pids_edit` resolves the ECU key case-insensitively** when writing.
- Corrected historic ioniq-2017 capture data affected by the transport bugs
  above (including recovery of full-length `21F2` payloads).

## [1.4.0] - 2026-07-27

### Changed

- **Diagnostic captures are now stored as JSON** (`captures/YYYY-MM-DD.json`)
  instead of YAML. JSON parses ~60x faster, which was the dominant cost of every
  history-consuming command — `ecu`, `coverage`, `decode`, `correlate`, `hunt`,
  `investigate`, `captures`, `validate captures`. On the bundled profile
  `ecu <name> --captures` drops from ~1.0 s to ~0.3 s and `coverage` from ~0.9 s
  to ~0.2 s. There is **no dual-format read path**: a profile created before this
  release fails fast with a clear error pointing at the new migration command
  (below). Capture files are still written/edited only by the tool; the schema
  (`canlib/schema/captures_schema.json`) is unchanged (it validates the parsed
  structure), and the human companion doc moved to `captures/SCHEMA.md`.
- **The live `monitor` TUI repaints far more cheaply.** The monitor rebuilt its
  entire body — every ECU, PID, parameter table and hex/history line — on every
  poll cycle *and* every mid-cycle partial resolve, re-running the per-byte Rich
  `Text` assembly and re-parsing each parameter's byte-index expression every
  time, even for PIDs whose values were unchanged. Each PID's rendered block is
  now cached and keyed on the inputs that affect its output, so unchanged PIDs
  are reused instead of re-rendered (a ~7x cheaper repaint when nothing changed
  between paints — the common case for a slow-timeout or partial-resolve tick).
  Output is byte-identical.
- **`canair ecu` list defaults to grouping by CAN bus** and gains per-column
  sorting (`--sort {bus,name,tx,proto,pids,verif,caps}`); the list was trimmed
  (dropped the redundant alias suffix and PARM column). Capture-count columns are
  now opt-in via **`--captures`** (they require reading the capture store), so a
  bare `canair ecu`/`ecu <name>` is instant.

### Added

- **`canair captures migrate`** — one-time conversion of a profile's legacy
  per-day capture files (`captures/*.yaml`) to JSON. Each file is round-trip
  verified before its YAML is replaced (`--dry-run` to preview, `--json` for
  machine output). `scripts/migrate_captures_to_json.py` does the same across all
  discovered profiles.

### Fixed

- **`format_value` no longer crashes on `None`/`inf`.** A decoded value of `inf`
  raised an uncaught `OverflowError` during a render; `None` and non-numeric
  values are now handled explicitly.

## [1.3.3] - 2026-07-27

### Changed

- **Cross-signal analysis is much faster on mature profiles.** `investigate`,
  `correlate`, and `hunt` share a time-alignment engine whose O(P²) pairwise
  joins dominated wall-clock time. Three fixes remove the bulk of it: (1) series
  are joined on pre-sorted float epoch arrays instead of re-sorting and doing
  `datetime` arithmetic on every pair (`canlib/align.py` `PreparedSeries`);
  (2) `build_byte_series` reconstructs each capture frame once and indexes it,
  instead of re-parsing every payload once per byte offset; (3) capture
  timestamps parse via a direct fast-path (with a small date cache) rather than
  `strptime` in the hot loop. `investigate <ECU> <PID>` drops from ~20 s to
  ~0.2 s, `investigate … --bits` from >2 min to ~0.2 s, and
  `correlate --find-mirrors` from >2 min to ~14 s (with an early-exit
  equality join). Output is unchanged.

## [1.3.2] - 2026-07-27

### Changed

- **Read commands are dramatically faster on mature profiles.** YAML loading now
  uses the libyaml-backed C parser (`CSafeLoader`) everywhere it was still on the
  pure-Python parser — capture files, coverage/validate scans, and the profile/
  config/states loaders. On a profile with tens of thousands of captures this
  cuts commands that scan the capture store (e.g. `canair ecu <name>`, `coverage`,
  `validate captures`) from several seconds to a fraction of a second (~6-10x
  faster YAML parse, no behavioural change). Centralised in `canlib/yaml_io.py`.


## [1.3.1] - 2026-07-27

### Added

- **Per-profile physical CAN bus vocabulary.** ECUs can now declare which CAN
  bus segment(s) they sit on via a `can_bus:` field (edited with the new
  `canair pids set-can-bus ECU CODE …`), validated against a per-profile
  `can_buses.yaml` that maps each bare code to a human name + description (bus
  naming is vendor-specific, so it lives per profile — Hyundai/Kia use
  `B`/`P`/`C`/`M`/`H`/`All`). `canair ecu` gains a **BUS** column with
  `--sort {name,bus}`, and the detail view resolves each code to its full name.
  `canair validate can-buses` checks the vocabulary; `profile create` scaffolds a
  starter `can_buses.yaml`. Loader `canlib/can_buses.py`, schema
  `canlib/schema/can_buses_schema.yaml`.

### Changed

- **`canair wican autopid write`/`upload`/`diff` now default to verified-only.**
  Emitting the AutoPID profile previously defaulted to *all* parameters and you
  opted in to a verified subset with `--verified-only`. The safer default is
  reversed: only **verified** parameters ship by default, and you opt in to
  in-progress candidates with the new **`--include-unverified`** flag. The old
  `--verified-only` flag is still accepted as a no-op for back-compat.
- **`canair --help` groups the command list by category.** The top-level command
  map is now organised under bold headers (Live device / Analysis / Authoring /
  Import · export / Setup), with anything uncategorised under Other. The
  `[UDS]`/`[CAN]` domain tags were dropped from this overview (and the generated
  CLI reference index) — the list is grouped and each command's own `--help`
  still states its domain, so repeating the tag there was redundant. Grouping is
  driven by a single central map (`canlib/commands/_categories.py`).
- **`canair update` checks out the advertised release tag** instead of
  fast-forwarding the current branch to its HEAD, so the installed code is
  exactly the released version rather than whatever unreleased commits sit on
  `main`. When the latest tag can't be determined (GitHub unreachable) it now
  refuses to update rather than guess.

### Fixed

- **`pids` verified/enabled toggle preserves expression quoting.** The query
  TUI's toggle re-rendered the whole param when flipping a boolean, stripping
  hand-added quotes on the untouched `expression` field (e.g. `"B10:1"` →
  `B10:1`). A new surgical single-field editor rewrites only the boolean line.
- **`hunt` skips implausible float-reinterpretation noise.** Reading integer byte
  runs as IEEE floats produced absurd magnitudes (~5e-36 / ~1e30) that surfaced
  as weak spurious hits in the ranking; such float interpretations are now
  filtered (integer reads are never filtered).

## [1.3.0] - 2026-07-27

### Added

- **`canair update` reports the install context and warns on drift.** With both
  a `uv tool install` copy and a working clone on the machine, a bare `canair`
  runs the installed snapshot while `uv run canair` runs the repo working tree —
  and the two silently drift once you edit/pull the clone. `canair update` now
  reports **which copy is running** (repo working tree vs the `uv tool install`
  snapshot vs another install) and **warns when the installed tool copy's
  version differs from the source clone's `pyproject.toml`** ("out of sync": a
  bare `canair` would run different code than `uv run canair`). `--json` gains an
  `install` block (`running_origin`/`running_version`/`clone_version`/
  `tool_version`/`out_of_sync`); the same out-of-sync warning also surfaces in
  `canair status`. New library module `canlib/install_context.py`.

- **Typed (multi-modal) signal analysis.** Parameters can now declare an optional
  **`type:`** (`enum`/`bitmask`/`ascii`/`date`/`bcd`/`struct`) with companion
  `values:`/`bits:`/`fields:` maps, giving canair a first-class model for signals
  that aren't a number on a line — fan/mode enums, day-of-week schedule masks,
  part-number strings, manufacture/schedule dates, and multi-field records. The
  WiCAN `expression` stays a pure float (device output and numeric analysis are
  unchanged); the type is a **parallel decoding** produced by the new
  `canlib/decode_value.py` (which also becomes the shared home for the date/BCD/
  ASCII logic previously siloed in the identity reader). Author with
  `canair pids upsert-param --type … --value RAW=LABEL / --bit INDEX=LABEL`
  (passing only `--value`/`--bit` infers the type); `canair decode` renders the
  decoded labels/flags/dates. `canair validate pids` enforces the new fields.
- **Categorical statistics.** `canlib/stats.py` gains **Cramér's V** and
  (normalized) **mutual information** for nominal association — the right measure
  for a mode/flag/enum byte, where Pearson/Spearman don't apply. Wired in as
  `--method cramers_v|mutual_info` on `canair correlate` and `canair decode
  --corr`, and used automatically by `canair decode --discriminate state` for
  typed enum/bitmask params (Cramér's V vs the interval-scale F).
- **`canair investigate --events --field NAME`.** Collapses a single typed param
  into one logical signal, emitting one transition per change of its *decoded*
  value (e.g. `fanMAX (45) → fan1 (40)`) instead of scattered per-byte edges —
  the fastest way to read a schedule/mode/date field's timeline. Struct-typed
  params render as a single record (`{days=tue, hour=7, minute=30}`).
- New concept doc `docs/concepts/typed-signals.md` and an expanded
  `docs/bring-your-own-car/06-analyze.md` (categorical analysis + the
  toggle→re-read→diff workflow for decoding settings the head unit *writes*).

- **Live-monitor recording controls.** `canair query --monitor --save` now shows a
  blinking `● REC` in the status line while a recording is active, and adds an
  **`n`** key to close the current capture segment (reconciling it to its own
  capture file) and start a fresh, newly-labelled one — so one monitor run can
  produce several independently-labelled sessions (e.g. parked → driving →
  charging) without stopping. The existing **`s`** save modal now states which
  segment it is labelling. Journal stems gained microsecond precision so a
  same-second segment rotation can't collide.

## [1.2.0] - 2026-07-25

### Added

- **`canair update` + automatic update checker.** canair now checks GitHub once
  a day (in a background daemon thread — never blocking a command, and fully
  offline-safe: any network failure is silently ignored) for a newer released
  version, caches the result to `~/.config/canair/update_check.json`, and prints
  a one-line "update available" notice with a changelog link on the next run
  (also surfaced in `canair status`). The new **`canair update`** command
  upgrades in place while keeping the git-clone install: it locates the source
  clone (via uv's tool receipt, falling back to the package repo root), reports
  current vs latest version, and — after confirmation — runs `git pull --ff-only`
  + `uv tool install . --reinstall`. Flags: `--check` (report only), `--yes`
  (skip the prompt), `--json`. Refuses a dirty clone and degrades to manual
  instructions when no clone/`uv` is found. Disable the auto-check with
  `check_for_updates: false` in config or the `CANAIR_NO_UPDATE_CHECK` env var.
  New library module `canlib/update_check.py`.

- **`canair investigate can FILE --id 0xID` — explain one arbitration ID
  (Stage 2c).** The domain-B analogue of `investigate uds`: for every varying data
  byte of one arbitration ID in a raw broadcast-CAN frame log, reports its
  strongest cross-ID anchor (Pearson r + linear fit `y=m·x+c`) and a physical-unit
  guess, ranked strongest first (`--bits` for toggling bits, `--json`). Frames have
  no defined-param mapping or power-state metadata, so the report is anchor-centric.
  `investigate` becomes a `uds`/`can` kind group like `correlate`/`hunt`; a bare
  `canair investigate MCU 2102` still defaults to the diagnostic (`uds`) path.
- **`canair correlate can --find-mirrors` — cross-arbitration-ID frame mirrors.**
  Reports frame byte/bit positions time-aligned *equal* ACROSS arbitration IDs —
  a signal broadcast on two IDs (e.g. wheel speed on `0x386` and `0x331`, verified
  on the real uhi22 Ioniq-28 log). The domain-B analogue of the diagnostic
  `correlate --find-mirrors`; `--bits` for bit-level.
- **Broadcast signal definitions + DBC interop (Stage 4).** The domain-B analogue
  of a PID's parameters — a DBC-compatible **linear** signal model in
  `signals/<bus>.yaml` (arbitration ID → named signals: `start_bit`/`length`/
  `byte_order`/`scale`/`offset`/`min`/`max`/`unit`/`verified`):
  - **`canair signals`** (`list` / `upsert` / `rm`) — surgical, comment-preserving,
    validated + auto-reverted editing (via `canlib/signals_edit.py`); never
    hand-edit the sidecar.
  - **`canair import dbc <FILE>`** — import a DBC's broadcast signals into
    `signals/` (cantools, `strict=False` for real-world overlapping-signal DBCs;
    `--bus`/`--tx-ecu`/`--ids`/`--dry-run`). Verified on the real
    `uhi22/Ioniq28Investigations` DBC (287 signals).
  - **`canair export dbc`** — write `signals/` back to a DBC for SavvyCAN/cabana/
    Wireshark (`--bus`/`--verified-only`/`-o`); round-trips losslessly with
    `import dbc`. Adds `cantools` as a dependency.
- **SavvyCAN GVRET CSV import (Stage 3).** `canair import can` (and
  `correlate can`/`hunt can`) now read SavvyCAN **GVRET** frame logs — the
  `Time Stamp,ID,Extended,Dir,Bus,LEN,D1..D8` format (microsecond timestamps).
  Since GVRET also uses `.csv`, the format is auto-detected by header sniff
  (distinguished from python-can CSV) or forced with `--format gvret`. This
  unlocks importing the real `uhi22/Ioniq28Investigations` Ioniq-28 drive logs
  (verified: a 75k-frame, 86-ID log imports and analyses end-to-end).
- **`canair hunt can FILE --id 0xID --against 0xREF:rN` — "which frame byte
  is this signal?" (Stage 2b).** Sweeps every byte offset × interpretation
  (u8/i16/f32/… × endianness) of one arbitration ID's frames in a raw broadcast-CAN
  log, time-aligns each against a reference frame byte in the same log, and ranks
  by |r| with a linear fit + physical-unit guess — the frame-domain analogue of
  the diagnostic byte hunt, reusing the same interpretation sweep and ranking. Hits
  are raw-CAN `rN` labels (no PCI, no WiCAN expression); `--promote` is not
  supported for frames yet (they're defined in the linear `signals/` model, Stage 4).
  The diagnostic WiCAN hunt path is unchanged.
- **`canair correlate can FILE` — correlate raw broadcast-CAN frame bytes
  (Stage 2).** Reads a native frame log's per-byte series (`0xID:rN`, `--bits` for
  `0xID:rN.k`, `--id` to filter arbitration IDs) and runs the *same* correlation
  core as diagnostic captures — ranked cross-arbitration-ID pairs (clustered),
  or every byte vs an `--against 0xID:rN` reference — so broadcast frames flow
  into the analyzer (`--json`, `--min-r`/`--min-n`/`--top`/`--method`/`--join-tol`).
  The WiCAN `Bn` diagnostic path is untouched (byte-identical); frame bytes are a
  distinct raw-CAN space (no PCI, no WiCAN expression). New module
  `canlib/frame_series.py` (`plans/2026-07-24-raw-can-analysis.md`).
- **`canair import can` — raw broadcast-CAN frame-log import (Stage 1).** Reads a
  raw frame log (`.asc`/`.blf`/python-can `.csv`/candump `.log`/`.trc`, auto-detected
  by extension) via python-can's readers, stores it **verbatim** in the profile's
  `captures/can/` and indexes its metadata (frame count, distinct arbitration IDs,
  bitrate, date, label/state/notes/source) in `captures/can/index.yaml` — high-volume
  logs stay native rather than exploding into the `captures/*.yaml` schema. Flags:
  `--format`/`--label`/`--state`/`--notes`/`--source`/`--bitrate`/`--date`/`--force`/`--json`.
  List imported logs with **`canair captures can`**. SavvyCAN GVRET CSV import is
  Stage 3; `import dbc` is Stage 4. `scripts/fetch_can_corpus.py` fetches the
  reference Ioniq-28 corpus into a gitignored `references/can/`. New library module
  `canlib/can_logs.py` (`plans/2026-07-24-raw-can-analysis.md`).
- **Raw-CAN broadcast domain — Stage 0 scaffolding.** Groundwork for analysing
  passively-broadcast CAN frames (`plans/2026-07-24-raw-can-analysis.md`): two new
  tool-owned schemas (`canlib/schema/signals_schema.yaml` for DBC-compatible
  linear broadcast signal maps under `signals/<bus>.yaml`, and
  `can_index_schema.json` for the `captures/can/index.yaml` raw-log index),
  `canair validate signals` / `canair validate can` targets (both gracefully skip
  when absent, included in `validate all`), `Profile.signals_dir` / `.can_dir` /
  `.can_index_file` path accessors, and a `canair import` command scaffold
  (`import can` / `import dbc` — surface registered; handlers land in Stages 1/4).
- **`--notation {wican,isotp,torque,bix}` on the analysis commands.**
  `correlate`, `hunt`, `investigate`, `coverage`, and `decode`
  (`--discriminate`/`--find-mirrors`) can now render raw-byte labels in the
  notation you prefer — ISO-TP payload index (`iN`), Torque/OBDb letters, or bix —
  instead of only WiCAN `Bnn`. It is **display-only**: named parameters are
  untouched, and `--json` / `--promote` always emit the canonical WiCAN expression
  (the promotable/firmware form). Set a persistent default with
  `canair config set display.byte_notation NAME`. Backed by a new typed byte model
  (`canlib/notation.py`: `ByteRef`, canonical in ISO-TP space, with WiCAN/Torque/bix
  as derived views and a `RAW_CAN` space reserved for future raw-frame analysis) —
  the first step of de-conflating WiCAN's PCI-interleaved indexing from the tool's
  internal byte space (`plans/2026-07-24-byte-notation-phase2-isotp-canonical.md`).

### Fixed

- **`hunt can` / `correlate can` hardened on real logs.** Shaking the frame
  tooling out against the 75k/61k-frame uhi22 Ioniq-28 GVRET logs surfaced three
  issues: (1) `hunt can` aborted with `OverflowError` when a wide `f64`/`f32` byte
  interpretation produced values near the float max — `stats.pearson` now guards
  non-finite/overflowing inputs and returns undefined (`None`) instead of raising;
  (2) a bad `--id`/`--against` arbitration ID leaked Python's `invalid literal for
  int()` — now a clean `invalid arbitration ID` message; (3) `hunt can` printed
  opaque `<no-expr>` rows for little-endian multi-byte reads — they now render an
  actionable shift composition (`r1 | (r2 << 8)`), matching the diagnostic
  `wican_expr` (only floats / LE-signed stay `<no-expr>`). Also a large
  `correlate can --find-mirrors --bits` sweep no longer hangs (fused
  join+compare + pre-sort: a >3 min run drops to ~40 s over 60k frames).

- **Repeated on-demand saves in the live monitor no longer duplicate payloads.**
  Pressing `s` twice in a non-`--save` monitor session used to re-write the entire
  history each time; it now writes only the payloads captured **since the last
  save** (and reports "Nothing new since last save" when there's nothing new).
  (`--save`/journal sessions were already de-duplicated.)

- **`canair hunt` no longer surfaces (or promotes) ISO-TP framing bytes.** Its
  PCI-skip guard used a simplified `index % 8 == 0` test that missed the first
  frame's *second* PCI byte at WiCAN index 1, so a byte window overlapping B1
  (e.g. one tracking the multi-frame length byte) could be ranked as a signal and
  written by `--promote` — caught only later by `validate`'s PCI check. It now
  uses the canonical `wican_to_isotp` detector shared with `build_byte_series`,
  `coverage`, and `validate`, so every PCI position is excluded consistently.
- **`canair bix` no longer crashes on large payloads / high byte indices.** The
  Torque letter notation only models 1–2 letters (`A`..`ZZ`, index 0–701); past
  that, `bix --annotate`/`--table` on a long multi-frame payload and plain index
  lookups (`bix b99999`) raised an unhandled `ValueError`. The display now falls
  back to the numeric Torque index beyond `ZZ` (via a new `torque_label` helper),
  and the default (Torque-hidden) path no longer computes the letter at all.

### Changed

- **`uds` / `can` domain-kind spine (breaking CLI rename, no aliases).** The two
  data domains — **`uds`** (request/response diagnostics) and **`can`** (passive
  broadcast frames) — are now expressed consistently as a named subcommand
  *kind* wherever the split surfaces (ingest / list / analyze), following the
  `validate <target>` model, so it's obvious which domain a command touches:
    - `canair import-capture …` → **`canair import uds …`** (alongside the
      existing `import can` / `import dbc`).
    - `canair captures --can` → **`canair captures can`**; the diagnostic surface
      is **`canair captures uds …`** (QUERY/diff/step/summary/sessions/latest/
      recover).
    - `canair correlate --can-log FILE` → **`canair correlate can FILE`**;
      `canair hunt --can-log FILE --id …` → **`canair hunt can FILE --id …`**.
      Their diagnostic path is the default **`uds`** kind.
  Bare-invocation muscle memory is preserved (`captures BMS 2102`, `correlate …`,
  `hunt AAF 2181 --against …` all default to `uds`, like `scan`/`ecu`). `pids`
  (domain A) and `signals` (domain B) authoring are unchanged, but their `--help`
  now cross-references each other as the A/B pair. See the naming-spine addendum
  in `plans/2026-07-24-raw-can-analysis.md`.

- **Internal: consolidated the two WiCAN-byte reconstruction paths.**
  `wican_bytes.uds_hex_to_wican_bytes` now delegates the PCI-insertion to
  `byteindex.payload_to_wican_bytes` (the single source of truth) and only
  re-applies the multi-frame zero-padding, removing a duplicate implementation
  that had to stay byte-identical by hand. Output is unchanged (guarded by a new
  equivalence test). `bix`'s single-index PCI-neighbour warning also now uses the
  canonical `wican_to_isotp` detector instead of a hand-rolled `% 8` heuristic
  (behaviour unchanged; removes the copy-paste trap that produced the `hunt`
  bug). Docstrings on the expression evaluator and `byteindex` now state the
  byte-space contract explicitly — ISO-TP is canonical, WiCAN is a firmware-only
  view, convert at the edges — and user-facing "raw CAN frame index" wording was
  corrected to "WiCAN AutoPID frame index (ISO-TP + PCI)".

### Added

- **`canair bix --torque` and `--obdb`** show the Torque letter column and the
  OBDb `bix` (bit-index) column, respectively, in `--annotate`, `--table`, and the
  bare-`bix` overview. Both are **hidden by default** and are now **independent
  flags** (Torque and OBDb `bix` are distinct notations — request either or both;
  `--obdb` is no longer an alias of `--torque`). WiCAN and ISO-TP are the notations
  canair expressions use; Torque/bix are opt-in for cross-referencing third-party
  PID sheets. Torque notation is what the **Torque app, Car Scanner**, and similar
  OBD apps use. The overview and legend point at the flags.

- **`canair bix <index>` now reports the CAN frame the byte lands in.** A
  single-index lookup (`bix w9`, `bix b32`, `bix E`, …) adds a `CAN frame:` line
  naming the byte's 8-byte CAN frame and its span (e.g.
  `CAN frame: 1 (B08–B15)`) — handy for spotting which bytes share a frame and
  where the PCI boundaries fall.

- **`canair bix --annotate --raw` (alias `--frame`)** annotates an already-framed
  CAN payload (ISO-TP PCI bytes present, e.g. copied straight off the bus),
  indexing the bytes as-is instead of reconstructing the framing from a
  PCI-stripped UDS payload. `--annotate` also now reliably warns when the input's
  first byte contradicts the chosen mode — a UDS response SID (`0x40`–`0x7F`) and
  an ISO-TP PCI first byte (`0x00`–`0x3F`) occupy disjoint ranges — so a raw frame
  fed without `--raw` (or a PCI-stripped payload passed with `--raw`) is caught
  rather than silently mislabelled; a Flow-Control/invalid frame under `--raw`
  errors. The warning is emphasized (a `⚠ WARNING` banner + rule) and separated
  from the table below.
- **`canair bix` (no arguments) now prints a guided overview** instead of an
  error: a plain-language legend explaining each notation (WiCAN / ISO-TP /
  Torque / bix) and the `PCI`/`FF`/`CF`/`SID`/`PID`/`DID` Role labels, a compact
  2-frame table (B00–B15), and next-step hints. The legend ties the `PID`/`DID`
  row to the UDS subfunction (the same byte the `-1`/`-2` flags select). `--table`
  remains the full table and now prints the same legend above it.

### Changed

- **`canair bix --annotate` is now length-aware.** The ISO-TP / Torque / bix
  columns are derived from each byte's actual position in the reconstructed
  frame, so single-frame (≤7-byte) responses — one PCI byte at B00, SID at B01 —
  are mapped correctly. Previously these columns assumed the multi-frame ISO-TP
  layout and were off-by-one for single-frame payloads (disagreeing with the
  `Role` column). `--table` and single-index lookups remain the canonical
  multi-frame reference.
- **Torque 1 vs Torque 2 is now discoverable.** `--annotate` names the active
  Torque variant (Torque 1 for `21xx` PIDs / `-1`, Torque 2 for `22xxxx` DIDs /
  `-2`) and points at the other, and the `bix` legend explains that the
  Torque/OBDb mapping counts from the first UDS data byte and therefore shifts
  with the subfunction width — so it's clear the mapping isn't fixed.
- **`canair bix --table` now shows CAN frame boundaries.** Rows are grouped by
  8-byte CAN frame with `── Frame N ──` dividers, and a new `Role` column marks
  the ISO-TP framing (`FF PCI`/`CF PCI`) and UDS header (`SID`/`PID`/`DID`) bytes,
  so it's clear where frame boundaries fall and which rows are framing vs. data.
  `--annotate` gains the same frame dividers on multi-frame payloads. Frame
  dividers and PCI rows are color-highlighted on a TTY and plain when piped.

## [1.1.0] - 2026-07-24

### Added

- **`canair ecu add TX`** — register an ECU into a profile **offline** (no live
  bus), the counterpart to `discover --register` for seeding a known ECU into a
  blank/contributable profile. `ecu` is now a command group (`show` default +
  `add`). Validated and comment-preserving.
- **First-run profile chooser.** On the first interactive run that needs a
  profile, canair offers to pick a discovered profile or create a new one, with
  explicit path messaging, and records the choice as `default_profile`. Never
  fires when scripted/piped or when `--profile`/`CANAIR_PROFILE` is set.
- **`canair investigate --bits`** — rank individual toggling bits (`Bn:k`), not
  just bytes, so body/comfort-ECU status signals surface. Also fixes the
  no-co-polled-anchor case to rank by state separation with a hint (instead of
  misleadingly reporting "no varying bytes").
- **`canair investigate --events`** — the bit/byte edge timeline: each
  rising/falling transition with its timestamp and value, aligned to the nearest
  capture note (the narrated event log). Automates decoding event-driven captures
  (door/lock/hood etc.).
- **`canair correlate --find-mirrors`** — cross-ECU byte/bit mirror finder
  (time-aligned equal positions across co-polled PIDs); the cross-ECU companion
  to `decode --find-mirrors` (single-PID). Use with `--bits` for bit-level.
- **`canair bix --annotate --ecu ECU --pid PID`** — overlay which defined
  parameter (and bit) maps each byte, flagging unmapped data bytes. Makes a wrong
  byte offset obvious at a glance.
- **`canair pids rename-param` / `rm-param`** — rename or remove a parameter
  (comment-preserving, schema-validated, auto-reverted on failure). Removes the
  last "must hand-edit YAML" case for parameter maintenance.
- **`keep_mode` awareness in analysis.** `decode`, `correlate`, and `investigate`
  now warn when the scope includes `keep:unique` sessions (only rising-edge
  transitions were stored; falling edges/durations are absent) and caveat
  rate/duration transforms (`--corr-transform delta|cumsum`, `--lag-scan`) on
  such data.

### Changed

- **`canair validate pids`** now flags a duplicate *shipped* parameter name
  across PIDs (a device signal-name collision) as an error — previously this
  only surfaced at `wican autopid write` time.
- **ECU-file validation is profile-scoped.** Validating (and thus writing via
  `canair pids`/`ecu add`) an ECU file now resolves the vehicle-state vocabulary
  from the file's own profile rather than the globally-active one, so edits to a
  non-active profile work even when several profiles are discovered.

### Removed

- **`canair tester-present` command.** It duplicated behavior already provided
  automatically: opening an extended session (via a `session <ECU>` query step
  or any command's `--session`) keeps that session alive with idle-aware
  TesterPresent (`3E00`) keepalives. Send a one-off by hand with a query step
  (`canair query BMS:3E00`); the interactive `repl`'s `!tester [id]` loop
  remains for manual keepalive spamming. TesterPresent (SID `0x3E`) is shared by
  UDS and KWP2000 and is sent identically for both.

### Docs

- **Task-first documentation site** under `docs/`, published with MkDocs Material
  to [philipkocanda.github.io/canair](https://philipkocanda.github.io/canair/):
  getting-started, the full **Bring your own car** walkthrough, concepts, a
  reference, the bundled-profile tour, and a contributing guide.
- **Generated CLI reference** (`scripts/gen_cli_reference.py` →
  `docs/reference/cli/`) rendered from each command's `--help`, with a CI
  `--check` gate so it can't drift.
- **`CONTRIBUTING.md`** and prominent "contribute your profile/PIDs back"
  encouragement across the README and docs.
- README trimmed to a compact, high-level gateway that links into the docs site.

## [1.0.0] - 2026-07-23

First stable release. canair is a general-purpose CAN/UDS/KWP2000 diagnostic
reverse-engineering CLI that talks to a vehicle over the air through a WiCAN
dongle (both the WiCAN Pro and the classic/non-Pro WiCAN are supported).

### Added

- **`canair --version`** flag, single-sourced from the installed package
  metadata (`canlib.__version__` via `importlib.metadata`).
- **Live device tooling** — `query`, `scan` (range/iocontrol/routines/sessions),
  `discover`, `io`, `routines`, `identity`, `raw`, `repl`.
- **DTC handling** — `dtc` reads stored Diagnostic Trouble Codes across ECUs
  (UDS `0x19` / KWP2000 `0x18`), logs scans, reports changes, and can clear
  fault memory (`0x14`).
- **Passive sniffing** — `sniff` live per-ID broadcast table with optional
  `.asc`/`.blf`/`.csv` logging (raw SLCAN transport).
- **Capture pipeline** — `--save`/`--monitor` journaled capture recording with
  crash recovery (`captures --recover`), plus `captures` search/diff/step.
- **Analysis** — `decode` (stats, correlation, interactive `--plot` explorer,
  `--try` expression testing), `coverage` (decoding-gap audit), `research`
  (RE backlog).
- **Definition editing** — `pids` (surgical, validated, comment-preserving
  edits to per-ECU YAML) and `validate` (schema validation).
- **WiCAN integration** — `wican` AutoPID profile generation/upload/download/
  diff and device mode switching (device sync is Pro-only).
- **Profiles** — multi-vehicle profile bundles with `profile create/list/show`;
  ships `profiles/ioniq-2017/` as the default example.
- **Utilities** — `bix` byte-index converter, `ecu` registry inspection,
  `status` transport/mode snapshot, `config` user-config management.
- **Dual-transport architecture** — every bus feature works over both the raw
  `slcan-tcp` transport (default) and the `wican-ws` WebSocket ELM327 terminal.
- Command safety blocklist preventing UDS programming/write sessions against a
  real vehicle.

[Unreleased]: https://github.com/philipkocanda/canair/compare/v1.9.0...HEAD
[1.9.0]: https://github.com/philipkocanda/canair/compare/v1.8.1...v1.9.0
[1.8.1]: https://github.com/philipkocanda/canair/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/philipkocanda/canair/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/philipkocanda/canair/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/philipkocanda/canair/compare/v1.5.1...v1.6.0
[1.5.1]: https://github.com/philipkocanda/canair/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/philipkocanda/canair/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/philipkocanda/canair/compare/v1.3.3...v1.4.0
[1.3.3]: https://github.com/philipkocanda/canair/compare/v1.3.2...v1.3.3
[1.3.2]: https://github.com/philipkocanda/canair/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/philipkocanda/canair/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/philipkocanda/canair/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/philipkocanda/canair/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/philipkocanda/canair/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/philipkocanda/canair/releases/tag/v1.0.0
