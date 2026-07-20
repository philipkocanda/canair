"""Output formatting helpers."""

import json

from rich.console import Console
from rich.text import Text

from .byteindex import extract_byte_indices, wican_to_elm_idx

_console = Console(highlight=False)

# Map a base byte colour → its highlighted (changed-byte) variant.
_HIGHLIGHT_STYLE = {
    "green": "bold white on dark_green",
    "yellow": "bold white on dark_goldenrod",
    "bright_black": "bold white on grey37",
}


def _bytes_to_ascii(raw_hex: str) -> str:
    """Convert hex string to ASCII representation (printable chars or '.')."""
    data = bytes.fromhex(raw_hex)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _build_byte_colors(params: list, n_bytes: int) -> list[str]:
    """Return a per-ELM-byte colour list based on parameter coverage and verification.

    Priority: green (covered by verified param) > yellow (covered by unverified) >
              bright_black (not covered by any expression).

    Args:
        params:   list of (name, value, unit, expression, error, verified[, display])
        n_bytes:  number of ELM payload bytes
    Returns:
        list of Rich colour strings, one per byte index.
    """
    # 0 = uncovered, 1 = unverified, 2 = verified
    rank = [0] * n_bytes
    for row in params:
        expression, perr, verified = row[3], row[4], row[5]
        if perr or not expression:
            continue
        level = 2 if verified else 1
        for wi in extract_byte_indices(expression):
            ei = wican_to_elm_idx(wi, n_bytes)
            if ei is not None and 0 <= ei < n_bytes:
                if level > rank[ei]:
                    rank[ei] = level
    color_map = {0: "bright_black", 1: "yellow", 2: "green"}
    return [color_map[r] for r in rank]


def param_byte_indices(expression: str, n_bytes: int) -> list[int]:
    """Return the ELM payload byte positions a WiCAN expression reads.

    Maps each WiCAN byte index in ``expression`` to its position in the raw
    payload hex (ISO-TP/ELM index), so the result lines up with the byte columns
    shown in the hex view. Out-of-range indices are dropped. Sorted ascending.
    """
    elm: set[int] = set()
    for wi in extract_byte_indices(expression):
        ei = wican_to_elm_idx(wi, n_bytes)
        if ei is not None and 0 <= ei < n_bytes:
            elm.add(ei)
    return sorted(elm)


def format_byte_ranges(indices: list[int]) -> str:
    """Collapse a sorted index list into compact ranges: ``[3,4,5,9] → '3-5,9'``."""
    if not indices:
        return ""
    parts: list[str] = []
    start = prev = indices[0]
    for i in indices[1:]:
        if i == prev + 1:
            prev = i
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = i
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(parts)


def param_byte_index_str(expression: str, n_bytes: int) -> str:
    """Human-readable byte reference for a parameter's expression.

    Valid payload positions are shown as compact ranges (e.g. ``16-17``). WiCAN
    indices that don't map to a payload byte — PCI/ISO-TP framing bytes, or
    indices beyond the payload — are flagged as ``⚠B<idx>`` (a common definition
    bug: reading a framing byte as data). Returns ``""`` if nothing is referenced.
    """
    valid: list[int] = []
    bad_wican: list[int] = []
    for wi in sorted(extract_byte_indices(expression)):
        ei = wican_to_elm_idx(wi, n_bytes)
        if ei is not None and 0 <= ei < n_bytes:
            valid.append(ei)
        else:
            bad_wican.append(wi)
    parts: list[str] = []
    if valid:
        parts.append(format_byte_ranges(sorted(set(valid))))
    if bad_wican:
        parts.append("⚠B" + ",".join(str(w) for w in bad_wican))
    return " ".join(parts)




def format_value(value: float, unit: str, display: str = "") -> str:
    """Format a decoded value with unit and optional display expression.

    If display is set, evaluates it as an f-string with v=value and appends
    the formatted result in parentheses: "480 min (08:00)".
    """
    if value == int(value):
        base = f"{int(value)} {unit}".strip()
    else:
        base = f"{value:.2f} {unit}".strip()
    if display:
        try:
            v = value  # noqa: F841 — used in eval
            formatted = eval(display)
            base = f"{base} ({formatted})"
        except Exception:
            pass  # silently skip broken display expressions
    return base


