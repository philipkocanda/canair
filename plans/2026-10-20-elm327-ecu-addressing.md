# ELM327 addressing from the resolved `EcuAddress`: receive filter and flow-control id

Status: **IMPLEMENTED** (2026-10-20). Still to do: the on-car verification below; no
non-conventional ECU has been read over an ELM327 transport yet.

On the ELM327 transports (`wican-ws`, `elm327-tcp`) and in the generated AutoPID profile, drive
the adapter's CAN **receive filter** (`ATCRA`) and **flow-control id** (`ATFCSH` + flow-control
mode) from each ECU's resolved addressing. The raw `slcan-tcp` path already does this. The values
are **derived, not configured**: they come from the `EcuAddress` that canair resolves from
`addressing.rx_offset`, per-ECU `rx_id` and per-ECU `fc_id`. No new profile field is needed, and
none of these commands belongs in `init:`.

The same mechanism covers both ways an ECU leaves the ISO 15765-4 convention:

- **A non-`+0x08` response offset** (11-bit): Nissan Leaf ZE1 `0x79B → 0x7BB` and
  `0x743 → 0x763` (`+0x20`, profile-wide) plus its VCM `0x797 → 0x79A` (a per-ECU `rx_id`), XPeng
  G6 `0x704 → 0x784` (`+0x80`), and the rest of the upstream corpus listed below.
- **Functional request, physical response** (the "shared id" case, gap G-J in
  `plans/2026-07-28-multi-vehicle-support.md`): Renault Megane/Scénic/R5/Master E-Tech and
  Mitsubishi Outlander PHEV send to the functional broadcast id `0x18DB33F1`. The ECU answers on
  its physical id `0x18DAF1DB`, and flow control must go to the physical request id `0x18DADBF1`,
  not to the broadcast id.

Over `wican-ws`, canair sends only `ATSH<tx>` + `ATFCSH<tx>` today. Both cases are therefore
probably unreadable there. The G6's only capture was made over `slcan-tcp`, which filters and
addresses flow control in software and is unaffected.

## How the adapter picks both ids

**The adapter always filters; the only question is which filter is in effect.** The ELM327
datasheet: "When receiving CAN data, the ELM327 actually retrieves every message from the CAN bus,
and then decides whether or not to display it". With no `ATCRA` it runs *auto receive*, where the
receive address "will be chosen based on the current header bytes". The datasheet gives no rule
for an arbitrary 11-bit header, and the WiCAN Pro's STN2120 is not an ELM327 anyway.

**Flow control has three modes, and `ATFCSH` only applies in one of them.** From the datasheet's
"Altering Flow Control Messages":

| `ATFCSM` | Flow-control id | Flow-control data |
|---|---|---|
| `0` (default) | derived by the adapter from the First Frame | adapter (ISO 15765-4) |
| `1` | `ATFCSH` | `ATFCSD` |
| `2` | the First Frame's own id | `ATFCSD` |

"If you try to set the mode before defining these values, you will get an error": `ATFCSM1` needs
`ATFCSH` **and** `ATFCSD` set first. No bundled profile's init sets a working mode 1: the Ioniqs
never enable it, and the Leaf and XPeng inits send `ATFCSM1` before `ATFCSD` with no `ATFCSH`.
So, assuming the STN rejects that order as the datasheet says, **every `ATFCSH` canair sends
today is ignored**. Flow control runs in mode 0 and works only because the
adapter's derivation matches the ISO convention. The classic WiCAN's software emulation (compiled
out on the Pro) shows that derivation: an 11-bit First Frame's id with bit 3 cleared
(`0x7E8 → 0x7E0`), and for 29-bit, `0x18DA{source}F1` with `{source}` taken from the First Frame
(`wican-fw/main/elm327.c` on `main`, `elm327_send_flow_control_frame`).

What that means per case:

- **ISO-conventional ECUs work on the defaults.** Every bundled Ioniq ECU is `+0x08`
  (`0x770 → 0x778`, `0x7A0 → 0x7A8`, `0x7C6 → 0x7CE`, no `rx_id` overrides) and all of them read
  over `wican-ws` with no `ATCRA` and mode-0 flow control. `ATSH` does set a filter: `STPX`
  returned `NO DATA` after `ATSH` and worked after `ATCRA`
  (`plans/2026-08-09-wican-ws-throughput-ceiling.md`).
