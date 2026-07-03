"""
MCP prompt templates for OptiTrain.
"""

import mcp.types as types


async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="optimize_fare",
            description="Guide for finding the cheapest train fare on a route",
            arguments=[
                types.PromptArgument(
                    name="origin",
                    description="Origin station name (e.g. 'Berlin Hbf')",
                    required=True,
                ),
                types.PromptArgument(
                    name="destination",
                    description="Destination station name (e.g. 'München Hbf')",
                    required=True,
                ),
                types.PromptArgument(
                    name="travel_date",
                    description="Travel date (YYYY-MM-DD)",
                    required=True,
                ),
            ],
        ),
    ]


async def get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    if name == "optimize_fare":
        return _prompt_optimize_fare(arguments)
    raise ValueError(f"Unknown prompt: {name}")


def _prompt_optimize_fare(
    arguments: dict[str, str] | None,
) -> types.GetPromptResult:
    origin = (arguments or {}).get("origin", "Berlin Hbf")
    destination = (arguments or {}).get("destination", "München Hbf")
    travel_date = (arguments or {}).get("travel_date", "2026-07-10")

    return types.GetPromptResult(
        description=f"Find the cheapest fare from {origin} to {destination} on {travel_date}",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=(
                        f"I want to travel from {origin} to {destination} on {travel_date}.\n\n"
                        "Please help me find the cheapest fare by:\n"
                        "1. First searching for stations to get the correct IDs\n"
                        "2. Running the comprehensive fare analysis\n"
                        "3. Checking split-ticket options\n"
                        "4. Applying the 21-day booking window rule\n\n"
                        "Give me the cheapest option and any trade-offs (travel time vs price)."
                    ),
                ),
            ),
        ],
    )