def _render_hex_line(
    raw_hex: str,
    params: list,
    unmapped: bool,
    *,
    prev_raw: str = "",
    prefix: str = "      ",
    prefix_style: str = "",
) -> Text:
    """Render a payload hex line as Rich Text with per-byte change highlighting.

    Bytes get a base colour from parameter coverage (green=verified, yellow=
    unverified, grey=uncovered); bytes differing from ``prev_raw`` get a
    highlighted background variant. Unmapped/paramless payloads render grey with
    a trailing ASCII column. ``prefix`` is prepended before the hex bytes.
    """
    elm_bytes = [raw_hex[i : i + 2] for i in range(0, len(raw_hex), 2)]
    prev_bytes = [prev_raw[i : i + 2] for i in range(0, len(prev_raw), 2)] if prev_raw else []
    n_bytes = len(elm_bytes)
    t = Text()
    t.append(prefix, style=prefix_style)

    if unmapped or not params:
        for i, hb in enumerate(elm_bytes):
            if i > 0:
                t.append(" ")
            changed = i < len(prev_bytes) and prev_bytes[i] != hb
            style = _HIGHLIGHT_STYLE["bright_black"] if changed else "bright_black"
            t.append(hb, style=style)
        ascii_repr = _bytes_to_ascii(raw_hex)
        t.append(f"  {ascii_repr}  ({n_bytes} B)", style="bright_black")
    else:
        byte_color = _build_byte_colors(params, n_bytes)
        for i, hb in enumerate(elm_bytes):
            if i > 0:
                t.append(" ")
            base = byte_color[i]
            changed = i < len(prev_bytes) and prev_bytes[i] != hb
            style = _HIGHLIGHT_STYLE.get(base, base) if changed else base
            t.append(hb, style=style)
        t.append(f"  ({n_bytes} B)", style="bright_black")

    t.append("\n")
    return t


def render_param_table(
    params: list,
    *,
    verbose: bool = False,
    indent: str = "      ",
    n_bytes: int | None = None,
) -> Text:
    """Render decoded parameter rows as an aligned Rich Text block.

    Each row is ``(name, value, unit, expression, error, verified[, display])``.
    Layout: ``{indent}{name}  {value}  ✓|?`` with columns aligned; ``verbose``
    appends the dimmed expression. Error rows show ``ERROR: <msg>`` in red.

    When ``n_bytes`` is given, a dimmed byte-index column is appended after the
    mark, showing which payload byte position(s) each parameter reads (aligned
    with the hex view's ruler). Returns an empty ``Text`` when there are no params.
    """
    t = Text()
    if not params:
        return t

    max_name = max(len(r[0]) for r in params)
    max_val = max(
        len(
            format_value(r[1], r[2], r[6] if len(r) > 6 else "")
            if r[1] is not None
            else "ERROR"
        )
        for r in params
    )

    # Precompute byte-index strings (and their column width) when requested.
    byte_strs: dict[int, str] = {}
    max_bytes_w = 0
    if n_bytes is not None:
        for idx, row in enumerate(params):
            byte_strs[idx] = param_byte_index_str(row[3], n_bytes)
        max_bytes_w = max((len(s) for s in byte_strs.values()), default=0)

    for idx, row in enumerate(params):
        name, value, unit, expression, perr, verified = row[:6]
        display = row[6] if len(row) > 6 else ""
        mark_style = "green" if verified else "yellow"
        mark_char = "✓" if verified else "?"
        if perr:
            t.append(f"{indent}{name:<{max_name}}  ")
            t.append(f"ERROR: {perr}\n", style="red")
            continue

        val_str = format_value(value, unit, display)
        t.append(f"{indent}{name:<{max_name}}  ")
        t.append(f"{val_str:<{max_val}}  ")
        t.append(mark_char, style=mark_style)
        if n_bytes is not None:
            byte_str = byte_strs.get(idx, "")
            pad = max_bytes_w if verbose else 0
            t.append(f"  {byte_str:<{pad}}", style="dim")
        if verbose:
            t.append(f"  {expression}\n", style="dim")
        else:
            t.append("\n")
    return t



def print_decoded_params(params_results: list, verbose: bool = False):
    """Print decoded parameter values in a compact aligned table.

    Args:
        params_results: list of (name, value, unit, expression, error, verified[, display])
    """
    if not params_results:
        print("  No parameters to display")
        return

    max_name = max(len(r[0]) for r in params_results)
    max_val = max(
        len(format_value(r[1], r[2], r[6] if len(r) > 6 else "") if r[1] is not None else "ERROR")
        for r in params_results
    )

    for row in params_results:
        name, value, unit, expression, error, verified = row[:6]
        display = row[6] if len(row) > 6 else ""
        v_mark = " " if verified else "?"
        if error:
            print(f"  {v_mark} {name:<{max_name}}  {'ERROR':<{max_val}}  !! {error}")
        else:
            val_str = format_value(value, unit, display)
            if verbose:
                print(f"  {v_mark} {name:<{max_name}}  {val_str:<{max_val}}  [{expression}]")
            else:
                print(f"  {v_mark} {name:<{max_name}}  {val_str}")


