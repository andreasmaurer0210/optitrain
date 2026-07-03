"""
Predictive pricing strategies for Deutsche Bahn fares.

Implements:
  - 21-Day Threshold Rule  — cheapest booking window analysis
  - Split-Ticketing         — compare through-fare vs segment fares
  - Sparpreis vs Flexpreis  — price vs flexibility trade-off
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _best_price(data: dict[str, Any]) -> float | None:
    """Extract the cheapest available price from a journeys response."""
    journeys = data.get("journeys", [])
    prices = []
    for j in journeys:
        p = j.get("price") or {}
        amount = p.get("amount")
        if amount is not None:
            prices.append(float(amount))
    return min(prices) if prices else None


def _price_hints(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all available price+label combos from a journeys response."""
    hints = []
    for j in data.get("journeys", []):
        p = j.get("price") or {}
        amount = p.get("amount")
        hint = p.get("hint", "")
        if amount is not None:
            hints.append({"amount": float(amount), "hint": hint})
    return sorted(hints, key=lambda x: x["amount"])


def _format_price(amount: float | None) -> str:
    if amount is None:
        return "N/A"
    return f"{amount:.2f} EUR"


# ---------------------------------------------------------------------------
# 21-Day Threshold Rule
# ---------------------------------------------------------------------------


async def analyze_booking_window(
    from_id: str,
    to_id: str,
    travel_date: date,
    journey_fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    flexible_days: int = 3,
) -> dict[str, Any]:
    """
    Analyze how prices vary as booking date approaches travel date.

    DB Sparpreis pricing tiers typically align with a 21-day threshold:
      ≥21 days ahead → cheapest tier
      14–20 days     → moderate
      7–13 days      → expensive
      <7 days        → highest / sold out
    """
    today = date.today()
    days_until_travel = (travel_date - today).days

    thresholds = [21, 14, 7, 3]
    prices_by_window: dict[str, Any] = {}

    for days_before in thresholds:
        check_date = travel_date - timedelta(days=days_before)
        if check_date <= today:
            continue
        departure_str = f"{check_date}T08:00:00"
        try:
            data = await journey_fn(
                from_id, to_id, departure=departure_str, results=3
            )
            prices_by_window[f"{days_before}d_before"] = {
                "check_date": check_date.isoformat(),
                "best_price": _best_price(data),
                "price_hints": _price_hints(data),
            }
        except Exception as exc:
            logger.warning("Failed to check %d-day window: %s", days_before, exc)
            prices_by_window[f"{days_before}d_before"] = {
                "check_date": check_date.isoformat(),
                "error": str(exc),
            }

    # Current price (booking today)
    if days_until_travel >= 0:
        departure_str = f"{travel_date}T08:00:00"
        try:
            data = await journey_fn(
                from_id, to_id, departure=departure_str, results=3
            )
            prices_by_window["book_now"] = {
                "days_before_travel": days_until_travel,
                "best_price": _best_price(data),
                "price_hints": _price_hints(data),
            }
        except Exception as exc:
            prices_by_window["book_now"] = {"error": str(exc)}

    # Generate recommendation
    recommendation = _window_recommendation(
        prices_by_window, days_until_travel
    )

    return {
        "from": from_id,
        "to": to_id,
        "travel_date": travel_date.isoformat(),
        "days_until_travel": days_until_travel,
        "analysis_date": today.isoformat(),
        "prices_by_window": prices_by_window,
        "recommendation": recommendation,
    }


def _window_recommendation(
    prices: dict[str, Any], days_until: int
) -> str:
    """Generate human-readable booking advice."""
    now = prices.get("book_now")
    far = prices.get("21d_before")

    if days_until < 0:
        return "Travel date is in the past."
    if days_until >= 21 and far and far.get("best_price"):
        return (
            f"You are {days_until} days before travel — well within the cheap "
            f"booking window. Current price: {_format_price(now.get('best_price'))}. "
            f"Book now to lock the Sparpreis rate."
        )
    if days_until >= 14:
        return (
            f"You are {days_until} days before travel. "
            f"Prices may still be moderate but could rise. "
            f"Consider booking soon."
        )
    if days_until >= 7:
        current = now.get("best_price") if now else None
        return (
            f"You are {days_until} days before travel — late booking window. "
            f"Sparpreis may have increased. "
            f"Current best: {_format_price(current)}. "
            f"If this is acceptable, book now before further increases."
        )
    current = now.get("best_price") if now else None
    return (
        f"Last-minute booking ({days_until} days out). "
        f"Available: {_format_price(current)}. "
        f"Flexpreis may be the only option if Sparpreis is sold out."
    )


# ---------------------------------------------------------------------------
# Split-Ticketing
# ---------------------------------------------------------------------------

# Major ICE hubs useful as split points for long-distance routes.
_SPLIT_HUBS: dict[str, str] = {
    "Frankfurt (Main) Hbf": "8000105",
    "Hannover Hbf": "8000152",
    "Köln Hbf": "8000207",
    "Hamburg Hbf": "8002549",
    "München Hbf": "8000261",
    "Berlin Hbf": "8011160",
    "Stuttgart Hbf": "8000096",
    "Mannheim Hbf": "8000244",
    "Würzburg Hbf": "8000264",
    "Nürnberg Hbf": "8000284",
    "Kassel-Wilhelmshöhe": "8000199",
    "Leipzig Hbf": "8010205",
    "Dresden Hbf": "8010085",
    "Duisburg Hbf": "8000086",
    "Essen Hbf": "8000098",
    "Dortmund Hbf": "8000080",
    "Bremen Hbf": "8000050",
    "Freiburg (Breisgau) Hbf": "8000107",
    "Basel SBB": "8500010",
    "Erfurt Hbf": "8010097",
}


