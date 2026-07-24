"""Tests for the `canair coverage` audit — verified-aware byte mapping."""

from canlib.commands import coverage


class TestAnalyzePid:
    """analyze_pid is a pure function over (parameters, payload, subfunction bytes)."""

    def test_unmapped_and_unverified_split(self):
        # 2101 single-frame: payload 6101 AA BB CC DD -> WiCAN B0=SF PCI, B1=61,
        # B2=01 (sub echo), B3=AA(data), B4=BB, B5=CC, B6=DD. Data bytes B3..B6.
        params = {
            "VERIFIED_ONE": {"expression": "B3", "verified": True},
            "GUESS_ONE": {"expression": "B4"},  # unverified
        }
        result = coverage.analyze_pid(params, "6101AABBCCDD", sfb=1)
        assert result["unmapped"] == [5, 6]  # B5, B6 covered by nothing
        assert result["unverified_mapped"] == [4]  # B4 mapped but unverified
        # B3 is verified-mapped -> neither unmapped nor unverified.
        assert 3 not in result["unmapped"]
        assert 3 not in result["unverified_mapped"]

    def test_verified_covering_shared_byte_clears_unverified(self):
        params = {
            "GUESS": {"expression": "B3"},
            "CONFIRMED": {"expression": "B3", "verified": True},
        }
        result = coverage.analyze_pid(params, "6101AABBCCDD", sfb=1)
        # B3 is verified by CONFIRMED even though GUESS also reads it.
        assert result["unverified_mapped"] == []

    def test_fully_unverified(self):
        params = {"G1": {"expression": "B3"}, "G2": {"expression": "B4"}}
        result = coverage.analyze_pid(params, "6101AABB", sfb=1)
        assert result["unverified_mapped"] == [3, 4]
        assert result["unmapped"] == []


class TestKeepFilter:
    """The default view treats unverified-mapped bytes as a gap; --unverified isolates them."""

    def _run(self, tmp_path, monkeypatch, capsys, argv, payloads):
        import argparse

        monkeypatch.setattr(coverage, "load_longest_payloads", lambda: payloads)
        monkeypatch.setattr(coverage, "load_pids", lambda *_: {})
        monkeypatch.setattr(
            coverage,
            "build_ecu_index",
            lambda *_: {"MCU": {"pids": {"2102": {"parameters": {"GUESS": {"expression": "B4"}}}}}},
        )
        monkeypatch.setattr("canlib.ecus.canonical_ecu_name_safe", lambda n: n)
        p = coverage.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(argv)
        rc = coverage.run(args)
        return rc, capsys.readouterr().out

    def test_unverified_shown_by_default(self, tmp_path, monkeypatch, capsys):
        payloads = {
            ("MCU", "2102"): {
                "payload": "6102AABB",
                "date": "2026-07-01",
                "label": "",
                "file": "x.yaml",
            }
        }
        rc, out = self._run(tmp_path, monkeypatch, capsys, ["MCU", "2102"], payloads)
        assert rc == 0
        assert "UNVERIFIED" in out and "B4" in out

    def test_unverified_flag_isolates(self, tmp_path, monkeypatch, capsys):
        payloads = {
            ("MCU", "2102"): {
                "payload": "6102AABB",
                "date": "2026-07-01",
                "label": "",
                "file": "x.yaml",
            }
        }
        rc, out = self._run(
            tmp_path, monkeypatch, capsys, ["MCU", "2102", "--unverified"], payloads
        )
        assert rc == 0
        assert "UNVERIFIED" in out
