"""Thread-safe UTF-8 JSON persistence with atomic writes.

``JsonStore`` is intentionally small: tools own their domain logic while this
module owns directory creation, safe paths, JSON recovery, and durable writes.
Every store instance shares a re-entrant lock for a given file, so a read-modify-
write update stays safe even when tools receive separate ``JsonStore`` objects.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar, Final, TypeAlias

from researchtwin_mcp.models.schemas import StorageError


LOGGER = logging.getLogger(__name__)

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

RESEARCH_LOGS_FILE: Final = "research_logs.json"
PROJECT_STATUS_FILE: Final = "project_status.json"
ADVISOR_INSTRUCTIONS_FILE: Final = "advisor_instructions.json"
CANDIDATE_INTELLIGENCE_FILE: Final = "candidate_intelligence.json"
RESEARCH_INTELLIGENCE_BRIEFS_FILE: Final = "research_intelligence_briefs.json"

DEFAULT_RESEARCH_LOGS: Final[JsonObject] = {"activities": []}
DEFAULT_PROJECT_STATUS: Final[JsonObject] = {}
DEFAULT_ADVISOR_INSTRUCTIONS: Final[JsonObject] = {"instructions": []}
DEFAULT_CANDIDATE_INTELLIGENCE: Final[JsonObject] = {"candidates": []}
DEFAULT_RESEARCH_INTELLIGENCE_BRIEFS: Final[JsonObject] = {"briefs": []}

RUNTIME_JSON_DEFAULTS: Final[dict[str, JsonObject]] = {
    RESEARCH_LOGS_FILE: DEFAULT_RESEARCH_LOGS,
    PROJECT_STATUS_FILE: DEFAULT_PROJECT_STATUS,
    ADVISOR_INSTRUCTIONS_FILE: DEFAULT_ADVISOR_INSTRUCTIONS,
    CANDIDATE_INTELLIGENCE_FILE: DEFAULT_CANDIDATE_INTELLIGENCE,
    RESEARCH_INTELLIGENCE_BRIEFS_FILE: DEFAULT_RESEARCH_INTELLIGENCE_BRIEFS,
}
"""Initial contents for the four files owned by the runtime data directory."""


class JsonStore:
    """Persist JSON documents below one data directory.

    The constructor creates the data directory and the four standard runtime
    JSON files. All file names accepted by public methods are resolved relative
    to ``data_dir`` and cannot escape it through absolute paths, ``..``, or
    existing directory symlinks.
    """

    _locks_guard: ClassVar[threading.Lock] = threading.Lock()
    _file_locks: ClassVar[dict[str, threading.RLock]] = {}

    def __init__(self, data_dir: str | Path) -> None:
        """Create a store rooted at *data_dir* and initialise runtime defaults."""

        self.data_dir = Path(data_dir).expanduser().resolve()
        self.initialize()

    def initialize(self) -> None:
        """Create the data directory and any missing standard JSON documents."""

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                "storage_initialization_failed",
                "Unable to create the configured data directory.",
                details={"operation": "create_data_directory"},
            ) from exc

        for relative_path, default in RUNTIME_JSON_DEFAULTS.items():
            path = self.resolve_path(relative_path)
            with self._lock_for(path):
                if not path.exists():
                    self._write_json_path_unlocked(path, default)

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Return a safe resolved path under ``data_dir`` for *relative_path*."""

        try:
            requested_path = Path(relative_path)
        except TypeError as exc:
            raise StorageError(
                "unsafe_path",
                "Storage paths must be relative paths below the data directory.",
            ) from exc

        if requested_path.is_absolute():
            raise StorageError(
                "unsafe_path",
                "Storage paths must be relative paths below the data directory.",
                details={"path": str(relative_path)},
            )

        candidate = (self.data_dir / requested_path).resolve()
        try:
            candidate.relative_to(self.data_dir)
        except ValueError as exc:
            raise StorageError(
                "unsafe_path",
                "Storage paths must stay below the data directory.",
                details={"path": str(relative_path)},
            ) from exc

        if candidate == self.data_dir:
            raise StorageError(
                "unsafe_path",
                "Storage paths must identify a file below the data directory.",
                details={"path": str(relative_path)},
            )
        return candidate

    def read_json(
        self,
        relative_path: str | Path,
        default: JsonValue = None,
    ) -> JsonValue:
        """Read a JSON file, creating or returning a copy of *default* if needed.

        Corrupt or non-UTF-8 JSON is logged and treated as unavailable. The
        original malformed file is deliberately retained for recovery instead of
        being silently overwritten.
        """

        path = self.resolve_path(relative_path)
        with self._lock_for(path):
            return self._read_json_path_unlocked(path, default)

    def write_json(self, relative_path: str | Path, value: JsonValue) -> Path:
        """Atomically persist JSON using UTF-8 and literal non-ASCII characters."""

        path = self.resolve_path(relative_path)
        with self._lock_for(path):
            return self._write_json_path_unlocked(path, value)

    def update_json(
        self,
        relative_path: str | Path,
        default: JsonValue,
        updater: Callable[[JsonValue], JsonValue],
    ) -> JsonValue:
        """Atomically apply *updater* to a JSON document and return its new value."""

        path = self.resolve_path(relative_path)
        with self._lock_for(path):
            current = self._read_json_path_unlocked(path, default)
            updated = updater(copy.deepcopy(current))
            self._write_json_path_unlocked(path, updated)
            return copy.deepcopy(updated)

    def write_text_atomic(self, relative_path: str | Path, text: str) -> Path:
        """Atomically write UTF-8 text below ``data_dir`` and return its path.

        Reports use this method so their Markdown files receive the same durable
        temp-file, ``fsync``, and ``os.replace`` treatment as JSON documents.
        """

        if not isinstance(text, str):
            raise StorageError(
                "invalid_text",
                "Atomic text writes require a string value.",
                details={"path": str(relative_path)},
            )
        path = self.resolve_path(relative_path)
        with self._lock_for(path):
            self._write_text_path_unlocked(path, text)
        LOGGER.debug("Persisted text document: %s", self._relative_name(path))
        return path

    def load_research_logs(self) -> JsonObject:
        """Load the research-log container, falling back to its valid empty shape."""

        return self._load_known_object(
            RESEARCH_LOGS_FILE,
            DEFAULT_RESEARCH_LOGS,
            required_list_key="activities",
        )

    def save_research_logs(self, value: JsonObject) -> Path:
        """Persist the research-log container."""

        return self.write_json(RESEARCH_LOGS_FILE, value)

    def read_research_logs(self) -> JsonObject:
        """Alias for :meth:`load_research_logs` for read-oriented callers."""

        return self.load_research_logs()

    def write_research_logs(self, value: JsonObject) -> Path:
        """Alias for :meth:`save_research_logs` for write-oriented callers."""

        return self.save_research_logs(value)

    def load_project_status(self) -> JsonObject:
        """Load the current project status, or an empty status document."""

        return self._load_known_object(PROJECT_STATUS_FILE, DEFAULT_PROJECT_STATUS)

    def save_project_status(self, value: JsonObject) -> Path:
        """Persist the current project status."""

        return self.write_json(PROJECT_STATUS_FILE, value)

    def read_project_status(self) -> JsonObject:
        """Alias for :meth:`load_project_status` for read-oriented callers."""

        return self.load_project_status()

    def write_project_status(self, value: JsonObject) -> Path:
        """Alias for :meth:`save_project_status` for write-oriented callers."""

        return self.save_project_status(value)

    def load_advisor_instructions(self) -> JsonObject:
        """Load the advisor-instruction container, with a safe empty fallback."""

        return self._load_known_object(
            ADVISOR_INSTRUCTIONS_FILE,
            DEFAULT_ADVISOR_INSTRUCTIONS,
            required_list_key="instructions",
        )

    def save_advisor_instructions(self, value: JsonObject) -> Path:
        """Persist the advisor-instruction container."""

        return self.write_json(ADVISOR_INSTRUCTIONS_FILE, value)

    def read_advisor_instructions(self) -> JsonObject:
        """Alias for :meth:`load_advisor_instructions` for read-oriented callers."""

        return self.load_advisor_instructions()

    def write_advisor_instructions(self, value: JsonObject) -> Path:
        """Alias for :meth:`save_advisor_instructions` for write-oriented callers."""

        return self.save_advisor_instructions(value)

    def load_candidate_intelligence(self) -> JsonObject:
        """Load candidate intelligence records with their safe empty container shape."""

        return self._load_known_object(
            CANDIDATE_INTELLIGENCE_FILE,
            DEFAULT_CANDIDATE_INTELLIGENCE,
            required_list_key="candidates",
        )

    def save_candidate_intelligence(self, value: JsonObject) -> Path:
        """Persist the candidate intelligence container."""

        return self.write_json(CANDIDATE_INTELLIGENCE_FILE, value)

    def read_candidate_intelligence(self) -> JsonObject:
        """Alias for :meth:`load_candidate_intelligence` for read-oriented callers."""

        return self.load_candidate_intelligence()

    def write_candidate_intelligence(self, value: JsonObject) -> Path:
        """Alias for :meth:`save_candidate_intelligence` for write-oriented callers."""

        return self.save_candidate_intelligence(value)

    def load_research_intelligence_briefs(self) -> JsonObject:
        return self._load_known_object(RESEARCH_INTELLIGENCE_BRIEFS_FILE, DEFAULT_RESEARCH_INTELLIGENCE_BRIEFS, required_list_key="briefs")

    def save_research_intelligence_briefs(self, value: JsonObject) -> Path:
        return self.write_json(RESEARCH_INTELLIGENCE_BRIEFS_FILE, value)

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        """Return the process-wide re-entrant lock assigned to *path*."""

        # ``normcase`` keeps equivalent Windows spellings on the same lock.
        key = os.path.normcase(str(path.resolve()))
        with cls._locks_guard:
            lock = cls._file_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._file_locks[key] = lock
            return lock

    def _read_json_path_unlocked(self, path: Path, default: JsonValue) -> JsonValue:
        """Read one JSON path while its shared lock is already held."""

        fallback = copy.deepcopy(default)
        relative_name = self._relative_name(path)
        if not path.exists():
            try:
                self._write_json_path_unlocked(path, fallback)
            except StorageError:
                LOGGER.exception("Unable to initialise missing JSON document: %s", relative_name)
            return fallback

        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOGGER.warning("Malformed JSON document; using default: %s (%s)", relative_name, exc)
            return fallback
        except OSError:
            LOGGER.exception("Unable to read JSON document; using default: %s", relative_name)
            return fallback

        if not self._matches_default_type(loaded, fallback):
            LOGGER.warning("JSON document has an unexpected root type; using default: %s", relative_name)
            return fallback
        LOGGER.debug("Read JSON document: %s", relative_name)
        return loaded

    def _write_json_path_unlocked(self, path: Path, value: JsonValue) -> Path:
        """Serialise and atomically persist JSON while *path*'s lock is held."""

        try:
            text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        except (TypeError, ValueError) as exc:
            raise StorageError(
                "json_serialization_failed",
                "The supplied value cannot be persisted as JSON.",
                details={"path": self._relative_name(path)},
            ) from exc
        self._write_text_path_unlocked(path, text)
        LOGGER.debug("Persisted JSON document: %s", self._relative_name(path))
        return path

    def _write_text_path_unlocked(self, path: Path, text: str) -> None:
        """Write text via a synced sibling temporary file and atomic replacement."""

        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary_file:
                temporary_file.write(text)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            self._fsync_directory(path.parent)
        except OSError as exc:
            raise StorageError(
                "storage_write_failed",
                "Unable to atomically persist the requested file.",
                details={"path": self._relative_name(path)},
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Unable to remove failed temporary storage file: %s",
                        self._relative_name(temporary_path),
                    )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Best-effort directory sync for platforms that support it."""

        if os.name == "nt":
            return
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @staticmethod
    def _matches_default_type(value: JsonValue, default: JsonValue) -> bool:
        """Check only the JSON root shape that the caller declared as a default."""

        if default is None:
            return True
        # ``bool`` is an ``int`` subclass, but their JSON shapes are distinct.
        return type(value) is type(default)

    def _load_known_object(
        self,
        relative_path: str,
        default: JsonObject,
        *,
        required_list_key: str | None = None,
    ) -> JsonObject:
        """Load a known container and protect tool callers from corrupt shapes."""

        loaded = self.read_json(relative_path, default)
        if not isinstance(loaded, dict):
            return copy.deepcopy(default)
        if required_list_key is not None and not isinstance(loaded.get(required_list_key), list):
            LOGGER.warning(
                "JSON document is missing its required list; using default: %s",
                relative_path,
            )
            return copy.deepcopy(default)
        return loaded

    def _relative_name(self, path: Path) -> str:
        """Return a safe data-directory-relative name for logs and error details."""

        try:
            return path.resolve().relative_to(self.data_dir).as_posix()
        except ValueError:
            return path.name


__all__ = [
    "ADVISOR_INSTRUCTIONS_FILE",
    "CANDIDATE_INTELLIGENCE_FILE",
    "DEFAULT_ADVISOR_INSTRUCTIONS",
    "DEFAULT_CANDIDATE_INTELLIGENCE",
    "DEFAULT_PROJECT_STATUS",
    "DEFAULT_RESEARCH_LOGS",
    "JsonObject",
    "JsonStore",
    "JsonValue",
    "PROJECT_STATUS_FILE",
    "RESEARCH_LOGS_FILE",
    "RESEARCH_INTELLIGENCE_BRIEFS_FILE",
    "DEFAULT_RESEARCH_INTELLIGENCE_BRIEFS",
    "RUNTIME_JSON_DEFAULTS",
]
