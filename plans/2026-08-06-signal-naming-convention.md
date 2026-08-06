# Settle the vocabulary: **signal**, not parameter/param

Status: **PROPOSED** (2026-08-06) — decision taken, execution pending.
Started by the monitor/TUI pass that landed the payload-ordered signal tables,
the single-notation byte ruler, and the width-aware status bar; that change
normalised the *strings it touched* and recorded the convention in `AGENTS.md`.
This plan finishes the job everywhere else.

## The decision

**A decoded quantity read off the bus is a SIGNAL.** One word, both data domains:

| Domain | What it is | Called |
|---|---|---|
| A (diagnostics) | a field decoded from a UDS/KWP2000 PID/DID response | **signal** |
| B (broadcast) | a field decoded from a raw CAN frame | **signal** |

"Parameter" is retired from user-facing language. It survives only where renaming
it is a *migration*, not an edit — see [Deliberately not renamed](#deliberately-not-renamed).

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
   large (DBC, cantools, SavvyCAN, Vector) calls a decoded field.

### Why not rename the YAML key too (`parameters:` → `signals:`)?

Considered and deferred, not rejected. It requires a schema version bump, a
reader that accepts both keys, a `canair` migration command, and coordination with
every contributed profile in the wild — a change with its own risk profile that
should not ride along inside a vocabulary sweep. If it happens, it is its own
plan; this one must not half-start it.

## Where things stand

Rough counts of `param`-family tokens (2026-08-06, after the monitor pass):

| Tree | Occurrences |
|---|---|
| `canlib/` | ~1200 |
| `tests/` | ~500 |
| `docs/` | ~220 |
| `.claude/skills/` | ~110 |

Most are Python identifiers (`param`, `params`, `param_name`, `ParamRow`), which
users never see. The user-visible remainder is the priority.

## Plan

Four stages, each independently landable and reviewable. **Stage 1 is the one
that matters to users; stages 2-3 are hygiene; stage 4 is optional.**

### Stage 1 — user-visible strings (highest value, low risk)

Every string a user can read. Sweep by surface, not by grep hit, so the reviewer
can check a whole command at a time:

- **`--help` text**: `decode` (`--param`, `--unverified` help, the
  `N parameters (…verified)` header), `coverage`, `investigate`, `correlate`,
  `hunt`, `research`, `ecu` (`VERIF  verified/total parameters`), `wican autopid`
  (`Include unverified parameters`), `pids upsert-param`/`rm-param`/`rename-param`
  help bodies (the *names* stay — see below), `read --param`.
- **stdout/stderr**: `No parameters match the filter criteria`,
  `No parameters defined and no captures found …`, `N PID groups, M parameters`,
  the `decode` table header, `coverage`'s `Np` counts.
- **TUI labels & hints** not already done: `decode --plot` (`bytes/param mode`,
  `←/→ param`, `m param`), the plot annotate/rename prompts.
- **Docs**: `docs/concepts/*`, `docs/bring-your-own-car/*`, `docs/reference/*`
  (hand-written pages only — `docs/reference/cli/` is generated from `--help`, so
  it follows for free once the parsers are updated; re-run
  `scripts/gen_cli_reference.py`).
- **`README.md`**: the command map lines that say "parameters".
- **Skills**: `.claude/skills/*/SKILL.md`.
- **`CHANGELOG.md`**: `[Unreleased]` entry only; do not rewrite released history.

Gates: `uv run pytest -q` (several tests assert on these strings), then regenerate
the goldens that pin command output — `decode`'s header is pinned by
`tests/fixtures/golden/decode-*.txt` and `coverage`'s by `coverage-*.txt`:

```bash
CANAIR_REGEN_GOLDEN=1 uv run pytest -q tests/test_analysis_golden.py tests/test_captures_golden.py
git diff tests/fixtures/golden/   # READ it — the only change should be the word
```

Also re-render any screenshot whose output text changes
(`make screenshots-only ONLY="…"`), and re-run
`uv run python scripts/gen_cli_reference.py`.

### Stage 2 — the display/decoding helper API

The functions whose *names* a contributor reads while working on a view. Pure
rename, one commit, no behaviour change:

| Now | Becomes |
|---|---|
| `decoding.ParamRow` | `SignalRow` |
| `decoding.decode_param_rows` | `decode_signal_rows` |
| `formatting.render_param_table` | `render_signal_table` |
| `formatting.render_param_ranges` | `render_signal_ranges` |
| `formatting.param_byte_indices` | `signal_byte_indices` |
| `formatting.param_byte_index_str` | `signal_byte_label` |
| `formatting.changed_param_highlights` | `changed_signal_highlights` |
| `formatting.print_decoded_params` | `print_decoded_signals` |
| `_monitor_stats.ParamStats` | `SignalStats` (already has `SignalStat`) |

`canlib/__init__.py` re-exports several of these — update `__all__` in the same
commit. No deprecation aliases: this is an internal API with no external
consumers, and shims are how a rename ends up permanent.

### Stage 3 — local identifiers and docstrings

`param`/`params`/`param_name` locals, loop variables, dataclass fields, and
docstrings across `canlib/` and `tests/`. Mechanical, but **not** a blind
`sed`: `parameters` is also the YAML key (must not change), and
`iocontrol`/`routines` use "parameter" in its *correct* UDS sense (a control
parameter, a routine argument) — those stay. Split per package
(`modes/`, `commands/`, top-level) so each diff stays readable.

### Stage 4 — the data model (optional, separate decision)

`parameters:` → `signals:` in `ecus/`, and `pids upsert-param` →
`upsert-signal`. Needs: schema accepting both keys, `canair` migration
subcommand, `captures`-style round-trip verification, a release note, and a
deprecation window for the CLI verbs. **Do not start this as part of stages 1-3.**

## Deliberately not renamed

- **`parameters:`** in `profiles/*/ecus/*.yaml` and
  `canlib/schema/pids_schema.yaml` (stage 4 or never).
- **`canair pids upsert-param` / `rm-param` / `rename-param`** — the documented
  CLI surface; their help text says "signal" already.
- **`canair signals`** — stays the domain-B (broadcast) editor. The *word* signal
  is domain-neutral; this *command* is not, and renaming/merging it is a separate
  question from vocabulary.
- **"parameter" in its UDS sense** — a routine argument, an IOControl control
  parameter, a sub-function parameter. That is the correct word there.
- **Released `CHANGELOG.md` sections** and existing plan docs (historical record).

## Acceptance

- No user-visible string in `--help`, stdout, a TUI, `docs/`, `README.md`, or the
  skills calls a decoded quantity a parameter (excluding the deliberate list).
- `rg -w 'param|params|parameter' canlib/ --glob '*.py'` returns only: the
  `parameters` YAML key, the `pids *-param` CLI surface, and UDS-sense uses.
- `uv run pytest -q`, `ruff`, `ty`, `canair validate all`,
  `scripts/gen_cli_reference.py --check`, and
  `scripts/gen_screenshots.py --check` are all green.
- `AGENTS.md`'s "Vocabulary" section loses its "identifiers still say param"
  caveat (stages 2-3 done) and its pointer to this plan.
