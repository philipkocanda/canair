"""Validate pids/, ecus.yaml, and captures/ against their schemas.

Validators merged into one subcommand:

  * ``validate pids`` — per-ECU PID definition files in pids/ vs pids_schema.yaml
  * ``validate ecus`` — the ecus.yaml registry vs ecus_schema.yaml (+ pids cross-check)
  * ``validate captures`` — capture files in captures/ vs captures_schema.json
  * ``validate all`` (default) — run all three

Usage:
    canair validate                 # validate pids + ecus + captures
    canair validate pids            # validate all ECU files
    canair validate pids pids/bms.yaml  # validate specific file(s)
    canair validate pids --stats    # show parameter statistics
    canair validate ecus            # validate the ECU registry
    canair validate captures        # validate all capture files
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from canlib.byteindex import wican_to_isotp
from canlib.constants import SCHEMA_DIR

SCHEMA_FILE = SCHEMA_DIR / "pids_schema.yaml"
CAPTURES_SCHEMA_FILE = SCHEMA_DIR / "captures_schema.json"
ECUS_SCHEMA_FILE = SCHEMA_DIR / "ecus_schema.yaml"

NAME = "validate"

# Regex for valid WiCAN expressions (basic sanity — not a full parser)
EXPR_TOKEN_RE = re.compile(
    r"\[[BS]\d+:[BS]\d+\]|"  # [Bnn:Bmm] multi-byte (must be before Bnn:k)
    r"[BS]\d+:\d+|"  # Bnn:k (bit access)
    r"[BS]\d+|"  # Bnn, Snn
    r"V|"  # external value
    r"[0-9]+\.?[0-9]*|"  # numeric literal
    r"[+\-*/()&|^<>=\s]"  # operators, whitespace
)

DEPRECATED_FIELDS = {"ecu_tx", "ecu_rx", "ecu_name", "decoded"}


# ── pids validation (from validate-pids.py) ───────────────────────────────


def load_schema(path: Path = SCHEMA_FILE) -> dict:
    """Load schema definition from _schema.yaml."""
    with open(path) as f:
        schema = yaml.safe_load(f)
    if not schema or not isinstance(schema, dict):
        print(f"ERROR: {path} is empty or invalid", file=sys.stderr)
        sys.exit(1)
    return schema


def validate_expression(expr: str, param_name: str, pid: str, ecu: str) -> list[str]:
    """Basic expression syntax check."""
    errors = []
    if not expr or not expr.strip():
        errors.append(f"{ecu}/{pid}/{param_name}: empty expression")
        return errors

    # Check for completely unrecognizable tokens
    cleaned = EXPR_TOKEN_RE.sub("", expr)
    if cleaned.strip():
        errors.append(
            f"{ecu}/{pid}/{param_name}: suspicious expression chars: '{cleaned.strip()}' in '{expr}'"
        )
    return errors


def check_pci_bytes(expr: str, param_name: str, pid: str, ecu: str) -> list[str]:
    """Warn if an expression reads ISO-TP PCI bytes.

    PCI (frame-header) bytes live at WiCAN indices 0, 1, 8, 16, 24, 32, ...
    (``wican_to_isotp`` returns None for them). Reading one yields the frame
    counter/length, not real data — a common byte-index mistake. This also
    flags multi-byte ranges ``[Bnn:Bmm]`` that *span* a PCI byte, since those
    read consecutive raw bytes without skipping PCI.
    """
    warnings = []

    def is_pci(idx: int) -> bool:
        return wican_to_isotp(idx) is None

    # Multi-byte ranges: [Bnn:Bmm] / [Snn:Smm] — flag if any index in range is PCI
    ranges = []
    for m in re.finditer(r"\[[BS](\d+):[BS](\d+)\]", expr):
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        ranges.append((m.span(), lo, hi))
        pci = [x for x in range(lo, hi + 1) if is_pci(x)]
        if pci:
            warnings.append(
                f"{ecu}/{pid}/{param_name}: range '{m.group(0)}' spans ISO-TP PCI byte(s) "
                f"{', '.join(f'B{x}' for x in pci)} — reads frame header, not data"
            )

    # Single byte refs: Bnn, Snn, Bnn:k — skip those inside a flagged range
    for m in re.finditer(r"[BS](\d+)", expr):
        idx = int(m.group(1))
        if any(start <= m.start() < end for (start, end), _, _ in ranges):
            continue
        if is_pci(idx):
            warnings.append(
                f"{ecu}/{pid}/{param_name}: reads ISO-TP PCI byte B{idx} "
                f"(frame header, not data) in '{expr}'"
            )
    return warnings


def validate_ecu_file(
    path: Path,
    required_ecu_fields: set,
    optional_ecu_fields: set,
    required_pid_fields: set,
    optional_pid_fields: set,
    required_param_fields: set,
    all_param_fields: set,
) -> tuple[list[str], list[str], dict]:
    """Validate a single ECU YAML file.

    Returns (errors, warnings, stats).
    """
    errors = []
    warnings = []
    stats = {
        "ecus": 0,
        "pids": 0,
        "params": 0,
        "verified": 0,
        "unverified": 0,
        "ignored": 0,
        "iocontrol": 0,
        "research": 0,
        "routines": 0,
        "iocontrol_discoveries": 0,
    }

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if not data or not isinstance(data, dict):
        return [f"{path.name}: empty or invalid YAML"], [], stats

    all_ecu_fields = required_ecu_fields | optional_ecu_fields | {
        "iocontrol", "routines", "iocontrol_discoveries"
    }
    all_pid_fields = required_pid_fields | optional_pid_fields

    for ecu_name, ecu_def in data.items():
        stats["ecus"] += 1

        if not isinstance(ecu_def, dict):
            errors.append(f"{path.name}/{ecu_name}: ECU definition must be a dict")
            continue

        # Check required ECU fields
        for field in required_ecu_fields:
            if field not in ecu_def:
                errors.append(f"{path.name}/{ecu_name}: missing required field '{field}'")

        # Check unknown ECU fields
        for field in ecu_def:
            if field not in all_ecu_fields:
                warnings.append(f"{path.name}/{ecu_name}: unknown ECU field '{field}'")

        # Validate tx_id
        tx_id = ecu_def.get("tx_id")
        if tx_id is not None:
            if not isinstance(tx_id, int) or tx_id < 0 or tx_id > 0x7FF:
                errors.append(f"{path.name}/{ecu_name}: tx_id must be 0x000-0x7FF, got {tx_id}")

        # Validate PIDs
        pids = ecu_def.get("pids", {})
        if not isinstance(pids, dict):
            errors.append(f"{path.name}/{ecu_name}: 'pids' must be a dict")
            continue

        for pid_code, pid_def in pids.items():
            stats["pids"] += 1
            pid_str = str(pid_code)

            if not isinstance(pid_def, dict):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: PID definition must be a dict")
                continue

            # Check required PID fields
            for field in required_pid_fields:
                if field not in pid_def:
                    errors.append(
                        f"{path.name}/{ecu_name}/{pid_str}: missing required PID field '{field}'"
                    )

            # Check unknown PID fields
            for field in pid_def:
                if field not in all_pid_fields:
                    warnings.append(
                        f"{path.name}/{ecu_name}/{pid_str}: unknown PID field '{field}'"
                    )

            # Validate period
            period = pid_def.get("period")
            if period is not None and (not isinstance(period, int) or period < 0):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: period must be positive int")

            # Validate availability
            VALID_AVAILABILITY = {"sleep", "plugged", "acc", "acc2", "ready", "charging"}
            avail = pid_def.get("availability")
            if avail is not None:
                if not isinstance(avail, list):
                    errors.append(
                        f"{path.name}/{ecu_name}/{pid_str}: availability must be a list"
                    )
                else:
                    for v in avail:
                        if v not in VALID_AVAILABILITY:
                            errors.append(
                                f"{path.name}/{ecu_name}/{pid_str}: invalid availability "
                                f"value '{v}' (allowed: {sorted(VALID_AVAILABILITY)})"
                            )
                    if len(avail) != len(set(avail)):
                        errors.append(
                            f"{path.name}/{ecu_name}/{pid_str}: duplicate availability values"
                        )

            # Ignored PIDs — count and skip parameter validation
            if pid_def.get("ignored", False):
                stats["ignored"] += 1
                continue

            # Validate parameters
            params = pid_def.get("parameters", {})
            if not isinstance(params, dict):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: 'parameters' must be a dict")
                continue

            if not params:
                warnings.append(f"{path.name}/{ecu_name}/{pid_str}: no parameters defined")

            for param_name, param in params.items():
                stats["params"] += 1

                if not isinstance(param, dict):
                    errors.append(f"{ecu_name}/{pid_str}/{param_name}: parameter must be a dict")
                    continue

                # Required fields
                for field in required_param_fields:
                    if field not in param:
                        errors.append(f"{ecu_name}/{pid_str}/{param_name}: missing '{field}'")

                # Unknown fields
                for field in param:
                    if field not in all_param_fields:
                        warnings.append(
                            f"{ecu_name}/{pid_str}/{param_name}: unknown field '{field}'"
                        )

                # Expression validation
                expr = param.get("expression", "")
                if expr:
                    errors.extend(validate_expression(expr, param_name, pid_str, ecu_name))
                    warnings.extend(check_pci_bytes(expr, param_name, pid_str, ecu_name))

                # Stats
                if param.get("verified", False):
                    stats["verified"] += 1
                else:
                    stats["unverified"] += 1

        # Validate IOControl section
        iocontrol = ecu_def.get("iocontrol")
        if iocontrol is not None:
            if not isinstance(iocontrol, dict):
                errors.append(f"{path.name}/{ecu_name}: 'iocontrol' must be a dict")
            else:
                valid_fields = {
                    "label",
                    "verified",
                    "on",
                    "off",
                    "notes",
                    "session",
                    "hold",
                    "availability",
                    "status_param",
                    True,
                    False,  # YAML bool keys from unquoted on/off
                }
                for did, did_def in iocontrol.items():
                    did_str = str(did)
                    if not isinstance(did_def, dict):
                        errors.append(f"{path.name}/{ecu_name}/iocontrol/{did_str}: must be a dict")
                        continue
                    if "label" not in did_def:
                        warnings.append(
                            f"{path.name}/{ecu_name}/iocontrol/{did_str}: missing 'label'"
                        )
                    for field in did_def:
                        if field not in valid_fields:
                            warnings.append(
                                f"{path.name}/{ecu_name}/iocontrol/{did_str}: unknown field '{field}'"
                            )
                    stats["iocontrol"] += 1

        # Validate research section
        research = ecu_def.get("research")
        if research is not None:
            if not isinstance(research, list):
                errors.append(f"{path.name}/{ecu_name}: 'research' must be a list")
            else:
                valid_types = {"scan", "decode", "verify", "iocontrol_scan"}
                valid_statuses = {"pending", "captured", "nrc", "done"}
                valid_priorities = {"P1", "P2", "P3"}
                valid_prereqs = {"sleep", "plugged", "acc", "acc2", "ready", "charging"}
                research_optional = {
                    "priority",
                    "prerequisite",
                    "date",
                    "result",
                    "notes",
                    "sources",
                    "what_to_test",
                    "capture_protocol",
                }
                research_required = {"type", "target", "status"}
                all_research_fields = research_required | research_optional

                for i, entry in enumerate(research):
                    label = f"{path.name}/{ecu_name}/research[{i}]"
                    if not isinstance(entry, dict):
                        errors.append(f"{label}: entry must be a dict")
                        continue

                    # Required fields
                    for field in research_required:
                        if field not in entry:
                            errors.append(f"{label}: missing required field '{field}'")

                    # Unknown fields
                    for field in entry:
                        if field not in all_research_fields:
                            warnings.append(f"{label}: unknown field '{field}'")

                    # Validate type
                    rtype = entry.get("type")
                    if rtype and rtype not in valid_types:
                        errors.append(
                            f"{label}: invalid type '{rtype}' (allowed: {sorted(valid_types)})"
                        )

                    # Validate status
                    rstatus = entry.get("status")
                    if rstatus and rstatus not in valid_statuses:
                        errors.append(
                            f"{label}: invalid status '{rstatus}' "
                            f"(allowed: {sorted(valid_statuses)})"
                        )

                    # Validate priority
                    rprio = entry.get("priority")
                    if rprio and rprio not in valid_priorities:
                        errors.append(
                            f"{label}: invalid priority '{rprio}' "
                            f"(allowed: {sorted(valid_priorities)})"
                        )

                    # Validate prerequisite
                    prereq = entry.get("prerequisite")
                    if prereq is not None:
                        if not isinstance(prereq, list):
                            errors.append(f"{label}: prerequisite must be a list")
                        else:
                            for v in prereq:
                                if v not in valid_prereqs:
                                    errors.append(
                                        f"{label}: invalid prerequisite '{v}' "
                                        f"(allowed: {sorted(valid_prereqs)})"
                                    )

                    stats["research"] += 1

        # Validate routines section (RoutineControl 0x31 discoveries) and
        # iocontrol_discoveries section (IOControl scanner output: UDS 0x2F SF 00
        # with 4-hex-digit DID keys, or KWP2000 0x30 IOCP 00 with 2-hex-digit LID
        # keys). Each entry maps an id to
        # {session, response, nrc, nrc_desc, label, verified, notes}.
        _validate_hit_section(
            ecu_def, "routines", path, ecu_name, errors, warnings, stats
        )
        _validate_hit_section(
            ecu_def,
            "iocontrol_discoveries",
            path,
            ecu_name,
            errors,
            warnings,
            stats,
        )

    return errors, warnings, stats


def _validate_hit_section(
    ecu_def: dict,
    section_name: str,
    path: Path,
    ecu_name: str,
    errors: list,
    warnings: list,
    stats: dict,
) -> None:
    """Validate a scanner-generated hit section (routines/iocontrol_discoveries).

    Both sections share the same entry schema: id_hex → {session, response,
    nrc, nrc_desc, label, verified, notes} with session ∈ {default, extended}
    and exactly one of {response, nrc} set.
    """
    section = ecu_def.get(section_name)
    if section is None:
        return
    if not isinstance(section, dict):
        errors.append(f"{path.name}/{ecu_name}: '{section_name}' must be a dict")
        return
    valid_sessions = {"default", "extended"}
    valid_fields = {
        "session",
        "response",
        "nrc",
        "nrc_desc",
        "label",
        "verified",
        "notes",
    }
    for key_id, entry in section.items():
        key_str = str(key_id)
        label = f"{path.name}/{ecu_name}/{section_name}/{key_str}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be a dict")
            continue
        for field in entry:
            if field not in valid_fields:
                warnings.append(f"{label}: unknown field '{field}'")
        rsession = entry.get("session")
        if rsession is not None and rsession not in valid_sessions:
            errors.append(
                f"{label}: invalid session '{rsession}' "
                f"(allowed: {sorted(valid_sessions)})"
            )
        has_resp = bool(entry.get("response"))
        has_nrc = entry.get("nrc") is not None
        if not has_resp and not has_nrc:
            warnings.append(f"{label}: neither 'response' nor 'nrc' set")
        stats[section_name] += 1


def validate_meta(path: Path, required_fields: set) -> list[str]:
    """Validate _meta.yaml."""
    errors = []
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"_meta.yaml: YAML parse error: {e}"]

    if not data or not isinstance(data, dict):
        return ["_meta.yaml: empty or invalid"]

    for field in required_fields:
        if field not in data:
            errors.append(f"_meta.yaml: missing required field '{field}'")

    return errors


def collect_pids_validation(files: list[Path]) -> tuple[list[str], list[str], dict]:
    """Return (errors, warnings, total_stats) for the given pids yaml files.

    Mirrors validate-pids main() aggregation: skips _schema.yaml; runs
    validate_meta on _meta.yaml; validate_ecu_file on the rest.
    """
    schema = load_schema()
    required_ecu_fields = set(schema.get("required_ecu_fields", []))
    optional_ecu_fields = set(schema.get("optional_ecu_fields", []))
    required_pid_fields = set(schema.get("required_pid_fields", []))
    optional_pid_fields = set(schema.get("optional_pid_fields", []))
    required_param_fields = set(schema.get("required_param_fields", []))
    optional_param_fields = set(schema.get("optional_param_fields", []))
    all_param_fields = required_param_fields | optional_param_fields
    required_meta_fields = set(schema.get("required_meta_fields", []))

    all_errors = []
    all_warnings = []
    total_stats = {
        "ecus": 0,
        "pids": 0,
        "params": 0,
        "verified": 0,
        "unverified": 0,
        "ignored": 0,
        "iocontrol": 0,
        "research": 0,
        "routines": 0,
        "iocontrol_discoveries": 0,
    }

    for fpath in files:
        if fpath.name == "_schema.yaml":
            continue

        if fpath.name == "_meta.yaml":
            all_errors.extend(validate_meta(fpath, required_meta_fields))
            continue

        errors, warnings, stats = validate_ecu_file(
            fpath,
            required_ecu_fields,
            optional_ecu_fields,
            required_pid_fields,
            optional_pid_fields,
            required_param_fields,
            all_param_fields,
        )
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        for k in total_stats:
            total_stats[k] += stats[k]

    return all_errors, all_warnings, total_stats


def validate_pids_file(fpath: Path) -> tuple[bool, str]:
    errors, warnings, _ = collect_pids_validation([fpath])
    lines = [f"  WARN: {w}" for w in warnings]
    lines += [f"  ERROR: {e}" for e in errors]
    if errors:
        lines.append(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return (not errors, "\n".join(lines))


def _run_pids(files: list[str] | None, stats: bool) -> int:
    if files:
        file_paths = [Path(f) for f in files]
    else:
        from canlib.profile import active

        file_paths = sorted(active().pids_dir.glob("*.yaml"))

    all_errors, all_warnings, total_stats = collect_pids_validation(file_paths)

    # Print results
    if all_warnings:
        for w in all_warnings:
            print(f"  WARN: {w}")
        print()

    if all_errors:
        for e in all_errors:
            print(f"  ERROR: {e}")
        print(f"\n{len(all_errors)} error(s), {len(all_warnings)} warning(s)")
        return 1

    n_files = len([f for f in file_paths if f.name not in ("_schema.yaml", "_meta.yaml")])
    ignored = total_stats["ignored"]
    ignored_str = f", {ignored} ignored" if ignored else ""
    ioctl = total_stats["iocontrol"]
    ioctl_str = f", {ioctl} IOControl DIDs" if ioctl else ""
    research = total_stats["research"]
    research_str = f", {research} research items" if research else ""
    routines = total_stats["routines"]
    routines_str = f", {routines} routines" if routines else ""
    ioctl_disc = total_stats["iocontrol_discoveries"]
    ioctl_disc_str = f", {ioctl_disc} IO discoveries" if ioctl_disc else ""
    print(
        f"OK — {n_files} ECU files, {total_stats['pids']} PIDs{ignored_str}{ioctl_str}{research_str}{routines_str}{ioctl_disc_str}, "
        f"{total_stats['params']} parameters "
        f"({total_stats['verified']} verified, {total_stats['unverified']} unverified)"
    )

    if all_warnings:
        print(f"  {len(all_warnings)} warning(s)")

    if stats:
        print(f"\n  ECUs:       {total_stats['ecus']}")
        print(f"  PIDs:       {total_stats['pids']}")
        print(f"  Ignored:    {total_stats['ignored']}")
        print(f"  Parameters: {total_stats['params']}")
        print(f"  Verified:   {total_stats['verified']}")
        print(f"  Unverified: {total_stats['unverified']}")
        print(f"  IOControl:  {total_stats['iocontrol']}")
        print(f"  Research:   {total_stats['research']}")
        print(f"  Routines:   {total_stats['routines']}")
        print(f"  IO Discoveries: {total_stats['iocontrol_discoveries']}")

    return 0


# ── ecus validation (ecus.yaml registry) ──────────────────────────────────


def load_ecus_schema(path: Path = ECUS_SCHEMA_FILE) -> dict:
    """Load the ECU-registry schema definition."""
    with open(path) as f:
        schema = yaml.safe_load(f)
    if not schema or not isinstance(schema, dict):
        print(f"ERROR: {path} is empty or invalid", file=sys.stderr)
        sys.exit(1)
    return schema


def _parse_tx_key(key) -> int | None:
    """Parse an ``ecus:`` map key (hex TX id string/int) into an int, or None."""
    if isinstance(key, int):
        return key
    s = str(key).strip()
    try:
        return int(s, 16)
    except ValueError:
        return None


def validate_ecus_registry(path: Path) -> tuple[list[str], list[str], dict]:
    """Validate ecus.yaml structure and fields (no cross-file checks).

    Returns (errors, warnings, stats). This is what the ecus_edit safe-writer
    relies on, so it must not depend on pids/ (which may lag during bootstrap).
    """
    schema = load_ecus_schema()
    top_level = set(schema.get("top_level_fields", []))
    required = set(schema.get("required_fields", []))
    allowed = required | set(schema.get("optional_fields", []))
    valid_protocols = set(schema.get("valid_id_protocols", []))
    valid_confidence = set(schema.get("valid_identity_confidence", []))
    scan_log_fields = set(schema.get("scan_log_entry_fields", {}).get("optional", []))

    errors: list[str] = []
    warnings: list[str] = []
    stats = {"ecus": 0, "scan_log": 0}

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if data is None:
        return [], [], stats
    if not isinstance(data, dict):
        return [f"{path.name}: top-level must be a mapping"], [], stats

    for field in data:
        if field not in top_level:
            warnings.append(f"{path.name}: unknown top-level key '{field}'")

    ecus = data.get("ecus")
    if ecus is None:
        ecus = {}
    if not isinstance(ecus, dict):
        errors.append(f"{path.name}: 'ecus' must be a mapping")
    else:
        seen_names: dict[str, str] = {}
        for key, entry in ecus.items():
            stats["ecus"] += 1
            tx = _parse_tx_key(key)
            disp = f"0x{tx:03X}" if tx is not None else key
            label = f"{path.name}/ecus/{disp}"
            if tx is None:
                errors.append(f"{label}: key must be a hex TX id (e.g. 0x7E0)")
            elif tx < 0 or tx > 0x7FF:
                errors.append(f"{label}: TX id must be 0x000-0x7FF, got 0x{tx:X}")

            if not isinstance(entry, dict):
                errors.append(f"{label}: entry must be a mapping")
                continue

            for field in required:
                if field not in entry:
                    errors.append(f"{label}: missing required field '{field}'")
            for field in entry:
                if field not in allowed:
                    warnings.append(f"{label}: unknown field '{field}'")

            proto = entry.get("id_protocol")
            if proto is not None and proto not in valid_protocols:
                errors.append(
                    f"{label}: invalid id_protocol '{proto}' "
                    f"(allowed: {sorted(valid_protocols)})"
                )

            conf = entry.get("identity_confidence")
            if conf is not None and conf not in valid_confidence:
                errors.append(
                    f"{label}: invalid identity_confidence '{conf}' "
                    f"(allowed: {sorted(valid_confidence)})"
                )

            name = entry.get("name")
            if name:
                nm = str(name).upper()
                if nm in seen_names:
                    errors.append(
                        f"{label}: duplicate ECU name '{name}' (also {seen_names[nm]})"
                    )
                else:
                    seen_names[nm] = disp

    scan_log = data.get("scan_log")
    if scan_log is not None:
        if not isinstance(scan_log, dict):
            errors.append(f"{path.name}: 'scan_log' must be a mapping")
        else:
            for key, entries in scan_log.items():
                label = f"{path.name}/scan_log/{key}"
                if _parse_tx_key(key) is None:
                    warnings.append(f"{label}: key is not a hex TX id")
                if not isinstance(entries, list):
                    errors.append(f"{label}: must be a list of probe entries")
                    continue
                for i, entry in enumerate(entries):
                    stats["scan_log"] += 1
                    if not isinstance(entry, dict):
                        errors.append(f"{label}[{i}]: entry must be a mapping")
                        continue
                    for field in entry:
                        if field not in scan_log_fields:
                            warnings.append(f"{label}[{i}]: unknown field '{field}'")

    return errors, warnings, stats


def validate_ecus_file(path: Path) -> tuple[bool, str]:
    """(ok, message) wrapper used by the ecus_edit safe-writer."""
    errors, warnings, _ = validate_ecus_registry(path)
    lines = [f"  WARN: {w}" for w in warnings]
    lines += [f"  ERROR: {e}" for e in errors]
    if errors:
        lines.append(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return (not errors, "\n".join(lines))


def _ecus_pids_crosscheck(ecus_path: Path) -> tuple[list[str], list[str]]:
    """Cross-check that every pids/ ECU tx_id is registered in ecus.yaml.

    ecus.yaml is a superset (it lists non-decodable modules with no pids file),
    so registry-only entries are fine. But a pids ECU whose tx_id is missing
    from ecus.yaml is an error (captures for it would fail validation).
    """
    errors: list[str] = []
    warnings: list[str] = []
    try:
        with open(ecus_path) as f:
            edata = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return errors, warnings

    reg: dict[int, dict] = {}
    for key, entry in (edata.get("ecus") or {}).items():
        tx = _parse_tx_key(key)
        if tx is not None and isinstance(entry, dict):
            reg[tx] = entry

    try:
        from canlib.pids import load_pids

        pdata = load_pids()
    except Exception:
        return errors, warnings

    for ecu_name, ecu_def in (pdata.get("ecus") or {}).items():
        if not isinstance(ecu_def, dict):
            continue
        tx = ecu_def.get("tx_id")
        if tx is None:
            continue
        if tx not in reg:
            errors.append(
                f"pids ECU '{ecu_name}' tx_id 0x{tx:03X} is not registered in ecus.yaml"
            )
            continue
        names = {
            str(n).upper() for n in (reg[tx].get("name"), reg[tx].get("alias")) if n
        }
        if str(ecu_name).upper() not in names:
            warnings.append(
                f"pids ECU '{ecu_name}' name differs from ecus.yaml "
                f"name '{reg[tx].get('name')}' for 0x{tx:03X}"
            )

    return errors, warnings


def _run_ecus() -> int:
    from canlib.profile import active

    path = active().ecus_file
    if not path.exists():
        print("No ecus.yaml found in the active profile.")
        return 1

    errors, warnings, stats = validate_ecus_registry(path)
    ce, cw = _ecus_pids_crosscheck(path)
    errors += ce
    warnings += cw

    if warnings:
        for w in warnings:
            print(f"  WARN: {w}")
        print()

    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        return 1

    scan_str = f", {stats['scan_log']} scan-log entries" if stats["scan_log"] else ""
    print(f"OK — {stats['ecus']} ECUs{scan_str}")
    if warnings:
        print(f"  {len(warnings)} warning(s)")
    return 0


# ── captures validation (from validate-captures.py) ───────────────────────


def load_valid_rx_addrs() -> set[int]:
    """Set of valid ECU CAN response addresses (RX = request TX + 8)."""
    from canlib.ecus import build_rx_index

    return set(build_rx_index())


def _path_str(abs_path) -> str:
    """Render a jsonschema error path (deque) as e.g. sessions[0].captures[3].ecu."""
    out = ""
    for part in abs_path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out or "<root>"


def validate_captures_file(
    path: Path, validator: Draft202012Validator, rx_addrs: set[int]
) -> list[str]:
    from canlib.ecus import SENTINELS, parse_ecu_ref

    errors: list[str] = []
    with open(path) as f:
        data = yaml.safe_load(f)

    # Schema validation (structure, types, required/allowed fields, patterns).
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = _path_str(err.absolute_path)
        # Nicer message for known-deprecated fields flagged by additionalProperties.
        dep = DEPRECATED_FIELDS & set(err.instance) if isinstance(err.instance, dict) else set()
        if err.validator == "additionalProperties" and dep:
            errors.append(f"{loc}: deprecated field(s): {sorted(dep)}")
        else:
            errors.append(f"{loc}: {err.message}")

    # Cross-file / cross-field checks not expressible in JSON Schema.
    if isinstance(data, dict):
        for si, session in enumerate(data.get("sessions", []) or []):
            if not isinstance(session, dict):
                continue
            date = session.get("date", "")
            if date:
                # Schema checks the shape; verify the leading date is a real
                # calendar date (a "-<suffix>" for same-day sessions is allowed).
                try:
                    datetime.strptime(str(date)[:10], "%Y-%m-%d")
                except ValueError:
                    errors.append(f"sessions[{si}]: date '{date}' is not a valid calendar date")
                if path.stem != date:
                    errors.append(f"sessions[{si}]: date '{date}' doesn't match filename '{path.stem}'")

            for ci, cap in enumerate(session.get("captures", []) or []):
                if not isinstance(cap, dict):
                    continue
                ecu = cap.get("ecu")
                if ecu and str(ecu).lower() not in SENTINELS:
                    rx = parse_ecu_ref(ecu)
                    if rx is not None and rx not in rx_addrs:
                        errors.append(
                            f"sessions[{si}].captures[{ci}].ecu: response address "
                            f"'{ecu}' not in ecus.yaml (RX = TX + 8)"
                        )

    return errors


def _run_captures() -> int:
    with open(CAPTURES_SCHEMA_FILE) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    rx_addrs = load_valid_rx_addrs()

    from canlib.profile import active

    files = sorted(active().captures_dir.glob("*.yaml"))
    files = [f for f in files if f.name != "SCHEMA.yaml"]

    if not files:
        print("No capture files found.")
        return 0

    # State vocabulary for soft warnings (empty when no states.yaml → no warnings).
    from canlib.states import state_names

    vocab = set(state_names())

    total_errors = 0
    total_warnings = 0
    for path in files:
        errors = validate_captures_file(path, validator, rx_addrs)
        warnings = _capture_state_warnings(path, vocab) if vocab else []
        if errors:
            print(f"\n{path.name}: {len(errors)} errors")
            for e in errors:
                print(f"  - {e}")
            total_errors += len(errors)
        else:
            print(f"{path.name}: OK")
        for w in warnings:
            print(f"  ⚠ {w}")
        total_warnings += len(warnings)

    if total_warnings:
        print(f"\n{total_warnings} state warning(s) — see `canair validate states` / states.yaml")
    if total_errors:
        print(f"\n{total_errors} total errors across {len(files)} files")
        return 1
    else:
        print(f"\nAll {len(files)} files valid.")
        return 0


def _capture_state_warnings(path: Path, vocab: set[str]) -> list[str]:
    """Soft warnings for session states outside the profile's declared vocabulary.

    A capture ``state`` may be a comma-separated list (e.g. "ready, parked");
    each token is checked against the vocabulary. Never an error — free text is
    still accepted, this only nudges toward the standardized state names.
    """
    warnings: list[str] = []
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        state = session.get("state")
        if not state:
            continue
        tokens = [t.strip() for t in str(state).split(",") if t.strip()]
        unknown = [t for t in tokens if t not in vocab]
        if unknown:
            warnings.append(
                f"sessions[{si}]: state token(s) not in states.yaml vocabulary: "
                f"{unknown}"
            )
    return warnings


# ── CLI interface ─────────────────────────────────────────────────────────


def _run_states() -> int:
    """Validate the profile's optional states.yaml (structure + predicates)."""
    from canlib.profile import active
    from canlib.states import StatePredicateError, compile_predicate

    path = active().states_file
    if not path.exists():
        print("No states.yaml (optional) — skipping.")
        return 0

    data = yaml.safe_load(path.read_text()) or {}
    errors: list[str] = []
    if not isinstance(data, dict) or "states" not in data:
        print("states.yaml: missing top-level 'states:' list")
        return 1

    seen: set[str] = set()
    states = data.get("states") or []
    if not isinstance(states, list):
        print("states.yaml: 'states' must be a list")
        return 1

    for i, entry in enumerate(states):
        if not isinstance(entry, dict):
            errors.append(f"states[{i}]: must be a mapping")
            continue
        for extra in set(entry) - {"name", "description", "when"}:
            errors.append(f"states[{i}]: unknown field '{extra}'")
        name = entry.get("name")
        if not name:
            errors.append(f"states[{i}]: missing 'name'")
        elif name in seen:
            errors.append(f"states[{i}]: duplicate state name '{name}'")
        else:
            seen.add(name)
        expr = entry.get("when")
        if expr:
            try:
                compile_predicate(expr)
            except StatePredicateError as ex:
                errors.append(f"states[{i}] ('{name}'): invalid when: {ex}")

    if errors:
        print(f"states.yaml: {len(errors)} errors")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"states.yaml: OK ({len(seen)} states)")
    return 0


def add_parser(subparsers):
    parser = subparsers.add_parser(
        NAME,
        help="Validate pids/ and captures/ against their schemas",
        description="Validate pids/ and captures/ against their schemas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        choices=["pids", "captures", "ecus", "states", "all"],
        default="all",
        help="What to validate (default: all)",
    )
    parser.add_argument(
        "files", nargs="*", help="Specific pids/ files (only with target=pids)"
    )
    parser.add_argument(
        "--stats", action="store_true", help="Show parameter statistics (pids)"
    )
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if args.target == "pids":
        return _run_pids(args.files or None, args.stats)
    if args.target == "captures":
        return _run_captures()
    if args.target == "ecus":
        return _run_ecus()
    if args.target == "states":
        return _run_states()
    # all:
    rc_p = _run_pids(None, args.stats)
    print()
    rc_e = _run_ecus()
    print()
    rc_c = _run_captures()
    print()
    rc_s = _run_states()
    return rc_p or rc_e or rc_c or rc_s
