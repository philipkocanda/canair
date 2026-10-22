"""Surgical, validated editor for the broadcast signal-definition sidecar.

The domain-B analogue of :mod:`canlib.pids_edit`: safely create/update/remove
entries in ``signals/<bus>.yaml`` (arbitration ID → named linear signals), with
the same snapshot → write → re-parse → validate → rollback discipline, so a bad
edit never persists. Prefer this (and the ``canair signals`` command) over
hand-editing the sidecar.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from .validation.signals import (
    BYTE_ORDERS,
    CLASSIC_MAX_LENGTH,
    MAX_EXTENDED_ID,
    MAX_STANDARD_ID,
    MESSAGE_FIELDS,
    MESSAGE_LENGTH_RULE,
    SIGNAL_FIELDS,
    check_signals_doc,
    default_extended,
    is_message_length,
    message_is_extended,
    message_length,
)

if TYPE_CHECKING:
    from .profile import Profile


class SignalsEditError(Exception):
    """A signals/ edit was rejected (invalid input or failed post-validation)."""


# Optional signal fields ``upsert_signal(clear=...)`` may remove. Text fields are also
# cleared by an empty string, as an empty unit/source/note means "none".
CLEARABLE_SIGNAL_FIELDS = ("scale", "offset", "min", "max", "unit", "source", "notes")
_TEXT_FIELDS = ("unit", "source", "notes")


def _bus_path(profile: Profile, bus: str) -> Path:
    return profile.signals_dir / f"{bus}.yaml"


def _normalize_id(arb_id: str | int) -> str:
    """Canonical ``0xHEX`` arbitration-ID key (rejects a non-hex or out-of-range ID)."""
    try:
        val = arb_id if isinstance(arb_id, int) else int(str(arb_id), 16)
    except ValueError:
        raise SignalsEditError(f"arbitration ID {arb_id!r} is not hex (e.g. 0x220)") from None
    if not 0 <= val <= MAX_EXTENDED_ID:
        raise SignalsEditError(
            f"arbitration ID {arb_id!r} is outside 0x0-0x{MAX_EXTENDED_ID:X} (29-bit maximum)"
        )
    return f"0x{val:X}"


def _ordered_signal(fields: dict) -> dict:
    from ruamel.yaml.comments import CommentedMap

    out = CommentedMap()
    for k in SIGNAL_FIELDS:
        if k in fields and fields[k] is not None:
            out[k] = fields[k]
    return out


def _set_field(mapping, key: str, value, order: tuple[str, ...]) -> None:
    """Set ``mapping[key]``, inserting a new key at its canonical position in ``order``.

    A plain assignment appends, so a ``name`` or ``length`` added to a message that
    already has ``signals`` would otherwise land after the whole signal block.
    """
    if key in mapping:
        mapping[key] = value
        return
    rank = order.index(key)
    pos = sum(1 for k in mapping if k in order and order.index(k) < rank)
    mapping.insert(pos, key, value)


def _set_message_field(msg, key: str, value) -> None:
    _set_field(msg, key, value, MESSAGE_FIELDS)


def _canonical_frame_format(frame_id: int, msg) -> None:
    """Drop ``extended``/``fd`` flags that only restate what the ID and length imply.

    A flag is stored only when it carries information, so one fact has one spelling
    whichever editor wrote it. A *contradicting* flag (``extended: false`` on a 29-bit
    ID, ``fd: false`` on a frame longer than 8 bytes) is kept for the validator.
    """
    if "extended" in msg and msg["extended"] == default_extended(frame_id):
        del msg["extended"]
    ln = message_length(msg)
    if "fd" in msg and ln is not None:
        if (msg["fd"] is False and ln <= CLASSIC_MAX_LENGTH) or (
            msg["fd"] is True and "length" in msg and ln > CLASSIC_MAX_LENGTH
        ):
            del msg["fd"]


def upsert_signal(
    bus: str,
    arb_id: str | int,
    name: str,
    *,
    start_bit: int | None = None,
    length: int | None = None,
    byte_order: str | None = None,
    signed: bool | None = None,
    is_float: bool | None = None,
    scale: float | None = None,
    offset: float | None = None,
    min: float | None = None,
    max: float | None = None,
    unit: str | None = None,
    verified: bool | None = None,
    source: str | None = None,
    notes: str | None = None,
    clear: Iterable[str] = (),
    msg_name: str | None = None,
    tx_ecu: str | None = None,
    msg_length: int | None = None,
    extended: bool | None = None,
    fd: bool | None = None,
    profile: Profile | None = None,
) -> Path:
    """Add or update one broadcast signal in ``signals/<bus>.yaml`` (validated).

    Creates the file / message entry as needed; a new signal needs ``start_bit`` and
    ``length`` (``byte_order`` defaults to little). On an existing signal only the
    arguments given change: ``None`` keeps the stored value. ``signed=False`` and
    ``is_float=False`` remove the flag (unsigned integer is the default), an empty
    ``unit``/``source``/``notes`` removes that field, and ``clear`` removes any of
    :data:`CLEARABLE_SIGNAL_FIELDS`. Message fields given (``msg_name``, ``tx_ecu``,
    ``msg_length``, ``extended``, ``fd``) overwrite the message's. Comment-preserving;
    the write is re-parsed and structurally validated, and rolled back on failure.
    """
    from ruamel.yaml.comments import CommentedMap

    from .profile import active
    from .yaml_rt import round_trip_yaml as _yaml

    clear = set(clear)
    if unknown := clear - set(CLEARABLE_SIGNAL_FIELDS):
        raise SignalsEditError(
            f"cannot clear {', '.join(sorted(unknown))} "
            f"(clearable: {', '.join(CLEARABLE_SIGNAL_FIELDS)})"
        )
    given = {
        "start_bit": start_bit,
        "length": length,
        "byte_order": byte_order,
        "signed": signed,
        "float": is_float,
        "scale": scale,
        "offset": offset,
        "min": min,
        "max": max,
        "unit": unit,
        "verified": verified,
        "source": source,
        "notes": notes,
    }
    updates = {k: v for k, v in given.items() if v is not None}
    if both := sorted(clear & set(updates)):
        raise SignalsEditError(f"{', '.join(both)}: both set and cleared")
    for key in ("signed", "float"):
        if updates.get(key) is False:
            del updates[key]
            clear.add(key)
    for key in _TEXT_FIELDS:
        if updates.get(key) == "":
            del updates[key]
            clear.add(key)

    if byte_order is not None and byte_order not in BYTE_ORDERS:
        raise SignalsEditError(f"byte_order must be little|big (got {byte_order!r})")
    if length is not None and length < 1:
        raise SignalsEditError("length must be >= 1")
    if start_bit is not None and start_bit < 0:
        raise SignalsEditError("start_bit must be >= 0")
    if msg_length is not None and not is_message_length(msg_length):
        raise SignalsEditError(f"message length must be {MESSAGE_LENGTH_RULE} bytes")

    prof = profile or active()
    path = _bus_path(prof, bus)
    mid = _normalize_id(arb_id)
    frame_id = int(mid, 16)
    if extended is False and frame_id > MAX_STANDARD_ID:
        raise SignalsEditError(
            f"{mid} cannot be a standard frame (11-bit IDs end at 0x{MAX_STANDARD_ID:X})"
        )

    original = path.read_text() if path.exists() else None
    data = _yaml().load(original) if original else CommentedMap()
    if not isinstance(data, dict):
        raise SignalsEditError(f"{path.name}: top level is not a mapping")
    messages = data.setdefault("messages", CommentedMap())
    msg = messages.setdefault(mid, CommentedMap())
    for key, value in (
        ("name", msg_name),
        ("tx_ecu", tx_ecu),
        ("length", msg_length),
        ("extended", extended),
        ("fd", fd),
    ):
        if value is not None:
            _set_message_field(msg, key, value)
    signals = msg.setdefault("signals", CommentedMap())

    sig = signals.get(name)
    if sig is None:
        missing = [k for k in ("start_bit", "length") if k not in updates]
        if missing:
            raise SignalsEditError(
                f"{mid}/{name} is a new signal: {' and '.join(missing)} required"
            )
        signals[name] = _ordered_signal({"byte_order": "little", **updates})
    else:
        for key in clear:
            sig.pop(key, None)
        for key, value in updates.items():
            _set_field(sig, key, value, SIGNAL_FIELDS)
    _canonical_frame_format(frame_id, msg)

    _safe_write(path, original, data, check_signals_doc)
    return path


def merge_bus(
    bus: str,
    imported: dict,
    *,
    profile: Profile | None = None,
) -> tuple[Path, int]:
    """Merge a batch of messages/signals into ``signals/<bus>.yaml`` in one write.

    ``imported`` maps arbitration ID → ``{"name", "tx_ecu", "length", "extended", "fd",
    "signals": {name: fields}}`` (as produced by DBC import). Imported messages and
    signals overwrite the existing ones of the same ID and name; others are preserved.
    The frame format is the import's: an absent ``fd`` clears a stored one. Refused when
    an imported message's frame format (standard/extended) differs from the stored
    message under the same ID — those are two different frames sharing one key. One
    comment-preserving write + one validate + rollback. Returns ``(path, signal_count)``.
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
        frame_id = int(mid, 16)
        if mid in messages and isinstance(messages[mid], dict):
            have = message_is_extended(frame_id, messages[mid])
            want = message_is_extended(frame_id, spec)
            if have != want:
                kinds = {True: "an extended", False: "a standard"}
                raise SignalsEditError(
                    f"{mid} is {kinds[have]} frame in {path.name} but {kinds[want]} one in "
                    "the import; import it into another bus (--bus)"
                )
        msg = messages.setdefault(mid, CommentedMap())
        for key in ("name", "tx_ecu"):
            if spec.get(key):
                _set_message_field(msg, key, spec[key])
        for key in ("length", "extended"):
            if spec.get(key) is not None:
                _set_message_field(msg, key, spec[key])
        if spec.get("fd") is not None:
            _set_message_field(msg, "fd", spec["fd"])
        else:
            msg.pop("fd", None)
        sigs = msg.setdefault("signals", CommentedMap())
        for sname, fields in (spec.get("signals") or {}).items():
            sigs[sname] = _ordered_signal(fields)
            n += 1
        _canonical_frame_format(frame_id, msg)

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
