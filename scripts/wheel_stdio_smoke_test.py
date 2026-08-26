"""Install a built wheel into a temporary environment and probe its stdio entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STDIO_SMOKE_TEST = PROJECT_ROOT / "scripts" / "stdio_smoke_test.py"


def _venv_python(environment: Path) -> Path:
    """Return the interpreter path for a temporary virtual environment."""

    return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _console_entrypoint(environment: Path) -> Path:
    """Return the installed stdio console script path for a target environment."""

    directory = environment / ("Scripts" if sys.platform == "win32" else "bin")
    filename = "researchtwin-mcp-server.exe" if sys.platform == "win32" else "researchtwin-mcp-server"
    return directory / filename


def _run_checked(command: list[str], *, description: str) -> subprocess.CompletedProcess[str]:
    """Run an isolated build-validation command and surface its complete output on failure."""

    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode == 0:
        return completed
    details = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    raise RuntimeError(f"{description} failed with exit code {completed.returncode}:\n{details or '(no output)'}")


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the wheel-validation command line."""

    parser = argparse.ArgumentParser(description="Validate a non-editable ResearchTwin wheel over MCP stdio.")
    parser.add_argument("--wheel", required=True, type=Path, help="Path to the built .whl file.")
    return parser


def main() -> None:
    """Install exactly one wheel in a new environment and run the official stdio client smoke test."""

    args = build_argument_parser().parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise SystemExit(f"Wheel file was not found or is not a .whl archive: {wheel}")

    with tempfile.TemporaryDirectory(prefix="researchtwin-mcp-wheel-smoke-") as temporary_directory:
        environment = Path(temporary_directory) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        interpreter = _venv_python(environment)
        _run_checked(
            [str(interpreter), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", str(wheel)],
            description="Non-editable wheel installation",
        )
        package_info = _run_checked(
            [str(interpreter), "-m", "pip", "show", "researchtwin-mcp-server"],
            description="Installed-package inspection",
        ).stdout
        if "Editable project location:" in package_info:
            raise RuntimeError("The wheel validation environment unexpectedly contains an editable installation.")

        entrypoint = _console_entrypoint(environment)
        if not entrypoint.is_file():
            raise RuntimeError(f"Wheel installation did not provide the stdio console entry point: {entrypoint}")
        _run_checked(
            [sys.executable, str(STDIO_SMOKE_TEST), "--command", str(entrypoint)],
            description="Installed wheel MCP stdio smoke test",
        )

    print("Wheel stdio smoke test passed: a temporary virtual environment installed the wheel non-editably.")


if __name__ == "__main__":
    main()
