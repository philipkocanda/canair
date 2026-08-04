# Direct ELM327 socket transport (`elm327-tcp`) + offline ELM327-Emulator testing

Status: **DONE** (2026-08-03) — all three phased commits landed: commit 1
(channel/engine extraction), commit 2 (`elm327-tcp` transport), commit 3 (offline
ELM327-Emulator testing), plus the "leading-zero PID keys" follow-up fix.
**Deferred (out of scope, by design):** the `elm327-serial` transport
(`SerialChannel` + `pyserial` + `DeviceEntry.device`/`baudrate` + non-TCP liveness
probing). Clone-quirk/baudrate auto-detection is **rejected**, not deferred.

## Motivation

Today canair reaches the bus through exactly two transports, both assuming a
**WiCAN** device:

- `slcan-tcp` → `RawTerminal` (python-can `SlcanTcpBus` + client-side ISO-TP).
- `wican-ws` → `WiCANTerminal` (`canlib/terminal.py`) — a full **ELM327 protocol
  engine** whose only WiCAN-specific pieces are the WebSocket connect +
  `{"ws_mode":"terminal"}` handshake, the `{"type":"term_out","data":…}` JSON
  unwrap in the recv loop, and `reboot_wican` (HTTP).

But every generic $10 ELM327 clone (Kiwi WiFi, vLinker, OBDLink, no-name WiFi
dongles) speaks the **same ELM327 ASCII protocol** — just over a plain TCP socket
(WiFi clones, default port 35000) or a serial port (USB / Bluetooth-SPP) instead
of WiCAN's WebSocket-JSON channel. Supporting them is not a new protocol; it is a
new **byte channel** under the existing ELM327 engine.

