# SecurityAccess with a user-supplied provider

Some diagnostic operations require SecurityAccess (`0x27`). canair does not ship ECU keys,
manufacturer algorithms or a key-guessing tool. An authorized user can explicitly unlock one
ECU in a `read` pipeline, using a trusted local program or a file containing a fixed binary key.

For example, a pipeline can open a permitted session, unlock, and read in that same session:

```text
canair read --security-provider /path/to/provider --save --label "diagnostic read" \
  "session ENGINE --mode 03" "unlock ENGINE --level 01" "query ENGINE:2101"
```

The example names a hypothetical ECU. Session modes, levels and requests depend on the ECU;
do not assume this sequence works or is safe on a particular vehicle. `--yes` confirms the
unlock for non-interactive use. It does not authorize a later actuation or override the
[safety blocklist](safety.md).

For an ECU whose response is a fixed value, use `--security-key-file /path/to/key` instead of
`--security-provider`. The file contains the raw response bytes and must be a regular file owned
by the user, with no group or other permissions. Neither the key nor the seed goes in command
arguments. Do not put keys or providers in a shared profile.

## Provider contract

The provider is a trusted executable, run directly without a shell. It receives a single JSON
document on stdin, with `version` (integer `1`), `protocol`, `ecu`, `tx_id`, `level` and `seed`.
The last three fields are uppercase hex strings; `seed` contains the complete seed in wire order.
The provider returns a single JSON document on stdout with exactly `version` (integer `1`) and
`key` (an even-length, non-empty hex string). It exits zero on success. No example contains a
real ECU's algorithm, seed or key.

The provider has a short timeout and bounded output. Its output and error text are not shown;
failures stop the pipeline before the key is sent. The provider itself is not sandboxed: it can
read files or disclose anything the user's account can access, so run only code you trust. Its
environment contains only `PATH`, `HOME`, `LANG`, `LC_ALL` and `TMPDIR` when those are set;
other environment variables are not inherited.

## Safety and confidentiality

Unlocking changes the ECU's access state. canair asks before the exchange, sends at most one key,
and does not retry or guess a key after a failure or lost reply. An accepted session is required.
The ECU may expire the session or challenge, so a slow provider can fail; a communication loss
after sending a key makes its outcome unknown. Do not repeat the attempt without considering the
ECU's lockout rules.

The exchange is withheld from output, capture journals and logs. Ordinary reads after a successful
unlock are still saved with `--save`. Since a late diagnostic reply can arrive after an exchange,
raw wire tracing and per-ECU response logging stop for the rest of that connection. Other tools,
the provider, terminal input echo, and external CAN recordings are outside canair's control.

Direct raw key submissions and SecurityAccess range scans are refused; use the confirmed pipeline
instead. A successful unlock does not widen programming, reset, actuation, write or
functional-address safety rules. On exit, canair asks an identified KWP2000 ECU to return to its
standard session; it warns if the ECU does not confirm the request. This does not prove every ECU
clears access on that transition. Use SecurityAccess only on vehicles you are authorized to diagnose.
