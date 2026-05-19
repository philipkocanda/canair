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
cd wican-pro
uv sync
cp config.example.yaml config.yaml   # Set your WiCAN device IP
uv run canreq.py --help
```

## Key Directories

- `wican-pro/` — primary working directory (CLI tools, library, PID definitions)
- `wican-pro/pids/` — source of truth for all PID/DID definitions (YAML, 25+ ECU files)
- `wican-pro/canlib/modes/` — CLI sub-mode implementations (IOControl, scan, routines, etc.)
- `wican-pro/docs/` — documentation (CLI reference, IOControl commands, research notes)
- `.claude/skills/` — Claude/OpenCode agent skill definitions
