"""Surgical, validated editor for the broadcast signal-definition sidecar.

The domain-B analogue of :mod:`canlib.pids_edit`: safely create/update/remove
entries in ``signals/<bus>.yaml`` (arbitration ID → named linear signals), with
the same snapshot → write → re-parse → validate → rollback discipline, so a bad
edit never persists. Prefer this (and the ``canair signals`` command) over
hand-editing the sidecar.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .validation.signals import (
    BYTE_ORDERS,
    MAX_MESSAGE_LENGTH,
    MESSAGE_FIELDS,
    SIGNAL_FIELDS,
    check_signals_doc,
)

if TYPE_CHECKING:
    from .profile import Profile


class SignalsEditError(Exception):
    """A signals/ edit was rejected (invalid input or failed post-validation)."""


def _bus_path(profile: Profile, bus: str) -> Path:
    return profile.signals_dir / f"{bus}.yaml"


def _normalize_id(arb_id: str | int) -> str:
    """Canonical ``0xHEX`` arbitration-ID key."""
    val = arb_id if isinstance(arb_id, int) else int(str(arb_id), 16)
    return f"0x{val:X}"


def _ordered_signal(fields: dict) -> dict:
    from ruamel.yaml.comments import CommentedMap

    out = CommentedMap()
    for k in SIGNAL_FIELDS:
        if k in fields and fields[k] is not None:
            out[k] = fields[k]
    return out


def _set_message_field(msg, key: str, value) -> None:
    """Set ``msg[key]``, inserting a new key at its canonical position.

    A plain assignment appends, so a ``name`` or ``length`` added to a message that
    already has ``signals`` would otherwise land after the whole signal block.
    """
    if key in msg:
        msg[key] = value
        return
    rank = MESSAGE_FIELDS.index(key)
    pos = sum(1 for k in msg if k in MESSAGE_FIELDS and MESSAGE_FIELDS.index(k) < rank)
    msg.insert(pos, key, value)


def upsert_signal(
    bus: str,
    arb_id: str | int,
    name: str,
    *,
    start_bit: int,
    length: int,
    byte_order: str = "little",
    signed: bool | None = None,
    scale: float | None = None,
    offset: float | None = None,
    min: float | None = None,
    max: float | None = None,
    unit: str | None = None,
    verified: bool | None = None,
    source: str | None = None,
    notes: str | None = None,
    msg_name: str | None = None,
    tx_ecu: str | None = None,
    msg_length: int | None = None,
    profile: Profile | None = None,
) -> Path:
    """Add or update one broadcast signal in ``signals/<bus>.yaml`` (validated).

    Creates the file / message entry as needed. Comment-preserving; the write is
    re-parsed and structurally validated, and rolled back on failure.
    """
    from ruamel.yaml.comments import CommentedMap

    from .profile import active
    from .yaml_rt import round_trip_yaml as _yaml

    if byte_order not in BYTE_ORDERS:
        raise SignalsEditError(f"byte_order must be little|big (got {byte_order!r})")
    if length < 1:
        raise SignalsEditError("length must be >= 1")
    if start_bit < 0:
        raise SignalsEditError("start_bit must be >= 0")
    if msg_length is not None and not 0 <= msg_length <= MAX_MESSAGE_LENGTH:
        raise SignalsEditError(f"message length must be 0-{MAX_MESSAGE_LENGTH} bytes")

    prof = profile or active()
    path = _bus_path(prof, bus)
    mid = _normalize_id(arb_id)

    original = path.read_text() if path.exists() else None
    data = _yaml().load(original) if original else CommentedMap()
    if not isinstance(data, dict):
        raise SignalsEditError(f"{path.name}: top level is not a mapping")
    messages = data.setdefault("messages", CommentedMap())
    msg = messages.setdefault(mid, CommentedMap())
    if msg_name and not msg.get("name"):
        _set_message_field(msg, "name", msg_name)
    if tx_ecu and not msg.get("tx_ecu"):
        _set_message_field(msg, "tx_ecu", tx_ecu)
    if msg_length is not None:
        _set_message_field(msg, "length", msg_length)
    signals = msg.setdefault("signals", CommentedMap())
    signals[name] = _ordered_signal(
        {
            "start_bit": start_bit,
            "length": length,
            "byte_order": byte_order,
            "signed": signed,
            "scale": scale,
            "offset": offset,
            "min": min,
            "max": max,
            "unit": unit,
            "verified": verified,
            "source": source,
            "notes": notes,
        }
    )

    _safe_write(path, original, data, check_signals_doc)
    return path


def merge_bus(
    bus: str,
    imported: dict,
    *,
    profile: Profile | None = None,
) -> tuple[Path, int]:
    """Merge a batch of messages/signals into ``signals/<bus>.yaml`` in one write.

    ``imported`` maps arbitration ID → ``{"name", "tx_ecu", "length", "signals": {name:
    fields}}`` (as produced by DBC import). Existing entries are overwritten;
    others are preserved. One comment-preserving write + one validate + rollback.
    Returns ``(path, signal_count)``.
    """
    from ruamel.yaml.comments import CommentedMap

    from .profile import active
    from .yaml_rt import round_trip_yaml as _yaml

    prof = profile or active()
    path = _bus_path(prof, bus)
    original = path.read_text() if path.exists() else None
    data = _yaml().load(original) if original else CommentedMap()
    if not isinstance(data, dict):
        raise SignalsEditError(f"{path.name}: top level is not a mapping")
    messages = data.setdefault("messages", CommentedMap())

    n = 0
    for arb_id, spec in imported.items():
        mid = _normalize_id(arb_id)
        msg = messages.setdefault(mid, CommentedMap())
        for key in ("name", "tx_ecu"):
            if spec.get(key):
                _set_message_field(msg, key, spec[key])
        if spec.get("length") is not None:
            _set_message_field(msg, "length", spec["length"])
        sigs = msg.setdefault("signals", CommentedMap())
        for sname, fields in (spec.get("signals") or {}).items():
            sigs[sname] = _ordered_signal(fields)
            n += 1

    _safe_write(path, original, data, check_signals_doc)
    return path, n


def remove_signal(
    bus: str, arb_id: str | int, name: str, *, profile: Profile | None = None
) -> Path:
    """Remove a signal (and any now-empty message) from ``signals/<bus>.yaml``."""
    from .profile import active
    from .yaml_rt import round_trip_yaml as _yaml

    prof = profile or active()
    path = _bus_path(prof, bus)
    if not path.exists():
        raise SignalsEditError(f"no signals file for bus {bus!r}")
    mid = _normalize_id(arb_id)
    original = path.read_text()
    data = _yaml().load(original) or {}
    msg = (data.get("messages") or {}).get(mid) or {}
    sigs = msg.get("signals") or {}
    if name not in sigs:
        raise SignalsEditError(f"{mid}/{name} not found in {path.name}")
    del sigs[name]
    if not sigs:  # drop the now-signal-less message
        del data["messages"][mid]
    if not data.get("messages"):  # no messages left → remove the empty file
        path.unlink()
        return path
    _safe_write(path, original, data, check_signals_doc)
    return path


def _safe_write(path: Path, original: str | None, data, checker) -> None:
    """Write ``data`` (comment-preserving), re-parse, check, and roll back on error."""
    import yaml as _pyyaml

    from .yaml_rt import dump as _dump

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        _dump(data, f)
    try:
        reparsed = _pyyaml.safe_load(path.read_text()) or {}
        errors, _ = checker(reparsed)
        if errors:
            raise SignalsEditError("; ".join(errors[:5]))
    except SignalsEditError:
        _restore(path, original)
        raise
    except Exception as e:  # pragma: no cover - defensive
        _restore(path, original)
        raise SignalsEditError(f"edit failed post-check, reverted: {e}") from e


def _restore(path: Path, original: str | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(original)
