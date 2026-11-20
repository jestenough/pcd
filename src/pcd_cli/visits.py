"""Persistent navigation state, independent of the rebuildable project catalog."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from fnmatch import fnmatch
from itertools import chain, islice
from pathlib import Path
from typing import TYPE_CHECKING

from pcd_cli.filesystem import database_uri
from pcd_cli.models import ProjectUsage

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from pcd_cli.lookup import DirectoryEntry


@dataclass(frozen=True, slots=True)
class Visit:
    count: int
    used_at: float
    preference: float


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

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if not write and not self.path.exists():
            if self.path.with_name("history.json").is_file():
                with self._connection(write=True) as db:
                    yield db
                return
            raise FileNotFoundError(self.path)
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self.path)
            else:
                db = sqlite3.connect(database_uri(self.path) + "?mode=ro", uri=True)
            with closing(db), db:
                db.execute("PRAGMA trusted_schema = OFF")
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if (
                    version == 0
                    and not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
                ):
                    if not write:
                        # A concurrent first writer has not yet published its schema.
                        if self.path.with_name("history.json").is_file():
                            db.rollback()
                            with self._connection(write=True) as initialized:
                                yield initialized
                            return
                        raise FileNotFoundError(self.path)
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
        except sqlite3.Error as exc:
            raise OSError(f"Cannot access directory history: {exc}") from exc

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
                    """DELETE FROM visits WHERE learned=0 AND identity NOT IN (
                    SELECT identity FROM visits WHERE learned=0
                    ORDER BY used DESC,count DESC,path DESC LIMIT ?)""",
                    (max(0, limit),),
                )
            db.execute("UPDATE history_metadata SET value=value+1 WHERE key='revision'")

    def revision(self) -> int:
        """A committed ranking revision, read before query candidates and usage."""
        try:
            with self._connection() as db:
                row = db.execute(
                    "SELECT value FROM history_metadata WHERE key='revision'"
                ).fetchone()
                return int(row[0])
        except FileNotFoundError:
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
        except FileNotFoundError:
            return {}

    def usage_for(self, paths: Iterator[Path]) -> dict[Path, ProjectUsage]:
        """Fetch candidate usage through the primary key, without reading all visits."""
        result: dict[Path, ProjectUsage] = {}
        try:
            with self._connection() as db:
                while batch := list(islice(paths, 500)):
                    identities = {os.path.normcase(str(path)): path for path in batch}
                    placeholders = ",".join("?" for _ in identities)
                    for identity, count, used, preference in db.execute(
                        "SELECT identity,count,used,preference FROM visits WHERE identity IN ("
                        + placeholders
                        + ")",
                        tuple(identities),
                    ):
                        result[identities[identity]] = ProjectUsage(used, count, preference)
        except FileNotFoundError:
            pass
        return result

    def entries(self, query: str | None = None) -> dict[Path, Visit]:
        return {
            Path(path): Visit(count, used, preference)
            for path, _, count, used, preference, _ in self.candidates(query or "")
        }

    def candidates(self, query: str = "") -> Iterator[DirectoryEntry]:
        """Stream stored ranking metadata without constructing paths or a full map."""
        try:
            with self._connection() as db:
                sql = "SELECT path,name_key,count,used,preference,identity FROM visits WHERE learned=1"
                needle = query.casefold()
                if needle:
                    # Exact names use the partial index while retaining weaker
                    # matches. Identity order clusters siblings for live checks.
                    clause, parameters = _candidate_clause(needle)
                    exact = db.execute(sql + " AND name_key=? ORDER BY identity", (needle,))
                    rows = db.execute(
                        sql + " AND name_key != ?" + clause + " ORDER BY identity",
                        (needle, *parameters),
                    )
                    candidates = chain(exact, rows)
                else:
                    candidates = chain(db.execute(sql + " ORDER BY identity"))
                yield from candidates
        except FileNotFoundError:
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
        except FileNotFoundError:
            return None
        return Path(row[0]) if row else None


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
