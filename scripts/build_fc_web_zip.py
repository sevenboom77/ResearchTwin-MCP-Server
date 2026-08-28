"""Build a Linux x86_64 CPython 3.12 ZIP for an FC Web Function.

The development host can be Windows, so this builder never copies its virtual
environment.  It instead downloads the locked manylinux/pure-Python wheel
closure for the FC Debian 11 target, installs those wheels into a clean staging
directory, and performs structural and binary-format checks before archiving.

The resulting ZIP is an import root: FC can start it with::

    python3 -m researchtwin_mcp.remote_entry

This script deliberately does not create, configure, or invoke a cloud
function.  It cannot prove that Linux will load the native extensions or that
the final public endpoint is reachable; it only validates deployable artifact
structure from the Windows build host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from email import message_from_bytes
from pathlib import Path
from typing import Iterable

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
BUILD_ROOT = PROJECT_ROOT / "build"
STAGING_DIR = BUILD_ROOT / "fc-web-staging"
WHEELHOUSE_DIR = BUILD_ROOT / "fc-web-wheelhouse"
PROJECT_WHEEL_DIR = BUILD_ROOT / "fc-web-project-wheel"
DIST_FC_DIR = PROJECT_ROOT / "dist_fc"
DEFAULT_REQUIREMENTS_PATH = PROJECT_ROOT / "requirements" / "fc-web-linux-x86_64-py312.txt"

TARGET_RUNTIME = "FC custom.debian11"
TARGET_OS = "Debian 11"
TARGET_ARCHITECTURE = "x86_64"
TARGET_PYTHON = "3.12"
TARGET_IMPLEMENTATION = "cp"
TARGET_ABI = "cp312"
TARGET_PLATFORMS = (
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_28_x86_64",
)
EXPECTED_MCP_VERSION = "2.1.0"
DEFAULT_MAX_ZIP_BYTES = 100_000_000
MANIFEST_NAME = "RESEARCHTWIN_FC_WEB_PACKAGE.json"

REQUIRED_PACKAGE_PATHS = (
    Path("researchtwin_mcp") / "remote_entry.py",
    Path("researchtwin_mcp") / "server.py",
    Path("mcp") / "__init__.py",
    Path("pydantic") / "__init__.py",
    Path("pydantic_core"),
    Path("cryptography") / "__init__.py",
)
REQUIRED_NATIVE_PREFIXES = {
    "pydantic-core": "pydantic_core/_pydantic_core",
    "cryptography": "cryptography/hazmat/bindings/_rust",
}
FORBIDDEN_PATH_PARTS = frozenset({".git", ".venv", "tests", "runtime_data", "__pycache__"})
PRUNABLE_TEST_DIRECTORY_NAMES = frozenset({"test", "tests"})
FORBIDDEN_SUFFIXES = frozenset({".pyd", ".dll", ".dylib", ".pyc", ".pyo"})
CP311_MARKERS = ("cpython-311", "cp311")
WINDOWS_BINARY_SUFFIXES = frozenset({".pyd", ".dll"})
TEXT_FILE_SUFFIXES = frozenset({".cfg", ".ini", ".json", ".md", ".py", ".pyi", ".rst", ".toml", ".txt", ".yaml", ".yml"})


class BuildError(RuntimeError):
    """Raised for an unsafe or incomplete FC deployment artifact."""


@dataclass(frozen=True, slots=True)
class WheelMetadata:
    """Relevant metadata read directly from a wheel archive."""

    path: Path
    name: str
    version: str
    tags: tuple[str, ...]
    requirements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StaticValidation:
    """Static results that can be truthfully established on the build host."""

    native_extensions: tuple[str, ...]
    checked_python_files: int
    checked_text_files: int
    cp311_file_count: int
    windows_pyd_dll_count: int


def _canonical_distribution_name(name: str) -> str:
    """Apply the normalized distribution-name comparison used by Python tools."""

    return re.sub(r"[-_.]+", "-", name).lower()


def _require_inside_project(path: Path, *, expected_parent: Path | None = None) -> Path:
    """Resolve a generated path and reject a broad or unexpected deletion target."""

    resolved = path.resolve()
    root = PROJECT_ROOT.resolve()
    if not resolved.is_relative_to(root):
        raise BuildError(f"Generated path escapes the project root: {resolved}")
    if expected_parent is not None and resolved.parent != expected_parent.resolve():
        raise BuildError(f"Generated path has an unexpected parent: {resolved}")
    return resolved


def _clean_generated_directory(path: Path) -> None:
    """Recreate one explicitly named, project-local staging directory."""

    resolved = _require_inside_project(path, expected_parent=BUILD_ROOT)
    if resolved.name not in {"fc-web-staging", "fc-web-wheelhouse", "fc-web-project-wheel"}:
        raise BuildError(f"Refusing to clean an unexpected build directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=False)


def _run(command: list[str]) -> None:
    """Echo and run a build command without involving a shell."""

    print("$ " + " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def _git_output(*arguments: str) -> str:
    """Read Git state without changing it."""

    completed = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def _source_revision(*, allow_dirty: bool) -> tuple[str, str, bool]:
    """Return full/short revision and fail by default for an ambiguous source tree."""

    full_revision = _git_output("rev-parse", "HEAD")
    short_revision = _git_output("rev-parse", "--short=12", "HEAD")
    dirty = bool(_git_output("status", "--porcelain", "--untracked-files=all"))
    if dirty and not allow_dirty:
        raise BuildError(
            "Git working tree is not clean. Commit the FC packaging changes first, or use "
            "--allow-dirty only for a disposable local test artifact."
        )
    return full_revision, short_revision, dirty


def _read_project_metadata() -> tuple[str, str, list[str], str]:
    """Read packaging values and fail safely if the target contract changes."""

    with PYPROJECT_PATH.open("rb") as file_handle:
        document = tomllib.load(file_handle)
    project = document.get("project")
    if not isinstance(project, dict):
        raise BuildError("pyproject.toml has no [project] table.")

    name = project.get("name")
    version = project.get("version")
    dependencies = project.get("dependencies")
    requires_python = project.get("requires-python")
    if not isinstance(name, str) or not isinstance(version, str):
        raise BuildError("pyproject.toml must provide string project name and version.")
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise BuildError("pyproject.toml dependencies must be a list of strings.")
    if not isinstance(requires_python, str):
        raise BuildError("pyproject.toml must provide requires-python.")
    if requires_python != ">=3.11":
        raise BuildError(
            "The FC package validator currently expects requires-python == '>=3.11'. "
            "Review and update the CPython 3.12 compatibility check before changing it."
        )
    if f"mcp=={EXPECTED_MCP_VERSION}" not in dependencies:
        raise BuildError(
            f"The FC Linux lock is written for mcp=={EXPECTED_MCP_VERSION}; "
            "update the lock and validator before changing the MCP SDK requirement."
        )
    return name, version, dependencies, requires_python


def _read_locked_requirements(path: Path) -> dict[str, str]:
    """Read exact distribution pins; source distributions and unpinned inputs are forbidden."""

    if not path.is_file():
        raise BuildError(f"Locked FC requirements file was not found: {path}")

    parsed: dict[str, str] = {}
    pin_pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._!+\-]*)$")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if not line:
            continue
        match = pin_pattern.fullmatch(line)
        if match is None:
            raise BuildError(f"Requirements line {line_number} must be an exact name==version pin: {raw_line!r}")
        name, version = match.groups()
        canonical_name = _canonical_distribution_name(name)
        if canonical_name in parsed:
            raise BuildError(f"Requirements file repeats distribution {name!r}.")
        if canonical_name == "pywin32":
            raise BuildError("pywin32 is a Windows-only dependency and is forbidden from the Linux FC ZIP.")
        parsed[canonical_name] = version

    if parsed.get("mcp") != EXPECTED_MCP_VERSION:
        raise BuildError(f"Requirements file must pin mcp=={EXPECTED_MCP_VERSION}.")
    for required in ("pydantic", "pydantic-core", "cryptography", "python-dotenv"):
        if required not in parsed:
            raise BuildError(f"Requirements file is missing required FC dependency {required!r}.")
    return parsed


def _cross_platform_pip_arguments() -> list[str]:
    """Return strict pip selectors for the Linux FC artifact, never Windows wheels."""

    arguments = ["--only-binary=:all:"]
    for platform in TARGET_PLATFORMS:
        arguments.extend(["--platform", platform])
    arguments.extend(
        [
            "--implementation",
            TARGET_IMPLEMENTATION,
            "--python-version",
            TARGET_PYTHON,
            "--abi",
            TARGET_ABI,
        ]
    )
    return arguments


def _build_project_wheel() -> Path:
    """Build the current source into a pure project wheel without PyPI publication."""

    _clean_generated_directory(PROJECT_WHEEL_DIR)
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(PROJECT_WHEEL_DIR),
        ]
    )
    wheels = sorted(PROJECT_WHEEL_DIR.glob("*.whl"))
    if len(wheels) != 1:
        raise BuildError(f"Expected exactly one locally-built project wheel, found {len(wheels)}.")
    return wheels[0]


def _download_target_wheels(requirements_path: Path) -> list[Path]:
    """Download only locked, binary Linux/pure-Python wheels for FC."""

    _clean_generated_directory(WHEELHOUSE_DIR)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--dest",
            str(WHEELHOUSE_DIR),
            "--requirement",
            str(requirements_path),
            "--no-deps",
            "--disable-pip-version-check",
            "--no-input",
            *_cross_platform_pip_arguments(),
        ]
    )
    wheels = sorted(WHEELHOUSE_DIR.glob("*.whl"))
    if not wheels:
        raise BuildError("pip download produced no wheels for the FC wheelhouse.")
    non_wheels = [path.name for path in WHEELHOUSE_DIR.iterdir() if path.is_file() and path.suffix != ".whl"]
    if non_wheels:
        raise BuildError(f"FC wheelhouse contains non-wheel artifacts: {non_wheels}")
    return wheels


def _read_wheel_metadata(path: Path) -> WheelMetadata:
    """Read a wheel's package identity and advertised compatibility tags."""

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        wheel_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
        if len(metadata_names) != 1 or len(wheel_names) != 1:
            raise BuildError(f"Wheel metadata is malformed: {path.name}")
        metadata = message_from_bytes(archive.read(metadata_names[0]))
        wheel_file = message_from_bytes(archive.read(wheel_names[0]))

    name = metadata.get("Name")
    version = metadata.get("Version")
    tags = tuple(wheel_file.get_all("Tag") or ())
    if not isinstance(name, str) or not isinstance(version, str) or not tags:
        raise BuildError(f"Wheel lacks Name, Version, or Tag metadata: {path.name}")
    return WheelMetadata(
        path=path,
        name=name,
        version=version,
        tags=tags,
        requirements=tuple(metadata.get_all("Requires-Dist") or ()),
    )


