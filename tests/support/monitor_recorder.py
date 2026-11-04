"""A stand-in monitor controller for recorder tests."""


class FakeController:
    """Minimal stand-in exposing only the surface the recorder reads.

    ``suggested_states`` returns whatever ``next_state`` is set to, so a test can
    script the state changing cycle-to-cycle (charging → idle) the way the live
    states.yaml auto-suggest would from decoded values. ``next_state`` accepts a
    single token (composed into a one-element list) or a list of tokens.
    """

    def __init__(self, captures_dir, *, keep_mode=None, save=True):
        self.captures_dir = captures_dir
        self.keep_mode = keep_mode
        self.save = save
        self.prev_hex: dict = {}
        self.next_state: str | list[str] | None = None

    def query_label(self) -> str:
        return "BMS:2101"

    def suggested_states(self) -> list[str]:
        if not self.next_state:
            return []
        if isinstance(self.next_state, str):
            return [self.next_state]
        return list(self.next_state)

    def _ecu_ref(self, ecu_label: str) -> str:
        return "0x7EC"
