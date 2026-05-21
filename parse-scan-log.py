#!/usr/bin/env python3
"""
parse-scan-log.py — Parse ECU scan log files to extract RoutineControl (0x31) results.

Log format:
  [timestamp] <request> -> [echo] | [request_echo] | <response>

Usage:
  python3 parse-scan-log.py logs/ecu/BCM-2026-04-20.log
  python3 parse-scan-log.py logs/ecu/BCM-2026-04-20.log --service 31
  python3 parse-scan-log.py logs/ecu/BCM-2026-04-20.log --hits-only
"""

import re
import sys
import argparse
from collections import defaultdict

# UDS NRC descriptions
NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

LINE_RE = re.compile(
    r"\[(?P<ts>[^\]]+)\]\s+"
    r"(?P<req>[0-9A-Fa-f]+)\s+->\s+"
    r"(?P<rest>.+)"
)


def parse_response(rest: str) -> str:
    """Extract the final response field from the -> ... chain."""
    parts = [p.strip() for p in rest.split("|")]
    return parts[-1] if parts else rest.strip()


def classify_response(req_hex: str, resp: str):
    """
    Returns (category, detail) where category is one of:
      'hit'     — positive response (71xx)
      'nrc'     — negative response code
      'nodata'  — NO DATA / timeout
      'keepalive' — 7E00 tester present ack
      'other'   — anything else
    """
    resp = resp.strip().upper()

    if not resp or resp == "NO DATA":
        return "nodata", None

    if resp == "7E00":
        return "keepalive", None

    # Positive RoutineControl response: 71 <sub-fn> <RID high> <RID low> [data...]
    if resp.startswith("71"):
        return "hit", resp

    # Negative response: 7F <service> <NRC>
    if resp.startswith("7F") and len(resp) >= 6:
        try:
            nrc_byte = int(resp[4:6], 16)
            desc = NRC.get(nrc_byte, f"unknown(0x{nrc_byte:02X})")
            return "nrc", (nrc_byte, desc)
        except ValueError:
            pass

    return "other", resp


def parse_log(path: str, service_filter: str | None = None):
    """
    Parse the log file and return a list of dicts per scan request:
      { ts, request, response, category, detail }
    """
    entries = []
    service_prefix = service_filter.upper() if service_filter else None

    with open(path, "r") as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            req = m.group("req").upper()
            # Filter by service byte prefix (e.g. "31" matches requests starting with "31")
            if service_prefix and not req.startswith(service_prefix):
                continue
            resp = parse_response(m.group("rest"))
            category, detail = classify_response(req, resp)
            if category == "keepalive":
                continue  # skip keepalives in output
            entries.append({
                "ts": m.group("ts"),
                "request": req,
                "response": resp.strip(),
                "category": category,
                "detail": detail,
            })

    return entries


def summarize(entries, hits_only=False):
    nrc_counts = defaultdict(int)
    hits = []
    notable = []  # non-requestOutOfRange NRCs
    last_entry = None
    first_entry = None

    for e in entries:
        if first_entry is None:
            first_entry = e
        last_entry = e

        if e["category"] == "hit":
            hits.append(e)
        elif e["category"] == "nrc":
            nrc_byte, desc = e["detail"]
            nrc_counts[(nrc_byte, desc)] += 1
            if nrc_byte != 0x31:  # not requestOutOfRange — notable
                notable.append(e)
        elif e["category"] == "nodata":
            nrc_counts[("nodata", "NO DATA")] += 1

    if not hits_only:
        print(f"  Total probes:  {len(entries)}")
        if first_entry:
            req = first_entry["request"]
            rid = req[4:8] if len(req) >= 8 else "?"
            print(f"  First RID:     0x{rid}  ({first_entry['ts']})")
        if last_entry:
            req = last_entry["request"]
            rid = req[4:8] if len(req) >= 8 else "?"
            print(f"  Last RID:      0x{rid}  ({last_entry['ts']})  ← scan cut off here")

        print()
        print("  Response breakdown:")
        for (k, desc), count in sorted(nrc_counts.items(), key=lambda x: -x[1]):
            if k == "nodata":
                print(f"    NO DATA                          {count:6d}")
            else:
                print(f"    NRC 0x{k:02X} {desc:<30s} {count:6d}")

        if notable:
            print()
            print("  Notable (non-requestOutOfRange) responses:")
            for e in notable[:50]:
                req = e["request"]
                rid = f"0x{req[4:8]}" if len(req) >= 8 else req
                nrc_byte, desc = e["detail"]
                print(f"    RID {rid}  →  NRC 0x{nrc_byte:02X} ({desc})  [{e['ts']}]")
            if len(notable) > 50:
                print(f"    ... and {len(notable) - 50} more")

    print()
    if hits:
        print(f"  *** {len(hits)} HIT(S) FOUND ***")
        for e in hits:
            req = e["request"]
            rid = f"0x{req[4:8]}" if len(req) >= 8 else req
            print(f"    RID {rid}  →  {e['response']}  [{e['ts']}]")
    else:
        print("  No positive responses found.")


def main():
    parser = argparse.ArgumentParser(description="Parse ECU scan log files.")
    parser.add_argument("logfile", help="Path to log file (e.g. logs/ecu/BCM-2026-04-20.log)")
    parser.add_argument("--service", default="31",
                        help="Service byte prefix to filter on (hex, default: 31)")
    parser.add_argument("--hits-only", action="store_true",
                        help="Only print positive responses")
    args = parser.parse_args()

    print(f"Parsing: {args.logfile}  (service filter: 0x{args.service.upper()})")
    print()

    entries = parse_log(args.logfile, service_filter=args.service)

    if not entries:
        print("  No matching entries found.")
        return

    summarize(entries, hits_only=args.hits_only)


if __name__ == "__main__":
    main()
