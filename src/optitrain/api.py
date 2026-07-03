"""
HTTP client for the HAFAS transport REST API (v5.db.transport.rest).

Implements the public endpoints documented at
https://github.com/public-transport/hafas-rest-api
"""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = os.environ.get("OPTITRAIN_API_BASE", "https://v5.db.transport.rest")
USER_AGENT = os.environ.get("OPTITRAIN_USER_AGENT", "optitrain-mcp/0.1.0")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=30.0,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Station endpoints
# ---------------------------------------------------------------------------


async def search_stations(
    query: str, results: int = 10
) -> list[dict[str, Any]]:
    """Search stations by name keyword."""
    client = _get_client()
    resp = await client.get(
        "/stations", params={"query": query, "results": min(results, 50)}
    )
    resp.raise_for_status()
    return resp.json()


async def get_station(id: str) -> dict[str, Any]:
    """Get station details by ID (EVA number)."""
    client = _get_client()
    resp = await client.get(f"/stops/{id}")
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Journey endpoints
# ---------------------------------------------------------------------------


async def get_journeys(
    from_id: str,
    to_id: str,
    departure: str | None = None,
    arrival: str | None = None,
    results: int = 5,
    products: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Search journeys between two stations."""
    client = _get_client()
    params: dict[str, str] = {
        "from": from_id,
        "to": to_id,
        "results": str(min(results, 20)),
    }
    if departure:
        params["departure"] = departure
    if arrival:
        params["arrival"] = arrival
    if products:
        for k, v in products.items():
            params[f"products[{k}]"] = str(v).lower()

    resp = await client.get("/journeys", params=params)
    resp.raise_for_status()
    return resp.json()


async def refresh_journey(
    journey_id: str, stopovers: bool = True
) -> dict[str, Any]:
    """Get fresh data (prices, status) for a previously returned journey."""
    client = _get_client()
    params = {}
    if stopovers:
        params["stopovers"] = "true"
    resp = await client.get(f"/journeys/{journey_id}", params=params)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class HafasApiError(Exception):
    """Raised when the HAFAS API request fails."""
