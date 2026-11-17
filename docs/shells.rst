Shell setup and migration
=========================

Automatic setup
---------------

Shell names are ``bash``, ``zsh``, ``fish``, ``pwsh``, ``powershell``, and ``cmd``
(Windows only). Omit the name to detect the invoking shell. On Windows,
``shell install`` prompts for a choice if detection fails; the other shell
commands require an explicit name in that situation.

Choose your shell explicitly when needed::

    pcd shell install bash
    pcd shell install pwsh

Run only the command for the shell you use. Successful installation prints its
configuration path and a reload command. It preserves manual integration and
unrelated settings; uninstall removes only integration managed by pcd.

Use ``pcd shell status <shell>`` to inspect the configuration path selected by pcd.
The reported configuration does not prove that the current session loaded it.

On Windows, check the `Execution Policy and App Control notes <windows.rst>`_.
Profile loading and executable launch restrictions are separate issues.

Manual setup
------------

Add the matching initialization code to your startup file. Manual configuration
is left unchanged by the installer.

Bash and Git Bash
~~~~~~~~~~~~~~~~~

In ``~/.bashrc``::

    eval "$(command pcd shell init bash)"

Reload::

    source ~/.bashrc

Zsh
~~~

In ``~/.zshrc``, or ``.zshrc`` under ``ZDOTDIR``::

    eval "$(command pcd shell init zsh)"

Reload::

    source "${ZDOTDIR:-$HOME}/.zshrc"

Fish
~~~~

In ``~/.config/fish/config.fish``, or ``fish/config.fish`` under
``XDG_CONFIG_HOME``::

    command pcd shell init fish | source

Reload the default file::

    source ~/.config/fish/config.fish

If ``XDG_CONFIG_HOME`` is set, source the ``config.fish`` at that location instead.

.. _powershell:

PowerShell
~~~~~~~~~~

In your PowerShell profile, use this guarded startup block. Replace ``powershell``
with ``pwsh`` for PowerShell 7+ (also usable on Linux/macOS)::

    try {
        $__pcdInit = pcd shell init powershell
        if ($LASTEXITCODE -ne 0) {
            throw "pcd shell init failed (exit code $LASTEXITCODE)."
        }
        if ($__pcdInit) {
            $__pcdInit | Out-String | Invoke-Expression
        }
    } catch {
        $global:LASTEXITCODE = 1
        $__pcdFailure = $_.Exception
        if ($__pcdFailure.InnerException) {
            $__pcdFailure = $__pcdFailure.InnerException
        }
        Write-Warning ("pcd: shell integration was not loaded. " + $__pcdFailure.Message)
    } finally {
        Remove-Variable __pcdInit, __pcdFailure -ErrorAction SilentlyContinue
    }

Launch failures produce a warning and allow the rest of the profile to load.
Initialization code from an unsuccessful command is not evaluated. Automatic
installation also writes a guarded block; rerun ``shell install`` after upgrading
to update an existing managed block, once pcd is allowed to run.

Reload the current host's profile::

    . $PROFILE

Automatic installation targets ``Microsoft.PowerShell_profile.ps1``; other hosts
may use a different ``$PROFILE``. Use the installer's reload command for the
installed file. See `Windows troubleshooting <windows.rst>`_ if loading fails.

Command Prompt (CMD)
~~~~~~~~~~~~~~~~~~~~

Use ``pcd shell install cmd`` and its printed DOSKEY reload command, or open a
new Command Prompt. CMD uses a wrapper plus a DOSKEY command in the current user's
AutoRun value, without changing PATH.

Migration from older versions
-----------------------------

Since 0.2.3, ``pcd shell print`` and ``pcd shell-init`` have been removed. Replace
old manual startup lines with the matching ``pcd shell init`` code above, then
reload the shell. Old manual lines are not rewritten automatically.

Python callers must import ``pcd_cli.integrations.shell`` instead of
``pcd_cli.shell_integration``.

See also: `other startup problems <troubleshooting.rst>`_ and the `README <../README.txt>`_.