- **The Leaf needs both.** If the auto filter is effectively `header + 8`, the adapter listens on
  `0x7A3` and drops the `0x7BB` reply (`NO DATA`, even on a silent bus). If it instead accepts
  everything for an unusual header, broadcast traffic on a non-gatewayed OBD port gets mixed into
  the reply. Either way, mode-0 flow control derives `0x7BB → 0x7B3`, not `0x79B`. The ECU then
  never gets its go-ahead and stalls after the First Frame; the Leaf's `2101` is about 8 frames.
- **The functional case needs the filter; its flow control is already right.** The request goes
  to the broadcast id, so several ECUs may answer and only `ATCRA` selects ours. Mode-0 flow
  control derives `0x18DAF1DB → 0x18DADBF1`, which is the physical id. canair's `ATFCSH18DB33F1`
  is harmless only because mode 0 ignores it.

Pinning both ids explicitly is correct whatever the STN's undocumented rules turn out to be, so
the change does not wait on learning them.

## Where the ids come from (already resolved per ECU)

The offset is set once per profile **and** can be overridden per ECU, plus one derived mode:

- `profile.yaml` `addressing.rx_offset` (default `0x08`; may be negative — PSA `-0x20`).
- A per-ECU `rx_id:` overrides it (cars mixing offsets: Smart EQ uses `+0x01`, `+0x08` and `+0x20`).
- `normal_fixed_29bit` derives the response id by byte-swapping the request id.
- A per-ECU (or profile) `addressing.fc_id` names the flow-control id for a functional-request
  ECU. Without one, flow control goes to `tx_id`, the same as the raw path.

`canlib/addressing.py::resolve_ecu_address` combines these into an `EcuAddress`
(`tx_id`/`rx_id`/`mode`/`fc_id`/…), and `canlib/pids.py::build_ecu_index` stores one per ECU. The
raw path already consumes it (`canlib/modes/raw_ops.py` builds a `tx_id → EcuAddress` map for
`RawTerminal`; `canlib/transport/isotp_stack.py` applies the `fc_id` override). **Everything below
keys on the resolved `EcuAddress`, never on `rx_offset` alone**, so per-ECU overrides are
honoured automatically.

Rejected: putting `ATCRA…`/`ATFCSH…` in `init:`. The init string applies to the whole session, so
one ECU's values would apply to every ECU, and they would persist into later sessions (the
housekeeping warning in the `wican-hardware-and-protocol` skill).

## Upstream evidence

From the `meatpiHQ/wican-fw` corpus (`main`, `vehicle_profiles/`) and the Pro firmware
(`wican-pro` branch, `fab54ad`):

- **29 of 82 upstream profiles set `ATCRA` in `pid_init`, and none of them sets it on only some
  PIDs.** 9 more set it in the global `init`. Profiles that set it include Nissan Leaf AZE0/ZE1,
  Dacia Spring, Renault Twizy (`+0x20`), XPeng (`+0x80`), GWM (`+0x40`), the i-MiEV triplet
  (`+0x01`), Opel (`+0x400`) and PSA (negative offsets). A few `+0x08` profiles set it too (MG,
  Toyota bZ4X).
- **20 of the 23 profiles with a non-`+0x08` 11-bit pair also enable flow-control mode 1**,
  usually as `ATFCSH<tx>;ATFCSD300000;ATFCSM1` in `init` plus `ATFCSH<tx>` in every `pid_init`
  (Leaf, Smart EQ, Twizy, C-Zero). The three that don't (Audi A3, GMC Sierra, Opel) may only make
  single-frame reads.
- **The functional-request profiles set only `ATSH` + `ATCRA`**: Outlander
  `ATSH18DB33F1;ATCRA18DAF1DB`, Megane `ATSHDB33F1;ATCRA18DAF1DB`. Neither sets mode 1, which is
  consistent with mode 0 already deriving the physical flow-control id.
- **AutoPID sends each custom PID's `pid_init` verbatim before that PID and never resets the
  filter between PIDs** (`components/autopid/autopid.c:3961-3966`). The global `init` is resent
  only when the PID type changes (`:3914-3950`). A value set for one ECU therefore stays in effect
  for the next PID unless that PID's `pid_init` changes it.
- **The firmware itself resets the filter with a bare `ATCRA`**: in its standard-PID init
  (`autopid.c:372-375`, `autopid_config.c:902-907`), and after its CAN-filter monitoring slice,
  which then forces every init to be resent (`autopid.c:4177-4183`).

