from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Never, TYPE_CHECKING

import pytest

import pcd_cli.integrations.shell as shell_integration
import pcd_cli.integrations.shells.cmd as cmd_backend
import pcd_cli.integrations.shells.cmd_autorun as cmd_autorun
import pcd_cli.integrations.shells.windows_registry as windows_registry
import pcd_cli.integrations.shells.windows_shell as windows_shell
from pcd_cli.cli import cli
from pcd_cli.cli.shell import init_shell
from pcd_cli.integrations.shell import (
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
    result = runner.invoke(cli, ["shell", "init", "bash"])

    assert result.exit_code == 0
    assert "pcd()" in result.output
    assert "bash_source" in result.output
    assert "local -x PCD_WRAPPER=bash" in result.output
    assert "builtin cd" in result.output
    assert "-eq 10" in result.output
    assert "__PCD_CD__" not in result.output


def test_zsh_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "init", "zsh"])

    assert result.exit_code == 0
    assert "zsh_source" in result.output
    assert "local -x PCD_WRAPPER=zsh" in result.output


def test_fish_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "init", "fish"])

    assert result.exit_code == 0
    assert "function pcd" in result.output
    assert "fish_source" in result.output
    assert "command env PCD_WRAPPER=fish PCD_SHELL=1 pcd $argv" in result.output
    assert "command env _PCD_COMPLETE=fish_source pcd | source" in result.output
    assert "printf '%s\\n' $output" in result.output
    assert "env PCD_WRAPPER=fish command pcd" not in result.output


def test_powershell_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "init", "powershell"])

    assert result.exit_code == 0
    assert "function pcd" in result.output
    assert "powershell_source" in result.output
    assert "$env:PCD_WRAPPER = 'powershell'" in result.output
    assert "$env:PCD_SHELL = '1'" in result.output
    assert "Set-Location -LiteralPath" in result.output
    assert "-eq 10" in result.output


def test_pwsh_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "init", "pwsh"])

    assert result.exit_code == 0
    assert "function pcd" in result.output
    assert "$env:PCD_WRAPPER = 'pwsh'" in result.output
    assert "powershell_source" in result.output


def test_cmd_native(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "init", "cmd"])

    assert result.exit_code == 0
    assert "@echo off" in result.output
    assert 'set "PCD_WRAPPER=cmd"' in result.output
    assert "cd /d" in result.output
    assert "pcd.exe" in result.output


def test_shell_init_can_render_as_standalone_command(runner: CliRunner) -> None:
    result = runner.invoke(init_shell, ["bash"])

    assert result.exit_code == 0
    assert "bash_source" in result.output


def test_shell_mode_does_not_change_regular_cli_commands(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PCD_SHELL", "1")

    result = runner.invoke(cli, ["config", "path"])

    assert result.exit_code == 0
    assert result.output.strip()


def test_render_rejects_unsupported_shell() -> None:
    with pytest.raises(ValueError, match="Unsupported shell"):
        render_shell_integration("nushell")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "shell",
    [shell for shell in Shell if shell is not Shell.CMD],
    ids=lambda shell: shell.value,
)
def test_shell_install_supports_each_shell(runner: CliRunner, shell: Shell) -> None:
    integration = ShellIntegration.for_shell(shell)

    result = runner.invoke(cli, ["shell", "install", shell.value])

    assert result.exit_code == 0
    assert f"Installed {shell.value} integration" in result.output
    assert f"Reload the current shell with: {integration.reload_command()}" in result.output
    assert f"pcd shell init {shell.value}" in integration.config_path.read_text(encoding="utf-8")


