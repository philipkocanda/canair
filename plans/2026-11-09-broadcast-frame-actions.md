# Broadcast-frame actions: sending a crafted non-diagnostic CAN frame

Status: **IMPLEMENTED** (2026-11-09). The design below shipped; "Resolved decisions" at the end
records the four choices settled with the user, and "Still open" the questions the on-car test
(not the code) must answer.

Paths are relative to `src/canair/`. This plan adds a second kind of request to the profile action
model (`actions/`, `live/actuate/action.py`), so a profile can declare a guarded broadcast CAN
frame and send it. It builds on the action guard (`reads`/`require`/confirm/`release`), the quirk
grant mechanism (`safety/quirks.py`) and the raw transport's python-can bus
(`transport/raw/functional.py` already sends a crafted frame).

## Why

canair can send diagnostic requests only. `raw` sends UDS/KWP2000 (hex in, hex out) on an ECU's
request id; `send_functional` broadcasts one allowed read on `0x7DF`; the blocklist reasons about
diagnostic services. There is no way to put a plain broadcast frame (an arbitration id below the
diagnostic block, with a payload) on the bus.

The immediate goal is to test the designed remote-climate path on the bundled Ioniq. The telematics
unit's frame `0x587` (`TMU11`) carries `CF_Tmu_AirconCtr`; a 0→1 edge of that bit starts a
15-minute climate session when the car is in P, not READY, and plugged with the charger ready
(`profiles/hyundai-ioniq-ae-ev/research/VCU_FIRMWARE_FINDINGS.md` §9.5, §10.3). The EU car has no
telematics unit, so nothing else sends `0x587`: an injected frame has no competitor. The open
research entry `ecus/vcu.yaml` "remote climate and charge start: the TMU path and what the OBD port
reaches" names this exact test.

Two things are unknown and the test measures both:

1. **Does a frame from the OBD-II port (D-CAN) cross the IGPM to P-CAN at all?** Probably not: on a
   Kona the gateway forwarded only a fixed list of diagnostic ids, and it routes signals by its
   routing database, not whole frames (`IGPM_FINDINGS.md` §3.2, §4). D-CAN is probably not a source
   in that database. No reply comes back, so the only evidence is the car's behaviour.
2. **Will the VCU act on a single static frame?** It reads an alive counter (`0x587` bits 56-59)
   and the frame is normally periodic. A static frame may be ignored or may time out.

## What the frame must and must not carry

`0x587` holds bits that are not preconditioning. An injected frame must keep every one of them at 0:

| bits | field | risk if set |
|---|---|---|
| 0 | `CF_Tmu_VehSld` | stolen-vehicle slowdown |
| 1 | `CF_Tmu_VehImmo` | remote immobiliser, blocks READY |
| 32\|2 | `CF_TMU_IG3RLY` | changes charge/wake handling; a latched 1 blocks a later start |
| 36 | `CHAR_CANCEL` | stops the charge in progress |
| 38 | `AIRCONCTR_CANCEL` | cancels the climate session |

The test frame sets `CF_Tmu_AirconCtr` (bit 4) only, advances the alive counter (bits 56-59), and
holds every other bit at 0. `TMU_RDY` (bit 34) may be a precondition the VCU checks before it takes
remote input (`VCU_FIRMWARE_FINDINGS.md` §10.1 reads the schedule from CAN only when `0x567` is
valid or `TMU_RDY` is set); whether to set it is an open question below.

## The safety argument

A crafted broadcast frame is more dangerous than a diagnostic request: it is unauthenticated, it
has no service semantics the blocklist can read, and a wrong bit reaches a live ECU directly. The
guard therefore has four independent layers, all of which must pass:

1. **The profile declares the exact frame, not the code.** An action may send only a frame template
   the profile author wrote, with the variable bits named. canair never sends a free-form id and
   payload. (A free-form `canair send-frame ID DATA` is explicitly out of scope — see open
   questions.)
2. **A config gate, fail-closed.** A new `safety.allow_frame_injection` key, with the same rules as
   `safety.allow_unsafe` and `safety.allow_actuation`: read only from the user config file, only a
   YAML `true` turns it on, `canair config set` asks for a typed `YES` at a terminal, no flag
   answers the prompt. Default off. An agent never turns it on.
3. **The action guard.** `reads` and `require` are mandatory, as for every action: the condition is
   read live immediately before and again immediately after the prompt, and only TRUE sends. For
   the climate test the condition proves the car is plugged, in P and not READY.
4. **Transport restriction and id restriction.** `slcan-tcp` only (see below). The frame id must be
   below the diagnostic block (`< 0x700`), so an action can never forge a diagnostic response or an
   ISO-TP frame. A declared id in `0x700`-`0x7FF` is a load error.

## Design

### 1. The frame request in `actions.yaml`

An action's `requests:` (and `release:`) list today holds only diagnostic requests
(`{to: ECU|functional, send: HEX}`). Add a second shape, discriminated by its keys:

