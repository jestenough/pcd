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

Tab completion requires Bash 4.4 or later. Navigation and optional directory
tracking also work with the older Bash shipped by macOS.

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

Everyday navigation (opt-in)
----------------------------

Ordinary directory changes are remembered only when tracking is enabled.
For manual initialization, add ``--track-cd``::

    eval "$(command pcd shell init bash --track-cd)"
    command pcd shell init fish --track-cd | source

Use the corresponding shell name in the existing initialization block. For
managed installations, set ``PCD_TRACK_CD=1`` in the startup file **before**
the managed block. In Bash/Zsh use ``export``; in Fish
use ``set -gx``; in PowerShell use ``$env:PCD_TRACK_CD = '1'``. Remove that
setting and start a new shell to disable tracking.

Tracking remembers successful changes, including non-Git directories, and
honors configured path exclusions. Failed changes, canceled pickers, and
unchanged directories do not add visits. Each observed change is recorded
once; wrapper jumps and tracking hooks share a directory guard. An unchanged
prompt starts no additional process. History failures do not prevent a
successful directory change. Default ``pcd`` integration still records project
usage after a successful jump without learning arbitrary directories.

Bash wraps the builtin ``cd`` when no existing ``cd`` function or alias is
present; it preserves a custom implementation and observes its final directory
at the next prompt. The prompt hook also catches changes through other commands.
An existing custom ``cd`` that changes directories several times before a
prompt records only its final location. Bash preserves scalar or array
``PROMPT_COMMAND``. Zsh uses ``chpwd_functions`` and Fish watches ``PWD``.
PowerShell 7 uses the location-change event and preserves an existing handler.
Windows PowerShell 5.1 uses a generated proxy for the native ``Set-Location``
command, retaining its parameters and provider behavior; a pre-existing
``Set-Location`` function is preserved. PowerShell also retains the original
prompt and checks for changes there, covering other commands and custom
functions. Changes only observed at a prompt record the final location.
Only filesystem locations are learned.

CMD options and limitations
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Install the normal CMD wrapper first. Enable optional DOSKEY macros explicitly
in each interactive Command Prompt (or in your own startup script)::

    set PCD_TRACK_CD=1
    pcd _shell-setup

The setup command checks for existing ``cd``/``chdir`` macros before adding
bindings. Existing bindings are preserved.
``cd`` and ``chdir`` macros forward successful directory changes through the
wrapper and share its post-change tracking.

CMD has no supported prompt callback. DOSKEY macros apply to commands entered
interactively, and do not expand inside batch scripts, ``cmd /c`` or compound
commands after a separator. Directory changes that bypass these macros are not
learned. Paths containing spaces work with or without quotes, for example
``cd Program Files`` and ``cd /d "Program Files"``. The
wrapper preserves literal exclamation marks and carets in paths returned by
navigation. Remove macros with ``doskey cd=`` and ``doskey chdir=`` or open a new
shell without the setup command to disable them.

Migration from older versions
-----------------------------

Since 0.2.3, ``pcd shell print`` and ``pcd shell-init`` have been removed. Replace
old manual startup lines with the matching ``pcd shell init`` code above, then
reload the shell. Old manual lines are not rewritten automatically.

Python callers must import ``pcd_cli.integrations.shell`` instead of
``pcd_cli.shell_integration``.

See also: `other startup problems <troubleshooting.rst>`_ and the `README <../README.txt>`_.
