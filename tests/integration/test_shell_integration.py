from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
import pytest

from pcd_cli.cli import cli
from pcd_cli.cli.shell import shell_init
from pcd_cli.shell_integration import (
    inactive_shell_message,
    render_shell_integration,
    Shell,
    ShellIntegration,
    ShellIntegrationError,
    ShellIntegrationState,
)

if TYPE_CHECKING:
    from click.testing import CliRunner


def test_bash_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "print", "bash"])

    assert result.exit_code == 0
    assert "pcd()" in result.output
    assert "bash_source" in result.output
    assert "builtin cd" in result.output
    assert "-eq 10" in result.output
    assert "__PCD_CD__" not in result.output


def test_zsh_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "print", "zsh"])

    assert result.exit_code == 0
    assert "zsh_source" in result.output


def test_fish_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "print", "fish"])

    assert result.exit_code == 0
    assert "function pcd" in result.output
    assert "fish_source" in result.output


def test_legacy_shell_init_remains_available(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell-init", "zsh"])

    assert result.exit_code == 0
    assert "zsh_source" in result.output


def test_legacy_shell_init_can_render_as_standalone_command(runner: CliRunner) -> None:
    result = runner.invoke(shell_init, ["bash"])

    assert result.exit_code == 0
    assert "bash_source" in result.output


@pytest.mark.parametrize("shell", list(Shell))
def test_shell_wrapper_uses_registered_root_commands(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    shell: Shell,
) -> None:
    commands = {**cli.commands, "interactive": click.Command("interactive")}
    monkeypatch.setattr(cli, "commands", commands)

    result = runner.invoke(cli, ["shell", "print", shell.value])

    assert result.exit_code == 0
    assert "interactive" in result.output


def test_render_rejects_unsupported_shell() -> None:
    with pytest.raises(ValueError, match="Unsupported shell"):
        render_shell_integration("powershell")  # type: ignore[arg-type]


def test_shell_install_detects_zsh_and_is_idempotent(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    config = Path.home() / ".zshrc"
    config.write_text("export EDITOR=vim\n", encoding="utf-8")

    installed = runner.invoke(cli, ["shell", "install"])
    repeated = runner.invoke(cli, ["shell", "install"])
    content = config.read_text(encoding="utf-8")

    assert installed.exit_code == 0
    assert "Installed zsh integration" in installed.output
    assert "exec zsh" in installed.output
    assert repeated.exit_code == 0
    assert "already installed" in repeated.output
    assert content.startswith("export EDITOR=vim\n")
    assert content.count("# >>> pcd shell integration >>>") == 1
    assert 'eval "$(command pcd shell print zsh)"' in content


def test_shell_install_leaves_manual_configuration_untouched(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    config = Path.home() / ".zshrc"
    manual = 'eval "$(pcd shell-init zsh)"\n'
    config.write_text(manual, encoding="utf-8")

    result = runner.invoke(cli, ["shell", "install"])

    assert result.exit_code == 0
    assert "configured manually" in result.output
    assert config.read_text(encoding="utf-8") == manual


def test_shell_uninstall_removes_only_managed_integration(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    config = Path.home() / ".bashrc"
    original = "export EDITOR=nvim\n"
    config.write_text(original, encoding="utf-8")
    assert runner.invoke(cli, ["shell", "install"]).exit_code == 0

    result = runner.invoke(cli, ["shell", "uninstall"])

    assert result.exit_code == 0
    assert "Removed shell integration" in result.output
    assert config.read_text(encoding="utf-8") == original


def test_shell_uninstall_does_not_remove_manual_configuration(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    config = Path.home() / ".bashrc"
    manual = 'eval "$(pcd shell-init bash)"\n'
    config.write_text(manual, encoding="utf-8")

    result = runner.invoke(cli, ["shell", "uninstall"])

    assert result.exit_code == 0
    assert "managed manually; left unchanged" in result.output
    assert config.read_text(encoding="utf-8") == manual


def test_shell_uninstall_reports_absent_integration(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "uninstall", "bash"])

    assert result.exit_code == 0
    assert "not installed" in result.output


def test_shell_status_reports_configuration_and_activation(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/fish")
    monkeypatch.setenv("PCD_SHELL", "1")
    assert runner.invoke(cli, ["shell", "install"]).exit_code == 0

    result = runner.invoke(cli, ["shell", "status"])

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        f"{'Shell:':<24} fish",
        f"{'Config:':<24} {ShellIntegration.for_shell(Shell.FISH).config_path}",
        f"{'Configured:':<24} installed by pcd",
        "Active in current shell: yes",
    ]


def test_shell_install_can_be_explicit_when_shell_is_unknown(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHELL", raising=False)

    automatic = runner.invoke(cli, ["shell", "install"])
    explicit = runner.invoke(cli, ["shell", "install", "zsh"])

    assert automatic.exit_code == 2
    assert "Cannot detect a supported shell" in automatic.output
    assert explicit.exit_code == 0


def test_fish_config_uses_xdg_config_home(monkeypatch: pytest.MonkeyPatch) -> None:
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    integration = ShellIntegration.for_shell(Shell.FISH)

    assert integration.config_path == config_home / "fish" / "config.fish"


def test_zsh_config_respects_zdotdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dotfiles = tmp_path / "zsh"
    monkeypatch.setenv("ZDOTDIR", str(dotfiles))

    integration = ShellIntegration.for_shell(Shell.ZSH)

    assert integration.config_path == dotfiles / ".zshrc"


def test_install_preserves_symlinked_shell_config(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "zshrc"
    target.write_text("export PAGER=less\n", encoding="utf-8")
    config = Path.home() / ".zshrc"
    config.symlink_to(target)

    result = runner.invoke(cli, ["shell", "install"])

    assert result.exit_code == 0
    assert config.is_symlink()
    assert "pcd shell print zsh" in target.read_text(encoding="utf-8")


def test_invalid_managed_block_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    config = Path.home() / ".zshrc"
    config.write_text("# >>> pcd shell integration >>>\n", encoding="utf-8")

    with pytest.raises(ShellIntegrationError):
        ShellIntegration.detect().state()


def test_reversed_managed_block_is_rejected() -> None:
    integration = ShellIntegration.for_shell(Shell.BASH)
    integration.config_path.write_text(
        "# <<< pcd shell integration <<<\n# >>> pcd shell integration >>>\n",
        encoding="utf-8",
    )

    with pytest.raises(ShellIntegrationError, match="invalid pcd-managed"):
        integration.state()


def test_shell_config_rejects_invalid_utf8() -> None:
    integration = ShellIntegration.for_shell(Shell.BASH)
    integration.config_path.write_bytes(b"\xff")

    with pytest.raises(ShellIntegrationError, match="not valid UTF-8"):
        integration.state()


def test_uninstall_handles_managed_block_at_end_of_file() -> None:
    integration = ShellIntegration.for_shell(Shell.BASH)
    integration.config_path.write_text(
        "keep\n# >>> pcd shell integration >>>\npcd\n# <<< pcd shell integration <<<",
        encoding="utf-8",
    )

    assert integration.uninstall() is True
    assert integration.config_path.read_text(encoding="utf-8") == "keep\n"


def test_inactive_shell_message_handles_detection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/unsupported")

    message = inactive_shell_message()

    assert "Shell integration is not active" in message
    assert "pcd shell install" in message


def test_shell_integration_state_detects_manual_and_absent() -> None:
    integration = ShellIntegration.for_shell(Shell.BASH)
    assert integration.state() is ShellIntegrationState.ABSENT

    integration.config_path.write_text('eval "$(pcd shell-init bash)"\n', encoding="utf-8")
    assert integration.state() is ShellIntegrationState.MANUAL


def test_bash_wrapper_changes_directory_without_magic_stdout_protocol(tmp_path: Path) -> None:
    binary_dir = tmp_path / "bin"
    target = tmp_path / "target"
    binary_dir.mkdir()
    target.mkdir()

    executable = binary_dir / "pcd"
    executable.write_text(
        """#!/bin/sh
if [ -n \"${_PCD_COMPLETE:-}\" ]; then
    exit 0
fi
if [ \"${1:-}\" = jump ]; then
    printf '%s\\n' \"$PCD_TEST_TARGET\"
    exit 10
fi
if [ \"${1:-}\" = --project=jump ]; then
    printf '%s\\n' \"$PCD_TEST_TARGET\"
    exit 10
fi
if [ \"${1:-}\" = config ]; then
    if [ \"${PCD_SHELL:-}\" = 1 ]; then
        exit 99
    fi
    printf '%s\\n' 'config-output'
    exit 0
fi
printf '%s\\n' '__PCD_CD__:ordinary-output'
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    integration = tmp_path / "pcd.bash"
    integration.write_text(
        render_shell_integration(Shell.BASH, ("config",)),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{binary_dir}{os.pathsep}{environment['PATH']}"
    environment["PCD_TEST_TARGET"] = str(target)

    command = (
        'set -e; source "$1"; pcd jump; printf "cwd=%s\\n" "$PWD"; '
        'pcd config edit; pcd marker; pcd --project=jump; printf "cwd-option=%s\\n" "$PWD"; '
        'printf "alive\\n"'
    )
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(integration)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0
    assert result.stdout == (
        f"cwd={target}\nconfig-output\n__PCD_CD__:ordinary-output\ncwd-option={target}\nalive\n"
    )
    assert result.stderr == ""
