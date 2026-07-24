# 8. Share

You've got named, verified parameters for your car. Two ways to put them to use:
push them to your WiCAN dongle as an AutoPID profile, and/or contribute the
profile so others with the same vehicle benefit.

## Generate the WiCAN AutoPID JSON

`canair wican autopid write` renders your profile's parameters into the JSON
format the WiCAN's AutoPID feature consumes, written to the bundle's
`out/autopid.json`:

```bash
canair wican autopid write
canair wican autopid write --verified-only   # ship only what you've confirmed
```

On a **WiCAN Pro**, you can sync it to the device directly (these are Pro-only):

```bash
canair wican autopid diff      # what would change on the device
canair wican autopid upload    # push the profile to the dongle
```

`out/*.json` is generated — never hand-edit it; regenerate.

## Contribute the profile

A finished profile is a directory others can drop in and use. To share it:

- **Keep it clean.** `ecus/` is the source of truth; `captures/` are your raw
  data (useful evidence, but large). Decide whether to include captures.
- **Validate first.** `canair validate all` must pass — including the
  duplicate-signal-name check.
- **Verify what you claim.** Prefer `--verified` parameters with a `--source`
  recording your evidence. An unverified guess should say so.
- **Note the vehicle precisely.** `car_model` should pin down model/year/market/
  battery so someone can tell if it matches theirs.

## Keep going

A profile is never really "done" — there are always more unmapped bytes. Use
`canair research --summary` to see your open backlog, `canair coverage` to find
undecoded bytes, and loop back through [capture](05-capture.md) →
[analyze](06-analyze.md) → [define](07-define-and-verify.md) whenever you want to
decode more.

---

← Back to the **[overview](overview.md)**.
