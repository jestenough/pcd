"""Compare end-to-end jumps with one or two shells sharing a real manual catalog."""

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from pathlib import Path

POWERSHELL = """
$ErrorActionPreference = 'Stop'
pcd shell init pwsh | Out-String | Invoke-Expression
$startDir = $PWD
[Console]::WriteLine('ready')
while ($null -ne [Console]::ReadLine()) {
    Set-Location -LiteralPath $startDir
    pcd target
    if ($LASTEXITCODE -ne 0) { exit 1 }
    [Console]::WriteLine($PWD.Path)
}
"""
BASH = """
set -e
eval "$(command pcd shell init bash)"
start_dir=$PWD
printf 'ready\n'
while IFS= read -r line; do
    builtin cd -- "$start_dir"
    pcd target
    printf '%s\n' "$PWD"
done
"""


def prepare(root: Path, source: Path, size: int) -> dict[str, str]:
    root.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PCD_", "_PCD_"))}
    for key in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "WIN_PD_OVERRIDE_APPDATA",
        "WIN_PD_OVERRIDE_LOCAL_APPDATA",
    ):
        env[key] = str(root / key)
        Path(env[key]).mkdir()
    env["PYTHONPATH"] = str(source.resolve(strict=True))
    for index in range(size):
        path = root / ("target" if index == 0 else f"checkout-{index}")
        path.mkdir()
        subprocess.run(
            ["pcd", "add", str(path)], env=env, cwd=root, check=True, capture_output=True
        )
    return env


@contextmanager
def shell(root: Path, env: dict[str, str]) -> Iterator[subprocess.Popen[str]]:
    windows = os.name == "nt"
    script = root / ("worker.ps1" if windows else "worker.sh")
    script.write_text(POWERSHELL if windows else BASH, encoding="utf-8")
    command = ["pwsh", "-NoProfile", "-NonInteractive", "-File"] if windows else ["bash", "--norc"]
    with subprocess.Popen(
        [*command, str(script)],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as worker:
        assert worker.stdin is not None and worker.stdout is not None
        try:
            if worker.stdout.readline().strip() != "ready":
                raise RuntimeError("Shell initialization failed")
            yield worker
        finally:
            worker.stdin.close()
            if worker.wait(timeout=30):
                raise RuntimeError("Shell worker failed")


def jump(worker: subprocess.Popen[str], target: Path) -> float:
    assert worker.stdin is not None and worker.stdout is not None
    start = time.perf_counter_ns()
    worker.stdin.write("jump\n")
    worker.stdin.flush()
    line = worker.stdout.readline().rstrip("\r\n")
    elapsed = (time.perf_counter_ns() - start) / 1e6
    if not line or not target.samefile(line):
        raise RuntimeError(f"Unexpected destination {line!r}")
    return elapsed


def measure(
    root: Path, sources: dict[str, Path], size: int, repeats: int
) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {}
    environments = {}
    with ExitStack() as stack, ThreadPoolExecutor(2) as pool:
        workers = {}
        for label, source in sources.items():
            folder = root / f"{label}-{size}"
            env = prepare(folder, source, size)
            environments[label] = env
            workers[label] = [stack.enter_context(shell(folder, env)) for _ in range(2)]
        for iteration in range(repeats + 3):
            for label in list(workers) if iteration % 2 else list(reversed(workers)):
                target = root / f"{label}-{size}" / "target"
                for count in (1, 2):
                    pending = [
                        pool.submit(jump, worker, target) for worker in workers[label][:count]
                    ]
                    times = [future.result() for future in pending]
                    if iteration >= 3:
                        samples.setdefault(f"{label}/{size}/{count}", []).extend(times)
    # Shells keep a successful cd even if recording failed. Verify the count too.
    for label, env in environments.items():
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from pathlib import Path; "
                "from pcd_cli.catalog import ProjectCatalog; "
                "visits = ProjectCatalog.create().history.load(); "
                "assert visits[Path.cwd().resolve()].count == int(sys.argv[1])",
                str(3 * (repeats + 3)),
            ],
            cwd=root / f"{label}-{size}" / "target",
            env=env,
            check=True,
        )
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=Path("src"))
    parser.add_argument("--repeats", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    samples = {}
    with tempfile.TemporaryDirectory() as temporary:
        for size in (1, 25):
            samples.update(
                measure(
                    Path(temporary),
                    {"before": args.baseline_source, "after": args.source},
                    size,
                    args.repeats,
                )
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    for case, values in samples.items():
        p95 = sorted(values)[math.ceil(len(values) * 0.95) - 1]
        print(f"{case}: median {statistics.median(values):.1f} ms, p95 {p95:.1f} ms")


if __name__ == "__main__":
    main()
