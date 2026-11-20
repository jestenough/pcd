Configuration and scanning
==========================

Editing configuration
---------------------

Use ``pcd config path`` to locate ``config.toml`` and ``pcd config show`` to
inspect effective settings. The editor is selected from the top-level ``editor``
setting, then ``VISUAL``, then ``EDITOR``. For example, put this before any TOML
table header::

    editor = "nvim"

After changing roots, manual projects, or scan settings in the editor, run::

    pcd config validate
    pcd refresh

Configuration edits invalidate the previous project snapshot automatically.
The next ordinary lookup or listing rebuilds it. Running ``pcd refresh``
explicitly verifies the scan immediately; completion and picker query edits
remain cache-only and return no registered projects until that rebuild succeeds.

Scan behavior
-------------

By default scanning includes hidden directories, skips common dependency/build
directories, and does not descend into symlinked trees. A symlink directly to a
Git repository is still discovered. Scanning stops inside a found repository;
register a nested scan root explicitly when its child repositories are needed.

The ``scan.hidden``, ``scan.follow_symlinks``, and ``scan.exclude`` settings
control scanning. ``scan.exclude`` contains directory names, not project paths
or glob patterns.

Project exclusions
------------------

``pcd remove`` records a discovered project's canonical path in the top-level
``excluded_projects`` array in ``config.toml``. An omitted array defaults to empty.
Entries name individual project directories. A manual registration and an exclusion
for the same canonical path are rejected. ``scan.exclude`` continues to control
which directory names the scanner skips. An example before any table header::

    excluded_projects = ["~/development/archive/old-checkout"]

Use ``pcd add PATH`` to restore an excluded directory. Missing entries are kept
so a returning disk or checkout still respects the exclusion. ``pcd doctor``
reports missing paths. After editing the file manually, run ``pcd config validate``
and ``pcd refresh``.

Earlier releases reject the new TOML key. Before downgrading, back up the
configuration, remove ``excluded_projects`` from the older version's copy, and
run ``pcd refresh``. That version will discover those projects again.

Storage and upgrades
--------------------

Directory tracking and zoxide import store visit counts, timestamps and imported
scores in ``directories.sqlite3`` in the platform user-state directory. This
database also supplies the last visited subdirectory for resume. A refresh
rebuilds the separate project cache and preserves this state.

To omit private locations from tracking and import, set ``PCD_EXCLUDE_DIRS``
to glob patterns separated by the system path separator (semicolon on Windows,
colon on Unix). For example, ``~/private*`` matches paths beginning with that
prefix. Project path exclusions also cover descendant directories when learning.

Roots and manual registrations remain in ``config.toml``. The disposable project
catalog uses ``projects.sqlite3`` in the platform's user-cache directory.
Project jumps and tracked directory changes share one visit count in
``directories.sqlite3``. Switching tracking on or off preserves that count.
On upgrade, pcd adds counts from the old ``history.json`` to any visits
already recorded in SQLite. The migration is recorded in the same transaction,
so reopening the database does not add those counts again. Old project jumps
alone do not register ordinary directories or set a resume destination.

A valid 0.2.8 JSONL project cache is
imported on the first normal read. Completion does not perform migration.

Run ``pcd refresh`` to recover an invalid or missing database. Keep the small
``projects.sqlite3.initialized`` marker: it prevents a removed database from reviving
stale legacy data. The original ``projects.jsonl`` is retained for downgrade
compatibility; after downgrading, run ``pcd refresh`` to rebuild that version's
cache. See `storage design and validation <storage-030.rst>`_.

See also: `commands and navigation <usage.rst>`_ and the `README <../README.txt>`_.
