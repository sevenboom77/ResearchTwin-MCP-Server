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
- Dedicated Remote MCP entry point for pre-installed, long-lived Streamable HTTP deployment.
- Optional command-line SSE compatibility transport, when selected at startup.
- Dedicated stdio console entry point for uvx-hosted MCP clients.
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
├── Dockerfile                        # Streamable HTTP container image
├── src/researchtwin_mcp/
│   ├── config.py                     # RESEARCHTWIN_* settings validation
│   ├── server.py                     # MCP server and transport startup
│   ├── remote_entry.py                # Dedicated persistent Streamable HTTP entry
│   ├── stdio_entry.py                # Dedicated uvx/stdin-stdout MCP entry
│   ├── models/                       # Validation helpers and schemas
│   ├── storage/                      # Shared JSON persistence layer
│   └── tools/                        # Activity, status, advisor, and report tools
├── scripts/
│   ├── start_server.ps1
│   ├── show_connection_info.py       # Read-only local/LAN URL helper
│   ├── smoke_test.py
│   ├── deployment_check.py           # Read-only deployment preflight/probe
│   ├── build_fc_web_zip.py            # Linux x86_64 CPython 3.11 FC ZIP builder
│   ├── stdio_smoke_test.py            # Official Client stdio protocol smoke
│   └── wheel_stdio_smoke_test.py      # Non-editable wheel stdio validation
├── deploy/                           # systemd and Nginx deployment examples
├── tests/
├── docs/
├── examples/sample_data/             # Fictional, commit-safe demo data
└── runtime_data/                     # Local operational data; ignored by Git
~~~

## Requirements

- Windows PowerShell for local development, or Linux for deployment
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
| RESEARCHTWIN_HOST | 0.0.0.0 | Bind address. The code default permits trusted LAN clients to reach the service; use `127.0.0.1` behind a Linux reverse proxy. |
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

For a Docker container, use `RESEARCHTWIN_HOST=0.0.0.0` inside the container
and publish the container port only to host loopback when Nginx is the public
entry point. The supplied Dockerfile already has safe container defaults for
that pattern.

For a BaiLian-hosted stdio/uvx trial, set RESEARCHTWIN_DATA_DIR explicitly.
The FC example path /tmp/researchtwin-data is EPHEMERAL / DEMO ONLY: it may be
lost on instance recycling and is not long-term ResearchTwin Memory. See
[PyPI and BaiLian uvx preparation](docs/pypi_release.md).

## Run

With the virtual environment active:

~~~powershell
python server.py
~~~

For a deployment process that explicitly selects only the persistent Remote
Streamable HTTP transport, use:

~~~powershell
.\.venv\Scripts\python.exe -m researchtwin_mcp.remote_entry
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

### Hosted stdio mode for BaiLian uvx

The separate researchtwin-mcp-server console command runs the same six tools
over MCP stdin/stdout. It does not start HTTP, Uvicorn, or a listener on port
8000. PyPI release 0.1.0 remains the compatible stdio baseline. The Remote
entry added in this source tree is not part of that immutable PyPI release;
a future release needs a new version and separate publication. Do not replace
the existing BaiLian uvx service with this local source until the parallel
Remote deployment has completed its own public protocol verification.

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

Run the dedicated stdio smoke test after installing the project:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\stdio_smoke_test.py
~~~

It launches the dedicated console entry point through the official MCP stdio
client, verifies initialization and exactly six tools, records and reads back
temporary data, and checks an MCP isError response. It does not listen on port
8000.

Run the five-session Remote MCP stability check to verify the dedicated
pre-installed Python process. It uses its own temporary data directory and
prints cold/warm protocol latency measurements:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\remote_stability_test.py --rounds 5
~~~

## OpenTrek Integration

OpenTrek registration should use the UI's STREAMABLE choice and this URL shape:

~~~text
http://<LAN_IPV4>:8000/mcp
~~~

Do not hand-invent a transportType JSON value. Select STREAMABLE on the OpenTrek MCP registration page, enter the URL, save, and verify that all six tools are discovered. See [the step-by-step registration guide](docs/open_trek_registration.md) and [OpenTrek integration guidance](docs/open_trek_integration.md) for LAN IPv4 discovery, SSE compatibility, VPN checks, and a safe firewall troubleshooting process.

## Linux and remote deployment

The repository includes a non-root Docker image, a systemd service example, an
Nginx reverse-proxy example, and a read-only deployment preflight script.
They package the existing Streamable HTTP server without changing its six MCP
tools. Follow [the Linux deployment guide](docs/deployment_linux.md) before
using them.

The current service has no application-level authentication or authorization.
Never leave `http://<PUBLIC_IP>:8000/mcp` publicly exposed. A public deployment
needs HTTPS, a reverse proxy, restrictive network access, and an approved
authentication plan in addition to the provided packaging.

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
- [Linux deployment](docs/deployment_linux.md)
- [Remote MCP service](docs/remote_mcp.md)
- [FC Web Function ZIP packaging](docs/fc_web_deployment.md)
- [PyPI and BaiLian uvx preparation](docs/pypi_release.md)
- [Demo flow](docs/demo_flow.md)
- [Fictional sample data](examples/sample_data/README.md)
