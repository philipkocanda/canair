"""Fidelity tests for the compiled expression evaluator.

:func:`canlib.expression.evaluate_expression` used to re-parse its expression on
every call; it now compiles once into a closure tree (see
``plans/2026-08-04-expression-evaluator-performance.md``). The evaluator's output
is what ``--promote`` persists and what the WiCAN device itself computes, so
**any** divergence is a silently wrong decoded value rather than a crash.

The load-bearing guard is therefore differential: a verbatim copy of the
pre-change implementation lives *here* (deliberately not in ``canlib/``, so the
production module keeps one code path) and every test asserts the two agree on
the returned float **or** the exception type — including for the evaluator's
inherited quirks, which are out of scope to fix.
"""

from __future__ import annotations

import math
import random
import re

import pytest
import yaml

from canlib.constants import BUNDLED_PROFILES_DIR
from canlib.expression import _compile_cached, compile_expression, evaluate_expression

# --------------------------------------------------------------------------
# Preserved reference implementation (pre-compile, verbatim). Do not "clean up":
# its quirks are the contract.
# --------------------------------------------------------------------------


def reference_evaluate(expression: str, data: bytes, V: float = 0.0) -> float:
    operand_stack: list[float] = []
    operator_stack: list[str] = []

    def precedence(op: str) -> int:
        if op in ("|", "^"):
            return 1
        if op == "&":
            return 2
        if op in ("<<", ">>"):
            return 3
        if op in ("+", "-"):
            return 4
        if op in ("*", "/"):
            return 5
        return 0

    def apply_op(op: str, a: float, b: float) -> float:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise ZeroDivisionError(f"Division by zero in expression: {expression}")
            return a / b
        if op == "&":
            return float(int(a) & int(b))
        if op == "|":
            return float(int(a) | int(b))
        if op == "^":
            return float(int(a) ^ int(b))
        if op == "<<":
            return float(int(a) << int(b))
        if op == ">>":
            return float(int(a) >> int(b))
        raise ValueError(f"Unknown operator: {op}")

    def process_pending(min_prec: int):
        while (
            operator_stack
            and operator_stack[-1] != "("
            and precedence(operator_stack[-1]) >= min_prec
        ):
            op = operator_stack.pop()
            b = operand_stack.pop()
            a = operand_stack.pop()
            operand_stack.append(apply_op(op, a, b))

    i = 0
    expr = expression.strip()

    while i < len(expr):
        ch = expr[i]

        if ch == " ":
            i += 1
            continue

        if ch.isdigit() or (ch == "." and i + 1 < len(expr) and expr[i + 1].isdigit()):
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            operand_stack.append(float(expr[i:j]))
            i = j
            continue

        if ch == "V" and (i + 1 >= len(expr) or not expr[i + 1].isalnum()):
            operand_stack.append(V)
            i += 1
            continue

        if ch == "[":
            m_unsigned = re.match(r"\[B(\d+):B(\d+)\]", expr[i:])
            if m_unsigned:
                start_idx = int(m_unsigned.group(1))
                end_idx = int(m_unsigned.group(2))
                value = 0
                for j in range(start_idx, end_idx + 1):
                    shift = (end_idx - j) * 8
                    value |= data[j] << shift
                operand_stack.append(float(value))
                i += m_unsigned.end()
                continue

            m_signed = re.match(r"\[S(\d+):S(\d+)\]", expr[i:])
            if m_signed:
                start_idx = int(m_signed.group(1))
                end_idx = int(m_signed.group(2))
                span = end_idx - start_idx
                raw = 0
                for j in range(start_idx, end_idx + 1):
                    shift = (end_idx - j) * 8
                    raw |= data[j] << shift
                if span == 0:
                    value = raw if raw < 128 else raw - 256
                elif span == 1:
                    value = raw if raw < 32768 else raw - 65536
                elif span <= 3:
                    value = raw if raw < 2147483648 else raw - 4294967296
                else:
                    value = raw if raw < (1 << 63) else raw - (1 << 64)
                operand_stack.append(float(value))
                i += m_signed.end()
                continue

            raise ValueError(f"Invalid array syntax at position {i}: {expr[i:]}")

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
                operand_stack.append(float((data[idx] >> bit) & 1))
            else:
                operand_stack.append(float(data[idx]))
            continue

        if ch == "S":
            i += 1
            idx = 0
            while i < len(expr) and expr[i].isdigit():
                idx = idx * 10 + int(expr[i])
                i += 1
            val = data[idx]
            operand_stack.append(float(val if val < 128 else val - 256))
            continue

        if ch == "(":
            operator_stack.append("(")
            i += 1
            continue

        if ch == ")":
            while operator_stack and operator_stack[-1] != "(":
                op = operator_stack.pop()
                b = operand_stack.pop()
                a = operand_stack.pop()
                operand_stack.append(apply_op(op, a, b))
            if operator_stack and operator_stack[-1] == "(":
                operator_stack.pop()
            i += 1
            continue

        if ch in ("+", "-", "*", "/", "&", "|", "^"):
            process_pending(precedence(ch))
            operator_stack.append(ch)
            i += 1
            continue

        if ch == "<" and i + 1 < len(expr) and expr[i + 1] == "<":
            process_pending(precedence("<<"))
            operator_stack.append("<<")
            i += 2
            continue

        if ch == ">" and i + 1 < len(expr) and expr[i + 1] == ">":
            process_pending(precedence(">>"))
            operator_stack.append(">>")
            i += 2
            continue

        raise ValueError(f"Invalid character '{ch}' at position {i} in expression: {expression}")

    while operator_stack:
        op = operator_stack.pop()
        b = operand_stack.pop()
        a = operand_stack.pop()
        operand_stack.append(apply_op(op, a, b))

    if not operand_stack:
        raise ValueError(f"Empty expression: {expression}")
    return operand_stack[0]