def print_ecu_results(
    ecu_label: str,
    pid_results: list,
    verbose: bool = False,
):
    """Print all PID results for an ECU in a grouped, compact layout.

    Args:
        ecu_label:   ECU name + TX ID, e.g. 'BCM (0x7A0)'.
        pid_results: list of dicts, each with:
            pid:      PID code string, e.g. '22C00B'
            params:   list of (name, value, unit, expression, error, verified) — may be empty
            raw_hex:  full response hex string (optional)
            error:    error string if the PID query failed (optional)
            decode:   UDS decode string for raw/unmapped PIDs (optional)
            unmapped: bool — True for PIDs not in YAML
        verbose:     If True, show expressions.
    """
    if not pid_results:
        return

    c = _console

    # ECU header
    c.print(f"\n  [bold cyan]{ecu_label}[/bold cyan]")

    for entry in pid_results:
        pid = entry["pid"]
        error = entry.get("error")
        params = entry.get("params", [])
        raw_hex = entry.get("raw_hex", "")
        decode = entry.get("decode")
        unmapped = entry.get("unmapped", False)

        # PID sub-header
        tag = " [dim](unmapped)[/dim]" if unmapped else ""
        if error:
            c.print(f"    [yellow]{pid}[/yellow]{tag}  [red]{error}[/red]")
            continue

        c.print(f"    [yellow]{pid}[/yellow]{tag}")

        # Decoded parameters — aligned columns (shared renderer).
        if params:
            c.print(render_param_table(params, verbose=verbose), end="")
        elif decode:
            c.print(f"      {decode}")

        # Raw hex line (shared renderer; no prev_raw → no change highlighting).
        if raw_hex:
            c.print(_render_hex_line(raw_hex, params, unmapped), end="")


def print_hexdump(data: bytes, prefix: str = "  "):
    """Print a hex dump of raw bytes with WiCAN byte indices.

    Data is ISO-TP payload (PCI bytes already stripped). The WiCAN Bnn indices
    account for PCI bytes that occupy positions 0-1 (first frame) and 8,16,24,...
    (consecutive frames).
    """
    from .byteindex import isotp_to_wican

    for row_start in range(0, len(data), 16):
        row_end = min(row_start + 16, len(data))
        hex_part = " ".join(f"{data[j]:02X}" for j in range(row_start, row_end))
        bnn_part = " ".join(
            f"B{isotp_to_wican(j):02d}" for j in range(row_start, row_end)
        )
        print(f"{prefix}Bnn:  {bnn_part}")
        print(f"{prefix}Hex:   {hex_part}")
        print()


def format_raw_with_bnn(data: bytes) -> str:
    """Format ISO-TP payload bytes with WiCAN Bnn index labels.

    Returns a string like: B02=62 B03=BC B04=03 B05=FD ...
    Helps avoid byte offset confusion when reading raw output.
    """
    from .byteindex import isotp_to_wican

    parts = []
    for i, b in enumerate(data):
        bnn = isotp_to_wican(i)
        parts.append(f"B{bnn:02d}={b:02X}")
    return " ".join(parts)


