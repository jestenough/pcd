Commands and navigation
=======================

See the `README <../README.txt>`_ for installation and a quick start.

Command reference
-----------------

::

    pcd <project>                Jump to a project
    pcd                          Show help

    pcd add [path] [--name NAME]  Register a directory or restore an excluded project
    pcd remove <project>         Unregister or exclude a project, keeping its directory
    pcd info <project>           Show paths, source, discovery routes and status
    pcd doctor                   Check configuration, cache, paths and shell integration

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

Inspect, remove and restore projects
------------------------------------

Use ``pcd info NAME`` to inspect a project without changing directory or recording
a visit. The status reflects whether its directory exists now. Both ``info`` and
``remove`` accept an exact, case-insensitive name, complete manual and discovered
names from the cache, and offer a picker for duplicates.

For example, to hide a discovered checkout and later restore it::

    pcd info pigeon-surveillance
    pcd remove pigeon-surveillance
    pcd refresh
    pcd add ~/development/github/palantir/backend/pigeon-surveillance

Removing a discovered checkout excludes its canonical path across all scan roots.
Other checkouts with the same name remain available. The exclusion survives refresh
and cache rebuilding. Removing a manual project unregisters it. Its directory remains
on disk and can be discovered again if it is a Git checkout inside a scan root.

Adding an excluded directory restores discovery when a registered root still finds
it. Use ``--name NAME`` to restore it as a manual registration. An existing directory
outside discovery is also restored as manual. Refresh drops vanished discoveries
from the cache and keeps user-defined roots, manual registrations and exclusions.

Run ``pcd doctor`` for a read-only check of configuration, cache integrity, roots,
project directories, stale exclusions, the executable on PATH, and shell integration.
It also reports excluded projects still in the cache and manual registrations
that disagree with the cache. It returns status 1 when it finds an issue.
An undetectable shell is reported as unknown. Use ``pcd shell status SHELL`` for a
specific shell. If you suspect a stale reference, run doctor before refreshing so it
can also inspect discoveries still present in the old cache.

Search and selection
--------------------

Use several words to match path components in order. For example,
``pcd acme api`` can distinguish ``~/clients/acme/api`` from another checkout
named ``api``. A unique exact project name still takes precedence. With shell
directory tracking enabled, ordinary directories you visit also become
destinations. See the `shell setup guide <shells.rst>`_ for the opt-in hooks.

``pcd PROJECT --resume`` opens the most recently visited subdirectory recorded
under the project. If that directory disappeared, pcd explains the fallback
and opens the project root. Use ``pcd PROJECT`` to open the root directly.

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
