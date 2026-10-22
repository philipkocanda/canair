"""Structural checks for a profile's broadcast signal maps (``signals/<bus>.yaml``).

``canair validate signals`` reports these and :mod:`canlib.signals_edit` uses them
as its rollback guard, so both enforce the same rules. The
``canlib/schema/signals_schema.yaml`` companion documents the same fields for humans.

Also home to the pure frame-layout rules (byte span, legal data lengths, frame
format defaults) that the editor, the DBC importer and the DBC exporter share, so a
definition the validator accepts is one the exporter can write.
"""

from __future__ import annotations

import re

# Every field a signal may carry, in canonical render order. Shared by the
# validator and the editor so a field accepted by one is never dropped or rejected
# by the other.
SIGNAL_FIELDS = (
    "start_bit",
    "length",
    "byte_order",
    "signed",
    "float",
    "scale",
    "offset",
    "min",
    "max",
    "unit",
    "type",
    "values",
    "bits",
    "verified",
    "source",
    "notes",
)
MESSAGE_FIELDS = ("name", "tx_ecu", "length", "extended", "fd", "signals")
# A message's declared data length in bytes (DBC BO_ DLC): 0-8 on classic CAN; above
# that only the CAN FD data-length-code sizes exist.
CLASSIC_MAX_LENGTH = 8
FD_LENGTHS = (12, 16, 20, 24, 32, 48, 64)
MAX_MESSAGE_LENGTH = FD_LENGTHS[-1]
MESSAGE_LENGTH_RULE = "0-8 bytes, or a CAN FD size (12, 16, 20, 24, 32, 48 or 64)"
MAX_STANDARD_ID = 0x7FF
MAX_EXTENDED_ID = 0x1FFFFFFF
# An IEEE 754 single or double (DBC SIG_VALTYPE_ 1 / 2).
FLOAT_LENGTHS = (32, 64)
BYTE_ORDERS = ("little", "big")
SIGNAL_TYPES = ("numeric", "enum", "bitmask", "ascii", "date", "bcd")

_ARB_ID_RE = re.compile(r"^0x[0-9A-Fa-f]+$")


