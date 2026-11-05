"""The examples and legend shown under ``canair ecu --help``."""

EPILOG = """
  canair ecu                 # plain list of all ECUs (one per line)
  canair ecu BMS             # details + stats for the BMS
  canair ecu MDPS            # aliases resolve too (MDPS -> EPS)
  canair ecu 0x7E4           # hex TX id also works
  canair ecu 0x7EC           # hex RX id resolves too (the ECU's response address)
  canair ecu --states        # add a STATES column (states each ECU is readable in)
  canair ecu --sort states   # group the list by vehicle state
  canair ecu BMS --json      # machine-readable
  canair ecu --json          # all ECUs as JSON
  canair ecu HVAC edit       # open HVAC's ecus/ YAML in $EDITOR (TTY only)
  canair ecu rename Unknown-7D5 EPB   # rename an ECU (rewrites key + file)
  canair ecu revisions VCU   # an ECU's revisions (see `canair ecu revisions -h`)

Columns & legend:
  BUS    physical CAN bus segment(s) the ECU sits on (profile-specific codes,
         e.g. Hyundai B-CAN/P-CAN/C-CAN/MM-CAN/H-CAN/ALL); some ECUs span two
         (shown `H-CAN/P-CAN`). Blank (`—`) when unknown. The list is sorted by
         BUS by default. Shown as the last (widest, most-variable) column so
         the numeric columns stay aligned.
  PIDS   number of active (non-ignored) PIDs/DIDs defined.
  VERIF  verified/total parameters (green when all verified).
  CAPS   number of saved captures for the ECU.
  cap    in the per-PID detail view, "N cap" = number of saved captures for
         that individual PID.
  STATES the vehicle states the ECU is readable/awake in — its ECU-level
         `vehicle_states`, or the union of its PIDs' when that's unset. Opt-in
         (`--states`); shown after BUS.

  Sort with `--sort {bus,name,tx,proto,pids,verif,caps,states}`: string/hex
  columns (bus, name, tx, proto, states) ascending; numeric columns (pids,
  verif, caps) descending. `name` breaks ties.
"""
