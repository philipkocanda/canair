"""Run and print the checks of the profile's optional YAML files (everything but ecus/, captures/)."""

from canair.validation.other import (
    check_actions,
    check_can,
    check_can_buses,
    check_groups,
    check_signals,
    check_states,
)
from canair.validation.report import FileReport, SignalsReport
from canair.validation.variants import check_variants


def print_file_report(report: FileReport) -> int:
    """Print one file's result; the exit code is 1 when the file has a problem."""
    if report.skipped:
        print(report.skipped)
        return 0
    if report.broken:
        print(report.broken)
    elif report.errors:
        print(f"{report.name}: {len(report.errors)} errors")
        for error in report.errors:
            print(f"  - {error}")
        if report.error_hint:
            print(report.error_hint)
    else:
        print(f"{report.name}: OK {report.summary}")
    for note in report.notes:
        print(note)
    return 1 if report.failed else 0


def print_signals_report(report: SignalsReport) -> int:
    if report.skipped:
        print(report.skipped)
        return 0
    total_errors = 0
    for found in report.files:
        if found.errors:
            print(f"\n{found.name}: {len(found.errors)} errors")
            for error in found.errors:
                print(f"  - {error}")
            total_errors += len(found.errors)
        else:
            print(f"{found.name}: OK {found.summary}")
    if total_errors:
        print(f"\n{total_errors} total errors across {len(report.files)} signals file(s)")
        return 1
    print(f"\nAll {len(report.files)} signals file(s) valid ({report.total_signals} signals).")
    return 0


def run_states() -> int:
    return print_file_report(check_states())


def run_groups() -> int:
    return print_file_report(check_groups())


def run_actions() -> int:
    return print_file_report(check_actions())


def run_can_buses() -> int:
    return print_file_report(check_can_buses())


def run_signals() -> int:
    return print_signals_report(check_signals())


def run_can() -> int:
    return print_file_report(check_can())


def run_variants() -> int:
    return print_file_report(check_variants())
