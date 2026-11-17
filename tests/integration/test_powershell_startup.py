from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from pcd_cli.environment import Platform
from pcd_cli.integrations.shell import ConfiguredShell, Shell, ShellChange
from pcd_cli.integrations.shells.powershell import PowerShellDriver

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
def test_installer_upgrades_old_powershell_bootstrap(tmp_path: Path, shell: Shell) -> None:
    profile = tmp_path / "profile.ps1"
    profile.write_text(
        "# User settings\n"
        "# >>> pcd shell integration >>>\n"
        f"pcd shell init {shell.value} | Out-String | Invoke-Expression\n"
        "# <<< pcd shell integration <<<\n"
        "# More user settings\n",
        encoding="utf-8",
    )
    integration = ConfiguredShell.for_path(shell, profile, Platform.WINDOWS)

    assert integration.install() is ShellChange.CHANGED
    assert integration.install() is ShellChange.UNCHANGED
    updated = profile.read_text(encoding="utf-8")
    assert updated.startswith("# User settings\n")
    assert updated.endswith("# More user settings\n")
    assert "try {" in updated
    assert "Set-ExecutionPolicy does not unblock executables" in updated


@pytest.mark.skipif(os.name != "nt", reason="Real Windows PowerShell startup behavior")
@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
@pytest.mark.parametrize("outcome", ["success", "exit_error", "launch_error", "missing"])
def test_powershell_bootstrap_handles_launch_failures(
    tmp_path: Path, shell: Shell, outcome: str
) -> None:
    executable = shutil.which(f"{shell.value}.exe")
    if executable is None:
        pytest.skip(f"{shell.value} is not installed")
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    if outcome == "launch_error":
        # A native launch failure uses the same ApplicationFailedException as App Control.
        (binary_dir / "pcd.exe").write_bytes(b"not a Windows executable")
    elif outcome != "missing":
        code = (
            "function pcd { Write-Output 'wrapper-ready' }"
            if outcome == "success"
            else "Write-Output 'partial-output-must-not-run'"
        )
        (binary_dir / "pcd.cmd").write_text(
            f"@echo off\necho {code}\nexit /b {0 if outcome == 'success' else 7}\n",
            encoding="utf-8",
        )
    environment = os.environ.copy()
    path_key = next(key for key in environment if key.casefold() == "path")
    environment[path_key] = str(binary_dir)
    # Exercise actual launch errors at the normal Continue error preference, with a stale exit code.
    script = (
        "$ErrorActionPreference = 'Continue'\n"
        "$global:LASTEXITCODE = 10\n"
        + PowerShellDriver(shell, Platform.WINDOWS).startup_command()
        + '\nWrite-Output "exit=$global:LASTEXITCODE"\n'
        + "Write-Output 'profile-continued'\n"
        + 'Write-Output "preference=$ErrorActionPreference"\n'
        + "if (Get-Command pcd -CommandType Function -ErrorAction SilentlyContinue) { pcd }\n"
    )

    result = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "profile-continued" in result.stdout
    assert "preference=Continue" in result.stdout
    assert not result.stderr
    if outcome == "success":
        assert "wrapper-ready" in result.stdout
        assert "exit=0" in result.stdout
        assert "shell integration was not loaded" not in result.stdout
    else:
        assert "shell integration was not loaded" in result.stdout
        assert "Windows App Control" in result.stdout
        assert "exit=1" in result.stdout
        assert "wrapper-ready" not in result.stdout
        assert "partial-output-must-not-run" not in result.stdout


def test_posix_bootstrap_does_not_suggest_windows_policy_changes() -> None:
    command = PowerShellDriver(Shell.PWSH, Platform.LINUX).startup_command()

    assert "shell integration was not loaded" in command
    assert "Windows App Control" not in command
