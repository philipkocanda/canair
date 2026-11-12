# canair monitor — the README's opening picture: drivetrain values and bytes changing live.
#
# Rendered by `python3 scripts/gen/gen_screenshots.py` (which runs vhs from the repo root).
# scripts/gen/monitor_demo.py runs the real monitor on a recorded motorway drive of the bundled
# Ioniq profile, so the motor speed, the torque and the payload bytes move as they did on the
# road. No car or device is involved. Short and without key presses, so it loops cleanly.

Output "docs/screenshots/monitor-hero.gif"

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 760
Set Padding 12
Set Framerate 12
Set Theme "Dracula"

Hide
Type "export XDG_CONFIG_HOME=$(mktemp -d) CANAIR_NO_UPDATE_CHECK=1 CANAIR_VARIANT=28kwh" Enter
Type "clear" Enter
Type `uv run python scripts/gen/monitor_demo.py --query "MCU:2102 VCU:2101" --label "Motorway drive — drivetrain" --rows 2` Enter
Sleep 7s
Show

Sleep 9s

Hide
Type "q"
Sleep 1s
