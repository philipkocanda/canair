"""Coverage with a declared support map: non-signals drop out, reads outside the map surface."""

import json

from canlib.commands import coverage
from canlib.uds.byteindex import payload_index_to_wican
from canlib.uds.support_map import MapDecl

# 62 BC03 | map FD 00 3C 00 | data 0A 00 00 00   (WiCAN: map B5 B6 B7 B9, data B10 B11 B12 B13)
PAYLOAD = "62BC03FD003C000A000000"
DECL = MapDecl(0, 4, "bit")


def _params(**exprs):
    return {name: {"expression": e, "verified": True} for name, e in exprs.items()}


def _run(params, decl=DECL, payload=PAYLOAD):
    return coverage.analyze_pid(params, payload, sfb=2, support=decl)


class TestWithoutADeclaration:
    def test_output_is_exactly_what_it_always_was(self):
        params = _params(A="B10:1", B="B12:2")
        plain = coverage.analyze_pid(params, PAYLOAD, sfb=2)
        explicit = coverage.analyze_pid(params, PAYLOAD, sfb=2, support=None)
        assert plain == explicit
        assert not {"support_map", "outside_map", "support_map_past_end"} & set(plain)
        assert 5 in plain["unmapped"] and 11 in plain["unmapped"]


class TestNonSignalsDropOut:
    def test_map_bytes_are_not_unmapped_data(self):
        assert {5, 6, 7, 9}.isdisjoint(_run({})["unmapped"])

    def test_bytes_the_map_marks_nothing_in_are_not_unmapped(self):
        # Map byte 2 is 00 and byte 4 is 00: B11 and B13 carry no signal.
        res = _run({})
        assert {11, 13}.isdisjoint(res["unmapped"])
        assert res["support_map"]["not_signal_bytes"] == [11, 13]

    def test_a_byte_with_marks_and_no_definition_stays_unmapped(self):
        assert {10, 12} <= set(_run({})["unmapped"])

    def test_the_finding_names_the_map(self):
        sm = _run({})["support_map"]
        assert sm == {
            "unit": "bit",
            "mask": "FD 00 3C 00",
            "map_bytes": [5, 6, 7, 9],
            "not_signal_bytes": [11, 13],
        }


class TestBitGaps:
    def test_only_marked_bits_count_as_missing(self):
        # Mask FD marks bits 0,1,2,3,4,5,7; the signals read bits 1 and 3.
        gaps = _run(_params(A="B10:1", B="B10:3"))["incomplete_bitfields"]
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap["have"] == [1, 3]
        assert gap["missing"] == [0, 2, 4, 5, 7]
        assert gap["not_signals"] == [6]

    def test_a_byte_whose_marked_bits_are_all_defined_is_complete(self):
        # Mask 3C marks bits 2-5 of B12; defining all four closes the gap.
        res = _run(_params(A="B12:2", B="B12:3", C="B12:4", D="B12:5"))
        assert [g for g in res["incomplete_bitfields"] if g["byte"] == 12] == []

    def test_without_a_map_the_same_signals_leave_a_gap(self):
        params = _params(A="B12:2", B="B12:3", C="B12:4", D="B12:5")
        plain = coverage.analyze_pid(params, PAYLOAD, sfb=2)
        gap = next(g for g in plain["incomplete_bitfields"] if g["byte"] == 12)
        assert gap["missing"] == [0, 1, 6, 7]
        assert "not_signals" not in gap


class TestOutsideTheMap:
    def test_a_signal_on_an_unmarked_bit(self):
        out = _run(_params(S="B12:7"))["outside_map"]
        assert out == [{"signal": "S", "byte": 12, "bit": 7, "continuation": False}]

    def test_the_bit_just_above_a_mark_is_a_continuation(self):
        out = _run(_params(S="B12:6"))["outside_map"]
        assert out == [{"signal": "S", "byte": 12, "bit": 6, "continuation": True}]

    def test_a_whole_byte_read_of_a_non_signal_byte(self):
        out = _run(_params(S="B11"))["outside_map"]
        assert out == [{"signal": "S", "byte": 11, "bit": None, "continuation": False}]

    def test_a_whole_byte_read_of_a_marked_byte_is_fine(self):
        assert _run(_params(S="B10"))["outside_map"] == []

    def test_bits_inside_the_map_are_fine(self):
        assert _run(_params(S="B12:3"))["outside_map"] == []

    def test_a_byte_beyond_the_covered_data_is_not_judged(self):
        # The covered range is the 4 bytes after the map; a 5th is outside what the map speaks for.
        payload = "62BC03FD003C000A00000077"
        assert _run(_params(S="B14:3"), payload=payload)["outside_map"] == []


class TestPerByteMap:
    PAYLOAD = "62BC0380000000" + "11223344"  # map 80 00 00 00: only the first data byte

    def test_unmarked_bytes_are_not_signals_and_reads_of_them_are_outside(self):
        res = _run(_params(S="B11"), decl=MapDecl(0, 4, "byte"), payload=self.PAYLOAD)
        assert res["support_map"]["unit"] == "byte"
        assert res["outside_map"] == [
            {"signal": "S", "byte": 11, "bit": None, "continuation": False}
        ]
        assert 11 not in res["unmapped"]  # a non-signal is not open work either


