"""Build a MonitorController from loose arguments, with the settings the tests rely on."""

from canair.cli.profile_context import active
from canair.live.monitor.controller import MonitorController
from canair.live.monitor.options import MonitorOptions


def make_controller(
    terminal, query_steps, pids_data, verbose=False, *, raw_client=None, **options
) -> MonitorController:
    """A controller over ``query_steps``; ``options`` are the MonitorOptions fields.

    A test that sets nothing gets a 5 s interval and no on-screen history (keep-mode
    None), the controller's defaults before it took a MonitorOptions.
    """
    options.setdefault("interval", 5.0)
    options.setdefault("keep_mode", None)
    monitor = MonitorOptions(query_steps=query_steps, verbose=verbose, **options)
    return MonitorController(terminal, monitor, pids_data, raw_client=raw_client, profile=active())