def signal_byte_span(start_bit: int, length: int, byte_order: str = "little") -> int:
    """How many data bytes a signal occupies, counted from byte 0.

    ``start_bit`` uses DBC numbering: the LSB for little-endian, but the *MSB* for
    big-endian, whose bits then run down each byte and on into the next one (bit 7 of
    byte 0 is 7, so a 16-bit field in bytes 0-1 is ``7|16@0``). ``start_bit + length``
    is therefore only the extent of a little-endian field.
    """
    if byte_order == "big":
        last = (start_bit // 8) * 8 + (7 - start_bit % 8) + length - 1
    else:
        last = start_bit + length - 1
    return last // 8 + 1


def is_message_length(n: object) -> bool:
    """True for a data length a CAN (FD) frame can actually carry."""
    if isinstance(n, bool) or not isinstance(n, int):
        return False
    return 0 <= n <= CLASSIC_MAX_LENGTH or n in FD_LENGTHS


def frame_length_for(span: int) -> int:
    """The smallest legal data length holding ``span`` bytes (at least 1).

    A span past 64 bytes has no legal length; it is returned as-is for the caller's
    validation to reject.
    """
    span = max(1, span)
    if span <= CLASSIC_MAX_LENGTH:
        return span
    return next((n for n in FD_LENGTHS if n >= span), span)


def default_extended(frame_id: int) -> bool:
    """The frame format an ID implies when a message does not say: 29-bit above 0x7FF."""
    return frame_id > MAX_STANDARD_ID


def message_is_extended(frame_id: int, msg: dict) -> bool:
    """Whether a message is an extended (29-bit) frame — its ``extended`` flag, else the ID."""
    flag = msg.get("extended")
    return default_extended(frame_id) if flag is None else bool(flag)


def message_is_fd(msg: dict, length: int) -> bool:
    """Whether a message is a CAN FD frame — its ``fd`` flag, or a length only FD carries."""
    return bool(msg.get("fd")) or length > CLASSIC_MAX_LENGTH


def message_length(msg: dict) -> int | None:
    """A message's data length: the declared one, else the smallest holding its signals.

    ``None`` when the declared length is not an integer (the validator reports it).
    """
    if "length" in msg:
        ln = msg["length"]
        return ln if _is_int(ln) else None
    signals = msg.get("signals")
    spans = [
        span
        for sig in (signals.values() if isinstance(signals, dict) else ())
        if isinstance(sig, dict) and (span := _signal_span(sig)) is not None
    ]
    return frame_length_for(max(spans, default=0))


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int_key(k: object) -> int | None:
    """An enum/bitmask map key as an int — an int, or a quoted one (as the decoder reads)."""
    if isinstance(k, int) and not isinstance(k, bool):
        return k
    if isinstance(k, str):
        try:
            return int(k)
        except ValueError:
            return None
    return None


def _signal_span(sig: dict) -> int | None:
    sb, ln, bo = sig.get("start_bit"), sig.get("length"), sig.get("byte_order", "little")
    if not (isinstance(sb, int) and isinstance(ln, int)) or isinstance(sb, bool):
        return None
    if isinstance(ln, bool) or sb < 0 or ln < 1 or bo not in BYTE_ORDERS:
        return None
    return signal_byte_span(sb, ln, bo)


def check_signals_doc(data: object) -> tuple[list[str], int]:
    """Structural check of one parsed ``signals/<bus>.yaml`` doc.

    Returns ``(errors, signal_count)``.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return (["top level must be a mapping"], 0)
    for extra in set(data) - {"bus", "bitrate", "messages"}:
        errors.append(f"unknown top-level field '{extra}'")
    if "bitrate" in data and not _is_int(data.get("bitrate")):
        errors.append("'bitrate' must be an integer")
    messages = data.get("messages") or {}
    if not isinstance(messages, dict):
        return ([*errors, "'messages' must be a mapping keyed by arbitration ID"], 0)
    n_signals = 0
    for mid, msg in messages.items():
        if not isinstance(msg, dict):
            errors.append(f"message '{mid}': must be a mapping")
            continue
        errors.extend(f"message '{mid}': {e}" for e in _message_errors(mid, msg))
        signals = msg.get("signals") or {}
        if not isinstance(signals, dict):
            errors.append(f"message '{mid}': 'signals' must be a mapping")
            continue
        for sname, sig in signals.items():
            n_signals += 1
            if not isinstance(sig, dict):
                errors.append(f"{mid}/{sname}: must be a mapping")
                continue
            errors.extend(f"{mid}/{sname}: {e}" for e in _signal_errors(sig))
    return (errors, n_signals)


def _message_errors(mid: object, msg: dict) -> list[str]:
    errors = [f"unknown field '{extra}'" for extra in set(msg) - set(MESSAGE_FIELDS)]
    frame_id: int | None = None
    if not _ARB_ID_RE.match(str(mid)):
        errors.append("id is not a hex arbitration ID (e.g. 0x220)")
    else:
        frame_id = int(str(mid), 16)
        if frame_id > MAX_EXTENDED_ID:
            errors.append(f"id is above the 29-bit maximum 0x{MAX_EXTENDED_ID:X}")
    for flag in ("extended", "fd"):
        if flag in msg and not isinstance(msg[flag], bool):
            errors.append(f"{flag} must be true|false (got {msg[flag]!r})")
    if msg.get("extended") is False and frame_id is not None and frame_id > MAX_STANDARD_ID:
        errors.append(f"a standard (11-bit) frame id must be <= 0x{MAX_STANDARD_ID:X}")
    for key in ("name", "tx_ecu"):
        if key in msg and not isinstance(msg[key], str):
            errors.append(f"{key} must be a string (got {msg[key]!r})")

    signals = msg.get("signals")
    spans = (
        {
            name: span
            for name, sig in signals.items()
            if isinstance(sig, dict) and (span := _signal_span(sig)) is not None
        }
        if isinstance(signals, dict)
        else {}
    )
    if "length" in msg:
        ln = msg["length"]
        if not is_message_length(ln):
            errors.append(f"length must be {MESSAGE_LENGTH_RULE} (got {ln!r})")
            return errors
        limit, what = ln, f"length {ln} is"
    else:
        # No declared length: `export dbc` writes the smallest one holding the signals.
        ln = frame_length_for(max(spans.values(), default=0))
        limit, what = MAX_MESSAGE_LENGTH, f"a frame's {MAX_MESSAGE_LENGTH} bytes are"
    if msg.get("fd") is False and ln > CLASSIC_MAX_LENGTH:
        errors.append(f"a classic (non-FD) frame carries at most {CLASSIC_MAX_LENGTH} bytes")
    over = sorted((n for n, s in spans.items() if s > limit), key=str)
    if over:
        need = max(spans[n] for n in over)
        errors.append(
            f"{what} shorter than its signals need ({need} bytes for "
            f"{', '.join(map(str, over[:3]))}{', …' if len(over) > 3 else ''})"
        )
    return errors


def _signal_errors(sig: dict) -> list[str]:
    errors = [f"unknown field '{extra}'" for extra in set(sig) - set(SIGNAL_FIELDS)]
    for req in ("start_bit", "length"):
        if req not in sig:
            errors.append(f"missing required '{req}'")
    if "start_bit" in sig:
        sb = sig["start_bit"]
        if not _is_int(sb):
            errors.append(f"start_bit must be an integer (got {sb!r})")
        elif sb < 0:
            errors.append("start_bit must be >= 0")
    if "length" in sig:
        ln = sig["length"]
        if not _is_int(ln):
            errors.append(f"length must be an integer (got {ln!r})")
        elif ln < 1:
            errors.append("length must be >= 1")
        elif sig.get("float") is True and ln not in FLOAT_LENGTHS:
            errors.append(f"a float signal must be 32 or 64 bits long (got {ln})")
    bo = sig.get("byte_order")
    if "byte_order" in sig and bo not in BYTE_ORDERS:
        errors.append(f"byte_order must be little|big (got {bo!r})")
    for flag in ("signed", "float", "verified"):
        if flag in sig and not isinstance(sig[flag], bool):
            errors.append(f"{flag} must be true|false (got {sig[flag]!r})")
    for key in ("scale", "offset", "min", "max"):
        if key in sig and not _is_number(sig[key]):
            errors.append(f"{key} must be a number (got {sig[key]!r})")
    if _is_number(sig.get("scale")) and sig["scale"] == 0:
        errors.append("scale must not be 0 (every raw value would read the same)")
    for key in ("unit", "source", "notes"):
        if key in sig and not isinstance(sig[key], str):
            errors.append(f"{key} must be a string (got {sig[key]!r})")
    errors.extend(_typed_errors(sig))
    return errors


def _typed_errors(sig: dict) -> list[str]:
    """The typed-decoding companions, held to the same rules as a diagnostic PID param."""
    errors: list[str] = []
    st = sig.get("type")
    if "type" in sig and st not in SIGNAL_TYPES:
        errors.append(f"type must be one of {'|'.join(SIGNAL_TYPES)} (got {st!r})")
    for key, owner, what in (("values", "enum", "raw_int"), ("bits", "bitmask", "bit_index")):
        if key not in sig:
            if st == owner:
                errors.append(f"type '{owner}' requires a '{key}:' map")
            continue
        if st != owner:
            errors.append(f"'{key}' is only valid with type '{owner}'")
        mapping = sig[key]
        if not isinstance(mapping, dict):
            errors.append(f"'{key}' must be a mapping of {what} -> label")
            continue
        for k, label in mapping.items():
            index = _int_key(k)
            if index is None:
                errors.append(f"'{key}' key {k!r} must be an integer")
            elif key == "bits" and not 0 <= index <= 63:
                errors.append(f"'bits' index {index} out of range (0-63)")
            # YAML reads a bare on/off/yes/no label as a boolean, so insist on text.
            if not isinstance(label, str):
                errors.append(f"'{key}' label for {k!r} must be a string (got {label!r})")
    return errors
