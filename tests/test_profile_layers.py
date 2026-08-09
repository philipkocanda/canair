"""Tests for layered profiles — a read-only base plus the user's own capture layer.

A contributor who installs canair cannot record into the bundled profile they are
using (the install snapshot is wiped by the next reinstall, and a git checkout is
not always writable). Layering lets them keep the bundled *definitions* while
their captures land in ``~/.config/canair/profiles/<name>/``, declared by an
``extends:`` marker. These tests pin the resolution order, the write target, the
read-only refusals, and that a non-layered profile is completely unaffected.

Plan: ``plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md`` §B.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from canlib.capture_store import load_all_captures
from canlib.commands.captures import layers as capture_layers
from canlib.commands.profile import _cmd_list, _cmd_overlay, _cmd_show
from canlib.profile import (
    Profile,
    ProfileError,
    extends_target,
    profile_layers,
    require_writable_definitions,
    resolve_profile,
)
from canlib.profile_create import overlay_profile

CAPTURES_CMD = Path(__file__).resolve().parents[1] / "canlib" / "commands" / "captures"
CANLIB_CMD = CAPTURES_CMD.parent

# Anything calling one of these rewrites a capture file, so it owes the base-layer
# check. Discovering the modules beats listing them: the list cannot go stale.
_CAPTURE_WRITERS = (
    "delete_capture",
    "set_capture_note",
    "set_session_states",
    "set_session_state_spans",
)
_MUTATING_CAPTURE_MODULES = {
    p.name
    for p in CAPTURES_CMD.glob("*.py")
    if any(f"{w}(" in p.read_text() for w in _CAPTURE_WRITERS)
}


@pytest.fixture(autouse=True)
def _isolate_profile_state():
    """Drop the memoized active profile so a tmp_path Profile cannot leak."""
    from canlib import config, profile

    profile._active = None
    config.load_config.cache_clear()
    yield
    profile._active = None
    config.load_config.cache_clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A bundled root at ``tmp_path/profiles`` and a user root under XDG config.

    ``CANAIR_PROFILES_DIR`` is cleared: it outranks the user directory, so with it
    set an overlay there would never be read (and ``profile overlay`` refuses).
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
    monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", tmp_path / "profiles")
    from canlib import config

    config.load_config.cache_clear()
    return argparse.Namespace(
        bundled=tmp_path / "profiles",
        user=tmp_path / "cfg" / "canair" / "profiles",
    )


def _bundled(env, name: str = "ev6") -> Path:
    """A minimal discoverable base profile with one ECU and one capture file."""
    root = env.bundled / name
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text(f'car_model: "{name}"\ninit: "ATSP6;"\n')
    (root / "ecus" / "bms.yaml").write_text("BMS:\n  tx_id: 0x7E4\n")
    (root / "captures").mkdir()
    _seed(root / "captures" / "2026-01-01.json", "upstream drive")
    return root


def _seed(
    path: Path,
    label: str,
    *,
    time: str = "09:00:00",
    date: str = "2026-01-01",
    states: list[str] | None = None,
) -> None:
    session: dict = {
        "date": date,
        "label": label,
        "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": time}],
    }
    if states:
        session["vehicle_states"] = states
    path.write_text(json.dumps({"sessions": [session]}))


def _overlay(env, name: str = "ev6") -> Path:
    """The user's layer, as ``profile overlay`` would leave it."""
    dest = env.user / name
    (dest / "captures").mkdir(parents=True)
    (dest / "profile.yaml").write_text(f"extends: {name}\n")
    return dest


def _with_predicates(root: Path) -> None:
    """Give a profile a predicate-bearing state vocabulary.

    ``cmd_backfill_state_spans`` bails out before touching anything when no state
    has a ``when:`` rule, so a layering test needs at least one.
    """
    (root / "vehicle_states.yaml").write_text(
        "states:\n  - name: ACC\n    when: BMS.SOC == 1\n  - name: READY\n    when: BMS.SOC == 2\n"
    )


