# Case studies

Real, worked reverse-engineering hunts on canair — narrated end-to-end, including
the wrong turns. Where the [Bring your own car](../bring-your-own-car/overview.md)
journey and the [Analyze](../bring-your-own-car/06-analyze.md) page teach the
*method*, these show the method applied to a stubborn signal — what went wrong,
what finally worked, and what it taught us about the tooling.

- **[Finding the hidden AC input voltage](ac-input-voltage.md)** — a charger
  voltage written off as "not exposed", hidden by an inlet IR-drop confound, split
  across a "garbage" byte and an "ignored constant", and invisible to every
  current-anchored tool. How it was cracked, and the tooling gaps it exposed.
