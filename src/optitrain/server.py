"""
OptiTrain — MCP server entry point.

Wires together api.py, tools.py, resources.py, prompts.py
and runs the event loop over stdio transport.
"""

import asyncio
import logging

import mcp.server.stdio
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    PromptsCapability,
    ResourcesCapability,
    ServerCapabilities,
    ToolsCapability,
)

from optitrain import api, prompts, resources, tools

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

server = Server("optitrain")


# ---------------------------------------------------------------------------
# Wire MCP handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def handle_list_tools():
    return tools.get_tool_definitions()


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None):
    return await tools.handle_call_tool(name, arguments)


@server.list_resources()
async def handle_list_resources():
    return await resources.list_resources()


@server.read_resource()
async def handle_read_resource(uri: str):
    return await resources.read_resource(uri)


@server.list_prompts()
async def handle_list_prompts():
    return await prompts.list_prompts()


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict[str, str] | None):
    return await prompts.get_prompt(name, arguments)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

INIT_OPTIONS = InitializationOptions(
    server_name="optitrain",
    server_version="0.1.0",
    capabilities=ServerCapabilities(
        tools=ToolsCapability(),
        resources=ResourcesCapability(),
        prompts=PromptsCapability(),
    ),
)


async def run_stdio() -> None:
    """Start the MCP server over stdio transport."""
    logger.info("OptiTrain starting (stdio transport)...")
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, INIT_OPTIONS)
    finally:
        await api.close()
        logger.info("OptiTrain shut down.")