This also unlocks **device-free testing**: [ELM327-Emulator](https://github.com/ircama/ELM327-emulator)
serves the identical ELM327 protocol over TCP (`elm -n 35000`) or a pty (serial),
including ISO-TP FF/CF/FC multi-frame and KWP2000 — a perfect offline target for
the new transport.

## Decisions (confirmed with the user)

- **Extract a `Channel` seam** (recommended): pull the ELM327 engine out of the
  WiCAN-specific channel into a transport-agnostic `Elm327Terminal` parameterized
  by an async byte `Channel`. WiCAN, direct-TCP (now), and serial (follow-up) all
  share one battle-tested engine. Avoids the "duplicated and divergent" antipattern
  the contributing-code skill warns against (the delicate `_send_command_locked`
  loop must exist exactly once).
- **TCP first, serial as a designed-for follow-up.** Ship `elm327-tcp` now;
  design the `Channel` seam so a `SerialChannel` drops in with no engine change.
  Serial adds a `pyserial` dep + `device`/`baudrate` config + non-TCP liveness —
  deferred.
- **Opt-in ELM327-Emulator integration test.** A pytest test that spawns the
  emulator in TCP mode on an ephemeral port, **skipped when the `elm` package
  isn't importable** — the core CI gate stays device-free and fast. The emulator
  is **NOT** a canair dev dependency: its legacy build imports `pkg_resources`
  and breaks a clean `uv sync` (needs `setuptools<80` + `--no-build-isolation`),
  so it's a documented manual install instead.

## Architecture

### 1. `Channel` seam (`canlib/transport/channel.py`, new)

The async byte-stream seam an ELM327 engine talks to; each channel owns its own
framing/decoding.

```
class Channel(Protocol):
    async def connect(self) -> None: ...
    async def send(self, text: str) -> None: ...
    async def recv(self, timeout: float) -> str | None: ...   # decoded terminal text, None on timeout
    async def drain(self, per_recv_timeout: float, max_seconds: float) -> None: ...
    async def close(self) -> None: ...
```

- `WebSocketChannel(host)` — today's `websockets.connect` + `{"ws_mode":…}`
  handshake + `{"type":"term_out","data":…}` JSON unwrap, moved out of
  `WiCANTerminal`.
- `TcpChannel(host, port=35000)` — `asyncio.open_connection`; `recv` decodes raw
  ASCII (no JSON), reassembling partial chunks. No handshake.
- (follow-up) `SerialChannel(device, baudrate)` — pyserial in an executor thread.

### 2. `Elm327Terminal` engine (`canlib/transport/elm327_terminal.py`, new)

The transport-agnostic ELM327 engine, holding a `Channel`. Moves the generic
logic **verbatim** out of `terminal.py`: the `_send_command_locked` `>`-prompt
accumulation + `7F..78` ResponsePending loop + pipe-dirty drain, `_track_header`,
`set_header`, `send_uds`, `enter_extended_session`, `init_elm`, timing/diag
(`self.timings`, `self.diag`), header caching, `ecu_timeouts`. The only edit to
that loop: `self.ws.recv()`/`self.ws.send()` → `self._channel.recv()` /
`self._channel.send()`; JSON parsing moves into `WebSocketChannel`. Satisfies the
`Terminal` protocol structurally (ty-checked; runtime `isinstance` smoke test).

### 3. `WiCANTerminal` (`canlib/terminal.py`, reduced)

Thin subclass: `Elm327Terminal(WebSocketChannel(host))` + the WiCAN-only HTTP
`reboot`. Keeps the class name (imported widely, referenced by tests). **Zero
behavior change** on the WebSocket path — commit 1 is a pure structural refactor,
all existing tests green.

### 4. `Elm327TcpTerminal` (commit 2)

`Elm327Terminal(TcpChannel(host, port))`. The whole engine is inherited.

### 5. Transport registry & config (`transport/config.py`, commit 2)

- Register `elm327-tcp` in `TRANSPORTS` with `raw=False`.
- Add a **`wican_http: bool`** field to `TransportSpec` (True for
  `slcan-tcp`/`wican-ws`, False for `elm327-tcp`); `TransportConfig.is_wican_http`
  reads the spec instead of `host is not None`. This is the clean, registry-driven
  fix for "a $10 clone has no HTTP API" and matches the module's stated intent.
- `port` for `elm327-tcp` is the ELM socket port (default 35000). Host+port
  suffice; no new `DeviceEntry` fields for TCP (serial follow-up adds
  `device`/`baudrate`).
- Update the `--transport` help (`_live.py`) + `config.example.yaml` docs.

### 6. Connection dispatch (`commands/_live.py`, commit 2)

- `async_main`: replace the two-way `if transport.is_raw … else WebSocket` with
  spec-driven routing: raw → `run_raw`; else build the right ELM terminal by type.
- Generalize `connect_elm_terminal` into a factory keyed on `transport.type`
  (`WiCANTerminal` for `wican-ws`, `Elm327TcpTerminal` for `elm327-tcp`); the ELM
  init/ATST/per-ECU-budget setup already lives in the engine, so it is shared.
- Guard WiCAN-only calls behind `is_wican_http`: skip `require_ws_reachable` (use
  a plain TCP probe of `host:port` for `elm327-tcp`), `_print_sleep_banner`,
  `reboot_wican`. `build_elm_reconnector` reuses the same factory → reconnect
  works for free.

### 7. Fallback / liveness (`transport/fallback.py`, commit 2)

`_probe_port`: `is_wican_http` → 80 (unchanged); `elm327-tcp` → the data port
(`cand.port or 35000`). A live TCP probe of the ELM socket is a valid liveness
signal.

### 8. `canair status` (commit 2)

Split the `is_elm` branch: WiCAN-HTTP ELM device keeps today's `/load_config` +
`/check_status` probes; `elm327-tcp` (no HTTP) skips them and reports usability by
a TCP probe of `host:port`. Suppress the "WiCAN" device block for non-WiCAN
transports.

### 9. Errors

`transport_error_types()` already covers the `OSError` family (TCP failures, and
serial `SerialException ⊂ OSError` for the follow-up). Add an "ELM327/TCP"
`transport_label` at the call sites.

## Offline testing with ELM327-Emulator (commit 3, opt-in)

- The emulator is **not** a canair dependency (its legacy build imports
  `pkg_resources`, breaking `uv sync`); install it manually for offline testing:
  `uv pip install "setuptools<80"` then
  `uv pip install --no-build-isolation ELM327-emulator`.
- `tests/test_elm327_emulator.py` fixture: spawn `python -m elm -n <ephemeral>`,
  wait for readiness, yield `host:port`, tear down. **Skips** (via
  `importorskip("elm")`) when unavailable → core device-free CI stays green. The
  fixture is function-scoped (the emulator's `-n` mode serves one client).
- Integration assertions: `Elm327TcpTerminal` against the emulator — `ATI`
  round-trip (the transport proof), a stable stateless PID (`0105` → `4105…`),
  and `ATRV`. (`0100` bus-init and multi-frame VIN are emulator-flaky over TCP —
  exercised manually, not asserted.)

## Docs (required — same-change policy)

- `docs/concepts/architecture.md` — add `elm327-tcp` to Transports + mermaid;
  reframe "through the WiCAN" → "through a WiCAN **or a direct ELM327 adapter**."
- `docs/getting-started/connect-device.md` + `docs/reference/config.md` —
  configure a WiFi ELM327 clone (`transport.type: elm327-tcp`, host/port, a
  `devices:` entry).
- New `docs/getting-started/offline-testing.md` — running ELM327-Emulator
  (`elm -n 35000`) and pointing canair at `localhost:35000`.
- `README.md` — one terse transport/quick-start line linking into docs.
- `AGENTS.md` — extend the WiCAN Access / transports section with `elm327-tcp`.
- `CHANGELOG.md` `[Unreleased]` — new transport + offline-testing entry.

## Phasing (commits)

1. **Pure refactor:** `Channel` + `Elm327Terminal`; reduce `WiCANTerminal` to
   engine + `WebSocketChannel`. All existing tests green — structural only.
   **DONE** — `transport/channel.py` (`Channel` protocol + `WebSocketChannel`),
   `transport/elm327_terminal.py` (`Elm327Terminal` engine), `terminal.py`
   reduced to `WiCANTerminal(Elm327Terminal)` + `ws`/`url` proxy + `reboot_wican`.
   `skm_wakeup` rerouted `terminal._drain()` → `terminal._channel.drain()`.
   3410 tests + ruff + ty green; zero behavior change on the WebSocket path.
2. **Add `elm327-tcp`:** `TcpChannel`, `Elm327TcpTerminal`, registry + `wican_http`
   spec field, dispatch/status/fallback decoupling, unit tests, docs.
   **DONE** — `TcpChannel` + `Elm327TcpTerminal`; `TransportSpec.wican_http` +
   spec-driven `is_wican_http`; `DEFAULT_ELM327_TCP_PORT` (35000);
   `connect_elm_terminal(transport, …)` factory + `require_elm327_tcp_reachable`;
   `_probe_port`/`status`/`--transport` help decoupled from WiCAN. Tests:
   `test_elm327_tcp.py` (TcpChannel framing + engine-over-arbitrary-channel) +
   config/fallback cases.
3. **Offline testing:** emulator dev dep + opt-in fixture + integration tests.
   **DONE** — opt-in `tests/test_elm327_emulator.py` (spawns `elm -n <port>`,
   auto-skips when the `elm` package is absent — it's NOT a dep, its legacy build
   breaks `uv sync`). Docs: `docs/development/offline-testing.md`.

## Follow-up (post-review)

Moved the offline-testing doc into a new **Development** docs section (was under
Getting started) and added a **test profile** so `canair read` decodes against
the emulator out of the box:

- `tests/fixtures/profiles/elm327-emulator/` — an `ENGINE` ECU at `0x7E0` with
  standard OBD-II Mode-01 PIDs (`010C` RPM, `010D` speed, `010F` intake temp).
  Its `init` disables echo/headers and filters to `7E8` (`ATCRA7E8`) so the
  emulator's second simulated ECU doesn't duplicate every response. Not shipped
  in the wheel (under `tests/`, not top-level `profiles/`), so it never pollutes
  a real user's `canair profile list`.
- `tests/test_elm327_tcp.py::TestEmulatorProfile` guards the profile's decode
  expressions device-free (via the real `decode_param_rows` path).
- Doc refinements: reconnect-gap / `--wait` tip; flags-vs-config distinction
  (port is config-only; `--wican` is host-only; prefer `127.0.0.1` over
  `localhost`); "run `elm` in its own terminal" caveat.

**Leading-zero PID keys — FIXED (follow-up commit).** The surgical `canair pids`
editor now round-trips a PID key that is all decimal digits with a leading zero
(`0105`, `0902`) — standard OBD-II Mode-01/09 PIDs. Root cause: the key was
written bare and YAML re-parsed it as an int (losing the zero), so the editor's
key lookup failed and reverted. Fix: `canlib/pids_edit/_text.py::_key_token`
double-quotes a key whenever a bare token wouldn't stringify back to itself
(leaving ordinary keys like `2101` unquoted, so existing profiles don't churn),
and `_keyed_block` now matches an optionally-quoted key. Applied at every PID-key
emission site (`add_pid`/`upsert_parameter`/`rename_pid`). Regression tests in
`tests/test_pids_edit_params.py::TestLeadingZeroPidKeys`.

## Out of scope (follow-up)

- `elm327-serial` + `pyserial` dep + `DeviceEntry.device`/`baudrate` + non-TCP
  liveness. The `Channel` seam is designed so `SerialChannel` drops in unchanged.
- Auto-detection of clone quirks/baudrate — explicit config only (matches the
  "never auto-switch" policy).
