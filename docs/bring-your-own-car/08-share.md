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

## Contribute your profile back

**Please do — this is the single most valuable thing you can do for the
project.** 🎉 Every profile you contribute means the next person with your car
gets a head start instead of starting from zero — that's how canair becomes
useful beyond one vehicle. Contributions are genuinely wanted and warmly
welcomed, whether it's a whole new car, a handful of newly-decoded parameters, or
a fix to an existing one.

**How to contribute** (a standard GitHub pull request):

1. Fork [`philipkocanda/canair`](https://github.com/philipkocanda/canair) and
   put your profile under `profiles/<your-car>/` (see
   [step 1](01-create-profile.md) — create it there with `--path`).
2. Make sure it's clean and honest:
   - **Validate:** `canair validate all` must pass (including the
     duplicate-signal-name check).
   - **Verify what you claim.** Prefer `--verified` parameters with a `--source`
     recording your evidence; an unverified guess should say so.
   - **Name the vehicle precisely.** `car_model` should pin down
     model/year/market/battery so someone can tell if it matches theirs.
   - **Decide on `captures/`.** They're great evidence but large — include a
     representative subset rather than everything if size is a concern.
3. Open a PR. Even a *partial* profile is welcome — a few verified signals beats
   nothing, and others can build on it.

Not ready for a full profile? **Individual PID/parameter contributions are just
as valuable** — a single verified signal, a corrected offset, or a new
`research:` lead all help. And if you find a bug or a rough edge in canair
itself, an issue or PR is appreciated too.

## Keep going

A profile is never really "done" — there are always more unmapped bytes. Use
`canair research --summary` to see your open backlog, `canair coverage` to find
undecoded bytes, and loop back through [capture](05-capture.md) →
[analyze](06-analyze.md) → [define](07-define-and-verify.md) whenever you want to
decode more.

---

← Back to the **[overview](overview.md)**.
