"""Exercise an installed wheel without pytest or an editable source-tree import."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory


def smoke_wheel(expected_version: str) -> None:
    assert version("pcd-cli") == expected_version
    executable = Path(sys.executable).with_name("pcd")
    with TemporaryDirectory(prefix="pcd-wheel-") as directory:
        root = Path(directory).resolve()
        repo = root / "repo"
        (repo / ".git").mkdir(parents=True)
        home = root / "home"
        home.mkdir()
        environment = os.environ.copy()
        for name in (
            "PYTHONPATH",
            "PYTHONHOME",
            "PCD_SHELL",
            "PCD_WRAPPER",
            "_PCD_COMPLETE",
            "ZDOTDIR",
        ):
            environment.pop(name, None)
        environment.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_CACHE_HOME=str(root / "cache"),
            XDG_STATE_HOME=str(root / "state"),
            PATH=f"{executable.parent}{os.pathsep}{environment.get('PATH', '')}",
        )

        def run(*args: str, exit_code: int = 0) -> str:
            result = subprocess.run(
                [executable, *args],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            assert result.returncode == exit_code, result
            assert result.stderr == "", result.stderr
            return result.stdout

        assert run("--version") == f"pcd, version {expected_version}\n"
        help_text = run("--help")
        assert "Jump to local projects by name." in help_text
        assert run() == help_text
        assert run("init") == f"Added root: {root}\n"
        assert json.loads(run("list", "--json")) == [
            {"name": "repo", "path": str(repo), "source": "discovered", "status": "available"}
        ]
        for shell in ("bash", "zsh", "fish"):
            assert "pcd" in run("shell", "init", shell)
        environment["PCD_SHELL"] = "1"
        assert run("repo", exit_code=10) == f"{repo}\n"
    print(f"Wheel smoke passed for pcd-cli {expected_version}")


if __name__ == "__main__":
    smoke_wheel(sys.argv[1])
