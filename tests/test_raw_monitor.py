"""Tests for the raw-CAN monitor backend helpers (pure, no device)."""

import asyncio

from canlib.modes.monitor import MonitorController, _raw_pid_result
from canlib.modes.raw_monitor import _keep_mode, query_ecu_addresses


class _Args:
    def __init__(self, **kw):
        self.keep_unique = self.keep_all = False
        self.keep = None
        self.__dict__.update(kw)


class TestKeepMode:
    def test_none(self):
        assert _keep_mode(_Args()) is None

    def test_unique(self):
        assert _keep_mode(_Args(keep_unique=True)) == "unique"

    def test_all(self):
        assert _keep_mode(_Args(keep_all=True)) == "all"

    def test_last(self):
        assert _keep_mode(_Args(keep=5)) == "last"


class TestQueryEcuAddresses:
    def test_maps_tx_rx(self):
        ecu_index = {"IGPM": {"tx_id": 0x770}, "BMS": {"tx_id": 0x7E4}}
        steps = [{"ecu": "igpm", "pids": []}, {"ecu": "BMS", "pids": ["2101"]}]
        out = query_ecu_addresses(steps, ecu_index)
        assert out == {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)}

    def test_skips_unknown_ecu(self):
        out = query_ecu_addresses([{"ecu": "NOPE", "pids": []}], {"IGPM": {"tx_id": 0x770}})
        assert out == {}


class TestRawPidResult:
    def test_positive_mapped(self):
        r = _raw_pid_result("22BC03", {"parameters": {}}, False, bytes.fromhex("62BC03FDEE"), 1.0)
        assert r["raw_hex"] == "62BC03FDEE"
        assert r["acquired_at"] == 1.0
        assert "error" not in r

    def test_positive_unmapped(self):
        r = _raw_pid_result("22B003", None, True, bytes.fromhex("62B003AA"), 2.0)
        assert r["raw_hex"] == "62B003AA"
        assert r["unmapped"] is True

    def test_negative_response_nrc(self):
        r = _raw_pid_result("22B004", None, False, bytes.fromhex("7F2213"), 3.0)
        assert "NRC 0x13" in r["error"]

    def test_timeout_none(self):
        r = _raw_pid_result("2101", {"parameters": {}}, False, None, 4.0)
        assert r["error"] == "timeout"

    def test_exception_value(self):
        r = _raw_pid_result("2101", {"parameters": {}}, False, TimeoutError("x"), 5.0)
        assert r["error"] == "timeout"

    def test_other_exception(self):
        r = _raw_pid_result("2101", None, False, ValueError("boom"), 6.0)
        assert "boom" in r["error"]

    def test_empty_response(self):
        r = _raw_pid_result("2101", None, False, b"", 7.0)
        assert "empty" in r["error"]


class FakeRawClient:
    """Maps (ecu, request_bytes) -> response bytes (or None = timeout)."""

    def __init__(self, table):
        self.table = table

    def read(self, ecu, req, timeout=None):
        return self.table.get((ecu, bytes(req)))

    def poll(self, requests, timeout=None, on_result=None):
        out = {}
        for e, r in requests:
            val = self.table.get((e, bytes(r)))
            out[(e, bytes(r))] = val
            if on_result is not None:  # mirror the real client's incremental callback
                on_result((e, bytes(r)), val)
        return out


def _mk_ctrl(steps, ecu_index, lengths=None, nobatch=None, table=None):
    c = MonitorController(None, steps, {}, verbose=False, raw_client=FakeRawClient(table or {}))
    c._ecu_index = ecu_index
    if lengths:
        c._raw_lengths.update(lengths)
    if nobatch:
        c._raw_nobatch.update(nobatch)
    return c


_IGPM_BMS_INDEX = {
    "IGPM": {
        "tx_id": 0x770,
        "multi_did": True,
        "pids": {
            "22BC03": {"parameters": {}},
            "22BC06": {"parameters": {}},
            "22BC07": {"parameters": {}},
        },
    },
    "BMS": {"tx_id": 0x7E4, "pids": {"2101": {"parameters": {}}}},
}


