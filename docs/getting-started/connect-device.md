# Connect your dongle

canair reaches the CAN bus *through* an adapter — a WiCAN dongle, or a generic
ELM327 clone — it never talks CAN directly. This page gets your computer talking
to the dongle and confirms it's usable.

## 1. Plug in and power up

Plug the WiCAN into the vehicle's OBD-II port and switch the car to
ignition/accessory (so the ECUs are awake). The dongle powers from the port.

## 2. Get on the same network

Two options:

- **Access-point mode** — the WiCAN broadcasts its own `WiCAN_xxxx` WiFi
  network. Join it; the dongle is reachable at `192.168.80.1` (canair's default
  when no address is configured).
- **Your LAN** — use the WiCAN web UI to put the dongle on your home WiFi. It
  then gets a normal LAN IP you point canair at. This is the more convenient
  setup for repeated sessions.

## 3. Tell canair the address

Edit `~/.config/canair/config.yaml` or use `canair config set`:

```bash
canair config set devices.home.host 192.168.1.100
canair config set default_wican home
```

Any command takes `--wican home|vpn|<ip>` to pick which device to use. Each
device can carry its own transport (`canair config set devices.vpn.transport
wican-ws`), and when the chosen device is unreachable canair auto-falls-back to
the others (disable per-command with `--no-fallback`). Add `--wait` to any live
command to block until the device comes online and then start — handy for
`canair monitor @driving --save --wait`, which also keeps reconnecting if the
link drops mid-session. `config.example.yaml` in
the repo documents every key; see also the
[config reference](../reference/config.md).

## 4. Pro vs. classic

canair supports both the **WiCAN Pro** and the regular **classic** (non-Pro)
WiCAN. The default is `pro`; if you have a classic, tell canair so it cleanly
refuses Pro-only features instead of failing against the device:

```bash
canair config set wican_model classic
```

**Pro-only features:** AutoPID device sync (`wican autopid upload`/`download`/
`diff`), `wican mode set`, and the `wican-ws` WebSocket transport. All the core
reverse-engineering — query, scan, discover, decode, DTCs, sniff, and generating
AutoPID JSON — works on **both** over the default raw-SLCAN transport.

## Using a generic ELM327 clone (no WiCAN)

Don't have a WiCAN? A generic **WiFi ELM327 adapter** (Kiwi, vLinker, OBDLink, or
any no-name $10 clone) works over the **`elm327-tcp`** transport — a plain TCP
socket to the dongle's ELM327 terminal, no WiCAN required:

```bash
canair config set devices.clone.host 192.168.0.10   # the dongle's WiFi IP
canair config set devices.clone.transport elm327-tcp
canair config set devices.clone.port 35000           # most WiFi clones use 35000
canair config set default_wican clone
canair status                                        # confirm it's reachable
```

Join the dongle's WiFi network first (many clones host their own access point).
The ELM327 protocol engine is shared with `wican-ws`, so every canair command
works the same — the dongle performs ISO-TP. There's no HTTP config API on a
generic clone, so `canair status` reports transport reachability by probing the
ELM socket directly (no device/firmware block).

Want to try canair with **no hardware at all**? See
[Offline testing with ELM327-Emulator](offline-testing.md).

## 5. Confirm it's working

```bash
canair config    # config locations, WiCAN model + addresses, resolved transport
canair status    # what am I talking to, in what mode, is it reachable?
```

If `canair status` reports a reachable device, you're ready. New to the bundled
example car? Go to [Read live data](first-read.md). Building a profile for your
own car? Jump to [Bring your own car](../bring-your-own-car/overview.md).
