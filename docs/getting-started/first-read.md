# Read live data

Connect your [dongle](connect-device.md) and check that `canair status` reports it as reachable.
Then read some real data. These examples use the **bundled Ioniq Electric profile**. The ECU names
(`BMS`, `MCU`, …) are specific to the Ioniq. `discover` works on any car.

First, tell canair which car you have. A profile can describe several
[variants](../concepts/variants.md) of one model. The Ioniq profile has a 28 kWh car and a 38 kWh
facelift. canair does not talk to a car or record data until you name your variant:

```bash
canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # once; remembered for this profile
canair variants                                          # what the profile's variants are
```

`discover` and `identity` work before you choose a variant. You use them to find out which car you
have.

```bash
# See every ECU responding on the bus (works on any vehicle)
canair discover

# Read the battery ECU's main PID, decoded into named signals (Ioniq)
canair read BMS:2101

# Read all known signals for an ECU
canair read BMS

# Read specific named signals across ECUs
canair read --param SOC_BMS BATTERY_VOLTAGE BATTERY_POWER

# Watch a value live — refreshes, highlights changed bytes and the signals they decode
canair monitor BMS:2101

# Read Diagnostic Trouble Codes across every ECU
canair dtc --all
```

`canair read` uses a small [selector syntax](../concepts/query-mini-language.md) (`ECU:PID`). It
can run multi-step pipelines in one session.

After you capture some readings, `canair ecu <ECU> pids` shows the latest decoded value of each
PID. It turns raw payloads into named signals with units:

![canair ecu BMS pids — latest decoded values](../screenshots/ecu-bms-pids.svg)

## Watch it live

`canair monitor` is canair's main screen. It polls the PIDs you choose and shows their decoded
signals live, in place. Changed values and the bytes they come from light up:

```bash
canair monitor BMS:2101                       # the battery
canair monitor "BMS:2101 VCU:2101 MCU:2102"   # battery, vehicle control and motor together
canair monitor                                # pick ECUs and PIDs from a list
```

![canair monitor — the main screen: menus, live values, the status line](../screenshots/monitor.svg)

Every command is in the menu bar (**`f10`**, or click a title) and in the command palette
(**`ctrl+p`**). Press **`?`** for every key. Add **`--save`** to record what you watch.

**→ [The monitor](monitor.md)** is the full tour: the screen, the status line, recording, editing
signals, and the monitor on a phone.

## On a different car

`discover` lists the ECUs of *your* car. However, `BMS:2101` and the other named reads depend on the
definitions in the active profile. On a new profile, these definitions are empty. The next section
fills them:

**→ [Bring your own car](../bring-your-own-car/overview.md)** builds a profile for your vehicle.
Then these named reads work for *you*.
