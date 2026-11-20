Shell startup troubleshooting
=============================

Run ``pcd doctor`` in the affected shell to check whether pcd is on PATH and
whether it can detect the shell integration. Use ``pcd shell status <shell>`` to
find the installed file. Check startup errors and run ``pcd --version`` in that
same shell.
A configured file does not prove that the current session loaded it successfully.

For Windows profile restrictions or a blocked ``pcd.exe``, see the separate
`Windows troubleshooting guide <windows.rst>`_. PowerShell Execution Policy is
enforced on Windows, not Linux/macOS.

Common causes
-------------

An unreadable startup file, syntax errors, pcd missing from PATH, or a shell
started without its usual configuration can prevent integration from loading.
Check that you installed integration for the shell and host you actually use.
See `shell setup <shells.rst>`_ for startup files and reload commands.

Bash login shells
-----------------

Bash login shells read different startup files from interactive non-login
shells. Installing in ``~/.bashrc`` alone may require your login profile to
source it. See the `Bash startup file documentation
<https://www.gnu.org/software/bash/manual/html_node/Bash-Startup-Files.html>`_.

macOS Gatekeeper
----------------

Gatekeeper can block downloaded applications or executables. This is separate
from loading a shell profile; follow the diagnostic for the blocked application
instead of changing PowerShell settings. See `Apple's instructions
<https://support.apple.com/en-us/102445>`_.

Navigation history
------------------

``pcd doctor`` also checks the integrity and schema of ``directories.sqlite3``.
History is optional: a missing database is normal before the first recorded
visit. Existing ``history.json`` and older SQLite schemas are reported without
being migrated or changed. The check never refreshes the project catalog or
creates history, a journal, or SQLite shared-memory files.

If history is unreadable or corrupt, doctor reports a History issue. Keep a
backup before attempting recovery: unlike the project catalog, navigation
history cannot be rebuilt with ``pcd refresh``. Restore the history database
from a known good backup, or move it aside while pcd is idle to start fresh.
Moving it aside loses recorded visits and imported preferences; a retained
``history.json`` will be imported again on the next ordinary history access.

pcd uses rollback journals. Doctor refuses externally enabled WAL journaling
before opening the database because even a read-only SQLite connection can
create sidecars. Restore ``DELETE`` journal mode with SQLite while pcd is idle
before inspecting it. Normal lookup releases history read transactions between
bounded batches, so slow filesystem checks do not hold up concurrent visit
recording.

Return to the `README <../README.txt>`_.
