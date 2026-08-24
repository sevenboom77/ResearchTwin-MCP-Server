<#
.SYNOPSIS
Starts the ResearchTwin MCP server from this repository's isolated environment.
#>

param(
    [ValidateSet("streamable-http", "sse")]
    [string]$Transport = "streamable-http"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
$entryPoint = Join-Path $projectRoot "server.py"

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Virtual environment not found. Create it first with: python -m venv .venv"
}

& $pythonExecutable $entryPoint --transport $Transport
exit $LASTEXITCODE
