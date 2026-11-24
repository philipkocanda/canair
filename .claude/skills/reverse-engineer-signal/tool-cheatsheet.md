# Tool cheat-sheet for this workflow

Flags live in each command's `--help`. `docs/concepts/analysis-commands.md` is the "which command
when" map, and the `canair-commands` skill holds each command's rules. This table lists only
what those do not.

| Question | Tool |
|------|------|
| onboard a reading (no device) | `canair import uds ECU:PID=PAYLOAD --label … --state …` |
| map bytes | `canair bix --annotate HEX` (+ `--ecu`/`--pid`: which signal maps each byte; `--torque`/`--obdb` add the Torque letter and OBDb bix columns) |
| reason about a signal | [`signal-reasoning.md`](signal-reasoning.md) — ECU role, physics/EE, CS (enums/counters), statistics |
| a value behind no DID (RAM, calibration) | `canair scan memory` → `memory dump` ×2 → `memory diff` → define `ECU:23/ADDRESS+SIZE` ([memory reads](../../../docs/concepts/memory-reads.md)); never shipped, gate it to a `software:` revision |
| exercise a make-specific command, or **inject a crafted broadcast frame** | `canair action` (`actions.yaml`; `frames:` injection is `slcan-tcp` only, gated by `safety.allow_frame_injection`, bits held by a per-frame `mask`; [safety](../../../docs/concepts/safety.md)) |
| correct a stale PID note | `canair pids set-pid-notes <ECU> <PID> "…"` (omit text to clear) |
| work the backlog | `canair research` (`--gaps` for unread actuator families), `canair pids add-research`/`set-status` |
