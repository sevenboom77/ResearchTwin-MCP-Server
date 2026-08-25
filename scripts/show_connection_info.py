"""Print conservative, read-only MCP connection information.

The helper never changes network settings. It reports a LAN URL only when the
host name resolves to exactly one usable private IPv4 address; otherwise it
uses ``UNKNOWN`` so an operator can choose the correct adapter with ``ipconfig``.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from researchtwin_mcp.config import ConfigurationError, Settings


UNKNOWN = "UNKNOWN"
LOCAL_ONLY_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_usable_private_ipv4(value: str) -> bool:
    """Return whether *value* is a plausible LAN IPv4 address."""

    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False

    return (
        address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
    )


def detect_lan_ipv4() -> str | None:
    """Return one unambiguous private IPv4 candidate, or ``None``.

    A host can have Wi-Fi, Ethernet, VPN, virtual-machine, and WSL adapters at
    the same time. Choosing among multiple candidates would be misleading, so
    the caller receives ``None`` in that case.
    """

    try:
        records = socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
    except OSError:
        return None

    candidates = {
        record[4][0]
        for record in records
        if record[4] and _is_usable_private_ipv4(record[4][0])
    }
    if len(candidates) != 1:
        return None
    return next(iter(candidates))


def _configured_lan_ipv4(host: str) -> str | None:
    """Resolve the LAN address implied by the effective bind host safely."""

    normalized_host = host.strip().lower()
    if normalized_host in LOCAL_ONLY_HOSTS:
        return None
    if normalized_host == "0.0.0.0":
        return detect_lan_ipv4()
    if _is_usable_private_ipv4(host):
        return host
    return None


def _local_access_host(host: str) -> str | None:
    """Return the local host suitable for the configured bind address."""

    normalized_host = host.strip().lower()
    if normalized_host in {"0.0.0.0", "127.0.0.1"}:
        return "127.0.0.1"
    if normalized_host == "localhost":
        return "localhost"
    if normalized_host == "::1":
        return "[::1]"
    if _is_usable_private_ipv4(host):
        return host
    return None


def _url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show ResearchTwin MCP connection information.")
    parser.add_argument(
        "--transport",
        choices=("streamable-http", "sse"),
        default="streamable-http",
        help="Transport that will be started; defaults to streamable HTTP.",
    )
    return parser


def main() -> int:
    """Print URLs derived from the effective server configuration."""

    args = build_argument_parser().parse_args()
    try:
        settings = Settings.from_env(project_root=PROJECT_ROOT)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.transport == "streamable-http":
        transport_label = "STREAMABLE"
        active_path = "/mcp"
    else:
        transport_label = "SSE"
        active_path = "/sse"

    lan_ipv4 = _configured_lan_ipv4(settings.host)
    local_host = _local_access_host(settings.host)
    local_active_url = _url(local_host, settings.port, active_path) if local_host else UNKNOWN
    lan_active_url = _url(lan_ipv4, settings.port, active_path) if lan_ipv4 else UNKNOWN
    sse_url = _url(lan_ipv4, settings.port, "/sse") if lan_ipv4 else UNKNOWN

    print("ResearchTwin MCP Server")
    print(f"Host: {settings.host}")
    print(f"Port: {settings.port}")
    print(f"Transport: {transport_label}")
    print(f"Local URL: {local_active_url}")
    print(f"LAN URL: {lan_active_url}")
    print(f"SSE URL: {sse_url}")
    if settings.host == "0.0.0.0":
        print(
            "Listening on all interfaces; use 127.0.0.1 for local access or the "
            "machine LAN IPv4 for LAN access."
        )
    elif settings.host.strip().lower() in LOCAL_ONLY_HOSTS:
        print("LAN access is unavailable because RESEARCHTWIN_HOST is configured for local-only binding.")
    elif lan_ipv4 is None:
        print("LAN IPv4 is UNKNOWN; use ipconfig to select an active Ethernet or Wi-Fi IPv4 address.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
