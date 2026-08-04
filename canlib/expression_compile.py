"""WiCAN expression parser — text in, closure tree out.

A faithful shunting-yard port of the scan loop in
wican-fw/main/expression_parser.c, differing from it in one way: instead of
evaluating operands and applying operators as it scans, it *emits* the closure
nodes of :mod:`canlib.expression_nodes`. The control flow — token recognition,
precedence, associativity, the tolerance of malformed input — is otherwise
unchanged, which is what makes the compiled tree decode identically to the
firmware.

Everything here happens once per distinct expression string;
:func:`canlib.expression.evaluate_expression` caches the result.
"""

import re

from . import expression_nodes as nodes
from .expression_nodes import CompiledExpression

_RANGE_UNSIGNED_RE = re.compile(r"\[B(\d+):B(\d+)\]")
_RANGE_SIGNED_RE = re.compile(r"\[S(\d+):S(\d+)\]")

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


def compile_expression(expression: str) -> CompiledExpression:
    """Parse a WiCAN expression once into a callable ``(data, V) -> float``.

    Everything that does not depend on ``data`` — the character scan, byte-index
    reconstruction, the range regexes, operator precedence — happens here; the
    returned closure tree does only byte loads and arithmetic. The supported
    syntax is documented on :func:`canlib.expression.evaluate_expression`.

    Malformed input raises the same exception it always did, but a *parse* error
    now surfaces here rather than at the point the scan reached it. For input that
    is both unparseable and evaluates a bad operand (an out-of-range byte, a
    divide by zero), the parse error therefore wins where the old scan-as-you-go
    evaluator reported the payload-dependent one — which was never stable, since
    it depended on the payload. Anything that compiles decodes identically.
    Because :func:`canlib.expression.evaluate_expression` caches successful
    compiles only, a caller still sees an identical exception on every call.
    """
    operand_stack: list[CompiledExpression] = []
    operator_stack: list[str] = []

    def emit(op: str) -> None:
        # Operands are popped before the operator is recognised, so unbalanced
        # input (a stray ``(`` reaching the final reduction) underflows the
        # operand stack with an IndexError exactly as it always did.
        b = operand_stack.pop()
        a = operand_stack.pop()
        factory = nodes.BINARY_NODES.get(op)
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
            operand_stack.append(nodes.const(float(expr[i:j])))
            i = j
            continue

        # V (external value)
        if ch == "V" and (i + 1 >= len(expr) or not expr[i + 1].isalnum()):
            operand_stack.append(nodes.external_value())
            i += 1
            continue

        # Multi-byte range: [Bn:Bm] or [Sn:Sm]
        if ch == "[":
            m_unsigned = _RANGE_UNSIGNED_RE.match(expr, i)
            if m_unsigned:
                operand_stack.append(
                    nodes.unsigned_range(int(m_unsigned.group(1)), int(m_unsigned.group(2)))
                )
                i = m_unsigned.end()
                continue

            m_signed = _RANGE_SIGNED_RE.match(expr, i)
            if m_signed:
                operand_stack.append(
                    nodes.signed_range(int(m_signed.group(1)), int(m_signed.group(2)))
                )
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
                bit_index = int(expr[i])
                i += 1
                operand_stack.append(nodes.bit(idx, bit_index))
            else:
                operand_stack.append(nodes.unsigned_byte(idx))
            continue

        # Signed byte: Sn
        if ch == "S":
            i += 1
            idx = 0
            while i < len(expr) and expr[i].isdigit():
                idx = idx * 10 + int(expr[i])
                i += 1
            operand_stack.append(nodes.signed_byte(idx))
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
    return nodes.with_discarded(root, tuple(discarded)) if discarded else root
