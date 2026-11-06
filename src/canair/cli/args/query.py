from canair.profile import active

"""The QUERY mini-language as the analysis commands take it: ECU aliases resolved."""


def parse_canonical_query(query):
    """Parse a QUERY and canonicalize selector ECUs (aliases -> primary name).

    So `canair captures SMK` resolves to the SKM module. Falls back to the raw
    parse if the ECU registry is unavailable; :class:`EcuNameCollision` from an
    ambiguous registry is allowed to propagate.
    """
    from canair.query import parse_query

    q = parse_query(query)
    try:
        from canair.ecus.names import build_canonical_name_index

        name_index = build_canonical_name_index(profile=active())
    except FileNotFoundError:
        return q
    return q.canonicalize_ecus(lambda ecu: name_index.get(ecu, ecu).upper())
