Commands and navigation
=======================

See the `README <../README.txt>`_ for installation and a quick start.

Command reference
-----------------

::

    pcd <project>                Jump to a project
    pcd                          Show help

    pcd add [path] [--name NAME]  Register a directory (default: current directory)
    pcd remove <project>         Remove a manual registration, keeping its directory

    pcd init                     Add the current directory as a scan root
    pcd uninit                   Remove the current directory from scan roots
    pcd roots                    List scan roots
    pcd refresh                  Rescan roots, show added, removed, and total projects

    pcd list [options]           List and filter known projects
    pcd config path              Print the configuration file path
    pcd config show              Print the effective configuration
    pcd config edit              Edit the user configuration
    pcd config validate          Validate the user configuration

    pcd shell install [shell]    Install shell integration
    pcd shell init [shell]       Print shell integration code
    pcd shell status [shell]     Show shell integration status
    pcd shell uninstall [shell]  Remove managed shell integration

    pcd --project <project>      Jump to a project whose name matches a command
    pcd --version                Show version
    pcd --help                   Show help

Discovery and refresh
---------------------

pcd discovers Git repositories, identified by a ``.git`` directory or worktree
file, inside registered scan roots. Ordinary directories can be registered with
``pcd add``. You can register multiple scan roots.

Roots are scanned when registered and when the catalog must be rebuilt. A search
from the command line with no matching cached project rescans once and retries.
There is no background watcher; use ``pcd refresh`` to update an existing cache explicitly.

Search and selection
--------------------

Search is case-insensitive: exact names take priority, then prefix, substring,
and fuzzy matches. ``--project`` performs exact-name lookup only.

When several projects match, type to filter the picker, use Up/Down to move,
Enter to select, or Escape/Ctrl-C to cancel. Editing the query searches the whole
cached catalog; it never scans the filesystem. Clear the query to browse by
recency. The picker keeps at most 100 results in memory and displays 12 rows;
Up/Down loads further windows so every duplicate name remains reachable.
A command-line miss refreshes once and retries, while a picker miss stays empty.

Filter ``pcd list`` with ``--manual``, ``--discovered``, or ``--missing``.
Use ``pcd list --json`` for machine-readable output.

Tables, picker results and project messages display terminal control characters
as visible escapes, such as ``\x1b`` and ``\n``. Stored names and JSON values retain
their original contents. Names containing these controls are omitted from shell
completion because completion uses a line-based protocol.

Without active `shell integration <shells.rst>`_, pcd prints the destination and
a setup hint; it cannot change the parent shell's directory.

See also: `configuration <configuration.rst>`_ and `behavior details <../FEATURES.txt>`_.
