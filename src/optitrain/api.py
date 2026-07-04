"""
HTTP client for the HAFAS transport REST API with multi-backend fallback.

Primary: v5.db.transport.rest (community HAFAS wrapper for DB)
Fallback: known station DB + mock pricing data for offline demo
"""

import asyncio
import logging
import os
from datetime import date, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend configuration
# ---------------------------------------------------------------------------

BACKENDS = {
    "v5": {
        "url": "https://v5.db.transport.rest",
        "description": "Community HAFAS REST API (DB profile)",
        "status": "unknown",
    },
}

# The user can override via env var
CUSTOM_BACKEND = os.environ.get("OPTITRAIN_API_BASE")
if CUSTOM_BACKEND:
    BACKENDS["custom"] = {
        "url": CUSTOM_BACKEND,
        "description": f"Custom backend ({CUSTOM_BACKEND})",
        "status": "unknown",
    }

USER_AGENT = os.environ.get("OPTITRAIN_USER_AGENT", "optitrain-mcp/0.1.0")

# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------

_clients: dict[str, httpx.AsyncClient] = {}
_active_backend: str | None = None
_mock_mode: bool = False


def _get_client(backend: str | None = None) -> httpx.AsyncClient:
    """Get or create a client for a specific backend."""
    key = backend or next(iter(BACKENDS))
    if key not in _clients:
        url = BACKENDS[key]["url"]
        _clients[key] = httpx.AsyncClient(
            base_url=url,
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
        )
    return _clients[key]


async def close() -> None:
    for c in _clients.values():
        await c.aclose()
    _clients.clear()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


async def check_backend(name: str) -> dict[str, Any]:
    """Test if a backend is reachable and responsive."""
    cfg = BACKENDS[name]
    client = _get_client(name)
    result = {"name": name, "url": cfg["url"], "reachable": False, "latency_ms": None, "error": None}
    try:
        t0 = asyncio.get_event_loop().time()
        resp = await client.get("/stations", params={"query": "Berlin", "results": 1}, timeout=8.0)
        latency = (asyncio.get_event_loop().time() - t0) * 1000
        if resp.status_code == 200:
            result["reachable"] = True
            result["latency_ms"] = round(latency)
        else:
            result["error"] = f"HTTP {resp.status_code}"
    except httpx.TimeoutException:
        result["error"] = "timeout (8s)"
    except httpx.ConnectError as e:
        result["error"] = f"connection failed: {e}"
    except Exception as e:
        result["error"] = str(e)

    cfg["status"] = "ok" if result["reachable"] else result["error"]
    return result


async def health_check() -> dict[str, Any]:
    """Check all backends, set active backend + mock mode."""
    global _active_backend, _mock_mode
    results = {}
    for name in BACKENDS:
        results[name] = await check_backend(name)

    # Pick first reachable backend
    for name, r in results.items():
        if r["reachable"]:
            _active_backend = name
            _mock_mode = False
            logger.info("Active backend: %s (%s) — %.0fms", name, BACKENDS[name]["url"], r["latency_ms"])
            return {
                "status": "connected",
                "active_backend": name,
                "backends": results,
            }

    # No backend reachable — enable mock mode
    _active_backend = None
    _mock_mode = True
    logger.warning("No API backend reachable — running in mock mode")
    return {
        "status": "mock",
        "active_backend": None,
        "backends": results,
        "note": "No API backend reachable. Using mock data for demo.",
    }


def is_mock() -> bool:
    return _mock_mode


def get_active_backend() -> str | None:
    return _active_backend


# ---------------------------------------------------------------------------
# Known stations (EVA numbers) — always available, no API needed
# ---------------------------------------------------------------------------