`ATCRA`, `ATCRA hhh`/`hhhhhhhh`, `ATFCSH`, `ATFCSD` and `ATFCSM` are all standard ELM327 commands,
so `elm327-tcp` clones should accept them too (clone compliance varies; see Verification).

## Design

### Policy: explicit only where the ISO convention does not hold

An ECU is **conventional** when the adapter's own derivations are known to give the right ids:

- 11-bit, `rx_id == tx_id + 0x08`, flow control to `tx_id` (proven on the Pro); or
- `normal_fixed_29bit`, `rx_id == fixed_29bit_rx(tx_id)`, flow control to `tx_id` (the
  ISO 15765-4 29-bit convention; **unverified**, see Open questions).

Every other ECU is **explicit**: receive filter `ATCRA<rx_id>` (3 hex digits for 11-bit, 8 for
29-bit) and flow control in mode 1 to `fc_id`, or `tx_id` when there is no `fc_id`. This covers
both the offset case and the functional-request case. Three kinds of ECU are left alone instead:

- a broadcast request id with no `fc_id` (an OBD-II ECU registered at `0x7DF`, whose replies come
  from `0x7E8`–`0x7EF`) is addressed as a broadcast: auto filter, mode 0;
- the extended-addressing modes, which the ELM327 path cannot address yet (no `ATCEA`) and whose
  ECUs share one request id (BMW `0x6F1`), so a filter keyed by it would be wrong for all but one;
- a reply id the mode cannot carry (a Leaf-offset discovery sweep past `0x7DF` gives `0x800+`).

