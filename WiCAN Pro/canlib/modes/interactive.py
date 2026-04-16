"""Interactive REPL mode."""

import re

from ..elm327 import parse_elm_response, elm_hex_to_wican_bytes
from ..terminal import WiCANTerminal, reboot_wican
from ..pids import build_param_index, build_ecu_index
from ..formatting import print_decoded_params, print_hexdump
from ..expression import evaluate_expression


async def mode_interactive(terminal: WiCANTerminal, pids_data: dict, verbose: bool):
    """Interactive REPL mode -- type ELM327/UDS commands directly."""
    # Lazy imports to avoid circular dependencies for modes that reference each other
    from .identity import mode_identity
    from .skm_wakeup import mode_skm_wakeup
    from .tester import mode_tester_present

    import asyncio

    print("WiCAN ELM327 Terminal -- Interactive Mode")
    print(f"Connected to {terminal.host}")
    print()
    print("Commands:")
    print("  AT commands    ATZ, ATSH7E4, ATS0, etc.")
    print("  UDS requests   2101, 22C00B, etc. (set header first with ATSH)")
    print("  !decode        Decode last response using YAML definitions")
    print("  !hexdump       Show hex dump of last response")
    print("  !info <ECU>    Show ECU info from YAML (e.g., !info BMS)")
    print("  !list          List all known ECUs")
    print("  !skm [level]   SKM wakeup (acc/ign1/ign2/start, default: acc)")
    print("  !tester [id]   TesterPresent loop (broadcast or target ECU, Ctrl+C to stop)")
    print("  !identity      Query UDS identity DIDs from current ECU (set header first with ATSH)")
    print("  !reboot        Reboot WiCAN to restore AutoPID mode")
    print("  !quit / Ctrl+C Exit")
    print()

    ecu_index = build_ecu_index(pids_data)
    param_index = build_param_index(pids_data)
    last_response = None
    last_tx_id = None

    while True:
        try:
            cmd = await asyncio.get_event_loop().run_in_executor(None, lambda: input("> "))
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        cmd = cmd.strip()
        if not cmd:
            continue

        if cmd.lower() in ("!quit", "!exit", "!q"):
            print("Bye!")
            break

        if cmd.lower() == "!reboot":
            reboot_wican(terminal.host)
            break

        if cmd.lower().startswith("!skm"):
            parts = cmd.split()
            level = parts[1] if len(parts) > 1 else "acc"
            await mode_skm_wakeup(terminal, level, verbose)
            continue

        if cmd.lower().startswith("!tester"):
            parts = cmd.split()
            target = parts[1] if len(parts) > 1 else None
            print("  Starting TesterPresent loop. Ctrl+C to stop.")
            await mode_tester_present(terminal, target, 1.0, verbose)
            continue

        if cmd.lower() == "!list":
            print(f"\n{'ECU':<10} {'TX ID':<8} {'PIDs'}")
            print("\u2500" * 40)
            for name, info in sorted(ecu_index.items()):
                pids = ", ".join(sorted(info["pids"].keys()))
                print(f"{name:<10} 0x{info['tx_id']:03X}    {pids}")
            print()
            continue

        if cmd.lower().startswith("!info "):
            ecu_name = cmd[6:].strip().upper()
            if ecu_name not in ecu_index:
                print(f"  Unknown ECU: {ecu_name}. Use !list to see available ECUs.")
                continue
            info = ecu_index[ecu_name]
            print(f"\n  {ecu_name} -- TX 0x{info['tx_id']:03X}")
            for pid_code, pid_info in sorted(info["pids"].items()):
                n_params = len(pid_info["parameters"])
                enabled = "enabled" if pid_info["enabled"] else "disabled"
                print(f"    PID {pid_code} ({enabled}, {pid_info['period']}ms, {n_params} params)")
                for pname in sorted(pid_info["parameters"].keys()):
                    pdef = pid_info["parameters"][pname]
                    v = "+" if pdef.get("verified") else "?"
                    print(f"      {v} {pname}: {pdef.get('expression', '')} [{pdef.get('unit', '')}]")
            print()
            continue

        if cmd.lower() == "!decode":
            if last_response is None or not last_response.get("ok"):
                print("  No successful response to decode. Send a UDS request first.")
                continue
            if last_tx_id is None:
                print("  No TX ID set. Use ATSH to set the ECU header first.")
                continue
            resp_bytes = last_response["bytes"]
            if resp_bytes[0] == 0x61:
                pid_str = f"21{resp_bytes[1]:02X}"
            elif resp_bytes[0] == 0x62:
                pid_str = f"22{resp_bytes[1]:02X}{resp_bytes[2]:02X}"
            else:
                print(f"  Unknown response SID: 0x{resp_bytes[0]:02X}")
                continue

            found = False
            for ecu_name, ecu_info in ecu_index.items():
                if ecu_info["tx_id"] != last_tx_id:
                    continue
                pid_upper = pid_str.upper()
                if pid_upper in ecu_info["pids"]:
                    wican_bytes = elm_hex_to_wican_bytes(last_response["hex"])
                    params = ecu_info["pids"][pid_upper]["parameters"]
                    results = []
                    for pname, pdef in params.items():
                        expr = pdef.get("expression", "")
                        unit = pdef.get("unit", "")
                        verified = pdef.get("verified", False)
                        if not expr:
                            continue
                        try:
                            value = evaluate_expression(expr, wican_bytes)
                            value = round(value * 100) / 100
                            results.append((pname, value, unit, expr, None, verified))
                        except Exception as e:
                            results.append((pname, None, unit, expr, str(e), verified))
                    print(f"\n  {ecu_name} -- PID {pid_str} -- TX 0x{last_tx_id:03X}")
                    print_decoded_params(results, verbose=verbose)
                    print()
                    found = True
                    break
            if not found:
                print(f"  No YAML definition for TX 0x{last_tx_id:03X} PID {pid_str}")
            continue

        if cmd.lower() == "!hexdump":
            if last_response is None:
                print("  No response to dump.")
                continue
            if "bytes" in last_response:
                print(f"\n  Raw ELM response ({len(last_response['bytes'])} bytes):")
                print_hexdump(last_response["bytes"])
                wican_bytes = elm_hex_to_wican_bytes(last_response["hex"])
                print(f"  WiCAN-indexed ({len(wican_bytes)} bytes, with PCI prefix):")
                print_hexdump(wican_bytes)
            else:
                print(f"  Raw: {last_response.get('raw', '(none)')}")
            continue

        if cmd.lower() == "!identity":
            if last_tx_id is None:
                print("  No ECU header set. Use ATSH<id> first (e.g., ATSH7A0).")
            else:
                await mode_identity(terminal, last_tx_id, session=False, wake=False,
                                    as_json=False)
            continue

        # Track ATSH commands to know current TX ID
        atsh_match = re.match(r"^ATSH\s*([0-9A-Fa-f]{3})$", cmd, re.IGNORECASE)
        if atsh_match:
            last_tx_id = int(atsh_match.group(1), 16)

        try:
            raw = await terminal.send_command(cmd)
            print(raw)

            response = parse_elm_response(raw)
            if response.get("ok") or response.get("nrc") is not None:
                last_response = response

                if response.get("nrc") is not None:
                    nrc = response["nrc"]
                    svc = response.get("nrc_service", 0)
                    desc = response.get("nrc_desc", "unknown")
                    print(f"  [NRC] Service 0x{svc:02X} rejected: 0x{nrc:02X} ({desc})")

        except ValueError as e:
            print(f"  !! {e}")
        except Exception as e:
            print(f"  Error: {e}")
    print()
