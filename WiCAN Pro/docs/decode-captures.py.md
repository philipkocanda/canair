#### decode-captures.py

Decodes captured UDS response payloads from `captures/` directory using the WiCAN expression evaluator (faithful Python port of `wican-fw/main/expression_parser.c`). Loads all `captures/*.yaml` files (skips `SCHEMA.yaml`), resolves ECU names to TX IDs via `ecus.yaml`.

```bash
python3 decode-captures.py               # Decode all captures
python3 decode-captures.py --session 2025-08-04
python3 decode-captures.py --ecu BMS
python3 decode-captures.py --pid 2101
python3 decode-captures.py --param SOC
python3 decode-captures.py --hexdump     # Annotated hex dump with byte→parameter mapping
python3 decode-captures.py --raw <hex> --expr "B09/2"  # Direct expression evaluation
python3 decode-captures.py --raw <hex> --pid 2101      # Decode raw payload against PID defs
```
