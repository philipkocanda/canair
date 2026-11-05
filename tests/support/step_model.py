"""A StepModel builder for the captures stepper tests."""

from __future__ import annotations

from canair.tui.apps.captures_step.model import StepModel

PARAMS = {
    "P_STATIC": {"expression": "B04", "unit": "", "verified": True},
    "P_MOVING": {"expression": "B06", "unit": "A", "verified": False},
}


def _entry(**kw) -> dict:
    """A flat capture entry as ``load_all_captures`` would produce."""
    base = {
        "file": "2026-08-02.json",
        "date": "2026-08-02",
        "session_label": "",
        "vehicle_states": [],
        "session_notes": "",
        "ecu": "HVAC",
        "ecu_addr": "0x7BB",
        "pid": "220100",
        "payload": "6201005A6414",
        "response": None,
        "scan_results": None,
        "notes": "",
        "time": "12:00:00",
        "label": "",
        "_session_idx": 0,
        "_capture_idx": 0,
    }
    base.update(kw)
    # load_all_captures always locates a row; a bare name keeps these assertions short.
    base.setdefault("_path", base["file"])
    return base


def model(*, keys=None, entries=None, **kw) -> StepModel:
    entries = entries if entries is not None else _three_pid_entries()
    keys = keys or sorted({(e["ecu"], str(e["pid"])) for e in entries})
    defs = dict.fromkeys(keys, (PARAMS, 0x7B3))
    kw.setdefault("captures_dir", None)
    return StepModel.from_entries(entries, keys, defs, **kw)


def _three_pid_entries() -> list[dict]:
    """Three HVAC PIDs polled round-robin over two cycles ~1s apart."""
    out = []
    for cycle, base in enumerate(("12:00:0", "12:01:0")):
        for n, pid in enumerate(("220100", "2201A0", "2201A2")):
            out.append(
                _entry(
                    pid=pid,
                    time=f"{base}{n}",
                    payload=f"6201005A64{cycle}{n}",
                    _capture_idx=cycle * 3 + n,
                )
            )
    return out
