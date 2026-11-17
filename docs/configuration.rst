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

See also: `commands and navigation <usage.rst>`_ and the `README <../README.txt>`_.
