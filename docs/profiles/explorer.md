---
hide:
  - toc
---

# Profile explorer

Pick a bundled profile, see roughly how its ECUs sit on the CAN buses, then click an ECU to
browse its PIDs, signals, IO controls and research leads. Every record links to the line of
YAML it came from. The data is generated from [`profiles/`](index.md) whenever this site is
built, so it always matches the profiles.

Switch to **Data** to plot what the car actually reported. Add signals from any ECU to the plot
tray with ＋, pick a recorded session (a drive, a charge, a parked check), and they share one
timeline. The overview strip shows every signal that moved, grouped by how alike they are;
**Related signals** lists what follows the one you select, and **What changed here?** lists
what reacted inside the window you zoom to. The values are the decoded readings from the
profile's own recorded sessions; payloads, identity responses and scans are never published.

Short on room? On a wide screen, **⇤ Hide menu** (top right of the explorer) drops the site
navigation so plots use the whole window width; the choice is remembered. On a phone the tables
become one card per signal, and in a plot you zoom by dragging sideways across the overview strip.

Nothing here talks to a device. To try a PID or an actuator yourself, copy the `canair` command
shown beside it, and the terminal commands at the bottom of a plot reproduce it with the CLI.

<div id="explorer" class="cx-explorer">
  <noscript>The explorer needs JavaScript. The same data is in
  <a href="https://github.com/philipkocanda/canair/tree/main/profiles">profiles/</a> on GitHub.</noscript>
</div>
