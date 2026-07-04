# OptiTrain — MCP Server for Deutsche Bahn Fare Optimization

MCP server that queries the **HAFAS transport REST API** (v5.db.transport.rest)
and applies predictive pricing strategies to find the cheapest train fares in Germany.

## Tools

| Tool | Args | Purpose |
|------|------|---------|
| `search_stations` | `query`, `results?` | Search stations by name |
| `get_station_details` | `id` | Station info + lines |
| `get_journeys` | `from`, `to`, `departure?`, `arrival?`, `results?` | Timetable + prices |
| `analyze_fare` | `from`, `to`, `travel_date`, `flexible_days?` | Full fare optimization report |
| `check_api_health` | — | Check backend status (live vs mock mode) |
| `analyze_split_ticket` | `from`, `to`, `travel_date` | Split-ticketing savings analysis |
| `check_booking_window` | `from`, `to`, `travel_date` | 21-day threshold rule check |

## Strategies

- **21-Day Threshold** — booking ≥21 days ahead typically locks the cheapest Sparpreis tier
- **Split-Ticketing** — two partial tickets can undercut a through-fare at intermediate ICE hubs
- **Sparpreis vs Flexpreis** — price comparison with flexibility trade-off

## Usage

```bash
uv sync
uv run optitrain
```

## Config

| Env var | Default | Purpose |
|---------|---------|---------|
| `OPTITRAIN_API_BASE` | `https://v5.db.transport.rest` | HAFAS REST API base URL |
| `OPTITRAIN_USER_AGENT` | `optitrain-mcp/0.1.0` | HTTP User-Agent header |

## Client config

### OpenCode

```json
{
  "mcp": {
    "optitrain": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/Users/andreas.maurer/workspace/optitrain", "optitrain"],
      "enabled": true
    }
  }
}
```
