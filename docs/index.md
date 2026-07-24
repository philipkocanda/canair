# canair documentation

**CLI for reverse-engineering CAN/OBD diagnostics over-the-air with a WiCAN dongle.**

canair talks to your vehicle's ECUs over UDS and KWP2000 through a
[WiCAN](https://www.meatpi.com/products/wican-pro) WiFi dongle, then helps you
discover, capture, decode, and document its internal diagnostic data — turning
it into a shareable [vehicle profile](concepts/profiles.md).

These docs are organized around **what you're trying to do**, not around the
command list (that lives in the [CLI reference](reference/cli/index.md)).

## Pick your path

- **New here? →** [Getting started](getting-started/install.md) — install,
  connect your dongle, and read live data from the bundled example car in a few
  minutes.
- **Own a car that isn't the bundled Ioniq? →**
  [Bring your own car](bring-your-own-car/overview.md) — the headline journey:
  go from an empty profile to decoded, verified signals for *your* vehicle.
- **Contributing PID definitions? →**
  [Define & verify](bring-your-own-car/07-define-and-verify.md) and
  [Share your profile](bring-your-own-car/08-share.md) — how a reverse-engineered
  signal becomes a validated parameter others can use.
- **Want the details? →** [Concepts](concepts/architecture.md) explain *how* it
  works; the [Reference](reference/config.md) documents every flag, config key,
  and schema.
- **Just want to use the bundled Ioniq? →** [The Ioniq 2017
  profile](profiles/ioniq-2017.md) — what the shipped profile decodes, its ECUs,
  and its IOControl actuators.

## What you'll need

- A **WiCAN dongle** — Pro *or* classic (non-Pro). Most features work on both;
  a few are [Pro-only](getting-started/connect-device.md).
- A vehicle with an **OBD-II port**.
- [`uv`](https://docs.astral.sh/uv/) to install and run canair.

## A note on safety

Reading data is safe and free — canair only *reads* unless you explicitly ask it
to actuate hardware, and every state-changing action confirms first. That said,
**interacting with a vehicle's CAN bus carries real risk**. See
[Safety](concepts/safety.md) for what canair will and won't do, and why.
