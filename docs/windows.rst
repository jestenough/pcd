Windows troubleshooting
=======================

Two different restrictions can prevent pcd from working:

* **Execution Policy** can block loading a PowerShell ``.ps1`` profile.
* **Application Control** can block starting ``pcd.exe`` itself.

Changing Execution Policy does not resolve an executable block.
For installation instructions, see `shell setup <shells.rst>`_.

PowerShell Execution Policy
---------------------------

PowerShell can reject ``Microsoft.PowerShell_profile.ps1`` before pcd starts,
with "running scripts is disabled on this system"
(``PSSecurityException`` / ``UnauthorizedAccess``). The profile then cannot
activate directory changes or completion.

On Windows, ``shell install`` and ``shell status`` query ``Get-ExecutionPolicy``
in the selected ``powershell.exe`` or ``pwsh.exe`` with ``-NoProfile``. Install
stops before changing the profile under ``Restricted`` (profiles blocked),
``AllSigned`` (editing requires re-signing), or when the query fails. Status warns
about these conditions even if integration is already configured. pcd does not
change your execution policy.

To diagnose the problem, open the affected shell without loading its profile::

    powershell.exe -NoProfile

Or, for PowerShell 7+::

    pwsh.exe -NoProfile

Then run::

    Get-ExecutionPolicy -List

If ``MachinePolicy`` or ``UserPolicy`` is defined, follow your administrator's
policy. Otherwise, if you choose to allow local scripts for your account, you
can run::

    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

This persists for your account and normally requires no administrator rights.
Verify ``Get-ExecutionPolicy`` in the affected session: a ``Process`` setting
can override ``CurrentUser``. Retry ``pcd shell install powershell`` (or ``pwsh``),
then use its reload command.

With ``AllSigned``, add the `manual initialization block <shells.rst#powershell>`_
and have the profile signed by a trusted publisher through your organization's
signing process.

The check uses a child process with the inherited environment; it does not
execute or validate your profile. A different session or host may have different
settings. ``RemoteSigned`` can still reject downloaded or network-hosted
profiles; inspect the file and its origin before signing or unblocking it.
Do not use a system-wide ``Bypass``/``Unrestricted`` policy as a routine fix.

See `Microsoft's execution policy documentation
<https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies>`_.

Application Control
-------------------

"An Application Control policy has blocked this file" when starting ``pcd.exe``
(``ApplicationFailedException`` / ``NativeCommandFailed``, sometimes WinError 4551)
means Windows refused to launch the program. The Python CLI never starts, so it
cannot diagnose or repair this block itself. ``RemoteSigned``,
``Set-ExecutionPolicy``, or unblocking the ``.ps1`` profile do not grant permission
to run the executable.

Smart App Control can reject unsigned programs whose reputation is not
established; managed App Control/AppLocker rules can also restrict execution.
An installation through pip or pipx does not guarantee a signed or approved
Windows launcher. This project's release workflow does not currently produce
a signed Windows executable.

Open ``powershell.exe -NoProfile`` (or ``pwsh.exe -NoProfile``) and identify
the executable::

    Get-Command pcd -CommandType Application | Select-Object Source
    Get-Command pcd -CommandType Application | ForEach-Object {
        Get-AuthenticodeSignature -LiteralPath $_.Source
    }

Check Windows Security > App & browser control > Smart App Control. For the exact
blocked file and policy, inspect Event Viewer > Applications and Services Logs >
Microsoft > Windows > CodeIntegrity > Operational (App Control), or AppLocker >
EXE and DLL. Reading these logs may require administrator access.

Use a distribution accepted by the device's policy, or ask the administrator
to review and approve the package. Do not disable application control as an
automatic installation step. Changing the profile only handles the startup
error; pcd remains unavailable until Windows permits it to run.

If ``pcd.exe`` is already blocked, you can edit the profile without running pcd.
Replace the old startup line with the `guarded PowerShell block
<shells.rst#powershell>`_ to report the failure and continue loading the profile.
Temporarily commenting out the old pcd startup line instead disables integration
until the executable is approved. An existing manual block is never rewritten
by the installer.

See Microsoft's `App Control troubleshooting guide
<https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/appcontrol-debugging-and-troubleshooting>`_
and `Smart App Control FAQ
<https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions>`_.

See also: `other startup problems <troubleshooting.rst>`_ and the `README <../README.txt>`_.
