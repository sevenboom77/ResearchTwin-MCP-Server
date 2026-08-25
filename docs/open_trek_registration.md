# Register ResearchTwin MCP in OpenTrek

This guide prepares a ResearchTwin MCP Server for OpenTrek on the same machine or a trusted campus/LAN network. It does not claim that a connection already exists. End-to-end integration is confirmed only after the OpenTrek UI discovers tools and successfully calls one.

## 1. Start the MCP server

Open PowerShell in the actual project directory:

~~~powershell
Set-Location C:\work\ResearchTwin-MCP-Server
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe .\scripts\show_connection_info.py
.\scripts\start_server.ps1 -Transport streamable-http
~~~

The normal Streamable HTTP endpoint is `/mcp` on port `8000`. Keep this PowerShell window open while registering the service.

The connection-information script is read-only. It prints `UNKNOWN` when it cannot safely select one LAN IPv4 address. In that case, run `ipconfig` and use the IPv4 address of the active Ethernet or Wi-Fi adapter on the network that OpenTrek can reach. Do not use a VPN, WSL, Bluetooth, virtual-machine, disconnected-adapter, or another network's address.

## 2. Register the service in OpenTrek

In OpenTrek, open **工具箱 -> 注册 MCP 服务** (Toolbox -> Register MCP Server), or the equivalent MCP registration page in the version you are using. Create an enabled server entry with these values:

| Field | Value |
| --- | --- |
| Service name | `ResearchTwin-MCP` |
| Service description | Provides persistent research activity, advisor instruction, project status, and research report tools for the ResearchTwin agent. |
| Transport | Select `STREAMABLE` in the UI |
| URL, same machine | `http://127.0.0.1:8000/mcp` |
| URL, trusted campus/LAN | `http://<LAN_IPV4>:8000/mcp` |

Use the Streamable HTTP `/mcp` URL. Do not register `/messages/`; it is only the SSE message endpoint. Use `/sse` only when you deliberately stop the Streamable server and restart it with `-Transport sse` for an SSE-only client.

Do not invent a `transportType` JSON value. Select the UI's `STREAMABLE` option and let OpenTrek manage its own registration format.

## 3. Verify instead of assuming success

After saving the entry:

1. Run OpenTrek's tool discovery or test action.
2. Confirm that it lists all six tools: `record_research_activity`, `list_research_activities`, `update_project_status`, `get_project_status`, `record_advisor_instruction`, and `generate_research_report`.
3. Make one real tool call, preferably the read-only `get_project_status` first.
4. Record the result. Only then describe the OpenTrek integration as successful.

If discovery or the call does not work, the most likely gap is network reachability or UI configuration. Do not report a successful OpenTrek integration without that evidence.

## 4. Choose the reachable deployment path

### Case A: OpenTrek can reach the Windows machine on the same campus/LAN

Use the machine's active LAN IPv4 URL directly:

~~~text
http://<LAN_IPV4>:8000/mcp
~~~

First validate TCP reachability from the OpenTrek side or an equivalent client:

~~~powershell
Test-NetConnection <LAN_IPV4> -Port 8000
~~~

### Case B: OpenTrek's backend cannot reach a user's LAN address

This is a network-reachability limitation, not proof that the MCP implementation is broken. The next deployment must be an authorized, reachable environment such as a school server, ECS, or an organization-approved compliant tunnel.

Do not independently use ngrok, frp, or Cloudflare Tunnel to bypass the limitation. Such a deployment decision requires the relevant security, network, and data-handling approval.

## 5. Pending requests, VPNs, and firewall checks

If a request remains Pending for a long time, diagnose in this order:

1. Confirm that the MCP server is listening on its configured port.
2. Confirm a local connection or run the repository smoke test.
3. Confirm the correct active LAN IPv4 address.
4. Inspect VPN state and local-LAN/split-tunnel policy.
5. Inspect system routes.
6. Inspect Windows Firewall last.

An earlier campus integration observation was that a knowledge-base request remained Pending while a VPN was active and recovered after the VPN was manually disconnected. This is only a diagnostic clue. Never disable a VPN automatically; follow organizational policy and make any manual comparison only with permission.

For the full diagnostic commands and firewall safety guidance, see [OpenTrek integration](open_trek_integration.md).

## Security boundary

`127.0.0.1` is for local testing. The default `0.0.0.0` bind is appropriate only for a trusted LAN demonstration. This development server has no application-level authentication, so it must not be exposed to the public internet. A broader deployment needs HTTPS, authentication, authorization, a reverse proxy, and appropriate network controls.
