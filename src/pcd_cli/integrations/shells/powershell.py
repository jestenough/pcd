"""Windows PowerShell 5.1 and PowerShell 7+ integration backend."""

import os
from pathlib import Path
from textwrap import dedent

from pcd_cli.environment import Platform
from pcd_cli.integrations.shells.base import StartupDriver
from pcd_cli.integrations.shells.common import (
    Shell,
    SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    SHELL_WRAPPER_ENV,
)


class PowerShellDriver(StartupDriver):
    def config_path(self, home: Path) -> Path:
        match self.platform:
            case Platform.LINUX | Platform.MACOS:
                if self.shell is Shell.PWSH:
                    config_home = Path(
                        os.environ.get("XDG_CONFIG_HOME") or home / ".config"
                    ).expanduser()
                    return config_home / "powershell" / "Microsoft.PowerShell_profile.ps1"
                if self.shell is Shell.POWERSHELL:
                    return (
                        home
                        / "Documents"
                        / "WindowsPowerShell"
                        / "Microsoft.PowerShell_profile.ps1"
                    )
                raise ValueError(f"Unsupported PowerShell on POSIX: {self.shell}")
            case Platform.WINDOWS:
                from pcd_cli.integrations.shells import windows_registry

                result = windows_registry.read(
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                    "Personal",
                )
                if result is None:
                    documents = home / "Documents"
                else:
                    value, _value_type = result
                    documents = Path(os.path.expandvars(str(value)))

                if self.shell is Shell.POWERSHELL:
                    return documents / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
                if self.shell is Shell.PWSH:
                    return documents / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
                raise ValueError(f"Unsupported PowerShell: {self.shell}")

    def reload_command(self, path: Path) -> str:
        escaped = str(path).replace("'", "''")
        return f". '{escaped}'"

    def startup_command(self) -> str:
        return f"pcd shell init {self.shell.value} | Out-String | Invoke-Expression"

    def render(self) -> str:
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
            $env:{SHELL_WRAPPER_ENV} = '{self.shell.value}'
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