```yaml
actions:
  remote-climate-start:
    description: >-
      Inject the telematics unit's TMU11 frame (0x587) with CF_Tmu_AirconCtr set, to test whether a
      remote preconditioning start reaches the VCU from the OBD-II port. Car plugged in, in P, not
      READY. Observes HVAC diagnostics and VCU power draw.
    reads: [VCU:2101, IGPM:22BC07, OBC:2101]
    require: >-
      VCU.VCU_IG3_ON == 1 and VCU.VCU_INVERTER_ENABLED == 0 and IGPM.IG3_RELAY_OUTPUT == 1
    frames:
      - id: 0x587
        bus: P-CAN           # documentation only: where the author intends it to land
        data:   "10 00 00 00 00 00 00 00"   # bit 4 (AirconCtr) set, everything else 0
        mask:   "10 00 00 00 00 00 00 0F"    # the only bits this action is allowed to drive
        counter: {byte: 7, nibble: low, step: 1}   # advance bits 56-59 each frame
        rate_ms: 100
        duration_s: 30
    then: {wait: 5, read: [VCU:2101, HVAC:2101, BMS:2101]}
```

Field meaning:

- **`id`** — the arbitration id. Refused unless `< 0x700` (layer 4).
- **`data`** — the 8-byte template, whole hex bytes.
- **`mask`** — the bits this frame is permitted to differ from zero on. canair refuses the frame at
  load time if any bit set in `data` falls outside `mask` (so every dangerous bit named above, all
  outside the mask, is provably 0). The mask is the author's statement of intent and the check that
  enforces it.
- **`counter`** — optional free-running counter the sender advances each frame (byte, nibble or
  width, step). Its bits must lie inside `mask`.
- **`rate_ms` / `duration_s`** — send the frame every `rate_ms` for `duration_s` (or a `count`).
  A single-shot frame is `rate_ms` absent. The VCU's alive-counter and periodicity expectations
  are unknown, so a stream is the realistic case.

Model and parsing live beside the existing request parsing in `actions/__init__.py`: a
`FrameRequest` dataclass, a `_frame(raw, where)` parser, and the discriminator in `parse_action`
that routes a mapping with `id` to `_frame` and one with `to` to `_request`. The id-range, mask,
counter and rate checks are structural, so `canair validate actions` catches a bad frame offline.
An action may carry `frames:` alongside the diagnostic `requests:`/`release:`; both the guard and
the blocklist still apply to each.

### 2. The config gate

`config/file.py`: `ALLOW_FRAME_INJECTION_KEY` and `frame_injection_allowed()` through the shared
`_safety_flag`. Register it in `SAFETY_GATE_KEYS` (`cli/commands/config/keys.py`) and the `_GATES`
consent table (`cli/commands/config/edit.py`) with a prompt that says what it enables: putting a
crafted broadcast frame on the bus, which no reply confirms and which reaches ECUs directly, and
that only a person at the car should turn it on. `config/__init__.py` lists it in its charter.
`ConnectionOptions` (`transport/config.py`) gains `allow_frame_injection`, filled from the config in
`cli/live/runtime.py::connection_options`.

A broadcast-frame action is refused early (before the device lock) when the key is off, the way
`--unsafe` and the actuation gate refuse (`cli/live/runtime.py`): `canair action run NAME` inspects
the action's kind and exits 2 with the config command to turn the key on. The gate is a precondition
the user sets once at the car, not a per-run flag.

### 3. Sending — raw transport only

Add `send_frame(can_id: int, data: bytes) -> None` to the `Terminal` protocol
(`transport/protocol.py`):

- **`RawTerminal`** (`transport/raw/terminal.py`) sends it on its python-can `bus`
  (`bus.send(can.Message(...))`), the same call `raw_functional_exchange` already makes. A classic
  8-byte frame; a CAN-FD profile is refused, as the functional path is.
- **`Elm327Terminal`** raises `TransportUnsupported` (a new, caught error): the ELM327 raw-frame
  mode (`ATCAF0`) is untested on the WiCAN and out of scope for this plan. `wican-ws` and
  `elm327-tcp` therefore refuse a broadcast-frame action with a clear message naming `slcan-tcp`.

The streaming loop (rate/duration/count, advancing the counter) lives in `live/actuate/action.py`
beside `_send`, as a new `_send_frames(run)`. It must be interruptible: Ctrl-C stops the stream and
goes straight to `release`/`then`, like the diagnostic path. A frame send has no reply, so there is
no echo check; the loop times itself on the monotonic clock.

### 4. The guard flow reuses `mode_action`

`mode_action` already plans, checks the car, confirms with a typed name, re-checks, sends, reads
`then:`, and releases. A broadcast-frame action runs the same flow; only the send step differs
(`_send_frames` instead of `_send`). The planner (`actions/plan.py`) resolves a frame request to its
id and checks it against the id-range and mask rules instead of the blocklist (the blocklist has no
opinion on a non-diagnostic frame). `action_policy` is a no-op for a frame-only action: there is no
session policy to grant, because no diagnostic request is sent.

### 5. `release` for an injected frame

