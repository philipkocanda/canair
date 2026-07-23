#!/usr/bin/env python3
"""One-shot migration: PID `status:` enum + `vehicle_states` field rename.

Converts a profile's ecus/ and captures/ from the legacy schema to the
consolidated one (see plans / AGENTS notes):

  PID visibility booleans  ->  single `status:` lifecycle
    ignored: true                 -> status: ignored
    static: true (± enabled:false)-> status: static
    enabled: false                -> status: draft
    enabled: true / absent        -> status: active   (implicit; key dropped)

  Power-state vocabulary field renamed to `vehicle_states` everywhere:
    ECU/PID  availability: [...]        -> vehicle_states: [...]
    iocontrol  availability: [...]      -> vehicle_states: [...]
    research   prerequisite: [...]      -> vehicle_states: [...]
    scan_log   state: <scalar>          -> vehicle_states: [<token>]
    capture session  state: "free text" -> vehicle_states: [tokens]
        (non-vocabulary descriptive text is preserved in the session `notes`)

Tokens are lower-cased and light-normalized (asleep -> sleep). Files are
round-tripped through ruamel so comments/layout survive.

Usage:
    uv run python scripts/migrate_states_status.py [--profile NAME] [--apply]

Without --apply it is a DRY RUN: it prints a unified diff of every change and
writes nothing. Re-run with --apply to persist.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from io import StringIO
from pathlib import Path

from canlib.states import POWER_STATES
from canlib.yaml_rt import detect_sequence_indent, round_trip_yaml
from canlib.yaml_rt import dump as _dump

SYNONYMS = {"asleep": "sleep"}
# Vocabulary accepted verbatim into a vehicle_states list (composites included).
VOCAB = set(POWER_STATES) | {"deep sleep", "parked", "driving"}


def norm_token(tok: str) -> str:
    t = str(tok).strip().lower()
    return SYNONYMS.get(t, t)


def norm_state_list(value) -> list[str]:
    """Normalize an existing availability/prerequisite list to lower-case tokens."""
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    out: list[str] = []
    for v in value:
        t = norm_token(v)
        if t and t not in out:
            out.append(t)
    return out


def parse_free_state(text: str) -> tuple[list[str], str | None]:
    """Split a free-text capture ``state`` into (tokens, leftover-description).

    Comma-separated segments that are (or begin with) a vocabulary token feed
    ``vehicle_states``; anything unrecognized is returned as leftover so the
    caller can preserve it in the session notes.
    """
    segments = [s.strip() for s in str(text).split(",") if s.strip()]
    tokens: list[str] = []
    leftover: list[str] = []
    for seg in segments:
        low = norm_token(seg)
        if low in VOCAB:
            if low not in tokens:
                tokens.append(low)
            continue
        # Try the leading word (e.g. "driving mt->kw" -> "driving").
        head = norm_token(low.split()[0]) if low.split() else ""
        if head in VOCAB and head not in tokens:
            tokens.append(head)
        leftover.append(seg)
    return tokens, (", ".join(leftover) if leftover else None)


def _rename_key(m, old: str, new: str) -> bool:
    """Rename a mapping key in place, preserving position (ruamel CommentedMap)."""
    if old not in m:
        return False
    pos = list(m.keys()).index(old)
    val = m[old]
    del m[old]
    m.insert(pos, new, val)
    return True


def _set_status(pid) -> None:
    """Collapse legacy ignored/static/enabled booleans into a single status key."""
    ignored = pid.get("ignored") is True
    static = pid.get("static") is True
    enabled = pid.get("enabled")
    for k in ("ignored", "static", "enabled"):
        if k in pid:
            del pid[k]
    if ignored:
        status = "ignored"
    elif static:
        status = "static"
    elif enabled is False:
        status = "draft"
    else:
        status = "active"  # implicit default — no key
    if status != "active":
        pid.insert(0, "status", status)


def migrate_ecu(data) -> None:
    """Apply status + vehicle_states transforms to one loaded ECU document."""
    for _ecu_name, ecu in data.items():
        if not isinstance(ecu, dict):
            continue
        # ECU-level availability -> vehicle_states (normalize tokens)
        if "availability" in ecu:
            ecu["availability"] = norm_state_list(ecu["availability"])
            _rename_key(ecu, "availability", "vehicle_states")

        for pid in (ecu.get("pids") or {}).values():
            if not isinstance(pid, dict):
                continue
            _set_status(pid)
            if "availability" in pid:
                pid["availability"] = norm_state_list(pid["availability"])
                _rename_key(pid, "availability", "vehicle_states")

        for entry in (ecu.get("iocontrol") or {}).values():
            if isinstance(entry, dict) and "availability" in entry:
                entry["availability"] = norm_state_list(entry["availability"])
                _rename_key(entry, "availability", "vehicle_states")

        for entry in ecu.get("research") or []:
            if isinstance(entry, dict) and "prerequisite" in entry:
                entry["prerequisite"] = norm_state_list(entry["prerequisite"])
                _rename_key(entry, "prerequisite", "vehicle_states")

        for entry in ecu.get("scan_log") or []:
            if isinstance(entry, dict) and "state" in entry:
                entry["state"] = norm_state_list(entry["state"])
                _rename_key(entry, "state", "vehicle_states")


def migrate_captures(data) -> None:
    """Rename session ``state`` (free text) -> ``vehicle_states`` (token list)."""
    for session in data.get("sessions", []) or []:
        if not isinstance(session, dict) or "state" not in session:
            continue
        original = session["state"]
        tokens, leftover = parse_free_state(original)
        if leftover:
            note = f"[migrated state: {original}]"
            existing = session.get("notes")
            session["notes"] = f"{existing} {note}".strip() if existing else note
        session["state"] = tokens
        _rename_key(session, "state", "vehicle_states")


def _process(path: Path, migrate_fn, apply: bool) -> bool:
    original = path.read_text()
    seq = detect_sequence_indent(original)
    y = round_trip_yaml(*(seq if seq else (2, 0)))
    data = y.load(original)
    if data is None:
        return False
    migrate_fn(data)
    buf = StringIO()
    if seq:
        _dump(data, buf, sequence=seq[0], offset=seq[1])
    else:
        _dump(data, buf)
    new = buf.getvalue()
    if new == original:
        return False
    diff = difflib.unified_diff(
        original.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path), tofile=str(path) + " (migrated)",
    )
    sys.stdout.writelines(diff)
    if apply:
        path.write_text(new)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=None, help="Profile name (default: active)")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = ap.parse_args()

    from canlib.profile import active, resolve_profile
    prof = resolve_profile(args.profile) if args.profile else active()
    print(f"Profile: {prof.name}  ({'APPLY' if args.apply else 'DRY RUN'})\n")

    changed = 0
    for path in sorted(prof.ecus_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        if _process(path, migrate_ecu, args.apply):
            changed += 1

    cap_dir = prof.captures_dir
    if cap_dir.exists():
        for path in sorted(cap_dir.glob("*.yaml")):
            if path.name.startswith(("SCHEMA", "_")):
                continue
            if _process(path, migrate_captures, args.apply):
                changed += 1

    print(f"\n{'Applied' if args.apply else 'Would change'} {changed} file(s).")
    if not args.apply:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
