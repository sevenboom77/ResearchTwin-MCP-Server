<#
.SYNOPSIS
Starts the ResearchTwin MCP server from this repository's isolated environment.

.DESCRIPTION
Prints the effective connection information before starting the selected MCP
transport. This script does not change firewall, VPN, routing, or other network
settings.
#>

param(
    [ValidateSet("streamable-http", "sse")]
    [string]$Transport = "streamable-http"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
$entryPoint = Join-Path $projectRoot "server.py"
$connectionInfoScript = Join-Path $PSScriptRoot "show_connection_info.py"

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Virtual environment not found. Create it first with: python -m venv .venv"
}

if (-not (Test-Path -LiteralPath $connectionInfoScript)) {
    throw "Connection information helper not found: $connectionInfoScript"
}

& $pythonExecutable $connectionInfoScript --transport $Transport
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Starting ResearchTwin MCP Server. Press Ctrl+C to stop."
& $pythonExecutable $entryPoint --transport $Transport
exit $LASTEXITCODE
