"""
MCP resource handlers for OptiTrain.

URIs:
  bahn://stations/{id}    → station data as JSON
  bahn://journeys/{from}/{to}/{when} → journey data as JSON
"""

import json
import logging
import re

import mcp.types as types

from optitrain import api

logger = logging.getLogger(__name__)

STATION_RE = re.compile(r"^bahn://stations/(.+)$")
JOURNEY_RE = re.compile(r"^bahn://journeys/([^/]+)/([^/]+)/(.+)$")


async def list_resources() -> list[types.Resource]:
    """Return advertised resources (dynamic — top stations)."""
    # A curated set of major German stations as starting points
    top_stations = [
        ("8011160", "Berlin Hbf"),
        ("8002549", "Hamburg Hbf"),
        ("8000105", "Frankfurt (Main) Hbf"),
        ("8000261", "München Hbf"),
        ("8000096", "Stuttgart Hbf"),
        ("8000207", "Köln Hbf"),
        ("8000152", "Hannover Hbf"),
        ("8000244", "Mannheim Hbf"),
    ]
    resources = []
    for sid, sname in top_stations:
        resources.append(
            types.Resource(
                uri=f"bahn://stations/{sid}",
                name=f"{sname} — Station",
                description=f"Details for {sname}",
                mimeType="application/json",
            )
        )
    return resources


async def read_resource(uri: str) -> str:
    uri_str = str(uri)

    m = STATION_RE.match(uri_str)
    if m:
        data = await api.get_station(m.group(1))
        return json.dumps(data, indent=2, default=str)

    m = JOURNEY_RE.match(uri_str)
    if m:
        from_id, to_id, when = m.group(1), m.group(2), m.group(3)
        data = await api.get_journeys(from_id, to_id, departure=when)
        return json.dumps(data, indent=2, default=str)

    raise ValueError(f"Unknown resource URI: {uri_str}")
