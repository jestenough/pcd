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

Scan behavior
-------------

By default scanning includes hidden directories, skips common dependency/build
directories, and does not descend into symlinked trees. A symlink directly to a
Git repository is still discovered. Scanning stops inside a found repository;
register a nested scan root explicitly when its child repositories are needed.

The ``scan.hidden``, ``scan.follow_symlinks``, and ``scan.exclude`` settings
control scanning. ``scan.exclude`` contains directory names, not project paths
or glob patterns.

Storage and upgrades
--------------------

Roots and manual registrations remain in ``config.toml``. The disposable project
catalog uses ``projects.sqlite3`` in the platform's user-cache directory; usage stays
in ``history.json`` in the user-state directory. A valid 0.2.8 JSONL cache is
imported on the first normal read. Completion does not perform migration.

Run ``pcd refresh`` to recover an invalid or missing database. Keep the small
``projects.sqlite3.initialized`` marker: it prevents a removed database from reviving
stale legacy data. The original ``projects.jsonl`` is retained for downgrade
compatibility; after downgrading, run ``pcd refresh`` to rebuild that version's
cache. See `storage design and validation <storage-030.rst>`_.

See also: `commands and navigation <usage.rst>`_ and the `README <../README.txt>`_.
