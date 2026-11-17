from __future__ import annotations

import subprocess
from typing import Never, TYPE_CHECKING

import pytest

from pcd_cli.cli import cli
from pcd_cli.environment import Platform
from pcd_cli.integrations.shell import ConfiguredShell, Shell
from pcd_cli.integrations.shells import powershell_policy, windows_registry

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner


@pytest.fixture(autouse=True)
def isolated_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pcd_cli.environment.sys.platform", "win32")
    monkeypatch.setattr(windows_registry, "read", lambda _key, _name: None)


@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
@pytest.mark.parametrize("policy", ["Restricted", "AllSigned", "invalid output"])
@pytest.mark.parametrize("existing", [False, True])
def test_install_does_not_modify_profile_when_policy_check_fails(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    shell: Shell,
    policy: str,
    existing: bool,
) -> None:
    profile = ConfiguredShell.for_shell(shell).config_path
    original = b"# Existing user configuration\r\n"
    if existing:
        profile.parent.mkdir(parents=True)
        profile.write_bytes(original)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, policy, ""),
    )

    result = runner.invoke(cli, ["shell", "install", shell.value])

    assert result.exit_code == 1
    assert "profile left unchanged" in result.stderr
    assert "Get-ExecutionPolicy -List" in result.stderr
    assert "Reload the current shell" not in result.output
    assert profile.read_bytes() == original if existing else not profile.exists()


@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
def test_allowed_install_and_restricted_status(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, shell: Shell
) -> None:
    policy = "RemoteSigned"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, policy, ""),
    )
    installed = runner.invoke(cli, ["shell", "install", shell.value])
    repeated = runner.invoke(cli, ["shell", "install", shell.value])
    profile = ConfiguredShell.for_shell(shell).config_path
    original = profile.read_bytes()

    assert installed.exit_code == repeated.exit_code == 0
    assert "Reload the current shell" in installed.output
    assert "already installed" in repeated.output

    policy = "Restricted"
    status = runner.invoke(cli, ["shell", "status", shell.value])

    assert status.exit_code == 0
    assert "Configured: installed by pcd" in status.stdout
    assert "Warning: PowerShell execution policy is Restricted" in status.stderr
    assert "UnauthorizedAccess" in status.stderr
    assert profile.read_bytes() == original

    removed = runner.invoke(cli, ["shell", "uninstall", shell.value])
    assert removed.exit_code == 0
    assert "pcd shell init" not in profile.read_text(encoding="utf-8")


@pytest.mark.parametrize("command", ["init", "uninstall"])
def test_init_and_uninstall_do_not_probe_policy(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def unexpected_probe(_shell: Shell) -> Never:
        pytest.fail("Only install and status should query execution policy")

    monkeypatch.setattr(powershell_policy, "installation_issue", unexpected_probe)

    result = runner.invoke(cli, ["shell", command, "powershell"])

    assert result.exit_code == 0


def test_unix_shell_has_no_policy_preflight(tmp_path: Path) -> None:
    integration = ConfiguredShell.for_path(Shell.BASH, tmp_path / ".bashrc", Platform.WINDOWS)

    assert integration.installation_issue() is None