def _force_a_timeline(monkeypatch) -> None:
    """Make every session look like it has a real timeline to write.

    Building a decodable multi-cycle fixture would test the *inference*, which is
    covered elsewhere. What needs pinning here is which file the command opens and
    whether it consults the base-layer policy.
    """
    from canlib import state_infer

    spans = [
        {"at": "09:00:00", "states": ["ACC"]},
        {"at": "09:01:00", "states": ["READY"]},
    ]

    def fake(recorded, caps, rules, ecu_index, **kw):
        return state_infer.SessionSpans(
            spans=list(spans),
            inference=state_infer.SpanInference(
                spans=list(spans), n_cycles=2, n_informative=2, union=["ACC", "READY"]
            ),
        )

    monkeypatch.setattr("canlib.commands.captures.backfill_spans.session_state_spans", fake)


class TestResolution:
    def test_a_plain_user_profile_still_shadows(self, env):
        """No ``extends:`` means the old shadowing behaviour, unchanged."""
        _bundled(env)
        shadow = env.user / "ev6"
        (shadow / "ecus").mkdir(parents=True)
        (shadow / "profile.yaml").write_text('car_model: "mine"\n')
        prof = resolve_profile("ev6")
        assert prof.root == shadow
        assert prof.overlays == ()
        assert not prof.layered

    def test_extends_layers_instead_of_shadowing(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        prof = resolve_profile("ev6")
        assert prof.root == base
        assert prof.overlays == (dest,)
        assert prof.layered

    def test_definitions_come_from_the_base_and_writes_go_to_the_overlay(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        prof = resolve_profile("ev6")
        assert prof.ecus_dir == base / "ecus"
        assert prof.states_file == base / "vehicle_states.yaml"
        assert prof.write_root == dest
        assert prof.captures_dir == dest / "captures"
        assert prof.dtc_log_file == dest / "dtc_log.yaml"
        assert prof.out_dir == dest / "out"
        assert prof.capture_layers == [base / "captures", dest / "captures"]

    def test_profile_layers_is_most_specific_first(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        assert profile_layers("ev6") == [dest, base]

    def test_an_explicit_path_to_the_overlay_still_layers(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        prof = resolve_profile(str(dest))
        assert prof.root == base
        assert prof.overlays == (dest,)

    def test_an_explicit_path_to_the_base_does_not_layer(self, env):
        """Naming the base directly is how you opt *out* of your own layer."""
        base = _bundled(env)
        _overlay(env)
        prof = resolve_profile(str(base))
        assert prof.root == base
        assert prof.overlays == ()

    def test_a_dangling_overlay_is_an_error(self, env):
        """An ``extends:`` with nothing underneath cannot silently read as a profile."""
        _overlay(env)  # no bundled ev6 at all
        with pytest.raises(ProfileError, match="extends"):
            resolve_profile("ev6")

    def test_layering_onto_a_different_name_is_refused(self, env):
        _bundled(env)
        dest = env.user / "ev6"
        (dest / "captures").mkdir(parents=True)
        (dest / "profile.yaml").write_text("extends: kona\n")
        with pytest.raises(ProfileError, match="differently-named"):
            resolve_profile("ev6")

    def test_extends_target_reads_the_marker(self, env):
        base = _bundled(env)
        assert extends_target(base) is None
        assert extends_target(_overlay(env)) == "ev6"

    def test_profile_is_backward_compatible_without_overlays(self, tmp_path):
        """Every existing ``Profile(name, root)`` construction keeps working."""
        prof = Profile("x", tmp_path)
        assert prof.overlays == ()
        assert prof.write_root == tmp_path
        assert prof.captures_dir == tmp_path / "captures"
        assert prof.capture_layers == [tmp_path / "captures"]


class TestOverlayCommand:
    def _args(self, **kw) -> argparse.Namespace:
        base = {"name": "ev6", "profiles_dir": None, "set_default": False}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_creates_the_marker_and_an_empty_captures_dir(self, env, capsys):
        base = _bundled(env)
        assert _cmd_overlay(self._args()) == 0
        dest = env.user / "ev6"
        assert (dest / "captures").is_dir()
        assert extends_target(dest) == "ev6"
        # Nothing is copied — the base keeps tracking upstream.
        assert not (dest / "ecus").exists()
        assert not (dest / "captures" / "2026-01-01.json").exists()
        out = capsys.readouterr().out
        assert "Layered 'ev6'" in out
        assert str(base) in out
        assert str(dest) in out

    def test_unknown_profile_lists_what_exists(self, env, capsys):
        _bundled(env)
        assert _cmd_overlay(self._args(name="kona")) == 1
        assert "ev6" in capsys.readouterr().err

    def test_refuses_to_recreate_an_existing_layer(self, env, capsys):
        _bundled(env)
        assert _cmd_overlay(self._args()) == 0
        assert _cmd_overlay(self._args()) == 1
        assert "already exists" in capsys.readouterr().err

    def test_refuses_when_a_higher_precedence_root_would_keep_winning(
        self, env, monkeypatch, capsys
    ):
        _bundled(env)
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(env.bundled))
        assert _cmd_overlay(self._args()) == 2
        assert "outranks" in capsys.readouterr().err
        assert not (env.user / "ev6").exists()

    def test_set_default_records_the_name(self, env, tmp_path):
        _bundled(env)
        assert _cmd_overlay(self._args(set_default=True)) == 0
        cfg = (tmp_path / "cfg" / "canair" / "config.yaml").read_text()
        assert "default_profile: ev6" in cfg

    def test_overlay_profile_returns_both_roots(self, env):
        base = _bundled(env)
        got_base, dest = overlay_profile("ev6")
        assert got_base == base
        assert dest == env.user / "ev6"


class TestDefinitionsAreReadOnly:
    def test_guard_passes_for_an_ordinary_profile(self, env):
        _bundled(env)
        prof = resolve_profile("ev6")
        assert require_writable_definitions(prof) is prof

    def test_guard_refuses_a_layered_profile(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        with pytest.raises(ProfileError) as excinfo:
            require_writable_definitions(resolve_profile("ev6"))
        msg = str(excinfo.value)
        assert str(base) in msg
        assert str(dest) in msg
        assert "profile adopt ev6" in msg

    @pytest.mark.parametrize(
        "module, func, extra",
        [
            (
                "canlib.commands.pids",
                "cmd_upsert_param",
                {"values": None, "bits": None, "ptype": None, "ecu": "BMS", "no_validate": False},
            ),
            ("canlib.commands.states", "cmd_add", {"name": "TOWING"}),
            ("canlib.commands.groups", "cmd_add", {"name": "mine"}),
            ("canlib.commands.signals", "cmd_upsert", {}),
        ],
    )
    def test_authoring_commands_refuse(self, env, module, func, extra):
        """Each editor's funnel raises before it can touch the base's YAML."""
        import importlib

        _bundled(env)
        _overlay(env)
        mod = importlib.import_module(module)
        from canlib import profile as profile_mod

        profile_mod._active = resolve_profile("ev6")
        # Beyond what the command reads before the guard, every attribute is absent
        # on purpose — an AttributeError here would mean the guard ran too late.
        ns = argparse.Namespace(dir=None, **extra)
        with pytest.raises(ProfileError, match="read-only"):
            getattr(mod, func)(ns)


class TestLayeredCaptureReads:
    def test_both_layers_are_read(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        _seed(dest / "captures" / "2026-01-02.json", "my drive", date="2026-01-02")
        rows = load_all_captures_for("ev6")
        labels = [r["session_label"] for r in rows]
        assert labels == ["upstream drive", "my drive"]
        # Each row still points at the real file it came from.
        assert rows[0]["_path"] == str(base / "captures" / "2026-01-01.json")
        assert rows[1]["_path"] == str(dest / "captures" / "2026-01-02.json")

    def test_rows_are_chronological_across_layers(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        # The user's file sorts first by name but its session is later in the day.
        _seed(base / "captures" / "2026-01-01.json", "late", time="18:00:00")
        _seed(dest / "captures" / "2026-01-01.json", "early", time="06:00:00")
        rows = load_all_captures_for("ev6")
        assert [r["session_label"] for r in rows] == ["early", "late"]

    def test_a_session_present_in_both_layers_is_not_duplicated(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        # A contributor's layer that was pushed upstream now holds the same session.
        (dest / "captures" / "2026-01-01.json").write_text(
            (base / "captures" / "2026-01-01.json").read_text()
        )
        rows = load_all_captures_for("ev6")
        assert len(rows) == 1
        # The base copy wins, so the duplicate stays read-only.
        assert rows[0]["_path"] == str(base / "captures" / "2026-01-01.json")

    def test_a_single_layer_result_is_unchanged(self, env):
        base = _bundled(env)
        _seed(base / "captures" / "2026-01-02.json", "later", date="2026-01-02")
        prof = resolve_profile("ev6")
        assert not prof.layered
        rows = load_all_captures(prof.captures_dir)
        assert [r["session_label"] for r in rows] == ["upstream drive", "later"]


def load_all_captures_for(name: str) -> list[dict]:
    """Load through the active profile so the layer list is exercised."""
    from canlib import profile as profile_mod

    profile_mod._active = resolve_profile(name)
    return load_all_captures()


class TestBaseLayerWritePolicy:
    def test_nothing_is_read_only_without_a_second_layer(self, env):
        base = _bundled(env)
        prof = resolve_profile("ev6")
        assert not prof.layered
        f = base / "captures" / "2026-01-01.json"
        assert capture_layers.read_only_files([f], prof.captures_dir) == []
        assert capture_layers.refusal([f], "deleted", prof.captures_dir) is None

    def test_base_files_are_read_only_when_layered(self, env):
        base = _bundled(env)
        dest = _overlay(env)
        from canlib import profile as profile_mod

        profile_mod._active = resolve_profile("ev6")
        mine = dest / "captures" / "2026-01-02.json"
        _seed(mine, "mine", date="2026-01-02")
        theirs = base / "captures" / "2026-01-01.json"
        assert capture_layers.read_only_files([mine, theirs]) == [theirs]
        msg = capture_layers.refusal([mine, theirs], "deleted")
        assert msg is not None
        assert str(theirs) in msg
        assert str(mine) not in msg
        assert "cannot be deleted" in msg
        assert "profile adopt ev6" in msg

    def test_own_files_are_writable(self, env):
        _bundled(env)
        dest = _overlay(env)
        from canlib import profile as profile_mod

        profile_mod._active = resolve_profile("ev6")
        mine = dest / "captures" / "2026-01-02.json"
        _seed(mine, "mine", date="2026-01-02")
        assert capture_layers.read_only_files([mine]) == []
        assert capture_layers.refusal([mine], "deleted") is None


class TestBaseLayerMutationsAreRefused:
    """The policy is wired into every capture-mutating command, not just the helper."""

    def test_delete_refuses_a_base_layer_capture(self, env, capsys):
        base = _bundled(env)
        _overlay(env)
        from canlib.commands.captures.delete import cmd_delete

        entries = load_all_captures_for("ev6")
        before = (base / "captures" / "2026-01-01.json").read_text()
        assert cmd_delete(entries, "BMS:2101", assume_yes=True) == 1
        assert "cannot be deleted" in capsys.readouterr().err
        assert (base / "captures" / "2026-01-01.json").read_text() == before

    def test_delete_still_previews_a_base_layer_capture(self, env, capsys):
        """A ``--dry-run`` is a read; refusing it would hide what is there."""
        _bundled(env)
        _overlay(env)
        from canlib.commands.captures.delete import cmd_delete

        entries = load_all_captures_for("ev6")
        assert cmd_delete(entries, "BMS:2101", dry_run=True) == 0
        assert "Would delete" in capsys.readouterr().out

    def test_delete_allows_an_overlay_capture(self, env):
        _bundled(env)
        dest = _overlay(env)
        _seed(dest / "captures" / "2026-01-02.json", "mine", date="2026-01-02")
        from canlib.commands.captures.delete import cmd_delete

        entries = [e for e in load_all_captures_for("ev6") if e["date"] == "2026-01-02"]
        assert cmd_delete(entries, "BMS:2101", assume_yes=True) == 0

    def test_backfill_state_spans_refuses_a_base_layer_session(self, env, capsys, monkeypatch):
        base = _bundled(env)
        _with_predicates(base)
        _overlay(env)
        _seed(base / "captures" / "2026-01-01.json", "upstream", states=["ACC", "READY"])
        _force_a_timeline(monkeypatch)
        from canlib.commands.captures.backfill_spans import cmd_backfill_state_spans

        entries = load_all_captures_for("ev6")
        before = (base / "captures" / "2026-01-01.json").read_text()
        assert cmd_backfill_state_spans(entries, assume_yes=True) == 1
        assert "cannot be back-filled" in capsys.readouterr().err
        assert (base / "captures" / "2026-01-01.json").read_text() == before

    def test_backfill_state_spans_writes_to_the_file_the_session_came_from(self, env, monkeypatch):
        """Both layers can hold the same file name, so the row's path is the truth."""
        base = _bundled(env)
        _with_predicates(base)
        dest = _overlay(env)
        mine = dest / "captures" / "2026-01-01.json"
        _seed(mine, "mine", time="10:00:00", states=["ACC", "READY"])
        _force_a_timeline(monkeypatch)
        from canlib.commands.captures.backfill_spans import cmd_backfill_state_spans

        theirs = base / "captures" / "2026-01-01.json"
        before = theirs.read_text()
        entries = [e for e in load_all_captures_for("ev6") if e["time"] == "10:00:00"]
        assert cmd_backfill_state_spans(entries, assume_yes=True) == 0
        assert "state_spans" in json.loads(mine.read_text())["sessions"][0]
        assert theirs.read_text() == before

    def test_a_base_files_live_spans_do_not_shield_the_overlays_same_name(self, env, monkeypatch):
        """Span provenance is keyed by path, not by file name.

        Both layers routinely hold ``<date>.json``. Keyed by name, the base's
        live-recorded timeline would make the command treat the *overlay's*
        unrelated session as live and silently decline to write it.
        """
        base = _bundled(env)
        _with_predicates(base)
        theirs = base / "captures" / "2026-01-01.json"
        data = json.loads(theirs.read_text())
        data["sessions"][0]["state_spans"] = {
            "source": "live",
            "spans": [{"at": "09:00:00", "states": ["ACC"]}],
        }
        theirs.write_text(json.dumps(data))

        dest = _overlay(env)
        mine = dest / "captures" / "2026-01-01.json"
        _seed(mine, "mine", time="10:00:00", states=["ACC", "READY"])
        _force_a_timeline(monkeypatch)
        from canlib.commands.captures.backfill_spans import cmd_backfill_state_spans

        entries = load_all_captures_for("ev6")
        assert cmd_backfill_state_spans(entries, assume_yes=True) == 0
        assert json.loads(mine.read_text())["sessions"][0]["state_spans"]["source"] == "backfill"
        # The base session keeps its live timeline: it is protected, not merely skipped.
        assert json.loads(theirs.read_text())["sessions"][0]["state_spans"]["source"] == "live"

    @pytest.mark.parametrize("module", sorted(_MUTATING_CAPTURE_MODULES))
    def test_every_mutating_command_consults_the_policy(self, module):
        """Pins the wiring so a new mutation path cannot skip the refusal.

        The module list is *discovered* from the writers each one calls rather than
        hand-maintained, so adding a capture-mutating command fails this test until
        it consults the policy.
        """
        src = (CAPTURES_CMD / module).read_text()
        assert "layers.refusal(" in src or "layers.read_only_files(" in src, (
            f"{module} writes to captures/ but does not check the base-layer policy"
        )

    def test_the_discovery_finds_every_known_mutation_path(self):
        """A canary: if the writer names change, the pin above must not go quiet."""
        assert _MUTATING_CAPTURE_MODULES >= {
            "backfill.py",
            "backfill_spans.py",
            "delete.py",
            "set_state.py",
            "step_tui.py",
        }


class TestLayerAwareReaders:
    """Readers that bypass ``load_all_captures`` must still see the base layer."""

    def test_coverage_sees_base_layer_payloads(self, env):
        _bundled(env)
        _overlay(env)
        from canlib import profile as profile_mod
        from canlib.commands.coverage import load_longest_payloads

        profile_mod._active = resolve_profile("ev6")
        # Without the layer walk a layered profile reports false "NO CAPTURE".
        assert ("BMS", "2101") in load_longest_payloads()

    def test_validate_captures_reads_every_layer(self):
        """A schema break in the base still breaks analysis, so it must be reported."""
        src = (CANLIB_CMD / "validate" / "captures.py").read_text()
        assert "capture_layers" in src


class TestLayeringIsVisible:
    def test_show_names_both_roots(self, env, capsys):
        base = _bundled(env)
        dest = _overlay(env)
        assert _cmd_show(argparse.Namespace(name="ev6", profiles_dir=None)) == 0
        out = capsys.readouterr().out
        assert f"root:       {base}" in out
        assert f"overlay:    {dest}" in out
        assert f"{base / 'captures'}  (base layer, read-only)" in out

    def test_list_marks_a_layered_profile(self, env, capsys):
        base = _bundled(env)
        _overlay(env)
        assert _cmd_list(argparse.Namespace(profiles_dir=None)) == 0
        out = capsys.readouterr().out
        assert f"layered over {base}" in out

    def test_list_says_nothing_for_a_plain_profile(self, env, capsys):
        _bundled(env)
        assert _cmd_list(argparse.Namespace(profiles_dir=None)) == 0
        assert "layered over" not in capsys.readouterr().out
