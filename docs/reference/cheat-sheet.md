# Cheat sheet

The commands you use most, on one page. Run `canair <command> --help` for every flag. The
[CLI reference](cli/index.md) lists all commands.

!!! warning "Live commands talk to the car"
    Commands in the first two sections send requests to the car. Reads are safe. Actuation and
    other state changes ask for confirmation first. See [Safety](../concepts/safety.md).

## Check the setup

```bash
canair status                  # transport, device, reachability, versions
canair config                  # config locations and resolved settings
canair profile list            # profiles; the active one is marked
canair profile use NAME --variant VARIANT   # choose the profile and the car variant
canair logs                    # transport drops and errors
```

## Read the car (live)

```bash
canair discover                # which ECUs answer
canair discover --register     # ...and add them to the profile
canair identity ECU            # part number, versions, VIN (printed, not stored)
canair read ECU:PID            # one PID, decoded into signals
canair read ECU                # all known PIDs of one ECU
canair read --param NAME ...   # named signals across ECUs
canair monitor ECU:PID         # live view
canair monitor @group          # live view of a saved group
canair monitor                 # picker for groups, ECUs and PIDs
canair dtc --all               # trouble codes from every ECU
canair scan ECU                # which PIDs and DIDs an ECU answers
canair sniff                   # passive CAN listener (slcan-tcp only)
```

Add `--save --label "text" --state STATE` to `read`, `monitor` or `scan` to record the data in
`captures/`. Add `--wait` to wait for the device. Add `--session` or `--wake` for ECUs that need an
extended session or a wake-up.

## Analyze captures (offline)

```bash
canair captures uds --sessions            # list recorded sessions
canair captures uds ECU:PID --diff        # byte-level diff across captures
canair decode ECU:PID --plot              # plot decoded values
canair investigate ECU PID                # per-byte report for one PID
canair hunt uds ECU PID --against ECU:PID:SIGNAL   # which byte is a known signal?
canair correlate --against ECU:PID:SIGNAL --bytes   # bytes that follow a known signal
canair search TEXT                        # find a string, VIN or byte pattern
canair coverage ECU PID                   # decoding gaps
canair research --summary                 # open reverse-engineering backlog
canair bix --annotate PAYLOAD             # label each byte of a payload
```

## Edit the profile

```bash
canair pids upsert-param ...   # add or update a signal (always use this, not hand edits)
canair states                  # list and edit vehicle states
canair groups                  # list and edit @groups
canair validate all            # check the whole profile
canair wican autopid write     # generate the WiCAN AutoPID JSON
```

## Import, share and update

```bash
canair import uds ECU:PID=PAYLOAD --label "text"   # add a reading without a device
canair import can FILE                             # add a raw frame log
canair contribute --dry-run    # preview a pull request that shares your profile
canair update --check          # is there a newer release?
```

## Habits that save time

- Pass `--profile NAME` on every command that writes.
- Use `ECU:PID` with a colon. A space means a separate selector.
- Record with `--save`. Do not edit `captures/` by hand.
- Mark new signals `--unverified` until you confirm them.
- Check byte offsets with `canair bix`. Do not convert them by hand.
