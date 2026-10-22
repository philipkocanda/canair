---
hide:
  - toc
---

# Profile explorer

Pick a bundled profile, see roughly how its ECUs sit on the CAN buses, then click an ECU to
browse its PIDs, signals, IO controls and research leads. Every record links to the line of
YAML it came from. The data is generated from [`profiles/`](index.md) whenever this site is
built, so it always matches the profiles.

This is a read-only view of the *definitions*: no captured values, and nothing talks to a
device. To try a PID or an actuator yourself, copy the `canair` command shown beside it.

<div id="explorer" class="cx-explorer">
  <noscript>The explorer needs JavaScript. The same data is in
  <a href="https://github.com/philipkocanda/canair/tree/main/profiles">profiles/</a> on GitHub.</noscript>
</div>