def _abi3_native_files(path: Path) -> tuple[str, ...]:
    """Return native files in a wheel so an ABI3 tag can be checked honestly."""

    with zipfile.ZipFile(path) as archive:
        return tuple(
            name
            for name in archive.namelist()
            if name.lower().endswith(".so") and ".dist-info/" not in name.lower()
        )


def _validate_wheel_tags(metadata: WheelMetadata) -> None:
    """Reject non-Linux wheels and Python tags that cannot run on CPython 3.12.

    A wheel tagged ``cp311-abi3`` is acceptable for the Debian 11 target only
    when every native extension is explicitly ABI3 (for example
    ``_rust.abi3.so``).  A CPython-specific ``cp311`` wheel or
    ``cpython-311`` extension is never accepted.
    """

    for tag in metadata.tags:
        try:
            python_tag, abi_tag, platform_tag = tag.split("-", maxsplit=2)
        except ValueError as exc:
            raise BuildError(f"Malformed wheel compatibility tag {tag!r} in {metadata.path.name}") from exc
        normalized = platform_tag.lower()
        if normalized == "any":
            if python_tag not in {"py3", "py312"} or abi_tag != "none":
                raise BuildError(f"Wheel Python tag is not CPython 3.12 compatible: {tag!r} in {metadata.path.name}")
            continue
        if "win" in normalized or "macosx" in normalized or "musllinux" in normalized:
            raise BuildError(f"Non-FC platform wheel tag {tag!r} in {metadata.path.name}")
        if "linux" not in normalized or "x86_64" not in normalized:
            raise BuildError(f"Wheel tag is not Linux x86_64 compatible: {tag!r} in {metadata.path.name}")

        if python_tag == "cp312" and abi_tag in {"cp312", "abi3"}:
            if abi_tag == "abi3":
                native_files = _abi3_native_files(metadata.path)
                if not native_files or any(
                    ".abi3.so" not in name.lower() or "cpython-311" in name.lower() for name in native_files
                ):
                    raise BuildError(
                        f"ABI3 wheel does not contain only explicit ABI3 extensions: {metadata.path.name}"
                    )
            continue

        if python_tag.startswith("cp") and python_tag[2:].isdigit() and abi_tag == "abi3":
            minimum_python = int(python_tag[2:])
            if minimum_python <= 312:
                native_files = _abi3_native_files(metadata.path)
                if native_files and all(
                    ".abi3.so" in name.lower() and "cpython-311" not in name.lower() for name in native_files
                ):
                    continue
        raise BuildError(f"Wheel Python tag is not CPython 3.12 compatible: {tag!r} in {metadata.path.name}")


