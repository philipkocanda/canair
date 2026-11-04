"""What a decoded value may physically be: bands, grid regions and unit guessing.

Belongs: the plausibility bands behind ``hunt --physical`` (``bands``), the mains-voltage and
line-frequency presets per grid region (``grid_regions``) and the slope-to-unit sniffer
(``unit_guess``).
Not here: asking the user which grid they charge on (``cli``) or reading the config key.
Imports: ``core`` only.
"""
