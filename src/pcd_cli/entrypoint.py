"""Start shell-generated visit notifications without loading the command tree."""

import os
import sys


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

    from pcd_cli.cli import main as cli_main

    cli_main()
