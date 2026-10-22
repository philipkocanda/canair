"""Tests for spotting a reply saved under the wrong ECU (``canlib.misfiled``).

Two ECUs that answer the same PID with the same header defeat the echo check: a queued reply
from one lands in the next ECU's request slot and is stored under the wrong ``rx``. See
``plans/2026-10-21-cross-ecu-reply-misfile.md``.
"""

from __future__ import annotations

import json

import pytest

from canlib import profile
from canlib.commands.validate import _run_captures
from canlib.commands.validate.misfiled import misfile_warnings
from canlib.misfiled import PayloadSample, find_misfiled
from canlib.pids import clear_cache

MCU, VCU, BMS = 0x7EB, 0x7EA, 0x7EC
NAMES = {MCU: "MCU", VCU: "VCU", BMS: "BMS"}


def _reply(n: int, seed: int = 0) -> bytes:
    """A positive ``61 F2`` reply of ``n`` bytes with enough distinct values to be substantial."""
    return bytes([0x61, 0xF2] + [(seed + i * 7) % 256 for i in range(n - 2)])


def _samples(*groups: tuple[int, bytes, int]) -> list[PayloadSample]:
    out: list[PayloadSample] = []
    for rx, payload, count in groups:
        for _ in range(count):
            out.append(PayloadSample(len(out), rx, "21F2", payload))
    return out


def test_wrong_length_matching_another_ecu_is_flagged():
    samples = _samples((MCU, _reply(67), 20), (VCU, _reply(86, seed=1), 9))
    samples.append(PayloadSample(len(samples), MCU, "21F2", _reply(86, seed=2)))
    [m] = find_misfiled(samples)
    assert m.sample.handle == len(samples) - 1
    assert m.likely_rx == VCU
    assert (m.own_length, m.likely_length) == (67, 86)
    assert not m.identical


def test_identical_and_wrong_length_reports_both():
    vcu = _reply(86, seed=1)
    samples = _samples((MCU, _reply(67), 20), (VCU, vcu, 9), (MCU, vcu, 1))
    [m] = find_misfiled(samples)
    assert m.likely_rx == VCU and m.length_mismatch and m.identical


def test_identical_same_length_reply_is_flagged():
    """Same-length ECUs: only the identical-bytes rule can catch it."""
    shared = _reply(40, seed=3)
    samples = _samples((MCU, _reply(40), 10), (VCU, shared, 4), (MCU, shared, 1))
    [m] = find_misfiled(samples)
    assert m.sample.rx == MCU and m.likely_rx == VCU
    assert m.identical and not m.length_mismatch


def test_owner_with_more_copies_is_not_flagged():
    shared = _reply(40, seed=3)
    samples = _samples((VCU, shared, 4), (MCU, _reply(40), 3), (MCU, shared, 1))
    assert [m.sample.rx for m in find_misfiled(samples)] == [MCU]


@pytest.mark.parametrize(
    "payload",
    [bytes.fromhex("5001"), bytes([0x61, 0xF2] + [0xFF] * 30)],
    ids=["short", "low-entropy"],
)
def test_unsubstantial_identical_replies_are_not_flagged(payload):
    assert find_misfiled(_samples((MCU, payload, 3), (VCU, payload, 3))) == []


def test_negative_responses_are_ignored():
    nrc = bytes.fromhex("7F2111")
    samples = _samples((MCU, _reply(67), 20), (VCU, nrc, 10), (MCU, nrc, 1))
    assert find_misfiled(samples) == []


def test_variable_length_pid_has_no_signature():
    """No length dominates, so an odd length proves nothing."""
    samples = _samples((MCU, _reply(60), 5), (MCU, _reply(67), 5), (VCU, _reply(86, seed=1), 9))
    samples.append(PayloadSample(len(samples), MCU, "21F2", _reply(86, seed=2)))
    assert find_misfiled(samples) == []


def test_thinly_sampled_ecu_has_no_signature():
    samples = _samples((MCU, _reply(67), 3), (VCU, _reply(86, seed=1), 9))
    samples.append(PayloadSample(len(samples), MCU, "21F2", _reply(86, seed=2)))
    assert find_misfiled(samples) == []


def test_pid_case_is_normalised():
    samples = _samples((MCU, _reply(67), 20), (VCU, _reply(86, seed=1), 9))
    samples.append(PayloadSample(len(samples), MCU, "21f2", _reply(86, seed=2)))
    assert len(find_misfiled(samples)) == 1


