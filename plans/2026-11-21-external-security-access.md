# Plan: SecurityAccess with an external key provider

Status: **DONE** (2026-11-21). The user approved the narrow policy change and implementation.
Verified offline with fake terminals and a loopback ELM327 TCP adapter; no vehicle or device
access was performed or authorized by this plan.

## Goal and policy decision

Allow an authorized user to open SecurityAccess (`0x27`) inside a diagnostic session and then run
an ordinary canair operation in that **same session**. The user supplies an external executable
that calculates the response. canair ships no manufacturer keys, seed-to-key algorithms, solver,
guessing, brute force or automatic attempts at other security levels.

This is a deliberate change to the current project policy:
`.claude/skills/contributing-code/safety-pii-keys.md` prohibits a SecurityAccess pipeline step and
says canair does not unlock ECUs. **Obtain the user's approval for that policy change before
implementation.** Approval was given on 2026-11-21. Revise the policy narrowly to permit a
user-supplied provider, not a bundled
algorithm or key; preserve the public-tree ban on real security material. The older
`plans/2026-07-22-security-access-and-bms-session.md` is historical, not an implementation model:
its algorithm bank and repeated attempts are explicitly out of scope.

## User experience

- Add an explicitly requested SecurityAccess step to the existing `read` pipeline, so it can be
  placed after an explicit session step and before the desired read or other permitted operation.
  The step takes one ECU and one explicitly chosen security level. Its exact spelling is an
  implementation decision; it is not a generic automatic unlock on an NRC.
- Select one **trusted local executable** explicitly for the invocation, or an owner-only file of
  fixed binary response bytes when the ECU does not require a calculation. Do not put executable
  paths or key material in a shared vehicle profile. A future user-config mapping may be considered
  separately; it must not execute anything merely because a profile declares it.
- Confirm the state-changing unlock before the first security request. Offer `--yes` for
  non-interactive use, following the existing CLI conventions; `--json` is never consent.
  Consent to unlock does not authorize a later actuator, write, reset or other guarded operation.
- Open and keep the ECU's session using the existing `SessionManager` and transport-neutral
  `Terminal`; require that the session step actually succeeded. Respect the existing session
  blocklist and profile quirks, and never open programming sessions or weaken other guards.
  `--session`'s UDS default is not assumed to fit a KWP ECU.
- One requested level, one seed request, and **at most one key submission**. Do not probe levels,
  try candidates, wait out lockouts, retry a key after any failure, or unlock on reconnect.
  Transport handling of `responsePending` may await the same response, but must not resend the
  key. A timeout or lost reply after submission leaves the outcome unknown, not safely retryable.
- Keep normal `--save` behavior for subsequent eligible diagnostic readings. Never record the
  SecurityAccess exchange itself, including in the capture journal. This preserves the agent rule
  to save approved device reads without persisting the sensitive seed or response.

## Provider protocol

Use a small, versioned, documented protocol with no vehicle-specific arithmetic in canair:

1. Execute the user-selected program directly, **without a shell**. It is trusted code running
   with the user's privileges, not a sandbox; do not execute a command obtained from the car or
   a downloaded profile. Preflight the executable before contacting the car.
2. Supply one JSON document over stdin with protocol version, diagnostic protocol, ECU selector,
   physical transmit ID, requested level and seed as hex. Do not pass the seed or key via command
   arguments, environment variables, temporary files or a shell command line.
3. Read one JSON document from stdout containing the computed key as hex. Validate the version,
   fields, encoding, even-length hex, non-empty key and maximum request size before submission.
   **Do not assume key length equals seed length**: algorithms and ECU protocols vary.
4. Bound both output streams and runtime, close stdin, and terminate/reap the process on timeout,
   cancellation or overflow. Treat nonzero exit, malformed output and extra output as failure.
   Never forward provider stdout, stderr, exception payloads or key-bearing JSON to user output
   or logs. State that a provider can itself log or disclose data; canair cannot prevent it.
5. Document the executable's input/output contract and show only a deliberately synthetic test
   provider, with no actual ECU algorithm, recovered key, or real seed/key pair. Ship no provider
   or default path that can unlock a real ECU. Any test fixture must be explicitly synthetic.

Choose a bounded provider timeout and keep the diagnostic session alive while it runs, subject to
the ECU's supported keepalive behavior. If session continuity is lost, or the provider returns too
late for the challenge, abort without sending the key. Do not assume that an all-zero seed means
the same thing on every KWP implementation; define any already-unlocked handling by protocol and
evidence, not by a universal shortcut.

## Privacy and safety prerequisites

These must ship with the first working provider, not as a later hardening pass:

