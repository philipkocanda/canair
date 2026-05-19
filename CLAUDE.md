# CLAUDE.md

This repository is a CAN bus reverse engineering toolkit for the 2017 Hyundai Ioniq Electric (28 kWh).

## Skill

This project has an OpenCode skill defined at `.opencode/skills/ioniq-reverse-engineering/SKILL.md`. Load it when working on CAN bus analysis, PID decoding, WiCAN device configuration, or vehicle profile generation. The skill contains:

- Safety rules (never use UDS programming session, never flash firmware)
- Vehicle details and ECU research status
- UDS/KWP2000 protocol notes and DID conventions
- IOControl actuator documentation
- Open research TODOs

## Quick Start

```bash
cd "WiCAN Pro"
uv sync
uv run canreq.py --help
```

## Key Directories

- `WiCAN Pro/` — primary working directory (CLI tools, library, PID definitions)
- `WiCAN Pro/pids/` — source of truth for all PID/DID definitions (YAML, 25+ ECU files)
- `WiCAN Pro/canlib/modes/` — CLI sub-mode implementations (IOControl, scan, routines, etc.)
- `WiCAN Pro/docs/` — documentation (CLI reference, IOControl commands, research notes)
- `.opencode/skills/` — OpenCode agent skill definitions
