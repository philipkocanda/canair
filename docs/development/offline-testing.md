# Offline testing with ELM327-Emulator

You can drive canair with **no dongle and no car** by pointing it at
[ELM327-Emulator](https://github.com/ircama/ELM327-emulator) — a Python program
that emulates an ELM327 adapter (multi-ECU, ISO-TP, KWP2000) and serves the same
ELM327 protocol canair speaks. It's ideal for trying commands, developing, or
debugging the [`elm327-tcp`](../getting-started/connect-device.md#using-a-generic-elm327-clone-no-wican)
transport without hardware.

## 1. Install the emulator

The emulator ships a legacy build that imports `pkg_resources` at build time,
which recent `setuptools` no longer provides — so install an older `setuptools`
first, and install the emulator without build isolation:

```bash
uv pip install "setuptools<80"
uv pip install --no-build-isolation ELM327-emulator
```

It is deliberately **not** a canair dependency (its build breaks a clean
`uv sync`), so this is a one-off manual step.

## 2. Start it in TCP mode

```bash
elm -n 35000            # serve the ELM327 terminal on TCP port 35000
```

`35000` is canair's default port for `elm327-tcp`, so using it lets you skip the
port config below.

!!! warning "Run it in its own terminal"
    `elm` is an **interactive** program (it drops you at a `CMD>` prompt). Run it
    in a dedicated terminal and leave it in the foreground — if you background it
    or otherwise close its stdin, its REPL hits end-of-input and **exits**. Its
    `-n` mode also serves **one client at a time** (see the reconnect note below).

## 3. Point canair at it

There are two ways; the difference matters because of one limitation: **the port
is config-only** — `--wican` takes a bare host (no `host:port`), and there is no
`--port` flag. So the flags-only route works only on the default port `35000`.

=== "Quick (flags, port 35000 only)"

    ```bash
    canair status --transport elm327-tcp --wican 127.0.0.1
    ```

    Use `127.0.0.1`, not `localhost` — `localhost` can resolve to IPv6 `::1`
    while the emulator listens on IPv4, giving a spurious "not reachable".

=== "Persistent (config, any port)"

    ```bash
    canair config set devices.emu.host 127.0.0.1
    canair config set devices.emu.transport elm327-tcp
    canair config set devices.emu.port 35000        # required for a non-35000 -n port
    canair config set default_wican emu

    canair status                                    # confirm it's reachable
    ```

    Switch back to your real device later with `canair config set default_wican
    <alias>` (or `canair config unset default_wican`).

## 4. Use the test profile for real reads

The emulator answers **generic OBD-II** at ECU header `0x7E0`, not a specific
car's manufacturer DIDs — so the bundled Ioniq profile's reads (`BMS:2101`) get
`NO DATA`. The repo ships a tiny **`elm327-emulator` profile** built for exactly
this: an `ENGINE` ECU (`0x7E0`) with standard Mode-01 PIDs that decode against
the emulator.

```bash
P=tests/fixtures/profiles/elm327-emulator
canair --profile $P read ENGINE:010F --transport elm327-tcp --wican 127.0.0.1   # intake temp
canair --profile $P read "ENGINE:010D ENGINE:010C ENGINE:010F" \
    --transport elm327-tcp --wican 127.0.0.1
```

```
  ENGINE (0x7E0)
    010D  VEHICLE_SPEED   16 km/h
    010C  ENGINE_RPM     740 rpm
    010F  INTAKE_TEMP_C   28 degC
```

Its `init` string disables echo/headers (`ATE0;ATH0;…`) and filters to the
primary ECU (`ATCRA7E8`) so responses are the bare, un-duplicated UDS payload.
It's a fine template if you want to add more PIDs — standard OBD-II PID keys
(including all-decimal ones with a leading zero like `0105`) are handled.

## Reconnect gap & `--wait`

The emulator rebuilds its TCP listener after each client disconnects, so a brief
window exists where connecting is refused — back-to-back canair commands can
occasionally fail with "connection refused". Just retry, or add **`--wait`** (any
live command), which blocks and retries until the emulator is reachable, then
starts:

```bash
canair --profile $P monitor ENGINE:010C --transport elm327-tcp --wican 127.0.0.1 --wait
```

## What works, what doesn't

The emulator is a functional stand-in, not a real car:

- **Great for:** exercising the `elm327-tcp` transport end-to-end (connect, ELM
  init, AT commands, single-frame OBD/UDS reads), scripting, and CI-style checks.
- **Generic OBD-II only:** its scenarios answer standard Mode-01/09 PIDs, not a
  profile's manufacturer DIDs. Use the `elm327-emulator` test profile (above), or
  extend the emulator with a custom scenario (`elm -s <scenario>`).
- **Flaky PIDs:** `0100` ("supported PIDs") simulates a multi-second `SEARCHING…`
  bus-init and is unstable over TCP; multi-frame ISO-TP (VIN `0902`) isn't
  reliably reassembled over headers-off TCP. Prefer stable stateless PIDs
  (`010C`, `010D`, `010F`, `0105`, `ATRV`).

## Automated tests

canair ships an **opt-in** integration test (`tests/test_elm327_emulator.py`)
that spawns the emulator on an ephemeral port and drives it through the real
`elm327-tcp` engine. It **auto-skips** when the emulator isn't installed, so the
normal `uv run pytest` stays device-free and fast. Install the emulator (step 1)
to run it. Device-free unit tests (`tests/test_elm327_tcp.py`) additionally guard
the `elm327-emulator` profile's decode expressions.

!!! note "`uv sync` uninstalls the emulator"

    The emulator is deliberately **not** a dev dependency (its legacy build needs
    `setuptools<80`), so it lives outside `uv.lock` — and `uv sync` prunes
    anything not in the lock. After a sync the module simply reports one
    `SKIPPED` instead of failing, which is easy to miss. Re-run step 1 to get it
    back.
