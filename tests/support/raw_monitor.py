"""A timed raw client for the raw monitor tests."""


class FakeRawClient:
    """Maps (ecu, request_bytes) -> response bytes (or None = timeout)."""

    def __init__(self, table):
        self.table = table
        self.ensured: list[str] = []

    def ensure_ecu(self, name, address):
        self.ensured.append(name)

    def read(self, ecu, req, timeout=None):
        return self.table.get((ecu, bytes(req)))

    def poll(self, requests, timeout=None, on_result=None):
        out = {}
        for e, r in requests:
            val = self.table.get((e, bytes(r)))
            out[(e, bytes(r))] = val
            if on_result is not None:  # mirror the real client's incremental callback
                on_result((e, bytes(r)), val)
        return out


class TimedRawClient(FakeRawClient):
    """A FakeRawClient whose unanswered requests each cost ``timeout_s``."""

    def __init__(self, table, timeout_s=1.0):
        super().__init__(table)
        self.timeout_s = timeout_s
        self.last_latency = {}

    def poll(self, requests, timeout=None, on_result=None):
        out = super().poll(requests, timeout, on_result)
        self.last_latency = {
            key: (self.timeout_s if val is None else 0.02) for key, val in out.items()
        }
        return out
