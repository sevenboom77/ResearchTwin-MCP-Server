# Architecture

## Purpose and boundaries

ResearchTwin is a long-horizon research-project agent. This repository provides its MCP action and persistence boundary: it records structured facts that the agent has decided should be retained, retrieves those facts later, and creates reports from them.

It deliberately does not duplicate the responsibilities of the agent or the RAG knowledge base:

| Layer | Owns | Does not own |
| --- | --- | --- |
| OpenTrek ResearchTwin Agent | Intent understanding, planning, tool selection, and prose refinement. | Direct file persistence rules. |
| ResearchTwin_Docs RAG | Retrieval and interpretation of existing research documents. | Mutation of project state. |
| ResearchTwin MCP Server | Validated research-management tools and deterministic report assembly. | LLM-driven document interpretation. |
| JSON storage | Durable local records. | Business rules or natural-language reasoning. |

This boundary makes it possible to trace a report back to specific stored activities, status fields, and advisor instructions.

## Component view

~~~mermaid
flowchart TB
    U[Researcher] --> A[OpenTrek ResearchTwin Agent]
    A -->|retrieve material| R[ResearchTwin_Docs RAG]
    A -->|call a named MCP tool| S[MCP Server]

    subgraph Srv[ResearchTwin MCP Server]
        C[Settings and logging]
        M[MCP tool registration]
        T1[Research activities]
        T2[Project status]
        T3[Advisor instructions]
        T4[Research reports]
        J[Shared JSON store]
        C --> M
        M --> T1
        M --> T2
        M --> T3
        M --> T4
        T1 --> J
        T2 --> J
        T3 --> J
        T4 --> J
    end

    S --> Srv
    J --> D[(runtime_data/)]
~~~

## Request lifecycle

1. OpenTrek receives the researcher's natural-language request.
2. The ResearchTwin Agent optionally uses RAG to understand relevant papers or technical context.
3. The agent selects an MCP tool and supplies structured arguments.
4. The server validates dates, enumerations, text fields, and list fields before modifying or reading storage.
5. A tool returns a structured success object, or a structured error object without exposing a Python traceback to the agent.
6. The agent can use the returned record or Markdown report in its response to the researcher.

The server therefore records facts after the agent has interpreted the request; it does not treat an unparsed chat transcript as a reliable project record.

## MCP transport

The primary transport is Streamable HTTP:

~~~text
bind:     0.0.0.0:8000 by default
endpoint: /mcp
URL:      http://<LAN_IPV4>:8000/mcp
~~~

Binding to 0.0.0.0 allows a trusted LAN OpenTrek deployment to connect. It does not itself grant access through a firewall, VPN, router, or public network.

An optional CLI-selected SSE compatibility mode may expose:

~~~text
SSE endpoint:      /sse
message endpoint:  /messages/
~~~

Only select the matching transport in the client. The two transports are alternatives; the default integration path is Streamable HTTP. See [OpenTrek integration](open_trek_integration.md) for safe registration and network troubleshooting.

The third, independent transport is stdio for a host that launches the
researchtwin-mcp-server console command, such as a future BaiLian uvx
deployment:

~~~text
process: researchtwin-mcp-server
wire:    stdin/stdout MCP JSON-RPC
port:    none
logs:    stderr only
~~~

It registers the same six tools through the same create_server() function. It
does not start Uvicorn, does not bind port 8000, and does not replace the
Streamable HTTP or SSE modes. See [PyPI and BaiLian uvx preparation](pypi_release.md)
for release and FC constraints.

## Tool modules

The MCP surface has six small, purpose-specific operations:

| Concern | Write operation | Read or derived operation |
| --- | --- | --- |
| Research history | record_research_activity | list_research_activities |
| Current project snapshot | update_project_status | get_project_status |
| Advisor requirements | record_advisor_instruction | Included by report generation |
| Project communication | — | generate_research_report |

The tool layer is intentionally separate from storage. A tool owns input validation and business behavior; the shared store owns directory initialization, UTF-8 JSON I/O, and safe replacement. This prevents duplicate file-writing logic from drifting across tools.

## Persistence model

Operational records live under the configured RESEARCHTWIN_DATA_DIR, which defaults to runtime_data/:

~~~text
runtime_data/
├── research_logs.json
├── project_status.json
├── advisor_instructions.json
└── reports/
    └── <date-range>_<report-type>.md
