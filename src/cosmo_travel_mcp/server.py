"""FastMCP server for cosmo-travel-mcp."""

from fastmcp import FastMCP

mcp = FastMCP("cosmo-travel-mcp")


def main() -> None:
    """Run the MCP server over stdio."""
    # Import and register tool modules.
    from .tools import flights

    flights.register(mcp)

    mcp.run()
