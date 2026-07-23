"""IOControl status query helper.

Queries the PID parameter mapped to an IOControl DID (via status_param) and
returns its current evaluated value. Used by the IOControl TUI and execute mode
to show a live status column alongside each actuator.
"""

from ..expression import evaluate_expression
from ..pids import build_param_index
from ..terminal import WiCANTerminal
from ..wican_bytes import uds_hex_to_wican_bytes


async def query_param_status(
    terminal: WiCANTerminal,
    pids_data: dict,
    param_name: str,
    verbose: bool = False,
) -> dict:
    """Query the current value of a named parameter from the car.

    Looks up the parameter's ECU, PID, and expression from pids_data, sends
    the UDS request, and evaluates the expression on the response.

    Returns a dict with keys:
        param   (str)   parameter name
        value   (float | None)  evaluated value, or None on error
        unit    (str)   unit string (may be empty)
        error   (str | None)  error description if value is None
    """
    param_index = build_param_index(pids_data)
    key = param_name.upper()

    if key not in param_index:
        return {"param": key, "value": None, "unit": "", "error": f"unknown param {key!r}"}

    pinfo = param_index[key]
    tx_id = pinfo["tx_id"]
    pid = pinfo["pid"]
    expression = pinfo["expression"]
    unit = pinfo.get("unit", "")

    await terminal.set_header(tx_id)

    response = await terminal.send_uds(pid)

    if not response["ok"]:
        nrc = response.get("nrc")
        if nrc is not None:
            error = f"NRC 0x{nrc:02X}: {response.get('nrc_desc', '')}"
        else:
            error = response.get("error", "no response")
        return {"param": key, "value": None, "unit": unit, "error": error}

    try:
        wican_bytes = uds_hex_to_wican_bytes(response["hex"])
        if verbose:
            print(f"    [status] {key}: raw={response['hex']}  bytes={wican_bytes.hex().upper()}")
        value = evaluate_expression(expression, wican_bytes)
        value = round(value * 100) / 100
        return {"param": key, "value": value, "unit": unit, "error": None}
    except Exception as exc:
        return {"param": key, "value": None, "unit": unit, "error": str(exc)}


def format_status_value(result: dict) -> str:
    """Format a status query result as a short display string.

    Examples:
        "1"        (bit flag, on)
        "0"        (bit flag, off)
        "45 km/h"  (with unit)
        "ERR: NRC 0x31"  (on error)
    """
    if result["error"]:
        # Shorten error for TUI column
        err = result["error"]
        if len(err) > 20:
            err = err[:17] + "..."
        return f"ERR: {err}"

    value = result["value"]
    unit = result.get("unit", "")

    if unit:
        return f"{value} {unit}"
    return str(int(value)) if value == int(value) else str(value)
