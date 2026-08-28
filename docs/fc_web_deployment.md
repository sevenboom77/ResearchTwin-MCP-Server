# FC Web Function ZIP packaging

This document prepares the persistent ResearchTwin Remote MCP service for the
following deployment route:

~~~text
ResearchTwin Remote MCP
    -> FC Web Function (custom.debian12)
    -> public HTTPS /mcp
    -> BaiLian Remote MCP
    -> OpenTrek
~~~

It does not create an FC function, upload a code package, change BaiLian, or
change OpenTrek. The existing BaiLian stdio service `ResearchTwin-MCP-v2` is a
separate rollback baseline and must remain unchanged.

## Target verified by packaging rules

| Item | Target |
| --- | --- |
| FC function type | Web Function |
| Runtime | Custom Runtime Debian 12 (`custom.debian12`) |
| Architecture | x86_64 |
| Python | `/usr/bin/python3`, CPython 3.11 |
| Application command | `/usr/bin/python3 -m researchtwin_mcp.remote_entry` |
| Listener | `RESEARCHTWIN_HOST=0.0.0.0`, `RESEARCHTWIN_PORT=8000` |
| MCP transport | official MCP Python SDK 2.1.0 Streamable HTTP |
| MCP route | `/mcp` |

Alibaba Cloud's custom-runtime documentation currently lists Debian 12
(`custom.debian12`) as x86_64 and lists Python 3.11.2 at
`/usr/bin/python3`, with no extra environment setup for the interpreter. It
also states that uploaded code resides at `/code`. See the [official custom
runtime reference](https://help.aliyun.com/zh/functioncompute/custom-runtime/).

The target FC region must support `custom.debian12`. The same reference
currently lists Hangzhou, Qingdao, Beijing, Zhangjiakou, Hohhot, and Chengdu;
confirm the region availability in the actual console before selecting a
region. Prefer the existing BaiLian region only if it supports this runtime.

## Why the Windows virtual environment is not deployable

Do **not** copy `.venv/` into an FC ZIP. The local Windows environment can
contain Windows native extensions such as `.pyd`, while the FC target needs
Linux x86_64 ELF `.so` extensions for packages including `pydantic-core` and
`cryptography`.

The committed `requirements/fc-web-linux-x86_64-py311.txt` is an exact lock of
the target dependency closure. It intentionally excludes `pywin32`: MCP SDK
2.1.0 declares it only under the `sys_platform == "win32"` marker, which is
false in the Linux FC target.

## Build the ZIP from a clean source tree

Use the project Python 3.11 environment after pulling the intended commit:

~~~powershell
Set-Location C:\work\ResearchTwin-MCP-Server
git status
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe .\scripts\build_fc_web_zip.py
~~~

The builder refuses a dirty Git tree by default so the artifact name and
manifest identify an actual commit. It creates only ignored generated paths:

~~~text
build/fc-web-project-wheel/
build/fc-web-wheelhouse/
build/fc-web-staging/
dist_fc/researchtwin-mcp-fc-web-<short-git-sha>.zip
dist_fc/researchtwin-mcp-fc-web-<short-git-sha>.zip.sha256
~~~

It builds the current source wheel locally instead of using public PyPI
0.1.0, then downloads only locked `manylinux` x86_64 or pure-Python wheels
with pip target selectors for CPython 3.11. It does not run `uvx` or `pip
install` when FC starts.

## Static checks performed by the builder

The build fails rather than producing a package when it finds any of the
following:

- missing `researchtwin_mcp/remote_entry.py`, MCP SDK, Pydantic, or
  Cryptography import paths at ZIP root;
- a Windows `.pyd`, `.dll`, macOS binary, source distribution, or non-x86_64
  wheel tag;
- a native extension that is not 64-bit little-endian x86_64 ELF;
- missing native `pydantic_core/_pydantic_core*.so` or
  `cryptography/hazmat/bindings/_rust*.so`;
- `.git`, `.venv`, `.env`, `runtime_data`, `__pycache__`, or third-party test
  suite directories in staging;
- credential-shaped values such as a real `sk-` key, DashScope API-key
  literal, Bearer header value, or long quoted access-key/secret/token value;
- a ZIP above the conservative 100,000,000-byte FC upload guard.

The checked ZIP has a flat import root. It must contain
`researchtwin_mcp/remote_entry.py` directly, not under `code/` or another
extra directory. A non-secret `RESEARCHTWIN_FC_WEB_PACKAGE.json` manifest
records wheel names/tags and native-extension paths.

The FC quota documentation lists ZIP code packages uploaded through the
console, developer tools, or OSS as 100 MB in many regions and 500 MB in
selected regions. The builder uses the lower 100,000,000-byte threshold. If a
future package exceeds it, do not remove required libraries; use the FC
console's supported OSS upload or Layer route after checking the selected
region's limit. See [FC quotas and limits](https://help.aliyun.com/zh/functioncompute/limits-of-usage).

## What Windows validation proves and does not prove

Windows can prove the ZIP layout, package metadata, wheel tags, ELF headers,
absence of `.pyd`, static Python syntax, checksum, and credential-pattern
scan. It cannot load the Linux `.so` files, start Debian 12 FC, or prove a
public `/mcp` connection. Those are separate FC and public-client acceptance
steps.

## FC Web Function configuration to use after authorization

When an authorized operator creates the function in the FC console:

1. Choose **Web Function**.
2. Select **Custom Runtime -> Debian 12** and **x86_64**.
3. Upload the generated ZIP from `dist_fc/`.
4. Set the start command exactly:

   ~~~text
   /usr/bin/python3 -m researchtwin_mcp.remote_entry
   ~~~

5. Set the FC **listener port** to `8000`. FC Web Function examples use 9000
   by default, but the configured listener port must match the HTTP server's
   actual port. This service intentionally uses 8000, so leaving the UI at
   9000 would fail the platform readiness check.
6. Set environment variables:

   ~~~dotenv
   RESEARCHTWIN_HOST=0.0.0.0
   RESEARCHTWIN_PORT=8000
   RESEARCHTWIN_LOG_LEVEL=INFO
   ~~~

7. For only a short first connectivity test, set
   `RESEARCHTWIN_DATA_DIR=/tmp/researchtwin-connectivity`. It is temporary
   instance storage and must not be called long-term memory.
8. For the competition demo, configure an FC-supported persistent mount (for
   example, an authorized NAS mount) and set
   `RESEARCHTWIN_DATA_DIR=<PERSISTENT_MOUNT>/researchtwin-data`. Verify that
   directory is writable by the function and that data survives a function
   restart.
9. For initial deployment diagnostics, set the **FC function execution
   timeout** to 300 seconds if the console's Web Function default is 60. This
   is an FC request/runtime setting, **not** an MCP session-timeout fix and not
   a replacement for prebuilt dependencies.

FC documents that a custom-runtime HTTP server must bind the configured port
on a non-loopback address and start within 120 seconds. The current Remote
entry gets host and port from `RESEARCHTWIN_HOST` and `RESEARCHTWIN_PORT`, and
starts the MCP SDK's `/mcp` Streamable HTTP server without stdio or `uvx`.
See [custom-runtime HTTP server principles](https://help.aliyun.com/zh/functioncompute/principles-1)
and [creating a Web Function](https://help.aliyun.com/zh/functioncompute/creating-a-web-function).

## Public verification order

After FC exposes its HTTPS function URL, the intended MCP URL is:

~~~text
https://<FC_HOST>/mcp
~~~

Use the exact function URL displayed in the console as the base; do not invent
an extra path prefix. Before opening BaiLian or OpenTrek, use an independent
MCP client to verify, in order:

1. `initialize`;
2. `tools/list` returns exactly six Tools;
3. `get_project_status` succeeds;
4. five independent public sessions repeat the first three checks;
5. `record_research_activity` then `list_research_activities` returns the
   same record;
6. a function restart preserves the record only after persistent storage is
   configured.

`scripts/remote_stability_test.py` remains a localhost self-starting test. It
does not prove an FC URL; use a separate authorized MCP client for public
validation.

## Authentication and later platform integration

FC trigger/function authentication and application-level MCP header/Bearer
authentication are distinct decisions. The current Remote MCP has no
application-level authorization layer. Do not leave `/mcp` anonymously exposed
as a permanent configuration. A short, explicitly time-bounded no-auth smoke
test may be used only if required by the FC console workflow; configure the
approved production authentication mechanism immediately afterward. Do not put
keys in the ZIP, Git, `.env`, or logs.

Only after the independent FC URL passes all checks:

1. In BaiLian MCP management, create a **parallel** service named
   `ResearchTwin-MCP-Remote`.
2. Select the actual UI's Remote HTTP / Streamable HTTP option and supply the
   verified HTTPS `/mcp` URL.
3. Configure authentication only through fields the current UI actually
   exposes; do not guess an import JSON schema.
4. In BaiLian, verify discovery of six Tools, `get_project_status`, and the
   record/list loop.
5. Keep `ResearchTwin-MCP-v2` unchanged.
6. Only then create a parallel OpenTrek registration named
   `ResearchTwin-MCP-Remote`; do not replace `ResearchTwin-MCP-Bailian`.

Alibaba Cloud's [custom MCP documentation](https://help.aliyun.com/zh/model-studio/custom-mcp)
shows Remote MCP configuration with `streamableHttp`/SSE protocol types. Use
the actual current BaiLian UI rather than hand-written JSON fields.
