"""A PlotModel builder for the decode plot tests."""

from canair.tui.apps.decode_plot.model import PlotModel


def _plot_results(values, pid="2101", ecu="BMS"):
    out = []
    for i, v in enumerate(values):
        out.append(
            {
                "capture": {
                    "payload": "6101" + f"{int(v) & 0xFF:02X}" + "00",
                    "date": "2026-07-22",
                    "time": f"12:00:{i:02d}",
                    "ecu": ecu,
                    "pid": pid,
                },
                "decoded": {"P": {"value": float(v)}},
            }
        )
    return out


def plot_model(values=(1, 2, 3, 4, 5)):
    results = _plot_results(values)
    params = {"P": {"expression": "B4", "verified": True}}
    return PlotModel(results, ["P"], params, set(), None, "BMS", "2101", defined_params=params)
