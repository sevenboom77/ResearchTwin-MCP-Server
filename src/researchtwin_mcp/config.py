"""Runtime configuration for the ResearchTwin MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_DATA_DIR = "runtime_data"
DEFAULT_LOG_LEVEL = "INFO"
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigurationError(ValueError):
    """Raised when a ResearchTwin environment variable is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated configuration sourced from ``RESEARCHTWIN_*`` variables."""

    host: str
    port: int
    data_dir: Path
    log_level: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        project_root: Path | None = None,
    ) -> "Settings":
        """Build settings without embedding network or user-specific values.

        When no explicit mapping is supplied, a repository-local ``.env`` file
        is loaded first with ``override=False``. Real process environment
        variables therefore always take precedence over local convenience
        settings. Tests and callers that pass ``environ`` remain side-effect
        free and never read a ``.env`` file.
        """

        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        if environ is None:
            load_dotenv(dotenv_path=root / ".env", override=False)
            values: Mapping[str, str] = os.environ
        else:
            values = environ

        host = values.get("RESEARCHTWIN_HOST", DEFAULT_HOST).strip()
        if not host:
            raise ConfigurationError("RESEARCHTWIN_HOST must not be empty.")

        raw_port = values.get("RESEARCHTWIN_PORT", str(DEFAULT_PORT)).strip()
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ConfigurationError("RESEARCHTWIN_PORT must be an integer between 1 and 65535.") from exc
        if not 1 <= port <= 65535:
            raise ConfigurationError("RESEARCHTWIN_PORT must be an integer between 1 and 65535.")

        raw_data_dir = values.get("RESEARCHTWIN_DATA_DIR", DEFAULT_DATA_DIR).strip()
        if not raw_data_dir:
            raise ConfigurationError("RESEARCHTWIN_DATA_DIR must not be empty.")
        data_dir = Path(raw_data_dir).expanduser()
        if not data_dir.is_absolute():
            data_dir = root / data_dir

        log_level = values.get("RESEARCHTWIN_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        if log_level not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ConfigurationError(f"RESEARCHTWIN_LOG_LEVEL must be one of: {allowed}.")

        return cls(host=host, port=port, data_dir=data_dir.resolve(), log_level=log_level)
