"""Structural checks on a WiCAN expression — mistakes the evaluator cannot see.

An expression that parses and evaluates can still be wrong in a way no single
payload reveals. The check here is the **signed low byte**: when an expression
builds a multi-byte integer by hand (``(S15 << 8) | B17``, ``S3*256 + B4``) only
the most significant byte may be signed. Reading a lower byte with ``Sn``
sign-extends it, so every value whose low byte is >= 0x80 decodes ``256 x`` that
byte's weight too low — correct across one band, wrong everywhere else, which is
easy to "verify" from captures that happen to sit in the good band.

The lint walks the same grammar the evaluator compiles
(:func:`canlib.expression.compile.parse_expression`) into a small structural tree.
"""

from dataclasses import dataclass

from canlib.expression.compile import parse_expression

# Combiners a hand-built word is assembled with.
_COMBINERS = frozenset({"|", "+"})


@dataclass(frozen=True)
class _Ref:
    """A byte load: ``Bn``/``Sn`` (``width`` 1) or a ``[Bn:Bm]``/``[Sn:Sm]`` range."""

    idx: int
    signed: bool
    width: int = 1

    @property
    def text(self) -> str:
        p = "S" if self.signed else "B"
        if self.width == 1:
            return f"{p}{self.idx}"
        return f"[{p}{self.idx}:{p}{self.idx + self.width - 1}]"


@dataclass(frozen=True)
class _Const:
    value: float


@dataclass(frozen=True)
class _BinOp:
    op: str
    a: "_Node"
    b: "_Node"


@dataclass(frozen=True)
class _Opaque:
    """A node the lint has no rule for (``V``, a bit read)."""


_Node = _Ref | _Const | _BinOp | _Opaque


class _TreeBuilder:
    def const(self, value: float) -> _Node:
        return _Const(value)

    def external_value(self) -> _Node:
        return _Opaque()

    def unsigned_byte(self, idx: int) -> _Node:
        return _Ref(idx, signed=False)

    def signed_byte(self, idx: int) -> _Node:
        return _Ref(idx, signed=True)

    def bit(self, idx: int, index: int) -> _Node:
        return _Opaque()

    def unsigned_range(self, start_idx: int, end_idx: int) -> _Node:
        return _Ref(start_idx, signed=False, width=end_idx - start_idx + 1)

    def signed_range(self, start_idx: int, end_idx: int) -> _Node:
        return _Ref(start_idx, signed=True, width=end_idx - start_idx + 1)

    def binary(self, op: str, a: _Node, b: _Node, expression: str) -> _Node:
        return _BinOp(op, a, b)

    def finish(self, root: _Node, discarded: tuple[_Node, ...]) -> _Node:
        return root


def _power_of_two_shift(node: _Node) -> int | None:
    """``k`` when ``node`` is the constant ``2**k`` (k >= 1), else ``None``."""
    if not isinstance(node, _Const) or node.value < 2 or node.value != int(node.value):
        return None
    v = int(node.value)
    return v.bit_length() - 1 if v & (v - 1) == 0 else None


def _byte_term(node: _Node) -> tuple[_Ref, int] | None:
    """``(ref, shift)`` when ``node`` is a byte load, optionally shifted left.

    Recognises ``ref``, ``ref << k`` and ``ref * 2**k`` (either operand order).
    """
    if isinstance(node, _Ref):
        return node, 0
    if not isinstance(node, _BinOp):
        return None
    if node.op == "<<" and isinstance(node.a, _Ref) and isinstance(node.b, _Const):
        return node.a, int(node.b.value)
    if node.op == "*":
        for ref, k in ((node.a, node.b), (node.b, node.a)):
            shift = _power_of_two_shift(k)
            if isinstance(ref, _Ref) and shift is not None:
                return ref, shift
    return None


def _flatten(node: _Node, out: list[_Node]) -> None:
    if isinstance(node, _BinOp) and node.op in _COMBINERS:
        _flatten(node.a, out)
        _flatten(node.b, out)
    else:
        out.append(node)


def _signed_low_terms(node: _Node) -> list[tuple[_Ref, _Ref]]:
    """Every ``(signed_low_ref, top_ref)`` pair in the hand-built words under ``node``."""
    if not isinstance(node, _BinOp):
        return []
    if node.op not in _COMBINERS:
        return _signed_low_terms(node.a) + _signed_low_terms(node.b)

    terms: list[_Node] = []
    _flatten(node, terms)
    found: list[tuple[_Ref, _Ref]] = []
    # Byte-aligned shifts only: ``S1*2 + S2`` is arithmetic, not a word.
    byte_terms = [t for t in map(_byte_term, terms) if t is not None and t[1] % 8 == 0]
    if len(byte_terms) >= 2:
        top_ref, top_shift = max(byte_terms, key=lambda t: t[1])
        if top_shift > 0:
            found.extend(
                (ref, top_ref) for ref, shift in byte_terms if ref.signed and shift < top_shift
            )
    for t in terms:
        if _byte_term(t) is None:
            found.extend(_signed_low_terms(t))
    return found


def signed_low_bytes(expression: str) -> list[tuple[str, str]]:
    """``(low_ref, top_ref)`` texts for each lower byte of a hand-built word read signed.

    An unparseable expression yields ``[]`` — syntax is reported elsewhere.
    """
    try:
        tree = parse_expression(expression, _TreeBuilder())
    except (ValueError, IndexError):
        return []
    return [(low.text, top.text) for low, top in _signed_low_terms(tree)]