def _capture_file(tmp_path, misfiled: bool):
    caps = [
        {"rx": "0x7EB", "pid": "21F2", "payload": _reply(67).hex(), "time": f"10:00:{i:02d}"}
        for i in range(20)
    ] + [
        {"rx": "0x7EA", "pid": "21F2", "payload": _reply(86, 1).hex(), "time": f"10:01:{i:02d}"}
        for i in range(9)
    ]
    if misfiled:
        stray = {"rx": "0x7EB", "pid": "21F2", "payload": _reply(86, 1).hex(), "time": "10:07:16"}
        caps.append(stray)
    path = tmp_path / "2026-08-08.json"
    path.write_text(
        json.dumps({"sessions": [{"date": "2026-08-08", "label": "t", "captures": caps}]})
    )
    return path


def test_validate_warning_names_both_ecus_and_the_capture(tmp_path):
    path = _capture_file(tmp_path, misfiled=True)
    [warning] = misfile_warnings([path], NAMES)[path]
    assert warning.location == "sessions[0].captures[29] (MCU 21F2 @ 10:07:16)"
    assert "86 bytes like VCU's 21F2 replies (9 captures), not MCU's usual 67" in warning.message
    assert "byte-identical" in warning.message
    assert "misfiled under MCU" in warning.message


def test_clean_corpus_has_no_warnings(tmp_path):
    assert misfile_warnings([_capture_file(tmp_path, misfiled=False)], NAMES) == {}


@pytest.fixture
def _restore_active_profile():
    saved = profile._active
    clear_cache()
    yield
    profile._active = saved
    clear_cache()


def test_validate_captures_reports_it_as_a_warning(tmp_path, capsys, _restore_active_profile):
    """End to end: a soft warning in `validate captures`, never an error."""
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    for name, tx in (("vcu", "0x7E2"), ("mcu", "0x7E3")):
        (root / "ecus" / f"{name}.yaml").write_text(
            f"{name.upper()}:\n  tx_id: {tx}\n  identity:\n    id_protocol: KWP2000\n"
        )
    path = _capture_file(tmp_path, misfiled=True)
    path.rename(root / "captures" / path.name)
    profile.set_active(str(root))
    clear_cache()
    assert _run_captures() == 0
    out = capsys.readouterr().out
    assert "probably VCU's reply misfiled under MCU" in out


def test_a_tie_flags_neither_ecu():
    """Equal copies on both sides is no evidence either way — not grounds to flag both."""
    shared = _reply(40, seed=3)
    samples = _samples(
        (MCU, _reply(40), 5), (VCU, _reply(40, seed=9), 5), (MCU, shared, 2), (VCU, shared, 2)
    )
    assert find_misfiled(samples) == []


def test_a_genuinely_shared_answer_is_not_a_misfile():
    """Every ECU returns the same VIN from 22F190, however unevenly it was polled."""
    vin = bytes.fromhex("62F190") + b"KMHC851CGLU000000"
    samples = [PayloadSample(i, BMS, "22F190", vin) for i in range(6)]
    samples += [PayloadSample(6 + i, MCU, "22F190", vin) for i in range(2)]
    assert find_misfiled(samples) == []


def test_the_response_header_does_not_count_as_substance():
    """``62 D0 01`` + a page of FF has four distinct bytes, all but one in the echo."""
    page = bytes.fromhex("62D001") + b"\xff" * 30
    samples = [PayloadSample(i, MCU, "22D001", page) for i in range(3)]
    samples.append(PayloadSample(3, VCU, "22D001", page))
    samples.append(PayloadSample(4, VCU, "22D001", bytes.fromhex("62D001") + bytes(range(30))))
    assert find_misfiled(samples) == []


def test_one_ecus_history_under_two_rx_values_is_not_compared_with_itself():
    """Legacy ``TX + 0x08`` saves on a profile whose ECU answers elsewhere."""
    legacy, current = 0x7EC, 0x804  # BMS at TX 0x7E4 on a +0x20 profile
    own = _reply(40, seed=3)
    samples = _samples((current, own, 4), (current, _reply(40), 4), (legacy, own, 1))
    assert [m.sample.rx for m in find_misfiled(samples)] == [legacy]  # unfolded: a false hit
    assert find_misfiled(samples, {legacy: current}) == []


def test_legacy_aliases_fold_only_addresses_no_other_ecu_owns():
    from canlib.commands.validate.misfiled import legacy_rx_aliases

    ecus = {
        0x7E4: {"name": "BMS", "rx_id": 0x804},  # legacy 0x7EC is free → folded
        0x7E2: {"name": "VCU", "rx_id": 0x802},  # legacy 0x7EA is MCU's real rx → kept
        0x7E3: {"name": "MCU", "rx_id": 0x7EA},
        0x7A0: {"name": "BCM", "rx_id": 0x7A8},  # conventional → nothing to fold
    }
    assert legacy_rx_aliases(ecus) == {0x7EC: 0x804, 0x7EB: 0x7EA}
