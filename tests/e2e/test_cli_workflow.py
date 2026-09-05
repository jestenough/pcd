from __future__ import annotations

import os
import pty
import subprocess
import sys
from pathlib import Path


def _pcd_executable() -> Path:
    return Path(sys.executable).with_name("pcd")


def _environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "PATH": f"{_pcd_executable().parent}{os.pathsep}{environment['PATH']}",
            "SHELL": "/bin/bash",
        }
    )
    Path(environment["HOME"]).mkdir(exist_ok=True)
    return environment


def test_cli_discovers_and_lists_project(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "repo/.git").mkdir(parents=True)
    environment = _environment(tmp_path)

    initialized = subprocess.run(
        [_pcd_executable(), "init"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    listed = subprocess.run(
        [_pcd_executable(), "list"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert initialized.returncode == 0
    assert "Added root" in initialized.stdout
    assert listed.returncode == 0
    assert "repo" in listed.stdout
    assert "scanned" in listed.stdout
    assert "available" in listed.stdout


def test_bash_wrapper_changes_parent_shell_directory(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    environment = _environment(tmp_path)
    subprocess.run([_pcd_executable(), "init"], cwd=root, env=environment, check=True)

    result = subprocess.run(
        [
            "bash",
            "-c",
            'eval "$("$1" shell print bash)"; pcd repo; pwd',
            "pcd-test",
            str(_pcd_executable()),
        ],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == str(repo)


def test_config_edit_keeps_terminal_attached_through_bash_wrapper(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    marker = tmp_path / "editor-opened"
    editor = tmp_path / "editor"
    editor.write_text(
        "#!/bin/sh\n"
        "if [ -t 0 ] && [ -t 1 ] && [ -t 2 ]; then\n"
        '    : > "$PCD_TEST_MARKER"\n'
        "    exit 0\n"
        "fi\n"
        "exit 91\n",
        encoding="utf-8",
    )
    editor.chmod(0o755)
    environment["EDITOR"] = str(editor)
    environment["PCD_TEST_MARKER"] = str(marker)
    master_fd, slave_fd = pty.openpty()

    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'eval "$("$1" shell print bash)"; pcd config edit',
                "pcd-test",
                str(_pcd_executable()),
            ],
            env=environment,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            check=False,
            timeout=10,
        )
    finally:
        os.close(slave_fd)
        os.close(master_fd)

    assert result.returncode == 0
    assert marker.is_file()
