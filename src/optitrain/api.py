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

# httpx imported lazily in async functions to keep sync probes working without extra deps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend configuration
# ---------------------------------------------------------------------------

# Prefer community-hosted, no-auth wrappers first (v6/v5/db-rest/transport.rest)
BACKENDS = {
    "v6": {
        "url": "https://v6.db.transport.rest",
        "description": "Community HAFAS REST API (v6) - no auth",
        "status": "unknown",
        "probe_paths": ["/stations", "/locations", "/journeys"],
    },
    "v5": {
        "url": "https://v5.db.transport.rest",
        "description": "Community HAFAS REST API (v5) - no auth",
        "status": "unknown",
        "probe_paths": ["/stations", "/locations", "/journeys"],
    },
    "transport_rest": {
        "url": "https://transport.rest",
        "description": "transport.rest community wrapper",
        "status": "unknown",
        "probe_paths": ["/stations", "/locations", "/journeys"],
    },
}


def probe_backends(timeout=5):
    """Probe configured BACKENDS. Return dict {name: (ok, status_code, reason)}.

    Use stdlib urllib to avoid extra deps. Keep short, non-raising.
    """
    import urllib.request
    import urllib.error

    results = {}
    for name, cfg in BACKENDS.items():
        base = cfg.get("url")
        paths = cfg.get("probe_paths", ["/stations"])
        ok = False
        last_status = (False, None, "no-probe")
        for p in paths:
            url = base.rstrip("/") + p if p.startswith("/") else base.rstrip("/") + "/" + p
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "optitrain-mcp/0.1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    code = resp.getcode()
                    if 200 <= code < 400:
                        ok = True
                        last_status = (True, code, "ok")
                        break
                    else:
                        last_status = (False, code, resp.reason if hasattr(resp, "reason") else "http")
            except urllib.error.HTTPError as he:
                last_status = (False, he.code, str(he))
            except Exception as e:
                last_status = (False, None, str(e))
        results[name] = last_status
    return results


