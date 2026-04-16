"""Output formatting helpers."""

import json


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
    max_val = max(len(format_value(r[1], r[2]) if r[1] is not None else "ERROR") for r in params_results)

    for name, value, unit, expression, error, verified in params_results:
        v_mark = " " if verified else "?"
        if error:
            print(f"  {v_mark} {name:<{max_name}}  {'ERROR':<{max_val}}  !! {error}")
        else:
            val_str = format_value(value, unit)
            if verbose:
                print(f"  {v_mark} {name:<{max_name}}  {val_str:<{max_val}}  [{expression}]")
            else:
                print(f"  {v_mark} {name:<{max_name}}  {val_str}")


def print_pid_table(pid_code: str, ecu_label: str, params_results: list,
                    raw_hex: str, verbose: bool = False):
    """Print a PID response as a bordered ASCII table.

    Args:
        pid_code:      PID identifier string, e.g. '22BC03'.
        ecu_label:     ECU name + TX ID, e.g. 'IGPM (0x770)'.
        params_results: list of (name, value, unit, expression, error, verified).
                        May be empty for unmapped PIDs.
        raw_hex:       Full response hex string, e.g. '62BC030000...'.
        verbose:       If True, include expression column.
    """
    # --- Column widths ---
    COL_V = 1        # verified marker
    COL_SEP = "  "   # between columns

    if params_results:
        col_name = max(len(r[0]) for r in params_results)
        col_val  = max(len(format_value(r[1], r[2]) if r[1] is not None else "ERROR")
                       for r in params_results)
    else:
        col_name = 0
        col_val  = 0

    # Raw bytes as grouped hex (space every byte)
    raw_bytes_str = " ".join(raw_hex[i:i+2] for i in range(0, len(raw_hex), 2))
    n_bytes = len(raw_hex) // 2
    raw_line = f"Raw  {raw_bytes_str}  ({n_bytes} bytes)"

    # Header title
    title = f" {ecu_label} · {pid_code} "

    # Build row strings (before boxing) to determine table width
    rows = []
    if params_results:
        if verbose:
            header = (f" {'Parameter':<{col_name}}{COL_SEP}{'Value':<{col_val}}"
                      f"{COL_SEP}{'V':<{COL_V}}{COL_SEP}{'Expression'} ")
        else:
            header = f" {'Parameter':<{col_name}}{COL_SEP}{'Value':<{col_val}}{COL_SEP}V "

        rows.append(header)
        rows.append(None)  # separator after header

        for name, value, unit, expression, error, verified in params_results:
            v_mark = "✓" if verified else "?"
            if error:
                val_str = "ERROR"
                suffix = f"  !! {error} "
            else:
                val_str = format_value(value, unit)
                suffix = (f"  [{expression}] " if verbose else " ")
            row = f" {name:<{col_name}}{COL_SEP}{val_str:<{col_val}}{COL_SEP}{v_mark}{suffix}"
            rows.append(row)

    rows.append(None)  # separator before raw line
    rows.append(f" {raw_line} ")  # trailing space for right-side breathing room

    # Table width = max of (title, all rows) + 2 for │ borders
    inner_width = max(
        len(title),
        max((len(r) for r in rows if r is not None), default=0),
    )
    width = inner_width + 2  # +2 for │ on each side

    top  = f"  ┌─{title}{'─' * (width - 2 - len(title))}┐"
    mid  = f"  ├{'─' * (width - 2)}┤"
    bot  = f"  └{'─' * (width - 2)}┘"

    print(top)
    for row in rows:
        if row is None:
            print(mid)
        else:
            padding = width - 2 - len(row)
            print(f"  │{row}{' ' * padding}│")
    print(bot)


def print_hexdump(data: bytes, prefix: str = "  "):
    """Print a hex dump of raw bytes."""
    for row_start in range(0, len(data), 16):
        row_end = min(row_start + 16, len(data))
        hex_part = " ".join(f"{data[j]:02X}" for j in range(row_start, row_end))
        idx_part = " ".join(f"{j:2d}" for j in range(row_start, row_end))
        print(f"{prefix}Idx:  {idx_part}")
        print(f"{prefix}Hex:  {hex_part}")
        print()


def print_json_result(result: dict):
    """Print result as JSON for machine consumption."""
    out = {}
    for k, v in result.items():
        if isinstance(v, bytes):
            out[k] = v.hex().upper()
        else:
            out[k] = v
    print(json.dumps(out, indent=2))
