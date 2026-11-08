"""What may be sent to the car.

Belongs: the command blocklist and session rules (``blocklist``), the make-specific quirks that
widen them for one protocol or grant an action one exact request (``quirks``), the ECU resets
``canair reset`` may send (``reset``), and the read/release/actuate classifier (``actuation``).
Not here: moving bytes (``transport/``), the actions themselves (``actions/``) or their live run
(``live/action.py``).
Imports: ``uds`` and ``core``. The blocklist is the one place a refusal is decided, so transports
and live operations depend on it and it depends on neither.
"""
