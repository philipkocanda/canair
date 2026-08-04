"""Runtime semantics of a compiled WiCAN expression — the closure node factories.

Each factory here returns a :data:`CompiledExpression`: a closure taking
``(data, V)`` and returning a float. Operand factories bind a byte index (and, for
a range, its sign-extension container) at build time; operator factories combine
two operand closures. Nothing in this module parses text — that is
:mod:`canlib.expression_compile`, which assembles these nodes into a tree.

Splitting them this way keeps the *semantics* (byte loads, sign extension,
arithmetic — the part that must match wican-fw/main/expression_parser.c exactly)
readable independently of the *grammar* (tokenizing, precedence, associativity).

The byte-space contract these nodes assume — indices are read literally, so
``data`` must be in the WiCAN AutoPID layout — is documented once in
:mod:`canlib.expression`.
"""

from collections.abc import Callable

#: A parsed expression (or subexpression), callable as ``(data, V) -> float``.
CompiledExpression = Callable[[bytes, float], float]

#: Combines two operand nodes into one, given the source expression for messages.
NodeFactory = Callable[[CompiledExpression, CompiledExpression, str], CompiledExpression]


# --------------------------------------------------------------------------
# The [Sn:Sm] sign-extension container ladder
#
# The firmware accumulates a signed range into the smallest native container that
# holds the span — int8/int16/int32/int64 (see ``sum_*_signed`` in
# wican-fw/main/expression_parser.c) — so the sign bit is taken from the
# *container's* top bit, not the read's. The public guard and the ladder that
# creates the need for it live together, so neither can drift from the other.
# --------------------------------------------------------------------------

# Byte widths the ``[Sn:Sm]`` range form sign-extends *correctly*: only a span
# whose width exactly fills a container. A 3/5/6/7-byte signed range is
# sign-extended from the wrong bit and can never go negative, so ``[Sn:Sm]`` must
# not be used for it — emit an arithmetic composition with the MSB signed instead
# (e.g. ``S5*65536 + B6*256 + B7``), which is exact for any width.
SIGNED_RANGE_WIDTHS = frozenset({1, 2, 4, 8})


def signed_range_is_exact(width: int) -> bool:
    """True if ``[Sn:Sm]`` spanning ``width`` bytes sign-extends correctly.

    See :data:`SIGNED_RANGE_WIDTHS`. Callers that build expressions (the byte
    sweeps in :mod:`canlib.inspect_bytes` and :class:`canlib.notation.ByteRef`)
    must fall back to an arithmetic composition when this is False, or they emit
    an expression that decodes to a different value than they measured.
    """
    return width in SIGNED_RANGE_WIDTHS


def _signed_container_bits(span: int) -> int:
    """Width of the native container the firmware accumulates a signed range into."""
    if span == 0:
        return 8
    if span == 1:
        return 16
    if span <= 3:
        return 32
    return 64


# --------------------------------------------------------------------------
# Operand nodes
# --------------------------------------------------------------------------


def const(value: float) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return value

    return node


def external_value() -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return V

    return node


def unsigned_byte(idx: int) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float(data[idx])

    return node


def signed_byte(idx: int) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        val = data[idx]
        return float(val if val < 128 else val - 256)

    return node


def bit(idx: int, index: int) -> CompiledExpression:
    def node(data: bytes, V: float) -> float:
        return float((data[idx] >> index) & 1)

    return node


def unsigned_range(start_idx: int, end_idx: int) -> CompiledExpression:
    if start_idx > end_idx:
        # Reversed range: the firmware's accumulation loop runs zero times and
        # never touches ``data`` — so not even an out-of-range index raises.
        return const(0.0)

    def node(data: bytes, V: float) -> float:
        if end_idx >= len(data):
            raise IndexError("index out of range")
        return float(int.from_bytes(data[start_idx : end_idx + 1], "big"))

    return node


def signed_range(start_idx: int, end_idx: int) -> CompiledExpression:
    if start_idx > end_idx:
        return const(0.0)
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
# The factory signature is uniform so :data:`BINARY_NODES` can be a plain lookup;
# only ``/`` uses the ``expression`` argument, to name the offending expression in
# its ZeroDivisionError as the scanning evaluator did.
# --------------------------------------------------------------------------


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


#: Operator token → the node factory implementing it.
BINARY_NODES: dict[str, NodeFactory] = {
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


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def with_discarded(
    root: CompiledExpression, discarded: tuple[CompiledExpression, ...]
) -> CompiledExpression:
    """Keep the side effects of operands the expression silently drops.

    Only ``operand_stack[0]`` is ever returned, but the pre-compile evaluator
    *read* every operand as it scanned — so an out-of-range byte in a dropped
    operand (``B1 B99``) raised ``IndexError`` rather than being ignored. A
    dropped entry is always pushed after the root stopped being combined, so
    evaluating the root first and the dropped subtrees after reproduces the scan's
    ordering exactly.
    """

    def node(data: bytes, V: float) -> float:
        value = root(data, V)
        for extra in discarded:
            extra(data, V)
        return value

    return node
