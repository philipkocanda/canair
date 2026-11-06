# Settle the vocabulary: **signal**, not parameter/param

Status: **PROPOSED** (2026-08-06, revised 2026-11-05) — decision taken, execution pending.
Started by the monitor/TUI pass that landed the payload-ordered signal tables,
the single-notation byte ruler, and the width-aware status bar; that change
normalised the *strings it touched* and recorded the convention in `AGENTS.md`.
This plan finishes the job everywhere else.

## The decision

**A decoded quantity read off the bus is a SIGNAL.** One word, both data domains.
The container keeps its own name: a PID/DID response or a CAN frame holds one or
more signals.

| Domain | Container | Field inside it |
|---|---|---|
| A (diagnostics) | a UDS/KWP2000/OBD-II PID/DID response | **signal** |
| B (broadcast) | a raw CAN frame (one arbitration ID) | **signal** |

"Parameter" is retired from user-facing language. It survives only where renaming
it is a *migration*, not an edit, or where another format or the UDS standard
requires it — see [Deliberately not renamed](#deliberately-not-renamed).

### How this matches the wider field

Usage differs between communities, so the choice is deliberate, not universal:

| Community | Container | Field |
|---|---|---|
| DBC tools (Vector, cantools, SavvyCAN, cabana) | message | signal |
| AUTOSAR COM | PDU | signal |
| OBDb signal sets | command | signal |
| SAE J1939 | Parameter Group (PGN) | Suspect Parameter Number (SPN) |
| ODX (Open Diagnostic data eXchange, ISO 22901) | DID / service | `PARAM` |
| AUTOSAR DCM | DID | data element |
| WiCAN AutoPID | PID | parameter |
| SAE J1979 (OBD-II) | PID (Parameter ID) | usually one value per PID |

canair follows DBC and OBDb, the tools closest to its users (owners and reverse
engineers). Readers from J1939 or ODX get a terminology page (stage 1) instead of
a second word.

### Why not keep the domain split ("parameter" for UDS, "signal" for frames)?

It was the other candidate, and it is defensible — `parameters:` is the schema key
and `signals/` is the frame sidecar, so the split matches the files. It was
rejected because:

1. **Users don't think in domains.** Someone reading `SOC_BMS 92.5 %` off a
   monitor and someone reading `WHL_SPD11 42 km/h` off a sniffed frame are doing
   the same thing. Two words for one concept is a tax on every doc sentence
   ("parameters and signals", "params/signals", "the param (or signal) …").
2. **The tool already leaked.** The monitor's view modes are `ecus → ranges →
   signals → full`; `_monitor_stats.py` has `SignalStat`; `investigate`/`decode`
   say "signal" in half their output and "parameter" in the other half. The split
   was never actually maintained.
3. **"Parameter" is the weaker word.** In UDS a *parameter* is anything you pass
   (a sub-function, a routine argument, an IOControl control parameter — canair
   uses the word that way too). "Signal" is unambiguous and is what CAN tooling at
   large (DBC, cantools, SavvyCAN, Vector) and OBDb call a decoded field.

## Where things stand

Rough counts of `param`-family tokens (2026-11-05):

| Tree | Occurrences |
|---|---|
| `src/canair/` | ~1500 |
| `tests/` | ~800 |
| `docs/` | ~280 |
| `.claude/skills/` | ~125 |

Most are Python identifiers (`param`, `params`, `param_name`, `ParamRow`), which
users never see. The user-visible remainder is the priority. Stage 1 is not done:
the skills still use "param"/"parameter" for decoded fields throughout (e.g.
`reverse-engineer-signal/SKILL.md`, `decode-bitfields/SKILL.md`), and `docs/` has
no terminology page.

## Plan

Four stages, each independently landable and reviewable. **Stage 1 is the one
that matters to users; stages 2-3 are hygiene; stage 4 is the data model and gets
its own plan.**

### Stage 1 — user-visible strings (highest value, low risk)

Every string a user can read. Sweep by surface, not by grep hit, so the reviewer
can check a whole command at a time. Load the **cli-tui-standards** skill first.

- **`--help` text**: `decode` (`--param`, `--verified`/`--unverified` help, the
  `N parameters (…verified)` header), `coverage`, `investigate`, `correlate`,
  `hunt`, `research`, `ecu` (`VERIF  verified/total parameters`), `wican autopid`
  (`Include unverified parameters`), `pids upsert-param`/`rm-param`/`rename-param`
  help bodies (the *names* stay — see below), `read --param`.
- **Selector placeholders**: the metavar `ECU:PID:PARAM` (and `ECU:PID:PARAM|EXPR`)
  in `align`, `correlate`, `hunt`, `investigate` and `decode`, their help bodies
  and tips, and `ECU.PARAM` in `states add --when`. Becomes `ECU:PID:SIGNAL` /
  `ECU.SIGNAL`. A metavar is display text only, so nothing breaks.
- **Flags**: `read --param`, `decode --param` and `pids set-sensitive --param`
  gain `--signal` as their primary name, with `--param` kept as an alias on the
  same argument (argparse accepts several option strings), so scripts keep
  working. Both names show in `--help`, `--signal` first.
- **stdout/stderr**: `No parameters match the filter criteria`,
  `No parameters defined and no captures found …`, `N PID groups, M parameters`,
  the `decode` table header, `coverage`'s `Np` counts.
- **TUI labels & hints** not already done: `decode --plot` (`bytes/param mode`,
  `←/→ param`, `m param`), the plot annotate/rename prompts.
- **Terminology page**: a new `docs/concepts/terminology.md`, linked from the
  Concepts nav in `mkdocs.yml`. It defines canair's words and maps them to the
  other communities' words (the table under
  [How this matches the wider field](#how-this-matches-the-wider-field)). It also
  defines **PID** as canair uses it: any diagnostic read request — an OBD-II PID,
  a UDS DID (`22xxxx`) or a KWP2000 local identifier (`21xx`) — which OBDb calls a
  *command*.
- **Docs**: `docs/concepts/*`, `docs/bring-your-own-car/*`, `docs/getting-started/*`,
  `docs/reference/*` (hand-written pages only — `docs/reference/cli/` is generated
  from `--help`, so it follows for free once the parsers are updated), and the
  explorer's user-visible strings in `docs/assets/explorer/*.js`.
- **`README.md`**: the command map lines that say "parameters".
- **Skills**: `.claude/skills/*/SKILL.md` and their reference files.
- **`CHANGELOG.md`**: `[Unreleased]` entry only; do not rewrite released history.

Gates: `uv run pytest -q` (several tests assert on these strings), then regenerate
the goldens that pin command output — `decode`'s header is pinned by
`tests/fixtures/golden/decode-*.txt` and `coverage`'s by `coverage-*.txt`:

```bash
CANAIR_REGEN_GOLDEN=1 uv run pytest -q tests/integration/test_analysis_golden.py \
  tests/integration/test_captures_golden.py
git diff tests/fixtures/golden/   # READ it — the only change should be the word
```

Also re-render any screenshot whose output text changes
(`make screenshots-only ONLY="…"`), and re-run
`uv run python scripts/gen/gen_cli_reference.py`.

### Stage 2 — the display/decoding helper API

The functions whose *names* a contributor reads while working on a view. Pure
rename, one commit, no behaviour change:

| Now | Becomes |
|---|---|
| `expression.decoding.ParamRow` | `SignalRow` |
| `expression.decoding.decode_param_rows` | `decode_signal_rows` |
| `expression.decoding.decode_payload` | `decode_signals` |
| `render.formatting.render_param_table` | `render_signal_table` |
| `render.formatting.render_param_ranges` | `render_signal_ranges` |
| `render.formatting.param_byte_indices` | `signal_byte_indices` |
| `render.formatting.param_byte_index_str` | `signal_byte_label` |
| `render.formatting.changed_param_highlights` | `changed_signal_highlights` |
| `render.formatting.print_decoded_params` | `print_decoded_signals` |
| `live._monitor_stats.ParamStats` | `SignalStats` (already has `SignalStat`) |

`decode_payload` arrived in `decoding` from the `decode` command in `a50c530` and
deliberately kept its old name: renaming it there and then would have left the
module half-renamed beside `decode_param_rows`, which is exactly what this plan
exists to prevent. `decode_signals` is the right name — it returns one entry per
signal, and "payload" describes its *input*, not its output — so it renames here
with its neighbours.

No deprecation aliases: this is an internal API with no external consumers, and
shims are how a rename ends up permanent.

### Stage 3 — local identifiers and docstrings

`param`/`params`/`param_name` locals, loop variables, dataclass fields, and
docstrings across `src/canair/` and `tests/`. Mechanical, but **not** a blind
`sed`: `parameters` is also the YAML key (must not change before stage 4), the
AutoPID exporter writes the WiCAN `"parameters"` key, and `iocontrol`/`routines`
use "parameter" in its *correct* UDS sense (a control parameter, a routine
argument) — those stay. Split per package so each diff stays readable. Do stages
2 and 3 back to back, so the tree does not sit half-renamed.

### Stage 4 — the data model (decide now, execute in its own plan)

`parameters:` → `signals:` in `ecus/`, and `pids upsert-param`/`rm-param`/
`rename-param` → `upsert-signal`/`rm-signal`/`rename-signal`.

**Recommendation: do it, after stage 1.** While the key stays `parameters:`,
every contributor reads "parameters" in the source of truth and "signal"
everywhere else. The reasons to do it soon:

- The cost grows with every profile; six are bundled today, plus user profiles
  and forks.
- The repo already runs this kind of migration: `pids migrate-categories`
  (`ha_class` → `category`) and `captures migrate-rx` (`ecu` → `rx`).
- A loader that accepts both keys during a deprecation window keeps unmigrated
  profiles loading.

It needs: the schema and loader accepting both keys (rejecting a PID that has
both), a `pids migrate-signals` command, `validate pids` warning on the old key,
every editor writing the new key, the old CLI verbs kept as aliases for a
deprecation window, a release note, and the bundled profiles migrated in the same
release. The AutoPID exporter keeps writing `"parameters"`, because that is the
WiCAN format's key.

This is a judgment call. "Never" is also a valid outcome if format churn is the
greater cost — but record the decision here, so the question stops being open.
**Do not start this as part of stages 1-3.**

## Deliberately not renamed

- **`parameters:`** in `profiles/*/ecus/*.yaml` and
  `src/canair/schema/pids_schema.yaml` until stage 4 lands.
- **`canair pids upsert-param` / `rm-param` / `rename-param`** until stage 4;
  their help text says "signal" already.
- **`--param`** on `read`, `decode` and `pids set-sensitive` stays as an alias of
  `--signal` (stage 1).
- **`"parameters"` in the generated WiCAN AutoPID JSON** — the firmware's format,
  a translation boundary rather than canair's vocabulary.
- **`canair signals`** — stays the domain-B (broadcast) editor. The *word* signal
  is domain-neutral; this *command* is not, and renaming/merging it is a separate
  question from vocabulary.
- **"parameter" in its UDS sense** — a routine argument, an IOControl control
  parameter, a sub-function parameter. That is the correct word there.
- **Released `CHANGELOG.md` sections** and other plan docs (historical record).

## Acceptance

- No user-visible string in `--help` (metavars included), stdout, a TUI, `docs/`,
  `README.md`, or the skills calls a decoded quantity a parameter (excluding the
  deliberate list).
- `docs/concepts/terminology.md` exists, is in the nav, and maps canair's terms
  to the other communities' terms.
- `rg -w 'param|params|parameter' src/canair/ --glob '*.py'` returns only: the
  `parameters` YAML key, the `pids *-param` CLI surface, the `--param` alias, the
  AutoPID `"parameters"` key, and UDS-sense uses.
- `uv run pytest -q`, `ruff`, `ty`, `make structure`, `canair validate all`,
  `scripts/gen/gen_cli_reference.py --check`, and
  `scripts/gen/gen_screenshots.py --check` are all green.
- `AGENTS.md`'s "Vocabulary" section loses its "identifiers still say param"
  caveat (stages 2-3 done) and its pointer to this plan.
- Stage 4 has a recorded decision (do / never) in this plan.