# --------------------------------------------------------------------------
# Differential comparison
# --------------------------------------------------------------------------


def _outcome(fn, expr: str, data: bytes, V: float):
    try:
        return ("value", fn(expr, data, V))
    except Exception as exc:
        return ("raised", type(exc))


def _compile_error(expr: str) -> type[BaseException] | None:
    try:
        compile_expression(expr)
    except Exception as exc:
        return type(exc)
    return None


def assert_equivalent(expr: str, data: bytes, V: float = 0.0) -> None:
    """The compiled path and the reference must agree on value or exception type.

    One deliberate exception: input that cannot be **parsed** now fails when the
    expression is compiled rather than at the point the scan reached it. So for
    input that is *both* unparseable and evaluates a bad operand (an out-of-range
    byte, a divide by zero), the parse error wins where the eager reference
    surfaced the payload-dependent error first. Both still raise, and which one
    the reference reported was itself payload-dependent — the expression is
    broken either way. Anything that *does* compile must match exactly.
    """
    kind_ref, ref = _outcome(reference_evaluate, expr, data, V)
    kind_new, new = _outcome(evaluate_expression, expr, data, V)

    assert kind_ref == kind_new, (
        f"{expr!r}: reference {kind_ref} {ref!r}, compiled {kind_new} {new!r}"
    )
    if kind_ref == "raised":
        if ref is not new:
            assert new is _compile_error(expr), (
                f"{expr!r}: reference raised {ref.__name__}, compiled raised {new.__name__}"
            )
        return
    assert isinstance(new, float)
    if math.isnan(ref):
        assert math.isnan(new), f"{expr!r}: reference NaN, compiled {new!r}"
        return
    assert ref == new, f"{expr!r}: reference {ref!r}, compiled {new!r}"


# --------------------------------------------------------------------------
# Fuzz corpus generation
# --------------------------------------------------------------------------

_BINARY_OPS = ("+", "-", "*", "/", "&", "|", "^")
_LITERALS = ("0", "1", "2", "3", "10", "100", "256", "1000", "0.5", "1.5", ".5")


def _rand_operand(rng: random.Random) -> str:
    kind = rng.randrange(7)
    if kind == 0:
        return f"B{rng.randrange(0, 40)}"
    if kind == 1:
        return f"S{rng.randrange(0, 40)}"
    if kind == 2:
        return f"B{rng.randrange(0, 40)}:{rng.randrange(0, 10)}"
    if kind == 3:
        start = rng.randrange(0, 40)
        return f"[B{start}:B{start + rng.randrange(0, 9)}]"
    if kind == 4:
        start = rng.randrange(0, 40)
        return f"[S{start}:S{start + rng.randrange(0, 9)}]"
    if kind == 5:
        return "V"
    return rng.choice(_LITERALS)


