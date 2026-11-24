---
name: decode-bitfields
description: Decode the UNKNOWN bits of a partially-decoded bitflag byte in a canair profile — finding bytes where only one or two bits are mapped, capturing narrated event data, and attributing the remaining bits with investigate --events/--dwell/--bits, discriminate --bits and --find-mirrors --bits. Load this when auditing bitfield coverage, working a "byte X has 3 of 8 bits decoded" gap, or decoding discrete body/status signals (doors, locks, lights, relays, latches). NOT for continuous analog bytes (use reverse-engineer-signal), pruning/renaming already-decoded signals (use pid-cleanup), or submitting a profile upstream (use contributing-profiles).
---

# Decoding the unknown bits of a bitflag byte

A body/status byte is rarely a number — it is 8 independent flags. Profiles
accumulate them **one bit at a time** (a door bit here, a light bit there), so
the steady state of a mature profile is *partially-decoded bitfield bytes*: 3 of
8 bits named, 5 unknown but almost certainly meaningful. Those 5 are the
cheapest remaining signals in the whole profile — the byte is already being
polled, already captured, and its neighbours are already proven.

This skill is the **bit-level** loop. For the general orient → capture →
analyze → define → verify method see `reverse-engineer-signal`; that skill's
byte-level statistics (Pearson r, unit guessing, physical bands) mostly do
**not** apply to a 0/1 series. Bits are decoded by **event attribution**, not
correlation.

Always `uv run canair …` from the repo root. Edit definitions only via
`canair pids`. Pass `--profile NAME` on every mutation.

## Critical rules

- **Step 3 talks to the car.** Propose the `monitor` command and the action sequence, and run it
  only after the user explicitly approves (`AGENTS.md` → The car).
- Author bits as the `Bn:k` accessor, **never** an `&`-mask on a partially-decoded byte: it claims
  the whole byte and the gap vanishes from `coverage --bitfields`.
- Name a bit only after >=2 independent actuation->edge pairs; otherwise use `--unverified`.
- A bit that never toggles is *unexercised*, not unused: never file it as a constant state.
- Check the byte is a real flag set (not an enum or an analog low bit) before judging bits.
- Never name a bit against a reference without cross-tabbing the reference's full value set.

## Workflow

Seven steps, in order (the table below maps each to its file): find the partly-decoded bytes,
orient with `bix`, capture narrated events, attribute the bits (with an enum/analog triage),
judge the evidence, define the bits, verify.

## Where to read next

| When you are...                                                    | Read                                                  |
|--------------------------------------------------------------------|-------------------------------------------------------|
| Choosing one-signal-per-bit vs `type: bitmask`                      | `.claude/skills/decode-bitfields/models.md`           |
| Step 1: finding partly-decoded bytes, support maps, blind spots    | `.claude/skills/decode-bitfields/find-bytes.md`       |
| Steps 2-3: orienting with `bix` and capturing narrated events      | `.claude/skills/decode-bitfields/orient-capture.md`   |
| Steps 4-4.5: attributing bits, enum/analog triage                  | `.claude/skills/decode-bitfields/attribute-triage.md` |
| Steps 5-6: judging evidence, defining bits via `canair pids`       | `.claude/skills/decode-bitfields/judge-define.md`     |
| Step 7: verifying, debugging symptoms, checking definition of done | `.claude/skills/decode-bitfields/verify-done.md`      |
