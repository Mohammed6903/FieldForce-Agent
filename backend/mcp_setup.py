"""Elastic MCP server wiring — the partner integration that qualifies us.

We attach Elastic's official MCP server (@elastic/mcp-server-elasticsearch) to
the agent crew via ADK's McpToolset. This is what makes the Elastic integration
"meaningful and via MCP": the Diagnostics agent and ad-hoc analytics queries
call Elastic's own MCP tools (search / esql / list_indices) live at runtime.

Requires Node.js (npx) on the host. If unavailable, agents still function via
the native Python tools in backend/tools/* — the MCP toolset is additive.
"""
from __future__ import annotations

from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StdioServerParameters,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from .config import settings


def get_elastic_mcp_toolset(tool_filter: list[str] | None = None) -> McpToolset:
    """Build an McpToolset bound to the Elastic MCP server.

    Args:
        tool_filter: Optionally restrict to specific MCP tool names
            (e.g. ["search", "esql"]) to keep the agent's tool surface focused.
    """
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=["-y", "@elastic/mcp-server-elasticsearch"],
                env={
                    "ES_URL": settings.elastic_endpoint,
                    "ES_API_KEY": settings.elastic_api_key,
                    # Prefer IPv4 and disable Happy-Eyeballs autoselection: some
                    # networks (DNS64/NAT64) hand Node an IPv6 address for Elastic
                    # Cloud that blackholes, and autoselection still races to it.
                    # Forcing the IPv4-first result is reachable everywhere.
                    "NODE_OPTIONS": "--dns-result-order=ipv4first --no-network-family-autoselection",
                    # The MCP server's OpenTelemetry exporter tries localhost:4318
                    # and spams errors when no collector is present; disable it.
                    "OTEL_SDK_DISABLED": "true",
                },
            ),
            timeout=60,
        ),
        tool_filter=tool_filter,
    )
