"""Read-only Windows execution policy preflight for profile installation."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcd_cli.integrations.shells.common import Shell


def installation_issue(shell: Shell) -> str | None:
    """Check the selected PowerShell edition without loading its profiles."""
    executable = f"{shell.value}.exe"
    inspect = (
        f"Open {executable} -NoProfile and run Get-ExecutionPolicy -List. "
        "MachinePolicy/UserPolicy are managed by your administrator; "
        "a Process policy can also override CurrentUser."
    )
    try:
        result = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                # Python may inherit PS7 module paths while launching Windows PowerShell.
                # Use only the selected edition's built-in security module for this query.
                '$env:PSModulePath = "$PSHOME/Modules"; Get-ExecutionPolicy',
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        policy = ""
    else:
        policy = result.stdout.strip() if result.returncode == 0 else ""

    match policy:
        case "RemoteSigned" | "Unrestricted" | "Bypass":
            return None
        case "Restricted":
            return (
                "PowerShell execution policy is Restricted: .ps1 profiles cannot load "
                "(PSSecurityException / UnauthorizedAccess).\n"
                f"{inspect}\n"
                "If MachinePolicy/UserPolicy are Undefined and your organization permits it, "
                "you can allow local scripts for your account "
                "with: Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned\n"
                "This persists for your account. Verify with Get-ExecutionPolicy in the target "
                f"shell, then retry: pcd shell install {shell.value}. "
                "pcd never changes execution policy automatically."
            )
        case "AllSigned":
            return (
                "PowerShell execution policy is AllSigned: profiles must be signed by a trusted "
                "publisher, and automatic editing would invalidate an existing signature.\n"
                f"{inspect}\n"
                f"Configure pcd shell init {shell.value} manually as described in docs/shells.rst, "
                "then sign the profile using your organization's signing process."
            )
        case _:
            return (
                f"Cannot determine the PowerShell execution policy using {executable}. "
                "Automatic profile installation requires a successful policy check.\n"
                f"{inspect}\n"
                "Check that the selected PowerShell executable is installed and permitted to run, "
                "then retry."
            )
