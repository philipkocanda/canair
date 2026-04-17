"""WiCAN expression evaluator.

Faithful Python port of wican-fw/main/expression_parser.c evaluate_expression().
"""

import re


def evaluate_expression(expression: str, data: bytes, V: float = 0.0) -> float:
    """Evaluate a WiCAN expression against a byte array.

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

        # Whitespace
        if ch == " ":
            i += 1
            continue

        # Numeric literal
        if ch.isdigit() or (ch == "." and i + 1 < len(expr) and expr[i + 1].isdigit()):
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            operand_stack.append(float(expr[i:j]))
            i = j
            continue

        # V (external value)
        if ch == "V" and (i + 1 >= len(expr) or not expr[i + 1].isalnum()):
            operand_stack.append(V)
            i += 1
            continue

        # Multi-byte range: [Bn:Bm] or [Sn:Sm]
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
                # Sign-extend based on byte count (matching firmware logic)
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
                operand_stack.append(float((data[idx] >> bit) & 1))
            else:
                operand_stack.append(float(data[idx]))
            continue

        # Signed byte: Sn
        if ch == "S":
            i += 1
            idx = 0
            while i < len(expr) and expr[i].isdigit():
                idx = idx * 10 + int(expr[i])
                i += 1
            val = data[idx]
            operand_stack.append(float(val if val < 128 else val - 256))
            continue

        # Parentheses
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

        # Operators
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

    # Final reduction
    while operator_stack:
        op = operator_stack.pop()
        b = operand_stack.pop()
        a = operand_stack.pop()
        operand_stack.append(apply_op(op, a, b))

    if not operand_stack:
        raise ValueError(f"Empty expression: {expression}")
    return operand_stack[0]
