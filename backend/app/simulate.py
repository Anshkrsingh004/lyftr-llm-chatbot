"""Parsing for the X-Simulate-Failure test hook.

Header format: comma-separated ``<key_id>=<status>`` pairs. ``*`` targets every
key. Allowed statuses: 429, 500, 502, 503. Only honored when failure simulation
is enabled on the deployment (ENABLE_FAILURE_SIMULATION=true).
"""
from typing import Dict, Optional

ALLOWED_STATUSES = {429, 500, 502, 503}


def parse_sim_header(header_value: Optional[str], enabled: bool) -> Dict[str, int]:
    """Return a mapping of key_id -> simulated HTTP status.

    Returns an empty dict when simulation is disabled, the header is missing, or
    nothing parseable is found. ``*`` is kept as a literal key meaning "all keys".
    """
    if not enabled or not header_value:
        return {}

    result: Dict[str, int] = {}
    for pair in header_value.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        raw_key, raw_status = pair.split("=", 1)
        key = raw_key.strip()
        try:
            status = int(raw_status.strip())
        except ValueError:
            continue
        if not key or status not in ALLOWED_STATUSES:
            continue
        result[key] = status
    return result
