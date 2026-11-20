from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from pcd_cli.cli import cli
from pcd_cli.integrations.shells.common import Shell
from pcd_cli.integrations.shells.features import optional_features
from pcd_cli.integrations.shells.registry import driver_for

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "pwsh", "powershell"])
def test_tracking_is_opt_in(runner: CliRunner, shell: str) -> None:
    default = runner.invoke(cli, ["shell", "init", shell])
    enabled = runner.invoke(cli, ["shell", "init", shell, "--track-cd"])
    assert default.exit_code == enabled.exit_code == 0
    assert optional_features(Shell(shell), track_cd=True) not in default.output
    assert optional_features(Shell(shell), track_cd=True) in enabled.output


def test_feature_environment_enables_installed_init(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PCD_TRACK_CD", "1")
    result = runner.invoke(cli, ["shell", "init", "bash"])
    assert result.exit_code == 0
    assert "_pcd_track_cd=1" in result.output


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior")
@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
def test_powershell_tracking_runs_only_for_changed_successful_cwd(
    tmp_path: Path, shell: Shell
) -> None:
    exe = shutil.which(shell.value)
    if exe is None:
        pytest.skip(f"{shell.value} is unavailable")
    target = tmp_path / "destination"
    target.mkdir()
    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    log = tmp_path / "visits.txt"
    stub = tmp_path / "pcd.cmd"
    stub.write_text(
        "@echo off\n"
        "if defined _PCD_COMPLETE exit /b 0\n"
        'if "%~1"=="_visit" goto visit\n'
        'if "%~1"=="fail" exit /b 3\n'
        'if "%~1"=="broken" (echo %PCD_BROKEN_TARGET%& exit /b 10)\n'
        "echo %PCD_TEST_TARGET%\nexit /b 10\n"
        ':visit\necho %CD%>>"%PCD_VISIT_LOG%"\nexit /b 0\n',
        encoding="utf-8",
    )
    integration = tmp_path / "integration.ps1"
    integration.write_text(
        driver_for(shell).render() + optional_features(shell, track_cd=True),
        encoding="utf-8",
    )
    script = tmp_path / "test.ps1"
    script.write_text(
        ". '" + str(integration).replace("'", "''") + "'\n"
        "prompt | Out-Null\nprompt | Out-Null\n"
        "Set-Location -LiteralPath $env:PCD_INTERMEDIATE_TARGET\n"
        "Set-Location -LiteralPath $env:PCD_TEST_TARGET\n"
        "prompt | Out-Null\nprompt | Out-Null\n"
        "pcd fail\nWrite-Output ('failed=' + $LASTEXITCODE)\n"
        "pcd broken -ErrorAction SilentlyContinue\n"
        "Write-Output ('broken=' + $LASTEXITCODE)\n"
        "prompt | Out-Null\n"
        "pcd alpha beta\nWrite-Output ('jump=' + $LASTEXITCODE)\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path) + os.pathsep + environment["PATH"]
    environment["PCD_VISIT_LOG"] = str(log)
    environment["PCD_TEST_TARGET"] = str(target)
    environment["PCD_BROKEN_TARGET"] = str(tmp_path / "missing")
    environment["PCD_INTERMEDIATE_TARGET"] = str(intermediate)
    result = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["failed=3", "broken=1", "jump=0"]
    assert log.read_text().splitlines() == [str(intermediate), str(target)]


@pytest.mark.skipif(os.name != "nt", reason="CMD behavior")
@pytest.mark.parametrize("delayed", ["off", "on"])
@pytest.mark.parametrize("arguments", ["Program Files", "/d Program Files", '"Program Files"'])
def test_cmd_records_only_changed_successful_cd(
    tmp_path: Path, delayed: str, arguments: str
) -> None:
    (tmp_path / "Program").mkdir()
    target = tmp_path / "Program Files"
    target.mkdir()
    log = tmp_path / "visits.txt"
    stub = tmp_path / "backend.cmd"
    stub.write_text(
        '@echo off\nif not "%~1"=="_visit" exit /b 3\n'
        "setlocal EnableDelayedExpansion\n"
        'echo !CD!>>"%PCD_VISIT_LOG%"\nexit /b 0\n',
        encoding="utf-8",
    )
    wrapper = tmp_path / "pcd.cmd"
    wrapper.write_text(driver_for(Shell.CMD).render(), encoding="utf-8")
    script = tmp_path / "test.cmd"
    script.write_text(
        "@echo off\n"
        f'call "{wrapper}" _shell-cd {arguments}\n'
        "echo first=%ERRORLEVEL%\n"
        "echo cwd=%CD%\n"
        f'call "{wrapper}" _shell-cd .\n'
        "echo same=%ERRORLEVEL%\n"
        f'call "{wrapper}" _shell-cd "missing-directory"\n'
        "echo missing=%ERRORLEVEL%\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["_PCD_EXECUTABLE"] = str(stub)
    environment["PCD_TRACK_CD"] = "1"
    environment["PCD_TEST_TARGET"] = str(target)
    environment["PCD_VISIT_LOG"] = str(log)
    result = subprocess.run(
        ["cmd", "/d", "/v:" + delayed, "/c", str(script)],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.stdout.splitlines() == ["first=0", f"cwd={target}", "same=0", "missing=1"]
    assert log.read_text().splitlines() == [str(target)]


@pytest.mark.parametrize("shell", [Shell.BASH, Shell.ZSH, Shell.FISH])
def test_posix_tracking_avoids_duplicate_visits(tmp_path: Path, shell: Shell) -> None:
    exe = shutil.which(shell.value)
    if os.name == "nt":
        exe = shutil.which("bash", path="C:/Program Files/Git/bin") if shell is Shell.BASH else None
    if exe is None:
        pytest.skip(f"{shell.value} unavailable")
    probe = subprocess.run([exe, "--version"], capture_output=True, check=False)
    if probe.returncode == 0xC0000142:
        pytest.skip("Git Bash cannot initialize in this Windows sandbox (0xC0000142)")
    target = tmp_path / "destination"
    target.mkdir()
    stub = tmp_path / "pcd"
    stub.write_text(
        "#!/bin/sh\n"
        'test -n "${_PCD_COMPLETE:-}" && exit 0\n'
        'if [ "$1" = _visit ]; then printf "%s\\n" "$PWD" >> "$PCD_VISIT_LOG"; exit 0; fi\n'
        'if [ "$1" = fail ]; then exit 3; fi\n'
        'printf "%s\\n" "$PCD_TEST_TARGET"\nexit 10\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    script = driver_for(shell).render() + optional_features(shell, track_cd=True)
    script += '\n_pcd_record_visit; cd "$PCD_TEST_TARGET"; _pcd_record_visit; '
    script += "_pcd_record_visit; pcd fail; pcd jump; _pcd_record_visit\n"
    log = tmp_path / "visits.txt"
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path) + os.pathsep + environment["PATH"]
    environment["PCD_TEST_TARGET"] = target.as_posix()
    environment["PCD_VISIT_LOG"] = log.as_posix()
    result = subprocess.run(
        [exe, "-c", script], capture_output=True, text=True, env=environment, check=False
    )
    assert result.returncode == 0, result.stderr
    assert len(log.read_text().splitlines()) == 1
    assert log.read_text().strip().endswith("/destination")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior")
@pytest.mark.parametrize("shell", [Shell.POWERSHELL, Shell.PWSH])
@pytest.mark.parametrize("track_cd", [False, True])
def test_jump_records_with_the_resolving_executable(
    tmp_path: Path, shell: Shell, track_cd: bool
) -> None:
    exe = shutil.which(shell.value)
    if exe is None:
        pytest.skip(f"{shell.value} unavailable")
    target = tmp_path / "destination"
    other = tmp_path / "other"
    target.mkdir()
    other.mkdir()
    # A relative PATH entry changes meaning after cd. Use the same backend
    # that resolved the project to record its successful jump.
    (target / "pcd.cmd").write_text(
        '@echo wrong recorder>>"%PCD_VISIT_LOG%"\n@exit /b 1\n', encoding="utf-8"
    )
    log = tmp_path / "visits.txt"
    (tmp_path / "pcd.cmd").write_text(
        "@echo off\nif defined _PCD_COMPLETE exit /b 0\n"
        'if "%~1"=="_visit" goto visit\n'
        "echo %PCD_TEST_TARGET%\nexit /b 10\n"
        ':visit\necho %~2:%CD%>>"%PCD_VISIT_LOG%"\nexit /b 0\n',
        encoding="utf-8",
    )
    script = tmp_path / "test.ps1"
    script.write_text(
        driver_for(shell).render()
        + optional_features(shell, track_cd=track_cd)
        + "\npcd jump\nSet-Location -LiteralPath $env:PCD_OTHER\npcd jump\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((".", str(tmp_path), environment["PATH"]))
    environment["PCD_TEST_TARGET"] = str(target)
    environment["PCD_OTHER"] = str(other)
    environment["PCD_VISIT_LOG"] = str(log)
    result = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=other,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    expected = (
        [f":{target}", f":{other}", f":{target}"] if track_cd else [f"--project-only:{target}"] * 2
    )
    assert log.read_text().splitlines() == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 7 session worker")
def test_powershell_restarts_exited_worker(runner: CliRunner, tmp_path: Path) -> None:
    from pcd_cli.filesystem import ApplicationPaths
    from pcd_cli.visits import DirectoryVisits

    shell = shutil.which("pwsh")
    if not shell or not shutil.which("pcd.exe"):
        pytest.skip("requires installed pcd.exe and pwsh")
    target = tmp_path / "session-target"
    target.mkdir()
    assert runner.invoke(cli, ["add", str(target)]).exit_code == 0
    script = tmp_path / "restart.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + driver_for(Shell.PWSH).render()
        + """
$start = $PWD.Path
try {
    pcd session-target
    if ($PWD.Path -ne $env:EXPECTED_TARGET) { throw 'first jump failed' }
    $first = $global:__pcdWorker
    $first.Kill($true)
    $first.WaitForExit()
    Set-Location -LiteralPath $start
    pcd session-target
    if ($PWD.Path -ne $env:EXPECTED_TARGET -or $global:__pcdWorker -eq $first) {
        throw 'worker did not restart'
    }
} finally {
    if ($global:__pcdWorker -and -not $global:__pcdWorker.HasExited) {
        $global:__pcdWorker.StandardInput.Close()
        $global:__pcdWorker.WaitForExit()
    }
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=tmp_path,
        env=os.environ | {"EXPECTED_TARGET": str(target)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert DirectoryVisits(ApplicationPaths.resolve().history).usage()[target.resolve()].count == 2
