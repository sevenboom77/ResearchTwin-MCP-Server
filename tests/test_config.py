"""Tests for environment-backed configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from researchtwin_mcp.config import ConfigurationError, Settings


def test_settings_resolve_relative_data_dir(tmp_path: Path) -> None:
    """Relative data locations are anchored to the repository root."""

    settings = Settings.from_env(
        {
            "RESEARCHTWIN_HOST": "0.0.0.0",
            "RESEARCHTWIN_PORT": "8010",
            "RESEARCHTWIN_DATA_DIR": "data",
            "RESEARCHTWIN_LOG_LEVEL": "debug",
        },
        project_root=tmp_path,
    )

    assert settings.host == "0.0.0.0"
    assert settings.port == 8010
    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("name", "value"),
    [("RESEARCHTWIN_PORT", "not-a-port"), ("RESEARCHTWIN_PORT", "70000"), ("RESEARCHTWIN_LOG_LEVEL", "TRACE")],
)
def test_invalid_settings_are_rejected(name: str, value: str) -> None:
    """Invalid environment values fail before server startup."""

    settings = {name: value}
    with pytest.raises(ConfigurationError):
        Settings.from_env(settings)
