---
name: canair-commands
description: Per-command purpose and rules for the canair CLI that `--help` does not carry. Load this before you run, script or change a canair subcommand — live device commands (read, monitor, scan, io, reset, action, memory, dtc), analysis (captures, decode, hunt, investigate), authoring (pids, signals, ecu, states, wican, bix) and import/export/contribute.
---

# canair commands

All functionality is one CLI, `canair`, run as `uv run canair` from the repo root. **Flags and
subcommand lists are in `canair <cmd> --help` and `docs/reference/cli/<cmd>.md`.** The files below
give each command's purpose and the rules `--help` does not carry. Read only the file you need.

| You are about to… | Read |
|---|---|
| Talk to the car or the dongle (needs the user's approval first: `AGENTS.md` → The car) | `live-device.md` |
| Query, decode or correlate recorded captures (offline) | `analysis.md` |
| Edit a profile's definitions: `pids`, `signals`, `ecu`, `states`, `groups`, `variants`, `bus`, `validate`, `wican`, `bix`, `config` | `authoring.md` |
| Import a capture, DBC or log, export a DBC, `contribute`, `update` | `import-export.md` |

Rules that hold for every file:

- **Method is not here.** *How* to choose and read the analysis tools is the
  **reverse-engineer-signal** skill and `docs/concepts/analysis-commands.md`.
- **Pass `--profile NAME` on every mutative command** (`AGENTS.md` → Profiles).
- **Never hand-edit `captures/`, `signals/` or `vehicle_states.yaml`** (`AGENTS.md` → Prefer
  canair's built-in tooling).
