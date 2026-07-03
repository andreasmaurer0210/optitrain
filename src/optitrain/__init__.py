"""
OptiTrain — MCP server for Deutsche Bahn fare optimization.

Modules:
  api.py        HTTP client for HAFAS transport REST API
  tools.py      MCP tool definitions + handlers
  strategies.py Predictive pricing strategies (21-day, split-ticketing)
  resources.py  MCP resource handlers
  prompts.py    MCP prompt templates
  server.py     MCP wiring + entry point
"""

import asyncio
import os

from optitrain.server import run_stdio


def main() -> None:
    """Synchronous entry point for the `optitrain` CLI command."""
    asyncio.run(run_stdio())


__all__ = ["main"]
