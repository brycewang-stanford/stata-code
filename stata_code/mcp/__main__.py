"""Allow `python -m stata_code.mcp` to launch the MCP server."""

from stata_code.mcp.server import run_main

if __name__ == "__main__":  # pragma: no cover
    run_main()
