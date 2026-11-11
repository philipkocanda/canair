# 7. Define & verify

[Analysis](06-analyze.md) gave you a hypothesis that the data confirmed. For example: *"byte 12 of
`MyECU:2101` correlates with a known speed signal at r=0.99, about 1:1, so it is speed in km/h."*
Now you turn that hypothesis into a named **signal** that is stored in the profile. The signal
makes `canair read MyECU` decode the value for anyone who uses your profile.

This is the core of **contributing PIDs**. A raw byte becomes a documented, validated, shareable
signal. PID stands for parameter identifier. In canair, a PID is a diagnostic request that returns
a page of data.

## Write the signal

Use `canair pids upsert-param`. The command is surgical, preserves comments and validates against
the schema. It reverts the edit automatically if the edit would break validation:

```bash
canair pids upsert-param MyECU 2101 SPEED_KMH "[B12]" \
    --unit km/h --min 0 --max 200 \
    --source "hunt vs GPS speed, r=0.99" --unverified
```

- `SPEED_KMH` — the signal name. It must be unique across the shipped signals of the profile.
- `"[B12]"` — the [expression](../concepts/byte-indexing.md) that extracts the value from the
  payload. Byte indexing matters, so confirm the offset with `canair bix --annotate`.
- `--unit`, `--min`, `--max` — metadata that documents and bounds the signal.
- `--category` — what the signal is. Use it **only where the unit does not already say it**:
    - A `%` that is a `state_of_charge`, `state_of_health` or `humidity`.
    - A two-state signal that is a `door`, `opening`, `lock`, `lamp`, `plug` or `charging`.
      A two-state signal also gets `--value 0=closed --value 1=open` (or its own labels). These
      record which value means which.

    A `V` signal is already a voltage. If you state it anyway, validation fails.

- `--source` — *why you believe it*. Give evidence, such as a correlation, a reference car or a
  datasheet.
- `--unverified` — **start here.** A hypothesis stays a hypothesis until you prove it.

### Share fields across a page

Signals on one page often share fields. An example is a group of cell voltages with the same unit,
range and evidence. State these fields once on the PID, for example with
`canair pids set-pid-defaults ECU PID --unit V --min 0 --max 5 --verified`. The command also
accepts `--source` and `--source-link`. Each signal that sets none of its own inherits the
defaults. A signal that sets its own value keeps it. `canair pids hoist-defaults` moves repetition
that you already have up to the PID. It changes no signal.

## Verify before you trust it

Mark a signal `--verified` only after you confirm that it holds up. Check three things:

- The value tracks a known reference.
- The value stays physically plausible across vehicle states.
- The signal survives more captures.

```bash
canair decode MyECU 2101 --param SPEED_KMH --stats   # sane range/distribution?
canair pids upsert-param MyECU 2101 SPEED_KMH "[B12]" --verified   # promote
```

The discipline is: **read and capture freely, define conservatively.** Bad profiles spread when
optimistic guesses become "fact".

## Fixing and renaming

Maybe you got a byte offset wrong, mis-keyed a PID or need to remove a dead one. Use the surgical
`canair pids` editors, and do not touch the YAML. Every edit preserves comments and validates
against the schema. canair reverts the edit if validation fails:

```bash
canair pids rename-pid   MyECU B002 22B002   # re-key a PID (e.g. add the 22 service prefix)
canair pids rename-param MyECU 2101 OLD NEW   # rename a parameter (fields preserved)
canair pids rm-param     MyECU 2101 NAME      # remove one parameter
canair pids rm-pid       MyECU 22B002         # remove a whole PID (drops pids: if it was the last)
```

If no `canair pids` subcommand covers a field you need, edit the file directly. Then run
`canair validate pids` and [report the gap](../contributing/index.md).

`canair validate pids` also catches a common hand-editing slip: a key that is written twice. An
example is two signals of the same name in one PID. YAML would load the later one and say nothing.
Every other `canair validate` target makes the same check on its file. The files are
`profile.yaml`, `variants.yaml`, `vehicle_states.yaml`, `groups.yaml`, `can_buses.yaml`,
`actions.yaml` and `signals/`.

## Audit for gaps

`canair coverage` compares your signal expressions with the payload length from the newest session
of each PID. It flags what is still undecoded:

```bash
canair coverage MyECU          # unmapped bytes, partial bitfields, no-capture PIDs
canair validate all           # schema + duplicate-name checks
```

`canair coverage` reports these findings per PID:

| Finding | Meaning |
|---|---|
| `UNMAPPED` | a data byte that no expression reads |
| `UNVERIFIED` | a data byte that only unverified signals read |
| `BITS` | a byte that is read bit by bit, with some bits still undecoded |
| `PAST END` | a signal that reads beyond the end of the payload — a short reply, or a wrong offset |
| `MAP` | the PID declares a [support map](../concepts/analysis-techniques.md#support-maps-which-bits-are-signals-at-all): its own bytes, and any covered bytes that carry no signal, are not data to decode |
| `OUTSIDE MAP` | a signal reads a bit or byte that the declared support map does not mark (a bit just above a mark is shown dimmed as `FIELD CONTINUATION`) |
| `FIELDS` | a field map: how many parameters it marks, against how many the bytes hold and how many signals define (`FIELDS BROKEN` when no split fits) |
| `OPEN FIELDS` | the parameters of a field map that no signal reads, as byte ranges |
| `NO CAPTURE` | no positive reply recorded (`in scope` when a scope flag such as `--until` is set) |
| `NEGATIVE RESPONSE ONLY` | the electronic control unit (ECU) only ever answered with a negative response. The negative response code (NRC) is shown |

### Which reply sets the length

- The **newest session** that read the PID sets the length. canair orders sessions by date, then
  by the time of the first capture. Within the session, the most common length wins. On a tie, the
  longer length wins.
- Only replies that echo the PID count. A misfiled reply never sets the length. A misfiled reply
  is the answer of another PID, stored under this one. A negative response never sets it either.
- A PID flagged `variable_length:` (`canair pids set-pid-variable-length`) has no single length.
  canair audits it against its **longest** reply in scope instead.
- `may include trailing frame padding` marks a length that could be padding. Captures made before
  sessions recorded a canair `version` may store the last controller area network (CAN) frame
  padded out to 8 bytes. So may anything brought in with `canair import uds`. canair raises the
  flag only when the length is a whole number of frames and every reply of that length ends in
  the same fill byte (`00`, `55`, `AA`, `CC` or `FF`). Read the PID again to settle it.

### Body, climate and powertrain pages

On a body or climate ECU, the data identifiers (DIDs) are often rows of bit flags. On such an ECU,
run `canair investigate <ECU> --support-map` first. If a DID starts with a constant support map,
declare it with `canair pids set-support-map`. `coverage` then stops counting bits that are not
signals as gaps. `BITS … missing{…}` lists only the bits that the map marks. It shows the rest
as `(+N not signals)`.

Do the same on a powertrain page (service 21). There the map counts parameters. An `EXACT` split
names each parameter that nobody has decoded yet.

Unmapped bytes are your next research leads. Loop back to [capture](05-capture.md) and
[analyze](06-analyze.md).

> **Verified a signal? Consider contributing it back.** You do not have to wait for a "finished"
> profile. A single verified signal, or a corrected one, is a welcome pull request. It helps
> everyone with your car. See [Share](08-share.md#contribute-your-profile-back).

---

Next: **[8. Share →](08-share.md)**
