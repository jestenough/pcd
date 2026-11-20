"""Line protocol for the PowerShell session's private child process."""

import json
import os
import sqlite3
import sys
from pathlib import Path

from pcd_cli.config import InvalidConfigError
from pcd_cli.entrypoint import _COMMANDS
from pcd_cli.filesystem import ApplicationPaths
from pcd_cli.storage import SQLiteProjectStore
from pcd_cli.visits import DirectoryVisits, record_current_directory

if sys.platform == "win32":

    def _local_volume(path: Path) -> bool:
        if sqlite3.sqlite_version_info < (3, 51, 3):
            return False
        import ctypes

        volume = ctypes.create_unicode_buffer(32768)
        kernel = ctypes.windll.kernel32
        resolved = str(path.resolve())
        return bool(
            not resolved.startswith("\\\\")
            and kernel.GetVolumePathNameW(resolved, volume, len(volume))
            and kernel.GetDriveTypeW(volume.value) == 3
        )
else:

    def _local_volume(path: Path) -> bool:
        return False


def serve() -> None:
    writer: sqlite3.Connection | None = None
    current: Path | None = None

    configure_journal = False

    def open_history(path: Path) -> DirectoryVisits:
        nonlocal writer, current, configure_journal
        if writer is None or current != path:
            if writer is not None:
                writer.close()
                writer = None
            path.parent.mkdir(parents=True, exist_ok=True)
            writer = sqlite3.connect(path)
            writer.execute("PRAGMA synchronous=FULL")
            configure_journal = _local_volume(path)
            current = path
        return DirectoryVisits(path, writer)

    # ASCII JSON replies also work with Windows PowerShell's legacy encoding.
    try:
        for line in sys.stdin.buffer:
            try:
                operation, value, exclusions = json.loads(line.decode("utf-8-sig"))
                if operation == "lookup":
                    result = ""
                    if value and not value.startswith("-") and value not in _COMMANDS:
                        paths = ApplicationPaths.resolve()
                        store = SQLiteProjectStore(paths.cache, config_path=paths.config)
                        target = store.unique_cached_path(value)
                        if target and target.is_dir() and str(target).isprintable():
                            result = str(target)
                elif operation in ("project", "track") and Path(value).is_absolute():
                    os.environ["PCD_EXCLUDE_DIRS"] = exclusions or ""
                    committed = record_current_directory(
                        project_only=operation == "project",
                        directory=Path(value),
                        open_history=open_history,
                    )
                    if committed and configure_journal and writer is not None:
                        configure_journal = False
                        # Racing readers may prevent the switch. The visit has committed.
                        try:
                            writer.execute("PRAGMA busy_timeout=0")
                            writer.execute("PRAGMA journal_mode=WAL").fetchone()
                        except sqlite3.Error:
                            pass
                        finally:
                            writer.execute("PRAGMA busy_timeout=5000")
                    result = "ok"
                else:
                    result = ""
            except (OSError, ValueError, InvalidConfigError) as exc:
                from pcd_cli.display import terminal_text

                print(terminal_text(str(exc)), file=sys.stderr)
                result = ""
            print(json.dumps(result, ensure_ascii=True), flush=True)
    finally:
        if writer is not None:
            writer.close()