def test_shell_install_supports_cmd(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    added: list[Path] = []
    removed: list[Path] = []
    registered = False
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)

    def add_to_autorun(path: Path) -> bool:
        nonlocal registered
        if registered:
            return False
        added.append(path)
        registered = True
        return True

    def remove_from_autorun(path: Path) -> None:
        nonlocal registered
        removed.append(path)
        registered = False

    monkeypatch.setattr("pcd_cli.integrations.shells.cmd_autorun.add", add_to_autorun)
    monkeypatch.setattr(
        "pcd_cli.integrations.shells.cmd_autorun.configured",
        lambda _path: registered,
    )
    monkeypatch.setattr(
        "pcd_cli.integrations.shells.cmd_autorun.remove",
        remove_from_autorun,
    )

    result = runner.invoke(cli, ["shell", "install", "cmd"])
    integration = ShellIntegration.for_shell(Shell.CMD)

    assert result.exit_code == 0
    assert integration.state() is ShellIntegrationState.MANAGED
    wrapper = integration.config_path.read_text(encoding="utf-8")
    assert wrapper.startswith("@echo off")
    assert added == [integration.config_path]

    assert integration.install() is False

    uninstalled = runner.invoke(cli, ["shell", "uninstall", "cmd"])

    assert uninstalled.exit_code == 0
    assert integration.state() is ShellIntegrationState.ABSENT
    assert removed == [integration.config_path]


def test_cmd_autorun_preserves_existing_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    current = 'call "existing.cmd"'
    written: list[str] = []
    monkeypatch.setattr(cmd_autorun, "_read", lambda: (current, 1))
    monkeypatch.setattr(
        cmd_autorun,
        "_write",
        lambda value, _value_type: written.append(value),
    )

    assert cmd_autorun.add(wrapper) is True
    expected = f"{current} & {cmd_autorun.doskey_command(wrapper)}"
    assert written == [expected]

    written.clear()
    with_trailing_command = f'{expected} & call "later.cmd"'
    monkeypatch.setattr(
        cmd_autorun,
        "_read",
        lambda: (with_trailing_command, 1),
    )
    cmd_autorun.remove(wrapper)
    assert written == [f'{current} & call "later.cmd"']


def test_cmd_autorun_detects_existing_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    command = cmd_autorun.doskey_command(wrapper)
    monkeypatch.setattr(cmd_autorun, "_read", lambda: (command, 1))

    assert cmd_autorun.configured(wrapper) is True
    assert cmd_autorun.add(wrapper) is False


def test_cmd_autorun_ignores_missing_or_embedded_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    command = cmd_autorun.doskey_command(wrapper)
    written: list[str] = []
    monkeypatch.setattr(cmd_autorun, "_write", lambda value, _kind: written.append(value))

    for value in ("other", f"prefix{command}", f"{command}suffix", f"other & {command}suffix"):
        monkeypatch.setattr(cmd_autorun, "_read", lambda value=value: (value, 1))
        assert cmd_autorun.configured(wrapper) is False
        cmd_autorun.remove(wrapper)

    assert written == []


def test_cmd_autorun_registry_io(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[str, str, str, int]] = []
    deletes: list[tuple[str, str]] = []
    monkeypatch.setattr(windows_registry, "read", lambda _key, _name: None)
    monkeypatch.setattr(windows_registry, "string_value_type", lambda: 1)
    monkeypatch.setattr(
        windows_registry,
        "write",
        lambda key, name, value, kind: writes.append((key, name, value, kind)),
    )
    monkeypatch.setattr(
        windows_registry,
        "delete",
        lambda key, name: deletes.append((key, name)),
    )

    assert cmd_autorun._read() == ("", 1)
    cmd_autorun._write("command", 2)
    cmd_autorun._write("", 2)

    key = r"Software\Microsoft\Command Processor"
    assert writes == [(key, "AutoRun", "command", 2)]
    assert deletes == [(key, "AutoRun")]


def test_cmd_update_restores_wrapper_when_autorun_update_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    previous_content = cmd_backend.render(Shell.CMD).replace(
        "setlocal\n",
        "rem Previous generated version\nsetlocal\n",
    )
    wrapper.write_text(previous_content, encoding="utf-8")
    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)
    monkeypatch.setattr(cmd_autorun, "configured", lambda _wrapper: False)

    def fail_to_add(_wrapper: Path) -> bool:
        raise OSError("registry unavailable")

    monkeypatch.setattr(cmd_autorun, "add", fail_to_add)

    with pytest.raises(OSError, match="registry unavailable"):
        cmd_backend.install(wrapper)

    assert wrapper.read_text(encoding="utf-8") == previous_content


