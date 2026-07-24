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
    # (off-by-one) layout that put Torque A at B04.
    args = _parse(["-1", "-a", "6101FFEEDDCC"])
    assert bix.run(args) == 0
    lines = capsys.readouterr().out.splitlines()
    b03 = next(ln for ln in lines if ln.strip().startswith("B03"))
    assert "A" in b03 and " 0 " in b03  # Torque A, bix 0 on the first data byte
    b01 = next(ln for ln in lines if ln.strip().startswith("B01"))
    assert "SID" in b01  # single-frame SID sits at B01 (one PCI byte, not two)


def test_annotate_multi_frame_torque_unchanged(capsys):
    # Multi-frame (>7 payload bytes) keeps the 2-byte FF PCI layout: for a 22xxxx
    # DID, the first data byte / Torque A is at B05.
    args = _parse(["-2", "-a", "62B004FFEEDDCCBBAA9988776655"])
    assert bix.run(args) == 0
    lines = capsys.readouterr().out.splitlines()
    b05 = next(ln for ln in lines if ln.strip().startswith("B05"))
    assert "A" in b05 and " 0 " in b05


# ── Torque 1 vs Torque 2 is discoverable (the mapping isn't fixed) ──


def test_annotate_names_active_torque_variant(capsys):
    args = _parse(["-1", "-a", "6101FF"])
    assert bix.run(args) == 0
    assert "Torque 1" in capsys.readouterr().out


def test_annotate_sub2_names_torque_2_variant(capsys):
    args = _parse(["-2", "-a", "62B004FF"])
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
    args = _parse(["-2"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "DID" in out
    assert "2 DID bytes" in out  # Torque column note reflects the 2-byte subfunction


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
    a1 = _parse(["-1", "-a", "6101FFEEDD"])
    assert bix.run(a1) == 0
    out1 = capsys.readouterr().out
    assert "A" in _row(out1, "B03")  # -1: first data byte at B03

    a2 = _parse(["-2", "-a", "62B004FFEEDD"])
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