KNOWN_STATIONS: dict[str, dict[str, str | float | list[str]]] = {
    "8011160": {"name": "Berlin Hbf", "lat": 52.5251, "lon": 13.3695, "products": ["national", "regional", "suburban"]},
    "8002549": {"name": "Hamburg Hbf", "lat": 53.5527, "lon": 9.9897, "products": ["national", "regional", "suburban"]},
    "8000105": {"name": "Frankfurt (Main) Hbf", "lat": 50.1071, "lon": 8.6637, "products": ["national", "regional", "suburban"]},
    "8000261": {"name": "München Hbf", "lat": 48.1402, "lon": 11.5581, "products": ["national", "regional", "suburban"]},
    "8000096": {"name": "Stuttgart Hbf", "lat": 48.7837, "lon": 9.1813, "products": ["national", "regional"]},
    "8000207": {"name": "Köln Hbf", "lat": 50.9429, "lon": 6.9581, "products": ["national", "regional"]},
    "8000152": {"name": "Hannover Hbf", "lat": 52.3767, "lon": 9.7416, "products": ["national", "regional"]},
    "8000244": {"name": "Mannheim Hbf", "lat": 49.4791, "lon": 8.4697, "products": ["national", "regional"]},
    "8010205": {"name": "Leipzig Hbf", "lat": 51.3451, "lon": 12.3821, "products": ["national", "regional"]},
    "8000284": {"name": "Nürnberg Hbf", "lat": 49.4462, "lon": 11.0825, "products": ["national", "regional"]},
    "8000199": {"name": "Kassel-Wilhelmshöhe", "lat": 51.3126, "lon": 9.4451, "products": ["national", "regional"]},
    "8000080": {"name": "Dortmund Hbf", "lat": 51.5178, "lon": 7.4592, "products": ["national", "regional"]},
    "8000098": {"name": "Essen Hbf", "lat": 51.4515, "lon": 7.0137, "products": ["national", "regional"]},
    "8000050": {"name": "Bremen Hbf", "lat": 53.0836, "lon": 8.8136, "products": ["national", "regional"]},
    "8000107": {"name": "Freiburg (Breisgau) Hbf", "lat": 47.9975, "lon": 7.8419, "products": ["national", "regional"]},
    "8010085": {"name": "Dresden Hbf", "lat": 51.0407, "lon": 13.7316, "products": ["national", "regional"]},
    "8010097": {"name": "Erfurt Hbf", "lat": 50.9720, "lon": 11.0368, "products": ["national", "regional"]},
    "8500010": {"name": "Basel SBB", "lat": 47.5475, "lon": 7.5899, "products": ["national", "regional"]},
    "8000086": {"name": "Duisburg Hbf", "lat": 51.4297, "lon": 6.7606, "products": ["national", "regional"]},
}
KNOWN_STATIONS_BY_NAME: dict[str, str] = {}
for sid, sdata in KNOWN_STATIONS.items():
    name_lower = sdata["name"].lower()  # type: ignore
    KNOWN_STATIONS_BY_NAME[name_lower] = sid


def search_known_stations(query: str, results: int = 10) -> list[dict[str, Any]]:
    q = query.lower()
    matches = []
    seen: set[str] = set()
    # Match by name
    for name_lower, sid in KNOWN_STATIONS_BY_NAME.items():
        if q in name_lower:
            seen.add(sid)
            s = KNOWN_STATIONS[sid]
            matches.append({
                "id": sid,
                "name": s["name"],
                "type": "station",
                "location": {"latitude": s["lat"], "longitude": s["lon"]},
                "products": s["products"],
            })
    # Match by ID prefix
    for sid, s in KNOWN_STATIONS.items():
        if sid not in seen and q in sid.lower():
            matches.append({
                "id": sid,
                "name": s["name"],
                "type": "station",
                "location": {"latitude": s["lat"], "longitude": s["lon"]},
                "products": s["products"],
            })
    matches.sort(key=lambda x: x["name"])
    return matches[:results]


# ---------------------------------------------------------------------------
# Station endpoints
# ---------------------------------------------------------------------------


