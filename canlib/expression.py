"""WiCAN expression evaluator.

Faithful Python port of wican-fw/main/expression_parser.c evaluate_expression().

**Byte-space contract (important):** this evaluator is *byte-space-agnostic* — it
indexes the ``data`` argument literally (``data[idx]``) with no PCI-awareness.
The WiCAN indexing convention (``B0=PCI``, ``B9=first consecutive-frame data
byte``, …) is therefore imposed entirely by the *caller*, which must pass bytes
in the WiCAN AutoPID layout (PCI re-inserted) — see
:func:`canlib.byteindex.payload_to_wican_bytes` /
:func:`canlib.autopid_layout.uds_hex_to_wican_bytes`. Feeding a raw ISO-TP payload
(PCI stripped) would silently misalign every index past the first PCI byte.

**Parsing is separate from evaluation.** An expression is constant across a whole
series, so :func:`compile_expression` parses it once into a tree of closures and
:func:`evaluate_expression` is a thin, cached wrapper around that — the entry
point and the byte-space contract above are unchanged. Bulk consumers that
evaluate one expression against many payloads may call
:func:`compile_expression` directly to skip the per-call cache lookup.
"""

import re
from collections.abc import Callable
from functools import lru_cache

# Byte widths the ``[Sn:Sm]`` range form sign-extends *correctly*.
#
# The firmware accumulates a signed range into the smallest native container that
# holds the span — int8/int16/int32/int64 (see ``sum_*_signed`` in
# wican-fw/main/expression_parser.c, mirrored by :func:`_signed_container_bits`)
# — so the sign bit is taken from the *container's* top bit, not the read's. That
# only coincides for a span whose width exactly fills a container: 1, 2, 4 or 8
# bytes. A 3/5/6/7-byte signed range is sign-extended from the wrong bit and can
# never go negative, so ``[Sn:Sm]`` must not be used for it — emit an arithmetic
# composition with the MSB signed instead (e.g. ``S5*65536 + B6*256 + B7``), which
# is exact for any width.
SIGNED_RANGE_WIDTHS = frozenset({1, 2, 4, 8})

#: A parsed expression, callable as ``(data, V) -> float``.
CompiledExpression = Callable[[bytes, float], float]

_RANGE_UNSIGNED_RE = re.compile(r"\[B(\d+):B(\d+)\]")
_RANGE_SIGNED_RE = re.compile(r"\[S(\d+):S(\d+)\]")


def signed_range_is_exact(width: int) -> bool:
    """True if ``[Sn:Sm]`` spanning ``width`` bytes sign-extends correctly.

    See :data:`SIGNED_RANGE_WIDTHS`. Callers that build expressions (the byte
    sweeps in :mod:`canlib.inspect_bytes` and :class:`canlib.notation.ByteRef`)
    must fall back to an arithmetic composition when this is False, or they emit
    an expression that decodes to a different value than they measured.
    """
    return width in SIGNED_RANGE_WIDTHS


# --------------------------------------------------------------------------
# Operand nodes
# --------------------------------------------------------------------------


def _const(value: float) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return value

    return node


def _external_value() -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return V

    return node


def _unsigned_byte(idx: int) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float(data[idx])

    return node


def _signed_byte(idx: int) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        val = data[idx]
        return float(val if val < 128 else val - 256)

    return node


def _bit(idx: int, bit: int) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float((data[idx] >> bit) & 1)

    return node


def _unsigned_range(start_idx: int, end_idx: int) -> CompiledExpression:
    if start_idx > end_idx:
        # Reversed range: the firmware's accumulation loop runs zero times and
        # never touches ``data`` — so not even an out-of-range index raises.
        return _const(0.0)

    def node(data: bytes, V: float) -> float:
        if end_idx >= len(data):
            raise IndexError("index out of range")
        return float(int.from_bytes(data[start_idx : end_idx + 1], "big"))

    return node


def _signed_container_bits(span: int) -> int:
    """Width of the native container the firmware accumulates a signed range into."""
    if span == 0:
        return 8
    if span == 1:
        return 16
    if span <= 3:
        return 32
    return 64


def _signed_range(start_idx: int, end_idx: int) -> CompiledExpression:
    if start_idx > end_idx:
        return _const(0.0)
    bits = _signed_container_bits(end_idx - start_idx)
    threshold = 1 << (bits - 1)
    modulus = 1 << bits

    def node(data: bytes, V: float) -> float:
        if end_idx >= len(data):
            raise IndexError("index out of range")
        raw = int.from_bytes(data[start_idx : end_idx + 1], "big")
        return float(raw if raw < threshold else raw - modulus)

    return node