A conventional ECU keeps the **baseline** filter: whatever the profile `init` left in place,
normally auto. That preserves an init that filters to one ECU on purpose (the ELM327-Emulator
fixture's `ATCRA7E8`), and a filter already on the ECU's own reply id is left alone too.

One pure module owns the rule, `canlib/transport/elm327_addressing.py`: `is_conventional(addr)`,
and `target_for(addr)`, which returns the `ElmTarget` (header, flow-control id, filter, mode) an
ECU needs. The live path, AutoPID and validation all use it, so they cannot disagree.

Why not always explicit: every profile canair has actually read over `wican-ws` is all `+0x08`.
Always pinning would add a round trip to each ECU switch on exactly the cars that work today
(about one link RTT per AT command: ~60 ms each on the VPN link measured in the throughput
plan). Doing it only where needed changes nothing for them.

**Flow-control data** is `30 00 {stmin}`, with `stmin` from the profile's `isotp.stmin`
(`canlib/transport/isotp_params.py`). The defaults give `300000`, which is what the upstream
corpus uses. The block size is always 0, whatever `isotp.blocksize` says: the datasheet has mode 1
send the user's FlowControl frame "in response to a First Frame", and nothing documents a further
one after each block, which a non-zero block size makes the ECU wait for.

### 1. Live ELM path (`canlib/transport/elm327_terminal.py`)

- **Give the terminal the address map.** `canlib/addressing.py::AddressBook` holds every
  registered ECU's `EcuAddress` plus the profile default `rx_offset`/`mode` for an unknown TX id
  (a discovery sweep). `connect_elm_terminal` (`canlib/commands/_live/connect.py`) sets it as
  `terminal.addresses`, next to where it sets `ecu_timeouts`, along with
  `terminal.flow_control_data`. `raw_ops.py` builds `RawTerminal`'s map from the same book.
- **`set_header(tx_id)` settles the whole address.** The ~35 call sites keep passing only
  `tx_id`. The terminal looks up the ECU and sends only what differs from the cached state, in
  this order:
  1. `ATSH<tx>`.
  2. `ATFCSH<fc>`: the flow-control id for an explicit ECU, `tx` otherwise (today's behaviour).
  3. Once per session, the first time an explicit ECU is selected: `ATFCSD<data>` then `ATFCSM1`.
     The datasheet's ordering is required (`FCSH` and `FCSD` before `FCSM1`).
  4. `ATCRA<rx>` for an explicit ECU. For a conventional ECU, a bare `ATCRA` when the filter is
     currently pinned or unknown.

  Mode 1 stays on for the rest of the session. That stays correct for conventional ECUs, because
  step 2 already points `ATFCSH` at `tx`, so there is no need to toggle back to mode 0. Callers
  already hold `transaction()` across `set_header` + `send_uds`, so a keepalive cannot interleave.
- **Functional headers restore the adapter's defaults.** A functional request with no specific
  responder (`0x7DF` TesterPresent in `canlib/modes/tester.py`, or any TX id that is a functional
  broadcast and *not* a registered explicit ECU) gets bare `ATCRA` and `ATFCSM0`. That lets
  several ECUs answer and lets each get its own flow control. A registered functional-request ECU
  (Renault/Outlander) is explicit instead, and pins its own physical response.
- **Track the state like the headers.** `AdapterState` replaces the cached
  `_cur_header`/`_cur_fc_header` and adds the receive filter (*unknown* / *auto* / pinned id), the
  flow-control mode and data. `observe(cmd, reply)` runs after every command's reply, including
  direct sends:
  - `ATCRA<hex>`, `ATFCSD…` and `ATFCSM…` set their values;
  - bare `ATCRA` and `ATAR` set the filter to *auto*;
  - `ATZ`, `ATWS` and `ATD` reset everything to adapter defaults;
  - a malformed argument, or a `?` reply (the adapter rejected the command), sets *unknown*,
    and that exact command is not planned again until a reset, so a clone that lacks one costs a
    single round trip rather than one per read. Two exceptions: a rejected `ATSH` is never
    skipped, and `set_header` raises `AdapterRefusedError` instead of letting the request reach
    the previously addressed ECU (likewise `ATFCSH` while mode 1 is in force); and `ATFCSM1` is
    only skipped when it was rejected with `ATFCSH` and `ATFCSD` already set, since before that
    the rejection is by design (an upstream-ordered init);
  - a command whose reply never arrived sets *unknown* without being skipped;
  - commands canair never plans still invalidate what they touch: `ATCP`/`ATSP`/`ATTP` the
    header, `ATTA` the header and filter, `ATCF`/`ATCM`/`ATSR`/`ATCEA` and the STN `STF…`
    filters the receive filter, and the STN flow-control filters/address pairs the flow control.

  A resync, a start-up banner in a reply slot (a late connect banner, or the adapter resetting
  itself mid-session) and an idle gap longer than
  `IDLE_FORGET_SECONDS` (a WiCAN in `auto_pid` resumes its own AutoPID 10 s after the last ELM327
  request, and its `pid_init`s re-point everything) also make the whole state unknown; a
  `send_uds` retry after a resync re-addresses first.

  `connect()` sets everything to *unknown*, so the first `set_header` after a (re)connect states
  it explicitly (a fresh, never-connected terminal starts as after a reset). That also clears
  values left over from another tool or session, as long as the session's `init_elm` (which
  starts with `ATZ`) has not already done so. A `?` answer to
  any command is logged once per command to `canair logs` (and stderr with `--verbose`; not
  unconditionally, because a reconnect can run while the monitor TUI owns the screen), from both
  `init_elm` and `set_header`: `init_elm` used to ignore error replies, which is how a misordered
  init could fail silently.
- **Feed the link estimator.** `ATCRA`, `ATFCSD` and `ATFCSM` are answered from adapter memory
  with no CAN traffic, so add them to `_LINK_PROBE_CMDS`. None is a prefix of a command that does
  more work.
- **Direct `ATSH` senders.** `canlib/modes/tester.py` and `canlib/modes/skm_wakeup.py` write
  `ATSH`/`ATFCSH` themselves. Route them through `set_header` (the tester through the
  functional-header rule above). SKM wake is Ioniq-only, but its tracking must stay coherent.
- **29-bit on the ELM path.** `set_header` already formats with `:03X`, so a 29-bit id is sent as
  `ATSH` with 8 digits (`SH wwxxyyzz`, ELM327 v2.x/STN). The profile's `init` must select a 29-bit
  protocol (`ATSP7`/`ATSP9`), and nothing checks that today; see Validation.
- **Discovery.** An unknown TX id resolves through the profile defaults, so a Leaf sweep pins
  each address to `tx + 0x20` with flow control to `tx`. That matches what the raw path does
  today. ECUs on a different offset than the profile default are found by neither path, and this
  plan does not change that.
- **Shared request ids.** `set_header` gets only a request id, so when several registered ECUs
  claim one with different addressing (two functional-request ECUs on `0x18DB33F1`, answering on
  `…DB` and `…DA`), it cannot know which is meant. `AddressBook.shared` marks such ids and
  `target_in` addresses them as a broadcast (auto filter, mode 0, logged once), rather than
  pinning whichever ECU was loaded last. `validate pids` warns about it (except for the
  extended-addressing modes, which share an id by design). AutoPID is not affected: each entry
  knows its ECU and pins that ECU's own ids.

### 2. AutoPID generation (`canlib/autopid_profile.py`)

- `generate_profile` resolves each shipped ECU's `EcuAddress` (via `resolve_ecu_address` with the
  profile meta) and calls `is_conventional`; when any shipped ECU is explicit, every entry uses
  `pinned_target`.
