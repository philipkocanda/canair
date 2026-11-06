"""Surgical, validated editor for the broadcast signal-definition sidecar.

The domain-B analogue of :mod:`canair.yaml_edit.pids`: safely create/update/remove
entries in ``signals/<bus>.yaml`` (arbitration ID → named linear signals), with
the same snapshot → write → re-parse → validate → rollback discipline, so a bad
edit never persists. Prefer this (and the ``canair signals`` command) over
hand-editing the sidecar.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml.comments import CommentedMap

from canair.core.edit_errors import EditError
from canair.core.paths import schema_modeline
from canair.validation.signals import (
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

from .doc import Target, edit_doc
from .ruamel_ops import place_in_order, remove_key

if TYPE_CHECKING:
    from canair.profile.model import Profile


class SignalsEditError(EditError):
    """A signals/ edit was rejected (invalid input or failed post-validation)."""


# Optional signal fields ``upsert_signal(clear=...)`` may remove. Text fields are also
# cleared by an empty string, as an empty unit/source/note means "none".
CLEARABLE_SIGNAL_FIELDS = ("scale", "offset", "min", "max", "unit", "source", "notes")
_TEXT_FIELDS = ("unit", "source", "notes")


def _check(data: object) -> None:
    errors, _ = check_signals_doc(data or {})
    if errors:
        raise SignalsEditError("; ".join(errors[:5]))


SIGNALS = Target(
    key="signals",
    path=lambda profile, bus: profile.signals_dir / f"{bus}.yaml",
    error=SignalsEditError,
    section="messages",
    check=_check,
    header=schema_modeline("signals") + "\n",
    sequence=(2, 0),
)


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
    out = CommentedMap()
    for k in SIGNAL_FIELDS:
        if k in fields and fields[k] is not None:
            out[k] = fields[k]
    return out


def _set_message_field(msg, key: str, value) -> None:
    place_in_order(msg, key, value, MESSAGE_FIELDS)


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
    profile: Profile,
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

    mid = _normalize_id(arb_id)
    frame_id = int(mid, 16)
    if extended is False and frame_id > MAX_STANDARD_ID:
        raise SignalsEditError(
            f"{mid} cannot be a standard frame (11-bit IDs end at 0x{MAX_STANDARD_ID:X})"
        )

    with edit_doc(SIGNALS, profile, create=True, bus=bus) as doc:
        msg = doc.section.setdefault(mid, CommentedMap())
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
                place_in_order(sig, key, value, SIGNAL_FIELDS)
        _canonical_frame_format(frame_id, msg)
    return doc.path


def merge_bus(
    bus: str,
    imported: dict,
    *,
    profile: Profile,
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
    n = 0
    with edit_doc(SIGNALS, profile, create=True, bus=bus) as doc:
        messages = doc.section
        for arb_id, spec in imported.items():
            mid = _normalize_id(arb_id)
            frame_id = int(mid, 16)
            if mid in messages and isinstance(messages[mid], dict):
                have = message_is_extended(frame_id, messages[mid])
                want = message_is_extended(frame_id, spec)
                if have != want:
                    kinds = {True: "an extended", False: "a standard"}
                    raise SignalsEditError(
                        f"{mid} is {kinds[have]} frame in {doc.path.name} but {kinds[want]} one "
                        "in the import; import it into another bus (--bus)"
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
    return doc.path, n


def remove_signal(bus: str, arb_id: str | int, name: str, *, profile: Profile) -> Path:
    """Remove a signal (and any now-empty message) from ``signals/<bus>.yaml``."""
    mid = _normalize_id(arb_id)
    with edit_doc(SIGNALS, profile, bus=bus, missing=f"no signals file for bus {bus!r}") as doc:
        sigs = (doc.section.get(mid) or {}).get("signals") or {}
        if name not in sigs:
            raise SignalsEditError(f"{mid}/{name} not found in {doc.path.name}")
        remove_key(sigs, name)
        if not sigs:  # drop the now-signal-less message
            remove_key(doc.section, mid, parent=doc.data, parent_key="messages")
        doc.delete_file = not doc.section  # no messages left → remove the empty file
    return doc.path
