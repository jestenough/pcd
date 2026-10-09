"""Select one implementation for each supported shell."""

from pcd_cli.environment import current_platform, Platform
from pcd_cli.integrations.shells.base import ShellDriver
from pcd_cli.integrations.shells.common import Shell, ShellIntegrationError


def driver_for(shell: Shell, platform: Platform | None = None) -> ShellDriver:
    platform = platform or current_platform()
    match shell:
        case Shell.BASH | Shell.ZSH | Shell.FISH:
            from pcd_cli.integrations.shells.unix import UnixDriver

            return UnixDriver(shell, platform)
        case Shell.POWERSHELL | Shell.PWSH:
            from pcd_cli.integrations.shells.powershell import PowerShellDriver

            return PowerShellDriver(shell, platform)
        case Shell.CMD:
            match platform:
                case Platform.WINDOWS:
                    from pcd_cli.integrations.shells.cmd import CmdDriver

                    return CmdDriver(shell, platform)
                case Platform.LINUX | Platform.MACOS:
                    raise ShellIntegrationError("CMD integration is only available on Windows")
    raise ValueError(f"Unsupported shell: {shell}")
