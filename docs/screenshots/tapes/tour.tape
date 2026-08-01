# A quick tour of read-only canair analysis commands (device-free).
#
# Rendered by `python3 scripts/gen_screenshots.py` (which runs vhs from the repo
# root). Output path is relative to the repo root. The setup block is hidden and
# uses only portable paths (no absolute/owner-specific paths). A relative
# --profiles-dir keeps the username out of any path shown on screen.

Output "docs/screenshots/tour.gif"

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 720
Set Padding 16
Set Framerate 24
Set Theme "Dracula"
Set TypingSpeed 40ms

Hide
Type "export XDG_CONFIG_HOME=$(mktemp -d) CANAIR_NO_UPDATE_CHECK=1 FORCE_COLOR=1 COLUMNS=96" Enter
Type "alias canair='uv run canair --profiles-dir profiles --profile ioniq-2017'" Enter
Type "clear" Enter
Show

Type "canair ecu BMS pids"
Sleep 500ms
Enter
Sleep 3500ms

Type "clear" Enter
Type "canair decode BMS:2101"
Sleep 500ms
Enter
Sleep 3500ms

Type "clear" Enter
Type "canair coverage BMS"
Sleep 500ms
Enter
Sleep 3500ms
