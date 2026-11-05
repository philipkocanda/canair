"""``canair hunt`` — which byte on ECU:PID *is* this known signal?

Sweeps every byte offset × interpretation (u8/i16/f32/… × endianness) on a
target PID, time-aligns each against a reference signal from another ECU/PID,
and ranks by |Pearson r| — reporting the best linear fit and a physical-unit
guess for each top hit. Automates the "which byte tracks vehicle speed?"
question that previously needed a scratch script.

Read-only analysis over ``captures/``; talks to no device.

* :mod:`.parser` — the group and its two kinds, ``uds`` and ``can``.
* :mod:`.uds` — the diagnostic-PID sweep; :mod:`.physical` is its reference-free mode and
  :mod:`.promote` writes the winner into ``ecus/``.
* :mod:`.can` — the raw broadcast-frame sweep; :mod:`.render` labels a hit for both.

Belongs: `canair hunt`: the parser, the sweeps' glue and their output.
Not here: the byte search itself (`analysis/cross_signal.py`, `analysis/frame_series.py`).
Imports: `analysis`, `captures`, `yaml_edit` and below.
"""

from .parser import NAME, add_parser
from .uds import run

__all__ = ["NAME", "add_parser", "run"]
