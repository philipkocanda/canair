# Direct ELM327 socket transport (`elm327-tcp`) + offline ELM327-Emulator testing

Status: in progress (2026-08-03) — commit 1 (channel/engine extraction) done.

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
- **Opt-in ELM327-Emulator integration fixture.** Add the emulator as a dev
  dependency + a pytest fixture that spawns it in TCP mode on an ephemeral port,
  skipped when unavailable — the core CI gate stays device-free and fast.

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

- Add `ELM327-emulator` to the `dev` dependency group.
- `tests/fixtures/elm_emulator.py` fixture: spawn `python -m elm -n <ephemeral>
  -s car`, wait for readiness, yield `host:port`, tear down. **Skip** (via
  `importorskip` / port check) when unavailable → core device-free CI stays green.
- `tests/test_elm327_tcp.py` (integration): `Elm327TcpTerminal` against the
  emulator — `init_elm`, `set_header` + single-frame `send_uds`, an ISO-TP
  **multi-frame** response, a `NO DATA`/NRC path, and one run through
  `dispatch_mode` (proving transport-agnosticism, per `TestDispatchTransportAgnostic`).
- Device-free unit tests (always run): `TcpChannel` framing (ASCII decode,
  `>`-boundary, partial-chunk reassembly); the `Elm327Terminal`/`WiCANTerminal`
  split (existing `test_terminal.py` passes unchanged); transport-config for
  `elm327-tcp` (incl. `is_wican_http` False); fallback probe-port selection.

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
3. **Offline testing:** emulator dev dep + opt-in fixture + integration tests.

## Out of scope (follow-up)

- `elm327-serial` + `pyserial` dep + `DeviceEntry.device`/`baudrate` + non-TCP
  liveness. The `Channel` seam is designed so `SerialChannel` drops in unchanged.
- Auto-detection of clone quirks/baudrate — explicit config only (matches the
  "never auto-switch" policy).