def _rand_expression(rng: random.Random, depth: int = 0) -> str:
    if depth >= 3 or rng.random() < 0.35:
        return _rand_operand(rng)
    left = _rand_expression(rng, depth + 1)
    right = _rand_expression(rng, depth + 1)
    op = rng.choice(_BINARY_OPS)
    pad = " " if rng.random() < 0.4 else ""
    out = f"{left}{pad}{op}{pad}{right}"
    return f"({out})" if rng.random() < 0.3 else out


# `<`/`>` are excluded deliberately. `<<`/`>>` take their right operand by
# *precedence*, so a fuzzed `[B10:B11] << 14 * [B27:B31]` asks both
# implementations for a multi-terabyte integer — an OOM, not a divergence. Shifts
# are covered exhaustively and deterministically by TestShiftEquivalence instead.
_MUTATION_CHARS = "BSV[]():.+-*/&|^0123456789 @xX"


def _mutate(rng: random.Random, expr: str) -> str:
    if not expr:
        return rng.choice(_MUTATION_CHARS)
    pos = rng.randrange(len(expr))
    mode = rng.randrange(3)
    if mode == 0:  # delete
        return expr[:pos] + expr[pos + 1 :]
    if mode == 1:  # replace
        return expr[:pos] + rng.choice(_MUTATION_CHARS) + expr[pos + 1 :]
    return expr[:pos] + rng.choice(_MUTATION_CHARS) + expr[pos:]  # insert


def _rand_payload(rng: random.Random) -> bytes:
    # Short payloads on purpose: out-of-range byte reads are a real code path and
    # must raise identically.
    length = rng.choice((0, 1, 8, 16, 32, 64))
    return bytes(rng.randrange(256) for _ in range(length))


class TestDifferentialFuzz:
    """Random expressions × random payloads: compiled must match the reference."""

    def test_wellformed(self):
        rng = random.Random(0xC0FFEE)
        for _ in range(4000):
            assert_equivalent(
                _rand_expression(rng), _rand_payload(rng), rng.choice((0.0, 7.5, -3.0))
            )

    def test_mutated(self):
        rng = random.Random(0xBADC0DE)
        for _ in range(4000):
            expr = _rand_expression(rng)
            for _ in range(rng.randrange(1, 4)):
                expr = _mutate(rng, expr)
            assert_equivalent(expr, _rand_payload(rng), rng.choice((0.0, 2.5)))


class TestShiftEquivalence:
    """`<<`/`>>` — kept out of the fuzz (see _MUTATION_CHARS), covered here."""

    _DATA = bytes([0x00, 0x01, 0x0F, 0xF0, 0x80, 0xFF, 0x02, 0x03, *range(8, 40)])

    @pytest.mark.parametrize("op", ["<<", ">>"])
    @pytest.mark.parametrize("count", list(range(0, 17)))
    def test_byte_shifted_by_literal(self, op: str, count: int):
        for idx in (0, 2, 3, 4, 5):
            assert_equivalent(f"B{idx} {op} {count}", self._DATA)
            assert_equivalent(f"S{idx}{op}{count}", self._DATA)
            assert_equivalent(f"[B{idx}:B{idx + 1}] {op} {count}", self._DATA)

    @pytest.mark.parametrize(
        "expr",
        [
            # Shift count taken from a byte (≤255 bits — bounded).
            "B2 << B6",
            "B5 >> B6",
            # Precedence: + - * / all bind tighter than a shift, so the shift's
            # operands are whole arithmetic subexpressions.
            "B1 + B2 << B6",
            "B5 >> B6 + B1",
            "B1 << B6 * B7",
            "B5 / 2 >> B6",
            # Shifts are left-associative and lower precedence than `&`, higher
            # than `|`/`^`.
            "B2 << 2 << 3",
            "B5 >> 1 >> 2",
            "B5 & B2 << 1",
            "B5 << 1 | B2",
            "B5 << 1 ^ B2",
            # Grouping and nesting.
            "(B2 << 4) >> 2",
            "((B1 + B2) << 3) & 255",
            "[B1:B2] << 8 | B3",
            # Fractional operands: the firmware truncates via int() first.
            "B2 / 2 << 1",
            "1.5 << 2",
            # Degenerate / invalid shift forms.
            "B2 <",
            "B2 >",
            "B2 < B1",
            "B2 > B1",
            "<< B2",
            "B2 <<",
            "B2 >> -1",
        ],
    )
    def test_shift_forms(self, expr: str):
        assert_equivalent(expr, self._DATA)
        assert_equivalent(expr, self._DATA, 3.0)


