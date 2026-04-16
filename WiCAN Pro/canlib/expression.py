"""Expression evaluator import helper.

Imports evaluate_expression from decode-captures.py (hyphenated filename
requires importlib workaround).
"""

import importlib.util
from .constants import SCRIPT_DIR

_spec = importlib.util.spec_from_file_location(
    "decode_captures", SCRIPT_DIR / "decode-captures.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

evaluate_expression = _mod.evaluate_expression
