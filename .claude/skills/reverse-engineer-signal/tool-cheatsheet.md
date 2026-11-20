# Tool cheat-sheet for this workflow

Scope: which canair command answers which question during signal reverse-engineering.

## Tool cheat-sheet (this workflow)

Flags live in each command's `--help`; `docs/concepts/analysis-commands.md` is the
"which command when" map.

| Question | Tool |
|------|------|
| what to work on | `canair research` (`--gaps` for unread actuator families), `canair coverage` |
| what's captured | `canair captures uds --sessions` (TOC; `--json`) |
| talk to the car (explicit approval first) | `canair read`/`monitor`/`scan`/`discover` (`--save`) |
| onboard a reading (no device) | `canair import uds ECU:PID=PAYLOAD --label … --state …` |
| see captures | `canair captures uds` (`--diff`/`--step`/`--rulers`/`--latest`/`--summary`) |
| map bytes | `canair bix --annotate` (+ `--ecu`/`--pid`: which param maps each byte) |
| reason about a signal | [`signal-reasoning.md`](signal-reasoning.md) — ECU role, physics/EE, CS (enums/counters), statistics |
| explain an unknown PID | `canair investigate <ECU> <PID>` (mapped? / state F / best anchor + unit / triage / band); omit positionals to sweep an ECU or the profile |
| test expressions | `canair decode --try` / `--stats` / `--corr` / `--plot` |
| decode a body event capture | `canair investigate … --events --bits` + `canair correlate --find-mirrors --bits` |
| which bytes/bits are signals at all · how many parameters a record holds | `canair investigate [<ECU> [<PID>]] --support-map` (per-bit/per-byte/per-field map, open and unsupported positions, `--json`); declare with `canair pids set-support-map` |
| find an odometer / hour meter / cycle count | `canair investigate [<ECU> [<PID>]] --counters` (monotonic windows over the WHOLE corpus, scored in bits) — the one question correlation can't answer |
| what's co-polled here | `canair correlate --overlap` |
| cross-ECU correlate | `canair decode … --corr ECU:PID:PARAM`; `canair correlate [--against REF] [--bytes/--bits]` |
| which byte is signal Y | `canair hunt <ECU> <PID> --against ECU:PID:PARAM` (fit + unit guess; `--promote`) |
| eyeball signals side by side | `canair align ECU:PID:PARAM …` (time-aligned table; `--csv`/`--json`) |
| reference an external log | `canair hunt/correlate … --against-file series.csv` |
| a signal with no bus anchor | `canair hunt … --physical`; `canair investigate … --independent-of` |
| remove a confounder | `canair hunt/correlate … --control ECU:PID:PARAM` (partial correlation) |
| raw bytes for external analysis | `canair decode <ECU> <PID> --dump-bytes [--json]` |
| find state-dependent signals | `canair decode … --discriminate state [--bytes] [--bits]` |
| find redundant mirrors | `canair decode --find-mirrors` / `canair correlate --find-mirrors` (+ `--allow-offset`) |
| scope a drive | `--state DRIVING` / `--since`/`--until`/`--date` / `--last-session` |
| per-segment stats · evolution | `canair decode … --stats --group-by state` · `--compact --changes-only` |
| write definitions | `canair pids upsert-param`/`add-pid`/`rename-param`/`rm-param`/`add-research`/`set-status` |
| correct a stale PID note | `canair pids set-pid-notes <ECU> <PID> "…"` (omit text to clear) |
| validate · ship | `canair validate pids`, `canair coverage` · `canair wican autopid write` |
| a value behind no DID (RAM, calibration) | `canair scan memory` → `memory dump` ×2 → `memory diff` → define `ECU:23/ADDRESS+SIZE` ([memory reads](../../../docs/concepts/memory-reads.md)); never shipped, gate it to a `software:` revision |
| exercise a make-specific command, or **inject a crafted broadcast frame** | `canair action` (`actions.yaml`; `frames:` injection is `slcan-tcp` only, gated by `safety.allow_frame_injection`, bits held by a per-frame `mask`; [safety](../../../docs/concepts/safety.md)) |

---

