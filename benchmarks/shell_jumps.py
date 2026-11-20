"""Compare verified pcd and zoxide jumps in a persistent native shell."""

import argparse
import contextlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASH = """set -e
eval "$(command pcd shell init bash)"
eval "$(command zoxide init bash)"
printf 'ready\\n'
while read -r tool name; do
    case "$tool" in
        pcd) pcd "$name" ;;
        z) z "$name" ;;
        *) exit 2 ;;
    esac
    printf '%s\\n' "$PWD"
done
"""

POWERSHELL = """$ErrorActionPreference = 'Stop'
pcd shell init pwsh | Out-String | Invoke-Expression
zoxide init powershell | Out-String | Invoke-Expression
[Console]::WriteLine('ready')
while ($null -ne ($line = [Console]::ReadLine())) {
    $tool, $name = $line.Split(' ')
    if ($tool -eq 'pcd') { pcd $name } elseif ($tool -eq 'z') { z $name } else { exit 2 }
    [Console]::WriteLine($PWD.Path)
}
"""


def measure(root: Path, *, phases: bool = False) -> dict[str, list[float]]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("PCD_", "_PCD_", "_ZO_")):
            del env[key]
    for key in (
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "WIN_PD_OVERRIDE_APPDATA",
        "WIN_PD_OVERRIDE_LOCAL_APPDATA",
        "_ZO_DATA_DIR",
    ):
        env[key] = str(root / key)
        Path(env[key]).mkdir(exist_ok=True)
    targets = [root / name for name in ("jump-alpha", "jump-beta")]
    for target in targets:
        target.mkdir()
        for tool in ("pcd", "zoxide"):
            subprocess.run([tool, "add", str(target)], env=env, cwd=root, check=True)
    windows = platform.system() == "Windows"
    script = root / ("worker.ps1" if windows else "worker.sh")
    script.write_text(POWERSHELL if windows else BASH, encoding="utf-8")
    shell = (
        ["pwsh", "-NoProfile", "-NonInteractive", "-File"]
        if windows
        else ["bash", "--noprofile", "--norc"]
    )
    samples: dict[str, list[float]] = {"pcd": [], "z": []}
    with (
        subprocess.Popen(
            [*shell, str(script)],
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as proc,
        contextlib.ExitStack() as stack,
    ):
        assert proc.stdin is not None and proc.stdout is not None
        stack.callback(proc.stdin.close)
        if proc.stdout.readline().strip() != "ready":
            raise RuntimeError("Shell initialization failed")
        for iteration in range(24):
            for tool in ("pcd", "z") if iteration % 2 == 0 else ("z", "pcd"):
                for target in targets:
                    start = time.perf_counter_ns()
                    proc.stdin.write(f"{tool} {target.name}\n")
                    proc.stdin.flush()
                    destination = proc.stdout.readline().rstrip("\r\n")
                    elapsed = (time.perf_counter_ns() - start) / 1e6
                    if not destination or not Path(destination).samefile(target):
                        raise RuntimeError(f"{tool} returned unexpected directory {destination!r}")
                    if iteration >= 3:
                        samples[tool].append(elapsed)
        proc.stdin.close()
        if proc.wait(timeout=15):
            raise RuntimeError("Shell exited unsuccessfully")
    if phases:
        samples.update(measure_cli_phases(env, targets))
    return samples


def measure_cli_phases(env: dict[str, str], targets: list[Path]) -> dict[str, list[float]]:
    """Time the two CLI invocations independently of the wrapper and its IPC."""
    samples: dict[str, list[float]] = {"python": [], "import_cli": [], "resolve": [], "record": []}
    for index in range(24):
        target = targets[index % len(targets)]
        for phase, command, code in (
            ("python", [sys.executable, "-c", "pass"], 0),
            ("import_cli", [sys.executable, "-c", "import pcd_cli.cli"], 0),
            ("resolve", ["pcd", target.name], 10),
            ("record", ["pcd", "_visit", "--project-only"], 0),
        ):
            start = time.perf_counter_ns()
            result = subprocess.run(
                command,
                cwd=target if phase == "record" else target.parent,
                env=env | {"PCD_SHELL": "1"},
                text=True,
                capture_output=True,
                timeout=30,
            )
            elapsed = (time.perf_counter_ns() - start) / 1e6
            if result.returncode != code or result.stderr:
                raise RuntimeError(f"{phase} failed: {result.returncode} {result.stderr}")
            if phase == "resolve" and not Path(result.stdout.strip()).samefile(target):
                raise RuntimeError("Resolved the wrong directory")
            if index >= 3:
                samples[phase].append(elapsed)
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--phases", action="store_true", help="Also time lookup and visit recording."
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        samples = measure(Path(directory).resolve(), phases=args.phases)
    result = {
        "platform": platform.platform(),
        "zoxide": subprocess.check_output(["zoxide", "--version"], text=True).strip(),
        "samples_ms": samples,
        "median_ms": {tool: statistics.median(times) for tool, times in samples.items()},
    }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
