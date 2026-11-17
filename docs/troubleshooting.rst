Shell startup troubleshooting
=============================

Use ``pcd shell status <shell>`` to find the installed file, check your shell's
startup errors, and verify that ``pcd --version`` runs in that same shell.
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

Return to the `README <../README.txt>`_.
