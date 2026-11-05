"""What the groups of ``canair wican`` share: the Pro and firmware guards."""

from __future__ import annotations

import argparse

from canair.device.wican.firmware import Surface
from canair.render import messages

WICAN_TIMEOUT = 10  # seconds


def require_pro(operation: str) -> int | None:
    """Return an error code if the configured WiCAN is not a Pro.

    AutoPID profile sync (upload/download/diff) and device protocol switching
    are WiCAN Pro-only features. The classic (non-Pro) WiCAN has no AutoPID
    support, so we refuse these device operations up front with a clear message
    instead of letting them fail obscurely against the device. Returns ``None``
    when the model is Pro (proceed) or an int exit code to abort.
    """
    from canair.config import is_wican_pro

    if is_wican_pro():
        return None
    messages.error(
        f"`canair wican {operation}` needs a WiCAN Pro — the classic "
        "(non-Pro) WiCAN has no AutoPID / vehicle-profile support"
    )
    messages.note(
        "your config sets wican_model: classic. Generating the AutoPID "
        "JSON still works — run `canair wican autopid write` to write it to out/"
    )
    messages.hint("if this device is actually a Pro, run: canair config set wican_model pro")
    return 2


def require_v4_api(base_url: str, surface: Surface, *, writes: bool = False) -> int | None:
    """Return an error code if the device's firmware can't serve this command.

    These commands drive WiCAN 4.x HTTP routes that firmware v6 removed, so
    refuse them on v6 before any request is sent. A 4.x version outside the
    tested range only warns. A command that ``writes`` to the device also warns
    when the generation could not be confirmed at all (the check is off, or the
    device didn't identify itself) — a warning rather than a refusal, so an
    unusual or offline-configured device is not locked out; v6 answers the 4.x
    write routes with an error, so going ahead fails loudly, not silently.
    """
    from canair.device.wican.firmware import (
        compatibility,
        detect,
        print_verdict,
        probes,
        unconfirmed,
    )

    firmware = detect(base_url, surface)
    verdict = compatibility(firmware, surface)
    print_verdict(verdict)
    if verdict.refused:
        return 2
    if writes and firmware.generation == "unknown":
        print_verdict(unconfirmed(surface, "unidentified" if probes(surface) else "check-off"))
    return None


def add_wican_arg(parser: argparse.ArgumentParser) -> None:
    from canair.config import default_wican, wican_addresses

    # Resolved exactly like the live commands' --wican (wican_api.resolve_wican_url):
    # a device alias, else the value itself as the host — never another device.
    parser.add_argument(
        "--wican",
        default=None,
        help=f"WiCAN address: {', '.join(wican_addresses())}, an IP/host, or a URL "
        f"(default: config transport.host / default_wican={default_wican()})",
    )