def test_cmd_state_rejects_missing_managed_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)
    monkeypatch.setattr(cmd_autorun, "configured", lambda _wrapper: True)
    integration = ShellIntegration(Shell.CMD, wrapper)

    with pytest.raises(ShellIntegrationError, match="registered wrapper is missing"):
        integration.state()


def test_cmd_uninstall_removes_stale_autorun_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    removed: list[Path] = []
    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)
    monkeypatch.setattr(cmd_autorun, "configured", lambda _wrapper: True)
    monkeypatch.setattr(cmd_autorun, "remove", removed.append)

    assert cmd_backend.uninstall(wrapper) is True
    assert removed == [wrapper]


def test_cmd_uninstall_restores_registration_when_wrapper_cannot_be_deleted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    wrapper.write_text(cmd_backend.render(Shell.CMD), encoding="utf-8")
    events: list[str] = []
    original_unlink = Path.unlink

    def unlink(path: Path, missing_ok: bool = False) -> None:
        if path == wrapper:
            raise PermissionError("wrapper is busy")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)
    monkeypatch.setattr(cmd_autorun, "remove", lambda _path: events.append("remove"))
    monkeypatch.setattr(cmd_autorun, "add", lambda _path: events.append("restore"))
    monkeypatch.setattr(Path, "unlink", unlink)

    with pytest.raises(PermissionError, match="wrapper is busy"):
        cmd_backend.uninstall(wrapper)

    assert wrapper.exists()
    assert events == ["remove", "restore"]


def test_cmd_manual_wrapper_is_never_modified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    original = "@echo off\necho user wrapper\n"
    wrapper.write_text(original, encoding="utf-8")
    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)

    assert cmd_backend.state(wrapper) is ShellIntegrationState.MANUAL
    assert cmd_backend.install(wrapper) is False
    assert cmd_backend.uninstall(wrapper) is False
    assert wrapper.read_text(encoding="utf-8") == original


def test_cmd_install_removes_new_wrapper_when_registration_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)

    def fail(_path: Path) -> Never:
        raise OSError("registry unavailable")

    monkeypatch.setattr(cmd_autorun, "add", fail)

    with pytest.raises(OSError, match="registry unavailable"):
        cmd_backend.install(wrapper)

    assert not wrapper.exists()


def test_cmd_install_reports_rollback_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    wrapper.write_text(cmd_backend.render(Shell.CMD) + "rem old\n", encoding="utf-8")
    write = cmd_backend._write
    writes = 0

    def write_wrapper(path: Path, content: str) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise PermissionError("restore denied")
        write(path, content)

    def register(_path: Path) -> Never:
        raise OSError("registry unavailable")

    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)
    monkeypatch.setattr(cmd_backend, "_write", write_wrapper)
    monkeypatch.setattr(cmd_autorun, "add", register)

    with pytest.raises(ShellIntegrationError, match="could not restore wrapper") as caught:
        cmd_backend.install(wrapper)

    assert "registry unavailable" in str(caught.value)
    assert "restore denied" in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)


def test_cmd_state_reports_absent_or_unregistered_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "pcd.cmd"
    monkeypatch.setattr(cmd_backend, "_require_windows", lambda _action: None)
    monkeypatch.setattr(cmd_autorun, "configured", lambda _path: False)

    assert cmd_backend.state(wrapper) is ShellIntegrationState.ABSENT
    assert cmd_backend.uninstall(wrapper) is False
    wrapper.write_text(cmd_backend.render(Shell.CMD), encoding="utf-8")

    with pytest.raises(ShellIntegrationError, match="AutoRun registration is missing"):
        cmd_backend.state(wrapper)


