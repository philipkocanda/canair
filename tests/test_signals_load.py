"""Reading a profile's broadcast signal maps (canlib.signals)."""

from canlib.profile import Profile
from canlib.signals import load_signals, signals_dir

PT = """\
messages:
  '0x386':
    name: WHEEL_SPEEDS
    signals:
      WHL_SPD_FL:
        start_bit: 0
        length: 14
"""

BODY = """\
messages:
  '0x548':
    signals: {}
"""


def _profile(tmp_path, *, with_signals=True) -> Profile:
    if with_signals:
        d = tmp_path / "signals"
        d.mkdir()
        (d / "powertrain.yaml").write_text(PT)
        (d / "body.yaml").write_text(BODY)
    return Profile("testcar", tmp_path)


class TestLoadSignals:
    def test_loads_every_bus_sorted(self, tmp_path):
        docs = load_signals(profile=_profile(tmp_path))
        assert [d.bus for d in docs] == ["body", "powertrain"]
        assert docs[1].data["messages"]["0x386"]["name"] == "WHEEL_SPEEDS"
        assert docs[1].path.name == "powertrain.yaml"

    def test_filters_to_one_bus(self, tmp_path):
        docs = load_signals("powertrain", profile=_profile(tmp_path))
        assert [d.bus for d in docs] == ["powertrain"]

    def test_unknown_bus_is_empty(self, tmp_path):
        assert load_signals("nope", profile=_profile(tmp_path)) == []

    def test_absent_dir_is_empty(self, tmp_path):
        assert load_signals(profile=_profile(tmp_path, with_signals=False)) == []

    def test_empty_file_parses_to_empty_dict(self, tmp_path):
        prof = _profile(tmp_path)
        (prof.signals_dir / "blank.yaml").write_text("")
        docs = {d.bus: d.data for d in load_signals(profile=prof)}
        assert docs["blank"] == {}

    def test_absent_dir_is_distinguishable_from_empty(self, tmp_path):
        # The callers' error messages depend on telling these apart.
        missing = _profile(tmp_path / "a", with_signals=False)
        (tmp_path / "a").mkdir(parents=True, exist_ok=True)
        assert not signals_dir(missing).is_dir()

        empty = tmp_path / "b"
        (empty / "signals").mkdir(parents=True)
        assert signals_dir(Profile("b", empty)).is_dir()
        assert load_signals(profile=Profile("b", empty)) == []
