"""Output formatting helpers."""

import json

from rich.console import Console
from rich.table import Table
from rich import box

_console = Console(highlight=False)
# Narrow console for tables — prevents Rich from expanding to full terminal width
_table_console = Console(highlight=False, width=100)


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


def print_pid_table(
    pid_code: str,
    ecu_label: str,
    params_results: list,
    raw_hex: str,
    verbose: bool = False,
):
    """Print a PID response as a Rich table.

    Args:
        pid_code:       PID identifier string, e.g. '22BC03'.
        ecu_label:      ECU name + TX ID, e.g. 'IGPM (0x770)'.
        params_results: list of (name, value, unit, expression, error, verified).
                        May be empty for unmapped PIDs.
        raw_hex:        Full response hex string, e.g. '62BC030000...'.
        verbose:        If True, include expression column.
    """
    title = f"{ecu_label} · {pid_code}"
    raw_bytes_str = " ".join(raw_hex[i : i + 2] for i in range(0, len(raw_hex), 2))
    n_bytes = len(raw_hex) // 2

    if params_results:
        table = Table(
            title=title,
            box=box.SIMPLE_HEAD,
            show_header=True,
            title_justify="left",
            padding=(0, 1),
            expand=False,
        )
        max_name_len = max(len(r[0]) for r in params_results)
        table.add_column(
            "Parameter",
            style="bold",
            no_wrap=True,
            min_width=max_name_len,
            max_width=max_name_len,
        )
        table.add_column("Value", no_wrap=True)
        table.add_column("V", justify="center", no_wrap=True)
        if verbose:
            table.add_column("Expression", style="dim", no_wrap=True)

        for name, value, unit, expression, error, verified in params_results:
            v_mark = "✓" if verified else "[yellow]?[/yellow]"
            if error:
                val_str = f"[red]ERROR: {error}[/red]"
            else:
                val_str = format_value(value, unit)
            row = [name, val_str, v_mark]
            if verbose:
                row.append(expression if not error else "")
            table.add_row(*row)

        _table_console.print(table)
    else:
        _table_console.print(f"[bold]{title}[/bold]")

    # Raw line always printed separately — not squeezed by any column max_width
    _table_console.print(f"  [dim]raw  {raw_bytes_str}  ({n_bytes} bytes)[/dim]")
    _table_console.print()


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