# --------------------------------------------------------------------------
# Real corpus: every expression in every bundled profile
# --------------------------------------------------------------------------


def _bundled_expressions() -> list[str]:
    found: set[str] = set()
    for ecu_file in sorted(BUNDLED_PROFILES_DIR.glob("*/ecus/*.yaml")):
        for expr in _walk_expressions(yaml.safe_load(ecu_file.read_text())):
            found.add(expr)
    return sorted(found)


def _walk_expressions(node) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "expression" and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_walk_expressions(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_expressions(item))
    return out


_BUNDLED = _bundled_expressions()


class TestBundledCorpus:
    """Equivalence across the shipped profiles — the expressions that matter."""

    def test_corpus_is_not_empty(self):
        # Guards the test itself: a changed profile layout must not silently
        # reduce this to a no-op.
        assert len(_BUNDLED) > 100, f"only {len(_BUNDLED)} expressions found"

    @pytest.mark.parametrize("expr", _BUNDLED)
    def test_equivalent_across_payloads(self, expr: str):
        rng = random.Random(hash(expr) & 0xFFFFFFFF)
        payloads = [
            bytes(128),
            bytes([0xFF] * 128),
            bytes(i % 256 for i in range(128)),
            *(bytes(rng.randrange(256) for _ in range(128)) for _ in range(5)),
        ]
        for data in payloads:
            assert_equivalent(expr, data, 0.0)
            assert_equivalent(expr, data, 11.0)


# --------------------------------------------------------------------------
# Inherited quirks — explicit, so a future "cleanup" fails loudly
# --------------------------------------------------------------------------

_RAMP = bytes(range(64))  # B0=0, B1=1, B5=5, …
_FF = b"\x00\xff\xff\xff" + bytes(60)


