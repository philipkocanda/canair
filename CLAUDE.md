# CLAUDE.md

This repository is a CAN bus reverse engineering toolkit for the 2017 Hyundai Ioniq Electric (28 kWh).

## Skill

This project has a Claude skill defined at `.claude/skills/ioniq-reverse-engineering/SKILL.md`. Load it when working on CAN bus analysis, PID decoding, WiCAN device configuration, or vehicle profile generation. The skill contains:

- Safety rules (never use UDS programming session, never flash firmware)
- Vehicle details and ECU research status
- UDS/KWP2000 protocol notes and DID conventions
- IOControl actuator documentation
- Open research TODOs

## Running the CLI — ALWAYS `uv run canair` from the repo root

**Agents working in this repo MUST invoke the CLI as `uv run canair …` from the project root**, never a bare `canair`. A globally installed `canair` (`uv tool install .`) can run **stale code** and resolve the **wrong vehicle profile**; `uv run canair …` guarantees the current working-tree code and the repo-bundled `profiles/ioniq-2017/`.

```bash
uv sync                                                # Set up the dev environment
cp config.example.yaml ~/.config/canair/config.yaml    # Set your WiCAN device IP
uv run canair --help
```

Prefer canair's built-in subcommands (`query`/`scan`/`discover`/`captures`/`decode`/`coverage`/`research`/`pids`) for all querying, analysis, and reverse-engineering rather than hand-rolled scripts — and always pass `--save` (with `--label`/`--state`/`--notes`) when reading the device. `uv tool install .` is for end users, not agents.

Vehicle data lives in a *profile* bundle; the repo ships `profiles/ioniq-2017/` as the default/example. Inspect with `uv run canair profile list` / `show` / `path`.

## Key Directories

- `profiles/ioniq-2017/pids/` — source of truth for all PID/DID definitions (YAML, 25+ ECU files) in the bundled example profile
- `profiles/ioniq-2017/{captures,out}/`, `ecus.yaml` — captures, generated WiCAN profiles, and the ECU registry for that profile
- `canlib/cli.py` + `canlib/commands/` — the `canair` CLI entrypoint and per-subcommand modules
- `canlib/modes/` — live CLI sub-mode implementations (IOControl, scan, routines, etc.)
- `canlib/schema/` — tool-owned schemas (`pids_schema.yaml`, `captures_schema.json`)
- `docs/` — local documentation (CLI reference, IOControl commands, research notes; gitignored)
- `.claude/skills/` — Claude/OpenCode agent skill definitions
