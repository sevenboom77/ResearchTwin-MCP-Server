"""Unit coverage for FC ZIP build safety rules without downloading or packaging."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_fc_web_zip.py"


def _load_builder_module():
    """Load the standalone build script as a module for side-effect-free tests."""

    module_name = "researchtwin_fc_web_builder_test"
    spec = importlib.util.spec_from_file_location(module_name, BUILD_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load FC build script: {BUILD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_fc_linux_lock_is_exact_and_excludes_windows_only_pywin32() -> None:
    """The committed lock must cover the target native dependencies, not Windows ones."""

    builder = _load_builder_module()
    pins = builder._read_locked_requirements(builder.DEFAULT_REQUIREMENTS_PATH)

    assert pins["mcp"] == "2.1.0"
    assert "pydantic-core" in pins
    assert "cryptography" in pins
    assert "pywin32" not in pins
    assert all("*" not in version and ">" not in version and "<" not in version for version in pins.values())


def test_fc_wheel_tag_validation_rejects_a_windows_wheel() -> None:
    """A Windows wheel cannot silently enter an x86_64 Debian package."""

    builder = _load_builder_module()
    windows_wheel = builder.WheelMetadata(
        path=Path("pydantic_core-0-cp311-cp311-win_amd64.whl"),
        name="pydantic-core",
        version="0",
        tags=("cp311-cp311-win_amd64",),
        requirements=(),
    )

    with pytest.raises(builder.BuildError, match="Non-FC platform"):
        builder._validate_wheel_tags(windows_wheel)


def test_fc_elf_validator_accepts_only_x86_64_elf64(tmp_path: Path) -> None:
    """Native extension checks inspect ELF headers without trying to execute Linux code on Windows."""

    builder = _load_builder_module()
    valid = bytearray(20)
    valid[:4] = b"\x7fELF"
    valid[4] = 2
    valid[5] = 1
    valid[18:20] = (62).to_bytes(2, byteorder="little")
    valid_path = tmp_path / "extension.so"
    valid_path.write_bytes(valid)
    builder._validate_elf_x86_64(valid_path)

    invalid_path = tmp_path / "windows.pyd"
    invalid_path.write_bytes(b"MZ")
    with pytest.raises(builder.BuildError, match="not an ELF"):
        builder._validate_elf_x86_64(invalid_path)
