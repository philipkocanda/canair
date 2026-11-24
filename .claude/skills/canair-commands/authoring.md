# Authoring (writes into a profile — pass `--profile`)

**Every definition edit goes through `yaml_edit/`** (its package docstring is the map). An editor
declares a `Target`, registers it in `yaml_edit/targets.py`, and writes through `edit_doc` or
`edit_ecu_text`, which write atomically, re-check, and **revert on any failure**. A rejected edit
raises `EditError`; no command catches it. A new editor never opens the file itself, and removes or
renames an entry with `yaml_edit.ruamel_ops` (a bare `del` loses the comments).

- **`pids`** — Add/update `ecus/` signals and research entries; prefer it over hand-editing.
  **WiCAN `Bnn` includes the ISO-TP PCI bytes**: confirm an offset with
  `canair bix -a HEX --ecu … --pid …`. A successful `upsert-param` echoes the decoded range across
  existing captures; a `constant` where you expected variation usually means the offset landed on
  a PCI byte. **State a shared value once, never per signal**: whatever holds for the whole page
  (`unit`/`min`/`max`/`verified`/`source`) goes on the PID (`pids set-pid-defaults`,
  `pids hoist-defaults`); a signal's `notes` carry only what is specific to it. Pass plain strings
  for `notes`; they are formatted for you.
- **`signals`** — The domain-B analogue: the broadcast sidecar `signals/<bus>.yaml`. **Never
  hand-edit `signals/`.**
- **`ecu`** — Inspect the registry; `ecu add TX …` registers an ECU offline; `ecu <ECU> rename NEW`
  renames it everywhere; `ecu revisions`/`set-variants` edit revisions and presence. `ecu <ECU>
  edit` opens `$EDITOR` and is **TTY-only — agents use `pids …`/`ecu add`**.
- **`states`** — The state vocabulary. **Never hand-edit `vehicle_states.yaml`**: the editors also
  retarget `implies:` references, and `states rename` rewrites every reference
  (`yaml_edit/state_rename.py`).
- **`groups`** (`@group` sets; overlapping selectors are coalesced to one step per ECU in
  `query/steps.py::normalize_query_steps`, load-bearing for `monitor`), **`variants`**
  (`variants.yaml`; `docs/development/variants-and-revisions.md`), **`bus`** (`can_buses.yaml`).
- **`validate`** (alias `val`) — Schema and consistency checks. **`validate states` is the only
  place a renamed or mistyped `when:` signal is caught.** Per-target detail:
  `validation/__init__.py`. CI ratchets: `--max-untimed`, `--max-untagged`.
- **`wican`** — Build and sync the device's AutoPID profile from `ecus/` (`wican autopid write` →
  `out/`). Verified-only by default. Details: `interop/autopid/`.
- **`bix`** — Byte-index converter and payload annotator. **Never convert a byte index by hand**:
  it depends on the response's length and service, which `bix` reads off the payload.
- **`config`** — The user config (`~/.config/canair/config.yaml`); `config example` prints the
  authoritative `config.example.yaml`. Prefer it over hand-editing.
