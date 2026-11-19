"""Disposable SQLite snapshots. Configuration remains the source of truth."""

from __future__ import annotations

import json
import os
import tempfile
import time
import zlib
from contextlib import closing, contextmanager, ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pcd_cli.cache import (
    _CacheReadError,
    _decode_project,
    _is_cache_entry,
    _read_json,
    ProjectCache,
)
from pcd_cli.filesystem import _sync_directory, atomic_write, file_lock
from pcd_cli.lookup import (
    lookup_snapshot,
    LookupEntry,
    LookupMode,
    LookupResult,
    MemorySnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Iterator
    from sqlite3 import Connection

    from pcd_cli.lookup import LookupRequest, UsageLoader
    from pcd_cli.models import Project

VERSION = 1
APPLICATION_ID = 1346585667
_METADATA = "SELECT id, name_key, name_length, path FROM projects INDEXED BY by_name"
_PAYLOADS = (
    "SELECT id, name_key, name_length, path, payload, payload_crc FROM projects INDEXED BY by_name"
)
_ORDER = " ORDER BY name_key, path"


def _row(value: object, length: int) -> tuple[object, ...]:
    if not isinstance(value, tuple) or len(value) != length:
        raise _CacheReadError("Invalid SQLite record")
    return value


def _entry(value: object) -> LookupEntry[int]:
    reference, key, name_length, path = _row(value, 4)
    if (
        not isinstance(reference, int)
        or isinstance(reference, bool)
        or reference < 1
        or not isinstance(key, bytes)
        or not isinstance(name_length, int)
        or isinstance(name_length, bool)
        or name_length < 0
        or not isinstance(path, str)
    ):
        raise _CacheReadError("Invalid project metadata")
    name_key = key.decode("utf-8")
    if name_key.casefold() != name_key:
        raise _CacheReadError("Invalid project name key")
    return LookupEntry(reference, name_key, name_length, path)


def _decode(entry: LookupEntry[int], payload: object, checksum: object) -> Project:
    if (
        not isinstance(payload, str)
        or not isinstance(checksum, int)
        or isinstance(checksum, bool)
        or zlib.crc32(payload.encode("utf-8")) != checksum
    ):
        raise _CacheReadError("Invalid project payload checksum")
    value = _read_json(payload)
    if not _is_cache_entry(value):
        raise _CacheReadError("Invalid project payload")
    if (value["name"].casefold(), len(value["name"]), value["path"]) != (
        entry.name_key,
        entry.name_length,
        entry.path,
    ):
        raise _CacheReadError("Project does not match index")
    return _decode_project(value)


def _prefix_clause(key: str) -> tuple[str, tuple[bytes, ...]]:
    start = key.encode("utf-8")
    # The exclusive byte successor also handles prefixes ending in U+10FFFF.
    for index in range(len(start) - 1, -1, -1):
        if start[index] < 255:
            end = start[:index] + bytes((start[index] + 1,))
            return " WHERE name_key >= ? AND name_key < ?", (start, end)
    return " WHERE name_key >= ?", (start,)


@dataclass(frozen=True, slots=True)
class SQLiteSnapshot:
    connection: Connection
    generation: int
    count: int

    def _entries(
        self, clause: str = "", parameters: tuple[bytes, ...] = ()
    ) -> Iterator[LookupEntry[int]]:
        previous: tuple[str, str] | None = None
        with closing(self.connection.execute(_METADATA + clause + _ORDER, parameters)) as cursor:
            for row in cursor:
                entry = _entry(row)
                order = entry.name_key, entry.path
                if previous is not None and order <= previous:
                    raise _CacheReadError("Unsorted or repeated project metadata")
                previous = order
                yield entry

    def records(self) -> Iterator[LookupEntry[int]]:
        return self._entries()

    def exact(self, key: str) -> Iterator[LookupEntry[int]]:
        return self._entries(" WHERE name_key = ?", (key.encode("utf-8"),))

    def prefix(self, key: str) -> Iterator[LookupEntry[int]]:
        return self._entries(*_prefix_clause(key))

    def read(self, entry: LookupEntry[int]) -> Project:
        with closing(
            self.connection.execute(
                "SELECT payload, payload_crc FROM projects WHERE id = ?", (entry.reference,)
            )
        ) as cursor:
            payload, checksum = _row(cursor.fetchone(), 2)
        return _decode(entry, payload, checksum)

    def projects(self, key: str | None = None) -> Iterator[Project]:
        """Read a full/prefix snapshot in one query, without one SQL lookup per project."""
        clause, parameters = ("", ()) if key is None else _prefix_clause(key)
        previous: tuple[str, str] | None = None
        count = 0
        with closing(self.connection.execute(_PAYLOADS + clause + _ORDER, parameters)) as cursor:
            for row in cursor:
                values = _row(row, 6)
                entry = _entry(values[:4])
                order = entry.name_key, entry.path
                if previous is not None and order <= previous:
                    raise _CacheReadError("Unsorted or repeated project metadata")
                previous = order
                count += 1
                yield _decode(entry, values[4], values[5])
        if key is None and count != self.count:
            raise _CacheReadError("Invalid project count")


@dataclass(frozen=True, slots=True)
class SQLiteProjectStore:
    path: Path

    @property
    def legacy_path(self) -> Path:
        return self.path.with_name("projects.jsonl")

    @property
    def initialized_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".initialized")

    def _migrate(self) -> None:
        # Never resurrect legacy data after a SQLite snapshot has existed.
        if _exists(self.path) or _exists(self.initialized_path):
            return
        legacy = ProjectCache(self.legacy_path).snapshot()
        if legacy is not None:
            generation, projects = legacy
            identities = {(project.name.casefold(), str(project.path)) for project in projects}
            if generation >= 2**64 or len(identities) != len(projects):
                return
            try:
                self._save(projects, generation, importing=True)
            except UnicodeError:
                return

    @contextmanager
    def _snapshot(self) -> Generator[SQLiteSnapshot]:
        # Keep sqlite3 out of unrelated CLI startup and shell commands.
        import sqlite3

        # mode=ro must never create a missing cache, its directory or a journal.
        self.path.stat()
        with closing(sqlite3.connect(_database_uri(self.path) + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.execute("BEGIN")
            try:
                if conn.execute("PRAGMA application_id").fetchone() != (APPLICATION_ID,):
                    raise _CacheReadError("Invalid SQLite application ID")
                if conn.execute("PRAGMA user_version").fetchone() != (VERSION,):
                    raise _CacheReadError("Unsupported SQLite cache version")
                rows = conn.execute(
                    "SELECT singleton, generation, project_count FROM metadata LIMIT 2"
                ).fetchall()
                if len(rows) != 1:
                    raise _CacheReadError("Invalid SQLite cache metadata")
                singleton, generation, count = _row(rows[0], 3)
                if (
                    singleton != 1
                    or not isinstance(generation, str)
                    or len(generation) > 20
                    or not generation.isascii()
                    or not generation.isdecimal()
                    or not 0 <= int(generation) < 2**64
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 0
                ):
                    raise _CacheReadError("Invalid SQLite cache metadata")
                # Verify required columns/index without scanning project records.
                conn.execute(_PAYLOADS + " LIMIT 0").close()
            except sqlite3.DatabaseError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_ERROR:
                    raise _CacheReadError("Invalid SQLite cache schema") from exc
                raise
            yield SQLiteSnapshot(conn, int(generation), count)

    def inspect(self) -> list[Project]:
        """Validate a disk snapshot without migration, recovery or SQLite sidecar writes."""
        import sqlite3

        # Our writer uses rollback journals. Refuse externally enabled WAL before
        # connecting: SQLite may otherwise create a shared-memory sidecar even for
        # a read-only connection. A normal read transaction keeps concurrent pcd
        # refreshes consistent and never attempts hot-journal recovery here.
        with self.path.open("rb") as stream:
            header = stream.read(20)
        if len(header) == 20 and (header[18] == 2 or header[19] == 2):
            raise OSError(
                "Cache uses unsupported WAL journaling; inspection could create sidecars. "
                "Restore DELETE journal mode with SQLite while pcd is idle"
            )
        try:
            with self._snapshot() as snapshot:
                with closing(snapshot.connection.execute("PRAGMA integrity_check")) as cursor:
                    if cursor.fetchall() != [("ok",)]:
                        raise _CacheReadError("SQLite integrity check failed")
                return list(snapshot.projects())
        except (_CacheReadError, UnicodeError, sqlite3.DatabaseError) as exc:
            raise OSError(f"Invalid project cache: {exc}") from exc

    @contextmanager
    def _read_snapshot(self, *, recover_journal: bool) -> Generator[SQLiteSnapshot]:
        import sqlite3

        with ExitStack() as stack:
            try:
                snapshot = stack.enter_context(self._snapshot())
            except sqlite3.DatabaseError as exc:
                if not recover_journal or not _hot_journal(exc):
                    raise
                # Ordinary reads may recover an interrupted transaction; completion
                # remains strictly read-only and reports an unavailable cache instead.
                with file_lock(self.path):
                    _rollback_hot_journal(self.path)
                snapshot = stack.enter_context(self._snapshot())
            yield snapshot

    def load(self) -> list[Project] | None:
        import sqlite3

        self._migrate()
        try:
            with self._read_snapshot(recover_journal=True) as snapshot:
                return list(snapshot.projects())
        except (_CacheReadError, FileNotFoundError, UnicodeError):
            return None
        except sqlite3.DatabaseError as exc:
            if _recoverable_read_error(exc):
                return None
            raise OSError(f"Cannot read project cache: {exc}") from exc

    def lookup(
        self,
        request: LookupRequest,
        load_usage: UsageLoader | None = None,
    ) -> LookupResult | None:
        import sqlite3

        if request.mode is LookupMode.PREFIX:
            try:
                if not _exists(self.path):
                    if _exists(self.initialized_path):
                        return None
                    legacy = ProjectCache(self.legacy_path).find_prefix(request.query)
                    if legacy is None:
                        return None
                    return lookup_snapshot(MemorySnapshot(legacy), request)
            except OSError:
                return None
        else:
            self._migrate()
        try:
            with self._read_snapshot(
                recover_journal=request.mode is not LookupMode.PREFIX
            ) as snapshot:
                if (
                    request.mode is LookupMode.PREFIX
                    and request.limit is None
                    and request.after is None
                    and request.before is None
                    and not request.last
                ):
                    matches = list(snapshot.projects(request.query.casefold()))
                    return LookupResult(matches, len(matches), generation=snapshot.generation)
                return lookup_snapshot(snapshot, request, load_usage)
        except (_CacheReadError, FileNotFoundError, UnicodeError):
            return None
        except sqlite3.DatabaseError as exc:
            if request.mode is LookupMode.PREFIX or _recoverable_read_error(exc):
                return None
            raise OSError(f"Cannot read project cache: {exc}") from exc
        except OSError:
            if request.mode is LookupMode.PREFIX:
                return None
            raise

    def save(self, projects: Iterable[Project], *, started_at_ns: int | None = None) -> bool:
        generation = time.time_ns() if started_at_ns is None else started_at_ns
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or not 0 <= generation < 2**64
        ):
            raise ValueError("Invalid refresh timestamp")
        return self._save(projects, generation)

    def _save(
        self, projects: Iterable[Project], generation: int, *, importing: bool = False
    ) -> bool:
        import sqlite3

        # A failed write may consume a generator before encountering corrupt pages.
        # Keep its projects available for a fresh database recovery attempt.
        projects = list(projects)
        try:
            with file_lock(self.path):
                if importing and (_exists(self.path) or _exists(self.initialized_path)):
                    return False
                existing = _exists(self.path)
                rebuild = not existing
                if existing:
                    # Read-only connections cannot roll back a hot journal after a crash.
                    # Recover it only here, under the writer lock, before testing staleness.
                    try:
                        with self._snapshot():
                            pass
                    except sqlite3.DatabaseError as exc:
                        if _hot_journal(exc):
                            _rollback_hot_journal(self.path)
                        elif not _corrupt(exc):
                            raise
                    except (_CacheReadError, UnicodeError):
                        pass
                    try:
                        with self._snapshot() as current:
                            if current.generation > generation:
                                # A corrupt newer snapshot must not block recovery.
                                for _ in current.projects():
                                    pass
                                return False
                    except (_CacheReadError, UnicodeError):
                        rebuild = True
                    except sqlite3.DatabaseError as exc:
                        if not _corrupt(exc):
                            raise
                        rebuild = True
                if not rebuild:
                    try:
                        with closing(_connect_writable(self.path)) as connection:
                            connection.execute("PRAGMA synchronous = FULL")
                            connection.execute("PRAGMA trusted_schema = OFF")
                            with connection:
                                connection.execute("BEGIN IMMEDIATE")
                                self._write(connection, projects, generation)
                                self._mark_initialized()
                    except sqlite3.DatabaseError as exc:
                        if not _corrupt(exc):
                            raise
                        rebuild = True
                if rebuild:
                    self._replace(projects, generation)
                return True
        except sqlite3.DatabaseError as exc:
            # CLI's I/O boundary reports access, busy, disk-full and SQLite failures.
            raise OSError(f"Cannot publish project cache: {exc}") from exc

    def _write(self, connection: Connection, projects: Iterable[Project], generation: int) -> None:
        import sqlite3

        connection.execute("DELETE FROM projects")
        connection.execute("DELETE FROM metadata")
        count = 0
        try:
            for project in projects:
                payload = json.dumps(
                    {
                        "name": project.name,
                        "path": str(project.path),
                        "display": str(project.display_path),
                        "source": project.source.value,
                        "discoveries": [
                            {"root": str(item.root), "display": str(item.display_path)}
                            for item in project.discoveries
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                raw = payload.encode("utf-8")
                connection.execute(
                    "INSERT INTO projects "
                    "(name_key, name_length, path, payload, payload_crc) VALUES (?, ?, ?, ?, ?)",
                    (
                        project.name.casefold().encode("utf-8"),
                        len(project.name),
                        str(project.path),
                        payload,
                        zlib.crc32(raw),
                    ),
                )
                count += 1
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate project identity") from exc
        # The covering index includes original name length; casefold expansion can
        # still produce the same name/path with different lengths (e.g. ß and ss).
        if (
            connection.execute(
                "SELECT 1 FROM projects INDEXED BY by_name GROUP BY name_key, path "
                "HAVING count(*) > 1 LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise ValueError("Duplicate project identity")
        connection.execute("INSERT INTO metadata VALUES (1, ?, ?)", (str(generation), count))

    def _replace(self, projects: Iterable[Project], generation: int) -> None:
        import sqlite3

        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with closing(sqlite3.connect(temporary)) as connection:
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA trusted_schema = OFF")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {VERSION}")
                    connection.execute(
                        "CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK (singleton=1), "
                        "generation TEXT NOT NULL, project_count INTEGER NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE projects (id INTEGER PRIMARY KEY, name_key BLOB NOT NULL, "
                        "name_length INTEGER NOT NULL, path TEXT NOT NULL, payload TEXT NOT NULL, "
                        "payload_crc INTEGER NOT NULL)"
                    )
                    connection.execute(
                        "CREATE UNIQUE INDEX by_name ON projects(name_key, path, name_length)"
                    )
                    self._write(connection, projects, generation)
            # Mark initialization before publication so loss cannot revive legacy records.
            self._mark_initialized()
            _publish(temporary, self.path)
            _sync_directory(self.path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _mark_initialized(self) -> None:
        if not _exists(self.initialized_path):
            with atomic_write(self.initialized_path) as marker:
                marker.write("1\n")


def _database_uri(path: Path) -> str:
    from urllib.parse import quote_from_bytes

    absolute = path.resolve()
    # Leave Windows UNC and device paths to pathlib. For local files, as_uri()
    # imports urllib.request on newer Python versions just to encode the path.
    if absolute.drive.startswith("\\\\"):
        return absolute.as_uri()
    prefix = "file:///" if absolute.drive else "file://"
    return prefix + quote_from_bytes(os.fsencode(absolute.as_posix()), safe="/:")


def _connect_writable(path: Path) -> Connection:
    import sqlite3

    return sqlite3.connect(_database_uri(path) + "?mode=rw", uri=True)


def _corrupt(exc: Exception) -> bool:
    import sqlite3

    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 255) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def _hot_journal(exc: Exception) -> bool:
    import sqlite3

    return getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_READONLY_ROLLBACK


def _rollback_hot_journal(path: Path) -> None:
    with closing(_connect_writable(path)) as connection:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA schema_version").fetchone()


def _recoverable_read_error(exc: Exception) -> bool:
    return _corrupt(exc) or _hot_journal(exc)


def _exists(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _publish(temporary: Path, destination: Path) -> None:
    for attempt in range(6):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 5:
                raise
            time.sleep(0.01 * 2**attempt)
