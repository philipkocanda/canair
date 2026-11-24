# Architecture and code placement

Transports, the two data domains, WiCAN replaceability, and where code lives (layers).

## Transports — the most important architectural rule

The transport list, precedence and the rules a transport change must keep are in `AGENTS.md` →
WiCAN access and transports. The designs that span transports are `docs/development/frame-counts.md`
and `link-health.md`. Two families sit behind one surface: **raw** (`slcan-tcp`, python-can +
client-side ISO-TP in `RawTerminal`) and **ELM327** (`wican-ws`, `elm327-tcp`, device-side ISO-TP
in the shared `Elm327Terminal` engine over a swappable `Channel`).

All backends satisfy the `Terminal` protocol (`src/canair/transport/protocol.py`). Live commands run
through the **single shared** `src/canair/live/session.py::run_session_guarded`, called by both the
ELM path (`cli/live/runtime.py::async_main`) and the raw path (`live/read/raw_ops.py::run_raw`).

**The design contract:**

- Write mode handlers against the `Terminal` surface **only**. A command that goes through
  `run_session_guarded` and uses just those methods works on every transport for free.
- **Do not** open your own WebSocket, python-can bus, or socket, and do not branch on
  `transport.type` inside a mode. If you think you must, add the method to *all* terminal
  implementations instead so the mode stays transport-agnostic (matching signatures, identical
  returned dict shape — everything funnels through `src/canair/uds/parse.py::parse_uds_response`).
- A genuinely transport-specific optimization (e.g. the pipelined `raw_monitor` fast path) must
  *optimize* an already-working shared path, and the shared path must still work everywhere.

## Two data domains — diagnostics *and* raw CAN frames

Diagnostics (request/response UDS/KWP2000, decoded via `ecus/` signals) is mature; the raw broadcast
domain (`canair sniff`/`import can`/`correlate can`, `signals/<bus>.yaml`, `captures/can/`) is
younger but **parallel, not bolted on**. `SlcanTcpBus` (`transport/raw/slcan_tcp.py`) is a clean,
vehicle-neutral `can.BusABC` — the seam to build on. Keep the domains symmetric:

- **Capture parity** — frame captures go through the shared capture/journal machinery (scoped,
  schema-validated, journaled), not a second bespoke path.
- **Analysis parity** — a broadcast signal flows into the existing
  `decode`/`correlate`/`hunt`/`align` tools, not a bespoke analyzer.
- **Definition parity** — a broadcast signal map is the frame-domain analogue of a PID's signals:
  modelled in the profile with the same edit-via-tool/schema-validated discipline
  (`canair signals`).
- **Shared primitives, separate concerns** — frame parsing lives in its own module (mirroring
  `src/canair/uds/parse.py`); shared logic (byte/bit extraction, expression eval, correlation) goes
  in a neutral helper both domains call.

If a diagnostics assumption leaks into shared code (e.g. a loader assuming an ISO-TP `payload` +
`pid`), generalize the shared layer rather than special-casing frames.

## Keep the WiCAN replaceable

The WiCAN is one device we reach the bus through, not the tool's definition.

- **Bus access goes through `transport/`**, never a WiCAN assumption baked into a command or mode. A
  future `socketcan`, serial-SLCAN, or `.asc`/`.blf` *replay* transport should slot in by
  implementing the transport surface, with no command edits.
- **Isolate WiCAN-specifics** (mode switch, datarate/port discovery, reboot, AutoPID, AP-mode IP) in
  `device/wican/` and `cli/commands/wican/`. `sniff.py` still reaches into them for
  mode handling — route that through the transport/config layer when you touch it.
- `wican-ws` is WiCAN-coupled by nature; that's fine *behind* the transport surface. Don't let it
  leak upward.
- **Naming:** generic bus/frame/analysis code is named for what it does (`slcan`, `can`, `frame`,
  `bus`); keep device-specific code under `device/wican/`.

Litmus test: *would this feature still make sense from a SocketCAN interface or a replayed `.asc`?*
If it works "only with edits to the command", push the device-specific part into `transport/`.

## Placing and moving code — the layer structure

`src/canair/` is layered and the layering is enforced, not advisory. The map is
`docs/development/architecture.md` (placement table, what each package may import); the reasoning
and the migration are `plans/2026-11-04-package-layout-and-layering.md`. Read the page rather
than trusting a list copied here.

**Placing new code**: follow `AGENTS.md` → Code structure and `make structure`. Read the target
package's `__init__.py` charter (*Belongs / Not here / Imports*) first, and if the code does not fit
it, ask rather than stretch the package. If a gate fails, change the structure, not the gate.
Never add a function-level import whose only purpose is to hide a cycle.

**Moving code** (one package per commit, mechanical, nothing else in the commit)

1. `git mv`, then rewrite every import, every `monkeypatch.setattr("canair.…")` string and the
   dotted names in `[tool.importlinter]`. No compatibility alias for the old path.
2. Move the package's tests in the same commit.
3. Remove the `ignore_imports` entries the move fixes and lower the baselines
   (`uv run python -m tests.contracts.test_structure --write`). That command also drops the
   baseline entry of a module that no longer exists, so rename the key of a moved oversized
   module in `baseline.json` by hand first, or its ceiling is lost.
4. Give a new package its charter and regenerate the architecture page
   (`uv run python scripts/gen/gen_architecture.py`).
5. Confirm every `--help` is unchanged (`scripts/gen/gen_cli_reference.py --check`), then run the
   full suite.
6. A codemod drops the alias of `from canair import module as alias` and skips the files it moves,
   so read the `ruff` output after `--fix` for undefined names, and watch the ceilings: an import
   that lands in its own isort group adds a line.
