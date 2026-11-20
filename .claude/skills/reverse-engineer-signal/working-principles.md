# Working principles: systems-engineer mindset and notes style

Scope: the ECU-engineer mindset and the note-writing style for profile files. The evidence and
`verified: true` rules stay in `.claude/skills/reverse-engineer-signal/SKILL.md`.

- **Put yourself in the shoes of the ECU's systems engineer.** Before looking at
  bytes, ask what this module must measure, report and control: a BMS engineer thinks
  cell voltages, pack current, temperatures, SOC, contactors, isolation resistance; an
  ESC engineer thinks wheel speeds, yaw rate, accelerations, brake pressure. Signals
  cluster by the ECU's job, come in physically sensible units and ranges, sit in
  orderly blocks (four wheel speeds in a row), and are scaled to fit their field
  width. A decode no real systems engineer would design is probably wrong.

- **Notes are technical records, not prose**: byte offset, observed range, per-state
  values, the key evidence, the decision and why — then stop. Speculation gets one line
  at most. These files are read repeatedly and grow forever.
