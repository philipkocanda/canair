"""DTC mode — read and clear Diagnostic Trouble Codes across UDS and KWP2000.

Many vehicles mix two diagnostic protocols across their ECUs, so DTC access is
protocol-aware (auto-selected from the profile's ``id_protocol`` registry, like
``identity``):

* **UDS** ECUs (BCM, IGPM, ...) use ReadDTCInformation ``0x19`` subfunction
  ``0x02`` (reportDTCByStatusMask) and ClearDiagnosticInformation ``0x14`` with a
  3-byte groupOfDTC. DTC records are 4 bytes (3-byte DTC + 1-byte status).
* **KWP2000** ECUs (BMS, VCU, MCU, LDC, ...) use readDiagnosticTroubleCodesBy
  Status ``0x18`` and clearDiagnosticInformation ``0x14`` with a 2-byte group.
  DTC records are 3 bytes (2-byte DTC + 1-byte status).

Clearing mutates ECU fault memory; the caller confirms intent before invoking.

The package is split by concern:

* :mod:`.codes` — DTC formatting and record decoding (pure)
* :mod:`.reading` — one ECU's ``19 02`` / ``18`` read, with the request fallbacks
* :mod:`.read` — ``canair dtc ECU``
* :mod:`.sweep` — ``canair dtc --all``
* :mod:`.clear` — ``canair dtc ECU --clear``
* :mod:`.scan_log` — recording scans and clears to the DTC log

Belongs: reading and clearing a car's fault memory over a ``Terminal``.
Not here: the DTC log, meanings and history (``canair.dtc``) and the flags (``cli/commands/dtc``).
Imports: ``dtc`` (the data), ``transport`` (the protocol only), ``safety`` and ``uds``.
"""

from .clear import mode_dtc_clear
from .codes import (
    decode_dtc_records,
    decode_kwp_dtc_records,
    decode_status,
    format_dtc,
    format_kwp_dtc,
    resolve_protocol,
)
from .read import mode_dtc_read
from .reading import read_ecu_dtcs
from .sweep import mode_dtc_scan_all

__all__ = [
    "decode_dtc_records",
    "decode_kwp_dtc_records",
    "decode_status",
    "format_dtc",
    "format_kwp_dtc",
    "mode_dtc_clear",
    "mode_dtc_read",
    "mode_dtc_scan_all",
    "read_ecu_dtcs",
    "resolve_protocol",
]
