# Offline testing with ELM327-Emulator

You can drive canair with **no dongle and no car** by pointing it at
[ELM327-Emulator](https://github.com/ircama/ELM327-emulator) — a Python program
that emulates an ELM327 adapter (multi-ECU, ISO-TP, KWP2000) and serves the same
ELM327 protocol canair speaks. It's ideal for trying commands, developing, or
debugging the [`elm327-tcp`](connect-device.md#using-a-generic-elm327-clone-no-wican)
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

The emulator prints the port it's listening on. Its `-n` mode serves a **single**
client at a time. Add `-s car` for a Toyota-flavoured PID set (the default
scenario already answers standard OBD-II PIDs).

## 3. Point canair at it

```bash
canair status --transport elm327-tcp --wican localhost:35000
```

or make it the default so every command uses it:

```bash
canair config set devices.emu.host localhost
canair config set devices.emu.transport elm327-tcp
canair config set devices.emu.port 35000
canair config set default_wican emu

canair status
canair read 0105          # engine coolant temp — a stable stateless PID
```

## What works, what doesn't

The emulator is a functional stand-in, not a real car:

- **Great for:** exercising the `elm327-tcp` transport end-to-end (connect, ELM
  init, AT commands, single-frame OBD/UDS reads), scripting, and CI-style checks.
- **Quirks:** the `0100` "supported PIDs" request simulates bus-init with a
  multi-second `SEARCHING…` delay and can be flaky over TCP — prefer stable
  stateless PIDs like `0105` / `010C` / `ATRV`. Multi-frame ISO-TP responses
  (e.g. VIN `0902`) aren't reliably reassembled by the emulator over headers-off
  TCP. Its scenarios are generic OBD-II, not a specific profile's DIDs.

## Automated tests

canair ships an **opt-in** integration test
(`tests/test_elm327_emulator.py`) that spawns the emulator on an ephemeral port
and drives it through the real `elm327-tcp` engine. It **auto-skips** when the
emulator isn't installed, so the normal `uv run pytest` stays device-free and
fast. Install the emulator (step 1) to run it.
