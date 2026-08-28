"""Regression coverage for the dedicated persistent Remote MCP entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REMOTE_STABILITY_SCRIPT = PROJECT_ROOT / "scripts" / "remote_stability_test.py"


def test_remote_entrypoint_completes_five_independent_streamable_http_sessions() -> None:
    """A direct pre-installed Python process must serve five new MCP sessions."""

    completed = subprocess.run(
        [sys.executable, str(REMOTE_STABILITY_SCRIPT), "--rounds", "5"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    details = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    assert completed.returncode == 0, details
    assert "summary: 5/5 initialize, tools/list, get_project_status" in completed.stdout
