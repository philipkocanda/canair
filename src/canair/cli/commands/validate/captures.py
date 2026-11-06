"""Run and print ``canair validate captures``."""

from collections import OrderedDict

from canair.profile import active
from canair.render import messages
from canair.validation.captures import check_captures
from canair.validation.report import CapturesReport, CaptureWarning

# How many locations to list inline before collapsing a grouped warning to "(+N more)".
_MAX_GROUP_LOCATIONS = 6


def _print_grouped(warnings: list[CaptureWarning], file: str, *, error: bool = False) -> None:
    """Print findings on stderr, collapsing repeats of the same message into one entry.

    A message seen once prints inline (``file: location: message``); a message seen multiple
    times prints once as ``file: message — N locations:`` followed by a capped list of them.
    "Locations", not "captures": the same lint can be session-scoped (a state or quality
    warning) or capture-scoped.
    """
    emit = messages.error if error else messages.warning
    groups: OrderedDict[str, list[str]] = OrderedDict()
    for w in warnings:
        groups.setdefault(w.message, []).append(w.location)
    for message, locations in groups.items():
        if len(locations) == 1:
            emit(f"{file}: {locations[0]}: {message}")
            continue
        shown = locations[:_MAX_GROUP_LOCATIONS]
        extra = len(locations) - len(shown)
        tail = f", … (+{extra} more)" if extra else ""
        emit(f"{file}: {message} — {len(locations)} locations:\n{', '.join(shown)}{tail}")


def _print_tags(report: CapturesReport) -> None:
    tags = report.tags
    for problem in tags.problems:
        messages.warning(problem)
    if tags.several_variants and tags.untagged:
        ceiling = ""
        if tags.max_untagged is not None:
            verb = "exceeds" if tags.failed else "within"
            ceiling = f", {verb} the --max-untagged {tags.max_untagged} baseline"
        messages.note(
            f"{tags.untagged} untagged session(s){ceiling}: no car named — tag them with "
            "`captures migrate-variants` or `captures uds --set-variant`"
        )
    if tags.failed:
        messages.error(
            f"{tags.untagged} untagged session(s) exceeds the --max-untagged "
            f"{tags.max_untagged} baseline — new recordings must name their car "
            "(--variant / profile use)"
        )


def _print_untimed(report: CapturesReport) -> None:
    """The untimed-payload footer. Writes carry a timestamp now, so the count only ever falls.

    A ``--max-untimed`` baseline turns "must be zero" (which the grandfathered legacy rows can
    never satisfy) into "must not GROW", a real CI gate that needs no history rewrite and
    ratchets down opportunistically.
    """
    gaps, limit = report.total_time_gaps, report.max_untimed
    if not report.strict and gaps:
        ceiling = ""
        if limit is not None:
            relation = "exceeds" if report.ratchet_failed else "within"
            ceiling = f" ({relation} the --max-untimed {limit} baseline)"
        detail = (
            "" if report.show_untimed else " (--show-untimed lists them, --strict fails on them)"
        )
        messages.note(f"{gaps} untimed payload capture(s){ceiling}{detail}")
    if report.ratchet_failed:
        messages.error(
            f"{gaps} untimed payload capture(s) exceeds the "
            f"--max-untimed {limit} baseline — new untimed captures are not "
            "allowed (the write path enforces a timestamp). If this is legacy data, "
            "raise the baseline deliberately; otherwise fix the capture(s)."
        )


def run_captures(
    strict: bool = False,
    max_untimed: int | None = None,
    show_untimed: bool = False,
    max_untagged: int | None = None,
) -> int:
    report = check_captures(strict, max_untimed, show_untimed, max_untagged, profile=active())
    if not report.n_files:
        print("No capture files found.")
        return 0

    for found in report.files:
        if found.error_count:
            print(f"{found.name}: {found.error_count} errors")
            for e in found.errors:
                messages.error(f"{found.name}: {e}")
            _print_grouped(found.strict_gaps, found.name, error=True)
        _print_grouped(found.warnings, found.name)

    _print_tags(report)
    if report.total_warnings:
        print(
            f"\n{report.total_warnings} warning(s) — see `canair validate states` / "
            "echo mismatches above"
        )
    _print_untimed(report)
    if report.total_errors:
        print(f"\n{report.total_errors} total errors across {report.n_files} files")
        return 1
    if report.failed:
        return 1
    print(f"\nAll {report.n_files} files valid.")
    return 0
