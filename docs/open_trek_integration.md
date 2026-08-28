# OpenTrek Integration

This guide connects ResearchTwin MCP Server to an OpenTrek ResearchTwin Agent on the same Windows machine or a trusted campus/LAN network.

> Scope: this guide covers HTTP Streamable and optional SSE registration only.
> BaiLian-hosted uvx uses the separate stdio console entry point and does not
> receive an /mcp URL; see [PyPI and BaiLian uvx preparation](pypi_release.md).
> A browser open on this computer does not prove that a platform backend MCP
> request originates on this computer.

The primary integration is **Streamable HTTP**. Do not guess or hand-write an OpenTrek transportType JSON value. Use the OpenTrek registration UI to select the transport it labels STREAMABLE, then enter the matching URL.

For the non-expert, click-by-click registration workflow, see [OpenTrek registration](open_trek_registration.md). Preparing an endpoint does not prove that OpenTrek is connected: only UI discovery and an actual tool call do that.

## 1. Start the server

Open PowerShell at the repository root and activate the virtual environment:

~~~powershell
Set-Location C:\work\ResearchTwin-MCP-Server
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe .\scripts\show_connection_info.py
.\scripts\start_server.ps1 -Transport streamable-http
~~~

Equivalent direct command:

~~~powershell
python server.py --transport streamable-http
~~~

The defaults are:

| Setting | Value |
| --- | --- |
| Bind host | 0.0.0.0 |
| Port | 8000 |
| Primary transport | Streamable HTTP |
| Primary endpoint | /mcp |

The log should indicate that the service is starting with streamable-http and path /mcp. Keep that PowerShell process running while OpenTrek connects.

## 2. Find the correct Windows IPv4 address

On the Windows machine running the server:

~~~powershell
ipconfig
~~~

Use the IPv4 Address of the active Ethernet or Wi-Fi adapter on the same network as OpenTrek. Do not use:

- 127.0.0.1 when OpenTrek runs on another device;
- a VPN, virtual-machine, WSL, Bluetooth, or disconnected adapter address;
- an address from a different campus/LAN subnet.

For a shorter view, this can also help identify candidates:

~~~powershell
Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias,IPAddress,AddressState
~~~

For example only, if the actual active LAN address shown by ipconfig is 192.168.50.23, the Streamable URL is:

~~~text
http://192.168.50.23:8000/mcp
~~~

Replace that example address with the real IPv4 address reported by your own machine. The service is not automatically assigned a fixed LAN IP.

## 3. Register Streamable HTTP in OpenTrek

In OpenTrek, open **工具箱 → 注册 MCP 服务** (Toolbox → Register MCP Server, or the equivalent MCP registration page in the current UI). Create a server entry with these UI values:

| OpenTrek field | Value |
| --- | --- |
| Name | ResearchTwin-MCP |
| Description | Provides persistent research activity, advisor instruction, project status, and research report tools for the ResearchTwin agent. |
| URL | http://<LAN_IPV4>:8000/mcp |
| Transport | Select **STREAMABLE** in the UI |
| Enabled | Enabled |

Save the entry, then use OpenTrek's tool discovery or test feature. It should expose:

1. record_research_activity
2. list_research_activities
3. update_project_status
4. get_project_status
5. record_advisor_instruction
6. generate_research_report

If the OpenTrek UI produces or exports JSON, let the UI generate its actual transport field. This project intentionally does not publish a guessed transportType string.

Do not call the integration successful merely because the form was saved. It is successful only after OpenTrek discovers these tools and completes at least one real tool call.

### Same-machine connection

If OpenTrek and the MCP server run on the same machine, use:

~~~text
http://127.0.0.1:8000/mcp
~~~

This is not suitable for a separate LAN device.

## 4. Optional SSE compatibility mode

Use SSE only if the OpenTrek registration UI requires or is explicitly configured for SSE. Stop the Streamable server, then start the server in the separate SSE mode:

~~~powershell
.\scripts\start_server.ps1 -Transport sse
~~~

or:

~~~powershell
python server.py --transport sse
~~~

The SSE endpoint to register is:

~~~text
http://<LAN_IPV4>:8000/sse
~~~

The server also uses /messages/ as the SSE message endpoint. Register /sse with the client, not /messages/. Do not register both Streamable and SSE URLs as if they were interchangeable on one running transport; select one transport and its matching endpoint.

## 5. Verify the connection in layers

Use evidence from the lowest layer upward instead of assuming a browser response proves MCP works.

### A. Verify local MCP behavior

