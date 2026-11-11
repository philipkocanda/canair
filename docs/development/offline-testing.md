# Offline testing with ELM327-Emulator

You can drive canair with **no dongle and no car**. Point canair at
[ELM327-Emulator](https://github.com/ircama/ELM327-emulator). This Python program emulates an
ELM327 adapter (multi-ECU, ISO-TP, KWP2000) and serves the same ELM327 protocol that canair speaks.
It helps you to try commands, to develop, or to debug the
[`elm327-tcp`](../getting-started/connect-device.md#using-a-generic-elm327-clone-no-wican)
transport without hardware. ECU means electronic control unit. ISO-TP is the transport protocol of
the Controller Area Network (CAN). KWP2000 is Keyword Protocol 2000.

## 1. Install the emulator

The emulator ships a legacy build that imports `pkg_resources` at build time. Recent `setuptools`
no longer provides it. Install an older `setuptools` first, and install the emulator without build
isolation:

```bash
uv pip install "setuptools<80"
uv pip install --no-build-isolation ELM327-emulator
```

The emulator is deliberately **not** a canair dependency, because its build breaks a clean
`uv sync`. This is a one-off manual step.

## 2. Start it in TCP mode

```bash
elm -n 35000            # serve the ELM327 terminal on TCP port 35000
```

The command serves on a Transmission Control Protocol (TCP) port. `35000` is the default port of
canair for `elm327-tcp`. If you use it, you can skip the port config below.

!!! warning "Run it in its own terminal"
    `elm` is an **interactive** program. It drops you at a `CMD>` prompt. Run it in a dedicated
    terminal and leave it in the foreground. If you run it in the background or close its stdin,
    its read-eval-print loop (REPL) hits end-of-input and **exits**. Its `-n` mode also serves
    **one client at a time** (see the reconnect note below).

## 3. Point canair at it

There are two ways to do this. The difference matters because of one limitation: **the port is
config-only**. `--wican` takes a bare host (no `host:port`), and there is no `--port` flag. The
flags-only route therefore works only on the default port `35000`.

=== "Quick (flags, port 35000 only)"

    ```bash
    canair status --transport elm327-tcp --wican 127.0.0.1
    ```

    Use `127.0.0.1`, not `localhost`. `localhost` can resolve to IPv6 `::1` while the emulator
    listens on IPv4. This gives a spurious "not reachable".

=== "Persistent (config, any port)"

    ```bash
    canair config set devices.emu.host 127.0.0.1
    canair config set devices.emu.transport elm327-tcp
    canair config set devices.emu.port 35000        # required for a non-35000 -n port
    canair config set default_wican emu

    canair status                                    # confirm it's reachable
    ```

    Switch back to your real device later with `canair config set default_wican <alias>`
    (or `canair config unset default_wican`).

## 4. Use the test profile for real reads

The emulator answers **generic On-Board Diagnostics II (OBD-II)** at the ECU header `0x7E0`. It
does not answer the manufacturer data identifiers (DIDs) of a specific car. The reads of the
bundled Ioniq profile (`BMS:2101`) therefore get `NO DATA`. The repo ships a small
**`elm327-emulator` profile** for exactly this case. The profile has an `ENGINE` ECU (`0x7E0`)
with standard Mode-01 parameter identifiers (PIDs) that decode against the emulator.

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

The `init` string of the profile disables echo and headers (`ATE0;ATH0;…`). It also filters to the
primary ECU (`ATCRA7E8`), so responses are the bare, un-duplicated Unified Diagnostic Services
(UDS) payload. The profile is a good template if you want to add more PIDs. canair handles
standard OBD-II PID keys, including all-decimal ones with a leading zero such as `0105`.

## Reconnect gap & `--wait`

The emulator rebuilds its TCP listener after each client disconnects. For a brief window, the
emulator refuses connections. Back-to-back canair commands can therefore occasionally fail with
"connection refused". Retry, or add **`--wait`** (any live command). `--wait` blocks and retries
until the emulator is reachable, then starts:

```bash
canair --profile $P monitor ENGINE:010C --transport elm327-tcp --wican 127.0.0.1 --wait
```

## What works, what doesn't

The emulator is a functional stand-in, not a real car:

- **Good for:** exercising the `elm327-tcp` transport end to end (connect, ELM init, AT commands,
  single-frame OBD/UDS reads), scripting, and checks of the kind that continuous integration (CI)
  runs.
- **Generic OBD-II only:** its scenarios answer standard Mode-01/09 PIDs, not the manufacturer
  DIDs of a profile. Use the `elm327-emulator` test profile (above), or extend the emulator with a
  custom scenario (`elm -s <scenario>`).
- **Flaky PIDs:** `0100` ("supported PIDs") simulates a multi-second `SEARCHING…` bus-init and is
  unstable over TCP. The emulator does not reliably reassemble multi-frame ISO-TP (the vehicle identification number, `0902`)
  over headers-off TCP. Prefer stable stateless PIDs (`010C`, `010D`, `010F`, `0105`,
  `ATRV`).

## Automated tests

canair ships an **opt-in** integration test (`tests/integration/test_elm327_emulator.py`). It
spawns the emulator on an ephemeral port and drives it through the real `elm327-tcp` engine. It
**auto-skips** when the emulator is not installed, so the normal `uv run pytest` stays device-free
and fast. To run it, install the emulator (step 1). Device-free unit tests
(`tests/transport/elm327/test_elm327_tcp.py`) also guard the decode expressions of the
`elm327-emulator` profile.

!!! note "`uv sync` uninstalls the emulator"

    The emulator is deliberately **not** a dev dependency, because its legacy build needs
    `setuptools<80`. It therefore lives outside `uv.lock`, and `uv sync` prunes anything that is
    not in the lock. After a sync, the test reports one `SKIPPED` instead of failing. That is easy
    to miss. Re-run step 1 to get the emulator back.
