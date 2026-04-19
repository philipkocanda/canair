#!/usr/bin/env python3
"""Clean up corrupted ``iocontrol_discoveries:`` entries in pids/*.yaml.

Background
----------
Before ``canlib/elm327.py`` gained SID/DID echo validation, the IOControl
scanner trusted whatever hex the ELM327 adapter returned. Late-arriving
responses from a previous probe would leak into the next read, silently
attributing e.g. BC09's response (``6FBC0900``) to BC0A. Every subsequent
hit shifted by -1 DID. A separate anomaly left a handful of entries with
garbage like ``response: "7E00"`` — not a valid 6F/7F UDS response.

This script scrubs those bad records:

- **Bad discovery entries** are removed when their ``response:`` doesn't
  start with ``6F{DID}`` (off-by-one / wrong-DID) or isn't a well-formed
  6F/7F UDS reply at all.
- **Stale promotes** (curated entries with ``verified: false`` whose
  ``on:`` payload looks pre-fix — either bare ``2F{DID}03`` with no
  controlState byte, or ``2F{DID}03FF...`` all-FF — are listed so the
  operator can decide whether to re-scan + re-promote. This script does
  NOT modify curated/promoted entries.

Default behaviour is to **apply** changes (writing files in place).
Use ``--dry-run`` to preview without writing. Empty
``iocontrol_discoveries:`` sections are removed entirely.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from canlib.constants import PIDS_DIR
from canlib.pids_edit import (
    PidsEditError,
    _count_discovery_entries,
    _find_discovery_block,
    _remove_discovery_section,
)


DISCOVERY_SECTION_RE = re.compile(r"^ {2}iocontrol_discoveries:\s*$", re.MULTILINE)
DISCOVERY_SECTION_TAIL_RE = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
DID_KEY_RE = re.compile(r"^ {4}([0-9A-Fa-f]{4}):\s*$", re.MULTILINE)
RESPONSE_LINE_RE = re.compile(r'^ {6}response:\s*"([0-9A-Fa-f]*)"\s*$', re.MULTILINE)

# Match a curated on: line that's stale (pre-fix promote default):
#   on: "2F{DID}03"        -- no controlState byte
#   on: "2F{DID}03FF..."   -- all-FF fallback from the old _infer_on_payload
STALE_ON_RE = re.compile(
    r'^ {6}on:\s*"2F([0-9A-Fa-f]{4})03((?:FF)*)"\s*$',
    re.MULTILINE,
)


def classify_discovery_response(did: str, response_hex: str) -> str | None:
    """Return reason string if the discovery response is bad; else None.

    Good responses:
      * Positive echo:  ``6F {DID} ...``
      * NRC for SID 2F: ``7F 2F {NRC}``
    """
    r = response_hex.upper()
    did_u = did.upper()
    if len(r) % 2 != 0:
        return f"odd hex length ({len(r)})"
    if len(r) < 2:
        return "response too short"
    if r.startswith(f"6F{did_u}"):
        return None
    if r.startswith("6F"):
        echoed = r[2:6]
        return f"DID echo mismatch (response carries 0x{echoed}, not 0x{did_u})"
    if r.startswith("7F2F"):
        return None
    if r.startswith("7F"):
        svc = r[2:4]
        return f"NRC for wrong service (0x{svc}, not 0x2F)"
    return f"not a 6F/7F UDS response (leading byte 0x{r[:2]})"


def find_bad_discoveries(text: str) -> list[tuple[str, str, str]]:
    """Return list of (did, response_hex, reason) for bad discovery entries."""
    sec_m = DISCOVERY_SECTION_RE.search(text)
    if not sec_m:
        return []
    tail_m = DISCOVERY_SECTION_TAIL_RE.search(text, pos=sec_m.end() + 1)
    sec_end = tail_m.start() if tail_m else len(text)

    bad: list[tuple[str, str, str]] = []
    for did_m in DID_KEY_RE.finditer(text, pos=sec_m.end(), endpos=sec_end):
        did = did_m.group(1).upper()
        # Find the response: line inside this DID's block (up to next DID key
        # or section end).
        next_m = DID_KEY_RE.search(text, pos=did_m.end(), endpos=sec_end)
        block_end = next_m.start() if next_m else sec_end
        block = text[did_m.start():block_end]
        resp_m = RESPONSE_LINE_RE.search(block)
        if not resp_m:
            # No response field at all — treat as bad so it doesn't linger.
            bad.append((did, "", "no response: field"))
            continue
        reason = classify_discovery_response(did, resp_m.group(1))
        if reason:
            bad.append((did, resp_m.group(1), reason))
    return bad


def find_stale_promotes(text: str) -> list[tuple[str, str]]:
    """Return (did, on_payload_hex) for curated entries with a pre-fix on:.

    Only flags entries within the ``  iocontrol:`` section (not discoveries)
    that are ``verified: false``. Operator decides follow-up.
    """
    # Locate the iocontrol: section boundaries (not iocontrol_discoveries:).
    # Match 2-space indent "iocontrol:" specifically, not "iocontrol_...".
    sec_re = re.compile(r"^ {2}iocontrol:\s*$", re.MULTILINE)
    sec_m = sec_re.search(text)
    if not sec_m:
        return []
    tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
    tail_m = tail_re.search(text, pos=sec_m.end() + 1)
    sec_end = tail_m.start() if tail_m else len(text)

    stale: list[tuple[str, str]] = []
    for did_m in DID_KEY_RE.finditer(text, pos=sec_m.end(), endpos=sec_end):
        did = did_m.group(1).upper()
        next_m = DID_KEY_RE.search(text, pos=did_m.end(), endpos=sec_end)
        block_end = next_m.start() if next_m else sec_end
        block = text[did_m.start():block_end]
        if re.search(r"^ {6}verified:\s*true\s*$", block, re.MULTILINE):
            continue
        on_m = STALE_ON_RE.search(block)
        if on_m and on_m.group(1).upper() == did:
            # Stale: matches 2F{DID}03 or 2F{DID}03(FF)+
            payload = on_m.group(2).upper()
            stale.append((did, f"2F{did}03{payload}"))
    return stale


def process_file(path: Path, dry_run: bool) -> dict:
    """Process one pids/*.yaml. Returns summary dict."""
    original = path.read_text()
    text = original

    bad = find_bad_discoveries(text)
    stale = find_stale_promotes(text)

    removed = 0
    errors: list[str] = []
    for did, _resp, _reason in bad:
        try:
            start, end = _find_discovery_block(text, did)
            text = text[:start] + text[end:]
            removed += 1
        except PidsEditError as exc:
            errors.append(f"{did}: {exc}")

    # If the section is now empty, drop the header + trailing blank line.
    if removed > 0 and _count_discovery_entries(text) == 0:
        text = _remove_discovery_section(text)
        section_dropped = True
    else:
        section_dropped = False

    changed = text != original
    if changed and not dry_run:
        path.write_text(text)

    return {
        "path": path,
        "bad": bad,
        "stale": stale,
        "removed": removed,
        "section_dropped": section_dropped,
        "errors": errors,
        "changed": changed,
    }


def format_summary(result: dict, dry_run: bool) -> str:
    lines: list[str] = []
    rel = result["path"].name
    bad = result["bad"]
    stale = result["stale"]

    if not bad and not stale:
        return ""  # Skip quiet files.

    lines.append(f"\n=== {rel} ===")

    if bad:
        action = "would remove" if dry_run else "removed"
        lines.append(f"  {action} {len(bad)} bad discover{'y' if len(bad) == 1 else 'ies'}:")
        for did, resp, reason in bad:
            resp_disp = f'"{resp}"' if resp else "(missing)"
            lines.append(f"    {did}  response={resp_disp}  — {reason}")
        if result["section_dropped"]:
            lines.append("  iocontrol_discoveries: section emptied and removed")

    if result["errors"]:
        lines.append("  errors:")
        for err in result["errors"]:
            lines.append(f"    ! {err}")

    if stale:
        lines.append(
            f"  {len(stale)} stale unverified promote{'s' if len(stale) != 1 else ''} "
            f"(pre-fix on: payload — NOT modified, re-promote recommended):"
        )
        for did, on_hex in stale:
            lines.append(f"    {did}  on=\"{on_hex}\"")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files",
    )
    ap.add_argument(
        "--pids-dir",
        type=Path,
        default=PIDS_DIR,
        help=f"Directory of per-ECU YAML files (default: {PIDS_DIR})",
    )
    args = ap.parse_args()

    files = sorted(args.pids_dir.glob("*.yaml"))
    if not files:
        print(f"No *.yaml files in {args.pids_dir}", file=sys.stderr)
        return 1

    total_bad = 0
    total_stale = 0
    touched_files: list[str] = []

    for path in files:
        result = process_file(path, dry_run=args.dry_run)
        summary = format_summary(result, dry_run=args.dry_run)
        if summary:
            print(summary)
        total_bad += len(result["bad"])
        total_stale += len(result["stale"])
        if result["changed"]:
            touched_files.append(path.name)

    print("\n--- Summary ---")
    print(f"  scanned:     {len(files)} file(s)")
    print(f"  bad discoveries {'(would be)' if args.dry_run else ''} removed: {total_bad}")
    print(f"  stale promotes listed (manual review): {total_stale}")
    if args.dry_run:
        print(f"  files that WOULD change: {len(touched_files)}")
        if touched_files:
            print(f"    {', '.join(touched_files)}")
        print("\n  Re-run without --dry-run to apply.")
    else:
        print(f"  files modified: {len(touched_files)}")
        if touched_files:
            print(f"    {', '.join(touched_files)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
