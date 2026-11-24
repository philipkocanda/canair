# Analysis (read-only — never touches the device)

> **Method lives in the skills.** *How* to choose and read these tools is the
> **reverse-engineer-signal** skill and `docs/concepts/analysis-commands.md`.

- **`captures`** (alias `cap`) — Query captured data. `captures uds` (domain A; a bare
  `canair captures BMS 2102` is shorthand) has the views `--diff`, `--step`, `--latest`,
  `--summary`, `--sessions` and the mutating modes. `captures can` (domain B) lists imported frame
  logs. Rules: `cli/commands/captures/` docstring.
- **`decode`** (alias `dec`; `--try` tests an expression, `--plot` explores), **`align`**
  (time-aligned cross-ECU table), **`correlate`** (`correlate can` for raw frames), **`hunt`**
  (which byte *is* a known signal; `--promote` writes it), **`investigate`** (per-byte verdict;
  `--events`/`--dwell`/`--bits`, `--counters`, `--support-map`), **`search`** (string, VIN or byte
  pattern across captures), **`research`** (open backlog; `--gaps`).
- A support map is per-PID and *declared* (`pids set-support-map`), never inferred at read time.
- **`coverage`** — Decoding gaps: UNMAPPED bytes, incomplete BITS. **Author bits as `Bn:k`, never
  as an `&` mask**: coverage cannot see a mask.

**Shared flags are declared once so their defaults cannot drift** — reuse these helpers when you
add an analysis command: `cli/args/join.py::add_join_args` (`--join-tol`/`--fill`/`--max-hold`),
`cli/args/scope.py::add_scope_args` (`--since`/`--state`/`--label`/…), `--find-mirrors` (one
matcher, `analysis/mirrors.py`) and `--notation` (**display only**: `--json` and `--promote` always
emit the canonical WiCAN expression).
