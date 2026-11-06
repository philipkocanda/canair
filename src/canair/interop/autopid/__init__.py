"""The WiCAN AutoPID profile: shipping rules, the v4 and v6 renderers, send-to topics.

``canair wican autopid write`` makes one file for each firmware generation (``autopid-v4.json``,
``autopid-v6.json``). Both renderers take their shipping decision from
``profile.ship_profile``, so they cannot disagree on what ships, and both ship verified signals
only unless ``--include-unverified``. The v6 renderer refuses a value the firmware's fixed
buffers would cut short and never truncates it. A PID's proven ``response_frames`` count is
appended to its request unless ``--no-expected-responses``; the rules for that are in
``docs/development/frame-counts.md``. A signal with ``send_to: TOPIC`` streams to that MQTT
topic on every poll, because the batch snapshot's ``cycle`` is device config that canair does
not manage: a faster ``period`` alone never reaches the broker faster.

Belongs: turning a profile's verified signals into the JSON a WiCAN loads (``profile``,
``v6``), the MQTT topic rule (``send_to``) and the signal-category to AutoPID class map
(``classes``), and the signal-level diff of two profiles (``diff``).
Not here: uploading to the device (``device/wican``, ``cli/commands/wican/``).
Imports: ``ecus``, ``privacy``, ``transport`` (the request framing it must match) and ``uds``.
"""