async def analyze_split_ticket(
    from_id: str,
    to_id: str,
    travel_date: str,
    journey_fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    station_fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
) -> dict[str, Any]:
    """
    Check whether splitting the ticket at an intermediate station saves money.

    Strategy: for each major ICE hub between origin and destination,
    query prices for both segments and compare with the through-fare.
    """
    # Get direct journey and its price
    try:
        direct_data = await journey_fn(from_id, to_id, departure=travel_date, results=5)
        direct_price = _best_price(direct_data)
    except Exception as exc:
        return {"error": f"Failed to fetch direct journey: {exc}"}

    if direct_price is None:
        return {"error": "Direct journey returned no price data."}

    direct_hints = _price_hints(direct_data)

    # Check each candidate split hub
    splits: list[dict[str, Any]] = []
    for hub_name, hub_id in _SPLIT_HUBS.items():
        if hub_id in (from_id, to_id):
            continue
        try:
            leg1 = await journey_fn(from_id, hub_id, departure=travel_date, results=3)
            leg1_price = _best_price(leg1)
            if leg1_price is None or leg1_price >= direct_price:
                continue  # no savings possible if one leg costs ≥ total

            # Approximate transfer time: assume 1h buffer, use arrival from leg1
            leg1_arrival = _leg_arrival(leg1)
            if not leg1_arrival:
                continue

            leg2 = await journey_fn(hub_id, to_id, departure=leg1_arrival, results=3)
            leg2_price = _best_price(leg2)
            if leg2_price is None:
                continue

            combined = leg1_price + leg2_price
            savings = direct_price - combined
            if savings > 0.50:  # only report meaningful savings (≥0.50 EUR)
                splits.append({
                    "split_hub": hub_name,
                    "split_hub_id": hub_id,
                    "leg1_price": leg1_price,
                    "leg1_detail": _price_hints(leg1),
                    "leg2_price": leg2_price,
                    "leg2_detail": _price_hints(leg2),
                    "combined_price": combined,
                    "direct_price": direct_price,
                    "savings": round(savings, 2),
                })
        except Exception as exc:
            logger.debug("Split check at %s failed: %s", hub_name, exc)
            continue

    splits.sort(key=lambda s: s["savings"], reverse=True)

    return {
        "from": from_id,
        "to": to_id,
        "travel_date": travel_date,
        "direct_price": direct_price,
        "direct_options": direct_hints[:3],
        "split_options": splits[:10],
        "best_savings": splits[0]["savings"] if splits else 0,
        "recommendation": _split_recommendation(direct_price, splits),
    }


def _leg_arrival(data: dict[str, Any]) -> str | None:
    """Extract arrival time of the first (fastest) journey's last leg."""
    journeys = data.get("journeys", [])
    if not journeys:
        return None
    legs = journeys[0].get("legs", [])
    if not legs:
        return None
    dest = legs[-1].get("destination", {})
    return dest.get("arrival") or dest.get("arrivalEstimated")


def _split_recommendation(
    direct: float, splits: list[dict[str, Any]]
) -> str:
    if not splits:
        return (
            f"No split-ticket savings found. Through-fare "
            f"{_format_price(direct)} is the best option."
        )
    best = splits[0]
    return (
        f"Splitting at **{best['split_hub']}** saves "
        f"{_format_price(best['savings'])} "
        f"({best['leg1_price']:.2f} + {best['leg2_price']:.2f} = "
        f"{best['combined_price']:.2f} vs through-fare "
        f"{_format_price(direct)}). "
        f"Found {len(splits)} viable split(s)."
    )


# ---------------------------------------------------------------------------
# Comprehensive fare report
# ---------------------------------------------------------------------------


async def full_fare_analysis(
    from_id: str,
    to_id: str,
    travel_date_str: str,
    journey_fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    station_fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    flexible_days: int = 3,
) -> dict[str, Any]:
    """Run all strategies and produce a consolidated fare report."""
    try:
        travel_date = date.fromisoformat(travel_date_str)
    except ValueError:
        return {"error": f"Invalid date: {travel_date_str}. Use YYYY-MM-DD."}

    window = await analyze_booking_window(
        from_id, to_id, travel_date, journey_fn, flexible_days
    )
    split = await analyze_split_ticket(
        from_id, to_id, travel_date_str, journey_fn, station_fn
    )

    report = {
        "route": {"from": from_id, "to": to_id, "travel_date": travel_date_str},
        "booking_window_analysis": window,
        "split_ticket_analysis": split,
        "fare_summary": _fare_summary(window, split),
    }
    return report


def _fare_summary(
    window: dict[str, Any], split: dict[str, Any]
) -> dict[str, Any]:
    """One-line bottom line."""
    now_price = window.get("prices_by_window", {}).get("book_now", {}).get("best_price")
    split_savings = split.get("best_savings", 0)
    best_split = split.get("split_options", [{}])[0] if split.get("split_options") else None

    return {
        "current_best_direct": _format_price(now_price),
        "split_ticket_savings": _format_price(split_savings) if split_savings else "None",
        "recommended_approach": (
            "Book single through-fare"
            if not best_split
            else f"Split at {best_split['split_hub']} "
                 f"- saves {_format_price(best_split['savings'])}"
        ),
    }
