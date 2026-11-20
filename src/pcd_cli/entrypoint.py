"""Handle common shell operations without loading the Click command tree."""

import os
import sys

# Click commands take precedence over project names. Checked against cli.commands
# in tests so adding a command cannot silently turn it into a shell jump.
_COMMANDS = frozenset(
    [
        "project",
        "add",
        "remove",
        "info",
        "list",
        "init",
        "uninit",
        "roots",
        "refresh",
        "doctor",
        "import",
        "_visit",
        "config",
        "shell",
    ]
)


def main() -> None:
    args = sys.argv[1:]
    if args in (["_visit"], ["_visit", "--project-only"]) and not os.environ.get("_PCD_COMPLETE"):
        from pcd_cli.config import InvalidConfigError
        from pcd_cli.visits import record_current_directory

        try:
            record_current_directory(project_only=len(args) == 2)
        except InvalidConfigError as exc:
            print(f"Config error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        return

    if (
        len(args) == 1
        and args[0]
        and not args[0].startswith("-")
        and args[0] not in _COMMANDS
        and os.environ.get("PCD_SHELL") == "1"
        and not os.environ.get("_PCD_COMPLETE")
    ):
        from pcd_cli.filesystem import ApplicationPaths
        from pcd_cli.integrations.shells.common import SHELL_CD_EXIT_CODE
        from pcd_cli.storage import SQLiteProjectStore

        try:
            paths = ApplicationPaths.resolve()
            store = SQLiteProjectStore(paths.cache, config_path=paths.config)
            destination = store.unique_cached_path(args[0])
            if destination is not None and destination.is_dir() and str(destination).isprintable():
                print(destination)
                raise SystemExit(SHELL_CD_EXIT_CODE)
        except (OSError, UnicodeError):
            # Let normal CLI dispatch handle filesystem and terminal errors.
            pass

    from pcd_cli.cli import main as cli_main

    cli_main()
