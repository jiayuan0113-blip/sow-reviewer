"""Synthetic sanctions-list lookup exposed as an MCP tool (stdio).

Hand-written by Joyce on 2026-10-29. Spec: docs/verify_rules.md (second half).
Do NOT generate this file with an AI assistant.
"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("sanctions-lookup")


@server.tool()
def sanctions_lookup(name: str) -> dict:
    """Look up a name against the synthetic sanctions list."""
    raise NotImplementedError("hand-write me on 2026-10-29")


if __name__ == "__main__":
    server.run(transport="stdio")