# --------------------------------------------------------------------------
# Operator nodes
#
# Each factory combines two operand closures. Operands are evaluated
# left-then-right, matching the order the shunting-yard scan read them, so an
# ``IndexError`` from a later operand still precedes an operator's own error.
#
# The factory signature is uniform so :data:`_BINARY_NODES` can be a plain
# lookup; only ``/`` uses the ``expression`` argument, to name the offending
# expression in its ZeroDivisionError as the scanning evaluator did.
# --------------------------------------------------------------------------

#: Combines two operand nodes into one, given the source expression for messages.
_NodeFactory = Callable[[CompiledExpression, CompiledExpression, str], CompiledExpression]


def _add(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return a(data, V) + b(data, V)

    return node


def _sub(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return a(data, V) - b(data, V)

    return node


def _mul(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return a(data, V) * b(data, V)

    return node


def _div(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        left = a(data, V)
        right = b(data, V)
        if right == 0:
            raise ZeroDivisionError(f"Division by zero in expression: {expression}")
        return left / right

    return node


def _bit_and(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float(int(a(data, V)) & int(b(data, V)))

    return node


def _bit_or(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float(int(a(data, V)) | int(b(data, V)))

    return node


def _bit_xor(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float(int(a(data, V)) ^ int(b(data, V)))

    return node


def _shl(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float(int(a(data, V)) << int(b(data, V)))

    return node


def _shr(a: CompiledExpression, b: CompiledExpression, expression: str) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float(int(a(data, V)) >> int(b(data, V)))

    return node


_BINARY_NODES: dict[str, _NodeFactory] = {
    "+": _add,
    "-": _sub,
    "*": _mul,
    "/": _div,
    "&": _bit_and,
    "|": _bit_or,
    "^": _bit_xor,
    "<<": _shl,
    ">>": _shr,
}

_PRECEDENCE: dict[str, int] = {
    "|": 1,
    "^": 1,
    "&": 2,
    "<<": 3,
    ">>": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
}


def _precedence(op: str) -> int:
    return _PRECEDENCE.get(op, 0)


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------


def _with_discarded(
    root: CompiledExpression, discarded: tuple[CompiledExpression, ...]
) -> CompiledExpression:
    """Keep the side effects of operands the expression silently drops.

    Only ``operand_stack[0]`` is ever returned, but the scan *read* every operand
    as it went — so an out-of-range byte in a dropped operand (``B1 B99``) raised
    ``IndexError`` rather than being ignored. A dropped entry is always pushed
    after the root stopped being combined, so evaluating the root first and the
    dropped subtrees after reproduces the scan's ordering exactly.
    """

    def node(data: bytes, V: float) -> float:
        value = root(data, V)
        for extra in discarded:
            extra(data, V)
        return value

    return node


def compile_expression(expression: str) -> CompiledExpression:
    """Parse a WiCAN expression once into a callable ``(data, V) -> float``.

    Shunting-yard over the syntax documented on :func:`evaluate_expression`.
    Everything that does not depend on ``data`` — the character scan, byte-index
    reconstruction, the range regexes, operator precedence — happens here; the
    returned closure tree does only byte loads and arithmetic.

    Malformed input raises the same exception it always did, but a *parse* error
    now surfaces here rather than at the point the scan reached it. For input that
    is both unparseable and evaluates a bad operand (an out-of-range byte, a
    divide by zero), the parse error therefore wins where the old scan-as-you-go
    evaluator reported the payload-dependent one — which was never stable, since
    it depended on the payload. Anything that compiles decodes identically.
    Because :func:`evaluate_expression` caches successful compiles only, a caller
    still sees an identical exception on every call.
    """
    operand_stack: list[CompiledExpression] = []
    operator_stack: list[str] = []

    def emit(op: str) -> None:
        # Operands are popped before the operator is recognised, so unbalanced
        # input (a stray ``(`` reaching the final reduction) underflows the
        # operand stack with an IndexError exactly as it always did.
        b = operand_stack.pop()
        a = operand_stack.pop()
        factory = _BINARY_NODES.get(op)
        if factory is None:
            raise ValueError(f"Unknown operator: {op}")
        operand_stack.append(factory(a, b, expression))

    def process_pending(min_prec: int) -> None:
        while (
            operator_stack
            and operator_stack[-1] != "("
            and _precedence(operator_stack[-1]) >= min_prec
        ):
            emit(operator_stack.pop())

    i = 0
    expr = expression.strip()

    while i < len(expr):
        ch = expr[i]

        # Whitespace
        if ch == " ":
            i += 1
            continue

        # Numeric literal
        if ch.isdigit() or (ch == "." and i + 1 < len(expr) and expr[i + 1].isdigit()):
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            operand_stack.append(_const(float(expr[i:j])))
            i = j
            continue

        # V (external value)
        if ch == "V" and (i + 1 >= len(expr) or not expr[i + 1].isalnum()):
            operand_stack.append(_external_value())
            i += 1
            continue

        # Multi-byte range: [Bn:Bm] or [Sn:Sm]
        if ch == "[":
            m_unsigned = _RANGE_UNSIGNED_RE.match(expr, i)
            if m_unsigned:
                operand_stack.append(
                    _unsigned_range(int(m_unsigned.group(1)), int(m_unsigned.group(2)))
                )
                i = m_unsigned.end()
                continue

            m_signed = _RANGE_SIGNED_RE.match(expr, i)
            if m_signed:
                operand_stack.append(_signed_range(int(m_signed.group(1)), int(m_signed.group(2))))
                i = m_signed.end()
                continue

            raise ValueError(f"Invalid array syntax at position {i}: {expr[i:]}")

        # Unsigned byte: Bn or Bn:bit
        if ch == "B":
            i += 1
            idx = 0
            while i < len(expr) and expr[i].isdigit():
                idx = idx * 10 + int(expr[i])
                i += 1
            if i < len(expr) and expr[i] == ":":
                i += 1
                bit = int(expr[i])
                i += 1
                operand_stack.append(_bit(idx, bit))
            else:
                operand_stack.append(_unsigned_byte(idx))
            continue

        # Signed byte: Sn
        if ch == "S":
            i += 1
            idx = 0
            while i < len(expr) and expr[i].isdigit():
                idx = idx * 10 + int(expr[i])
                i += 1
            operand_stack.append(_signed_byte(idx))
            continue

        # Parentheses
        if ch == "(":
            operator_stack.append("(")
            i += 1
            continue

        if ch == ")":
            while operator_stack and operator_stack[-1] != "(":
                emit(operator_stack.pop())
            if operator_stack and operator_stack[-1] == "(":
                operator_stack.pop()
            i += 1
            continue

        # Operators
        if ch in ("+", "-", "*", "/", "&", "|", "^"):
            process_pending(_precedence(ch))
            operator_stack.append(ch)
            i += 1
            continue

        if ch == "<" and i + 1 < len(expr) and expr[i + 1] == "<":
            process_pending(_precedence("<<"))
            operator_stack.append("<<")
            i += 2
            continue

        if ch == ">" and i + 1 < len(expr) and expr[i + 1] == ">":
            process_pending(_precedence(">>"))
            operator_stack.append(">>")
            i += 2
            continue

        raise ValueError(f"Invalid character '{ch}' at position {i} in expression: {expression}")

    # Final reduction
    while operator_stack:
        emit(operator_stack.pop())

    if not operand_stack:
        raise ValueError(f"Empty expression: {expression}")
    root, *discarded = operand_stack
    return _with_discarded(root, tuple(discarded)) if discarded else root


_compile_cached = lru_cache(maxsize=512)(compile_expression)


def evaluate_expression(expression: str, data: bytes, V: float = 0.0) -> float:
    """Evaluate a WiCAN expression against a byte array.

    ``data`` must be in the WiCAN AutoPID layout (PCI bytes re-inserted); indices
    are read literally (see the module docstring's byte-space contract).

    The expression is parsed by :func:`compile_expression` and the result cached,
    so re-evaluating the same expression against many payloads re-parses nothing.

    Supported syntax:
        Bn      — unsigned byte at index n
        Sn      — signed byte at index n (int8)
        Bn:m    — bit m of byte n (0=LSB)
        [Bn:Bm] — big-endian unsigned multi-byte (up to 8 bytes)
        [Sn:Sm] — big-endian signed multi-byte (auto-sized: 8/16/32/64-bit)
        V       — external value parameter (default 0)
        + - * / — arithmetic
        << >>   — bit shift
        & | ^   — bitwise AND, OR, XOR
        ( )     — grouping
        numeric — integer or float literals
    """
    return _compile_cached(expression)(data, V)
