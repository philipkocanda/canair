"""Export a profile's definitions as a plain, JSON-able dict for the docs explorer.

The explorer page (``docs/profiles/explorer.md``) is static, so everything it shows is
derived here at docs build time. This module is deliberately pure — a profile directory
in, a dict out — so the content rules live in pytest and the JavaScript only renders.

Two rules shape it:

- **Allow-list, not a dump.** Every field is copied by name, so a new profile field never
  reaches the public site by accident. In particular the ``identity:`` block (live VIN,
  serial and part numbers) is never exported: only its free-text ``description`` and the
  confidence flag are.
- **Every record carries its source line.** A plain ``yaml.safe_load`` drops line numbers,
  so the files are read with ruamel (round-trip mode) and the line comes from each node.

Plan: ``plans/2026-10-21-profile-explorer.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .addressing import resolve_mode, resolve_rx, resolve_rx_offset
from .ecu_files import iter_ecu_files
from .explorer_layout import layout_buses
from .pids import pid_status
from .yaml_rt import round_trip_yaml

DEFAULT_BLOB_BASE = "https://github.com/philipkocanda/canair/blob/main/"

SIGNAL_FIELDS = ("expression", "unit", "min", "max", "type", "verified", "notes", "source")
BROADCAST_SIGNAL_FIELDS = (
    "start_bit",
    "length",
    "byte_order",
    "signed",
    "scale",
    "offset",
    "min",
    "max",
    "unit",
    "verified",
    "source",
    "notes",
)
WAKE_FIELDS = ("method", "prime_pid", "attempts", "interval_ms", "sleep_timer_ms", "notes")
RESEARCH_FIELDS = (
    "type",
    "target",
    "status",
    "priority",
    "vehicle_states",
    "notes",
    "what_to_test",
    "result",
)
IOCONTROL_FIELDS = ("label", "session", "session_mode", "hold", "verified", "status_param", "notes")


def profile_maturity(counts: Mapping[str, int]) -> str:
    """One-word maturity hint from ``ecus``/``params``/``verified`` counts."""
    if counts["verified"] >= 20:
        return "mature"
    if counts["params"] <= 2 and counts["ecus"] <= 2:
        return "seed / stub"
    return "early"


def _plain(value: Any) -> Any:
    """Convert a ruamel node to builtin JSON types (strings lose their folded style)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def _hex(value: Any) -> str | None:
    return f"0x{int(value):X}" if isinstance(value, int) and not isinstance(value, bool) else None


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _pick(node: Mapping, fields: tuple[str, ...]) -> dict[str, Any]:
    """The allow-listed, non-empty fields of ``node``, in the order of ``fields``."""
    return {f: _plain(node[f]) for f in fields if f in node and _has_value(node[f])}


def _src(file: str, node: Any, key: Any) -> dict[str, Any]:
    """Source location of ``key`` inside the ruamel mapping/sequence ``node``."""
    lc: Any = getattr(node, "lc", None)
    try:
        position = lc.key(key) if isinstance(node, Mapping) else lc.item(key)
        line = position[0]
    except (AttributeError, KeyError, TypeError, IndexError):
        line = 0
    return {"file": file, "line": line + 1}


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _load(path: Path) -> Any:
    with path.open() as fh:
        return round_trip_yaml().load(fh)


def _rx_id(ecu_def: Mapping, meta: Mapping) -> int | None:
    """The ECU's CAN response address, resolved the way the transports resolve it."""
    tx = ecu_def.get("tx_id")
    if not isinstance(tx, int) or isinstance(tx, bool):
        return None
    rx = ecu_def.get("rx_id")
    rx = int(rx) if isinstance(rx, int) and not isinstance(rx, bool) else None
    return resolve_rx(int(tx), rx, resolve_rx_offset(meta), resolve_mode(meta, ecu_def))


def _is_web_url(url: str) -> bool:
    return url.lower().startswith(("https://", "http://"))


def _signal_record(name: str, sig: Mapping, node: Any, file: str) -> dict[str, Any]:
    rec = {"name": name, **_pick(sig, SIGNAL_FIELDS), "src": _src(file, node, name)}
    links = [str(u) for u in sig.get("source_links") or [] if isinstance(u, str) and _is_web_url(u)]
    if links:
        rec["source_links"] = links
    rec.setdefault("verified", False)
    return rec


def _pid_record(pid: Any, pid_def: Mapping, node: Any, file: str) -> dict[str, Any]:
    params = _mapping(pid_def.get("parameters"))
    rec: dict[str, Any] = {
        "id": str(pid),
        "status": pid_status(dict(pid_def)),
        **_pick(pid_def, ("period", "response_frames", "vehicle_states", "notes")),
        "signals": [
            _signal_record(str(n), s, params, file)
            for n, s in params.items()
            if isinstance(s, Mapping)
        ],
        "src": _src(file, node, pid),
    }
    return rec


def _iocontrol_records(ecu_def: Mapping, file: str) -> list[dict[str, Any]]:
    io = _mapping(ecu_def.get("iocontrol"))
    records = []
    for did, cmd in io.items():
        if not isinstance(cmd, Mapping):
            continue
        # YAML 1.1 reads bare `on:`/`off:` keys as booleans.
        on = cmd.get("on") or cmd.get(True) or ""
        off = cmd.get("off") or cmd.get(False) or ""
        records.append(
            {
                "did": str(did).upper(),
                "on": str(on),
                "off": str(off),
                **_pick(cmd, IOCONTROL_FIELDS),
                "src": _src(file, io, did),
            }
        )
    return records


