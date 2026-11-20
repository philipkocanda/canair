# Never share security keys

SecurityAccess (0x27) material: what is forbidden and what to do instead.

## Never share security keys — it's illegal

**Sharing the material that unlocks an ECU's SecurityAccess (UDS/KWP2000 `0x27`)
is illegal in many jurisdictions and dangerous — it must never land in a
contribution, not even by accident.** canair is a read/diagnostics tool; it
stays *aware* of `0x27` at the protocol level but never attempts to break it, and
ships no key-cracking / seed→key solver.

**Forbidden in a profile, its captures, notes, or commit history:**

- **Keys / seed→key pairs / unlock responses** — a captured `27 02 <key>` request
  or `67 01 <seed>` response, a seed paired with the key that unlocked it, or any
  recorded successful-unlock exchange. (If a capture accidentally recorded one,
  delete it before contributing.)
- **Unlock algorithms & secrets** — a seed→key transform for any ECU, PINs,
  passwords, certificates, manufacturer secrets.

**If a capability genuinely needs security access** (dealer-level reprogramming,
coding, an ECU-specific unlock), **do not source, derive, or share the key** —
direct the person to their **local authorized dealer or a licensed repairer**,
who are permitted to perform it. If key material already reached the tree, treat
it as a **security incident** (as with PII): flag it immediately — a plain delete
doesn't purge history, and the key may need rotating.
