"""Dedicated entry point for the pre-installed, persistent Remote MCP service.

Unlike ``researchtwin_mcp.stdio_entry``, this module starts the official MCP
SDK Streamable HTTP transport on the configured host and port. It reuses the
same server factory and six registered Tools; it does not use ``uvx`` or
perform any runtime dependency installation.
"""

from __future__ import annotations

import sys

from researchtwin_mcp.config import ConfigurationError, Settings
from researchtwin_mcp.server import run_streamable_http_server


def main() -> None:
    """Load operational settings and run the long-lived Remote MCP endpoint."""

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    run_streamable_http_server(settings)


if __name__ == "__main__":
    main()
