"""Output formatting helpers."""

import json
import re

from rich.console import Console

_console = Console(highlight=False)


def _bytes_to_ascii(raw_hex: str) -> str:
    """Convert hex string to ASCII representation (printable chars or '.')."""
    data = bytes.fromhex(raw_hex)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _extract_byte_indices(expression: str) -> set[int]:
    """Extract all byte indices referenced in a WiCAN expression.

    Patterns: B03, S03, B03:0 (bit), [B04:B05] (range), [S04:S05] (range).
    """
    indices = set()
    # Multi-byte ranges: [B04:B05] or [S04:S05]
    for m in re.finditer(r"\[([BS])(\d+):([BS])(\d+)\]", expression):
        lo, hi = int(m.group(2)), int(m.group(4))
        indices.update(range(lo, hi + 1))
    # Single byte: B03, S03, B03:0 (bit access)
    for m in re.finditer(r"(?<!\[)([BS])(\d+)(?::\d+)?(?!\d)", expression):
        indices.add(int(m.group(2)))
    return indices


def _wican_idx_to_elm_idx(wican_idx: int, payload_len: int) -> int | None:
    """Map a WiCAN AutoPID byte index to an ELM payload byte index.

    WiCAN layout has PCI bytes at positions 0, 8, 16, 24, ...
    For single-frame (payload <= 7): PCI at 0, data at 1..7.
    For multi-frame: FF PCI at 0-1, data at 2-7, then CF PCI at 8, data 9-15, etc.
    Returns None if the index points to a PCI byte.
    """
    if payload_len <= 7:
        # Single frame: [PCI] [d d d d d d d]
        if wican_idx == 0:
            return None  # PCI byte
        return wican_idx - 1
    else:
        # Multi-frame
        frame = wican_idx // 8
        pos_in_frame = wican_idx % 8
        if frame == 0:
            # First frame: [PCI_hi PCI_lo] [d d d d d d]
            if pos_in_frame < 2:
                return None  # PCI
            return pos_in_frame - 2
        else:
            # Consecutive frame: [PCI] [d d d d d d d]
            if pos_in_frame == 0:
                return None  # PCI
            return 6 + (frame - 1) * 7 + (pos_in_frame - 1)


def format_value(value: float, unit: str) -> str:
    """Format a decoded value with unit."""
    if value == int(value):
        return f"{int(value)} {unit}".strip()
    return f"{value:.2f} {unit}".strip()


def print_decoded_params(params_results: list, verbose: bool = False):
    """Print decoded parameter values in a compact aligned table.

    Args:
        params_results: list of (name, value, unit, expression, error, verified)
    """
    if not params_results:
        print("  No parameters to display")
        return

    max_name = max(len(r[0]) for r in params_results)
    max_val = max(
        len(format_value(r[1], r[2]) if r[1] is not None else "ERROR")
        for r in params_results
    )

    for name, value, unit, expression, error, verified in params_results:
        v_mark = " " if verified else "?"
        if error:
            print(f"  {v_mark} {name:<{max_name}}  {'ERROR':<{max_val}}  !! {error}")
        else:
            val_str = format_value(value, unit)
            if verbose:
                print(
                    f"  {v_mark} {name:<{max_name}}  {val_str:<{max_val}}  [{expression}]"
                )
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

        # Decoded parameters — aligned columns
        if params:
            max_name = max(len(r[0]) for r in params)
            max_val = max(
                len(format_value(r[1], r[2]) if r[1] is not None else "ERROR")
                for r in params
            )
            for name, value, unit, expression, perr, verified in params:
                mark = "[green]✓[/green]" if verified else "[yellow]?[/yellow]"
                if perr:
                    val_str = f"[red]ERROR: {perr}[/red]"
                    c.print(f"      {name:<{max_name}}  {val_str}")
                else:
                    val_str = format_value(value, unit)
                    if verbose:
                        c.print(
                            f"      {name:<{max_name}}  {val_str:<{max_val}}  {mark}  [dim]{expression}[/dim]"
                        )
                    else:
                        c.print(
                            f"      {name:<{max_name}}  {val_str:<{max_val}}  {mark}"
                        )
        elif decode:
            c.print(f"      {decode}")

        # Raw hex line
        if raw_hex:
            n_bytes = len(raw_hex) // 2
            elm_bytes = [raw_hex[i : i + 2] for i in range(0, len(raw_hex), 2)]

            if unmapped or not params:
                # Unmapped: all dim hex + ASCII
                spaced = " ".join(elm_bytes)
                ascii_repr = _bytes_to_ascii(raw_hex)
                c.print(f"      [dim]{spaced}  {ascii_repr}  ({n_bytes} B)[/dim]")
            else:
                # Mapped: find which ELM payload bytes are covered by expressions
                covered_elm = set()
                for _, _, _, expression, perr, _ in params:
                    if perr or not expression:
                        continue
                    wican_indices = _extract_byte_indices(expression)
                    for wi in wican_indices:
                        ei = _wican_idx_to_elm_idx(wi, n_bytes)
                        if ei is not None and 0 <= ei < n_bytes:
                            covered_elm.add(ei)

                # Build hex with uncovered bytes highlighted
                hex_parts = []
                for i, hb in enumerate(elm_bytes):
                    if i in covered_elm:
                        hex_parts.append(f"[dim]{hb}[/dim]")
                    else:
                        hex_parts.append(f"[bold]{hb}[/bold]")
                c.print(f"      {' '.join(hex_parts)}  [dim]({n_bytes} B)[/dim]")


def print_hexdump(data: bytes, prefix: str = "  "):
    """Print a hex dump of raw bytes."""
    for row_start in range(0, len(data), 16):
        row_end = min(row_start + 16, len(data))
        hex_part = " ".join(f"{data[j]:02X}" for j in range(row_start, row_end))
        idx_part = " ".join(f"{j:2d}" for j in range(row_start, row_end))
        print(f"{prefix}Idx:  {idx_part}")
        print(f"{prefix}Hex:  {hex_part}")
        print()


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
        ctrl_name = (
            CONTROL_TYPES.get(ctrl, f"0x{ctrl:02X}") if ctrl is not None else "?"
        )
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
        return (
            f"WARNING: ControlDTCSetting — DID 0x{did:04X}, DTC logging may be altered"
        )

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