def _validate_wheelhouse(wheels: Iterable[Path], expected_pins: dict[str, str]) -> tuple[WheelMetadata, ...]:
    """Ensure every exact lock pin was downloaded as a permitted wheel exactly once."""

    metadata = tuple(_read_wheel_metadata(path) for path in wheels)
    found: dict[str, WheelMetadata] = {}
    for item in metadata:
        _validate_wheel_tags(item)
        canonical_name = _canonical_distribution_name(item.name)
        if canonical_name == "pywin32" or "win" in item.path.name.lower():
            raise BuildError(f"Windows dependency entered the FC wheelhouse: {item.path.name}")
        if canonical_name in found:
            raise BuildError(f"Multiple wheels for one locked distribution: {item.name}")
        found[canonical_name] = item

    missing = sorted(name for name in expected_pins if name not in found)
    unexpected = sorted(name for name in found if name not in expected_pins)
    version_mismatches = sorted(
        f"{name}: expected {expected_pins[name]}, received {found[name].version}"
        for name in expected_pins.keys() & found.keys()
        if expected_pins[name] != found[name].version
    )
    if missing or unexpected or version_mismatches:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        if version_mismatches:
            details.append(f"version_mismatches={version_mismatches}")
        raise BuildError("FC wheelhouse does not match the committed lock: " + "; ".join(details))
    return metadata