class TestBuildRawSubmissions:
    def test_batches_multi_did_when_lengths_known(self):
        steps = [
            {"ecu": "IGPM", "pids": ["BC03", "BC06", "BC07"]},
            {"ecu": "BMS", "pids": ["2101"]},
        ]
        c = _mk_ctrl(
            steps,
            _IGPM_BMS_INDEX,
            lengths={("IGPM", "BC03"): 8, ("IGPM", "BC06"): 8, ("IGPM", "BC07"): 8},
        )
        subs, _plan = c._build_raw_submissions()
        igpm = [s for s in subs if s["ecu"] == "IGPM"]
        assert len(igpm) == 1
        assert igpm[0]["req"] == bytes.fromhex("22BC03BC06BC07")
        assert igpm[0]["lengths"] == [("BC03", 8), ("BC06", 8), ("BC07", 8)]
        bms = [s for s in subs if s["ecu"] == "BMS"]
        assert bms[0]["req"] == bytes.fromhex("2101") and bms[0]["lengths"] is None

    def test_no_batch_without_lengths(self):
        steps = [{"ecu": "IGPM", "pids": ["BC03", "BC06", "BC07"]}]
        c = _mk_ctrl(steps, _IGPM_BMS_INDEX)  # no learned lengths
        subs, _plan = c._build_raw_submissions()
        assert len(subs) == 3
        assert all(s["lengths"] is None for s in subs)

    def test_nobatch_forces_singles(self):
        steps = [{"ecu": "IGPM", "pids": ["BC03", "BC06", "BC07"]}]
        c = _mk_ctrl(
            steps,
            _IGPM_BMS_INDEX,
            lengths={("IGPM", "BC03"): 8, ("IGPM", "BC06"): 8, ("IGPM", "BC07"): 8},
            nobatch={"IGPM"},
        )
        subs, _plan = c._build_raw_submissions()
        assert len(subs) == 3 and all(s["lengths"] is None for s in subs)

    def test_batch_capped_at_three(self):
        idx = {
            "IGPM": {
                "tx_id": 0x770,
                "multi_did": True,
                "pids": {f"22BC0{i}": {"parameters": {}} for i in range(1, 6)},
            }
        }
        steps = [{"ecu": "IGPM", "pids": [f"BC0{i}" for i in range(1, 6)]}]
        lengths = {("IGPM", f"BC0{i}"): 4 for i in range(1, 6)}
        c = _mk_ctrl(steps, idx, lengths=lengths)
        subs, _plan = c._build_raw_submissions()
        # 5 DIDs -> batch of 3 + batch of 2.
        assert [len(s["members"]) for s in subs] == [3, 2]


class TestPollRaw:
    def test_learn_then_batch_and_decode(self):
        bc03 = "62BC03FDEE3C730A000000"
        bc06 = "62BC06B480000000000000"
        batch = "62BC03FDEE3C730A000000BC06B480000000000000"
        table = {
            ("IGPM", bytes.fromhex("22BC03")): bytes.fromhex(bc03),
            ("IGPM", bytes.fromhex("22BC06")): bytes.fromhex(bc06),
            ("IGPM", bytes.fromhex("22BC03BC06")): bytes.fromhex(batch),
        }
        steps = [{"ecu": "IGPM", "pids": ["BC03", "BC06"]}]
        c = _mk_ctrl(steps, _IGPM_BMS_INDEX, table=table)

        # Cycle 1: no lengths -> two single reads that learn lengths.
        asyncio.run(c._poll_raw())
        assert c._raw_lengths[("IGPM", "BC03")] == 8
        assert c._raw_lengths[("IGPM", "BC06")] == 8
        subs, _ = c._build_raw_submissions()
        assert len(subs) == 1 and subs[0]["req"] == bytes.fromhex("22BC03BC06")

        # Cycle 2: one batched request, split back into two decoded PIDs.
        asyncio.run(c._poll_raw())
        (_label, results) = c.last_queries[0]
        got = {r["pid"]: r.get("raw_hex") for r in results}
        assert got["22BC03"] == "62BC03FDEE3C730A000000"
        assert got["22BC06"] == "62BC06B480000000000000"

    def test_batch_nrc_falls_back_to_nobatch(self):
        table = {
            ("IGPM", bytes.fromhex("22BC03BC06")): bytes.fromhex("7F2213"),  # rejected
            ("IGPM", bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA"),
            ("IGPM", bytes.fromhex("22BC06")): bytes.fromhex("62BC06BB"),
        }
        steps = [{"ecu": "IGPM", "pids": ["BC03", "BC06"]}]
        c = _mk_ctrl(
            steps,
            _IGPM_BMS_INDEX,
            lengths={("IGPM", "BC03"): 1, ("IGPM", "BC06"): 1},
            table=table,
        )
        # First cycle attempts the batch, gets NRC 0x13 -> disables batching.
        asyncio.run(c._poll_raw())
        assert "IGPM" in c._raw_nobatch
        # Next cycle uses single reads.
        subs, _ = c._build_raw_submissions()
        assert len(subs) == 2 and all(s["lengths"] is None for s in subs)

    def test_partial_render_fires_and_pending_pid_keeps_last_value(self):
        # Incremental rendering: fast PIDs render mid-cycle via _on_partial, and a
        # PID still pending (not yet resolved) keeps its previous value (no
        # flicker) rather than vanishing from the frame.
        table = {
            ("IGPM", bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA"),
            ("IGPM", bytes.fromhex("22BC06")): bytes.fromhex("62BC06BB"),
        }
        steps = [{"ecu": "IGPM", "pids": ["BC03", "BC06"]}]
        c = _mk_ctrl(steps, _IGPM_BMS_INDEX, table=table)
        renders = []
        c._on_partial = lambda: renders.append(1)

        asyncio.run(c._poll_raw())
        assert len(renders) >= 1  # repainted mid-cycle, not just at the end
        got = {r["pid"]: r.get("raw_hex") for _lbl, res in c.last_queries for r in res}
        assert got["22BC03"] == "62BC03AA"
        assert got["22BC06"] == "62BC06BB"

        # The cycle remembered both entries, so a frame built with nothing yet
        # resolved this cycle (all PIDs still pending) shows their last values —
        # this is what keeps other PIDs visible while a slow one is in flight.
        _subs, plan_by_ecu = c._build_raw_submissions()
        frame = c._raw_build_queries(plan_by_ecu, {})
        pending = {r["pid"]: r.get("raw_hex") for _lbl, res in frame for r in res}
        assert pending["22BC03"] == "62BC03AA"
        assert pending["22BC06"] == "62BC06BB"
