# canair decode --plot — the interactive signal explorer (device-free).
#
# Rendered by `python3 scripts/gen_screenshots.py` (which runs vhs from the repo
# root). Output path is relative to the repo root. The setup block is hidden and
# uses only portable paths (no absolute/owner-specific paths). A relative
# --profiles-dir keeps the username out of any path shown on screen.

Output "docs/screenshots/decode-plot.gif"

Set Shell "bash"
Set FontSize 16
Set Width 1100
Set Height 640
Set Padding 16
Set Framerate 24
Set Theme "Dracula"

Hide
Type "export XDG_CONFIG_HOME=$(mktemp -d) CANAIR_NO_UPDATE_CHECK=1 FORCE_COLOR=1" Enter
Type "clear" Enter
Show

Type "uv run canair --profiles-dir profiles --profile ioniq-2017 decode BMS:2101 --plot"
Sleep 700ms
Enter
Sleep 4s

# Switch from raw-byte mode to decoded parameters (they vary meaningfully)
Type "m" Sleep 1500ms

# Step through a few parameters to show different signal shapes
Down Sleep 1400ms
Down Sleep 1400ms
Down Sleep 1400ms
Down Sleep 1800ms

Sleep 1200ms
Type "q"
Sleep 1s