def _fc_marker_environment() -> dict[str, str]:
    """Return the PEP 508 environment represented by FC Debian 11 Python 3.12."""

    environment = default_environment()
    environment.update(
        {
            "implementation_name": "cpython",
            "implementation_version": "3.12.4",
            "os_name": "posix",
            "platform_machine": TARGET_ARCHITECTURE,
            "platform_python_implementation": "CPython",
            "platform_system": "Linux",
            "python_full_version": "3.12.4",
            "python_version": TARGET_PYTHON,
            "sys_platform": "linux",
            "extra": "",
        }
    )
    return environment


def _validate_linux_dependency_closure(metadata: Iterable[WheelMetadata]) -> None:
    """Verify all non-extra target dependencies resolve from the locked wheel set.

    Optional extras are intentionally not inferred from arbitrary dependency
    metadata.  The MCP-selected crypto dependency is pinned explicitly in the
    committed lock and checked separately as a required package.
    """

    items = tuple(metadata)
    installed = {_canonical_distribution_name(item.name): item for item in items}
    environment = _fc_marker_environment()
    missing: list[str] = []
    incompatible: list[str] = []
    malformed: list[str] = []

    for item in items:
        for raw_requirement in item.requirements:
            try:
                requirement = Requirement(raw_requirement)
            except Exception as exc:  # pragma: no cover - malformed third-party metadata is rare but unsafe
                malformed.append(f"{item.name}: {raw_requirement!r} ({type(exc).__name__})")
                continue
            if requirement.marker is not None and not requirement.marker.evaluate(environment):
                continue
            dependency_name = _canonical_distribution_name(requirement.name)
            dependency = installed.get(dependency_name)
            if dependency is None:
                missing.append(f"{item.name} requires {raw_requirement}")
                continue
            if requirement.specifier and Version(dependency.version) not in requirement.specifier:
                incompatible.append(
                    f"{item.name} requires {raw_requirement}, found {dependency.name}=={dependency.version}"
                )

    if malformed or missing or incompatible:
        details = []
        if malformed:
            details.append("malformed=" + "; ".join(sorted(malformed)))
        if missing:
            details.append("missing=" + "; ".join(sorted(missing)))
        if incompatible:
            details.append("incompatible=" + "; ".join(sorted(incompatible)))
        raise BuildError("Linux wheel dependency closure is incomplete: " + " | ".join(details))


