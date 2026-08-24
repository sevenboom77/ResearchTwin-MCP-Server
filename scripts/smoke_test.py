"""End-to-end MCP Streamable HTTP smoke test using the official MCP client."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = {
    "record_research_activity",
    "list_research_activities",
    "update_project_status",
    "get_project_status",
    "record_advisor_instruction",
    "generate_research_report",
}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_listening(process: subprocess.Popen[str], port: int, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _fail_with_server_output(process, "The MCP server exited before it started listening.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.15)
    _fail_with_server_output(process, f"The MCP server did not listen on port {port} in time.")


async def _exercise_client(url: str, title: str) -> None:
    """Discover tools and persist/read an activity through official MCP client APIs."""

    # Local proxy/VPN environment variables can otherwise route 127.0.0.1 through a proxy.
    async with httpx2.AsyncClient(trust_env=False, timeout=8.0) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                discovered = await session.list_tools()
                discovered_names = {tool.name for tool in discovered.tools}
                missing = EXPECTED_TOOL_NAMES - discovered_names
                if missing:
                    raise RuntimeError(f"MCP tool discovery did not return: {sorted(missing)}")

                record_result = await session.call_tool(
                    "record_research_activity",
                    {
                        "activity_type": "coding",
                        "title": title,
                        "description": "Created by the official MCP Streamable HTTP smoke test.",
                        "tags": ["smoke-test"],
                        "source": "test",
                    },
                )
                if record_result.is_error:
                    raise RuntimeError(f"record_research_activity failed: {record_result.model_dump(mode='json')}")

                list_result = await session.call_tool(
                    "list_research_activities",
                    {"tag": "smoke-test", "limit": 10},
                )
                if list_result.is_error:
                    raise RuntimeError(f"list_research_activities failed: {list_result.model_dump(mode='json')}")
                if title not in json.dumps(list_result.model_dump(mode="json"), ensure_ascii=False):
                    raise RuntimeError("The persisted smoke-test activity was not returned by list_research_activities.")


def _fail_with_server_output(process: subprocess.Popen[str], message: str) -> NoReturn:
    process.terminate()
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
    details = output[-4000:] if output else "(no server output)"
    raise RuntimeError(f"{message}\nServer output:\n{details}")


def main() -> None:
    """Launch an isolated server process and validate real MCP traffic."""

    port = _find_free_port()
    activity_title = "MCP Streamable HTTP smoke-test activity"
    with tempfile.TemporaryDirectory(prefix="researchtwin-mcp-smoke-") as data_directory:
        environment = os.environ.copy()
        environment.update(
            {
                "RESEARCHTWIN_HOST": "127.0.0.1",
                "RESEARCHTWIN_PORT": str(port),
                "RESEARCHTWIN_DATA_DIR": data_directory,
                "RESEARCHTWIN_LOG_LEVEL": "WARNING",
                "PYTHONUNBUFFERED": "1",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_until_listening(process, port)
            anyio.run(_exercise_client, f"http://127.0.0.1:{port}/mcp", activity_title)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=8)

    print("MCP Streamable HTTP smoke test passed: six tools discovered; activity persisted and read back.")


if __name__ == "__main__":
    main()