Stopping the stream returns the bus to normal (on the EU car nothing else sends `0x587`). But a
climate session, once the 15-minute timer has started, runs on its own timer; stopping the injection
does not end it. The honest `release` is an `AIRCONCTR_CANCEL` frame (`0x587` bit 38). That means the
action's `mask` must also permit bit 38, and the release frame sets it. Document plainly in the
action description and in `then`'s output that the release cancels a started session but cannot undo
a charge interaction. Decide with the user whether the first test ships with a cancel release or
relies on the 15-minute timeout.

## The on-car test this unlocks (needs explicit approval, separately)

Once the code and the action exist and the loopback test passes, propose to the user: put the WiCAN
in `slcan-tcp` mode, car plugged in and in P, run `canair action run remote-climate-start`, and
watch `VCU:2101` (`VCU_POWER_STATE`, inverter/LDC enables), `HVAC:2101` and `BMS:2101`
(`BMS_MAIN_RELAY`) plus 12 V draw. A climate start proves the OBD→P-CAN route and the VCU's
acceptance of an injected TMU frame. No change proves nothing on its own: the gateway may have
dropped the frame, or a precondition was unmet. The fallback the research names is a P-CAN tap
(IGPM connector I/P-G pins 1 and 2); this plan does not build that.

## Docs

- `docs/concepts/safety.md`: an "Enabling frame injection" section next to the `--unsafe` and
  actuation gates (what it is, the four layers, how to turn it on/off, the "what this guard is for"
  note). This is the most dangerous capability canair has; the section says so.
- `docs/concepts/` (the actions page, wherever `actions.yaml` is documented): the `frames:` request
  shape, the id-range and mask rules, and that it is `slcan-tcp` only.
- `config.example.yaml` (`safety:` block) and `docs/reference/config.md`: the new key.
- `src/canair/schema/`: if `actions.yaml` is schema-checked, add the `frames:` shape; else the
  structural checks in `actions/` stand in. Confirm which.
- `AGENTS.md`: one clause on the `canair action` entry — a frame-only action needs
  `safety.allow_frame_injection`, an agent never turns it on, `slcan-tcp` only. The file is
  ratcheted, so it must fit the existing lines.
- Skills: `ioniq-reverse-engineering` (the `0x587` path and this action), `reverse-engineer-signal`
  (the frame-injection action as a tool), one line each.
- Regenerate `docs/reference/cli/` if any `--help` changed.
- Update the `ecus/vcu.yaml` research entry to point at the action once it exists.

## Tests (device-free)

- `tests/actions/` (or wherever `actions` tests live): parse a `frames:` action; reject an id in
  `0x700`-`0x7FF`; reject `data` bits outside `mask`; reject a counter outside `mask`; reject mixing
  `frames:` and `requests:`; accept the climate template. `canair validate actions` catches each.
- The mask check proves every dangerous `0x587` bit (0, 1, 32-33, 36, 38) is outside the test
  action's `mask` unless the action deliberately names it.
- `RawTerminal.send_frame` puts the right id and bytes on a fake bus; `Elm327Terminal.send_frame`
  raises `TransportUnsupported`; a CAN-FD raw profile refuses.
- `_send_frames` over a fake terminal: the right number of frames at the right spacing, the counter
  advancing each frame, Ctrl-C stopping the stream and still running `release`/`then`.
- `mode_action` for a frame-only action over a fake terminal: refused when the condition is not
  TRUE, refused when the gate is off, confirmed name required, release sent on Ctrl-C. Add the row
  to `tests/cli/commands/test_live_operations.py`.
- `tests/config/`: `frame_injection_allowed()` reads only the user config and only YAML `true`;
  `config set` asks consent, refuses with no terminal, accepts `YES`.

## Resolved decisions (settled with the user, 2026-11-09)

1. **`TMU_RDY` (bit 34): not set.** The EU car has no telematics unit, so the first test keeps bit
   34 at 0 (outside the `mask`). Revisit only if the car ignores the frame.
2. **Gate: the global `safety.allow_frame_injection` config key**, for consistency with
   `allow_unsafe`/`allow_reset`. No per-profile quirk is required on top.
3. **Release: rely on the 15-minute timeout.** The first action ships with no `AIRCONCTR_CANCEL`
   release, so bit 38 stays outside the `mask`. Stopping the stream is enough; a started session
   ends on its own.
4. **An action may mix `frames:` and diagnostic `requests:`.** The "one kind per action" restriction
   was dropped: the capability is general.

## Still open (for the on-car test, not the code)

1. **The counter.** Is a free-running nibble enough, or does the VCU check the counter's *value*
   against a sequence or a CRC? The frame has no visible checksum in the layout, so probably a plain
   wrap counter, but unconfirmed.
2. **Does the OBD→P-CAN gateway forward `0x587` at all?** Probably not; the on-car test (or a P-CAN
   tap) settles it. A null result proves nothing on its own.
3. **A general `canair send-frame`.** Out of scope here on purpose: a free-form id+payload command
   has none of the profile-declared mask protection. If the user wants arbitrary frame injection for
   broader gateway-routing tests, that is a separate plan with its own (weaker) safety story, and it
   should still refuse the diagnostic id block and sit behind the same config gate.
