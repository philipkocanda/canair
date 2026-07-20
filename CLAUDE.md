# CLAUDE.md

This repository is a CAN bus reverse engineering toolkit for the 2017 Hyundai Ioniq Electric (28 kWh).

## Skill

This project has a Claude skill defined at `.claude/skills/ioniq-reverse-engineering/SKILL.md`. Load it when working on CAN bus analysis, PID decoding, WiCAN device configuration, or vehicle profile generation. The skill contains:

- Safety rules (never use UDS programming session, never flash firmware)
- Vehicle details and ECU research status
- UDS/KWP2000 protocol notes and DID conventions
- IOControl actuator documentation
- Open research TODOs

## Quick Start

```bash
uv tool install .                              # Install the canair CLI (or: uv sync for dev)
cp config.example.yaml ~/.config/canair/config.yaml   # Set your WiCAN device IP
canair --help
```

`uv run canair ...` also works in the repo. Vehicle data lives in a *profile* bundle; the repo ships `profiles/ioniq-2017/` as the default/example. Inspect with `canair profile list` / `show` / `path`.

## Key Directories

- `profiles/ioniq-2017/pids/` — source of truth for all PID/DID definitions (YAML, 25+ ECU files) in the bundled example profile
- `profiles/ioniq-2017/{captures,out}/`, `ecus.yaml` — captures, generated WiCAN profiles, and the ECU registry for that profile
- `canlib/cli.py` + `canlib/commands/` — the `canair` CLI entrypoint and per-subcommand modules
- `canlib/modes/` — live CLI sub-mode implementations (IOControl, scan, routines, etc.)
- `canlib/schema/` — tool-owned schemas (`pids_schema.yaml`, `captures_schema.json`)
- `docs/` — local documentation (CLI reference, IOControl commands, research notes; gitignored)
- `.claude/skills/` — Claude/OpenCode agent skill definitions
