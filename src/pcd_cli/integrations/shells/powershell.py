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
from pcd_cli.integrations.shells.features import recorder


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
        warning = "pcd: shell integration was not loaded. "
        if self.platform is Platform.WINDOWS:
            warning += (
                "If Windows App Control blocked pcd.exe, use an approved installation or "
                "contact your administrator. Set-ExecutionPolicy does not unblock executables. "
                "See docs/windows.rst (Application Control). "
            )
        return dedent(
            f"""\
            try {{
                $__pcdInit = pcd shell init {self.shell.value}
                if ($LASTEXITCODE -ne 0) {{
                    throw "pcd shell init failed (exit code $LASTEXITCODE)."
                }}
                if ($__pcdInit) {{
                    $__pcdInit | Out-String | Invoke-Expression
                }}
            }} catch {{
                $global:LASTEXITCODE = 1
                $__pcdFailure = $_.Exception
                if ($__pcdFailure.InnerException) {{
                    $__pcdFailure = $__pcdFailure.InnerException
                }}
                Write-Warning ("{warning}" + $__pcdFailure.Message)
            }} finally {{
                Remove-Variable __pcdInit, __pcdFailure -ErrorAction SilentlyContinue
            }}
            """
        ).rstrip()

    def installation_issue(self) -> str | None:
        if self.platform is not Platform.WINDOWS:
            return None

        from pcd_cli.integrations.shells.powershell_policy import installation_issue

        return installation_issue(self.shell)

    def render(self) -> str:
        from pcd_cli.entrypoint import _COMMANDS

        commands = ", ".join(f"'{name}'" for name in sorted(_COMMANDS))
        return recorder(self.shell) + dedent(
            f"""\
        function pcd {{
            [CmdletBinding()]
            param(
                [Parameter(ValueFromRemainingArguments = $true)]
                [string[]] $PcdArguments
            )

            $previousWrapper = $env:{SHELL_WRAPPER_ENV}
            $env:{SHELL_WRAPPER_ENV} = '{self.shell.value}'
            try {{
                $pcdExecutable = __pcdExecutablePath
                if ($env:_PCD_COMPLETE) {{
                    & $pcdExecutable @PcdArguments
                    return
                }}

                $previousShellMode = $env:{SHELL_MODE_ENV}
                $env:{SHELL_MODE_ENV} = '1'
                try {{
                    $output = $null
                    if ($null -ne $PcdArguments -and $PcdArguments.Count -eq 1 -and $PcdArguments[0] -notlike '-*' -and
                        $PcdArguments[0] -cnotin @({commands})) {{
                        $output = __pcdWorkerRequest $pcdExecutable 'lookup' $PcdArguments[0]
                    }}
                    if ($output) {{
                        $code = {SHELL_CD_EXIT_CODE}
                    }} else {{
                        $output = & $pcdExecutable @PcdArguments
                        $code = $LASTEXITCODE
                    }}
                }} finally {{
                    $env:{SHELL_MODE_ENV} = $previousShellMode
                }}

                if ($code -eq {SHELL_CD_EXIT_CODE}) {{
                    $beforeChange = $PWD.Path
                    $global:__pcdLastDirectory = $beforeChange
                    $previousExecutable = $global:__pcdVisitExecutable
                    $global:__pcdVisitExecutable = $pcdExecutable
                    try {{
                        Set-Location -LiteralPath ([string] $output) -ErrorAction Stop
                        if (-not $global:__pcdTrackCd) {{
                            $global:__pcdLastDirectory = $beforeChange
                        }}
                        __pcdRecordVisit
                    }} catch {{
                        Write-Error -ErrorRecord $_ -ErrorAction Continue
                        $global:LASTEXITCODE = 1
                        return
                    }} finally {{
                        $global:__pcdVisitExecutable = $previousExecutable
                    }}
                    $global:LASTEXITCODE = 0
                    return
                }}
                if ($null -ne $output) {{
                    Write-Output $output
                }}
                $global:LASTEXITCODE = $code
            }} finally {{
                $env:{SHELL_WRAPPER_ENV} = $previousWrapper
            }}
        }}

        $hadCompletionMode = Test-Path Env:_PCD_COMPLETE
        $previousCompletionMode = $env:_PCD_COMPLETE
        $env:_PCD_COMPLETE = 'powershell_source'
        try {{
            $pcdExecutable = __pcdExecutablePath
            $completion = & $pcdExecutable
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
