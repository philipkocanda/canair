# Read live data

With your [dongle connected](connect-device.md) and `canair status` happy, read
some real data. These examples target the **bundled 2017 Ioniq profile**, so the
ECU names (`BMS`, `MCU`, …) are Ioniq-specific — but `discover` works on any car.

```bash
# See every ECU responding on the bus (works on any vehicle)
canair discover

# Read the battery ECU's main PID, decoded into named parameters (Ioniq)
canair query BMS:2101

# Read all known parameters for an ECU
canair query BMS

# Read specific named parameters across ECUs
canair query --param SOC_BMS BATTERY_VOLTAGE BATTERY_POWER

# Watch a value live — refreshes and highlights changed bytes
canair query BMS:2101 --monitor

# Read Diagnostic Trouble Codes across every ECU
canair dtc --all
```

`canair query` uses a small [selector syntax](../concepts/query-mini-language.md)
(`ECU:PID`) and can run multi-step pipelines over one session.

## On a different car

`discover` will list *your* car's ECUs, but `BMS:2101` and the other named reads
depend on the active profile's definitions — which, on a fresh profile, are
empty. That's the whole point of the next section:

**→ [Bring your own car](../bring-your-own-car/overview.md)** builds a profile for
your vehicle so these named reads work for *you*.
