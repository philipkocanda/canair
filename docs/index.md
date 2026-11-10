# canair documentation

**CLI for reverse engineering CAN/OBD diagnostics over the air with a WiCAN dongle.**

canair uses a [WiCAN](https://www.meatpi.com/products/wican-pro) WiFi dongle to work with the data
of a vehicle in **two domains**:

- **Diagnostics.** canair sends requests to the electronic control units (ECUs) of the car. It
  uses the UDS (Unified Diagnostic Services) and KWP2000 (Keyword Protocol 2000) protocols.
- **Raw broadcast CAN.** The car sends these Controller Area Network (CAN) frames on its own.
  canair listens passively, or imports frames from external logs and DBC (CAN database) files.

canair helps you capture, decode, correlate and document this data. The result is a
[vehicle profile](concepts/profiles.md) that you can share.

## Why canair?

- **Evidence stays with the definition.** Most tools store only the PID, DID and signal
  definitions. canair also stores the captures that support each definition. Anyone can check a
  definition against the data, and the community can decode together. A definition without proof
  has little worth.
- **Two kinds of data in one profile.** canair combines request/response diagnostics (OBD-II,
  UDS, KWP2000) with raw broadcast CAN frames (for example from DBC files). Both kinds use the
  same profile and the same analysis tools. What you know about a signal in one kind of data helps
  you find the same signal in the other.
- **Made for humans and agents.** Helpers import and analyze both kinds of captures. Every
  capability is a subcommand that you can script, with structured output (`--json`). A person or
  an AI coding agent can use it in the same way. The reverse-engineering workflows are available
  as agent skills.
- **Shareable without copied definitions.** Profiles come from captures of real cars. They do not
  copy the definitions of a dealer scan tool. People can share them without copying the content of
  a vendor tool. This opens vehicle maintenance and diagnostics to a wider community.


## See a real car's data, right in your browser

<div class="cx-hero">
  <p>The <strong>profile explorer</strong> shows a whole vehicle profile. You can plot what the car
  reported. Click an ECU on the CAN bus map. Put signals from different ECUs on one timeline. The
  explorer shows which signals move together. You do not install or connect anything.</p>
  <p><a class="md-button md-button--primary" href="profiles/explorer/">Open the explorer →</a>
  <span class="cx-muted">Try it on the 2017 Ioniq: 30 ECUs, 600+ signals and 100+ recorded sessions.</span></p>
  <div class="cx-hero-cards">
    <a href="profiles/explorer/#hyundai-ioniq-ae-ev/BMS">
      <img src="assets/explorer/explorer-bus.png#only-light" alt="CAN bus map of the Ioniq's ECUs, coloured by how much of each is decoded" loading="lazy">
      <img src="assets/explorer/explorer-bus-dark.png#only-dark" alt="CAN bus map of the Ioniq's ECUs, coloured by how much of each is decoded" loading="lazy">
      <strong>Browse the buses</strong>
      <span>See which ECU is on which CAN segment. Open an ECU to see its PIDs, signals, actuators
        and research leads. Each one links to its YAML.</span>
    </a>
    <a href="profiles/explorer/#hyundai-ioniq-ae-ev/plot/2026-08-08-e84a15?s=BMS:2101:BATTERY_POWER,ESC:22C101:REAL_SPEED_KMH,BMS:2101:SOC_BMS">
      <img src="assets/explorer/explorer-plot.png#only-light" alt="Battery power, speed and state of charge plotted together over a drive, a fast charge and another drive" loading="lazy">
      <img src="assets/explorer/explorer-plot-dark.png#only-dark" alt="Battery power, speed and state of charge plotted together over a drive, a fast charge and another drive" loading="lazy">
      <strong>Plot signals from any ECU together</strong>
      <span>One picture shows a drive, a 60 kW fast charge and another drive. It plots battery
        power, speed and state of charge, with the vehicle states above.</span>
    </a>
    <a href="profiles/explorer/#hyundai-ioniq-ae-ev/plot/2026-08-08-e84a15?s=BMS:2101:BATTERY_POWER">
      <img src="assets/explorer/explorer-overview.png#only-light" alt="Heat-strip overview of every signal that changed during a session, similar signals grouped" loading="lazy">
      <img src="assets/explorer/explorer-overview-dark.png#only-dark" alt="Heat-strip overview of every signal that changed during a session, similar signals grouped" loading="lazy">
      <strong>Find what hides in the data</strong>
      <span>One strip shows every signal that moved in a session. Similar signals are side by side.
        Select a signal to see what follows it. Zoom to a moment to see what reacted.</span>
    </a>
  </div>
</div>

![canair analysis commands and the interactive decode --plot signal explorer in action](screenshots/tour.gif)

These docs follow **what you want to do**. The command list is in the
[CLI reference](reference/cli/index.md).

## Where do I start?

Answer these questions in order. Take the first path that fits.

1. **Do you have a WiCAN dongle and a car with an OBD-II port, and canair is not installed yet?**
   Read [Getting started](getting-started/install.md). You install canair, connect the dongle and
   read live data from the bundled example car in a few minutes.
2. **Does something not work?** Read [Troubleshooting](getting-started/troubleshooting.md).
3. **Is your car one of the bundled cars?** Read [Bundled profiles](profiles/index.md). The
   [Ioniq Electric profile](profiles/hyundai-ioniq-ae-ev.md) is the most complete. Each page lists
   the ECUs, decoded signals and IOControl actuators.
4. **Is your car not bundled?** Read [Bring your own car](bring-your-own-car/overview.md). This is
   the main journey. It takes you from an empty profile to decoded, verified signals.
5. **Do you want to share what you decoded?** Read
   [Define & verify](bring-your-own-car/07-define-and-verify.md), then
   [Share your profile](bring-your-own-car/08-share.md).
6. **Do you want to look around first?** Open the [profile explorer](profiles/explorer.md). It
   plots real Ioniq drives and charges in your browser. You install nothing.
7. **Do you want to learn from a real hunt?** Read the [case studies](case-studies/index.md). They
   show real investigations from start to end, including the wrong turns. One example is
   [finding the hidden AC input voltage](case-studies/ac-input-voltage.md).

## Look something up

- [Cheat sheet](reference/cheat-sheet.md): the commands you use most, on one page.
- [CLI reference](reference/cli/index.md): every command and flag.
- [Configuration](reference/config.md): every config key.
- [Glossary](reference/glossary.md): ECU, PID, DID, ISO-TP and other terms.
- [Concepts](concepts/architecture.md): how canair works.

## What you need

- A **WiCAN dongle**, Pro or classic (non-Pro). Most features work on both. A few are
  [Pro-only](getting-started/connect-device.md).
- A vehicle with an **OBD-II port**.
- [`uv`](https://docs.astral.sh/uv/) to install and run canair.

## A note on safety

Reading data is safe. canair only *reads* unless you tell it to actuate hardware, and every
action that changes state asks for confirmation first. However, **work on the CAN bus of a
vehicle has real risk**. [Safety](concepts/safety.md) explains what canair does and does not do,
and why.

## Contribute back

If you reverse engineer *your* vehicle, **please send it back as a pull request**. A whole
profile, a few decoded signals or a fix all help other owners of the same car. See
[Contributing](contributing/index.md).
