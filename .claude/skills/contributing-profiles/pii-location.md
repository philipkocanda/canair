# No PII or location data

The PII/location scrubbing rules: leak classes and practices (the tree is public).

## No PII or location data — the tree is public

Everything contributed is **shareable/public** and git history is forever.
Nothing that could identify or geolocate the author — or any car owner — may land
in the tree, in **data, captures, docs, commit messages, or history**. When in
doubt, leave it out and ask. (The same rule constrains *code* contributions — see
**`contributing-code`**.)

Watch for these leaks (reason about the *class*, not just the list):

- **Vehicle identity:** VINs, license plates, insurance/registration numbers —
  and remember **these hide inside captured payloads.** Identity DIDs (UDS `F190`,
  KWP2000 record `90`) return the VIN in raw bytes; a capture is *not* safe just
  because it "is just hex". The `canair contribute` PII scan flags these, but
  redact them yourself. A per-unit **ECU serial** (`F18C`/`F18B`,
  `identity.serial`) is *not* in this class — it names a module, not a person, and
  the project treats it as shareable diagnostic data.
  **Mark a DID like this `sensitive: true`** (`canair pids set-sensitive`; the
  Ioniq CCM's `22F102` EVCCID is one): canair then masks it on screen, never saves
  it and strips it from every shared copy, so the rule holds without relying on
  you remembering it.
- **Identity:** real names, emails, phone numbers, usernames. Only emails are
  scanned for — a phone number is on you to keep out.
- **Location:** home/work addresses, GPS, odometer-at-a-place, Wi-Fi SSIDs, and
  network coordinates (real LAN/VPN IPs, MACs, hostnames). Device addresses live
  in the **gitignored** `~/.config/canair/config.yaml` — keep real values out of
  anything committed.
- **Free-text that quietly accumulates PII:** capture `--label`/`--notes`,
  `research:` notes, DTC-log labels, profile descriptions. Keep them technical.

**Practices:**

- **Scrub before you submit.** Check every capture (and its notes/labels) drawn
  from a real car; redact identity DIDs. `canair validate` is not a privacy
  scanner and the `contribute` pre-flight is a heuristic net, not a guarantee —
  the reviewer is the backstop.
- **If something sensitive already reached the tree, treat it as an incident.**
  Deleting it in a new commit does *not* erase it from history — flag it to the
  user (history rewrite / secret rotation may be needed), don't just
  delete-and-move-on.
