# ResearchTwin MCP Server

ResearchTwin MCP Server is the persistent action layer for ResearchTwin, a long-horizon research-project agent. It gives an OpenTrek-hosted agent real MCP tools for recording research work, retaining project state and advisor requirements, and producing evidence-based progress reports.

The repository is designed as a competition-quality reference implementation: RAG answers questions from research material, while MCP performs explicit, auditable changes to the project record.

> All committed examples are fictional and anonymized. Operational data belongs in runtime_data/ and is intentionally excluded from Git.

## Overview

A research assistant should do more than answer a single question. ResearchTwin keeps a durable record of what happened in an evolving project:

- concrete activities, outcomes, blockers, and next steps;
- the current project stage, tasks, risks, and decisions;
- structured advisor requirements;
- weekly, meeting, or stage reports assembled from persisted evidence.

The server is intended to be called by the ResearchTwin Agent in OpenTrek. It does not replace the agent, an LLM, or the existing ResearchTwin_Docs knowledge base.

## Why MCP

RAG and MCP have distinct responsibilities:

| Capability | Responsibility |
| --- | --- |
| ResearchTwin_Docs RAG | Retrieve and explain already available papers, notes, and technical materials. |
| ResearchTwin MCP Server | Persist and retrieve research-management state through explicit tool calls. |
| ResearchTwin Agent | Decide when to retrieve, record, query, and summarize; turn natural language into structured tool arguments. |

This separation keeps the project record deterministic and reviewable. The MCP server does not need to run another LLM merely to store a structured activity or create a report from stored facts.

## Architecture

~~~mermaid
flowchart LR
    U[Researcher] --> A[OpenTrek ResearchTwin Agent]
    A -->|retrieve and reason| R[ResearchTwin_Docs RAG]
    R --> K[Research papers and technical material]
    A -->|MCP function calls| M[ResearchTwin MCP Server]
    M --> T[Six research-management tools]
    T --> S[JSON persistence layer]
    S --> D[Runtime research records and reports]
~~~

See [docs/architecture.md](docs/architecture.md) for component boundaries, persistence rules, and extension points.

## Features

- Official Python MCP SDK integration.
- Streamable HTTP as the primary MCP transport at /mcp.
- Optional command-line SSE compatibility transport, when selected at startup.
- Six focused tools instead of a monolithic server script.
- UTF-8 JSON persistence with atomic replacement and in-process locking.
- UUID record identifiers and timezone-aware ISO 8601 timestamps.
- Structured success and error responses suitable for agent tool handling.
- Windows PowerShell startup, test, smoke-test, and OpenTrek integration guidance.

## MCP Tools

| Tool | Use it when the agent needs to… |
| --- | --- |
| record_research_activity | Persist completed work, experimental results, blockers, reading, or next steps. |
| list_research_activities | Recall work history using date, type, or tag filters. |
| update_project_status | Merge or replace the current stage, task lists, risks, and decisions. |
| get_project_status | Read the current project snapshot before planning or reporting. |
| record_advisor_instruction | Preserve a structured advisor requirement, priority, deadline, and follow-up. |
| generate_research_report | Build a weekly, meeting, or stage Markdown report from persisted data. |

The complete input, output, and error contract is in [docs/mcp_tools.md](docs/mcp_tools.md).

## Project Structure

~~~text
ResearchTwin-MCP-Server/
├── server.py                         # Repository-root launch entry point
├── src/researchtwin_mcp/
│   ├── config.py                     # RESEARCHTWIN_* settings validation
│   ├── server.py                     # MCP server and transport startup
│   ├── models/                       # Validation helpers and schemas
│   ├── storage/                      # Shared JSON persistence layer
│   └── tools/                        # Activity, status, advisor, and report tools
├── scripts/
│   ├── start_server.ps1
│   ├── show_connection_info.py       # Read-only local/LAN URL helper
│   └── smoke_test.py
├── tests/
├── docs/
├── examples/sample_data/             # Fictional, commit-safe demo data
└── runtime_data/                     # Local operational data; ignored by Git
~~~

## Requirements

- Windows PowerShell (the documented workflow)
- Python 3.11 or newer; Python 3.11.x is the recommended competition environment
- Network access only when OpenTrek runs from another device on the LAN

## Installation

From a new Windows PowerShell session:

~~~powershell
Set-Location C:\work\ResearchTwin-MCP-Server
python --version
where.exe python

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python --version
where.exe python
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
~~~

The first result from where.exe python should be the virtual environment interpreter after activation. If PowerShell blocks activation for the current session, use its documented process-scoped execution-policy procedure, then activate the environment again; do not weaken system-wide policy unnecessarily.

## Configuration

The server reads these environment variables from the process environment:

| Variable | Default | Meaning |
| --- | --- | --- |
| RESEARCHTWIN_HOST | 0.0.0.0 | Bind address. Keeping this default permits trusted LAN clients to reach the service. |
| RESEARCHTWIN_PORT | 8000 | TCP port used by the selected transport. |
| RESEARCHTWIN_DATA_DIR | runtime_data | Local persistence directory, resolved relative to the repository root when relative. |
| RESEARCHTWIN_LOG_LEVEL | INFO | Python log level. |

