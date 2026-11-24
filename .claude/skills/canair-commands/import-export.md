# Import / export / sharing

- **`import uds ECU:PID=PAYLOAD …`** — Device-free capture import (domain A). `--label` is
  required. `--from-log FILE` is the bulk form: always `--dry-run` first. **`import can`** stores a
  raw frame log verbatim in `captures/can/`. **`import dbc`** imports a DBC into `signals/`; add a
  dialect repair in `interop/dbc/repair.py`, never a pre-edited copy. **`export dbc`** writes
  `signals/` out.
- **`contribute`** (alias `share`) — Opens the upstream PR for the **active** profile via `gh`,
  after `validate all`, a PII pre-flight and several guards. Read **contributing-profiles** first;
  design: `docs/development/contribute-pipeline.md`.
- **`update`** — Checks out the latest **released** tag of a git-clone install and reinstalls;
  refuses a dirty clone or an unknown latest tag.
