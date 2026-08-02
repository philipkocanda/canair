# LIVE monitor capture — battery (BMS) in READY mode. NON-REPRODUCIBLE: this
# talks to a real car, so it is recorded manually (marked `live: true` in
# shots.yaml and skipped by the default `gen_screenshots.py` run). Re-record with:
#
#   python3 scripts/gen_screenshots.py --only monitor-bms
#
# Requires a reachable WiCAN (`--wican home` here) with the car awake. Uses the
# real user config for the device, but a relative --profiles-dir + the bundled
# profile for definitions. --save records the live payloads (state: ready).

Output "docs/screenshots/monitor-bms.gif"

Set Shell "bash"
Set FontSize 15
Set Width 1180
Set Height 900
Set Padding 16
Set Framerate 24
Set Theme "Dracula"

Hide
Type "export CANAIR_NO_UPDATE_CHECK=1" Enter
Type "clear" Enter
Show

Type "uv run canair --profiles-dir profiles --profile ioniq-2017 monitor BMS:2101 --wican home --transport wican-ws --interval 2 --save --state ready --label 'READY mode — battery (live monitor, docs)'"
Sleep 500ms
Enter
Sleep 20s
Type "q"
Sleep 1500ms