- **If any shipped ECU is explicit:**
  - every `pid_init` carries `ATSH<tx>;ATFCSH<fc>;ATCRA<rx>;`, conventional ECUs included (with
    their conventional ids). That is the upstream convention, and it means no PID in such a
    profile depends on the auto filter or on the previous PID's state;
  - the generated top-level `init` gets `ATFCSH<fc>;ATFCSD<data>;ATFCSM1;` appended after the
    profile's own init, using the first shipped ECU's flow-control id. That is the upstream
    Leaf/Smart/Twizy pattern, and it puts the mode-1 prerequisites in the order the datasheet
    requires.
- **If none is explicit**, the output is **byte-identical to today's**, so the Ioniq's
  `autopid diff` against the device stays clean.
- `make_pid_init` takes the resolved ids as arguments. Addressing goes before the optional
  `1003` session request, because the session's reply must pass the filter too.
- Regenerate `profiles/xpeng-g6/out/autopid.json` (it gains `ATCRA784` and mode 1). The Leaf seed
  has no `out/` yet: its PID is `draft` and its signals are unverified.
- Known limit, unchanged by this plan: once mode 1 is on, an upstream-style *standard* PID
  (functional `7DF`) would send flow control to the last `ATFCSH`. canair does not generate
  standard PIDs.

### 3. Profile `init` and validation

- **canair now owns the addressing commands**, the same way it already owns `ATSH`. Remove
  `ATFCSM1;ATFCSD300000;` from `profiles/nissan-leaf-ze1/profile.yaml` and
  `profiles/xpeng-g6/profile.yaml`, and reword their comments. Both list `ATFCSM1` *before*
  `ATFCSD` with no `ATFCSH` set, which is the order the datasheet says fails. So on the live path
  those bits are probably rejected silently today (verify with `--verbose`).
  `profiles/xpeng-g6/KNOWN-ISSUES.md` (the long-response mitigation) is updated to match.
  `ATSTFF` stays, because it is not addressing.
