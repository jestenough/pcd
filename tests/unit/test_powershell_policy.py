from __future__ import annotations

import subprocess
from typing import Never

import pytest

from pcd_cli.environment import Platform
from pcd_cli.integrations.shells import powershell_policy
from pcd_cli.integrations.shells.common import Shell
from pcd_cli.integrations.shells.powershell import PowerShellDriver


@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("RemoteSigned", None),
        ("Unrestricted", None),
        ("Bypass", None),
        ("Restricted", "PSSecurityException / UnauthorizedAccess"),
        ("AllSigned", "profiles must be signed"),
    ],
)
def test_policy_check(
    monkeypatch: pytest.MonkeyPatch, shell: Shell, policy: str, expected: str | None
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == [
            f"{shell.value}.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            '$env:PSModulePath = "$PSHOME/Modules"; Get-ExecutionPolicy',
        ]
        assert kwargs["timeout"] == 10
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(args, 0, f"{policy}\r\n", "")

    monkeypatch.setattr(powershell_policy.subprocess, "run", run)

    issue = PowerShellDriver(shell, Platform.WINDOWS).installation_issue()

    if expected is None:
        assert issue is None
    else:
        assert issue is not None
        assert expected in issue
        assert f"Open {shell.value}.exe -NoProfile" in issue
        assert "Get-ExecutionPolicy -List" in issue
        assert "MachinePolicy/UserPolicy" in issue
        if policy == "Restricted":
            assert "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned" in issue
            assert "pcd never changes execution policy automatically" in issue
        else:
            assert "invalidate an existing signature" in issue
            assert "Set-ExecutionPolicy" not in issue


@pytest.mark.parametrize("failure", ["missing", "timeout", "nonzero", "invalid", "empty"])
def test_policy_probe_failure_is_actionable(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        match failure:
            case "missing":
                raise FileNotFoundError("missing executable")
            case "timeout":
                raise subprocess.TimeoutExpired(args, 10)
            case "nonzero":
                return subprocess.CompletedProcess(args, 1, "RemoteSigned", "failed")
            case "invalid":
                return subprocess.CompletedProcess(args, 0, "unexpected output", "")
            case _:
                return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(powershell_policy.subprocess, "run", run)

    issue = powershell_policy.installation_issue(Shell.PWSH)

    assert issue is not None
    assert "Cannot determine" in issue
    assert "pwsh.exe" in issue
    assert "Get-ExecutionPolicy -List" in issue


@pytest.mark.parametrize("platform", [Platform.LINUX, Platform.MACOS])
@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
def test_policy_is_not_checked_on_posix(
    monkeypatch: pytest.MonkeyPatch, platform: Platform, shell: Shell
) -> None:
    def unexpected_probe(_shell: Shell) -> Never:
        pytest.fail("Execution policy is only enforced on Windows")

    monkeypatch.setattr(powershell_policy, "installation_issue", unexpected_probe)

    assert PowerShellDriver(shell, platform).installation_issue() is None