def test_shell_install_detects_zsh_and_is_idempotent(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    config = Path.home() / ".zshrc"
    config.write_text("export EDITOR=vim\n", encoding="utf-8")

    installed = runner.invoke(cli, ["shell", "install"])
    repeated = runner.invoke(cli, ["shell", "install"])
    content = config.read_text(encoding="utf-8")

    assert installed.exit_code == 0
    assert "Installed zsh integration" in installed.output
    assert ShellIntegration.for_shell(Shell.ZSH).reload_command() in installed.output
    assert repeated.exit_code == 0
    assert "already installed" in repeated.output
    assert content.startswith("export EDITOR=vim\n")
    assert content.count("# >>> pcd shell integration >>>") == 1
    assert 'eval "$(command pcd shell init zsh)"' in content


def test_shell_install_updates_only_managed_block(runner: CliRunner) -> None:
    integration = ShellIntegration.for_shell(Shell.BASH)
    original = (
        "export EDITOR=vim\n"
        "# >>> pcd shell integration >>>\n"
        "old initialization\n"
        "# <<< pcd shell integration <<<\n"
        "export PAGER=less\n"
    )
    integration.config_path.write_text(original, encoding="utf-8")

    result = runner.invoke(cli, ["shell", "install", "bash"])

    assert result.exit_code == 0
    content = integration.config_path.read_text(encoding="utf-8")
    assert content.startswith("export EDITOR=vim\n")
    assert content.endswith("export PAGER=less\n")
    assert "old initialization" not in content
    assert content.count("# >>> pcd shell integration >>>") == 1
    assert integration.install() is False


def test_pwsh_profile_uses_unix_config_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shells.powershell.sys.platform", "linux")
    expected = Path(os.environ["XDG_CONFIG_HOME"]) / "powershell/Microsoft.PowerShell_profile.ps1"

    assert ShellIntegration.for_shell(Shell.PWSH).config_path == expected


def test_shell_backends_are_not_imported_during_cli_startup() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import pcd_cli.cli; "
            "assert not any(name in sys.modules for name in ("
            "'pcd_cli.integrations.shells.unix', "
            "'pcd_cli.integrations.shells.powershell', "
            "'pcd_cli.integrations.shells.cmd', 'shellingham'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_shell_commands_do_not_create_project_catalog(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_catalog() -> Never:
        pytest.fail("Shell management must not resolve project data paths")

    monkeypatch.setattr("pcd_cli.cli.app.ProjectCatalog.create", unexpected_catalog)

    result = runner.invoke(cli, ["shell", "init", "bash"])

    assert result.exit_code == 0


def test_shell_install_prompts_for_shell_on_windows(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.cli.shell.sys.platform", "win32")
    monkeypatch.setattr(
        windows_shell,
        "detect_shell",
        lambda: _raise_shell_detection_error(),
    )

    result = runner.invoke(cli, ["shell", "install"], input="4\n")

    assert result.exit_code == 0
    assert "Select shell:" in result.output
    assert "1. PowerShell 7+" in result.output
    assert "2. Windows PowerShell 5.1" in result.output
    assert "3. Command Prompt (cmd)" in result.output
    assert "4. Git Bash" in result.output
    assert "Installed bash integration" in result.output


def test_shell_install_detects_windows_shell_without_prompt(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.cli.shell.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr("pcd_cli.integrations.shell.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(windows_shell, "detect_shell", lambda: "pwsh")

    result = runner.invoke(cli, ["shell", "install"])

    assert result.exit_code == 0
    assert "Select shell:" not in result.output
    assert "Installed pwsh integration" in result.output


def test_shell_detection_does_not_use_windows_detector_on_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/bash")

    def unexpected_windows_detection() -> str:
        pytest.fail("Windows shell detection was loaded on Linux")

    monkeypatch.setattr(windows_shell, "detect_shell", unexpected_windows_detection)

    assert shell_integration.detect_shell() is Shell.BASH


def test_windows_shell_detector_is_loaded_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("shellingham")
    monkeypatch.setattr(module, "detect_shell", lambda: ("pwsh.exe", "pwsh.exe"), raising=False)
    monkeypatch.setitem(sys.modules, "shellingham", module)

    assert windows_shell.detect_shell() == "pwsh"


def test_windows_shell_detector_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("shellingham")

    def fail() -> Never:
        raise OSError("process tree unavailable")

    monkeypatch.setattr(module, "detect_shell", fail, raising=False)
    monkeypatch.setitem(sys.modules, "shellingham", module)

    with pytest.raises(ShellIntegrationError, match="Cannot detect the surrounding Windows shell"):
        windows_shell.detect_shell()


def test_windows_detection_does_not_hide_broken_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_import(_name: str) -> Never:
        raise ModuleNotFoundError("missing internal dependency", name="internal_dependency")

    monkeypatch.setattr(windows_shell, "import_module", broken_import)

    with pytest.raises(ModuleNotFoundError, match="missing internal dependency"):
        windows_shell.detect_shell()


def test_windows_detection_reports_missing_shellingham(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_import(_name: str) -> Never:
        raise ModuleNotFoundError("missing shellingham", name="shellingham")

    monkeypatch.setattr(windows_shell, "import_module", missing_import)

    with pytest.raises(ShellIntegrationError, match="detection is unavailable"):
        windows_shell.detect_shell()


def _raise_shell_detection_error() -> Never:
    raise ShellIntegrationError("Cannot detect the surrounding Windows shell")


def test_shell_install_leaves_manual_configuration_untouched(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    config = Path.home() / ".zshrc"
    manual = 'eval "$(pcd shell init zsh)"\n'
    config.write_text(manual, encoding="utf-8")

    result = runner.invoke(cli, ["shell", "install"])

    assert result.exit_code == 0
    assert "configured manually" in result.output
    assert config.read_text(encoding="utf-8") == manual


def test_shell_uninstall_removes_only_managed_integration(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
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
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/bash")
    config = Path.home() / ".bashrc"
    manual = 'eval "$(pcd shell init bash)"\n'
    config.write_text(manual, encoding="utf-8")

    result = runner.invoke(cli, ["shell", "uninstall"])

    assert result.exit_code == 0
    assert "managed manually; left unchanged" in result.output
    assert config.read_text(encoding="utf-8") == manual


def test_shell_uninstall_reports_absent_integration(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell", "uninstall", "bash"])

    assert result.exit_code == 0
    assert "not installed" in result.output


@pytest.mark.parametrize(
    "wrapper",
    [None, "bash", "zsh", "fish", "powershell", "pwsh", "cmd", "unknown"],
)
@pytest.mark.parametrize("navigation", ["0", "1"])
def test_shell_status_reports_configuration_and_wrapper(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    wrapper: str | None,
    navigation: str,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/fish")
    monkeypatch.setenv("PCD_SHELL", navigation)
    if wrapper is None:
        monkeypatch.delenv("PCD_WRAPPER", raising=False)
    else:
        monkeypatch.setenv("PCD_WRAPPER", wrapper)
    assert runner.invoke(cli, ["shell", "install", "fish"]).exit_code == 0

    result = runner.invoke(cli, ["shell", "status", "fish"])

    assert result.exit_code == 0
    assert "Shell: fish" in result.output
    assert "Configured: installed by pcd" in result.output
    expected = f"yes ({wrapper})" if wrapper in {shell.value for shell in Shell} else "no"
    assert f"Invoked through wrapper: {expected}" in result.output.splitlines()


def test_shell_install_can_be_explicit_when_shell_is_unknown(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.cli.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/unsupported")

    automatic = runner.invoke(cli, ["shell", "install"])
    explicit = runner.invoke(cli, ["shell", "install", "zsh"])

    assert automatic.exit_code == 2
    assert "Cannot detect a supported shell" in automatic.output
    assert explicit.exit_code == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    ("shell", "executable_name"),
    [(Shell.POWERSHELL, "powershell.exe"), (Shell.PWSH, "pwsh.exe")],
)
def test_powershell_wrapper_changes_directory(
    tmp_path: Path,
    shell: Shell,
    executable_name: str,
) -> None:
    if shutil.which(executable_name) is None:
        pytest.skip(f"{executable_name} is not installed")

    binary_dir = tmp_path / "bin"
    target = tmp_path / "target"
    binary_dir.mkdir()
    target.mkdir()

    executable = binary_dir / "pcd.cmd"
    executable.write_text(
        """@echo off
if defined _PCD_COMPLETE echo # completion
if defined _PCD_COMPLETE exit /b 0
if "%~1"=="jump" goto jump
if "%~1"=="--project" if "%~2"=="jump" goto jump
if "%~1"=="config" goto config
echo __PCD_CD__:ordinary-output
exit /b 0

:jump
echo %PCD_TEST_TARGET%
exit /b 10

:config
echo config-output
exit /b 0

""",
        encoding="utf-8",
    )

    integration = tmp_path / "pcd.ps1"
    integration.write_text(
        render_shell_integration(shell),
        encoding="utf-8",
    )
    script = tmp_path / "test.ps1"
    quoted_integration = str(integration).replace("'", "''")
    script.write_text(
        f""". '{quoted_integration}'
pcd jump
Write-Output "cwd=$((Get-Location).Path)"
pcd config edit
pcd marker
pcd --project jump
Write-Output "cwd-option=$((Get-Location).Path)"
Write-Output "exit=$global:LASTEXITCODE"
Write-Output "wrapper=$env:PCD_WRAPPER"
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    path_key = next(key for key in environment if key.casefold() == "path")
    environment[path_key] = f"{binary_dir}{os.pathsep}{environment[path_key]}"
    environment["PCD_TEST_TARGET"] = str(target)
    environment.pop("PCD_SHELL", None)
    environment.pop("PCD_WRAPPER", None)
    environment.pop("_PCD_COMPLETE", None)

    result = subprocess.run(
        [
            executable_name,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"cwd={target}",
        "config-output",
        "__PCD_CD__:ordinary-output",
        f"cwd-option={target}",
        "exit=0",
        "wrapper=",
    ]
    assert result.stderr == ""


def test_reload_command_quotes_config_path(tmp_path: Path) -> None:
    config = tmp_path / "shell config"
    integration = ShellIntegration(Shell.BASH, config)

    assert integration.reload_command() == f"source '{config}'"


def test_powershell_profile_and_reload_command() -> None:
    home = Path.home()
    integration = ShellIntegration.for_shell(Shell.POWERSHELL)

    assert integration.config_path == (
        home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
    )
    assert integration.reload_command() == f". '{integration.config_path}'"


def test_pwsh_profile_and_reload_command() -> None:
    home = Path.home()
    integration = ShellIntegration.for_shell(Shell.PWSH)

    if os.name == "nt":
        expected = home / "Documents/PowerShell/Microsoft.PowerShell_profile.ps1"
    else:
        expected = (
            Path(os.environ["XDG_CONFIG_HOME"]) / "powershell/Microsoft.PowerShell_profile.ps1"
        )
    assert integration.config_path == expected
    assert integration.reload_command() == f". '{integration.config_path}'"


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD behavior test")
def test_cmd_wrapper_changes_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    executable = tmp_path / "pcd-stub.cmd"
    executable.write_text(
        """@echo off
if "%~1"=="jump" goto jump
if "%~1"=="config" goto config
if "%~1"=="fail" goto fail
echo ordinary-output
exit /b 0

:jump
echo %PCD_TEST_TARGET%
exit /b 10

:config
echo config-output
exit /b 0
:fail
echo failure-output
exit /b 3
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "pcd.cmd"
    wrapper.write_text(render_shell_integration(Shell.CMD), encoding="utf-8")
    script = tmp_path / "test.cmd"
    script.write_text(
        f"""@echo off
call "{wrapper}" jump
echo cwd=%CD%
call "{wrapper}" config edit
call "{wrapper}" marker
call "{wrapper}" fail
echo code=%ERRORLEVEL%
echo wrapper=%PCD_WRAPPER%
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["_PCD_EXECUTABLE"] = str(executable)
    environment["PCD_TEST_TARGET"] = str(target)
    environment.pop("PCD_SHELL", None)
    environment.pop("PCD_WRAPPER", None)

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"cwd={target}",
        "config-output",
        "ordinary-output",
        "failure-output",
        "code=3",
        "wrapper=",
    ]
    assert result.stderr == ""


def test_powershell_reload_command_escapes_single_quotes(tmp_path: Path) -> None:
    config = tmp_path / "user's profile.ps1"
    integration = ShellIntegration(Shell.POWERSHELL, config)
    escaped = str(config).replace("'", "''")

    assert integration.reload_command() == f". '{escaped}'"


def test_detects_powershell_executable(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "powershell.exe")

    result = runner.invoke(cli, ["shell", "status"])

    assert result.exit_code == 0
    assert "Shell: powershell" in result.output


def test_detects_pwsh_executable(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "pwsh.exe")

    result = runner.invoke(cli, ["shell", "status"])

    assert result.exit_code == 0
    assert "Shell: pwsh" in result.output


@pytest.mark.skipif(os.name != "nt", reason="Git Bash detection test")
def test_detects_git_bash_executable(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(windows_shell, "detect_shell", lambda: "bash")

    result = runner.invoke(cli, ["shell", "status"])

    assert result.exit_code == 0
    assert "Shell: bash" in result.output


@pytest.mark.skipif(os.name != "nt", reason="Windows shell detection test")
def test_windows_status_reports_detection_failure(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PCD_WRAPPER", raising=False)
    monkeypatch.setattr(
        windows_shell,
        "detect_shell",
        lambda: _raise_shell_detection_error(),
    )

    result = runner.invoke(cli, ["shell", "status"])

    assert result.exit_code == 2
    assert "Cannot detect the surrounding Windows shell" in result.output


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
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
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
    assert "pcd shell init zsh" in target.read_text(encoding="utf-8")


def test_invalid_managed_block_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    config = Path.home() / ".zshrc"
    config.write_text("# >>> pcd shell integration >>>\n", encoding="utf-8")

    with pytest.raises(ShellIntegrationError):
        ShellIntegration.detect().state()


def test_embedded_managed_markers_are_rejected_without_modifying_config() -> None:
    integration = ShellIntegration.for_shell(Shell.BASH)
    original = 'echo "# >>> pcd shell integration >>>"\necho "# <<< pcd shell integration <<<"\n'
    integration.config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ShellIntegrationError, match="invalid pcd-managed"):
        integration.install()
    with pytest.raises(ShellIntegrationError, match="invalid pcd-managed"):
        integration.uninstall()

    assert integration.config_path.read_text(encoding="utf-8") == original


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
    monkeypatch.setattr("pcd_cli.integrations.shell.sys.platform", "win32")

    def unexpected_windows_detection() -> str:
        pytest.fail("Windows shell detection was loaded during regular navigation")

    monkeypatch.setattr(windows_shell, "detect_shell", unexpected_windows_detection)

    message = inactive_shell_message()

    assert "Shell integration is not active" in message
    assert "pcd shell install" in message


def test_shell_integration_state_detects_manual_and_absent() -> None:
    integration = ShellIntegration.for_shell(Shell.BASH)
    assert integration.state() is ShellIntegrationState.ABSENT

    integration.config_path.write_text('eval "$(pcd shell init bash)"\n', encoding="utf-8")
    assert integration.state() is ShellIntegrationState.MANUAL


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell behavior test")
@pytest.mark.parametrize("shell", [Shell.BASH, Shell.ZSH, Shell.FISH])
def test_unix_wrappers_preserve_failure_output_and_environment(
    shell: Shell,
    tmp_path: Path,
) -> None:
    executable = shutil.which(shell.value)
    if executable is None:
        pytest.skip(f"{shell.value} is not installed")
    stub = tmp_path / "pcd"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ -n "${_PCD_COMPLETE:-}" ]; then exit 0; fi\n'
        'printf "wrapper=%s mode=%s\\n" "$PCD_WRAPPER" "$PCD_SHELL"\n'
        'printf "failure-output\\n"\n'
        "exit 3\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path}{os.pathsep}{environment['PATH']}"
    environment["PCD_WRAPPER"] = "original"
    environment["PCD_SHELL"] = "original"
    environment.pop("_PCD_COMPLETE", None)
    if shell is Shell.FISH:
        report = (
            "pcd; set -l code $status; "
            'printf "code=%s\\n" $code; '
            'printf "restored=%s,%s\\n" "$PCD_WRAPPER" "$PCD_SHELL"'
        )
    else:
        report = (
            'pcd; code=$?; printf "code=%s\\n" "$code"; '
            'printf "restored=%s,%s\\n" "$PCD_WRAPPER" "$PCD_SHELL"'
        )

    result = subprocess.run(
        [executable, "-c", render_shell_integration(shell) + "\n" + report],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"wrapper={shell.value} mode=1",
        "failure-output",
        "code=3",
        "restored=original,original",
    ]
    assert result.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell behavior test")
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
        render_shell_integration(Shell.BASH),
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