From the repository root, run:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\smoke_test.py
~~~

The smoke test starts an isolated Streamable HTTP process, discovers exactly six tools through an official MCP client, calls all six, verifies persistence and report generation, and checks representative MCP `isError` failures. It does not use `runtime_data/`.

### B. Verify the server is listening

On the host that runs the server:

~~~powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
netstat -ano | Select-String ":8000"
~~~

If RESEARCHTWIN_PORT was changed, substitute that port number.

### C. Verify LAN TCP reachability

From another device on the same trusted LAN, run:

~~~powershell
Test-NetConnection <LAN_IPV4> -Port 8000
~~~

TcpTestSucceeded should be True before investigating MCP-specific behavior. Then use OpenTrek's test/discovery action and confirm that all six tools appear.

An ordinary browser or curl request is not a complete MCP protocol test. The /mcp endpoint expects an MCP client negotiation, so a generic HTTP status alone should not be treated as tool-discovery evidence.

## 6. VPN and campus-network guidance

An earlier integration symptom was that a knowledge-base tool request remained Pending when a VPN was active and recovered after the VPN was manually disconnected. That is a diagnostic clue, not a rule that every VPN causes a problem.

When an OpenTrek request remains Pending for an unusually long time during campus-network integration, check in this order:

1. Confirm the server process is still listening on the expected port (8000 by default).
2. Run the local MCP smoke test or a local OpenTrek connection test first.
3. Identify the active Ethernet or Wi-Fi LAN IPv4 address; `show_connection_info.py` prints `UNKNOWN` rather than guessing when it is ambiguous.
4. Inspect VPN state and split-tunnel policy, including whether the VPN blocks local-LAN access.
5. Inspect system routing with `route print` or `Get-NetRoute -AddressFamily IPv4`.
6. Inspect Windows Firewall only after the previous checks isolate inbound filtering as a plausible cause.

Do **not** automatically disable a VPN. If organizational policy permits a manual comparison test, the user may disconnect it temporarily, run the same connection test, record the result, and reconnect it. If a VPN must remain active, consult the relevant network policy or administrator about local-LAN access and routing.

Useful read-only diagnostics:

~~~powershell
route print
Get-NetRoute -AddressFamily IPv4
Get-NetTCPConnection -LocalPort 8000
Test-NetConnection <LAN_IPV4> -Port 8000
~~~

## 7. Windows Firewall guidance

This project never changes Windows Firewall rules automatically. First prove that the server works locally and that a remote client cannot reach TCP port 8000.

Inspect the active network profile before considering a rule:

~~~powershell
Get-NetConnectionProfile
~~~

If you have determined that an inbound firewall rule is required, an administrator may choose to run the following command manually in an elevated PowerShell window. It opens only the configured TCP port on the Private profile:

~~~powershell
New-NetFirewallRule -DisplayName "ResearchTwin MCP Server TCP 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private
~~~

If RESEARCHTWIN_PORT differs from 8000, replace the port in both the display name and LocalPort value. Verify from the other LAN device:

~~~powershell
Test-NetConnection <LAN_IPV4> -Port 8000
~~~

Remove the rule later, when the demo or local integration no longer needs it:

~~~powershell
Remove-NetFirewallRule -DisplayName "ResearchTwin MCP Server TCP 8000"
~~~

Do not broaden the rule to public networks or disable the firewall as a workaround.

## Troubleshooting

| Symptom | Check | Likely correction |
| --- | --- | --- |
| OpenTrek cannot discover tools | URL and transport selection | Use STREAMABLE with the LAN_IPV4 /mcp URL, then save/reload the entry. |
| An SSE client does not connect | Server startup mode and endpoint | Start with --transport sse and register /sse, not /messages/. |
| Local smoke test passes but remote TCP fails | LAN address, VPN route, firewall | Use Test-NetConnection, then inspect routing and firewall in that order. |
| Request remains Pending | VPN policy, system routes, campus-network reachability | Compare diagnostics with the permitted VPN state; do not turn VPN off automatically. |
| Connection is refused | Process listener and port | Confirm the server process and Get-NetTCPConnection output; verify the configured port. |
| Generic HTTP request looks odd | Test method | Use the MCP smoke test or OpenTrek tool discovery rather than treating curl as a full protocol check. |

## Network safety

Binding to 0.0.0.0 is for trusted LAN demonstrations. Do not expose the default development service through public port forwarding. Before any non-LAN deployment, add appropriate authentication, authorization, encryption, reverse-proxy, and network controls.
