"""Protocol-level smoke coverage for the dedicated stdio console entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STDIO_SMOKE_TEST = PROJECT_ROOT / "scripts" / "stdio_smoke_test.py"


def test_stdio_console_entrypoint_smoke() -> None:
    """Use the official MCP client smoke script against the installed console entry point."""

    completed = subprocess.run(
        [sys.executable, str(STDIO_SMOKE_TEST)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    details = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    assert completed.returncode == 0, details
    assert "MCP stdio smoke test passed" in completed.stdout