~~~

New activity and advisor-instruction records have UUIDs plus created_at and updated_at values expressed as timezone-aware ISO 8601 timestamps. The project-status snapshot also preserves created_at and updated_at. JSON is encoded as UTF-8 with non-ASCII text preserved.

The store initializes missing directories and expected empty structures. Writes are performed through a temporary file and atomic replacement, while an in-process lock protects concurrent tool calls within the server process. A malformed individual record is treated defensively so it does not take down the entire service.

JSON files are the first-version persistence choice because they are transparent, portable, and easy to demonstrate. They are not a substitute for a multi-user database, cross-process locking strategy, access control, backup policy, or audit system in a production deployment.

### FC temporary-storage boundary

If a BaiLian FC stdio trial sets
RESEARCHTWIN_DATA_DIR=/tmp/researchtwin-data, that path is EPHEMERAL / DEMO
ONLY. An instance recycle can remove all JSON records and reports, and separate
instances do not share the directory. Such a trial validates Tool Discovery and
Function Calling only; it does not complete long-term Memory or durable
research-process management. NAS, OSS, a database, or another approved
persistent backend requires a separate future design phase.

## Validation and error contract

The tools validate inputs before persistence:

- dates use YYYY-MM-DD;
- activity type, priority, report type, and merge mode are constrained enumerations;
- required text cannot be blank;
- list fields contain valid strings;
- activity queries reject an inverted date range;
- duplicate activity attempts are rejected rather than silently treated as success.

The common result shape is:

~~~json
{
  "status": "success"
}
~~~

or:

~~~json
{
  "status": "error",
  "error_code": "machine_readable_reason",
  "message": "A concise safe explanation for the agent."
}
~~~

Successful MCP calls carry typed structured content that conforms to the tool's published success output schema. A handled business failure carries the safe error JSON as text content and sets `CallToolResult.isError` to `true`; it intentionally does not claim to satisfy the success output schema. Detailed tracebacks belong in server logs for diagnosis, not in the final tool response.

## Reporting flow

generate_research_report reads the persisted research logs, current status, and advisor instructions. Activities are selected by their inclusive report-date window. Advisor instructions are selected only when the date in each record's `created_at` timestamp falls within that same inclusive window; records with a missing or malformed timestamp are omitted. The current project-status snapshot is retained in full as current context, even when it was last updated outside the report window. The tool writes and returns a Markdown document with these stable sections:

1. Stage goals
2. Completed work
3. Key results
4. Current issues and risks
5. Learning and literature reading
6. Advisor requirements and items to confirm
7. Important project decisions
8. Next-step plan

The report is structured evidence. The higher-level agent may refine its language, but should not present unsupported conclusions as stored facts.

## Configuration and observability

All runtime settings come from RESEARCHTWIN_* environment variables:

- RESEARCHTWIN_HOST, default 0.0.0.0
- RESEARCHTWIN_PORT, default 8000
- RESEARCHTWIN_DATA_DIR, default runtime_data
- RESEARCHTWIN_LOG_LEVEL, default INFO

The server logs startup, tool invocation, storage reads and writes, and errors. Logs should use record IDs and operational metadata rather than full advisor messages, raw research material, or other sensitive text.

The runtime loads a repository-root `.env` file when present, without overriding existing process environment variables. `.env.example` documents the supported variables; `.env` is local operational configuration and is excluded from Git.

## Security and privacy boundary

The agent should send only the structured minimum needed for a tool call. Do not persist full chat transcripts, source documents, API keys, personal addresses, or real advisor messages by default.

runtime_data/ is excluded from version control. examples/sample_data/ contains intentionally fictional fixtures only. Before sharing a demo, verify that generated reports and screenshots also use fictional or approved data.

For LAN demonstrations, expose the service only to a trusted network. Do not assume that a 0.0.0.0 bind is appropriate for public deployment; add the appropriate authentication, authorization, TLS, and network controls before any broader exposure.

## Extension path

The current separation supports incremental upgrades without changing the agent-facing intent:

1. Replace JSON storage behind the store boundary with a database.
2. Add a long-term ResearchTwin Memory module that consumes validated records.
3. Add ResearchTwin_Core tools for domain-specific experiment or paper workflows.
4. Add an authenticated dashboard that reads the same project state.
5. Add report templates while preserving the source-record traceability shown here.
