"""Persistent navigation state, independent of the rebuildable project catalog."""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from fnmatch import fnmatch
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from pcd_cli.filesystem import database_uri
from pcd_cli.models import ProjectUsage

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Mapping

    from pcd_cli.lookup import DirectoryEntry


class DirectoryHistoryError(OSError):
    """An unavailable navigation-history store, distinct from project-cache errors."""


class _HistoryNotFoundError(FileNotFoundError):
    """Optional history has not been created yet."""


@dataclass(frozen=True, slots=True)
class Visit:
    count: int
    used_at: float
    preference: float


def record_current_directory(
    *,
    project_only: bool,
    directory: Path | None = None,
    open_history: Callable[[Path], DirectoryVisits] | None = None,
) -> bool:
    import sys

    from pcd_cli.config import Config
    from pcd_cli.display import terminal_text
    from pcd_cli.filesystem import ApplicationPaths

    try:
        path = (directory if directory is not None else Path.cwd()).resolve()
        paths = ApplicationPaths.resolve()
        if not is_excluded(path, Config(paths.config).visit_exclusions()):
            history = (open_history or DirectoryVisits)(paths.history)
            history.record(path, project_only=project_only)
            return True
    except OSError as exc:
        print(f"Warning: could not write usage history: {terminal_text(str(exc))}", file=sys.stderr)
    return False


def is_excluded(path: Path, excluded_projects: frozenset[Path]) -> bool:
    if excluded_projects and (
        path in excluded_projects or any(parent in excluded_projects for parent in path.parents)
    ):
        return True
    patterns = os.environ.get("PCD_EXCLUDE_DIRS", "").split(os.pathsep)
    return any(pattern and fnmatch(str(path), os.path.expanduser(pattern)) for pattern in patterns)


