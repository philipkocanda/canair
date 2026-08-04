"""Typed (multi-modal) value decoding — the parallel layer to the pure-float
``expression`` evaluator.

The WiCAN ``expression`` is, and stays, a scalar ``float`` (see
``canlib/expression.py`` — a faithful firmware port that must not change return
type; the float is what the device ships and what the numeric analysis suite
consumes). This module adds the *typed* interpretation on top: given a parameter
definition that declares an optional ``type:`` (``enum``/``bitmask``/``ascii``/
``date``/``bcd``/``struct``) plus its companion map, it turns the raw integer
into a category label, flag set, string, date, or structured record — for
display and for categorical analysis — without touching the numeric path.

Leaf module: depends only on ``canlib.expression`` (itself a leaf subsystem —
``expression`` + ``expression_compile`` + ``expression_nodes`` import nothing else
from ``canlib``). Kept dependency-free of the rest of ``canlib`` so any caller
(decode, captures, investigate, monitor, identity) can import it without a cycle.
The date/BCD/ASCII primitives here are the single home for that logic —
``identity_decode`` delegates to them so identity DIDs and the analysis suite
decode the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date

from canlib.expression import evaluate_expression

# Param `type:` values (mirror canlib/schema/pids_schema.yaml valid_param_types).
NUMERIC = "numeric"
ENUM = "enum"
BITMASK = "bitmask"
ASCII = "ascii"
DATE = "date"
BCD = "bcd"
STRUCT = "struct"

VALID_TYPES = (NUMERIC, ENUM, BITMASK, ASCII, DATE, BCD, STRUCT)

# Plausible calendar-year window for date detection (shared with identity).
_MIN_YEAR = 1990
_MAX_YEAR = 2099


@dataclass
class DecodedValue:
    """The typed interpretation of a parameter against one payload.

    ``raw`` is always the numeric expression value (``None`` on eval error), so
    every existing numeric consumer keeps working regardless of ``kind``. The
    type-specific fields are populated per ``kind``:

    * ``numeric``  — only ``raw``.
    * ``enum``     — ``label`` (falls back to ``"raw"`` for an unmapped value).
    * ``bitmask``  — ``flags`` (labels of set bits, in bit order).
    * ``ascii``    — ``text``.
    * ``date``     — ``dt`` and ``text`` (ISO ``YYYY-MM-DD``).
    * ``bcd``      — ``raw`` holds the decoded decimal (also in ``text``).
    * ``struct``   — ``fields`` (name -> nested ``DecodedValue``).
    """

    kind: str = NUMERIC
    raw: float | None = None
    label: str | None = None
    text: str | None = None
    flags: list[str] = field(default_factory=list)
    dt: _date | None = None
    fields: dict[str, DecodedValue] = field(default_factory=dict)
    error: str | None = None

    def is_categorical(self) -> bool:
        """True when this value is nominal (enum/bitmask/struct) rather than an
        interval-scaled number — the signal that categorical stats, not Pearson,
        apply."""
        return self.kind in (ENUM, BITMASK, STRUCT)

    def category(self) -> str | None:
        """A single categorical key for association stats / display.

        ``enum`` -> its label; ``bitmask`` -> the sorted flag set joined; else
        None (numeric/ascii/date are handled by their own renderers)."""
        if self.kind == ENUM:
            return self.label
        if self.kind == BITMASK:
            return "|".join(self.flags) if self.flags else "(none)"
        return None


def _bcd_byte(n: int) -> int | None:
    """Decode a single BCD byte to 0-99, or None if either nibble is > 9."""
    hi, lo = n >> 4, n & 0x0F
    if hi > 9 or lo > 9:
        return None
    return hi * 10 + lo


def _valid_md(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31


def decode_date(stripped: bytes) -> str | None:
    """Decode a manufacture/programming/schedule date, or None if not a
    plausible calendar date.

    Handles the two encodings seen on Hyundai/Kia ECUs:

    * **BCD** (UDS F18x): ``20 17 06 06`` -> ``2017-06-06`` (3-byte ``YY MM DD``
      or 4-byte ``YYYY MM DD``).
    * **Binary** (some KWP2000 1A records): ``07 E0 03 02`` -> year ``0x07E0`` =
      2016, month 3, day 2 -> ``2016-03-02``.

    Returns None for values that don't form a real calendar date (e.g. version
    codes like ``1E090D14``), so callers can fall back to text/hex.
    """
    if len(stripped) == 4:
        d0, d1, d2, d3 = (_bcd_byte(x) for x in stripped)
        if d0 is not None and d1 is not None and d2 is not None and d3 is not None:
            year = d0 * 100 + d1
            if _MIN_YEAR <= year <= _MAX_YEAR and _valid_md(d2, d3):
                return f"{year:04d}-{d2:02d}-{d3:02d}"
        year = (stripped[0] << 8) | stripped[1]  # binary: uint16 BE year
        if _MIN_YEAR <= year <= _MAX_YEAR and _valid_md(stripped[2], stripped[3]):
            return f"{year:04d}-{stripped[2]:02d}-{stripped[3]:02d}"
        return None
    if len(stripped) == 3:
        d0, d1, d2 = (_bcd_byte(x) for x in stripped)  # BCD YY MM DD
        if d0 is not None and d1 is not None and d2 is not None and _valid_md(d1, d2):
            return f"{2000 + d0:04d}-{d1:02d}-{d2:02d}"
        return None
    return None


def bytes_to_ascii(stripped: bytes) -> str:
    """Render bytes as text when mostly printable, else uppercase hex.

    Strips the common Hyundai/Kia AA/00/FF padding first. Shared by the
    identity reader and the ``type: ascii`` param decoder.
    """
    trimmed = stripped.rstrip(b"\xaa\x00\xff").lstrip(b"\x00")
    if not trimmed:
        return "(empty)"
    printable = sum(1 for b in trimmed if 32 <= b < 127)
    if printable >= max(1, len(trimmed)) * 0.6:
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in trimmed)
        return text.strip() or trimmed.hex().upper()
    return trimmed.hex().upper()


def _eval_raw(expression: str, wican_bytes: bytes) -> float:
    return evaluate_expression(expression, wican_bytes)


def _decode_enum(raw: float, values: dict) -> str:
    """Map a raw int to its enum label, falling back to the numeric string."""
    key = int(raw)
    # `values` keys may be int or str (YAML) — accept both.
    if key in values:
        return str(values[key])
    if str(key) in values:
        return str(values[str(key)])
    return str(key)


def _decode_bitmask(raw: float, bits: dict) -> list[str]:
    """Return the labels of set bits, in ascending bit-index order.

    A bit with no label but set is reported as ``bit<N>`` so unmapped-but-active
    flags are still visible.
    """
    val = int(raw)
    out: list[str] = []
    for i in range(64):
        if not (val >> i) & 1:
            continue
        label = bits.get(i, bits.get(str(i)))
        out.append(str(label) if label is not None else f"bit{i}")
    return out


def _payload_slice_for_type(wican_bytes: bytes, param: dict) -> bytes:
    """Best-effort byte slice for ascii/date types.

    ``ascii``/``date`` interpret a *run* of bytes, not a single scalar. When the
    expression is a simple multi-byte range ``[Bn:Bm]`` we honor it; otherwise
    we fall back to the whole frame after the header. This keeps the common case
    (an explicit range) exact without requiring a full expression AST.
    """
    import re

    expr = (param.get("expression") or "").strip()
    m = re.fullmatch(r"\[[BS](\d+):[BS](\d+)\]", expr)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 0 <= lo <= hi < len(wican_bytes):
            return wican_bytes[lo : hi + 1]
    # Fallback: drop the PCI + SID/DID header heuristically (first 3 bytes) and
    # use the rest. Callers that need precision should give an explicit range.
    return wican_bytes[3:] if len(wican_bytes) > 3 else wican_bytes


def decode_typed(param: dict, wican_bytes: bytes) -> DecodedValue:
    """Decode one parameter against a WiCAN-layout payload into a typed value.

    ``param`` is the parameter definition dict (``expression`` + optional
    ``type``/``values``/``bits``/``fields``). ``wican_bytes`` must be in the
    WiCAN AutoPID layout (PCI re-inserted) — the same contract as
    ``evaluate_expression`` (see ``canlib.expression`` module docstring).

    A parameter with no ``type`` (or ``type: numeric``) yields a ``numeric``
    ``DecodedValue`` carrying just ``raw`` — identical information to today's
    float path.
    """
    ptype = (param.get("type") or NUMERIC).lower()

    # ASCII/date read a byte run, not a scalar expression.
    if ptype == ASCII:
        try:
            text = bytes_to_ascii(_payload_slice_for_type(wican_bytes, param))
            return DecodedValue(kind=ASCII, text=text)
        except Exception as e:
            return DecodedValue(kind=ASCII, error=str(e))

    if ptype == DATE:
        try:
            sl = _payload_slice_for_type(wican_bytes, param).rstrip(b"\xaa\x00\xff")
            iso = decode_date(sl)
            if iso:
                y, mo, d = (int(x) for x in iso.split("-"))
                return DecodedValue(kind=DATE, text=iso, dt=_date(y, mo, d))
            return DecodedValue(kind=DATE, text=None, error="not a valid date")
        except Exception as e:
            return DecodedValue(kind=DATE, error=str(e))

    if ptype == STRUCT:
        result = DecodedValue(kind=STRUCT)
        for sub in param.get("fields") or []:
            if not isinstance(sub, dict):
                continue
            name = sub.get("name") or "?"
            result.fields[name] = decode_typed(sub, wican_bytes)
        return result

    # Scalar-expression-backed types: numeric / enum / bitmask / bcd.
    try:
        raw = _eval_raw(param.get("expression", ""), wican_bytes)
    except Exception as e:
        return DecodedValue(kind=ptype, raw=None, error=str(e))

    if ptype == ENUM:
        return DecodedValue(kind=ENUM, raw=raw, label=_decode_enum(raw, param.get("values") or {}))
    if ptype == BITMASK:
        return DecodedValue(
            kind=BITMASK, raw=raw, flags=_decode_bitmask(raw, param.get("bits") or {})
        )
    if ptype == BCD:
        dec = _bcd_byte(int(raw)) if 0 <= int(raw) <= 0xFF else None
        if dec is None:
            return DecodedValue(kind=BCD, raw=raw, text=str(int(raw)))
        return DecodedValue(kind=BCD, raw=float(dec), text=str(dec))

    return DecodedValue(kind=NUMERIC, raw=raw)


def render(dv: DecodedValue, unit: str = "") -> str:
    """Render a DecodedValue for terminal/JSON display."""
    if dv.error and dv.kind not in (DATE,):
        return "ERROR"
    if dv.kind == ENUM:
        base = dv.label if dv.label is not None else "?"
        raw_s = "" if dv.raw is None else f" ({int(dv.raw)})"
        return f"{base}{raw_s}"
    if dv.kind == BITMASK:
        return "|".join(dv.flags) if dv.flags else "(none)"
    if dv.kind == ASCII:
        return dv.text or "(empty)"
    if dv.kind == DATE:
        return dv.text or "(invalid date)"
    if dv.kind == BCD:
        return f"{dv.text}{unit}" if dv.text is not None else "ERROR"
    if dv.kind == STRUCT:
        return "{" + ", ".join(f"{k}={render(v)}" for k, v in dv.fields.items()) + "}"
    # numeric
    if dv.raw is None:
        return "ERROR"
    if dv.raw == int(dv.raw):
        return f"{int(dv.raw)}{unit}"
    return f"{dv.raw:.2f}{unit}"
