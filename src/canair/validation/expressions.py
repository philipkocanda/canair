"""Signal expression checks: syntax, the structural lint, the firmware grammar and PCI bytes."""

import re

from canair.expression.decode_value import BYTE_RUN_TYPES as _BYTE_RUN_TYPES
from canair.expression.lint import firmware_grammar_problems, signed_low_bytes
from canair.uds.byteindex import wican_to_isotp

from ._common import EXPR_TOKEN_RE


def validate_expression(expr: str, param_name: str, pid: str, ecu: str) -> list[str]:
    """Expression syntax check, plus the structural lint (:mod:`canair.expression.lint`)."""
    errors = []
    if not expr or not expr.strip():
        errors.append(f"{ecu}/{pid}/{param_name}: empty expression")
        return errors

    # Check for completely unrecognizable tokens
    cleaned = EXPR_TOKEN_RE.sub("", expr)
    if cleaned.strip():
        errors.append(
            f"{ecu}/{pid}/{param_name}: suspicious expression chars: '{cleaned.strip()}' in '{expr}'"
        )
    for low, top in signed_low_bytes(expr):
        errors.append(
            f"{ecu}/{pid}/{param_name}: '{low}' is a lower byte of a word whose top byte is "
            f"'{top}' but is read signed — only the top byte of a multi-byte integer carries "
            f"the sign; use '{low.replace('S', 'B')}' in '{expr}'"
        )
    return errors


def firmware_expression_errors(expr: str, param_name: str, pid: str, ecu: str) -> list[str]:
    """A shipped expression WiCAN firmware v6 refuses, which would sink its whole table."""
    return [
        f"{ecu}/{pid}/{param_name}: WiCAN firmware v6 refuses '{expr}': {reason}, and would "
        f"reject the whole AutoPID table"
        for reason in firmware_grammar_problems(expr)
    ]


def check_pci_bytes(
    expr: str, param_name: str, pid: str, ecu: str, param_type: str | None = None
) -> list[str]:
    """Warn if an expression reads ISO-TP PCI bytes.

    PCI (frame-header) bytes live at WiCAN indices 0, 1, 8, 16, 24, 32, ...
    (``wican_to_isotp`` returns None for them). Reading one yields the frame
    counter/length, not real data — a common byte-index mistake. This also
    flags multi-byte ranges ``[Bnn:Bmm]`` that *span* a PCI byte, since those
    read consecutive raw bytes without skipping PCI.

    ``param_type`` exempts the range form for the **byte-run** types
    (``ascii``/``date``): there the range delimits a run of text/date bytes rather
    than a scalar, and the decoder drops the framing bytes it spans (see
    :func:`canair.expression.decode_value._data_run`). A 17-char VIN necessarily crosses a
    frame boundary, so flagging it would be flagging the only correct spelling.
    """
    warnings = []
    run_type = (param_type or "").lower() in _BYTE_RUN_TYPES

    def is_pci(idx: int) -> bool:
        return wican_to_isotp(idx) is None

    # Multi-byte ranges: [Bnn:Bmm] / [Snn:Smm] — flag if any index in range is PCI
    ranges = []
    for m in re.finditer(r"\[[BS](\d+):[BS](\d+)\]", expr):
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        ranges.append((m.span(), lo, hi))
        pci = [x for x in range(lo, hi + 1) if is_pci(x)]
        if pci and not run_type:
            warnings.append(
                f"{ecu}/{pid}/{param_name}: range '{m.group(0)}' spans ISO-TP PCI byte(s) "
                f"{', '.join(f'B{x}' for x in pci)} — reads frame header, not data"
            )

    # Single byte refs: Bnn, Snn, Bnn:k — skip those inside a flagged range
    for m in re.finditer(r"[BS](\d+)", expr):
        idx = int(m.group(1))
        if any(start <= m.start() < end for (start, end), _, _ in ranges):
            continue
        if is_pci(idx):
            warnings.append(
                f"{ecu}/{pid}/{param_name}: reads ISO-TP PCI byte B{idx} "
                f"(frame header, not data) in '{expr}'"
            )
    return warnings
