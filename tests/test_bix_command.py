"""Tests for the ``canair bix`` command layer (arg parsing + payload annotation)."""

import argparse

import pytest

from canlib.commands import bix


def _parse(argv):
    """Build a parser with just the bix subcommand and parse argv."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    bix.add_parser(subparsers)
    return parser.parse_args(["bix", *argv])


# ── --annotate accepts quoted, unquoted, and no-space hex identically ──


@pytest.mark.parametrize(
    "argv",
    [
        # unquoted space-separated (each byte its own shell token)
        ["-2", "-a", "62", "01", "A0", "55"],
        # quoted space-separated (one token)
        ["-2", "-a", "62 01 A0 55"],
        # no-space blob (one token)
        ["-2", "-a", "6201A055"],
    ],
)
def test_annotate_forms_parse_to_same_payload(argv, capsys):
    args = _parse(argv)
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    # SID + the two DID bytes + first data byte all present in the table
    assert "0x62" in out
    assert "0x01" in out
    assert "0xA0" in out
    assert "0x55" in out


def test_annotate_joins_tokens_before_parsing():
    args = _parse(["-a", "62", "01", "A0", "55"])
    # nargs="+" yields a list; run() joins with spaces
    assert args.annotate == ["62", "01", "A0", "55"]


def test_parse_hex_payload_spacing_equivalence():
    assert (
        bix._parse_hex_payload("62 01 A0 55")
        == bix._parse_hex_payload("6201A055")
        == [0x62, 0x01, 0xA0, 0x55]
    )


# ── error paths name the offending input ──


def test_parse_hex_payload_odd_length_exits(capsys):
    with pytest.raises(SystemExit):
        bix._parse_hex_payload("62 0")
    assert "odd number of hex characters" in capsys.readouterr().err


def test_parse_hex_payload_invalid_byte_names_token(capsys):
    with pytest.raises(SystemExit):
        bix._parse_hex_payload("62 ZZ A0")
    err = capsys.readouterr().err
    assert "invalid hex byte 'ZZ'" in err


# ── --annotate --ecu/--pid parameter overlay (T3.1) ──


def test_annotate_ecu_pid_overlay_shows_params(capsys):
    # IGPM 22BC03 B10 holds the door bits; the overlay must name them and flag
    # unmapped bytes — resolved against the active profile.
    args = _parse(["-2", "-a", "62BC03FDEE3C7320010000", "--ecu", "IGPM", "--pid", "22BC03"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Param" in out  # overlay column present
    assert "DOOR_DRV_OPEN" in out  # a mapped bit is named
    assert "unmapped" in out  # a data byte with no param is flagged


def test_annotate_ecu_requires_pid(capsys):
    args = _parse(["-2", "-a", "62BC03", "--ecu", "IGPM"])
    assert bix.run(args) == 1
    assert "--ecu requires --pid" in capsys.readouterr().err


def test_annotate_pid_requires_ecu(capsys):
    args = _parse(["-2", "-a", "62BC03", "--pid", "22BC03"])
    assert bix.run(args) == 1
    assert "--pid requires --ecu" in capsys.readouterr().err


def test_annotate_no_overlay_has_no_param_column(capsys):
    args = _parse(["-2", "-a", "6201A055"])
    assert bix.run(args) == 0
    assert "Param" not in capsys.readouterr().out


# ── --annotate is length-aware: single-frame vs multi-frame Torque/ISO-TP ──


def test_annotate_single_frame_torque_starts_after_header(capsys):
    # 21 01 response, single frame (≤7 payload bytes): PCI at B00, SID at B01,
    # PID at B02, first DATA byte at B03. Torque A / bix 0 must land on B03 —
    # the Torque column must agree with the Role column, not the multi-frame
    # (off-by-one) layout that put Torque A at B04. (--torque reveals the column.)
    args = _parse(["-1", "--torque", "-a", "6101FFEEDDCC"])
    assert bix.run(args) == 0
    lines = capsys.readouterr().out.splitlines()
    b03 = next(ln for ln in lines if ln.strip().startswith("B03"))
    assert "A" in b03 and " 0 " in b03  # Torque A, bix 0 on the first data byte
    b01 = next(ln for ln in lines if ln.strip().startswith("B01"))
    assert "SID" in b01  # single-frame SID sits at B01 (one PCI byte, not two)


def test_annotate_multi_frame_torque_unchanged(capsys):
    # Multi-frame (>7 payload bytes) keeps the 2-byte FF PCI layout: for a 22xxxx
    # DID, the first data byte / Torque A is at B05.
    args = _parse(["-2", "--torque", "-a", "62B004FFEEDDCCBBAA9988776655"])
    assert bix.run(args) == 0
    lines = capsys.readouterr().out.splitlines()
    b05 = next(ln for ln in lines if ln.strip().startswith("B05"))
    assert "A" in b05 and " 0 " in b05


# ── Torque 1 vs Torque 2 is discoverable (the mapping isn't fixed) ──


def test_annotate_names_active_torque_variant(capsys):
    args = _parse(["-1", "--torque", "-a", "6101FF"])
    assert bix.run(args) == 0
    assert "Torque 1" in capsys.readouterr().out


def test_annotate_sub2_names_torque_2_variant(capsys):
    args = _parse(["-2", "--torque", "-a", "62B004FF"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Torque 2" in out
    assert "-1" in out  # points at the other variant


# ── --table CAN frame boundaries + Role column ──


def test_table_has_role_column_and_frame_dividers(capsys):
    args = _parse(["--table"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    # Role column header and framing labels for the multi-frame layout.
    assert "Role" in out
    assert "FF PCI" in out  # first-frame PCI at B00/B01
    assert "CF PCI" in out  # consecutive-frame PCI at B08/B16/...
    assert "SID" in out
    assert "PID" in out  # sub=1 default → header byte is a PID
    # CAN frame boundary dividers between 8-byte blocks.
    assert "Frame 0" in out
    assert "Frame 1" in out


def test_table_sub2_labels_header_bytes_as_did(capsys):
    args = _parse(["-2", "--table"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    # The Role column marks the two header bytes as DID (not a single PID byte).
    assert "| DID    |" in out
    assert "| PID    |" not in out  # 22xxxx DIDs have no single-byte PID header row


def test_table_no_ansi_when_not_a_tty(capsys):
    # capsys makes stdout a non-tty, so no escape sequences must leak.
    args = _parse(["--table"])
    assert bix.run(args) == 0
    assert "\033[" not in capsys.readouterr().out


def test_c_helper_wraps_only_on_tty(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert bix._c("X", bix._CYAN) == f"{bix._CYAN}X{bix._RESET}"
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert bix._c("X", bix._CYAN) == "X"


def test_cerr_helper_gates_on_stderr_tty(monkeypatch):
    # Warnings go to stderr, so their color must follow stderr's TTY-ness — not
    # stdout's (which may be redirected to a file/pipe independently).
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    assert bix._cerr("X", bix._YELLOW) == f"{bix._YELLOW}X{bix._RESET}"
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    assert bix._cerr("X", bix._YELLOW) == "X"


def test_table_role_multiframe_layout():
    # sub=1 (21xx): B00/B01 FF PCI, B02 SID, B03 PID, B04.. data; B08 CF PCI.
    assert bix._table_role(0, 1) == "FF PCI"
    assert bix._table_role(1, 1) == "FF PCI"
    assert bix._table_role(2, 1) == "SID"
    assert bix._table_role(3, 1) == "PID"
    assert bix._table_role(4, 1) == ""
    assert bix._table_role(8, 1) == "CF PCI"
    assert bix._table_role(16, 1) == "CF PCI"
    # sub=2 (22xxxx): B03/B04 are the two DID bytes.
    assert bix._table_role(3, 2) == "DID"
    assert bix._table_role(4, 2) == "DID"
    assert bix._table_role(5, 2) == ""


# ── bare `bix` friendly overview (legend + compact 2-frame table) ──


def test_bare_bix_prints_overview_and_succeeds(capsys):
    args = _parse([])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    # Legend explains every notation and every Role label.
    for term in ("WiCAN", "ISO-TP", "Torque", "bix", "FF PCI", "CF PCI", "SID", "PID", "PCI ="):
        assert term in out, term
    # Compact 2-frame table only: shows B00-B15 (Frame 0 and 1), not Frame 2+.
    assert "Frame 0" in out
    assert "Frame 1" in out
    assert "Frame 2" not in out
    assert "|   B15 |" in out  # last row of the 2-frame view
    assert "|   B16 |" not in out  # Frame 2 row is not rendered
    # Points the reader at the ways to go deeper.
    assert "canair bix --table" in out
    assert "--annotate" in out


def test_bare_bix_no_ansi_when_not_a_tty(capsys):
    args = _parse([])
    assert bix.run(args) == 0
    assert "\033[" not in capsys.readouterr().out


def test_bare_bix_sub2_legend_describes_did(capsys):
    # With --torque the legend's Torque note reflects the 2-byte subfunction.
    args = _parse(["-2", "--torque"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "DID" in out
    assert "2 DID bytes" in out  # Torque column note reflects the 2-byte subfunction


def test_legend_ties_pid_did_row_to_uds_subfunction(capsys):
    # The -1/-2 help calls that byte the "subfunction"; the Role legend must
    # cross-reference it so PID/DID and subfunction read as the same slot.
    a1 = _parse([])
    assert bix.run(a1) == 0
    assert "PID       the Parameter ID byte (UDS subfunction)" in capsys.readouterr().out
    a2 = _parse(["-2"])
    assert bix.run(a2) == 0
    assert "DID       the 2 Data Identifier bytes (UDS subfunction)" in capsys.readouterr().out


def test_full_table_shows_more_than_two_frames(capsys):
    # --table is the larger view: it must reach beyond the compact overview.
    args = _parse(["--table"])
    assert bix.run(args) == 0
    assert "Frame 2" in capsys.readouterr().out


def test_pad_aligns_before_coloring(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    # The visible text is padded to width BEFORE the ANSI codes wrap it, so the
    # padded region stays width-correct regardless of color.
    assert bix._pad("AB", 5, bix._CYAN) == f"{bix._CYAN}AB   {bix._RESET}"
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert bix._pad("AB", 5, bix._CYAN) == "AB   "


def _row(out: str, wican: str) -> str:
    """Return the annotate/table row whose WiCAN cell is ``wican`` (e.g. 'B03')."""
    return next(ln for ln in out.splitlines() if ln.strip().startswith(wican))


# ── single-frame edge cases + no spurious frame dividers ──


def test_annotate_exactly_seven_bytes_is_single_frame(capsys):
    # Exactly 7 payload bytes still fit one CAN frame (single-frame layout):
    # one PCI byte at B00, data through B07, and NO frame divider.
    args = _parse(["-1", "-a", "6101FFEEDDCCBB"])  # 7 bytes
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Frame 0" not in out  # single frame → no boundary markers
    assert "B00" in out and "B07" in out
    # SID at B01 (single PCI byte), never a two-byte FF PCI header.
    assert "SID" in _row(out, "B01")


def test_annotate_eight_bytes_becomes_multi_frame(capsys):
    # 8 payload bytes no longer fit one frame → multi-frame: two FF PCI bytes at
    # B00/B01, a CF PCI at B08, and frame dividers appear.
    args = _parse(["-1", "-a", "6101FFEEDDCCBBAA"])  # 8 bytes
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Frame 0" in out and "Frame 1" in out
    assert "PCI" in _row(out, "B00")
    assert "PCI" in _row(out, "B01")  # second FF PCI byte
    assert "SID" in _row(out, "B02")  # SID shifts to B02 in multi-frame


def test_annotate_single_frame_has_no_frame_divider(capsys):
    args = _parse(["-1", "-a", "6101FF"])
    assert bix.run(args) == 0
    assert "── Frame" not in capsys.readouterr().out


# ── Torque 1 vs Torque 2 byte-level offset ──


def test_torque1_vs_torque2_first_data_byte_offset(capsys):
    # Same reassembled-looking header echo, different subfunction width: Torque A
    # (bix 0) lands one byte later under -2 than under -1 (single-frame here).
    a1 = _parse(["-1", "--torque", "-a", "6101FFEEDD"])
    assert bix.run(a1) == 0
    out1 = capsys.readouterr().out
    assert "A" in _row(out1, "B03")  # -1: first data byte at B03

    a2 = _parse(["-2", "--torque", "-a", "62B004FFEEDD"])
    assert bix.run(a2) == 0
    out2 = capsys.readouterr().out
    assert "A" in _row(out2, "B04")  # -2: header is SID + 2 DID, data at B04


def test_isotp_to_torque_respects_subfunction_width():
    # Unit-level guard for the offset the annotate columns rely on.
    from canlib.byteindex import isotp_to_torque

    # 1-byte subfunction: header = SID + PID = ISO-TP 0,1 → data starts at 2.
    assert isotp_to_torque(1, 1) is None  # PID byte, not data
    assert isotp_to_torque(2, 1) == 0  # Torque A
    # 2-byte subfunction: header = SID + 2 DID = ISO-TP 0,1,2 → data starts at 3.
    assert isotp_to_torque(2, 2) is None  # second DID byte, not data
    assert isotp_to_torque(3, 2) == 0  # Torque A


# ── color IS emitted on a TTY (frame dividers, legend) ──


def test_table_emits_ansi_on_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    args = _parse(["--table"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert bix._CYAN in out  # frame dividers are colored
    assert bix._DIM in out  # PCI rows are dimmed


def test_bare_bix_emits_ansi_on_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    args = _parse([])
    assert bix.run(args) == 0
    assert bix._BOLD in capsys.readouterr().out  # legend section headers are bold


def test_annotate_emits_ansi_on_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    args = _parse(["-1", "-a", "6101FFEEDDCCBBAA"])  # multi-frame → colored divider
    assert bix.run(args) == 0
    assert bix._CYAN in capsys.readouterr().out


# ── --table --max controls how many frames render ──


def test_table_max_limits_rendered_frames(capsys):
    args = _parse(["--table", "--max", "7"])  # only WiCAN B00-B07, one frame
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Frame 0" in out
    assert "Frame 1" not in out
    assert "|   B07 |" in out
    assert "|   B08 |" not in out


# ── --annotate --raw: index an already-framed CAN payload (PCI present) ──


def test_annotate_raw_frame_indexes_as_is(capsys):
    # 0x06 SF PCI + 62 B0 04 74 02 99: SID must land at B01 (the byte after the
    # single PCI byte), NOT be treated as data and re-framed.
    args = _parse(["-2", "--raw", "-a", "0662B004740299"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "SID" in _row(out, "B01")
    assert "0x62" in _row(out, "B01")  # the real SID sits at B01
    assert "0x06" in _row(out, "B00")  # PCI byte stays at B00


def test_annotate_raw_matches_reconstructed(capsys):
    # Annotating a raw single-frame payload (--raw) and the equivalent PCI-stripped
    # payload (default) must agree on every data byte's WiCAN index.
    a_raw = _parse(["-2", "--raw", "-a", "0662B004740299"])
    assert bix.run(a_raw) == 0
    raw_out = capsys.readouterr().out

    a_uds = _parse(["-2", "-a", "62B004740299"])
    assert bix.run(a_uds) == 0
    uds_out = capsys.readouterr().out

    for wican in ("B01", "B04", "B06"):
        assert _row(raw_out, wican).split("|")[1:] == _row(uds_out, wican).split("|")[1:]


def test_annotate_raw_flow_control_frame_errors(capsys):
    args = _parse(["--raw", "-a", "30000A"])
    assert bix.run(args) == 1
    assert "Flow-Control" in capsys.readouterr().err


def test_raw_without_annotate_errors(capsys):
    args = _parse(["--raw"])
    assert bix.run(args) == 1
    assert "--raw only applies to --annotate" in capsys.readouterr().err


# ── reliable payload-kind mismatch warnings (disjoint SID / PCI ranges) ──


def test_annotate_warns_when_raw_frame_passed_without_raw(capsys):
    # 0x06 first byte is a PCI byte (< 0x40), not a UDS SID → warn, suggest --raw.
    args = _parse(["-2", "-a", "0662B004"])
    assert bix.run(args) == 0
    err = capsys.readouterr().err
    assert "looks like an ISO-TP PCI byte" in err
    assert "--raw" in err


def test_annotate_warns_when_uds_payload_passed_with_raw(capsys):
    # 0x62 first byte is a UDS SID (0x40-0x7F), not a PCI byte, so warn + suggest
    # dropping --raw. (It also then errors because 0x62 isn't a valid frame type.)
    args = _parse(["-2", "--raw", "-a", "62B004740299"])
    assert bix.run(args) == 1
    err = capsys.readouterr().err
    assert "looks like a UDS response SID" in err
    assert "drop --raw" in err


def test_warning_is_emphasized_and_separated(capsys):
    # The warning must stand out: a WARNING banner, a rule, and a blank line of
    # separation before the table caption that follows on stdout.
    args = _parse(["-2", "-a", "0662B004"])
    assert bix.run(args) == 0
    captured = capsys.readouterr()
    err = captured.err
    assert "⚠ WARNING" in err  # emphasized banner
    assert "─" * 20 in err  # horizontal rule under the banner
    assert err.startswith("\n")  # blank line above sets it apart
    assert err.endswith("\n\n")  # blank line below separates it from the table


def test_annotate_no_warning_for_wellformed_uds_payload(capsys):
    args = _parse(["-2", "-a", "62B004740299"])
    assert bix.run(args) == 0
    assert "WARNING" not in capsys.readouterr().err


def test_annotate_no_warning_for_wellformed_raw_frame(capsys):
    args = _parse(["-2", "--raw", "-a", "0662B004740299"])
    assert bix.run(args) == 0
    assert "WARNING" not in capsys.readouterr().err


def test_looks_like_predicates_are_disjoint():
    # The reliability guarantee: no byte value is both a plausible PCI first byte
    # and a plausible UDS response SID.
    for b in range(256):
        assert not (bix._looks_like_pci_first_byte(b) and bix._looks_like_uds_sid(b))
    assert bix._looks_like_pci_first_byte(0x06)  # SF PCI
    assert bix._looks_like_pci_first_byte(0x10)  # FF PCI
    assert bix._looks_like_pci_first_byte(0x21)  # CF PCI
    assert bix._looks_like_uds_sid(0x62)  # 22xxxx response
    assert bix._looks_like_uds_sid(0x61)  # 21xx response
    assert bix._looks_like_uds_sid(0x7F)  # negative response


# ── Torque + bix (OBDb) columns are hidden by default, shown with --torque ──


def test_annotate_hides_torque_bix_by_default(capsys):
    args = _parse(["-1", "-a", "6101FFEEDD"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "WiCAN" in out and "ISO-TP" in out and "Role" in out
    assert "Torque" not in out  # no Torque column header, no caption
    assert "bix" not in out


def test_annotate_shows_torque_bix_with_flag(capsys):
    args = _parse(["-1", "--torque", "-a", "6101FFEEDD"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Torque" in out
    assert "bix" in out


def test_annotate_obdb_is_alias_for_torque(capsys):
    args = _parse(["-1", "--obdb", "-a", "6101FFEEDD"])
    assert bix.run(args) == 0
    assert "Torque" in capsys.readouterr().out


def test_table_hides_torque_bix_by_default(capsys):
    args = _parse(["--table", "--max", "7"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    # Column headers: WiCAN | ISO-TP | Role only.
    assert "| WiCAN | ISO-TP | Role" in out
    assert "Torque 1" not in out  # the sub-labelled Torque header is gone


def test_table_shows_torque_bix_with_flag(capsys):
    args = _parse(["--table", "--torque", "--max", "7"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Torque 1" in out
    assert "bix" in out


def test_bare_overview_hints_at_torque_flag(capsys):
    args = _parse([])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    # Default overview points the reader at the opt-in flag, in both the legend
    # and the "Go further" block.
    assert "--torque" in out
    assert "add the Torque / bix (OBDb) columns" in out


# ── large payloads / high indices must not crash (Torque letter past ZZ) ──


def _big_payload(data_bytes: int, sid_did: str = "62B004") -> str:
    return sid_did + "00" * data_bytes


def test_annotate_large_payload_default_does_not_crash(capsys):
    # >702 data bytes pushes the Torque index past ZZ (701). The default path
    # hides Torque, so it must render cleanly — it previously crashed because the
    # letter was computed (and raised) even when unused.
    args = _parse(["-2", "-a", _big_payload(800)])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "B900" in out  # rendered well past the ZZ boundary
    assert "Torque" not in out  # hidden by default


def test_annotate_large_payload_torque_shows_numeric_past_zz(capsys):
    # With --torque past ZZ, the Torque column falls back to the numeric index
    # instead of crashing.
    args = _parse(["-2", "--torque", "-a", _big_payload(800)])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    b914 = _row(out, "B914")  # ISO-TP 0x31E → torque 795 (past ZZ=701)
    assert "795" in b914


def test_table_large_max_does_not_crash(capsys):
    for args in (["--table", "--max", "5000"], ["--table", "--torque", "--max", "5000"]):
        assert bix.run(_parse(args)) == 0
        capsys.readouterr()


def test_lookup_high_index_does_not_crash(capsys):
    # A byte-index lookup past ZZ must show the numeric Torque index, not crash.
    for token in ("b99999", "i99999", "w100000"):
        assert bix.run(_parse([token])) == 0
        capsys.readouterr()