def decode_uds_response(data: bytes) -> str | None:
    """Return a human-readable one-line decode of a UDS response, or None."""
    if len(data) < 1:
        return None

    sid = data[0]

    # UDS control types (0x2F IOControl)
    CONTROL_TYPES = {
        0x00: "returnControlToECU",
        0x01: "resetToDefault",
        0x02: "freezeCurrentState",
        0x03: "shortTermAdjustment",
    }

    # 0x50-0x5F: DiagnosticSessionControl, ECUReset, SecurityAccess, etc.
    if sid == 0x50 and len(data) >= 2:
        session_names = {0x01: "default", 0x02: "programming", 0x03: "extended"}
        stype = data[1]
        name = session_names.get(stype, f"0x{stype:02X}")
        return f"DiagnosticSessionControl: {name} session"

    if sid == 0x51 and len(data) >= 2:
        reset_names = {0x01: "hardReset", 0x02: "keyOffOnReset", 0x03: "softReset"}
        rtype = data[1]
        name = reset_names.get(rtype, f"0x{rtype:02X}")
        return f"ECUReset: {name}"

    if sid == 0x67 and len(data) >= 2:
        level = data[1]
        if level % 2 == 1:  # odd = seed response
            seed_hex = data[2:].hex().upper()
            return f"SecurityAccess: level {level} seed = {seed_hex}"
        else:
            return f"SecurityAccess: level {level} key accepted"

    if sid == 0x62 and len(data) >= 3:
        did = (data[1] << 8) | data[2]
        payload_len = len(data) - 3
        return f"ReadDataByIdentifier: DID 0x{did:04X}, {payload_len} data bytes"

    if sid == 0x61 and len(data) >= 2:
        pid = data[1]
        payload_len = len(data) - 2
        return f"ReadDataByIdentifier (mfr): PID 0x{pid:02X}, {payload_len} data bytes"

    if sid == 0x6E and len(data) >= 3:
        did = (data[1] << 8) | data[2]
        return f"WriteDataByIdentifier: DID 0x{did:04X} accepted"

    if sid == 0x6F and len(data) >= 3:
        did = (data[1] << 8) | data[2]
        ctrl = data[3] if len(data) >= 4 else None
        ctrl_name = CONTROL_TYPES.get(ctrl, f"0x{ctrl:02X}") if ctrl is not None else "?"
        status = data[4:].hex().upper() if len(data) > 4 else ""
        result = f"IOControl: DID 0x{did:04X}, {ctrl_name}"
        if status:
            result += f", status={status}"
        return result

    if sid == 0x71 and len(data) >= 4:
        rtype = data[1]
        rid = (data[2] << 8) | data[3]
        type_names = {0x01: "start", 0x02: "stop", 0x03: "requestResults"}
        name = type_names.get(rtype, f"0x{rtype:02X}")
        return f"RoutineControl: {name} routine 0x{rid:04X}"

    if sid == 0x59 and len(data) >= 2:
        sub = data[1]
        sub_names = {
            0x01: "reportNumberOfDTCByStatusMask",
            0x02: "reportDTCByStatusMask",
            0x03: "reportDTCSnapshotIdentification",
            0x04: "reportDTCSnapshotRecordByDTCNumber",
            0x06: "reportDTCExtendedDataRecordByDTCNumber",
            0x09: "reportSeverityInformationOfDTC",
            0x0A: "reportSupportedDTC",
            0x0B: "reportFirstTestFailedDTC",
            0x0E: "reportMostRecentConfirmedDTC",
        }
        name = sub_names.get(sub, f"subFunction 0x{sub:02X}")
        dtc_count = len(data) - 2
        return f"ReadDTCInformation: {name}, {dtc_count} data bytes"

    if sid == 0x63 and len(data) >= 2:
        addr_len = len(data) - 1
        return f"ReadMemoryByAddress: {addr_len} bytes returned"

    if sid == 0x74 and len(data) >= 2:
        fmt = data[1]
        mem_len = (fmt >> 4) & 0xF
        addr_len = fmt & 0xF
        return f"WARNING: RequestDownload ACCEPTED — ECU ready to receive firmware (addrLen={addr_len}, memLen={mem_len})"

    if sid == 0x75 and len(data) >= 2:
        fmt = data[1]
        mem_len = (fmt >> 4) & 0xF
        addr_len = fmt & 0xF
        return f"WARNING: RequestUpload ACCEPTED — ECU ready to send memory (addrLen={addr_len}, memLen={mem_len})"

    if sid == 0x76 and len(data) >= 2:
        seq = data[1]
        payload_len = len(data) - 2
        return f"WARNING: TransferData — block {seq}, {payload_len} bytes being transferred"

    if sid == 0x77:
        return "WARNING: TransferExit — firmware transfer completed"

    if sid == 0x68 and len(data) >= 3:
        did = (data[1] << 8) | data[2]
        return f"WARNING: ControlDTCSetting — DID 0x{did:04X}, DTC logging may be altered"

    if sid == 0x6C and len(data) >= 2:
        sub = data[1]
        sub_names = {
            0x01: "enableRxAndTx",
            0x02: "enableRxAndDisableTx",
            0x03: "disableRxAndTx",
        }
        name = sub_names.get(sub, f"0x{sub:02X}")
        return f"CommunicationControl: {name}"

    if sid == 0x7E:
        return "TesterPresent: acknowledged"

    return None


def print_json_result(result: dict):
    """Print result as JSON for machine consumption."""
    out = {}
    for k, v in result.items():
        if isinstance(v, bytes):
            out[k] = v.hex().upper()
        else:
            out[k] = v
    print(json.dumps(out, indent=2))
