"""Print a warning an analysis helper worded for itself, in the one message form.

``analysis/`` builds some warnings as ready-made text that starts ``warning: `` or
``<command>: warning: ``. The level and the stream belong to :mod:`canair.render.messages`, so
this strips that lead-in and hands the rest over.
"""

from __future__ import annotations

import re

from canair.render import messages

_LEAD_IN = re.compile(r"^(?:\w+: )?warning: ")


def warn_analysis(text: str) -> None:
    """Print ``text`` as ``warning: …`` on stderr, without any lead-in it already carries."""
    messages.warning(_LEAD_IN.sub("", text))
