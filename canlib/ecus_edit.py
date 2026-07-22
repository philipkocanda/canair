"""Surgical, comment-preserving writes to the ECU registry (``ecus.yaml``).

The registry is otherwise hand-authored. These helpers let discovery/identity
flows register new ECUs and fill in metadata without clobbering human edits:

* :func:`register_ecu` — add a new ``ecus:`` entry (keyed by TX id), or merge
  missing fields into an existing one.
* :func:`set_ecu_fields` — update metadata on an existing entry.
* :func:`append_scan_log` — record a probe outcome under ``scan_log:``.

All writes go through :func:`_safe_write`, which re-parses and schema-validates
the file and reverts on failure — a broken edit never persists. Fields are
validated against ``canlib/schema/ecus_schema.yaml`` so typos are rejected up
front. Merges never overwrite existing non-empty values unless ``overwrite=True``.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarint import HexCapsInt

from .yaml_rt import dump as _dump
from .yaml_rt import round_trip_yaml as _yaml

# Order used when rendering a brand-new ECU entry (unknown fields appended last).
CANONICAL_FIELD_ORDER = (
    "name",
    "alias",
    "description",
    "part_number",
    "mfg_date",
    "hw_version",
    "sw_version",
    "hw_sw",
    "boot_sw",
    "app_sw",
    "fw_version",
    "firmware",
    "serial",
    "ecu_id",
    "sw_id",
    "calibration",
    "supplier",
    "diag_address",
    "vin",
    "id_protocol",
    "identity_confidence",
    "notes",
)


class EcusEditError(Exception):
    """Raised when an ecus.yaml edit cannot be applied safely."""


# ── helpers ───────────────────────────────────────────────────────────────


def tx_key(tx_id: int) -> str:
    """Human-readable ``ecus:`` map key for a TX id (e.g. ``0x7E0``).

    Used for display/messages. The registry is keyed by the *integer* TX id
    (see :func:`_hex_key`); YAML round-trips it back to ``0xNNN`` form.
    """
    if not isinstance(tx_id, int) or isinstance(tx_id, bool) or tx_id < 0 or tx_id > 0x7FF:
        raise EcusEditError(f"tx_id must be an int in 0x000-0x7FF, got {tx_id!r}")
    return f"0x{tx_id:03X}"


def _hex_key(tx_id: int) -> HexCapsInt:
    """A hex-rendering integer map key so new entries look like ``0x7E0``."""
    return HexCapsInt(tx_id, width=3)


def _resolve_path(path: Path | None) -> Path:
    if path is None:
        from .profile import active

        return active().ecus_file
    return Path(path)


def _allowed_fields() -> set[str]:
    from .commands.validate import load_ecus_schema

    schema = load_ecus_schema()
    return set(schema.get("required_fields", [])) | set(schema.get("optional_fields", []))


def _check_fields(fields: dict) -> None:
    unknown = set(fields) - _allowed_fields()
    if unknown:
        raise EcusEditError(
            f"unknown ECU field(s): {', '.join(sorted(unknown))}. "
            f"See canlib/schema/ecus_schema.yaml."
        )


def _load_doc(path: Path) -> CommentedMap:
    """Round-trip load ``ecus.yaml`` (or a fresh doc if the file is absent/empty)."""
    y = _yaml()
    data = None
    if path.exists():
        with open(path) as f:
            data = y.load(f)
    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise EcusEditError(f"{path} top-level must be a mapping")
    if data.get("ecus") is None:
        data["ecus"] = CommentedMap()
    return data


def _merge_fields(entry: dict, updates: dict, overwrite: bool) -> bool:
    """Merge ``updates`` into ``entry``; return True if anything changed.

    A value is written when the field is absent, empty, or ``overwrite`` is set.
    ``None`` update values are skipped (nothing to fill in).
    """
    changed = False
    for key, val in updates.items():
        if val is None:
            continue
        cur = entry.get(key)
        if overwrite or cur is None or cur == "":
            if cur != val:
                entry[key] = val
                changed = True
    return changed


def _new_entry(fields: dict) -> CommentedMap:
    entry = CommentedMap()
    for key in CANONICAL_FIELD_ORDER:
        if fields.get(key) is not None:
            entry[key] = fields[key]
    # Any non-canonical (but schema-allowed) fields, appended in call order.
    for key, val in fields.items():
        if key not in entry and val is not None:
            entry[key] = val
    return entry


def _safe_write(path: Path, original: str | None, data) -> None:
    """Write ``data``, then re-parse + schema-validate; revert on failure."""
    with open(path, "w") as f:
        _dump(data, f)
    try:
        from .commands.validate import validate_ecus_file

        ok, msg = validate_ecus_file(path)
        if not ok:
            raise EcusEditError(f"ecus.yaml invalid after edit:\n{msg}")
    except EcusEditError:
        _restore(path, original)
        raise
    except Exception as e:  # pragma: no cover - defensive
        _restore(path, original)
        raise EcusEditError(f"edit failed post-check, reverted: {e}") from e


def _restore(path: Path, original: str | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(original)


# ── public API ──────────────────────────────────────────────────────────────


def register_ecu(
    tx_id: int,
    name: str | None = None,
    *,
    overwrite: bool = False,
    path: Path | None = None,
    **fields,
) -> bool:
    """Register an ECU under ``ecus:`` (keyed by TX id), or merge into existing.

    A new entry defaults its ``name`` to ``Unknown-<TX>`` when none is given.
    Existing entries keep their human-authored fields; only missing/empty ones
    are filled unless ``overwrite=True``. Returns True if the file changed.
    """
    if name is not None:
        fields = {"name": name, **fields}
    _check_fields(fields)

    path = _resolve_path(path)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)
    ecus = data["ecus"]
    disp = tx_key(tx_id)  # validates range + display form

    if tx_id in ecus:
        entry = ecus[tx_id]
        if not isinstance(entry, dict):
            raise EcusEditError(f"ecus[{disp}] is not a mapping")
        changed = _merge_fields(entry, fields, overwrite)
    else:
        if fields.get("name") is None:
            fields["name"] = f"Unknown-{tx_id:03X}"
        ecus[_hex_key(tx_id)] = _new_entry(fields)
        changed = True

    if changed:
        _safe_write(path, original, data)
    return changed


def set_ecu_fields(
    tx_id: int,
    *,
    overwrite: bool = False,
    path: Path | None = None,
    **fields,
) -> bool:
    """Update metadata fields on an existing ECU entry. Returns True if changed.

    Raises :class:`EcusEditError` if the ECU is not registered (use
    :func:`register_ecu` first).
    """
    _check_fields(fields)
    path = _resolve_path(path)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)
    ecus = data["ecus"]
    disp = tx_key(tx_id)

    if tx_id not in ecus:
        raise EcusEditError(f"ECU {disp} not registered; call register_ecu first")
    entry = ecus[tx_id]
    if not isinstance(entry, dict):
        raise EcusEditError(f"ecus[{disp}] is not a mapping")

    changed = _merge_fields(entry, fields, overwrite)
    if changed:
        _safe_write(path, original, data)
    return changed


# scan_log entry fields we accept (mirrors ecus_schema.yaml scan_log_entry_fields).
_SCAN_LOG_FIELDS = ("service", "range", "date", "hits", "probes", "state", "notes")


def append_scan_log(
    tx_id: int,
    *,
    service=None,
    range=None,
    date=None,
    hits=None,
    probes=None,
    state: str | None = None,
    notes: str | None = None,
    path: Path | None = None,
) -> None:
    """Append a probe-outcome entry under ``scan_log.<tx>`` (date defaults to today)."""
    path = _resolve_path(path)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)

    if data.get("scan_log") is None:
        data["scan_log"] = CommentedMap()
    log = data["scan_log"]
    tx_key(tx_id)  # validate range
    if log.get(tx_id) is None:
        log[_hex_key(tx_id)] = []

    values = {
        "service": HexCapsInt(service) if isinstance(service, int) and not isinstance(service, bool) else service,
        "range": range,
        "date": date if date is not None else _date.today().isoformat(),
        "hits": hits,
        "probes": probes,
        "state": state,
        "notes": notes,
    }
    entry = CommentedMap()
    for field in _SCAN_LOG_FIELDS:
        if values[field] is not None:
            entry[field] = values[field]
    log[tx_id].append(entry)

    _safe_write(path, original, data)