class TestDeclarationOutrunsThePayload:
    def test_is_reported_and_changes_nothing_else(self):
        params = _params(A="B10:1")
        plain = coverage.analyze_pid(params, PAYLOAD, sfb=2)
        res = _run(params, decl=MapDecl(8, 4, "bit"))
        assert res["support_map_past_end"] is True
        assert {k: v for k, v in res.items() if k != "support_map_past_end"} == plain


class TestFiltering:
    def _entry(self, **kw):
        base = {"ecu": "E", "pid": "22BC03", "params": 1, "verified": 1}
        return base | {"unmapped": [], "unverified_mapped": [], "incomplete_bitfields": []} | kw

    class _Args:
        unmapped = unverified = bitfields = no_capture = all = False

    def test_a_hard_outside_read_is_a_finding(self):
        o = {"signal": "S", "byte": 12, "bit": 7, "continuation": False}
        assert coverage.keep(self._entry(outside_map=[o]), self._Args())

    def test_a_continuation_alone_is_not(self):
        o = {"signal": "S", "byte": 12, "bit": 6, "continuation": True}
        assert not coverage.keep(self._entry(outside_map=[o]), self._Args())

    def test_a_map_past_the_payload_is_a_finding(self):
        assert coverage.keep(self._entry(support_map_past_end=True), self._Args())


class TestAudit:
    def _index(self, support_map):
        return {
            "IGPM": {
                "pids": {
                    "22BC03": {
                        "parameters": _params(A="B12:7"),
                        "support_map": support_map,
                    }
                }
            }
        }

    def _captures(self):
        from canlib.commands.coverage.payloads import CoverageCaptures

        cap = {
            "payload": PAYLOAD,
            "date": "2026-10-25",
            "label": "",
            "file": "f",
            "may_include_padding": False,
        }
        return CoverageCaptures(payloads={("IGPM", "22BC03"): cap})

    def test_reads_the_declaration_from_the_pid_index(self):
        entry = coverage.audit(
            self._index({"at": 0, "length": 4, "unit": "bit"}), self._captures()
        )[0]
        assert entry["support_map"]["mask"] == "FD 00 3C 00"
        assert entry["outside_map"][0]["signal"] == "A"

    def test_no_declaration_no_new_keys(self):
        entry = coverage.audit(self._index(None), self._captures())[0]
        assert "support_map" not in entry and "outside_map" not in entry

    def test_a_malformed_declaration_is_ignored_here(self):
        # `validate pids` reports it; coverage must not crash on it.
        entry = coverage.audit(
            self._index({"at": 0, "length": 99, "unit": "bit"}), self._captures()
        )[0]
        assert "support_map" not in entry

    def test_json_round_trips(self):
        entry = coverage.audit(
            self._index({"at": 0, "length": 4, "unit": "bit"}), self._captures()
        )[0]
        assert json.loads(json.dumps(entry, default=str))["support_map"]["unit"] == "bit"


class TestRender:
    def _entry(self, **kw):
        return {
            "ecu": "IGPM",
            "pid": "22BC03",
            "params": 2,
            "verified": 2,
            "capture": {"date": "2026-10-25", "may_include_padding": False},
            "data_bytes": 8,
            "payload_len": 11,
            "unmapped": [],
            "unverified_mapped": [],
            "incomplete_bitfields": [],
            "past_end": [],
        } | kw

    def test_map_gap_and_outside_lines(self, capsys):
        entry = self._entry(
            support_map={
                "unit": "bit",
                "mask": "FD 00 3C 00",
                "map_bytes": [5, 6, 7, 9],
                "not_signal_bytes": [11, 13],
            },
            incomplete_bitfields=[
                {
                    "byte": 10,
                    "have": [1],
                    "missing": [0, 2],
                    "also_whole": False,
                    "not_signals": [6],
                }
            ],
            outside_map=[
                {"signal": "S", "byte": 12, "bit": 7, "continuation": False},
                {"signal": "T", "byte": 12, "bit": 6, "continuation": True},
            ],
        )
        coverage.render_text([entry], None, scoped=False)
        out = capsys.readouterr().out
        assert "MAP FD 00 3C 00 (bit)" in out and "2 byte(s) carry no signal" in out
        assert "(+1 not signals)" in out
        assert "OUTSIDE MAP" in out and "S (B12:7)" in out
        assert "FIELD CONTINUATION" in out and "T (B12:6)" in out

    def test_past_end_line(self, capsys):
        coverage.render_text([self._entry(support_map_past_end=True)], None, scoped=False)
        assert "MAP PAST END" in capsys.readouterr().out

    def test_no_map_no_map_lines(self, capsys):
        coverage.render_text([self._entry(unmapped=[10])], None, scoped=False)
        out = capsys.readouterr().out
        assert "MAP" not in out.replace("UNMAPPED", "")


def test_payload_index_to_wican_is_length_aware():
    multi = payload_index_to_wican(list(range(11)))
    assert multi[0] == 2 and multi[6] == 9  # FF carries 6 data bytes, then a CF PCI byte
    single = payload_index_to_wican(list(range(5)))
    assert single[0] == 1  # a single frame has one PCI byte
