"""``canair wican autopid stats``: the per-ECU/PID table."""

from __future__ import annotations

from canair.core.terminal import terminal_columns
from canair.ecus.response_frames import stored_count


def print_stats(data: dict) -> None:
    """Print a summary table of all PIDs and parameters."""
    total_params = 0
    verified_count = 0
    unverified_count = 0
    counted = 0
    total_pids = 0

    header = (
        f"\n{'ECU':<10} {'TX ID':<8} {'PID':<10} {'Period':<8} {'Frames':<8} "
        f"{'Signals':<8} {'Verified':<10} {'Source Summary'}"
    )
    rule = "─" * min(terminal_columns(), 100)
    print(header)
    print(rule)

    for ecu_name, ecu in data["ecus"].items():
        tx_id = ecu["tx_id"]
        for pid_code, pid_data in (ecu.get("pids") or {}).items():
            params = pid_data.get("parameters") or {}
            n_params = len(params)
            n_verified = sum(1 for p in params.values() if p.get("verified", False))
            n_unverified = n_params - n_verified
            total_params += n_params
            verified_count += n_verified
            unverified_count += n_unverified
            total_pids += 1

            sources = {p.get("source", "?") for p in params.values()}
            source_str = "; ".join(sorted(sources))[:40]

            # A dash means the PID still pays the adapter's full response-wait
            # budget; the count is only shown once wire evidence confirmed it.
            frames = stored_count(pid_data)
            if frames is not None:
                counted += 1
            frames_str = str(frames) if frames is not None else "—"

            v_str = f"{n_verified}/{n_params}"
            print(
                f"{ecu_name:<10} 0x{tx_id:03X}    {pid_code!s:<10} "
                f"{pid_data.get('period', '?')!s:<8} {frames_str:<8} "
                f"{n_params:<8} {v_str:<10} {source_str}"
            )

    print(rule)
    print(
        f"{'TOTAL':<10} {'':8} {'':10} {'':8} {f'{counted}/{total_pids}':<8} "
        f"{total_params:<8} {verified_count}/{total_params} verified "
        f"({unverified_count} unverified)"
    )
    print(f"\n{counted}/{total_pids} PIDs carry a response_frames count.")
