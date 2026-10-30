# Functional addressing follow-ups: responder sweep, request padding, functional keepalive

Status: **Parts 1 and 2 IMPLEMENTED; part 3 PROPOSED**, waiting on its car test (see "Gate: test
on the car first").

Follow-ups from `profiles/hyundai-ioniq-ae-ev/research/FUNCTIONAL_ADDRESSING_FINDINGS.md`. That
document collects what ECU firmware (CCM, VESS, AVN), third-party bus logs and Hyundai/Kia's own
diagnostic spec (ES95486-00, "UDS on CAN") say about UDS *functional* requests: the 11-bit
broadcast request ID `0x7DF`, forwarded unchanged by the gateway and answered by each ECU on its
own response ID.

The spec is a Hyundai/Kia internal document. It is kept as a local copy only. Cite its facts in
code comments, docs and commit messages; never quote its text or tables or commit the PDF.

Three changes, in this order:

1. A functional responder sweep, needed to run the findings' car test.
2. Separate request and response padding, so the Ioniq can pad requests with `0x55`.
3. An opt-in functional `3E 80` keepalive, gated on the car test.

## 1. Functional responder sweep (`canair discover --functional`)

### The gap

The findings' open question is which buses the gateway forwards `0x7DF` to; MM-CAN is unconfirmed.
A single functional request answered by every routed ECU would settle it, but canair cannot show
the answers:

- `raw` expects one reply.
- The REPL's `!tester` broadcasts `3E 00` but prints only counts, because ELM327 headers are off.
- `discover` probes physical IDs one at a time (sending `10 01` to each) and skips `0x7DF`.

### Proposal

- **Interface:** a `--functional` mode on `discover`, so it inherits `--save`, `--register` and
  the capture format discover already has. A separate `scan functional` would duplicate that
  plumbing.
- **What it sends:** one request on the functional ID: `0x7DF`, or `0x18DB33F1` in a 29-bit mode,
  from `addressing.py`.
- **Default request:** `3E 00` (TesterPresent). It is harmless, and both protocol families answer
  it.
- **Allow-list:** `--request HEX` accepts only reads: `3E 00`, `22 xxxx`, `1A xx`, `19 02 xx`,
  `18 00 FF 00`. A functional frame reaches every ECU at once, so the blast radius is the whole
  car. Session control, reset, clear, communication control, DTC setting, writes, IOControl and
  routines are refused even with `--unsafe`. Put the rule in `canlib/safety.py` next to the
  session rule, not in the command.
- **Collecting replies:** keep listening for a window, default 300 ms. That covers the spec's
  50 ms answer time plus gateway delay and the 100 kbit/s buses; it is tunable with `--window`.
- **Report:** one row per response ID, mapped through the profile (`rx_for_tx` in reverse) to the
  ECU name and its `can_bus`. Mark each reply positive or NRC, flag responders the profile does not
  know, and list known ECUs that stayed silent. Grouped by bus, that is the routing map.
- **Multi-frame replies:** for `22`/`19` requests, send each responder's flow control on its own
  physical request ID (response − 8, or the ECU's `fc_id`), never on `0x7DF`. The AVN times out
  otherwise.
- **Repeats:** `--repeat N --interval S`, interval at least 1 s, to reproduce the Kona burst
  silence (findings section 3).
- **`--save`:** writes a discover-style capture (`rx: broadcast`, one entry per responder with its
  `rx`), so `captures uds --sessions` and `validate captures` handle it unchanged.

### Transport notes

- **`slcan-tcp`:** send the frame and read every frame on IDs `0x700`-`0x7FF` for the window. Run
  one ISO-TP receiver per responding ID. can-isotp's per-address stacks may be heavier than needed
  here; the response set is small.
- **ELM327 (`wican-ws`, `elm327-tcp`):**
  - Turn headers on (`ATH1`) and use the functional target the addressing module already has
    (`functional_target`, automatic filter, flow-control mode 0).
  - The adapter collects replies until its `ATST` budget runs out, which covers the window.
  - Multi-frame replies from several ECUs at once are the risky case for an ELM327. Start with
    single-frame requests (`3E 00`), and treat `22`/`19` on ELM as best-effort.
  - Restore headers-off afterwards. `AdapterState` must observe `ATH1` and `ATH0`, or the
    header cache drifts.

### Tests

- Fake-terminal tests: a reply set spanning several buses is grouped correctly, and unknown
  responders are flagged.
- Allow-list tests: every refused service is refused, including with `--unsafe`.
- Flow control goes to the physical ID on the raw path.
- `--save` round-trips through `validate captures`.

### Docs

- Regenerate `docs/reference/cli/discover.md`.
- Add a line to the `discover` entry in `AGENTS.md`.
- Add a short "map which buses the gateway reaches" section to the bring-your-own-car docs. It is
  make-neutral: any gateway car.

## 2. Separate request and response padding

### The gap

`isotp.tx_padding` (default `0xAA`, `canlib/transport/isotp_params.py`) is used for two different
bytes:

- the fill byte canair **sends** in short frames (`build_isotp_params` → can-isotp);
- the fill byte canair **strips** from ECU replies (`resolve_tx_padding` in
  `modes/multi_batch.py`, `modes/monitor_raw.py`, `modes/multi.py`, `modes/monitor.py`).

ES95486 has them differ: requests are padded with `0x55`, responses with `0xAA`. Today a profile
cannot follow the spec. Setting `tx_padding: 0x55` would make the multi-DID splitter look for
`0x55` padding in replies padded with `0xAA`. `docs/concepts/profiles.md` mixes up the two as well
("an ECU that pads frames with `0x00`" describes the key canair transmits).

No ECU examined checks request padding, so nothing on the Ioniq is broken today. This is spec
conformance, with a small chance of mattering on a stricter ECU.

### Proposal

- **New key:** `isotp.rx_padding`, the byte ECU replies are padded with and the byte to strip.
- **Backward compatibility:** when `rx_padding` is absent it defaults to `tx_padding` when that is
  set, else `0xAA`. Profiles that set `tx_padding` today keep their behaviour.
- **Resolvers:** add `resolve_rx_padding(pids_data)` and switch the four strip/split call sites to
  it. `resolve_tx_padding` stays, for the transmit side only. Grep for every `pad=` / `self.pad`
  consumer; the multi-DID split and the identity-decode strip are the known ones.
- **Validation:** accept `rx_padding` in the `isotp:` key whitelist and the `validate pids` byte
  check. It is not added to `_DEFAULTS` itself: that dict is handed to can-isotp verbatim, which
  has no reply-padding parameter, so `rx_padding` is listed as a canair-only field next to it.
- **Profiles:**
  - The default stays `0xAA` for both keys. ISO 15765-2 does not mandate a fill byte, and other
    makes' profiles rely on the current behaviour.
  - `profiles/hyundai-ioniq-ae-ev/profile.yaml` sets `isotp: {tx_padding: 0x55, rx_padding: 0xAA}`.
  - `hyundai-ioniq-5-ne-ev` can follow once someone confirms that generation uses the same spec.
- **ELM327 path:** the adapter pads requests itself, normally with `0x00` (the Torque Pro bench
  logs show `00` fill), and `tx_padding` has never reached it.
  - The ELM327 command set has a CAN filler-byte programmable parameter. Programmable parameters
    are persistent EEPROM settings on genuine ELM327s, and canair must not change persistent
    device state behind the user's back.
  - The WiCAN Pro's STN2120 offers no volatile equivalent: the OBDLink Family Reference and
    Programming Manual lists the CAN filler byte only as programmable parameter `26`, and states
    that programmable parameters are stored in non-volatile memory. No `ST` command sets it.
  - So the ELM327 path keeps the adapter's padding, and the docs say so.

### Tests

- `resolve_rx_padding` defaults and fallback.
- The multi-DID split strips with `rx_padding` while frames are sent with `tx_padding`.
- `validate` rejects an out-of-range `rx_padding`.
- Existing tests that set only `tx_padding` keep passing, which shows the fallback works.

### Docs

- `docs/concepts/profiles.md`: describe both keys correctly.
- `templates/profile.yaml.tmpl`: the commented `isotp:` example gains `rx_padding`.
- `CHANGELOG` through the usual release flow.

## 3. Functional `3E 80` keepalive (opt-in, after the car test)

### The gap

`SessionManager` (`canlib/session_manager.py`) keeps each open session alive with a physical,
validated `3E 00` per ECU, every 2 s. On ELM327 that also means switching the header (`ATSH`) for
each ECU. A multi-ECU `monitor` session spends a command per ECU per cycle on keepalives.

ES95486 recommends a single functional `3E 80` instead: one frame, no replies, handled by a bypass
so it never blocks an ECU. The CCM, AVN and VESS firmware all implement that bypass.

### Proposal

- **Opt-in:** a profile setting (for example `keepalive: functional` in `profile.yaml`, schema in
  `canlib/schema/`), default `physical`.
- **When functional:**
  - one functional `3E 80` every 2 s, as the spec recommends, replaces the per-ECU `3E 00` for
    ECUs with `id_protocol: UDS`;
  - ECUs with `id_protocol: KWP2000` keep physical keepalives. KWP2000's TesterPresent
    sub-functions are `01`/`02`, and how the KWP-family ECUs treat `3E 80` is unknown; they answer
    functional requests with NRCs.
- **The "not validated" problem:**
  - The physical keepalive is validated deliberately: an unvalidated keepalive once hid an ELM327
    pipe desync (see the reconnect section of `AGENTS.md`). A request with no reply cannot be
    validated.
  - On `slcan-tcp` it is fire-and-forget: there is no pipe to desync.
  - On ELM327 the adapter waits out `ATST` and answers `NO DATA`, which costs the whole budget
    (about 614 ms on the Ioniq) per keepalive. It also has to go through `ResponsePipe` as an owed
    prompt.
  - So functional keepalive is **raw-transport only** at first. Lifting that needs a design for a
    deliberately reply-less ELM request: a short temporary `ATST`, restored afterwards, with the
    pipe ledger aware of it.
- **Side effect, documented and not hidden:** a functional frame tells in-car pollers to stop
  (findings section 4). That is what the spec intends, but it changes the car's behaviour. Say so
  in the help text.
- **Session state:** `mark_active` and the staleness clock still apply. A functional keepalive
  refreshes every UDS session's timestamp at once.

### Gate: test on the car first

Read-only. Parked, ignition on:

1. Open an extended session on the IGPM (`10 03`). Its `22BCxx` reads need it.
2. Stop physical keepalives and send only functional `3E 80` every 2 s for 30 s.
3. Read `22BC03` and repeat for one other UDS ECU with a session-gated read. Success means the
   functional keepalive held the session; a session-required NRC means it did not.
4. Run a control without any keepalive to confirm the session normally lapses after the spec's
   5 s.

Only implement if both UDS ECUs pass. If MM-CAN turns out not to be routed (part 1's sweep), the
functional keepalive cannot hold the AVN's session, and the feature must keep a physical keepalive
for ECUs on unrouted buses.

### Tests

- With the feature on, one functional frame replaces the per-ECU keepalives for UDS ECUs, and KWP
  ECUs still get physical ones.
- The ELM327 transports refuse or fall back to physical with a one-time notice.
- The default (`physical`) is unchanged.

### Docs

- The profile-settings section of `docs/concepts/profiles.md`.
- A note in the `read`/`monitor` session docs where keepalives are described.

## Open questions

- **ELM327 padding:** settled for the STN2120 (part 2): its filler byte is a non-volatile
  programmable parameter only, so canair leaves it alone.
- **KWP ECUs and `3E 80`:** whether the KWP-family ECUs accept it. Worth one extra line in the
  car test: open a session on a KWP ECU, send only functional `3E 80`, and see whether the session
  holds.
- **Who polls in-car:** which Ioniq ECUs poll other ECUs. Sweeping while watching for diagnostic
  requests not sent by canair would show it, but only a passive tap sees that (the OBD port is
  gateway-isolated).
