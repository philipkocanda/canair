"""CAN/UDS library for WiCAN ELM327 terminal communication."""

from .constants import SCRIPT_DIR
from .decoding import decode_param_rows
from .elm327 import (
    BLOCKED_UDS_SERVICES,
    NRC_ABBREV,
    NRC_CODES,
    check_command_safety,
    elm_hex_to_wican_bytes,
    nrc_abbrev,
    parse_elm_response,
)
from .formatting import (
    format_byte_ranges,
    format_value,
    param_byte_index_str,
    param_byte_indices,
    print_decoded_params,
    print_ecu_results,
    print_hexdump,
    print_json_result,
    render_byte_rulers,
    render_param_table,
)
from .ecus import (
    build_name_tx_index,
    build_rx_index,
    ecu_name,
    ecu_name_from_ref,
    load_ecus,
    parse_ecu_ref,
    rx_addr_str,
    rx_from_name,
)
from .log import init_logging, log_command, log_response
from .pids import build_ecu_index, build_param_index, load_pids
from .terminal import WiCANTerminal, reboot_wican

__all__ = [
    "BLOCKED_UDS_SERVICES",
    "DEFAULT_WICAN",
    "NRC_ABBREV",
    "NRC_CODES",
    "SCRIPT_DIR",
    "WICAN_ADDRESSES",
    "WiCANTerminal",
    "build_ecu_index",
    "build_name_tx_index",
    "build_param_index",
    "build_rx_index",
    "check_command_safety",
    "decode_param_rows",
    "ecu_name",
    "ecu_name_from_ref",
    "elm_hex_to_wican_bytes",
    "format_byte_ranges",
    "format_value",
    "init_logging",
    "load_ecus",
    "load_pids",
    "log_command",
    "log_response",
    "nrc_abbrev",
    "param_byte_index_str",
    "param_byte_indices",
    "parse_ecu_ref",
    "parse_elm_response",
    "print_decoded_params",
    "print_ecu_results",
    "print_hexdump",
    "print_json_result",
    "reboot_wican",
    "render_byte_rulers",
    "render_param_table",
    "rx_addr_str",
    "rx_from_name",
]


def __getattr__(name):
    """Lazily expose profile/config-dependent constants (PEP 562)."""
    if name in ("DEFAULT_WICAN", "WICAN_ADDRESSES", "PIDS_DIR", "ECUS_FILE", "CAPTURES_DIR"):
        from . import constants

        return getattr(constants, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
