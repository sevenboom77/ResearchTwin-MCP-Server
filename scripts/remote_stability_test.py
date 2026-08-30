"""Verify a pre-installed Remote MCP process across independent HTTP sessions.

This script starts the dedicated Remote entry point directly with the active
Python environment. It never uses uvx, never touches repository runtime_data,
and uses only the official MCP Streamable HTTP client.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import NoReturn

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = {
    "record_research_activity",
    "list_research_activities",
    "record_candidate_intelligence",
    "list_candidate_intelligence",
    "update_candidate_status",
    "update_project_status",
    "get_project_status",
    "record_advisor_instruction",
    "generate_research_report",
}


@dataclass(frozen=True, slots=True)
class SessionMetrics:
    """Measured results from one newly-created Streamable HTTP client session."""

    round_number: int
    initialize_seconds: float
    process_start_to_initialize_seconds: float
    tools_list_seconds: float
    get_project_status_seconds: float
    record_research_activity_seconds: float
    list_research_activities_seconds: float


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_listening(
    process: subprocess.Popen[str],
    port: int,
    *,
    started_at: float,
    timeout_seconds: float = 20.0,
) -> float:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if process.poll() is not None:
            _raise_with_server_output(process, "Remote MCP process exited before listening.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return time.perf_counter() - started_at
        except OSError:
            time.sleep(0.1)
    _raise_with_server_output(process, "Remote MCP process did not start listening in time.")


def _raise_with_server_output(process: subprocess.Popen[str], message: str) -> NoReturn:
    if process.poll() is None:
        process.terminate()
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
    raise RuntimeError(f"{message}\nServer output:\n{(output or '')[-4000:]}")


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)


def _start_remote_process(
    environment: dict[str, str],
) -> tuple[subprocess.Popen[str], int, float, float]:
    """Start a direct Remote process, retrying only a raced loopback port.

    A TCP port chosen by binding to port zero must be released before Uvicorn
    can bind it.  A different local process can theoretically win that tiny
    interval.  Retrying this known transient condition keeps the validation
    deterministic without hiding any other server-start failure.
    """

    for attempt in range(1, 4):
        port = _find_free_port()
        environment["RESEARCHTWIN_PORT"] = str(port)
        process_started_at = time.perf_counter()
        process = subprocess.Popen(
            [sys.executable, "-m", "researchtwin_mcp.remote_entry"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            listener_seconds = _wait_until_listening(
                process,
                port,
                started_at=process_started_at,
            )
        except RuntimeError as exc:
            if "address already in use" in str(exc).lower() and attempt < 3:
                continue
            raise
        return process, port, process_started_at, listener_seconds

    raise RuntimeError("Remote MCP process could not acquire a loopback port after three attempts.")


def _successful_payload(result: CallToolResult) -> dict[str, object]:
    if result.is_error or not isinstance(result.structured_content, dict):
        raise RuntimeError(f"Expected an MCP success result: {result.model_dump(mode='json')}")
    if result.structured_content.get("status") != "success":
        raise RuntimeError(f"Unexpected tool success envelope: {result.model_dump(mode='json')}")
    return result.structured_content


async def _exercise_independent_session(
    url: str,
    *,
    round_number: int,
    process_started_at: float,
    date: str,
) -> SessionMetrics:
    """Open one new client/session and verify discovery plus a write/read loop."""

    async with httpx2.AsyncClient(trust_env=False, timeout=20.0) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=30.0,
            ) as session:
                started = time.perf_counter()
                await session.initialize()
                initialize_seconds = time.perf_counter() - started
                process_start_to_initialize_seconds = time.perf_counter() - process_started_at

                started = time.perf_counter()
                tools = await session.list_tools()
                tools_list_seconds = time.perf_counter() - started
                discovered_tool_names = [tool.name for tool in tools.tools]
                if (
                    len(discovered_tool_names) != len(EXPECTED_TOOL_NAMES)
                    or set(discovered_tool_names) != EXPECTED_TOOL_NAMES
                ):
                    raise RuntimeError(
                        "tools/list did not return the expected nine tools: "
                        f"{sorted(discovered_tool_names)}"
                    )

                started = time.perf_counter()
                status = _successful_payload(await session.call_tool("get_project_status", {}))
                get_project_status_seconds = time.perf_counter() - started
                if "project_status" not in status:
                    raise RuntimeError("get_project_status did not return project_status.")

                title = f"Remote stability session {round_number}"
                tag = "remote-stability"
                started = time.perf_counter()
                activity = _successful_payload(
                    await session.call_tool(
                        "record_research_activity",
                        {
                            "date": date,
                            "activity_type": "coding",
                            "title": title,
                            "description": "Created by the local Remote MCP stability check.",
                            "tags": [tag],
                            "source": "remote-stability-test",
                        },
                    )
                )
                record_research_activity_seconds = time.perf_counter() - started
                activity_id = str(activity["activity_id"])

                started = time.perf_counter()
                activities = _successful_payload(
                    await session.call_tool(
                        "list_research_activities",
                        {
                            "start_date": date,
                            "end_date": date,
                            "tag": tag,
                            "limit": 100,
                        },
                    )
                )
                list_research_activities_seconds = time.perf_counter() - started
                if not any(
                    str(item["activity_id"]) == activity_id
                    for item in activities["activities"]
                    if isinstance(item, dict)
                ):
                    raise RuntimeError("The activity written in this session was not returned by list_research_activities.")

    return SessionMetrics(
        round_number=round_number,
        initialize_seconds=initialize_seconds,
        process_start_to_initialize_seconds=process_start_to_initialize_seconds,
        tools_list_seconds=tools_list_seconds,
        get_project_status_seconds=get_project_status_seconds,
        record_research_activity_seconds=record_research_activity_seconds,
        list_research_activities_seconds=list_research_activities_seconds,
    )


def _format_seconds(value: float) -> str:
    """Keep sub-millisecond localhost protocol measurements visible."""

    return f"{value:.6f}s"


def _print_results(listener_seconds: float, results: list[SessionMetrics]) -> None:
    first = results[0]
    warm_initialize = [result.initialize_seconds for result in results[1:]]
    print("Remote MCP local stability test")
    print(f"process_start_to_listener_seconds: {_format_seconds(listener_seconds)}")
    print(
        "process_start_to_protocol_ready_seconds: "
        f"{_format_seconds(first.process_start_to_initialize_seconds)}"
    )
    for result in results:
        print(
            "round={round} initialize={initialize} tools_list={tools} "
            "get_project_status={status} record_research_activity={record} "
            "list_research_activities={listed}".format(
                round=result.round_number,
                initialize=_format_seconds(result.initialize_seconds),
                tools=_format_seconds(result.tools_list_seconds),
                status=_format_seconds(result.get_project_status_seconds),
                record=_format_seconds(result.record_research_activity_seconds),
                listed=_format_seconds(result.list_research_activities_seconds),
            )
        )
    if warm_initialize:
        print(
            "warm_initialize_seconds: "
            f"min={_format_seconds(min(warm_initialize))} "
            f"max={_format_seconds(max(warm_initialize))} "
            f"avg={_format_seconds(sum(warm_initialize) / len(warm_initialize))}"
        )
    print(
        f"summary: {len(results)}/{len(results)} initialize, tools/list, "
        "get_project_status, record_research_activity, and list_research_activities succeeded."
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise five independent local Remote MCP Streamable HTTP sessions."
    )
    parser.add_argument("--rounds", type=int, default=5, help="Number of independent sessions; defaults to 5.")
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.rounds < 1:
        raise SystemExit("--rounds must be at least 1.")

    with tempfile.TemporaryDirectory(prefix="researchtwin-mcp-remote-stability-") as temporary_directory:
        environment = os.environ.copy()
        environment.update(
            {
                "RESEARCHTWIN_HOST": "127.0.0.1",
                "RESEARCHTWIN_DATA_DIR": temporary_directory,
                "RESEARCHTWIN_LOG_LEVEL": "WARNING",
                "PYTHONUNBUFFERED": "1",
            }
        )
        process, port, process_started_at, listener_seconds = _start_remote_process(environment)
        try:
            url = f"http://127.0.0.1:{port}/mcp"
            date = datetime.now(timezone.utc).date().isoformat()
            results = [
                anyio.run(
                    partial(
                        _exercise_independent_session,
                        url,
                        round_number=round_number,
                        process_started_at=process_started_at,
                        date=date,
                    )
                )
                for round_number in range(1, args.rounds + 1)
            ]
        finally:
            _stop_process(process)

    _print_results(listener_seconds, results)


if __name__ == "__main__":
    main()
