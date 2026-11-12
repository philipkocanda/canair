"""Fake ISO-TP stack and notifier for the raw UDS client tests."""

from canair.transport.raw import isotp_stack

# Load the stack factory's module now: it subclasses isotp.NotifierBasedCanStack when it
# loads, and the tests that use these fakes replace that class. Under lazy imports a test
# run alone would otherwise load it after the patch, with a lambda as its base.
assert isotp_stack.EcuStacks is not None


class FakeStack:
    """Stand-in for isotp.NotifierBasedCanStack keyed by txid + request bytes."""

    def __init__(self, txid, table):
        self.txid = txid
        self.table = table
        self.sent = []
        self._resp = None
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def available(self):
        return self._resp is not None

    def send(self, data, *a, **k):
        self.sent.append(bytes(data))
        self._resp = self.table.get((self.txid, bytes(data)))

    def recv(self, block=False, timeout=None):
        r, self._resp = self._resp, None
        return bytearray(r) if r is not None else None

    def is_rx_active(self):
        return False


class FakeNotifier:
    def __init__(self, *a, **k):
        pass

    def add_listener(self, *a, **k):
        pass

    def stop(self):
        pass
