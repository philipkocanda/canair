"""Each live subcommand runs the operation it names; nothing picks one from the flags.

``dispatch_mode`` used to choose a handler by testing which of sixteen mode-selector attributes
was truthy, in an order that mattered (``multi`` + ``monitor`` before bare ``multi``). A command
now hands :func:`~canair.cli.live.runtime.run_live` its :class:`LiveOperation`. Two things
can go wrong that the type checker cannot see, and both are pinned here:

* **which operation** — a command wired to the wrong handler, or to none, falls through to the
  wrong behaviour (the interactive REPL, for instance, where a read was meant).
* **the namespace** — a handler reads ``args.x`` directly, so the command's parser must define
  ``x``. Before, a shared table of defaults filled the gaps; now a missing flag is an
  ``AttributeError`` after the device is already connected.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from canair.cli import build_parser
from canair.cli.live import runtime
from canair.cli.live.handlers import actuators, diagnostics, elm_only, memory, multi, reads, scans

# (argv, handler name, records, scan types): the operation each live subcommand reaches.
CASES = [
    (["read", "BMS:2101"], "handle_multi", True, set()),
    (["read", "BMS:2101", "--param", "SOC"], "handle_multi", True, set()),
    (["read", "--param", "SOC"], "handle_param", False, set()),
    (["read"], "handle_interactive", False, set()),
    (["monitor", "BMS:2101"], "handle_monitor", True, set()),
    (["raw", "7E4:2101"], "handle_raw", True, set()),
    (["repl"], "handle_interactive", False, set()),
    (["identity", "BMS"], "handle_identity", False, set()),
    (["scan", "range", "BMS", "--range", "01-FF"], "handle_scan", True, {"scan"}),
    (["scan", "iocontrol", "BCM"], "handle_iocontrol_scan", False, {"iocontrol"}),
    (["scan", "routines", "BCM"], "handle_routines_scan", False, {"routines"}),
    (["scan", "sessions", "BCM"], "handle_sessions_scan", False, set()),
    (["scan", "memory", "BCM", "--range", "0-FF", "--yes"], "handle_memory_scan", False, set()),
    (["memory", "dump", "BCM", "0-FF", "--yes"], "handle_memory_dump", False, set()),
    (["discover"], "handle_discover", True, set()),
    (["dtc", "read", "BMS"], "handle_dtc", True, set()),
    (["dtc", "read", "--all"], "handle_dtc", True, set()),
    (["io", "IGPM"], "handle_iocontrol", False, set()),
    (["routines", "BCM"], "handle_routines", False, set()),
    (["action", "run", "sleep"], "handle_action", False, set()),
]

IDS = [" ".join(argv) for argv, *_ in CASES]


@pytest.fixture
def reached(monkeypatch):
    """Run a command's ``func`` and return the ``(args, operation)`` it handed the runtime."""
    from canair.cli.commands import monitor, read

    seen: list = []

    def fake_run_live(args, operation):
        seen.append((args, operation))
        return 0

    monkeypatch.setattr(runtime, "run_live", fake_run_live)
    monkeypatch.setattr("canair.cli.live.parser.run_live", fake_run_live)
    # The pre-flight reads of read/monitor/scan need a profile, not a device.
    monkeypatch.setattr(
        "canair.live.monitor.controller.query_ecu_error", lambda steps, pids, **__: None
    )
    monkeypatch.setattr("canair.cli.live.load_pids", lambda *_, **__: {})
    monkeypatch.setattr(monitor, "run_live", fake_run_live)
    monkeypatch.setattr(read, "run_live", fake_run_live)
    for name in (
        "scan.memory_kind",
        "memory.parser",
        "discover",
        "dtc",
        "io",
        "routines",
    ):
        module = __import__(f"canair.cli.commands.{name}", fromlist=["run_live"])
        if hasattr(module, "run_live"):
            monkeypatch.setattr(module, "run_live", fake_run_live)
    monkeypatch.setattr("canair.cli.commands.scan.run.run_live", fake_run_live)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def go(argv: list[str]):
        args = build_parser().parse_args(argv)
        seen.clear()
        rc = args.func(args)
        assert rc == 0, f"{argv} returned {rc}"
        assert len(seen) == 1, f"{argv} did not reach run_live exactly once"
        return seen[0]

    return go


@pytest.mark.parametrize(("argv", "handler", "records", "scan_types"), CASES, ids=IDS)
def test_command_reaches_its_operation(reached, argv, handler, records, scan_types):
    _, operation = reached(argv)
    assert operation.handler.__name__ == handler
    assert operation.records is records
    assert set(operation.scan_types) == scan_types


def test_monitor_hands_its_options_to_the_raw_transport(reached):
    """The raw transport polls with its pipelined client, which needs the same options."""
    _, operation = reached(["monitor", "BMS:2101", "--interval", "2"])
    assert operation.monitor is not None
    assert operation.monitor.interval == 2.0
    assert [s["ecu"] for s in operation.monitor.query_steps] == ["BMS"]
    assert operation.monitor.keep_mode == "all"


def test_monitor_brings_its_own_screen(reached):
    """The live layer runs whatever screen it is given; the command gives it the Textual app."""
    _, operation = reached(["monitor", "BMS:2101"])
    assert operation.monitor is not None
    assert operation.monitor.interactive is not None


def test_read_with_steps_wins_over_param(reached):
    """The old table tested ``multi`` before ``param``; a command with both still reads steps."""
    _, operation = reached(["read", "BMS:2101", "--param", "SOC"])
    assert operation.handler is multi.handle_multi


def _direct_reads(function) -> set[str]:
    """Attribute names a handler reads as ``args.name`` (``getattr`` with a default is safe)."""
    tree = ast.parse(inspect.getsource(function).lstrip())
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "args"
        and isinstance(node.ctx, ast.Load)
    }


@pytest.mark.parametrize(("argv", "handler", "records", "scan_types"), CASES, ids=IDS)
def test_parser_defines_what_the_handler_reads(reached, argv, handler, records, scan_types):
    args, operation = reached(argv)
    if operation.monitor is not None:
        # The monitor handler closes over its options; the builder is what reads the flags.
        reads_ = _direct_reads(multi.monitor_options)
    else:
        reads_ = _direct_reads(operation.handler)
    missing = sorted(name for name in reads_ if not hasattr(args, name))
    assert not missing, (
        f"`canair {' '.join(argv)}` never defines {missing}, but its handler reads it"
    )


def test_every_handler_is_reached_by_some_command():
    """A handler no command names is dead code (``handle_ecu`` and ``handle_skm_wake`` were)."""
    modules = (actuators, diagnostics, elm_only, memory, multi, reads, scans)
    defined = {
        name
        for module in modules
        for name, fn in vars(module).items()
        if name.startswith("handle_") and inspect.iscoroutinefunction(fn)
    }
    used = {handler for _, handler, *_ in CASES}
    # The monitor's handler is built per run by ``monitor_operation``.
    assert defined - used == set()


def test_the_package_documents_every_handler_module():
    doc = Path(inspect.getfile(reads)).with_name("__init__.py").read_text()
    for module in (actuators, diagnostics, elm_only, memory, multi, reads, scans):
        assert f":mod:`.{module.__name__.rsplit('.', 1)[-1]}`" in doc
