#!/usr/bin/env python3
"""Validate SKILL.md frontmatter for Claude Code / OpenCode compatibility.

canair ships agent skills under ``.claude/skills/<name>/SKILL.md`` (and picks up
``.agents/skills/`` / ``.opencode/skills/`` if either is ever used). Two different
tools load these files, and each enforces its own rules on the YAML frontmatter:

- OpenCode requires ``name`` (1-64 chars, lowercase/hyphenated, matching the
  directory name) and ``description`` (1-1024 chars); any other field is
  silently ignored.
- The portable Agent Skills spec (used by claude.ai skill uploads, the Skills
  API, and ``package_skill.py``) accepts only six fields:
  ``name``/``description``/``license``/``compatibility``/``metadata``/
  ``allowed-tools``. Any other key is a **hard error** at packaging/upload
  time. Claude Code's own interactive CLI is more permissive than this, but
  targeting the strict spec keeps a skill portable everywhere.

This script checks every bundled skill against the intersection of both rule
sets, so a skill that passes here loads cleanly in OpenCode and packages
cleanly for Claude Code.

Usage:
    uv run scripts/validate_skills.py

Run it after adding or editing a skill's frontmatter. Pre-commit and CI both
run it (see .pre-commit-config.yaml and .github/workflows/ci.yml).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

SKILL_GLOBS = [
    ".claude/skills/*/SKILL.md",
    ".agents/skills/*/SKILL.md",
    ".opencode/skills/*/SKILL.md",
]

# OpenCode's name rule: lowercase alphanumeric, single-hyphen separated, no
# leading/trailing/doubled hyphens.
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_COMPATIBILITY_LEN = 500

# The Agent Skills spec's full field set — the compatibility ceiling for a
# skill that must round-trip through both OpenCode and Claude Code's strict
# packaging/upload path. OpenCode ignores anything outside this set; the
# spec's own tooling hard-errors on it.
ALLOWED_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
REQUIRED_FIELDS = {"name", "description"}


def find_skill_files() -> list[Path]:
    files: list[Path] = []
    for pattern in SKILL_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    return files


def parse_frontmatter(text: str, rel_path: Path) -> tuple[dict, list[str]]:
    """Extract and parse a SKILL.md's YAML frontmatter block.

    Returns (data, errors). ``data`` is ``{}`` when parsing failed.
    """
    if not text.startswith("---"):
        return {}, [f"{rel_path}: missing YAML frontmatter (file must start with '---')"]

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, [f"{rel_path}: malformed frontmatter (no closing '---')"]

    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        return {}, [f"{rel_path}: invalid YAML in frontmatter: {exc}"]

    if not isinstance(data, dict):
        return {}, [f"{rel_path}: frontmatter must be a YAML mapping"]

    return data, []


def validate_frontmatter(data: dict, rel_path: Path, dir_name: str) -> list[str]:
    """Check a parsed frontmatter mapping against the combined rule set."""
    errors: list[str] = []

    unknown = sorted(set(data) - ALLOWED_FIELDS)
    if unknown:
        errors.append(
            f"{rel_path}: unrecognized frontmatter field(s) {unknown} — only "
            f"{sorted(ALLOWED_FIELDS)} are portable across Claude Code and OpenCode"
        )

    missing = sorted(REQUIRED_FIELDS - set(data))
    if missing:
        errors.append(f"{rel_path}: missing required field(s) {missing}")

    if "name" in data:
        name = data["name"]
        if not isinstance(name, str):
            errors.append(f"{rel_path}: 'name' must be a string, got {type(name).__name__}")
        else:
            if not (1 <= len(name) <= MAX_NAME_LEN):
                errors.append(
                    f"{rel_path}: 'name' must be 1-{MAX_NAME_LEN} characters, got {len(name)}"
                )
            if not NAME_RE.match(name):
                errors.append(
                    f"{rel_path}: 'name' must be lowercase alphanumeric with single-hyphen "
                    f"separators (matching {NAME_RE.pattern!r}), got {name!r}"
                )
            if name != dir_name:
                errors.append(
                    f"{rel_path}: 'name' ({name!r}) must match its directory name "
                    f"({dir_name!r}) — OpenCode requires this"
                )

    if "description" in data:
        description = data["description"]
        if not isinstance(description, str):
            errors.append(
                f"{rel_path}: 'description' must be a string, got {type(description).__name__}"
            )
        elif not (1 <= len(description) <= MAX_DESCRIPTION_LEN):
            errors.append(
                f"{rel_path}: 'description' must be 1-{MAX_DESCRIPTION_LEN} characters, "
                f"got {len(description)}"
            )

    if "license" in data and not isinstance(data["license"], str):
        errors.append(f"{rel_path}: 'license' must be a string")

    if "compatibility" in data:
        compatibility = data["compatibility"]
        if not isinstance(compatibility, str):
            errors.append(f"{rel_path}: 'compatibility' must be a string")
        elif len(compatibility) > MAX_COMPATIBILITY_LEN:
            errors.append(
                f"{rel_path}: 'compatibility' must be <= {MAX_COMPATIBILITY_LEN} characters, "
                f"got {len(compatibility)}"
            )

    if "metadata" in data:
        metadata = data["metadata"]
        if not isinstance(metadata, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
        ):
            errors.append(f"{rel_path}: 'metadata' must be a mapping of string to string")

    if "allowed-tools" in data:
        allowed_tools = data["allowed-tools"]
        is_str = isinstance(allowed_tools, str)
        is_str_list = isinstance(allowed_tools, list) and all(
            isinstance(t, str) for t in allowed_tools
        )
        if not (is_str or is_str_list):
            errors.append(f"{rel_path}: 'allowed-tools' must be a string or a list of strings")

    return errors


def validate_skill_file(path: Path) -> list[str]:
    rel_path = path.relative_to(REPO_ROOT)
    text = path.read_text()
    data, errors = parse_frontmatter(text, rel_path)
    if errors:
        return errors
    return validate_frontmatter(data, rel_path, dir_name=path.parent.name)


def main(argv: list[str] | None = None) -> int:
    del argv  # no flags — this is a pure check, there is nothing to (re)generate

    files = find_skill_files()
    if not files:
        print("No SKILL.md files found.")
        return 0

    all_errors: list[str] = []
    for path in files:
        all_errors.extend(validate_skill_file(path))

    if all_errors:
        print(
            f"Skill frontmatter validation failed ({len(all_errors)} issue(s)):",
            file=sys.stderr,
        )
        for error in all_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(
        f"Validated {len(files)} skill file(s) — frontmatter is Claude Code / OpenCode compatible."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
