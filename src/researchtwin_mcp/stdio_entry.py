"""Dedicated stdio entry point for uvx-hosted MCP clients.

This module intentionally starts the same six-tool server with the MCP SDK's
stdio transport. It opens no HTTP listener and writes configuration failures to
stderr so stdout remains reserved for MCP JSON-RPC messages.
"""

from __future__ import annotations

import sys

from researchtwin_mcp.config import ConfigurationError, Settings
from researchtwin_mcp.server import create_server


def main() -> None:
    """Run ResearchTwin MCP Server over stdin/stdout without an HTTP listener."""

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    create_server(settings).run("stdio")


if __name__ == "__main__":
    main()