Copy `.env.example` to `.env` if you want local configuration that survives a new PowerShell session. `.env` is ignored by Git and is loaded from the repository root when the server starts. Existing process or system environment variables always take precedence over values in `.env`.

~~~powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
~~~

For a one-off override, set values in the PowerShell session instead:

~~~powershell
$env:RESEARCHTWIN_HOST = "0.0.0.0"
$env:RESEARCHTWIN_PORT = "8000"
$env:RESEARCHTWIN_DATA_DIR = "runtime_data"
$env:RESEARCHTWIN_LOG_LEVEL = "INFO"
~~~

Do not put keys, personal identifiers, or a user-specific IP address in source code or committed configuration. Treat `.env` as local operational configuration, not a secret-management system.

## Run

With the virtual environment active:

~~~powershell
python server.py
~~~

The default primary endpoint is:

~~~text
http://<LAN_IPV4>:8000/mcp
~~~

For the local machine only, substitute 127.0.0.1 for <LAN_IPV4>. For OpenTrek on another trusted LAN device, use the Windows host's applicable IPv4 address. The helper script is also available:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\show_connection_info.py
.\scripts\start_server.ps1
~~~

`show_connection_info.py` only reads local configuration and network information. When it cannot unambiguously identify a LAN IPv4 address, it prints `UNKNOWN`; use `ipconfig` to choose the active Ethernet or Wi-Fi IPv4 address rather than guessing.

Streamable HTTP is the normal mode. For explicit SSE compatibility, run python server.py --transport sse and register the resulting /sse endpoint as documented in [OpenTrek integration guidance](docs/open_trek_integration.md). SSE is a separately selected transport mode, not an alternative URL to register alongside /mcp.

### Demo network safety

- Use `127.0.0.1` for local-only testing.
- The default `0.0.0.0` bind is only for a trusted LAN or campus-network demonstration.
- Do not expose this unauthenticated development server through public port forwarding.
- Before any public or broader deployment, add HTTPS, authentication, authorization, a reverse proxy, and appropriate network controls.

## Test

Run unit tests from the repository root:

~~~powershell
pytest -v
~~~

Run the local MCP Streamable HTTP smoke test after dependencies are installed:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\smoke_test.py
~~~

The smoke test starts an isolated Streamable HTTP server and uses the official MCP client to discover exactly six tools, call all six successfully, verify persistence and report generation, and check representative `isError` failures. It uses temporary data rather than your `runtime_data/` directory.

## OpenTrek Integration

OpenTrek registration should use the UI's STREAMABLE choice and this URL shape:

~~~text
http://<LAN_IPV4>:8000/mcp
~~~

Do not hand-invent a transportType JSON value. Select STREAMABLE on the OpenTrek MCP registration page, enter the URL, save, and verify that all six tools are discovered. See [the step-by-step registration guide](docs/open_trek_registration.md) and [OpenTrek integration guidance](docs/open_trek_integration.md) for LAN IPv4 discovery, SSE compatibility, VPN checks, and a safe firewall troubleshooting process.

## Demo Scenario

An end-to-end demonstration can show the difference between knowledge retrieval and persistent action:

1. The agent uses RAG to explain a fictional RNN-PPO paper or methods note.
2. The researcher says that an RNN-PPO experiment was completed but training is still unstable.
3. The agent calls record_research_activity with the outcome, problem, and next step.
4. A fictional advisor requirement to focus on generalization is recorded with record_advisor_instruction.
5. The agent checks project status, then calls generate_research_report for a group meeting.

The resulting Markdown report is grounded in persisted records, not a one-turn answer. A narrated runbook is in [docs/demo_flow.md](docs/demo_flow.md).

## Privacy and Git Safety

The repository's .gitignore excludes .venv/, __pycache__/, Python bytecode, .env, pytest and Ruff caches, runtime_data/, and log files. These paths may contain local research activity, advisor context, reports, credentials, or machine-specific data.

Only the fictional, anonymous fixtures in examples/sample_data/ are safe to commit. Before any commit or push, inspect:

~~~powershell
git status
git diff --check
~~~

Never commit real advisor messages, real paper content, chat transcripts, keys, VPN details, or personally identifying information.

## Roadmap

- Move from JSON files to a durable multi-user storage backend when needed.
- Add ResearchTwin Memory and ResearchTwin_Core integration points.
- Add paper-intelligence and citation workflows around the existing RAG layer.
- Add a protected dashboard for reviewing project history and reports.
- Improve the competition demo story without exposing real research data.

## Documentation

- [Architecture](docs/architecture.md)
- [MCP tool reference](docs/mcp_tools.md)
- [OpenTrek registration](docs/open_trek_registration.md)
- [OpenTrek integration](docs/open_trek_integration.md)
- [Demo flow](docs/demo_flow.md)
- [Fictional sample data](examples/sample_data/README.md)
