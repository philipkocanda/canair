# canair documentation

**CLI for reverse-engineering CAN/OBD diagnostics over-the-air with a WiCAN dongle.**

canair works with a vehicle's data across **two domains** through a
[WiCAN](https://www.meatpi.com/products/wican-pro) WiFi dongle: request/response
**diagnostics** (querying ECUs over UDS and KWP2000) and the **raw broadcast
CAN** the car emits on its own (passively sniffed, or imported from external
logs and DBCs). It then helps you capture, decode, statistically correlate, and
document that data — turning it into a shareable
[vehicle profile](concepts/profiles.md).

## See a real car's data, right in your browser

<div class="cx-hero">
  <p>The <strong>profile explorer</strong> lets you browse a whole vehicle profile and then
  plot what the car actually reported. Click an ECU on the CAN bus map, put signals from
  different ECUs on one timeline, and let it point out which signals move together. No install,
  nothing to connect.</p>
  <p><a class="md-button md-button--primary" href="profiles/explorer/">Open the explorer →</a>
  <span class="cx-muted">Try it on the 2017 Ioniq: 30 ECUs, 435 signals, 98 recorded drives and charges.</span></p>
  <div class="cx-hero-cards">
    <a href="profiles/explorer/#ioniq-2017/BMS">
      <img src="assets/explorer/explorer-bus.png#only-light" alt="CAN bus map of the Ioniq's ECUs, coloured by how much of each is decoded" loading="lazy">
      <img src="assets/explorer/explorer-bus-dark.png#only-dark" alt="CAN bus map of the Ioniq's ECUs, coloured by how much of each is decoded" loading="lazy">
      <strong>Browse the buses</strong>
      <span>See which ECU sits on which CAN segment, then open any ECU for its PIDs, signals, actuators and research leads, each linked to its YAML.</span>
    </a>
    <a href="profiles/explorer/#ioniq-2017/plot/2026-08-08-e84a15?s=BMS:2101:BATTERY_POWER,ESC:22C101:REAL_SPEED_KMH,BMS:2101:SOC_BMS">
      <img src="assets/explorer/explorer-plot.png#only-light" alt="Battery power, speed and state of charge plotted together over a drive, a fast charge and another drive" loading="lazy">
      <img src="assets/explorer/explorer-plot-dark.png#only-dark" alt="Battery power, speed and state of charge plotted together over a drive, a fast charge and another drive" loading="lazy">
      <strong>Plot signals from any ECU together</strong>
      <span>A drive, a 60 kW fast charge and another drive in one picture: battery power, speed and state of charge, with the vehicle states above.</span>
    </a>
    <a href="profiles/explorer/#ioniq-2017/plot/2026-08-08-e84a15?s=BMS:2101:BATTERY_POWER">
      <img src="assets/explorer/explorer-overview.png#only-light" alt="Heat-strip overview of every signal that changed during a session, similar signals grouped" loading="lazy">
      <img src="assets/explorer/explorer-overview-dark.png#only-dark" alt="Heat-strip overview of every signal that changed during a session, similar signals grouped" loading="lazy">
      <strong>Find what hides in the data</strong>
      <span>One strip shows every signal that moved in a session, similar ones side by side. Select a signal to see what follows it, or zoom to a moment to see what reacted.</span>
    </a>
  </div>
</div>

![canair analysis commands and the interactive decode --plot signal explorer in action](screenshots/tour.gif)

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
- **Learn from a real hunt? →** [Case studies](case-studies/index.md) narrate
  actual reverse-engineering investigations end-to-end — the wrong turns
  included — like [finding the hidden AC input voltage](case-studies/ac-input-voltage.md).
- **Just want to look around? →** the [profile explorer](profiles/explorer.md) plots real
  Ioniq drives and charges in your browser, with nothing to install.
- **Just want to use a bundled car? →** [Bundled profiles](profiles/index.md)
  lists what ships (the mature [Ioniq 2017 profile](profiles/ioniq-2017.md) and
  more) — their ECUs, decoded signals, and IOControl actuators.

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

## Contribute back 🎉

canair gets more useful with every car and signal people share. If you
reverse-engineer *your* vehicle — a whole profile, a few decoded parameters, or a
fix — **please contribute it back via a pull request** to
[`philipkocanda/canair`](https://github.com/philipkocanda/canair). It's the
single best way to help others with the same car. See
[Contributing](contributing/index.md) and
[Share your profile](bring-your-own-car/08-share.md#contribute-your-profile-back).
