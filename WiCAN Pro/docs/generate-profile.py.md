#### generate-profile.py

Generates WiCAN vehicle profiles from `ioniq-2017-pids.yaml`.

```bash
python3 generate-profile.py              # Generate Vehicle Profiles/ioniq-2017.json
python3 generate-profile.py --stats      # Show PID/parameter summary table
python3 generate-profile.py --verified-only  # Exclude unverified parameters
python3 generate-profile.py --diff       # Download device config, show parameter-level diff
python3 generate-profile.py --download   # Dump current device config
python3 generate-profile.py --upload     # Push to WiCAN (converts to device format)
python3 generate-profile.py --upload --reboot  # Push + reboot device
python3 generate-profile.py --wican home|vpn|<url>  # Select WiCAN address
python3 generate-profile.py --no-write   # Dry run (don't write output file)
```