class TransportClient:
    """Minimal client for community HAFAS wrappers. No-auth first.

    Methods: select_backend(), get_departures(station_id, results), get_journeys(from,to)
    """

    def __init__(self, backends=None, timeout=8):
        self.backends = backends or BACKENDS
        self.timeout = timeout
        self.base = None

    def select_backend(self):
        """Probe backends, pick first healthy.

        Returns chosen base URL or raises RuntimeError.
        """
        res = probe_backends(timeout=self.timeout)
        for name, stat in res.items():
            ok, code, reason = stat
            if ok:
                self.base = self.backends[name]["url"].rstrip("/")
                return self.base
        # no healthy backend
        raise RuntimeError(f"no healthy community backend: {res}")

    def _http_get(self, path, params=None):
        import urllib.request, urllib.parse, json

        if not self.base:
            self.select_backend()
        url = self.base + path if path.startswith("/") else self.base + "/" + path
        if params:
            qs = urllib.parse.urlencode(params)
            url = url + ("?" + qs)
        req = urllib.request.Request(url, headers={"User-Agent": "optitrain-mcp/0.1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except Exception as e:
            # return structured error to caller
            return {"error": str(e)}

    def get_departures(self, station_id, results=5):
        path = f"/stops/{station_id}/departures"
        return self._http_get(path, params={"results": results})

    def get_journeys(self, from_id, to_id, results=3):
        path = f"/journeys"
        return self._http_get(path, params={"from": from_id, "to": to_id, "results": results})


__all__ = ["BACKENDS", "probe_backends", "TransportClient"]

# simple in-memory circuit-breaker + cache (process-local)
_backend_failures: dict[str, dict] = {}
_FAIL_THRESHOLD = 3
_COOLDOWN_SECONDS = 60

# stations cache: id -> (ts, data)
_stations_cache: dict[str, tuple[float, dict]] = {}
_STATIONS_TTL = 300
_SEARCH_TTL = 120
_search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _normalize_query(q: str) -> str:
    """Normalize search query for caching and matching.

    Steps: NFKD unicode normalize, strip diacritics, lowercase, collapse whitespace.
    """
    import unicodedata

    if not isinstance(q, str):
        q = str(q)
    s = unicodedata.normalize("NFKD", q)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    s = " ".join(s.split())
    return s


def mark_backend_failure(name: str) -> None:
    s = _backend_failures.setdefault(name, {"count": 0, "first": 0.0, "blocked_until": 0.0})
    now = asyncio.get_event_loop().time()
    if s["count"] == 0:
        s["first"] = now
    s["count"] += 1
    if s["count"] >= _FAIL_THRESHOLD:
        s["blocked_until"] = now + _COOLDOWN_SECONDS


def backend_is_blocked(name: str) -> bool:
    s = _backend_failures.get(name)
    if not s:
        return False
    now = asyncio.get_event_loop().time()
    if s.get("blocked_until", 0) > now:
        return True
    # reset after cooldown
    s["count"] = 0
    s["first"] = 0.0
    s["blocked_until"] = 0.0
    return False


def _get_transport_client_for_active():
    """Return TransportClient instance when active backend is community/no-auth and healthy."""
    if not _active_backend or _mock_mode:
        return None
    cfg = BACKENDS.get(_active_backend)
    if not cfg:
        return None
    # prefer community wrappers (no 'auth' key)
    if cfg.get("auth"):
        return None
    if backend_is_blocked(_active_backend):
        return None
    return TransportClient(backends=BACKENDS)

# Additional candidate backends (optional). Add credentials via ~/.config/optitrain/credentials.json
BACKENDS.update({
    "db_official": {
        "url": "https://api.deutschebahn.com/free1/departureBoard/v1",
        "description": "Deutsche Bahn official APIs (requires API key)",
        "status": "unknown",
        "auth": {"type": "api_key", "header": "Authorization", "prefix": "Bearer", "headers": ["DB-Client-Id", "DB-Api-Key"]},
        # probe paths specific for this API (include station-based departureBoard variant)
        "probe_paths": [
            "/departureBoard",
            "/departureBoard?station=8011160",
            "/v1/departureBoard",
            "/",
        ],
    },
    "transport_ch": {
        "url": "https://transport.opendata.ch/v1",
        "description": "Switzerland transport.opendata.ch (journeys, locations)",
        "status": "unknown",
        "probe_paths": ["/locations", "/locations?query=Bern"],
    },
    "navitia": {
        "url": "https://api.navitia.io/v1",
        "description": "Navitia multi-region transport API (api key)",
        "status": "unknown",
        "auth": {"type": "api_key", "header": "Authorization", "prefix": "Basic"},
        "probe_paths": ["/coverage", "/coverage?depth=0"],
    },
})

# The user can override via env var
CUSTOM_BACKEND = os.environ.get("OPTITRAIN_API_BASE")
if CUSTOM_BACKEND:
    BACKENDS["custom"] = {
        "url": CUSTOM_BACKEND,
        "description": f"Custom backend ({CUSTOM_BACKEND})",
        "status": "unknown",
    }

USER_AGENT = os.environ.get("OPTITRAIN_USER_AGENT", "optitrain-mcp/0.1.0")

# Credentials store path (simple, file-based). Manage via admin endpoint or dev CLI.
_CRED_PATH = os.path.expanduser("~/.config/optitrain/credentials.json")


def _load_credentials() -> dict:
    try:
        import json

        with open(_CRED_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Failed reading credentials file")
        return {}


def _save_credentials(creds: dict) -> None:
    import json, os, tempfile

    os.makedirs(os.path.dirname(_CRED_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_CRED_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(creds, f, indent=2)
        # atomic replace
        os.replace(tmp, _CRED_PATH)
        # secure permissions
        os.chmod(_CRED_PATH, 0o600)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------

from typing import Any

_clients: dict[str, Any] = {}
_active_backend: str | None = None
_mock_mode: bool = False


def _get_client(backend: str | None = None):
    """Get or create a client for a specific backend."""
    key = backend or next(iter(BACKENDS))
    if key not in _clients:
        cfg = BACKENDS[key]
        url = cfg["url"]
        headers = {"User-Agent": USER_AGENT}
        # Attach static auth headers if available in credentials
        creds = _load_credentials()
        auth_cfg = cfg.get("auth")
        if auth_cfg:
            # support single-header style (Authorization: Bearer <token>)
            # and multi-header style (e.g., DB-Client-Id, DB-Api-Key)
            keyname = creds.get(key) or creds.get(cfg.get("name"))
            # multi-header mapping
            hdrs = auth_cfg.get("headers")
            if hdrs and isinstance(keyname, dict):
                for h in hdrs:
                    v = keyname.get(h)
                    if v:
                        headers[h] = v
            else:
                token = None
                if isinstance(keyname, str):
                    token = keyname
                elif isinstance(keyname, dict):
                    token = keyname.get("token")
                if token:
                    hdr = auth_cfg.get("header", "Authorization")
                    prefix = auth_cfg.get("prefix")
                    headers[hdr] = f"{prefix} {token}" if prefix else token

        # import httpx here to avoid hard dependency for sync-only users
        try:
            import httpx

            _clients[key] = httpx.AsyncClient(
                base_url=url,
                timeout=15.0,
                headers=headers,
            )
        except Exception:
            # fallback to storing base info for sync probes/tests
            _clients[key] = {"base_url": url, "headers": headers}
    return _clients[key]


async def _refresh_token(backend: str) -> bool:
    """Attempt provider-specific token refresh if credentials include refresh data.

    Expected creds format for refreshable backends:
      {"backend": {"token":"...","refresh_token":"...","refresh_url":"..."}}
    This is best-effort. Returns True if token updated.
    """
    creds = _load_credentials()
    data = creds.get(backend)
    if not isinstance(data, dict):
        return False
    refresh_token = data.get("refresh_token")
    refresh_url = data.get("refresh_url")
    if not refresh_token or not refresh_url:
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(refresh_url, json={"refresh_token": refresh_token})
            if resp.status_code == 200:
                j = resp.json()
                new_token = j.get("access_token") or j.get("token")
                if new_token:
                    data["token"] = new_token
                    creds[backend] = data
                    _save_credentials(creds)
                    return True
    except Exception:
        logger.exception("token refresh failed for %s", backend)
    return False


async def _do_request(backend: str, method: str, path: str, **kwargs):
    """Perform request with one retry on 401 after attempting token refresh."""
    client = _get_client(backend)
    try:
        # client may be httpx.AsyncClient or a fallback dict
        if hasattr(client, "request"):
            resp = await client.request(method, path, **kwargs)
        else:
            # sync fallback using urllib
            import urllib.request, urllib.parse, json

            base = client.get("base_url")
            url = base.rstrip("/") + (path if path.startswith("/") else "/" + path)
            data = None
            if kwargs.get("params"):
                qs = urllib.parse.urlencode(kwargs.get("params"))
                url = url + "?" + qs
            req = urllib.request.Request(url, headers=client.get("headers", {}))
            with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 10)) as r:
                class Dummy:
                    def __init__(self, code, txt):
                        self.status_code = code
                        self._txt = txt

                    def json(self):
                        return json.loads(self._txt)

                raw = r.read()
                resp = Dummy(200, raw.decode("utf-8"))
    except Exception:
        raise

    if getattr(resp, "status_code", None) == 401:
        # try refresh
        ok = await _refresh_token(backend)
        if ok:
            # rebuild client to pick up new headers
            try:
                await _clients[backend].aclose()
            except Exception:
                pass
            _clients.pop(backend, None)
            client = _get_client(backend)
            if hasattr(client, "request"):
                resp = await client.request(method, path, **kwargs)
            else:
                # fallback sync path
                resp = await _do_request(backend, method, path, **kwargs)
    return resp


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
        import httpx
        t0 = asyncio.get_event_loop().time()
        # prefer serverInfo or stations endpoint; fall back to root
        probe_paths = ["/serverInfo", "/stations", "/departures", "/journeys"]
        resp = None
        for p in probe_paths:
            try:
                resp = await client.get(p, params={"query": "Berlin", "results": 1}, timeout=8.0)
                if resp.status_code < 500:
                    break
            except Exception:
                resp = None
                continue
        latency = (asyncio.get_event_loop().time() - t0) * 1000
        if resp is not None and resp.status_code == 200:
            result["reachable"] = True
            result["latency_ms"] = round(latency)
        elif resp is not None:
            result["error"] = f"HTTP {resp.status_code}"
        else:
            result["error"] = "no response"
    except Exception as e:
        # httpx may not be available; collapse to generic error
        result["error"] = str(e)
    except Exception as e:
        result["error"] = str(e)

    cfg["status"] = "ok" if result["reachable"] else result["error"]
    return result


async def health_check() -> dict[str, Any]:
    """Check all backends, set active backend + mock mode."""
    global _active_backend, _mock_mode
    # Use sync probe to avoid requiring httpx for health check.
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, probe_backends, 5)

    # pick first healthy backend by insertion order
    chosen = None
    for name, stat in results.items():
        ok, code, reason = stat
        if ok:
            chosen = name
            break

    if chosen:
        _active_backend = chosen
        _mock_mode = False
        logger.info("Active backend: %s (%s)", chosen, BACKENDS[chosen]["url"])
        return {"status": "connected", "active_backend": chosen, "backends": results}

    _active_backend = None
    _mock_mode = True
    logger.warning("No API backend reachable — running in mock mode")
    return {"status": "mock", "active_backend": None, "backends": results, "note": "No API backend reachable. Using mock data for demo."}


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
    # normalize station name for robust matching (strip diacritics, lowercase, collapse whitespace)
    name_norm = _normalize_query(sdata["name"])  # type: ignore
    KNOWN_STATIONS_BY_NAME[name_norm] = sid


def search_known_stations(query: str, results: int = 10) -> list[dict[str, Any]]:
    q = _normalize_query(query)
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
    # cache by exact query
    now = asyncio.get_event_loop().time()
    nq = _normalize_query(query)
    cache_entry = _search_cache.get(nq)
    if cache_entry:
        ts, data = cache_entry
        if now - ts < _SEARCH_TTL:
            return data[:results]

    # try community wrapper via TransportClient when available
    tc = _get_transport_client_for_active()
    if tc:
        try:
            data = tc._http_get("/stations", params={"query": query, "results": min(results, 50)})
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data.get("error"))
            if isinstance(data, list):
                _search_cache[nq] = (now, data)
            return data
        except Exception as exc:
            logger.warning("TransportClient search_stations failed, falling back: %s", exc)

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
    # cache
    now = asyncio.get_event_loop().time()
    cached = _stations_cache.get(id)
    if cached:
        ts, data = cached
        if now - ts < _STATIONS_TTL:
            return data

    if not _mock_mode and _active_backend:
        try:
            tc = _get_transport_client_for_active()
            if tc:
                # try community wrapper
                d = tc.get_departures(id, results=1)
                if isinstance(d, dict) and d.get("error"):
                    raise RuntimeError(d.get("error"))
                # adapt wrapper response to station details when possible
                out = {"id": id, "departures_sample": d}
                _stations_cache[id] = (now, out)
                return out
            client = _get_client(_active_backend)
            resp = await client.get(f"/stops/{id}", timeout=10.0)
            resp.raise_for_status()
            out = resp.json()
            _stations_cache[id] = (now, out)
            return out
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
            tc = _get_transport_client_for_active()
            if tc:
                data = tc.get_journeys(from_id, to_id, results=min(results, 10))
                if isinstance(data, dict) and data.get("error"):
                    raise RuntimeError(data.get("error"))
                return data

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
