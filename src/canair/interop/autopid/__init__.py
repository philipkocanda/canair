"""The WiCAN AutoPID profile: shipping rules, the v4 and v6 renderers, send-to topics.

Belongs: turning a profile's verified signals into the JSON a WiCAN loads (``profile``,
``v6``), the MQTT topic rule (``send_to``) and the signal-category to AutoPID class map
(``classes``).
Not here: uploading to the device (``device/wican``, ``cli/commands/wican.py``).
Imports: ``ecus``, ``privacy``, ``transport`` (the request framing it must match) and ``uds``.
"""
