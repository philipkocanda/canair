# PII, location data and security keys

Public-tree rules: never commit personal/location data, never share ECU security keys.

## No PII or location data — the tree is public

Everything committed is shareable and git history is forever, so nothing that could identify or
geolocate the author or any car owner may land in **code, data, docs, tests, fixtures, commit
messages, or history**. (`contributing-profiles` mirrors this for *data*; here it's about not baking
a real IP/email/VIN into code, `--help` strings, or fixtures.) Reason about the *class*, not a list:

- **Identity** — names, emails, phone numbers, usernames, faces, signatures. `canair contribute`
  only scans for emails; a phone number in a note is on you.
- **Vehicle identity** — VINs, plates, registration numbers, and remember these hide *inside
  captured payloads* (the VIN comes back from `F190`). "Raw bytes" is not automatically safe. A
  per-unit ECU serial (`F18C`/`F18B`) is *not* in this class — it names a module, not a person.
- **Location** — addresses, GPS, Wi-Fi SSIDs/BSSIDs, and network coordinates that pin a place (real
  LAN/VPN IPs, MACs, hostnames). Real values live only in the gitignored
  `~/.config/canair/config.yaml`; `config.example.yaml`, docs and tests use obvious placeholders.
- **Free-text that quietly accumulates PII** — capture `--label`/`--notes`, `research:` notes,
  DTC-log labels, profile descriptions. Keep them technical.

**Practices:** examples use synthetic placeholders, always; scrub a real-car capture or fixture (and
its notes/labels) before committing — `canair validate` is not a privacy scanner; don't build
features that leak by default (no resolved device IP baked into a committed artifact); and if
something sensitive already reached the tree, treat it as an **incident** and flag it to the user —
deleting it in a new commit does not erase history.

## Never share security keys — it's illegal

**Sharing the material that defeats an ECU's SecurityAccess (`0x27`) is illegal in many
jurisdictions and dangerous.** It gates the services that reflash or reprogram an ECU, and defeating
it is exactly what manufacturers restrict to authorized dealers and licensed repairers.

**canair deliberately ships no key-cracking / seed→key solver.** Do not add one, nor a
per-marque algorithm bank or a seed:key pair identifier. An explicitly selected, user-supplied
external provider may supply a response for one SecurityAccess exchange: it runs only by user
request, with confirmation and without disclosing or storing the exchange. The design and
safeguards are in `plans/2026-11-21-external-security-access.md`.

**Forbidden everywhere (code, data, captures, docs, tests, commit messages, history):** keys,
seed→key pairs, recorded successful-unlock exchanges; seed→key transforms, PINs, passwords,
certificates, manufacturer secrets; solvers/brute-forcers in any form; and anything that lets a
reader reconstruct the above, including a "worked example" or a realistic fixture built from a real
ECU's seed/key.

**Practices:** tests use invented seeds and responses only, with an inert provider that is not a
solver. Do not source, derive, embed or share an ECU key in the public tree. Key material already
in the tree is a **security incident**: flag it (history purge, key rotation), don't
delete-and-move-on.
