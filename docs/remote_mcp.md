# Remote MCP service

## Purpose

`researchtwin-mcp-remote` is the explicit entry point for the long-lived,
pre-installed ResearchTwin Remote MCP service. It starts the exact same
`create_server(Settings)` factory and the same six Tool contracts as the
existing stdio entry point; it does not duplicate registration code, use
`uvx`, or install dependencies while handling a request.

The transport is the MCP Python SDK 2.1.0 Streamable HTTP implementation:

~~~text
host:     RESEARCHTWIN_HOST
port:     RESEARCHTWIN_PORT
endpoint: /mcp
~~~

The retained `researchtwin-mcp-server` command remains the stdio entry point
for the legacy BaiLian uvx deployment. It is intentionally not replaced.

## Local operation and protocol verification

Install the package first, then launch the Remote entry point directly:

~~~powershell
.\.venv\Scripts\python.exe -m researchtwin_mcp.remote_entry
~~~

Use a persistent data path outside the source tree for a long-running service:

~~~powershell
$env:RESEARCHTWIN_HOST = "127.0.0.1"
$env:RESEARCHTWIN_PORT = "8000"
$env:RESEARCHTWIN_DATA_DIR = "D:\researchtwin-data"
$env:RESEARCHTWIN_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m researchtwin_mcp.remote_entry
~~~

Run the independent five-session protocol check from a second terminal:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\remote_stability_test.py --rounds 5
~~~

The check starts its own direct Python Remote process with temporary data, then
measures process-to-listener, process-to-first-initialize, initialize,
`tools/list`, `get_project_status`, activity record, and activity list
latencies. It requires five successful independent client sessions. It does not
use the operator's `runtime_data/` directory.

## Container and persistence

The Docker image installs the package during `docker build` and runs
`python -m researchtwin_mcp.remote_entry` at runtime. It does not invoke
`uvx`, `pip install`, or dependency resolution after the container starts.

Mount a host-managed persistent directory at `/app/runtime_data`, or set
`RESEARCHTWIN_DATA_DIR` to another writable mounted path. The JSON store
survives a process/container restart when that directory persists. It is
single-process safe, but it is not a multi-replica database or a cross-process
locking system.

## Later BaiLian and OpenTrek registration

Do not change the existing `ResearchTwin-MCP-v2` uvx service. After an
authorized public HTTPS deployment has passed its own protocol checks, create a
parallel service named `ResearchTwin-MCP-Remote`.

Alibaba Cloud's current custom-MCP documentation distinguishes remote
`streamableHttp` and SSE configurations, and its FAQ maps Streamable HTTP to
the MCP `/mcp` endpoint. Use the actual current BaiLian UI to select the
Remote/Streamable HTTP transport and enter the deployed public
`https://<host>/mcp` URL; do not hand-author a UI import payload or infer
optional fields. See the [official custom MCP guide](https://help.aliyun.com/zh/model-studio/custom-mcp)
and [official MCP FAQ](https://help.aliyun.com/zh/model-studio/mcp-faq).

Only after BaiLian itself discovers all six Tools and completes a read/write
tool call should a new, parallel OpenTrek MCP registration be created. Do not
replace the existing BaiLian or OpenTrek configuration until that verification
has succeeded.
