"""
MCP tool definitions and handlers for OptiTrain.
"""

import logging
from datetime import date

import mcp.types as types

from optitrain import api, strategies

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def get_tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_stations",
            description="Search for German train stations by name keyword.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Station name keyword (e.g. 'Berlin Hbf', 'München', 'Frankfurt')",
                    },
                    "results": {
                        "type": "integer",
                        "description": "Maximum number of results (default 10, max 50)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_station_details",
            description="Get detailed information about a station by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Station ID (EVA number, e.g. '8011160' for Berlin Hbf). Use search_stations to find it.",
                    },
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="get_journeys",
            description="Find train connections between two stations with prices.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from": {
                        "type": "string",
                        "description": "Origin station ID (EVA number, e.g. '8011160')",
                    },
                    "to": {
                        "type": "string",
                        "description": "Destination station ID (EVA number)",
                    },
                    "departure": {
                        "type": "string",
                        "description": "Departure datetime (ISO 8601, e.g. '2026-07-10T08:00:00'). Default: now.",
                    },
                    "arrival": {
                        "type": "string",
                        "description": "Target arrival datetime (alternative to departure).",
                    },
                    "results": {
                        "type": "integer",
                        "description": "Number of journey results (default 5, max 20)",
                        "default": 5,
                    },
                },
                "required": ["from", "to"],
            },
        ),
        types.Tool(
            name="check_booking_window",
            description="Analyze DB 21-day threshold rule: how prices vary by booking date for a route.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from": {
                        "type": "string",
                        "description": "Origin station ID",
                    },
                    "to": {
                        "type": "string",
                        "description": "Destination station ID",
                    },
                    "travel_date": {
                        "type": "string",
                        "description": "Travel date (YYYY-MM-DD format)",
                    },
                    "flexible_days": {
                        "type": "integer",
                        "description": "Flexibility in days around travel date (default 3)",
                        "default": 3,
                    },
                },
                "required": ["from", "to", "travel_date"],
            },
        ),
        types.Tool(
            name="analyze_split_ticket",
            description="Check if buying separate tickets for journey segments is cheaper than a through-fare.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from": {
                        "type": "string",
                        "description": "Origin station ID",
                    },
                    "to": {
                        "type": "string",
                        "description": "Destination station ID",
                    },
                    "travel_date": {
                        "type": "string",
                        "description": "Travel date (YYYY-MM-DD) or ISO datetime",
                    },
                },
                "required": ["from", "to", "travel_date"],
            },
        ),
        types.Tool(
            name="analyze_fare",
            description="Comprehensive fare analysis: booking window + split-ticket + recommendations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from": {
                        "type": "string",
                        "description": "Origin station ID",
                    },
                    "to": {
                        "type": "string",
                        "description": "Destination station ID",
                    },
                    "travel_date": {
                        "type": "string",
                        "description": "Travel date (YYYY-MM-DD)",
                    },
                    "flexible_days": {
                        "type": "integer",
                        "description": "Flexibility in days (default 3)",
                        "default": 3,
                    },
                },
                "required": ["from", "to", "travel_date"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_search_stations(
    args: dict | None,
) -> list[types.TextContent]:
    if not args or "query" not in args:
        raise ValueError("Missing required argument: 'query'")

    query = args["query"]
    results = min(args.get("results", 10), 50)

    try:
        data = await api.search_stations(query, results)
    except Exception as exc:
        return [
            types.TextContent(
                type="text",
                text=f"API error: {exc}\n\nThe HAFAS API may be temporarily unavailable. Try again later.",
            )
        ]

    if not data:
        return [types.TextContent(type="text", text=f"No stations found matching '{query}'.")]

    lines = [f"Found {len(data)} station(s) matching '{query}':", ""]
    for s in data:
        loc = s.get("location", {})
        coords = f"({loc.get('latitude', '?')}, {loc.get('longitude', '?')})" if loc else ""
        products = ", ".join(s.get("products", []))[:60]
        lines.append(f"  • **{s['name']}** — ID `{s['id']}` {coords}")
        if products:
            lines.append(f"    Products: {products}")
    return [types.TextContent(type="text", text="\n".join(lines))]


async def _handle_get_station_details(
    args: dict | None,
) -> list[types.TextContent]:
    if not args or "id" not in args:
        raise ValueError("Missing required argument: 'id'")

    try:
        data = await api.get_station(args["id"])
    except Exception as exc:
        return [
            types.TextContent(
                type="text",
                text=f"API error: {exc}",
            )
        ]

    loc = data.get("location", {})
    lines = [
        f"**{data.get('name', '?')}**",
        f"ID: `{data.get('id', '?')}`",
        f"Location: ({loc.get('latitude', '?')}, {loc.get('longitude', '?')})",
    ]
    if data.get("products"):
        lines.append(f"Products: {', '.join(data['products'])}")
    if data.get("lines"):
        line_names = [l.get("name", "?") for l in data["lines"][:20]]
        lines.append(f"Lines: {', '.join(line_names)}")
    return [types.TextContent(type="text", text="\n".join(lines))]


async def _handle_get_journeys(
    args: dict | None,
) -> list[types.TextContent]:
    if not args or "from" not in args or "to" not in args:
        raise ValueError("Missing required arguments: 'from', 'to'")

    try:
        data = await api.get_journeys(
            from_id=args["from"],
            to_id=args["to"],
            departure=args.get("departure"),
            arrival=args.get("arrival"),
            results=args.get("results", 5),
        )
    except Exception as exc:
        return [
            types.TextContent(
                type="text",
                text=f"API error: {exc}\n\nThe HAFAS API may be temporarily unavailable. Try again later.",
            )
        ]

    journeys = data.get("journeys", [])
    if not journeys:
        return [types.TextContent(type="text", text="No journeys found for this route.")]

    lines = [f"**{len(journeys)} journey(s) found**", ""]
    for i, j in enumerate(journeys, 1):
        legs = j.get("legs", [])
        first = legs[0] if legs else {}
        last = legs[-1] if legs else {}

        dep = first.get("origin", {}).get("name", "?")
        dep_time = first.get("departure", "?")
        arr = last.get("destination", {}).get("name", "?")
        arr_time = last.get("arrival", "?")

        duration = j.get("duration", "?")

        price = j.get("price") or {}
        price_str = (
            f"{price['amount']:.2f} {price.get('currency', 'EUR')}"
            if price.get("amount") is not None
            else "Price N/A"
        )
        price_hint = f" ({price.get('hint', '')})" if price.get("hint") else ""

        # Transfer count
        transfers = len(legs) - 1

        lines.append(
            f"**#{i}**  {dep} → {arr}  "
            f"|  {dep_time} → {arr_time}"
        )
        lines.append(f"   Duration: {duration}s  |  Transfers: {transfers}  |  {price_str}{price_hint}")

        # Line info
        for leg in legs[:4]:  # show first few legs
            line = leg.get("line", {})
            if line:
                origin = leg.get("origin", {}).get("name", "?")
                dest = leg.get("destination", {}).get("name", "?")
                product = line.get("product", line.get("name", "?"))
                direction = leg.get("direction", "")
                dir_str = f" → {direction}" if direction else ""
                lines.append(f"   ├ {product}  {origin} → {dest}{dir_str}")

        if len(legs) > 4:
            lines.append(f"   └ … {len(legs) - 4} more leg(s)")

        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _handle_check_booking_window(
    args: dict | None,
) -> list[types.TextContent]:
    if not args or "from" not in args or "to" not in args or "travel_date" not in args:
        raise ValueError("Missing required arguments: 'from', 'to', 'travel_date'")

    try:
        travel_date = date.fromisoformat(args["travel_date"])
    except ValueError:
        return [
            types.TextContent(
                type="text",
                text=f"Invalid date: {args['travel_date']}. Use YYYY-MM-DD.",
            )
        ]

    result = await strategies.analyze_booking_window(
        from_id=args["from"],
        to_id=args["to"],
        travel_date=travel_date,
        journey_fn=api.get_journeys,
        flexible_days=args.get("flexible_days", 3),
    )

    lines = [
        f"## Booking Window Analysis",
        f"Route: {result['from']} → {result['to']}",
        f"Travel: {result['travel_date']}  |  {result['days_until_travel']} days away",
        f"Analysis date: {result['analysis_date']}",
        "",
        "### Prices by booking window",
    ]

    for key, val in result.get("prices_by_window", {}).items():
        if "error" in val:
            lines.append(f"  • **{key}**: ❌ {val['error']}")
            continue
        price = val.get("best_price")
        if val.get("price_hints"):
            hints_str = ", ".join(
                f"{h['amount']:.2f} ({h['hint']})" for h in val["price_hints"]
            )
        else:
            hints_str = ""
        lines.append(
            f"  • **{key}**: {price:.2f} EUR{' — ' + hints_str if hints_str else ''}"
        )

    lines.extend([
        "",
        f"### Recommendation",
        result.get("recommendation", "N/A"),
    ])

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _handle_analyze_split_ticket(
    args: dict | None,
) -> list[types.TextContent]:
    if not args or "from" not in args or "to" not in args or "travel_date" not in args:
        raise ValueError("Missing required arguments: 'from', 'to', 'travel_date'")

    result = await strategies.analyze_split_ticket(
        from_id=args["from"],
        to_id=args["to"],
        travel_date=args["travel_date"],
        journey_fn=api.get_journeys,
        station_fn=api.get_station,
    )

    if "error" in result:
        return [types.TextContent(type="text", text=f"Error: {result['error']}")]

    lines = [
        f"## Split-Ticket Analysis",
        f"Route: {result['from']} → {result['to']}",
        f"Travel date: {result['travel_date']}",
        "",
        f"**Direct fare:** {result['direct_price']:.2f} EUR",
        "",
    ]

    if not result["split_options"]:
        lines.append("No viable split-ticket savings found.")
    else:
        lines.append(f"**Best split** saves {result['best_savings']:.2f} EUR:")
        lines.append("")
        for s in result["split_options"][:5]:
            lines.append(
                f"  • Split at **{s['split_hub']}**: "
                f"{s['leg1_price']:.2f} + {s['leg2_price']:.2f} = {s['combined_price']:.2f} EUR "
                f" |  **Save {s['savings']:.2f} EUR**"
            )

    lines.extend([
        "",
        f"### Recommendation",
        result.get("recommendation", ""),
    ])

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _handle_analyze_fare(
    args: dict | None,
) -> list[types.TextContent]:
    if not args or "from" not in args or "to" not in args or "travel_date" not in args:
        raise ValueError("Missing required arguments: 'from', 'to', 'travel_date'")

    result = await strategies.full_fare_analysis(
        from_id=args["from"],
        to_id=args["to"],
        travel_date_str=args["travel_date"],
        journey_fn=api.get_journeys,
        station_fn=api.get_station,
        flexible_days=args.get("flexible_days", 3),
    )

    if "error" in result:
        return [types.TextContent(type="text", text=f"Error: {result['error']}")]

    summary = result.get("fare_summary", {})
    window = result.get("booking_window_analysis", {})
    split = result.get("split_ticket_analysis", {})

    lines = [
        "## 🚄 OptiTrain — Full Fare Report",
        f"**{result['route']['from']} → {result['route']['to']}**",
        f"Travel: {result['route']['travel_date']}",
        "",
        "---",
        "### Current Best Direct Fare",
        f"**{summary.get('current_best_direct', 'N/A')}**",
        "",
        "### Split-Ticket Savings",
        f"{summary.get('split_ticket_savings', 'N/A')}",
        "",
        "### 🔍 Recommended Approach",
        f"**{summary.get('recommended_approach', 'N/A')}**",
        "",
        "---",
        "### Booking Window Analysis",
    ]

    for key, val in window.get("prices_by_window", {}).items():
        if "error" in val:
            lines.append(f"  • {key}: ERROR — {val['error']}")
            continue
        price = val.get("best_price")
        lines.append(f"  • **{key}**: {price:.2f} EUR" if price else f"  • **{key}**: N/A")

    lines.extend([
        "",
        f"**Advice:** {window.get('recommendation', 'N/A')}",
        "",
        "---",
        "### Split-Ticket Details",
    ])

    if split.get("split_options"):
        for s in split["split_options"][:5]:
            lines.append(
                f"  • {s['split_hub']}: {s['leg1_price']:.2f} + {s['leg2_price']:.2f}"
                f" = {s['combined_price']:.2f}  |  save **{s['savings']:.2f} EUR**"
            )
    else:
        lines.append("  None found.")

    return [types.TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# Dispatching (defined after handlers to avoid NameError)
# ---------------------------------------------------------------------------

HANDLERS: dict[str, callable] = {
    "search_stations": _handle_search_stations,
    "get_station_details": _handle_get_station_details,
    "get_journeys": _handle_get_journeys,
    "check_booking_window": _handle_check_booking_window,
    "analyze_split_ticket": _handle_analyze_split_ticket,
    "analyze_fare": _handle_analyze_fare,
}


async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    handler = HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    return await handler(arguments)