def _research_records(ecu_def: Mapping, file: str) -> list[dict[str, Any]]:
    items = ecu_def.get("research") or []
    return [
        {**_pick(item, RESEARCH_FIELDS), "src": _src(file, items, i)}
        for i, item in enumerate(items)
        if isinstance(item, Mapping)
    ]


def _dtc_records(ecu_def: Mapping, file: str) -> list[dict[str, Any]]:
    dtcs = _mapping(ecu_def.get("dtcs"))
    return [
        {"code": str(code), **_pick(_mapping(d), ("description",)), "src": _src(file, dtcs, code)}
        for code, d in dtcs.items()
    ]


def _ecu_record(name: str, ecu_def: Mapping, doc: Any, file: str, meta: Mapping) -> dict[str, Any]:
    identity = _mapping(ecu_def.get("identity"))
    buses = [str(b) for b in ecu_def.get("can_bus") or []]
    pids = [
        _pid_record(pid, pd, _mapping(ecu_def.get("pids")), file)
        for pid, pd in _mapping(ecu_def.get("pids")).items()
        if isinstance(pd, Mapping)
    ]
    signals = [s for p in pids if p["status"] != "ignored" for s in p["signals"]]
    verified = sum(1 for s in signals if s["verified"])
    return {
        "name": name,
        "tx_id": _hex(ecu_def.get("tx_id")),
        "rx_id": _hex(_rx_id(ecu_def, meta)),
        "protocol": _plain(identity.get("id_protocol")),
        "description": _plain(identity.get("description")),
        "confidence": _plain(identity.get("identity_confidence")),
        "buses": buses,
        "vehicle_states": _plain(ecu_def.get("vehicle_states") or []),
        "wake": _pick(_mapping(ecu_def.get("wake")), WAKE_FIELDS) or None,
        "maturity": "verified" if verified else ("signals" if signals else "none"),
        "counts": {"pids": len(pids), "signals": len(signals), "verified": verified},
        "pids": pids,
        "iocontrol": _iocontrol_records(ecu_def, file),
        "research": _research_records(ecu_def, file),
        "dtcs": _dtc_records(ecu_def, file),
        "src": _src(file, doc, name),
    }


def _broadcast_records(root: Path) -> list[dict[str, Any]]:
    out = []
    signals_dir = root / "signals"
    for path in sorted(signals_dir.glob("*.yaml")) if signals_dir.is_dir() else []:
        file = f"profiles/{root.name}/signals/{path.name}"
        messages = _mapping(_mapping(_load(path)).get("messages"))
        for arb_id, msg in messages.items():
            msg = _mapping(msg)
            sigs = _mapping(msg.get("signals"))
            out.append(
                {
                    "id": str(arb_id),
                    "bus": path.stem,
                    **_pick(msg, ("name", "tx_ecu", "length")),
                    "signals": [
                        {
                            "name": str(n),
                            **_pick(_mapping(s), BROADCAST_SIGNAL_FIELDS),
                            "src": _src(file, sigs, n),
                        }
                        for n, s in sigs.items()
                    ],
                    "src": _src(file, messages, arb_id),
                }
            )
    return out


def _bus_records(root: Path) -> list[dict[str, Any]]:
    path = root / "can_buses.yaml"
    if not path.exists():
        return []
    buses = _mapping(_mapping(_load(path)).get("can_buses"))
    return [
        {
            "code": str(code),
            **_pick(_mapping(b), ("name", "description", "bitrate")),
            "private": _mapping(b).get("private") is True,
        }
        for code, b in buses.items()
    ]


def export_profile(root: Path, *, blob_base: str = DEFAULT_BLOB_BASE) -> dict[str, Any]:
    """The explorer data for the profile bundle at ``root``.

    ``blob_base`` is the URL prefix a ``src.file`` is appended to (a commit-pinned GitHub
    ``blob`` URL for a real build), so a line number cannot drift when the file changes.
    """
    root = Path(root)
    meta_path = root / "profile.yaml"
    meta = _mapping(_load(meta_path)) if meta_path.exists() else {}

    ecus = []
    for path in iter_ecu_files(root / "ecus") if (root / "ecus").is_dir() else []:
        doc = _load(path)
        file = f"profiles/{root.name}/ecus/{path.name}"
        for name, ecu_def in _mapping(doc).items():
            if isinstance(ecu_def, Mapping):
                ecus.append(_ecu_record(str(name), ecu_def, doc, file, meta))

    buses = _bus_records(root)
    counts = {
        "ecus": len(ecus),
        "pids": sum(e["counts"]["pids"] for e in ecus),
        "params": sum(e["counts"]["signals"] for e in ecus),
        "verified": sum(e["counts"]["verified"] for e in ecus),
        "iocontrol": sum(len(e["iocontrol"]) for e in ecus),
        "research": sum(1 for e in ecus for r in e["research"] if r.get("status") != "done"),
    }
    return {
        "profile": {
            "name": root.name,
            "car_model": _plain(meta.get("car_model")) or root.name,
            "maturity": profile_maturity(counts),
            "stats": counts,
            "blob_base": blob_base,
        },
        "buses": buses,
        "layout": layout_buses(buses, ecus),
        "ecus": ecus,
        "broadcast": _broadcast_records(root),
    }
