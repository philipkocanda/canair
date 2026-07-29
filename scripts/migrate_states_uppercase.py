#!/usr/bin/env python3
"""One-shot migration: UPPERCASE + inline every ``vehicle_states:`` in ecus/.

State tokens became an UPPERCASE controlled vocabulary (like the CAN-bus segment
codes), and the per-ECU files render the ``vehicle_states:`` field as a compact
inline flow list (``[SLEEP, PLUGGED]``) for readability in the long files. This
converts a profile's existing per-ECU definitions to that form:

    vehicle_states:          ->   vehicle_states: [READY, CHARGING]
      - ready
      - charging

    vehicle_states: [acc]    ->   vehicle_states: [ACC]

Every ``vehicle_states:`` occurrence is rewritten regardless of the section it
sits in (ECU-level, per-PID, per-DID, iocontrol, research, scan_log). The
transform is text-based (not a YAML round-trip) so nothing else in the file is
reflowed — the diff is limited to the state fields.

Historical capture files are intentionally left untouched: ``parse_states``
upper-cases stored tokens on read, so lower-case captures normalize
automatically without rewriting the append-only logs.

Usage:
    uv run python scripts/migrate_states_uppercase.py [--profile NAME] [--apply]

Without --apply it is a DRY RUN (prints a unified diff, writes nothing).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

from canlib.states import parse_states

# A `vehicle_states:` line, capturing (indent, remainder-after-colon).
_FIELD_RE = re.compile(r"^(?P<indent>\s*)vehicle_states:(?P<rest>.*)$")


def _inline(indent: str, tokens: list[str], comment: str = "") -> str:
    """Render the canonical inline field line for ``tokens`` at ``indent``."""
    body = ", ".join(tokens)
    line = f"{indent}vehicle_states: [{body}]"
    return f"{line}{comment}" if comment else line


def migrate_text(text: str) -> str:
    """Rewrite every ``vehicle_states:`` field to UPPERCASE inline form."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip("\n")
        eol = raw[len(line) :]  # preserve the exact line ending ("" on EOF)
        m = _FIELD_RE.match(line)
        if not m:
            out.append(raw)
            i += 1
            continue

        indent = m.group("indent")
        rest = m.group("rest")
        stripped = rest.strip()

        # Case A: inline list already present (`vehicle_states: [a, b]  # note`).
        if stripped.startswith("["):
            close = rest.find("]")
            if close != -1:
                inner = rest[rest.find("[") + 1 : close]
                trailing = rest[close + 1 :]
                tokens = parse_states(inner)
                out.append(_inline(indent, tokens, trailing) + eol)
                i += 1
                continue
            out.append(raw)  # unclosed bracket — leave untouched (shouldn't happen)
            i += 1
            continue

        # Case B: a non-empty scalar remainder that isn't a list — leave as-is.
        if stripped and not stripped.startswith("#"):
            out.append(raw)
            i += 1
            continue

        # Case C: block form — the key line is bare (maybe a trailing comment),
        # followed by `- item` entries. YAML permits the dash either indented
        # past the key or flush with it, so accept any dash at indent >= the key.
        key_indent = len(indent)
        item_re = re.compile(r"^(?P<lead>\s*)-\s+(?P<item>.*)$")
        j = i + 1
        tokens = []
        consumed_any = False
        while j < n:
            item_line = lines[j].rstrip("\n")
            im = item_re.match(item_line)
            if im and len(im.group("lead")) >= key_indent:
                tokens.extend(parse_states(im.group("item")))
                consumed_any = True
                j += 1
                continue
            if not consumed_any and (not item_line.strip() or item_line.strip().startswith("#")):
                break
            break

        if not consumed_any:
            out.append(raw)  # bare `vehicle_states:` with no items (odd) — leave
            i += 1
            continue

        comment = rest.rstrip() if stripped.startswith("#") else ""
        out.append(_inline(indent, tokens, f"  {comment}" if comment else "") + eol)
        i = j

    return "".join(out)


def _process(path: Path, apply: bool) -> bool:
    original = path.read_text()
    new = migrate_text(original)
    if new == original:
        return False
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path) + " (migrated)",
    )
    sys.stdout.writelines(diff)
    if apply:
        path.write_text(new)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
        if _process(path, args.apply):
            changed += 1

    print(f"\n{'Applied' if args.apply else 'Would change'} {changed} file(s).")
    if not args.apply:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