@dataclass(frozen=True, slots=True)
class DirectoryVisits:
    path: Path
    writer: sqlite3.Connection | None = None

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        try:
            if not write:
                try:
                    self.path.stat()
                except FileNotFoundError:
                    if _legacy_exists(self.path):
                        with self._connection(write=True) as db:
                            yield db
                        return
                    raise _HistoryNotFoundError(self.path) from None
            if write and self.writer is not None:
                db = self.writer
            elif write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self.path)
            else:
                db = sqlite3.connect(database_uri(self.path) + "?mode=ro", uri=True)
            with nullcontext(db) if db is self.writer else closing(db), db:
                db.execute("PRAGMA trusted_schema = OFF")
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if (
                    version == 0
                    and not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
                ):
                    if not write:
                        # A concurrent first writer has not yet published its schema.
                        if _legacy_exists(self.path):
                            db.rollback()
                            with self._connection(write=True) as initialized:
                                yield initialized
                            return
                        raise _HistoryNotFoundError(self.path)
                    db.execute("""CREATE TABLE visits (
                        identity TEXT PRIMARY KEY, path TEXT NOT NULL,
                        count INTEGER NOT NULL DEFAULT 0, used REAL NOT NULL DEFAULT 0,
                        preference REAL NOT NULL DEFAULT 0)""")
                    db.execute("PRAGMA user_version = 1")
                    version = 1
                elif version not in (1, 2, 3):
                    raise OSError(f"Unsupported directory history schema: {version}")
                if (
                    version == 1
                    or not db.execute(
                        "SELECT 1 FROM history_metadata WHERE key='legacy_imported'"
                    ).fetchone()
                ):
                    if not write:
                        # Release the read transaction before acquiring the writer lock.
                        db.rollback()
                        with self._connection(write=True) as upgraded:
                            yield upgraded
                        return
                    if version == 1:
                        db.execute(
                            "ALTER TABLE visits ADD COLUMN learned INTEGER NOT NULL DEFAULT 1"
                        )
                        db.execute(
                            "ALTER TABLE visits ADD COLUMN tracked_used REAL NOT NULL DEFAULT 0"
                        )
                        db.execute("UPDATE visits SET tracked_used=used WHERE count > 0")
                        db.execute("""CREATE INDEX visits_project_recent ON visits
                            (used DESC,count DESC,path DESC) WHERE learned=0""")
                        db.execute("CREATE TABLE history_metadata (key TEXT PRIMARY KEY)")
                        db.execute("PRAGMA user_version = 2")
                    self._import_legacy(db)
                if version < 3:
                    if not write:
                        db.rollback()
                        with self._connection(write=True) as upgraded:
                            yield upgraded
                        return
                    db.execute("ALTER TABLE visits ADD COLUMN name_key TEXT NOT NULL DEFAULT ''")
                    db.execute("ALTER TABLE visits ADD COLUMN path_key TEXT NOT NULL DEFAULT ''")
                    db.execute(
                        "ALTER TABLE history_metadata ADD COLUMN value INTEGER NOT NULL DEFAULT 0"
                    )
                    db.execute("INSERT INTO history_metadata(key) VALUES('revision')")
                    rows = db.execute("SELECT identity,path FROM visits").fetchall()
                    db.executemany(
                        "UPDATE visits SET name_key=?,path_key=? WHERE identity=?",
                        ((_name_key(path), _path_key(path), identity) for identity, path in rows),
                    )
                    db.execute("""CREATE INDEX visits_by_name ON visits(name_key,identity)
                        WHERE learned=1""")
                    db.execute("PRAGMA user_version = 3")
                yield db
        except (_HistoryNotFoundError, DirectoryHistoryError):
            raise
        except (sqlite3.Error, OSError) as exc:
            raise DirectoryHistoryError(f"Cannot access directory history: {exc}") from exc

    def inspect(self) -> tuple[int, int]:
        """Check existing history without migrating it or creating SQLite sidecars."""
        # Journal mode can change while doctor runs. Open only a private copy,
        # so even a concurrent switch to WAL cannot create original sidecars.
        import shutil
        import tempfile

        sources = [self.path, *(Path(f"{self.path}{suffix}") for suffix in ("-wal", "-journal"))]

        def stamp(path: Path) -> tuple[int, int, int, int] | None:
            try:
                info = path.stat()
            except FileNotFoundError:
                return None
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

        try:
            with tempfile.TemporaryDirectory() as folder:
                for attempt in range(3):
                    before = [stamp(path) for path in sources]
                    if before[0] is None:
                        raise FileNotFoundError(self.path)
                    copy = Path(folder) / f"snapshot-{attempt}.db"
                    try:
                        for source, state in zip(sources, before, strict=True):
                            if state is not None:
                                suffix = source.name[len(self.path.name) :]
                                shutil.copyfile(source, Path(f"{copy}{suffix}"))
                    except FileNotFoundError:
                        continue
                    if before == [stamp(path) for path in sources]:
                        return self._inspect_file(copy)
        except (FileNotFoundError, DirectoryHistoryError):
            raise
        except OSError as exc:
            raise DirectoryHistoryError(f"Cannot inspect directory history: {exc}") from exc
        raise DirectoryHistoryError("History changed during inspection; retry doctor")

    @staticmethod
    def _inspect_file(path: Path) -> tuple[int, int]:
        try:
            with closing(sqlite3.connect(database_uri(path) + "?mode=ro", uri=True)) as db:
                db.execute("PRAGMA query_only = ON")
                db.execute("PRAGMA trusted_schema = OFF")
                db.execute("BEGIN")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (1, 2, 3):
                    raise DirectoryHistoryError(f"Unsupported directory history schema: {version}")
                if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise DirectoryHistoryError("SQLite integrity check failed")
                columns = "identity,path,count,used,preference"
                if version >= 2:
                    columns += ",learned,tracked_used"
                    db.execute("SELECT key FROM history_metadata LIMIT 0").close()
                if version >= 3:
                    columns += ",name_key,path_key"
                    _read_revision(db)
                    db.execute(
                        "SELECT name_key FROM visits INDEXED BY visits_by_name "
                        "WHERE learned=1 LIMIT 0"
                    ).close()
                db.execute("SELECT " + columns + " FROM visits LIMIT 0").close()
                count = db.execute("SELECT count(*) FROM visits").fetchone()[0]
                return version, count
        except DirectoryHistoryError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise DirectoryHistoryError(f"Cannot inspect directory history: {exc}") from exc

    def _import_legacy(self, db: sqlite3.Connection) -> None:
        from pcd_cli.history import load_legacy

        # The marker and additive merge commit together, even when another process
        # initializes the database at the same time or an import fails halfway.
        for path, usage in load_legacy(self.path.with_name("history.json")).items():
            db.execute(
                """INSERT INTO visits(identity,path,count,used,learned) VALUES(?,?,?,?,0)
                ON CONFLICT(identity) DO UPDATE SET count=count+excluded.count,
                used=MAX(used,excluded.used)""",
                (os.path.normcase(str(path)), str(path), usage.count, usage.used_at),
            )
        db.execute("INSERT INTO history_metadata(key) VALUES('legacy_imported')")

    def record(self, path: Path, *, project_only: bool = False, limit: int = 2048) -> None:
        used = time.time()
        with self._connection(write=True) as db:
            db.execute(
                """INSERT INTO visits(identity,path,count,used,learned,tracked_used,name_key,path_key)
                VALUES(?,?,1,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET
                count=count+1, used=MAX(used,excluded.used), learned=MAX(learned,excluded.learned),
                tracked_used=MAX(tracked_used,excluded.tracked_used),
                name_key=excluded.name_key,path_key=excluded.path_key""",
                (
                    os.path.normcase(str(path)),
                    str(path),
                    used,
                    not project_only,
                    0 if project_only else used,
                    path.name.casefold(),
                    _path_key(str(path)),
                ),
            )
            if project_only:
                db.execute(
                    """DELETE FROM visits WHERE rowid IN (
                    SELECT rowid FROM visits WHERE learned=0
                    ORDER BY used DESC,count DESC,path DESC LIMIT -1 OFFSET ?)""",
                    (max(0, limit),),
                )
            db.execute("UPDATE history_metadata SET value=value+1 WHERE key='revision'")

    def revision(self) -> int:
        """A committed ranking revision, read before query candidates and usage."""
        try:
            with self._connection() as db:
                return _read_revision(db)
        except _HistoryNotFoundError:
            return 0

    def usage(self) -> dict[Path, ProjectUsage]:
        try:
            with self._connection() as db:
                return {
                    Path(path): ProjectUsage(used, count, preference)
                    for path, count, used, preference in db.execute(
                        "SELECT path,count,used,preference FROM visits"
                    )
                }
        except _HistoryNotFoundError:
            return {}

    def usage_for(self, paths: Iterator[Path]) -> dict[Path, ProjectUsage]:
        """Fetch candidate usage through the primary key, without reading all visits."""
        result: dict[Path, ProjectUsage] = {}
        try:
            # Candidate iteration can perform slow filesystem checks; never
            # keep a history transaction open while advancing that iterator.
            while batch := list(islice(paths, 500)):
                identities = {os.path.normcase(str(path)): path for path in batch}
                placeholders = ",".join("?" for _ in identities)
                with self._connection() as db:
                    rows = db.execute(
                        "SELECT identity,count,used,preference FROM visits WHERE identity IN ("
                        + placeholders
                        + ")",
                        tuple(identities),
                    ).fetchall()
                for identity, count, used, preference in rows:
                    result[identities[identity]] = ProjectUsage(used, count, preference)
        except _HistoryNotFoundError:
            pass
        return result

    def entries(self, query: str | None = None) -> dict[Path, Visit]:
        return {
            Path(path): Visit(count, used, preference)
            for path, _, count, used, preference, _ in self.candidates(query or "")
        }

    def candidates(self, query: str = "") -> Generator[DirectoryEntry]:
        """Read bounded batches, releasing SQLite locks before live path checks."""
        needle = query.casefold()
        groups: tuple[tuple[str, tuple[str, ...]], ...]
        if needle:
            clause, parameters = _candidate_clause(needle)
            groups = (
                (" AND name_key=?", (needle,)),
                (" AND name_key != ?" + clause, (needle, *parameters)),
            )
        else:
            groups = (("", ()),)
        try:
            with self._connection() as db:
                # Finish schema inspection/migration, then let each fully fetched
                # SELECT own its short read transaction. Reuse the connection;
                # reopening and validating every batch hurts broad-search latency.
                db.commit()
                for clause, parameters in groups:
                    after: str | None = None
                    while True:
                        sql = (
                            "SELECT path,name_key,count,used,preference,identity FROM visits "
                            "WHERE learned=1" + clause
                        )
                        boundary = " AND identity > ?" if after is not None else ""
                        values = (*parameters, after) if after is not None else parameters
                        with closing(
                            db.execute(sql + boundary + " ORDER BY identity LIMIT 500", values)
                        ) as cursor:
                            rows = cursor.fetchall()
                        if not rows:
                            break
                        # No statement or transaction survives this yield: a
                        # consumer may spend seconds checking offline paths.
                        yield from rows
                        after = rows[-1][5]
                        if len(rows) < 500:
                            break
        except _HistoryNotFoundError:
            return

    def merge(self, scores: Mapping[Path, float]) -> None:
        if scores:
            with self._connection(write=True) as db:
                before = db.total_changes
                db.executemany(
                    """INSERT INTO visits(identity,path,preference,learned,name_key,path_key)
                    VALUES(?,?,?,1,?,?)
                    ON CONFLICT(identity) DO UPDATE SET preference=excluded.preference,learned=1
                    WHERE preference != excluded.preference OR learned=0""",
                    (
                        (
                            os.path.normcase(str(path)),
                            str(path),
                            score,
                            path.name.casefold(),
                            _path_key(str(path)),
                        )
                        for path, score in scores.items()
                    ),
                )
                if db.total_changes != before:
                    db.execute("UPDATE history_metadata SET value=value+1 WHERE key='revision'")

    def resume(self, root: Path) -> Path | None:
        prefix = os.path.join(os.path.normcase(str(root)), "")
        end = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        try:
            with self._connection() as db:
                row = db.execute(
                    """SELECT path FROM visits WHERE identity >= ? AND identity < ?
                    AND identity != ? AND tracked_used > 0
                    ORDER BY tracked_used DESC, identity LIMIT 1""",
                    (prefix, end, os.path.normcase(str(root))),
                ).fetchone()
        except _HistoryNotFoundError:
            return None
        return Path(row[0]) if row else None


def _read_revision(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT value FROM history_metadata WHERE key='revision'").fetchone()
    if row is None or not isinstance(row[0], int) or row[0] < 0:
        raise DirectoryHistoryError("Invalid directory history revision")
    return row[0]


def _legacy_exists(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.with_name("history.json").stat().st_mode)
    except FileNotFoundError:
        return False


def _name_key(path: str) -> str:
    return Path(path).name.casefold()


def _path_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _candidate_clause(needle: str) -> tuple[str, tuple[str, ...]]:
    """A necessary subsequence condition; Python remains the ranking authority."""
    words = needle.split()
    text = "".join(words) if len(words) > 1 else needle
    # Bound SQLite's LIKE recursion/length work and avoid its NUL terminator
    # semantics. The unrestricted fallback preserves matching for unusual input.
    if len(text) > 256 or "\0" in text:
        return "", ()
    pattern = "%" + "%".join("\\" + char if char in "\\%_" else char for char in text) + "%"
    column = "path_key" if len(words) > 1 else "name_key"
    # A basename containing spaces can be an exact match independently of the
    # component matcher. Exact names were already selected by the indexed query.
    return f" AND {column} LIKE ? ESCAPE '\\'", (pattern,)
