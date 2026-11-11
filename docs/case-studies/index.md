# Case studies

These are real reverse-engineering hunts that were done with canair. Each page tells the whole
story, including the wrong turns. The [Bring your own car](../bring-your-own-car/overview.md)
journey and the [Analyze](../bring-your-own-car/06-analyze.md) page teach the *method*. A case
study shows the method on a stubborn signal. It shows what went wrong, what finally worked and
what the hunt taught us about the tooling.

- **[Finding the hidden AC input voltage](ac-input-voltage.md)** — The notes wrote off this
  alternating current (AC) charger voltage as "not exposed". Three things hid it:

    - An inlet voltage drop (IR drop) confounded the value.
    - The value was split across a "garbage" byte and an "ignored constant".
    - Every current-anchored tool was blind to it.

    The page shows how the hunt cracked it and which tooling gaps it exposed.
