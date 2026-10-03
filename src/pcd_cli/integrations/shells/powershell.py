"""Windows PowerShell 5.1 and PowerShell 7+ integration backend."""

import os
import sys
from pathlib import Path
from textwrap import dedent

from pcd_cli.integrations.shells import startup_file
from pcd_cli.integrations.shells.common import (
    Shell,
    SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    SHELL_WRAPPER_ENV,
    ShellIntegrationState,
)

if sys.platform == "win32":
    import winreg


def config_path(shell: Shell, home: Path) -> Path:
    documents = _documents_path(home)
    if shell is Shell.POWERSHELL:
        return documents / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"

    if shell is Shell.PWSH:
        return documents / "PowerShell" / "Microsoft.PowerShell_profile.ps1"

    raise ValueError(f"Unsupported PowerShell: {shell}")


def reload_command(shell: Shell, path: Path) -> str:
    _require_supported(shell)
    escaped = str(path).replace("'", "''")
    return f". '{escaped}'"


def startup_command(shell: Shell) -> str:
    _require_supported(shell)
    return f"pcd shell init {shell.value} | Out-String | Invoke-Expression"


def state(shell: Shell, path: Path) -> ShellIntegrationState:
    _require_supported(shell)
    return startup_file.state(path, shell)


def install(shell: Shell, path: Path) -> bool:
    return startup_file.install(path, shell, startup_command(shell))


def uninstall(shell: Shell, path: Path) -> bool:
    _require_supported(shell)
    return startup_file.uninstall(path)


def render(shell: Shell) -> str:
    _require_supported(shell)
    return dedent(
        f"""\
        function pcd {{
            [CmdletBinding()]
            param(
                [Parameter(ValueFromRemainingArguments = $true)]
                [string[]] $PcdArguments
            )

            $hadPreviousWrapper = Test-Path Env:{SHELL_WRAPPER_ENV}
            $previousWrapper = $env:{SHELL_WRAPPER_ENV}
            $env:{SHELL_WRAPPER_ENV} = '{shell.value}'
            try {{
                $pcdExecutable = (
                    Get-Command pcd -CommandType Application -ErrorAction Stop |
                    Select-Object -First 1
                ).Source
                if ($env:_PCD_COMPLETE) {{
                    & $pcdExecutable @PcdArguments
                    return
                }}

                $hadPreviousShellMode = Test-Path Env:{SHELL_MODE_ENV}
                $previousShellMode = $env:{SHELL_MODE_ENV}
                $env:{SHELL_MODE_ENV} = '1'
                try {{
                    $output = & $pcdExecutable @PcdArguments
                    $code = $LASTEXITCODE
                }} finally {{
                    if ($hadPreviousShellMode) {{
                        $env:{SHELL_MODE_ENV} = $previousShellMode
                    }} else {{
                        Remove-Item Env:{SHELL_MODE_ENV} -ErrorAction SilentlyContinue
                    }}
                }}

                if ($code -eq {SHELL_CD_EXIT_CODE}) {{
                    Set-Location -LiteralPath ([string] $output)
                    $global:LASTEXITCODE = 0
                    return
                }}
                if ($null -ne $output) {{
                    Write-Output $output
                }}
                $global:LASTEXITCODE = $code
            }} finally {{
                if ($hadPreviousWrapper) {{
                    $env:{SHELL_WRAPPER_ENV} = $previousWrapper
                }} else {{
                    Remove-Item Env:{SHELL_WRAPPER_ENV} -ErrorAction SilentlyContinue
                }}
            }}
        }}

        $hadCompletionMode = Test-Path Env:_PCD_COMPLETE
        $previousCompletionMode = $env:_PCD_COMPLETE
        $env:_PCD_COMPLETE = 'powershell_source'
        try {{
            $pcdExecutable = Get-Command pcd -CommandType Application -ErrorAction Stop |
                Select-Object -First 1
            $completion = & $pcdExecutable.Source
            if ($completion) {{
                $completion | Out-String | Invoke-Expression
            }}
        }} finally {{
            if ($hadCompletionMode) {{
                $env:_PCD_COMPLETE = $previousCompletionMode
            }} else {{
                Remove-Item Env:_PCD_COMPLETE -ErrorAction SilentlyContinue
            }}
        }}
        """
    )


def _require_supported(shell: Shell) -> None:
    if shell not in (Shell.POWERSHELL, Shell.PWSH):
        raise ValueError(f"Unsupported PowerShell: {shell}")


def _documents_path(home: Path) -> Path:
    if sys.platform != "win32":
        return home / "Documents"

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, "Personal")
    except FileNotFoundError:
        return home / "Documents"

    return Path(os.path.expandvars(str(value)))
