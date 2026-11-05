"""The diagnostic request/response protocol layer (domain A), I/O-free.

``uds`` names the *domain* here, as in the CLI's ``uds`` kinds (``captures uds``,
``import uds``): request/response diagnostics on UDS (ISO 14229), KWP2000 (ISO 14230)
and the OBD-II (SAE J1979) modes, as opposed to broadcast CAN frames. Per-protocol
facts live in one module each, and code elsewhere branches on them rather than
re-deriving them:

* :mod:`~canair.uds.protocol` — which protocol an ECU speaks (``id_protocol`` → ``uds``/``kwp``).
* :mod:`~canair.uds.services` — what each request SID means, per protocol family.
* :mod:`~canair.uds.nrc` — negative response code names, per protocol.
* :mod:`~canair.uds.subfunctions` — the sub-function and control-parameter values of each
  service, and their names.
* :mod:`~canair.uds.requests` — the byte layout of the session, I/O control, routine control
  and DTC-clear requests canair sends.
* :mod:`~canair.uds.obd` — OBD-II facts beyond the service table.
* :mod:`~canair.uds.response` / :mod:`~canair.uds.parse` / :mod:`~canair.uds.echo` — the
  parsed-response shape and outcome taxonomy, reading a transport's reply into it, and the
  request echo a reply must repeat.
* :mod:`~canair.uds.addressing` — which CAN ids an ECU is reached on, per addressing mode.
* :mod:`~canair.uds.frame_counts` — the ledger of how many CAN frames a response occupies.

Belongs: protocol facts and decoding of request/response bytes, with no I/O.
Not here: reading or writing recordings or definitions (``captures/``, ``ecus/``), user config
(``config.py``), moving bytes (``transport/``), and deciding what may be sent (``safety``).
Imports: the standard library and ``core`` only. ``tests/contracts`` pins that it reads no data
store, config or transport.
"""
