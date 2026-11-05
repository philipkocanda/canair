"""The text shown under ``canair wican --help``."""

EPILOG = """
Build and sync the WiCAN device's AutoPID vehicle profile.

`canair wican` groups two families of device operations. Nothing is written
until you ask for it — a bare `canair wican` just prints this help.

Terminology (deliberately disambiguated):
  * the canair *profile bundle* — the `profiles/<name>/` directory (ecus/,
    profile.yaml, captures/, …) managed by `canair profile`; and
  * the WiCAN *AutoPID profile* — the JSON generated from that bundle's
    ecus/ and stored on the device's AutoPID feature, managed here.

Subcommands:
  autopid write               Generate the v4 and v6 AutoPID JSON to the bundle's out/
  autopid upload              Generate + upload to the device (Pro only)
  autopid download            Download the device's current AutoPID JSON (Pro)
  autopid diff                Download + diff against the generated JSON (Pro)
  autopid stats               Per-ECU/PID statistics table
  mode show                   Show the device's active protocol
  mode set MODE               Switch the device protocol/mode + align transport (Pro)

Examples:
  canair wican autopid write                       # verified-only, both firmware files
  canair wican autopid write --firmware 6          # only the firmware v6 table
  canair wican autopid write --include-unverified  # also unverified: autopid-v*-unverified.json
  canair wican autopid write --no-expected-responses  # omit the response-frame counts
  canair wican autopid upload --reboot             # upload + reboot to apply
  canair wican autopid diff --wican home           # compare device vs generated
  canair wican mode set slcan                 # raw CAN + set transport slcan-tcp
"""
