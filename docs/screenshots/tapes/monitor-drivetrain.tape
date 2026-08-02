# LIVE monitor capture — drivetrain (VCU vehicle-control + MCU motor/inverter)
# in READY mode, a cross-ECU view. NON-REPRODUCIBLE: this talks to a real car,
# so it is recorded manually (marked `live: true` in shots.yaml and skipped by
# the default `gen_screenshots.py` run). Re-record with:
#
#   python3 scripts/gen_screenshots.py --only monitor-drivetrain
#
# Requires a reachable WiCAN (`--wican home` here) with the car awake. Uses the
# real user config for the device, but a relative --profiles-dir + the bundled
# profile for definitions. --save records the live payloads (state: ready).

Output "docs/screenshots/monitor-drivetrain.gif"

Set Shell "bash"
Set FontSize 15
Set Width 1180
Set Height 820
Set Padding 16
Set Framerate 24
Set Theme "Dracula"

Hide
Type "export CANAIR_NO_UPDATE_CHECK=1" Enter
Type "clear" Enter
Show

Type "uv run canair --profiles-dir profiles --profile ioniq-2017 monitor 'VCU:2101 MCU:2102' --wican home --transport wican-ws --interval 2 --save --state ready --label 'READY mode — drivetrain (live monitor, docs)'"
Sleep 500ms
Enter
Sleep 20s
Type "q"
Sleep 1500ms