- **`validate pids`** (`canlib/commands/validate/addressing.py`) warns when:
  - `init` contains a canair-owned addressing command: `ATSH`, `ATFCSH`, `ATFCSD`, `ATFCSM`.
    A receive filter there is legitimate (the conventional ECUs' baseline), so `ATCRA` is not
    flagged;
  - an ECU's `tx_id` is a functional broadcast id (`0x7DF`, `0x18DB33F1`) with no `fc_id`
    (applies to both transports — the raw path would also send flow control to the broadcast
    id);
  - the ECUs are 29-bit but `init` selects an 11-bit protocol, or the reverse.

## Tests

- `is_conventional` / `target_for` (`tests/test_elm327_addressing.py`):
  - 11-bit `+0x08` → conventional;
  - `+0x20`, `+0x80`, `+0x01` and a negative offset → explicit, with flow control to `tx`;
  - an explicit per-ECU `rx_id` wins;
  - `normal_fixed_29bit` byte-swap → conventional;
  - functional `0x18DB33F1` / `rx_id 0x18DAF1DB` / `fc_id 0x18DADBF1` → explicit, filter
    `18DAF1DB`, flow control `18DADBF1` (8-digit forms);
  - the flow-control data follows `isotp.stmin`, with the block size always 0.
- `Elm327Terminal` over the fake channel (`tests/test_elm327_tcp.py` style, which already asserts
  `["ATSH7E4", "ATFCSH7E4", "2101"]`):
  - a `+0x08` ECU sends nothing new, so the existing assertion stays green unchanged;
  - a `+0x20` ECU sends `ATSH79B`, `ATFCSH79B`, `ATFCSD300000`, `ATFCSM1`, `ATCRA7BB` in that
    order, and a second read sends nothing;
  - explicit → conventional sends `ATSH`/`ATFCSH` + bare `ATCRA` (no `ATFCSM0`);
    conventional → conventional sends no `ATCRA`;
  - the functional-request ECU pins `ATCRA18DAF1DB` with `ATFCSH18DADBF1`; the `0x7DF` tester
    restores bare `ATCRA` + `ATFCSM0`;
  - after `connect()` the state is restated; a direct `ATZ`/`ATCRA…`/`ATFCSM…` send updates the
    cache; a `?` reply to `ATFCSM1` is reported.
- `generate_profile`:
  - a mixed profile puts `ATSH;ATFCSH;ATCRA` on every entry, ahead of `1003`, and appends the
    mode-1 setup to `init` in datasheet order;
  - an all-`+0x08` profile is byte-identical to today's (pin it with the Ioniq fixture);
  - the functional ECU emits its `fc_id`, not the broadcast id.
- Validation: each new warning fires and is silent for the bundled Ioniq.
- Optional ELM327-Emulator run (`tests/test_elm327_emulator.py`), if the emulator accepts these
  commands.

## Docs

- `wican-hardware-and-protocol` skill: replace "Is `ATCRA` worth setting? … irrelevant otherwise"
  with the policy above. Record that `ATFCSH` only takes effect in mode 1 and that canair owns
  both. Keep the housekeeping warning (it now applies to canair's own sessions only when a
  session dies mid-ECU).
- `AGENTS.md`, "Shared ELM327 engine" / transaction paragraph: one line saying `set_header`
  settles the header, receive filter and flow control from the resolved `EcuAddress`. Update the
  `fc_id` mention under Key Files/`addressing:` so it no longer reads as raw-path-only.
- `docs/concepts/profiles.md`, addressing section: the ELM327 transports and AutoPID derive the
  receive filter and flow-control id from `rx_offset`/`rx_id`/`fc_id`, so neither a
  non-`+0x08` car nor a functional-request ECU needs extra configuration. Addressing commands
  stay out of `init:`. `isotp.stmin` now also shapes ELM flow control.
- `config.example.yaml` needs nothing (no new user-config key).
- `plans/2026-07-28-multi-vehicle-support.md`, gap G-J: note that the ELM path is covered here.

## Verification

- **Ioniq regression (bench, `wican-ws`).** `--verbose` header trace on a multi-ECU `monitor`:
  no `ATCRA`/`ATFCSD`/`ATFCSM` is ever sent, and cycle time is unchanged.
- **The current init bits (bench, any car).** `--verbose` on the Leaf/XPeng profile shows the
  adapter's reply to `ATFCSM1` during init, which confirms or refutes the "silently rejected"
  reading.
- **The real test needs a non-conventional car.** A Leaf or G6 owner runs `uv run canair
  --profile nissan-leaf-ze1 read LBC:2101 --transport wican-ws --save …` before and after the
  change. Before is expected to fail (`NO DATA`, or a stalled multi-frame); after should show
  data. A Renault/Outlander owner does the same once such a profile exists. Ask in the
  contribution issue rather than assume.
- **`elm327-tcp` clone.** Confirm a cheap clone accepts `ATCRA hhh`, bare `ATCRA`, `ATFCSD` and
  `ATFCSM1`. If one answers `?`, decide whether to fall back silently or report it once.

## Out of scope

- Extended/mixed 11-bit addressing (`ATCEA`, BMW/Mini/PSA) on the ELM path.
- `STPX` (it would need `ATCRA` on every switch; still rejected by the throughput plan).
- The raw `slcan-tcp` path, which already filters on `rx_id` and honours `fc_id`.
- **Possible follow-up:** since `ATFCSH` is ignored in mode 0, the `ATFCSH` canair sends on every
  ECU switch in a conventional-only session is a wasted round trip. Dropping it would save about
  one link RTT per switch on the Ioniq, but that changes the reference car's command stream, so
  it needs its own measurement.

## Open questions

1. **Is `normal_fixed_29bit` really conventional on the STN?** Undecided. No 29-bit car has been
   read over `wican-ws`. Treating it as explicit costs one or two commands per switch; leaving it
   conventional matches the ISO convention but is unproven. It is a one-line change to
   `is_conventional` either way. Settle it with the first 29-bit capture.
2. **Kill switch?** Not for now. The change only touches ECUs that probably do not read today. A
   `transport.*` user-config key (like `transport.expected_responses`) can be added if a clone
   rejects the commands.
3. **Always explicit in AutoPID, even for an all-conventional profile?** Leaning no, not decided.
   It would match the MG/bZ4X upstream style, but it changes the Ioniq's generated output and
   device diff for no proven gain.
