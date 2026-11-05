"""The examples shown under ``canair pids --help``."""

EPILOG = r"""
  # Record a decoded parameter
  canair pids upsert-param MCU 2102 MCU_MOTOR_TORQUE "[S12:S13]/100" \
      --unit Nm --min -300 --max 300 --unverified --source "Soul VMCU CSV" \
      --notes "signed 16-bit BE at B12:B13"

  # Track a new investigation, then close it out
  canair pids add-research MCU --type decode --target 2103 \
      --status captured --priority P2 --prereq charging --notes "27 bytes, zeros"
  canair pids set-status MCU 2103 done --type decode

  # Set an ECU's curated identity notes (comment/format-preserving)
  canair pids set-identity BMS notes "Battery Management System — see sessions:."
"""