- Centralize unconditional SecurityAccess sensitivity independent of profile `sensitive:` flags.
  Withhold seed requests and replies, key requests and replies, and their raw representations from
  stdout/JSON, captures and journals, per-ECU logs, central events, exceptions, debug/verbose wire
  traces, frame-count caches and persisted frame-count learning. Suppress rather than attempt to
  reconstruct secrets in error paths. Include `--reveal`, raw/REPL paths and both transports in
  the review. Show only sanitized status, ECU, level and NRC; neither key nor seed is printed.
- This is a real existing gap: `transport/ecu_log.py::log_response` logs the request and response,
  and ELM channel verbose tracing prints wire text. The raw step can also journal its request and
  response in `live/read/multi.py`. Fix those paths centrally before adding an unlock feature;
  don't rely on users avoiding `--save` or `--verbose`.
- Treat the key-submission operation as security-sensitive and state-changing; require explicit
  consent and do not allow it to fall through as an ordinary read. `safety/actuation.py` currently
  classifies the entire service as read. After the provider works, audit and harden direct raw and
  REPL access to prevent unconfirmed key submissions through those paths too. Make classification
  conservative for malformed or unknown protocol/subfunction combinations, including the
  UDS/KWP difference in the high bit; do not break legitimate documented seed-only use by
  assuming every request has the same meaning on both protocols.
- The safety and privacy changes must cover **all entry points**, not just the new step, and must
  not permit functional/broadcast unlocks. Keep functional-address restrictions and all existing
  blocklist rules intact. Never let a provider, profile or `--yes` bypass them.
- Ensure in-memory buffers are short-lived; do not promise secure erasure of Python objects.
  Provider executables and external capture tools remain outside canair's confidentiality boundary.

## Implementation placement and sequencing

1. **Policy and contract:** obtain the policy decision above; agree the provider contract and CLI
   shape. Explicitly separate approval to change code from approval for any live vehicle action.
2. **Privacy foundation:** put protocol-sensitive classification at a shared seam usable by
   `privacy/`, `captures/` and both transports without violating the import layers; use it before
   data reaches logs, journals, frame-count learning or wire tracing. No provider yet.
3. **Exchange:** add I/O-free request/response parsing under `uds/`, the single-attempt session
   orchestration and subprocess adapter under an appropriate `live/` family, and pipeline grammar
   under `query/`. CLI supplies typed options and resolves the selected profile. No new top-level
   package. Check `docs/development/architecture.md` and package charters before placement.
4. **Consent and existing raw paths:** classify the key send correctly, enforce confirmation at
   every relevant entry point, and audit the raw/REPL paths. Ship this with the feature, even if
   the full raw-interface audit is developed as a separate logical unit.
5. **Docs and gates:** document the external-provider trust boundary, secret handling, session
   limitations and authorized-use responsibility; regenerate the CLI reference from `--help` and
   keep the README command map current if the user-facing map changes. Run the normal project
   checks, including `make structure` and the UI standards contracts.

The user approved raising the parser-build ceiling from 651 to 654 loaded modules and the
deferred-import ceiling from 84 to 86 for this design's separately placed components. Other
ratchets are unchanged; lower the blocklist's size ceiling after extracting its service rules.

As built, the pipeline step is `unlock`, and `read` accepts either `--security-provider` or
`--security-key-file`. Security-enabled KWP sessions request the KWP standard session on exit;
an unconfirmed close warns that access may remain active. A monitor refuses the step rather than
silently dropping it. The provider has a minimal environment and a bounded runtime; a failed
keepalive aborts before key submission. See `docs/concepts/security-access.md` for the contract.

Every security exchange uses the existing header/request transaction, expected SID and echo
validation, session tracking and transport error policy. Review `docs/development/link-health.md`
and `docs/development/frame-counts.md`: retries and response-count optimization must not cause a
duplicate key submission, retain sensitive request strings or compromise desync detection.

## Tests and acceptance

- Fake terminals and a loopback emulator only: verify one session, one seed request, one provider
  invocation, one key submission, then the intended operation on the same ECU and connection.
  Cover all supported transports and the UDS/KWP subfunction and echo distinctions. Use only
  invented inputs and an inert synthetic provider, never a real ECU algorithm or pair.
- Cover session refusal and loss, already-unlocked responses where specified, malformed or stale
  replies, wrong echo, negative responses, provider errors/timeouts/oversized streams, cancellation,
  keepalive contention, and loss of link before and after key submission. Assert no automatic
  retry, candidate sweep, reconnect unlock, functional request or subsequent operation on failure.
- Assert all output streams, verbose traces, event/per-ECU logs, captures, journals, JSON and
  frame-count persistence exclude synthetic seed and key bytes and full key requests, including
  error, crash-recovery and raw/REPL paths. Test confirmation and `--yes` without weakening other
  guards. Test an authorized subsequent read is still captured normally under `--save`.
- No live testing is part of implementation. Before any car or dongle interaction, separately ask
  for the exact commands, requests, intended state and capture handling; wait for approval.
