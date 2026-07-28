# Variable-length PID re-capture

**Date:** 2026-07-28
**Status:** in progress — READY-parked session captured
**Context:** follow-up to plans/2026-07-28-isotp-truncation-guard.md

## READY-parked results (2026-07-28, slcan-tcp)

Captured the Bucket-1 PIDs in READY-parked, then polled a focused set 5–6× in
the identical state. **Every PID returned a rock-stable single length — no
intra-state variation, no truncation.** Length is state/content-driven.

Confirmed **genuinely variable-length** (today's clean READY length ≠ historical
majority ⇒ two real lengths exist): AAF 2180/2181 (25B), EPS 220101 (18B),
ESC 22C101 (42B)/22C102 (26B), HVAC 220100/220102/2201A0–A6, MCU 2101 (30B)/
2102 (56B)/21F2 (67B), VCU 2101 (22B)/2102 (23B)/21F2 (86B), OBC 2101 (44B idle),
BMS 2101 (61B)/2102/2103/2104 (38B)/2105 (45B), SKM 22B002 (178B). BMS 2103/2104
38B — previously suspected truncation — is a real READY length (reclassified).

Confirmed **truncation strays** (never re-seen; each = majority − 7B = one dropped
ISO-TP frame): MCU 2103 20B, MCU 2101 27B, MCU 2102 55B, VCU 2101 20B, VCU 2102 20B.
→ **DELETED 2026-07-28 (26 captures).** MCU 2103 is now single-length (27B); the
others (MCU 2101 30/34, MCU 2102 56/62, VCU 2101 22/27, VCU 2102 23/27) retain
two genuine lengths.

**Data-provenance caveat recorded** (no firmware changes occurred, so historical
length variation can't be firmware — it's the transport bug): a banner in
`profile.yaml` + a note in `docs/concepts/captures-and-states.md` warn that
pre-2026-07-28 multi-frame lengths are untrustworthy and variable-length must be
confirmed by post-fix re-capture.

Still need driving / charging / acc2 / sleep sessions to observe the *other*
lengths (e.g. OBC 2101 48B while charging, MCU/VCU driving lengths) before
flagging each PID `variable_length: true`.

## Goal

Re-capture the multi-length PIDs on clean data to separate **genuine
variable-length** responses from **leftover ISO-TP truncation**, then flag the
genuine ones with `pids set-pid-variable-length` and delete confirmed-truncated
strays.

## Why re-capture works now

- Over **`slcan-tcp`** (current default transport) truncation is physically
  impossible — `can-isotp` returns a complete reassembly or nothing. Every
  re-captured length is a guaranteed-real ECU response.
- Over **`wican-ws`** the new capture-time guard (`parse_uds_response`) rejects
  a short multi-frame read, so it's protected too.
- Confirm transport first: `uv run canair status` (expect `slcan-tcp`).

## Buckets

**Bucket 1 — likely genuine variable-length (length tracks state):**
AAF 2180/2181, EPS 220101, ESC 22C101/22C102, HVAC 220100/220102/2201A0–A6,
MCU 2101/2102/21F2, VCU 2101/2102/21F2, OBC 2101, BMS 2101/2102/2105, IGPM 22BC01.
→ confirm, then `set-pid-variable-length ... true`.

**Bucket 2 — likely leftover truncation (sporadic n≤3, stateless, single day):**
MCU 2103 (20B), VCU 2101/2102 (20B), BMS 2103/2104 (38B), BCM 22B00x/22C00x
single-sample shorts, CLU 22B002, SKM 22B002.
→ if the short length never reappears, delete the stale rows.

## Re-capture commands (one --monitor session per state; press `q` to stop)

Add `--reboot` to the last command run to restore AutoPID.

Driving:

    uv run canair query "AAF:2180,2181" "EPS:220101" "ESC:22C101,22C102" "MCU:2101,2102,2103,21F2" "VCU:2101,2102,21F2" --wican vpn --save --label "var-length recheck: driving" --state driving --monitor

Ready, parked:

    uv run canair query "AAF:2180,2181" "BMS:2101,2102,2103,2104,2105" "MCU:2101,2102,2103,21F2" "VCU:2101,2102,21F2" "OBC:2101" "ESC:22C101,22C102" "EPS:220101" "HVAC:220100,220102,2201A0,2201A1,2201A2,2201A3,2201A4,2201A5,2201A6" "CLU:22B002" --wican vpn --save --label "var-length recheck: ready parked" --state "ready, parked" --monitor

Charging:

    uv run canair query "OBC:2101" "BMS:2101,2102,2103,2104,2105" "HVAC:220100,220102,2201A0,2201A1,2201A2,2201A3,2201A4,2201A5,2201A6" "IGPM:22BC01,22BC03,22BC04,22BC05,22BC06,22BC07" --wican vpn --save --label "var-length recheck: charging" --state charging --monitor

ACC2 (ignition on, HV off):

    uv run canair query "HVAC:220100,220102,2201A0,2201A1,2201A2,2201A3,2201A4,2201A5,2201A6" "CLU:22B002" "AAF:2180,2181" "BCM:22C001,22C003,22C004,22C005,22C006,22C007,22C008,22C009,22C00A,22C00B,22C00C,22C00D,22C00E,22C00F,22C011" --wican vpn --save --label "var-length recheck: acc2" --state acc2 --monitor

Sleep / standby (12V only, unplugged; may wake car; SKM often needs a wake):

    uv run canair query "BCM:22B002,22B003,22B004,22B005,22B006,22B007,22B008,22B009,22B00A,22B00C,22B00D,22B00E" "IGPM:22BC01,22BC03,22BC04,22BC05,22BC06,22BC07" "SKM:22B002" --wican vpn --save --label "var-length recheck: sleep standby" --state sleep --monitor --reboot

## Verify + flag (after re-capture)

    uv run canair captures uds MCU 21F2 --since <recapture-date> --latest
    uv run canair captures uds HVAC 2201A2 --since <recapture-date> --diff

- ≥2 clean lengths → genuine → `uv run canair --profile ioniq-2017 pids set-pid-variable-length HVAC 2201A2 true`
- historical short never reappears → truncation → `uv run canair captures uds MCU 2103 --delete --dry-run`

## Open questions

1. Also exercise the `wican-ws` path (to prove the guard live), or `slcan-tcp` only?
2. All 5 state-sessions, or just Bucket 1 (driving + ready + charging)?
3. Automate verify-and-flag (script diffs pre/post lengths, proposes commands) or keep manual?