class TestInheritedQuirks:
    """Documented oddities of the firmware port. Changing these is out of scope."""

    @pytest.mark.parametrize(
        "expr,data,expected",
        [
            # A bit read consumes exactly one digit; the stray `2` is pushed as a
            # literal and dropped, since only operand_stack[0] is returned.
            ("B5:12", _RAMP, 0.0),
            # Two operands, no operator — the second is silently discarded.
            ("B1 B2", _RAMP, 1.0),
            # `B` with no digits reads index 0, then subtracts 1.
            ("B-1", _RAMP, -1.0),
            # Reversed range: the accumulation loop runs zero times.
            ("[B3:B1]", _RAMP, 0.0),
            ("[S3:S1]", _RAMP, 0.0),
            # A stray closing paren is tolerated.
            ("B1)", _RAMP, 1.0),
            # A 3-byte signed range sign-extends from the int32 container's top
            # bit, so it can never go negative (see SIGNED_RANGE_WIDTHS).
            ("[S1:S3]", _FF, 16777215.0),
            ("[S1:S2]", _FF, -1.0),
        ],
    )
    def test_quirk_value(self, expr: str, data: bytes, expected: float):
        assert evaluate_expression(expr, data) == expected
        assert reference_evaluate(expr, data) == expected

    @pytest.mark.parametrize("expr", ["-B1", "+B1", "B1+", "(B1", "((B1+B2", "B1:"])
    def test_unbalanced_underflows(self, expr: str):
        with pytest.raises(IndexError):
            evaluate_expression(expr, _RAMP)
        assert_equivalent(expr, _RAMP)

    @pytest.mark.parametrize(
        "expr,in_range",
        [
            ("B1 B99", "B1 B2"),
            ("B1 [B60:B70]", "B1 [B2:B3]"),
            ("B1 & B2 B99", "B1 & B2 B2"),
        ],
    )
    def test_dropped_operand_still_reads_its_bytes(self, expr: str, in_range: str):
        # The value of a dropped operand is discarded, but the *read* is not: the
        # scan-as-you-go evaluator raised on an out-of-range index even in an
        # operand the expression throws away.
        with pytest.raises(IndexError):
            evaluate_expression(expr, _RAMP[:8])
        assert_equivalent(expr, _RAMP[:8])
        # In range, the dropped operand is simply ignored — B1 is still returned.
        assert evaluate_expression(in_range, _RAMP) == 1.0
        assert_equivalent(in_range, _RAMP)

    def test_parse_error_now_precedes_a_bad_operand(self):
        # The one intentional divergence from the pre-compile evaluator: an
        # unparseable expression fails at compile time, so the parse error wins
        # over the payload-dependent error that used to surface mid-scan.
        with pytest.raises(ValueError, match="Invalid character"):
            evaluate_expression("B6/V:5", b"\x8d")
        with pytest.raises(IndexError):
            reference_evaluate("B6/V:5", b"\x8d")
        # Which one the reference reported was itself payload-dependent, so this
        # was never a stable contract.
        with pytest.raises(ValueError, match="Invalid character"):
            reference_evaluate("B6/V:5", _RAMP)

        # Same for a parse-time operand-stack underflow beating a divide by zero.
        with pytest.raises(IndexError):
            evaluate_expression("V/0/B1+2/", _RAMP)
        with pytest.raises(ZeroDivisionError):
            reference_evaluate("V/0/B1+2/", _RAMP)

    @pytest.mark.parametrize("expr", ["VX", "0x10", "1.5.5", "B1:1:1", "", "   ", "@bad", "[B0]"])
    def test_invalid_raises_value_error(self, expr: str):
        with pytest.raises(ValueError):
            evaluate_expression(expr, _RAMP)
        assert_equivalent(expr, _RAMP)

    def test_reversed_range_ignores_out_of_range_indices(self):
        # The reversed-range short circuit must not start bounds-checking bytes
        # the reference never read.
        assert evaluate_expression("[B99:B3]", b"\x01\x02") == 0.0
        assert reference_evaluate("[B99:B3]", b"\x01\x02") == 0.0

    def test_out_of_range_range_read_raises(self):
        # …but a forward range past the end must still raise, as the per-byte
        # loop did.
        for expr in ("[B0:B9]", "[S0:S9]"):
            with pytest.raises(IndexError):
                evaluate_expression(expr, b"\x01\x02")
            assert_equivalent(expr, b"\x01\x02")

    def test_division_by_zero_message_names_the_expression(self):
        with pytest.raises(ZeroDivisionError, match=re.escape("B00/0")):
            evaluate_expression("B00/0", _RAMP)

    def test_left_operand_error_precedes_division_by_zero(self):
        # The scan read operands before applying operators, so a bad byte index
        # on the left must still win over the divide-by-zero check.
        with pytest.raises(IndexError):
            evaluate_expression("B99/0", b"\x01\x02")


# --------------------------------------------------------------------------
# Compilation / caching
# --------------------------------------------------------------------------


class TestCompileAndCache:
    """The cache must hold the compiled *form*, never a computed value."""

    def test_same_expression_different_payloads(self):
        a = bytes([0] * 3 + [10] + [0] * 20)
        b = bytes([0] * 3 + [20] + [0] * 20)
        assert evaluate_expression("B3/2", a) == 5.0
        assert evaluate_expression("B3/2", b) == 10.0
        assert evaluate_expression("B3/2", a) == 5.0

    def test_same_expression_different_v(self):
        data = bytes(8)
        assert evaluate_expression("V+1", data, V=1.0) == 2.0
        assert evaluate_expression("V+1", data, V=9.0) == 10.0

    def test_expression_is_parsed_once(self):
        _compile_cached.cache_clear()
        data = bytes(range(16))
        for _ in range(50):
            evaluate_expression("(B03*256+B04)/10", data)
        info = _compile_cached.cache_info()
        assert info.misses == 1
        assert info.hits == 49

    def test_compiled_form_is_reusable(self):
        compiled = compile_expression("[B1:B2]/10")
        assert compiled(b"\x00\x01\xf4", 0.0) == 50.0
        assert compiled(b"\x00\x00\x64", 0.0) == 10.0

    def test_compile_rejects_invalid_eagerly(self):
        with pytest.raises(ValueError, match="Invalid character"):
            compile_expression("@bad")

    def test_failed_compiles_are_not_cached(self):
        # An uncacheable failure must resurface identically on every call, not
        # succeed-by-accident or turn into a different error.
        for _ in range(3):
            with pytest.raises(ValueError, match="Empty expression"):
                evaluate_expression("", b"")