async def search_stations(query: str, results: int = 10) -> list[dict[str, Any]]:
    """Search stations by name keyword. Falls back to known station DB."""
    if not _mock_mode and _active_backend:
        try:
            client = _get_client(_active_backend)
            resp = await client.get(
                "/stations", params={"query": query, "results": min(results, 50)}, timeout=10.0
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("API search_stations failed, falling back: %s", exc)

    return search_known_stations(query, results)


async def get_station(id: str) -> dict[str, Any]:
    """Get station details by ID (EVA number)."""
    # Check known stations first (fast, no API needed)
    if id in KNOWN_STATIONS:
        s = KNOWN_STATIONS[id]
        return {
            "id": id,
            "name": s["name"],
            "type": "station",
            "location": {"latitude": s["lat"], "longitude": s["lon"]},
            "products": s["products"],
        }

    if not _mock_mode and _active_backend:
        try:
            client = _get_client(_active_backend)
            resp = await client.get(f"/stops/{id}", timeout=10.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("API get_station failed, using fallback: %s", exc)

    return {"id": id, "name": f"Station {id}", "type": "station", "error": "Details unavailable in mock mode"}


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
    if not _mock_mode and _active_backend:
        try:
            client = _get_client(_active_backend)
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

            resp = await client.get("/journeys", params=params, timeout=10.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("API get_journeys failed, using mock: %s", exc)

    return _mock_journeys(from_id, to_id, departure, results)


def _mock_journeys(
    from_id: str, to_id: str, departure: str | None, results: int = 5
) -> dict[str, Any]:
    from_name = KNOWN_STATIONS.get(from_id, {}).get("name", f"Station {from_id}")
    to_name = KNOWN_STATIONS.get(to_id, {}).get("name", f"Station {to_id}")

    base_price = _estimate_price(from_id, to_id)

    now = datetime.now()

    dep_dt = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if departure:
        try:
            dep_dt = datetime.fromisoformat(departure)
        except ValueError:
            pass

    days_until = (dep_dt.date() - now.date()).days

    def _price_multiplier(days_out: int) -> float:
        if days_out >= 21:
            return 1.0
        elif days_out >= 14:
            return 1.25
        elif days_out >= 7:
            return 1.5
        elif days_out >= 3:
            return 1.8
        elif days_out >= 0:
            return 2.0
        else:
            return 2.5

    def _price_hint(days_out: int) -> str:
        if days_out >= 21:
            return "Sparpreis"
        elif days_out >= 14:
            return "Sparpreis (moderate)"
        elif days_out >= 7:
            return "Sparpreis (high)"
        elif days_out >= 3:
            return "Flexpreis"
        else:
            return "Flexpreis (last-minute)"

    multiplier = _price_multiplier(days_until)
    hint = _price_hint(days_until)

    mock_journeys = []
    for i in range(min(results, 5)):
        offset_h = i * 2
        journey_dep = dep_dt.replace(hour=(dep_dt.hour + offset_h) % 24, minute=15)
        journey_arr = journey_dep.replace(hour=(journey_dep.hour + 2 + i) % 24, minute=45)
        duration = int((journey_arr - journey_dep).total_seconds())
        if duration < 0:
            duration += 86400

        price_variation = base_price * multiplier * (1 + i * 0.05)
        mock_journeys.append({
            "type": "journey",
            "legs": [
                {
                    "origin": {"name": from_name, "id": from_id},
                    "destination": {"name": to_name, "id": to_id},
                    "departure": journey_dep.isoformat(),
                    "arrival": journey_arr.isoformat(),
                    "line": {
                        "product": "ICE",
                        "name": f"ICE {700 + i}",
                    },
                    "direction": to_name,
                }
            ],
            "price": {"amount": round(price_variation, 2), "currency": "EUR", "hint": hint},
            "duration": duration,
        })

    return {"journeys": mock_journeys, "mock": True}


def _estimate_price(from_id: str, to_id: str) -> float:
    """Simple distance-based price heuristic (mock mode)."""
    s1 = KNOWN_STATIONS.get(from_id)
    s2 = KNOWN_STATIONS.get(to_id)
    if not s1 or not s2:
        return 49.99
    # Rough distance-based: €0.30/km, min €10
    lat1, lon1 = s1["lat"], s1["lon"]  # type: ignore
    lat2, lon2 = s2["lat"], s2["lon"]  # type: ignore
    dist_km = ((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) ** 0.5 * 111
    return max(10.0, round(dist_km * 0.30, 2))


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class HafasApiError(Exception):
    """Raised when the HAFAS API request fails."""
