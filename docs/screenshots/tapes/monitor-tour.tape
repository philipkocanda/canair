# canair monitor — a tour of the main screen, on a recorded motorway drive (device-free).
#
# vhs cannot press f10, and it types alt+letter as escape then the letter, which the monitor
# reads as two keys (escape deselects, the letter acts). So the tour reaches commands through
# the palette; the menus are in the stills (monitor-menu.svg).
#
# Rendered by `python3 scripts/gen/gen_screenshots.py` (which runs vhs from the repo root).
# scripts/gen/monitor_demo.py runs the real monitor with a replay terminal standing in for the
# dongle: it answers each request with the payloads the bundled Ioniq profile recorded on the
# road, so the values move as they did. The setup is hidden and uses only portable paths.

Output "docs/screenshots/monitor-tour.gif"

Set Shell "bash"
Set FontSize 15
Set Width 1180
Set Height 820
Set Padding 16
Set Framerate 24
Set Theme "Dracula"

Hide
Type "export XDG_CONFIG_HOME=$(mktemp -d) CANAIR_NO_UPDATE_CHECK=1 CANAIR_VARIANT=28kwh" Enter
Type "clear" Enter
Type "uv run python scripts/gen/monitor_demo.py" Enter
Sleep 5s
Show

# The values update in place; changed values and bytes light up.
Sleep 4s

# Select a signal: the bottom line shows it and its keys.
Down Sleep 500ms
Down Sleep 500ms
Down Sleep 500ms
Down Sleep 1500ms

# The palette finds any command by name: switch to the signals-only view…
Ctrl+p Sleep 1s
Type "signals" Sleep 1500ms
Enter Sleep 3s

# …and pause polling.
Ctrl+p Sleep 1s
Type "pause" Sleep 1500ms
Enter Sleep 2500ms

# Paused: a badge in the status line. Space resumes.
Space Sleep 2s

# Back to the full view (signals and bytes), then quit.
Type "V" Sleep 2500ms
Type "q"
Sleep 1s