def _install_wheels_into_staging(wheels: Iterable[Path]) -> None:
    """Install a prevalidated wheel set without allowing pip to resolve Windows markers."""

    _clean_generated_directory(STAGING_DIR)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(STAGING_DIR),
            "--no-deps",
            "--no-compile",
            "--disable-pip-version-check",
            "--no-input",
            *_cross_platform_pip_arguments(),
            *(str(path) for path in wheels),
        ]
    )


def _prune_non_runtime_test_directories() -> tuple[str, ...]:
    """Remove only test-suite directories copied from third-party wheels.

    These directories are not needed to run the Remote MCP service and would
    violate the deployment package boundary.  The operation is confined to the
    explicitly generated staging directory; repository tests are never touched.
    """

    removed: list[str] = []
    directories = sorted(
        (path for path in STAGING_DIR.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in directories:
        if path.name.lower() not in PRUNABLE_TEST_DIRECTORY_NAMES:
            continue
        relative = path.relative_to(STAGING_DIR)
        if not relative.parts:
            raise BuildError("Refusing to remove the staging root.")
        shutil.rmtree(path)
        removed.append(relative.as_posix())
    return tuple(sorted(removed))


def _validate_elf_x86_64(path: Path) -> None:
    """Verify a native extension is ELF64 little-endian x86_64 without loading it."""

    header = path.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise BuildError(f"Native extension is not an ELF binary: {path}")
    if header[4] != 2 or header[5] != 1:
        raise BuildError(f"Native extension is not a 64-bit little-endian ELF file: {path}")
    machine = int.from_bytes(header[18:20], byteorder="little")
    if machine != 62:
        raise BuildError(f"Native extension is not x86_64 ELF (e_machine={machine}): {path}")


def _is_text_candidate(path: Path) -> bool:
    """Select source and package metadata where a credential-shaped value can be checked safely."""

    return path.suffix.lower() in TEXT_FILE_SUFFIXES or path.name in {"METADATA", "WHEEL", "entry_points.txt"}


def _scan_for_credentials(paths: Iterable[Path]) -> int:
    """Reject credential-shaped values while never printing a matched secret."""

    patterns = {
        "OpenAI-style key": re.compile(r"(?i)\bsk-[a-z0-9_-]{20,}"),
        "DashScope API-key literal": re.compile(
            r"(?im)\bDASHSCOPE_API_KEY\s*=\s*[\"'](?!<|\$\{|YOUR_|REPLACE_)"
            r"[A-Za-z0-9._~+/-]{12,}[\"']"
        ),
        "Authorization bearer value": re.compile(r"(?i)Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/-]{16,}"),
        "cloud access-key pattern": re.compile(r"\b(?:AKIA|ASIA|LTAI)[A-Za-z0-9]{12,}\b"),
        "quoted credential literal": re.compile(
            r"(?im)\b(?:access[_-]?key(?:[_-]?(?:id|secret))?|secret(?:[_-]?key)?|api[_-]?key|token)"
            r"\s*[=:]\s*[\"'](?!<|\$\{|YOUR_|REPLACE_)[A-Za-z0-9._~+/-]{16,}[\"']"
        ),
    }
    matches: list[str] = []
    checked = 0
    for path in paths:
        if not _is_text_candidate(path):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        checked += 1
        for label, pattern in patterns.items():
            if pattern.search(content):
                matches.append(f"{label} in {path.relative_to(STAGING_DIR).as_posix()}")
    if matches:
        raise BuildError("Credential-shaped value detected in the staging package (values redacted): " + "; ".join(matches))
    return checked


def _validate_staging() -> StaticValidation:
    """Run all Windows-safe checks over the flattened FC ZIP import root."""

    for required in REQUIRED_PACKAGE_PATHS:
        if not (STAGING_DIR / required).exists():
            raise BuildError(f"Staging package is missing required import path: {required.as_posix()}")

    native_extensions: list[str] = []
    python_files: list[Path] = []
    cp311_files: list[str] = []
    windows_pyd_dll_files: list[str] = []
    all_files = sorted(path for path in STAGING_DIR.rglob("*") if path.is_file())
    for path in all_files:
        relative = path.relative_to(STAGING_DIR)
        relative_name = relative.as_posix()
        relative_lower = relative_name.lower()
        if any(part.lower() in FORBIDDEN_PATH_PARTS for part in relative.parts):
            raise BuildError(f"Forbidden development or runtime path entered staging: {relative.as_posix()}")
        if any(marker in relative_lower for marker in CP311_MARKERS):
            cp311_files.append(relative_name)
        if path.suffix.lower() in WINDOWS_BINARY_SUFFIXES:
            windows_pyd_dll_files.append(relative_name)
        if path.name.lower() == ".env" or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise BuildError(f"Forbidden Windows/cache/secret file entered staging: {relative_name}")
        if path.suffix.lower() == ".so":
            _validate_elf_x86_64(path)
            native_extensions.append(relative_name)
        if path.suffix.lower() == ".py":
            try:
                compile(path.read_text(encoding="utf-8"), str(relative), "exec")
            except (SyntaxError, UnicodeDecodeError) as exc:
                raise BuildError(f"Pure-Python static compile failed for {relative_name}: {exc}") from exc
            python_files.append(path)

    if cp311_files:
        raise BuildError("CPython 3.11-specific files entered the CPython 3.12 staging package: " + ", ".join(cp311_files))

    for distribution, prefix in REQUIRED_NATIVE_PREFIXES.items():
        if not any(path.startswith(prefix) for path in native_extensions):
            raise BuildError(f"Expected a Linux native extension for {distribution}: {prefix}*.so")
    if not native_extensions:
        raise BuildError("Expected native Linux wheels for pydantic-core and cryptography, but found no .so files.")

    text_files = _scan_for_credentials(all_files)
    return StaticValidation(
        native_extensions=tuple(native_extensions),
        checked_python_files=len(python_files),
        checked_text_files=text_files,
        cp311_file_count=len(cp311_files),
        windows_pyd_dll_count=len(windows_pyd_dll_files),
    )


def _write_manifest(
    *,
    project_name: str,
    project_version: str,
    full_revision: str,
    source_dirty: bool,
    wheel_metadata: Iterable[WheelMetadata],
    validation: StaticValidation,
    pruned_test_directories: Iterable[str],
) -> None:
    """Record enough non-secret provenance to review the ZIP without executing it."""

    manifest = {
        "format": 1,
        "project": {"name": project_name, "version": project_version},
        "source_revision": full_revision,
        "source_tree_clean": not source_dirty,
        "target": {
            "runtime": TARGET_RUNTIME,
            "operating_system": TARGET_OS,
            "architecture": TARGET_ARCHITECTURE,
            "python": TARGET_PYTHON,
            "startup_command": "python3 -m researchtwin_mcp.remote_entry",
            "listener_port": 8000,
            "mcp_path": "/mcp",
            "manylinux_platform_tags": list(TARGET_PLATFORMS),
        },
        "wheels": [
            {
                "name": item.name,
                "version": item.version,
                "filename": item.path.name,
                "tags": list(item.tags),
            }
            for item in sorted(wheel_metadata, key=lambda item: _canonical_distribution_name(item.name))
        ],
        "native_extensions": list(validation.native_extensions),
        "pruned_non_runtime_test_directories": sorted(pruned_test_directories),
        "static_checks": {
            "windows_pyd_rejected": True,
            "windows_pyd_dll_file_count": validation.windows_pyd_dll_count,
            "cp311_file_count": validation.cp311_file_count,
            "linux_elf_x86_64_checked": True,
            "credential_shaped_values_absent": True,
            "python_source_files_compiled": validation.checked_python_files,
            "text_files_scanned": validation.checked_text_files,
        },
    }
    (STAGING_DIR / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _archive_staging(destination: Path) -> None:
    """Create a flat, deterministic ZIP without an extra top-level directory."""

    destination = _require_inside_project(destination, expected_parent=DIST_FC_DIR)
    temporary_destination = destination.with_suffix(destination.suffix + ".tmp")
    if temporary_destination.exists():
        temporary_destination.unlink()

    fixed_timestamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(
        temporary_destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=False,
    ) as archive:
        for path in sorted(path for path in STAGING_DIR.rglob("*") if path.is_file()):
            relative_name = path.relative_to(STAGING_DIR).as_posix()
            zip_info = zipfile.ZipInfo(relative_name, date_time=fixed_timestamp)
            zip_info.compress_type = zipfile.ZIP_DEFLATED
            zip_info.external_attr = 0o100644 << 16
            archive.writestr(zip_info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    temporary_destination.replace(destination)


def _validate_zip(destination: Path) -> None:
    """Confirm the final ZIP still has a flat import root and no Windows binaries."""

    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        required_names = {required.as_posix() for required in REQUIRED_PACKAGE_PATHS if required.suffix}
        missing = sorted(required_names - set(names))
        if missing:
            raise BuildError(f"ZIP is missing required root-level imports: {missing}")
        if "code/researchtwin_mcp/remote_entry.py" in names:
            raise BuildError("ZIP incorrectly nests the package beneath a code/ directory.")
        for name in names:
            normalized = Path(name)
            if name.startswith("/") or ".." in normalized.parts:
                raise BuildError(f"ZIP contains an unsafe archive member path: {name}")
            if any(part.lower() in FORBIDDEN_PATH_PARTS for part in normalized.parts):
                raise BuildError(f"ZIP contains a forbidden path: {name}")
            if any(marker in name.lower() for marker in CP311_MARKERS):
                raise BuildError(f"ZIP contains a CPython 3.11-specific file: {name}")
            suffix = normalized.suffix.lower()
            if normalized.name.lower() == ".env" or suffix in FORBIDDEN_SUFFIXES:
                raise BuildError(f"ZIP contains a forbidden Windows/cache/secret file: {name}")
            if suffix == ".so":
                header = archive.read(name)[:20]
                if len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
                    raise BuildError(f"ZIP native extension is not a Linux ELF64 file: {name}")
                if int.from_bytes(header[18:20], byteorder="little") != 62:
                    raise BuildError(f"ZIP native extension is not x86_64: {name}")


def _write_sha256(destination: Path) -> Path:
    """Write the conventional SHA-256 sidecar without including any secret material."""

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum_path = destination.with_suffix(destination.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    return checksum_path


def _remove_existing_output(destination: Path, *, overwrite: bool) -> None:
    """Avoid accidental artifact replacement unless the caller explicitly requests it."""

    destination = _require_inside_project(destination, expected_parent=DIST_FC_DIR)
    checksum_path = destination.with_suffix(destination.suffix + ".sha256")
    existing = [path for path in (destination, checksum_path) if path.exists()]
    if existing and not overwrite:
        raise BuildError(
            "Output already exists. Use --overwrite only after reviewing it: " + ", ".join(str(path) for path in existing)
        )
    if overwrite:
        for path in existing:
            path.unlink()


def build_fc_web_zip(arguments: argparse.Namespace) -> tuple[Path, Path, StaticValidation]:
    """Build, validate, and checksum the FC ZIP; returns artifact paths and static results."""

    project_name, project_version, _dependencies, _requires_python = _read_project_metadata()
    requirements_path = arguments.requirements_file
    if not requirements_path.is_absolute():
        requirements_path = PROJECT_ROOT / requirements_path
    requirements_path = requirements_path.resolve()
    expected_pins = _read_locked_requirements(requirements_path)
    full_revision, short_revision, source_dirty = _source_revision(allow_dirty=arguments.allow_dirty)
    revision_label = f"{short_revision}-dirty" if source_dirty else short_revision

    DIST_FC_DIR.mkdir(parents=True, exist_ok=True)
    _require_inside_project(DIST_FC_DIR)
    destination = DIST_FC_DIR / f"researchtwin-mcp-fc-web-debian11-py312-{revision_label}.zip"
    _remove_existing_output(destination, overwrite=arguments.overwrite)

    project_wheel = _build_project_wheel()
    project_wheel_metadata = _read_wheel_metadata(project_wheel)
    _validate_wheel_tags(project_wheel_metadata)
    if _canonical_distribution_name(project_wheel_metadata.name) != _canonical_distribution_name(project_name):
        raise BuildError("Locally built wheel name does not match pyproject.toml.")
    if project_wheel_metadata.version != project_version:
        raise BuildError("Locally built wheel version does not match pyproject.toml.")

    dependency_wheels = _download_target_wheels(requirements_path)
    dependency_metadata = _validate_wheelhouse(dependency_wheels, expected_pins)
    _validate_linux_dependency_closure((project_wheel_metadata, *dependency_metadata))
    _install_wheels_into_staging([project_wheel, *dependency_wheels])
    pruned_test_directories = _prune_non_runtime_test_directories()

    validation = _validate_staging()
    _write_manifest(
        project_name=project_name,
        project_version=project_version,
        full_revision=full_revision,
        source_dirty=source_dirty,
        wheel_metadata=(project_wheel_metadata, *dependency_metadata),
        validation=validation,
        pruned_test_directories=pruned_test_directories,
    )
    validation = _validate_staging()
    _archive_staging(destination)
    _validate_zip(destination)

    zip_size = destination.stat().st_size
    if zip_size > arguments.max_zip_bytes:
        destination.unlink()
        raise BuildError(
            f"ZIP size {zip_size} bytes exceeds the configured FC upload guard of {arguments.max_zip_bytes} bytes. "
            "Do not delete dependencies to force it smaller; use OSS code upload, an FC Layer, or another "
            "official large-package path after confirming the applicable regional limit."
        )
    checksum_path = _write_sha256(destination)
    return destination, checksum_path, validation


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the intentionally small, explicit FC package command line."""

    parser = argparse.ArgumentParser(description="Build a static-validated FC Debian 11 CPython 3.12 MCP ZIP.")
    parser.add_argument(
        "--requirements-file",
        type=Path,
        default=DEFAULT_REQUIREMENTS_PATH,
        help="Committed exact Linux wheel lock; defaults to requirements/fc-web-linux-x86_64-py312.txt.",
    )
    parser.add_argument(
        "--max-zip-bytes",
        type=int,
        default=DEFAULT_MAX_ZIP_BYTES,
        help="Conservative FC upload guard (default: 100000000 bytes).",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Build a clearly suffixed disposable <git-sha>-dirty artifact for local packaging tests.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace only the matching generated ZIP and SHA256 sidecar in dist_fc/.",
    )
    return parser


def main() -> int:
    """Run the FC artifact build and print only non-secret delivery facts."""

    arguments = build_argument_parser().parse_args()
    if arguments.max_zip_bytes <= 0:
        print("FC package build failed: --max-zip-bytes must be positive.", file=sys.stderr)
        return 2
    try:
        destination, checksum_path, validation = build_fc_web_zip(arguments)
    except (BuildError, subprocess.CalledProcessError) as exc:
        print(f"FC package build failed: {exc}", file=sys.stderr)
        return 1

    digest = checksum_path.read_text(encoding="ascii").split(maxsplit=1)[0]
    print("FC Web ZIP build succeeded")
    print(f"target: {TARGET_OS} / {TARGET_ARCHITECTURE} / CPython {TARGET_PYTHON}")
    print("startup_command: python3 -m researchtwin_mcp.remote_entry")
    print("listener_port: 8000")
    print("mcp_endpoint: /mcp")
    print(f"zip: {destination}")
    print(f"zip_bytes: {destination.stat().st_size}")
    print(f"sha256: {digest}")
    print(f"cp311_file_count: {validation.cp311_file_count}")
    print(f"windows_pyd_dll_file_count: {validation.windows_pyd_dll_count}")
    print(f"linux_x86_64_elf_so_count: {len(validation.native_extensions)}")
    for native_extension in validation.native_extensions:
        print(f"linux_so: {native_extension}")
    print("credential_shaped_secret_scan: passed")
    print("linux_runtime_execution: not performed on this build host")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
