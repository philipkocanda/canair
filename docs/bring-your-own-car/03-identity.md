# 3. Read identity

After you [register](02-discover-ecus.md) the ECUs, `canair identity` reads the standard identity
data of an ECU and decodes it for display. The data includes the part number, the hardware and
software versions, the serial number and the VIN. canair uses UDS (`22 F1xx`) or KWP2000
(`1A 8x/9x`), according to the protocol that the ECU speaks (see
[ECU protocols & PID prefixes](../concepts/ecu-protocols.md)).

```bash
canair identity MyECU
canair identity 770              # by address instead of name
canair identity MyECU --session  # some ECUs only answer in an extended session
canair identity BMS --protocol kwp   # force KWP2000 for a powertrain ECU
```

`canair identity` is a **read**. It displays what it finds and writes nothing to `ecus/`. One
exception: it can offer to record a protocol that it proved in `identity.id_protocol`.

To capture the identity of every ECU during the discovery sweep, use `canair discover --identify`
(see [step 2](02-discover-ecus.md)).

Did you capture an identity DID, and you want to know where a part number landed? Run
`canair search 95400G7470`. It finds the value in every ECU and PID. `canair search` with no
pattern lists every plausible string in your captures. See
[6. Analyze](06-analyze.md#finding-text-part-numbers-versions-vins).

## Why it's worth doing early

Identity tells you *what each ECU is*. You can then rename the placeholder ECU entries
(`Unknown-770` → `IGPM`). You can also compare part numbers with the known signal maps of other
vehicles. This gives you a large head start on [analysis](06-analyze.md). A shared part number
with the public PID data of another car is one of the fastest ways to get hypotheses.

## Recording curated identity fields

To *store* more about an ECU than the raw decoded DIDs, use the validated editor. You can store
notes or a description:

```bash
canair pids set-identity MyECU notes "Body control module; mirrors IGPM door bits"
canair pids rm-identity  MyECU sw_version   # drop a field you filed wrongly
```

The field name must be one that the ECU schema declares (`identity` in
`src/canair/schema/pids_schema.yaml`). A typo is a validation **error**, and canair reverts the
edit. A misfiled `sofware:` cannot stay in the profile unnoticed.

### One reading, one field

Several of these fields have nearly the same meaning: `firmware`, `fw_version`, `sw_version` and
`sw_id`, and `hw_version` and `hw_sw`. Each marque names its identity DIDs differently. Pick the
field that matches what the ECU reported. **Do not copy the value into a second field.** The copy
becomes dead data, and later readers mistake it for independent evidence.

`canair validate pids` warns when two fields with the same meaning hold the same value. The
warning points you to `rm-identity`.

---

Next: **[4. Scan for data →](04-scan.md)**
