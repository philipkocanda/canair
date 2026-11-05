"""Structural checks for a profile's broadcast signal maps (``signals/<bus>.yaml``).

``canair validate signals`` reports these and :mod:`canair.yaml_edit.signals` uses them
as its rollback guard, so both enforce the same rules. ``src/canair/schema/signals_schema.yaml``
is the structural check; this module adds the frame-layout rules it cannot state.

Also home to the pure frame-layout rules (byte span, legal data lengths, frame
format defaults) that the editor, the DBC importer and the DBC exporter share, so a
definition the validator accepts is one the exporter can write.
"""

from __future__ import annotations

import re

from canair.core.json_schema import schema_errors
from canair.core.paths import SIGNALS_SCHEMA_FILE

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
BYTE_ORDERS = ("little", "big")

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


def _signal_span(sig: dict) -> int | None:
    sb, ln, bo = sig.get("start_bit"), sig.get("length"), sig.get("byte_order", "little")
    if not (isinstance(sb, int) and isinstance(ln, int)) or isinstance(sb, bool):
        return None
    if isinstance(ln, bool) or sb < 0 or ln < 1 or bo not in BYTE_ORDERS:
        return None
    return signal_byte_span(sb, ln, bo)


def check_signals_doc(data: object) -> tuple[list[str], int]:
    """Check one parsed ``signals/<bus>.yaml`` doc.

    ``signals_schema.yaml`` checks the structure; the frame-layout rules below check what it
    cannot. Returns ``(errors, signal_count)``.
    """
    errors = schema_errors(SIGNALS_SCHEMA_FILE, data)
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, dict):
        return (errors, 0)
    n_signals = 0
    for mid, msg in messages.items():
        if not isinstance(msg, dict):
            continue
        errors.extend(f"messages.{mid}: {e}" for e in _layout_errors(mid, msg))
        signals = msg.get("signals")
        n_signals += len(signals) if isinstance(signals, dict) else 0
    return (errors, n_signals)


def _layout_errors(mid: object, msg: dict) -> list[str]:
    """The frame-layout rules: the id width, the classic-frame size and the signal spans."""
    errors: list[str] = []
    if _ARB_ID_RE.match(str(mid)):
        frame_id = int(str(mid), 16)
        if msg.get("extended") is False and frame_id > MAX_STANDARD_ID:
            errors.append(f"a standard (11-bit) frame id must be <= 0x{MAX_STANDARD_ID:X}")

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
            return errors  # the schema reports it
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
