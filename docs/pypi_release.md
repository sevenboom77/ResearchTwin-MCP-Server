# PyPI release and BaiLian uvx preparation

This document is release preparation only. At the time of writing,
ResearchTwin MCP Server has **not** been published to PyPI, uploaded to
TestPyPI, deployed to BaiLian, or connected to Function Compute (FC).

## Current package identity

| Field | Current value |
| --- | --- |
| Distribution name | researchtwin-mcp-server |
| Version | 0.1.0 |
| Python requirement | >=3.11 |
| Runtime dependencies | mcp==2.1.0, python-dotenv>=1.0.0,<2.0.0 |
| HTTP/SSE entry point | researchtwin-mcp |
| BaiLian stdio entry point | researchtwin-mcp-server |

The package name was checked against the public PyPI JSON endpoint on
2026-08-26 and returned HTTP 404. That suggests the name was not published at
that instant; it is not a reservation or a guarantee that PyPI will accept a
future upload.

## 1. What the stdio entry point does

The console command below starts the same six MCP tools over stdin/stdout:

~~~text
researchtwin-mcp-server
~~~

It calls the official MCP SDK stdio transport. It does not start Uvicorn, does
not bind TCP port 8000, and does not expose an HTTP URL. Standard output is
reserved for MCP JSON-RPC traffic; configuration failures and Python logging
go to stderr.

The existing command remains unchanged:

~~~text
researchtwin-mcp
~~~

It defaults to Streamable HTTP and still supports the existing explicit SSE
mode. HTTP, SSE, and stdio are separate startup modes over the same six-tool
server, not three independent tool sets.

## 2. Release blockers that need a project-owner decision

The source package can be built and checked locally, but public release is not
legally/metadata-complete yet:

- no LICENSE file is present;
- no project license metadata is declared;
- no public author or maintainer metadata is declared.

The project owner must choose a license and decide what public contact metadata
is appropriate before any PyPI upload. Do not invent a license, copy a
personal email address, or add a token to solve this. Build and Twine checks do
not replace that ownership/legal decision.

## 3. Local build and validation

From the repository root, use the project virtual environment:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check dist/*
.\.venv\Scripts\python.exe .\scripts\wheel_stdio_smoke_test.py --wheel .\dist\researchtwin_mcp_server-0.1.0-py3-none-any.whl
~~~

The expected build outputs are:

~~~text
dist/researchtwin_mcp_server-0.1.0-py3-none-any.whl
dist/researchtwin_mcp_server-0.1.0.tar.gz
~~~

The wheel smoke test creates a temporary virtual environment, installs the
wheel non-editably, launches its installed researchtwin-mcp-server executable,
and runs an official MCP Client stdio tools/list verification. It uses a
temporary data directory and does not use runtime_data/.

Inspect the archives before release. They must contain the src package and
package metadata but must not contain .env, runtime_data/, data/, dist/,
credentials, research records, or private keys. The repository ignores build/
and dist/ so generated artifacts are not staged accidentally.

## 4. Manual TestPyPI and PyPI workflow

Only the project owner should perform uploads. This repository never asks for,
stores, prints, or commits a PyPI token.

Recommended order:

1. Finish the license and public metadata decision.
2. Build from a clean working tree and run the checks in the previous section.
3. Optionally create a new, unused version and upload it to TestPyPI first.
4. Install from TestPyPI in a separate environment and re-run the stdio
   discovery test.
5. Choose a new, unused final version and upload manually to public PyPI.
6. Verify the actual public distribution and executable with uvx.
7. Only then create or update the BaiLian MCP registration.

PyPI versions are immutable after upload. Never reuse an already uploaded
version number.

For a manual upload, Twine supports environment variables or the user's local
credential mechanism. For example, in a temporary shell only:

~~~powershell
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "<token-kept-only-in-this-shell>"
.\.venv\Scripts\python.exe -m twine upload dist/*
Remove-Item Env:TWINE_PASSWORD
~~~

The example is instructional only. Do not put a real token in .env,
pyproject.toml, README, a report, a shell history shared with others, or Git.

## 5. uvx verification after public PyPI release

The distribution name and stdio executable intentionally match. The expected
shape is therefore:

~~~text
uvx researchtwin-mcp-server==<PUBLISHED_VERSION>
~~~

This command is **not yet locally verified** because uv and uvx are not
installed in the current development environment. Do not copy it into BaiLian
until both conditions hold:

1. the exact version exists on public PyPI; and
2. the command succeeds in an environment with uvx installed and an official
   MCP Client can initialize and list exactly six tools.

If uvx resolves the distribution but does not select the expected executable,
the operator must use the actual verified alternative:

~~~text
uvx --from researchtwin-mcp-server==<PUBLISHED_VERSION> researchtwin-mcp-server
~~~

Do not guess between these forms; record the command that was actually tested.

## 6. Conditional BaiLian configuration template

Use this template only after public PyPI publication and real uvx verification.
It is not evidence that BaiLian has connected successfully.

~~~json
{
  "mcpServers": {
    "researchtwin-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "researchtwin-mcp-server==<PUBLISHED_VERSION>"
      ],
      "env": {
        "RESEARCHTWIN_DATA_DIR": "/tmp/researchtwin-data",
        "RESEARCHTWIN_LOG_LEVEL": "INFO"
      }
    }
  }
}
~~~

If the verified uvx command needs the --from form, use the verified argument
sequence instead of this template. Select the current BaiLian UI's stdio/uvx
installation mode manually; this repository does not operate the console.

Suggested human-entered values, subject to the current UI:

| Field | Value |
| --- | --- |
| Service name | ResearchTwin-MCP |
| Description | ResearchTwin 科研过程管理 MCP 服务，用于记录科研活动、维护项目状态、保存导师要求、查询科研历史并生成科研报告。 |
| Installation mode | uvx |
| Deployment mode | 基础模式 for the first connectivity test |
| 极速模式 | 关闭 for the first connectivity test |
| Region | 北京 only if the current authorized UI offers and requires that choice |

## 7. FC storage is ephemeral

The FC environment variable below is permitted only for first-round discovery
and function-calling verification:

~~~text
RESEARCHTWIN_DATA_DIR=/tmp/researchtwin-data
~~~

It is **EPHEMERAL / DEMO ONLY**. FC local disks can disappear when an instance
is recycled and are not a cross-instance shared store. The following records
can therefore be lost:

- research_logs;
- project_status;
- advisor_instructions;
- generated reports.

An FC uvx deployment can prove Tool Discovery and Function Calling. It cannot
prove long-term Memory or durable research-process management while using
/tmp. A later, separately designed phase must choose an approved persistent
backend such as NAS, OSS, a database, or another platform-supported storage
service. This phase does not implement that backend.

## 8. BaiLian acceptance after manual deployment

After the user publishes and configures the package, verify in order:

1. BaiLian starts the stdio command successfully.
2. The client discovers exactly the six existing tools.
3. get_project_status succeeds.
4. record_research_activity succeeds.
5. list_research_activities reads the activity back during the same instance
   lifetime.
6. An invalid tool input returns MCP isError=true.

Only these checks establish initial stdio Function Calling connectivity. They
do not establish durable storage across FC instance recycling.
