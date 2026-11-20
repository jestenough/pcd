"""Real Windows launcher/import/PowerShell benchmark with isolated, accessible fixtures.

Run with the project's Python: ``python benchmarks/windows_navigation.py --prepare``.
After source changes/tests finish, run again without --prepare. Results and fixture
paths are recorded in benchmarks/baselines/windows-navigation.json. Use --search-only
to skip shell transitions and preserve the original full report. No user profiles
are read or modified. Use ``--zoxide PATH`` with an official portable executable.
The synthetic legacy z dataset is imported into a real isolated zoxide database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY / "benchmarks/baselines/windows-navigation-fixture.json"


def run(arguments: Sequence[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        arguments, env=env, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {arguments}\n{result.stderr}")
    return result.stdout.strip()


def environment(root: Path, executable: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("PCD_", "_PCD_", "PYTHON")):
            del env[key]
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(root / "roaming"),
        LOCALAPPDATA=str(root / "local"),
        WIN_PD_OVERRIDE_APPDATA=str(root / "roaming"),
        WIN_PD_OVERRIDE_LOCAL_APPDATA=str(root / "local"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_CACHE_HOME=str(root / "cache"),
        XDG_STATE_HOME=str(root / "state"),
        PATH=str(executable.parent) + os.pathsep + env["PATH"],
        PYTHONUTF8="1",
        TEMP=str(executable.parents[2] / "tmp"),
        TMP=str(executable.parents[2] / "tmp"),
    )
    return env


def summary(samples: list[float]) -> dict[str, object]:
    return {
        "samples_ms": samples,
        "median_ms": statistics.median(samples),
        "p90_ms": sorted(samples)[math.ceil(len(samples) * 0.9) - 1],
        "repeats": len(samples),
    }


def source_hash(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*.py")):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare(manifest: Path, size: int, work_dir: Path | None) -> None:
    root = Path(tempfile.mkdtemp(prefix="pcd-windows-navigation-", dir=work_dir))
    fixture = root / "directories"
    fixture.mkdir()
    started = time.perf_counter()
    paths: list[Path] = []
    for index in range(size):
        name = f"service-{index:06d}"
        if index == size - 3:
            name = "quartz-xylophone-violet-73142"
        elif index == size - 2:
            name = "navigation-alpha"
        elif index == size - 1:
            name = "navigation-beta"
        path = fixture / f"team-{index // 1000:03d}" / name
        path.mkdir(parents=True)
        paths.append(path)
    scores = root / "z-import.txt"
    scores.write_text(
        "".join(
            f"{path}|{1 + index % 97}.0|{int(time.time())}\n" for index, path in enumerate(paths)
        ),
        encoding="utf-8",
    )
    data: dict[str, object] = {
        "root": str(root),
        "size": size,
        "creation_seconds": time.perf_counter() - started,
        "all_destinations_exist": all(path.is_dir() for path in paths),
        "scores": str(scores),
        "alpha": str(paths[-2]),
        "beta": str(paths[-1]),
        "selective": str(paths[-3]),
        "multiword_query": f"team-{(size // 2) // 1000:03d} service-{size // 2:06d}",
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps(data, indent=2), flush=True)


def install(root: Path) -> Path:
    """Install a normal wheel into a local venv, keeping code/dependencies off Z:."""
    runtime = root / ".venv"
    scratch = root / "tmp"
    scratch.mkdir(exist_ok=True)
    build_env = os.environ.copy()
    build_env.update(
        TEMP=str(scratch),
        TMP=str(scratch),
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        POETRY_KEYRING_ENABLED="false",
        PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
    )
    python = runtime / "Scripts/python.exe"
    if not python.exists():
        run([sys.executable, "-m", "venv", str(runtime)], env=build_env)
    if not (runtime / "Lib/site-packages/pip").exists():
        run([str(python), "-m", "ensurepip", "--upgrade", "--default-pip"], env=build_env)
    package_dir = runtime / "Lib/site-packages"
    original = Path(sys.executable).parents[1] / "Lib/site-packages"
    for name in ("click", "platformdirs", "prompt_toolkit", "wcwidth", "tomlkit", "shellingham"):
        for path in original.glob(name + "*"):
            if path.is_dir():
                shutil.copytree(path, package_dir / path.name, dirs_exist_ok=True)
    wheels = root / "wheel"
    wheels.mkdir(exist_ok=True)
    run(
        [
            sys.executable,
            "-c",
            "from poetry.core.masonry.api import build_wheel; import sys; print(build_wheel(sys.argv[1]))",
            str(wheels),
        ],
        env=build_env,
    )
    wheel = next(wheels.glob("*.whl"))
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--force-reinstall",
            str(wheel),
        ],
        env=build_env,
    )
    executable = runtime / "Scripts/pcd.exe"
    run([str(executable), "--version"])
    return executable


POWERSHELL_WORKER = r"""
$ErrorActionPreference = 'Stop'
$mode = $env:PCD_BENCH_MODE
$exe = $env:PCD_BENCH_EXE
$shellName = $env:PCD_BENCH_SHELL
if ($mode.EndsWith('tracked')) {
    & $exe shell init $shellName --track-cd | Out-String | Invoke-Expression
} else {
    & $exe shell init $shellName | Out-String | Invoke-Expression
}
$samples = [System.Collections.Generic.List[double]]::new()
$repetitions = [int]$env:PCD_BENCH_REPETITIONS
for ($i = -3; $i -lt $repetitions; $i++) {
    $target = if (($i % 2) -eq 0) { $env:PCD_BENCH_ALPHA } else { $env:PCD_BENCH_BETA }
    $name = if (($i % 2) -eq 0) { 'navigation-alpha' } else { 'navigation-beta' }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    if ($mode.StartsWith('cd-')) { cd -LiteralPath $target } else { pcd $name }
    $clock.Stop()
    if ($PWD.Path -ne $target) { throw "Wrong destination: $PWD instead of $target" }
    if ($mode.StartsWith('pcd-') -and $global:LASTEXITCODE -ne 0) { throw 'pcd failed' }
    if ($i -ge 0) { $samples.Add($clock.Elapsed.TotalMilliseconds) }
}
@{samples_ms=$samples.ToArray(); final_directory=$PWD.Path; version=$PSVersionTable.PSVersion.ToString();
    executable=(Get-Command pcd -CommandType Application | Select-Object -First 1).Source} | ConvertTo-Json -Depth 4
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--search-only", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--size", type=int, default=100_000)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--zoxide", type=Path)
    parser.add_argument("--repeats", type=int, default=21)
    parser.add_argument("--search-repeats", type=int, default=9)
    parser.add_argument("--lookup-worker")
    parser.add_argument("--resume-search", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        args.output = DEFAULT_MANIFEST.with_name(
            "windows-search.json" if args.search_only else "windows-navigation.json"
        )
    if args.lookup_worker is not None:
        from pcd_cli.catalog import ProjectCatalog

        result = ProjectCatalog.create().resolve(args.lookup_worker)
        print(json.dumps({"total": result.total, "window": len(result.matches)}))
        return
    if platform.system() != "Windows":
        parser.error("This benchmark requires real Windows shells and launchers")
    if args.size < 100 or args.repeats < 1 or args.search_repeats < 1:
        parser.error("--size must be >= 100; repetition counts must be positive")
    if args.prepare:
        prepare(args.manifest, args.size, args.work_dir)
        return
    if args.zoxide is None:
        parser.error("--zoxide must name an official portable zoxide executable")
    fixture = json.loads(args.manifest.read_text(encoding="utf-8"))
    initial_hash = source_hash(REPOSITORY / "src/pcd_cli")
    root = Path(fixture["root"])
    executable = install(root)
    state = root / "state-imported"
    env = environment(state, executable)
    env.update(
        _Z_DATA=fixture["scores"],
        _ZO_DATA_DIR=str(root / "zoxide-state"),
        _ZO_MAXAGE="1000000000",
        PATH=str(args.zoxide.parent) + os.pathsep + env["PATH"],
    )
    zoxide_version = run([str(args.zoxide), "--version"], env=env)
    run([str(args.zoxide), "import", "z", "--merge"], env=env)
    zoxide_rows = run([str(args.zoxide), "query", "--list", "--score", "--all"], env=env)
    if len(zoxide_rows.splitlines()) != fixture["size"]:
        raise RuntimeError("Official zoxide did not retain the requested fixture size")
    started = time.perf_counter()
    import_output = run([str(executable), "import", "zoxide"], env=env)
    import_seconds = time.perf_counter() - started
    if import_output != f"Merged {fixture['size']} directories.":
        raise RuntimeError(f"Unexpected import result: {import_output}")
    for key in ("alpha", "beta"):
        run([str(executable), "add", fixture[key]], env=env)
    env["PCD_SHELL"] = "1"
    results: dict[str, object] = {}
    if args.resume_search is not None:
        previous = json.loads(args.resume_search.read_text(encoding="utf-8"))
        if previous["source_sha256"] != initial_hash:
            raise RuntimeError(
                "Cannot resume searches measured against a different source snapshot"
            )
        results = previous["search"]
    middle = fixture["size"] // 2
    queries = {
        "exact_project": (["navigation-alpha"], Path(fixture["alpha"])),
        "exact_imported_directory": (
            ["service-000017"],
            root / "directories/team-000/service-000017",
        ),
        "selective_fuzzy": (["qzxv73142"], Path(fixture["selective"])),
        "multiword": (
            fixture["multiword_query"].split(),
            root / f"directories/team-{middle // 1000:03d}/service-{middle:06d}",
        ),
    }
    for label, (query, expected) in queries.items():
        if label in results:
            continue
        samples: list[float] = []
        for repeat in range(-2, args.search_repeats):
            start = time.perf_counter()
            completed = subprocess.run(
                [str(executable), *query], env=env, capture_output=True, text=True, check=False
            )
            elapsed = (time.perf_counter() - start) * 1000
            actual = Path(completed.stdout.strip())
            if completed.returncode != 10 or actual != expected or not actual.is_dir():
                raise RuntimeError(f"{label} failed: {completed.returncode}: {completed.stderr}")
            if repeat >= 0:
                samples.append(elapsed)
        results[label] = {
            "query": query,
            "verified_destination": str(expected),
            "verified_returncode": 10,
            **summary(samples),
        }
        print(json.dumps({label: results[label]}), flush=True)
    local_worker = root / "lookup_worker.py"
    shutil.copyfile(__file__, local_worker)
    broad_samples: list[float] = []
    broad_result: dict[str, int] = {}
    broad_repeats = range(-2, args.search_repeats)
    if args.resume_search is not None:
        previous_broad = previous["search"]["broad_initial_resolution"]
        broad_samples = previous_broad["samples_ms"]
        broad_result = {"total": previous_broad["total"], "window": previous_broad["window"]}
        broad_repeats = range(0)
    for repeat in broad_repeats:
        started = time.perf_counter()
        broad_result = json.loads(
            run(
                [
                    str(executable.with_name("python.exe")),
                    str(local_worker),
                    "--lookup-worker",
                    "service",
                ],
                env=env,
            )
        )
        elapsed = (time.perf_counter() - started) * 1000
        if broad_result["total"] != fixture["size"] - 3 or broad_result["window"] != 100:
            raise RuntimeError(f"Unexpected broad query results: {broad_result}")
        if repeat >= 0:
            broad_samples.append(elapsed)
    results["broad_initial_resolution"] = {
        "query": "service",
        **broad_result,
        **summary(broad_samples),
    }
    print(json.dumps({"broad_initial_resolution": results["broad_initial_resolution"]}), flush=True)
    worker = root / "transitions.ps1"
    if not args.search_only:
        worker.write_text(POWERSHELL_WORKER, encoding="utf-8")
    shells: dict[str, object] = {}
    for shell_name in () if args.search_only else ("powershell", "pwsh"):
        shell = shutil.which(shell_name)
        if shell is None:
            shells[shell_name] = {"not_run": "Shell is unavailable"}
            continue
        modes: dict[str, object] = {}
        for mode in ("cd-default", "cd-tracked", "pcd-default", "pcd-tracked"):
            scenario = root / (shell_name + "-" + mode)
            shutil.copytree(state, scenario, dirs_exist_ok=True)
            scenario_env = environment(scenario, executable)
            scenario_env.update(
                PCD_BENCH_MODE=mode,
                PCD_BENCH_SHELL=shell_name,
                PCD_BENCH_EXE=str(executable),
                PCD_BENCH_ALPHA=fixture["alpha"],
                PCD_BENCH_BETA=fixture["beta"],
                PCD_BENCH_REPETITIONS=str(args.repeats),
            )
            output = json.loads(
                run(
                    [shell, "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_WORKER],
                    env=scenario_env,
                )
            )
            samples = output.pop("samples_ms")
            if output["executable"] != str(executable):
                raise RuntimeError("PowerShell selected an unexpected pcd executable")
            database = next(scenario.rglob("directories.sqlite3"))
            with sqlite3.connect(database) as db:
                rows, visits, tracked = db.execute(
                    "SELECT COUNT(*),SUM(count),SUM(tracked_used > 0) FROM visits"
                ).fetchone()
            expected_visits = 0 if mode == "cd-default" else args.repeats + 3
            expected_tracked = 2 if mode.endswith("tracked") else 0
            if (rows, visits, tracked) != (fixture["size"], expected_visits, expected_tracked):
                raise RuntimeError(
                    f"Unexpected history after {shell_name} {mode}: {(rows, visits, tracked)}"
                )
            modes[mode] = {
                **output,
                "database_rows": rows,
                "recorded_visits": visits,
                "tracked_destinations": tracked,
                **summary(samples),
            }
            print(json.dumps({shell_name + "_" + mode: modes[mode]}), flush=True)
        shells[shell_name] = modes
    final_hash = source_hash(REPOSITORY / "src/pcd_cli")
    installed_hash = source_hash(root / ".venv/Lib/site-packages/pcd_cli")
    if initial_hash != final_hash or installed_hash != final_hash:
        raise RuntimeError("Source changed during measurements, or installed code differs")
    output = {
        "mode": "search-only" if args.search_only else "search-and-transitions",
        "platform": platform.platform(),
        "python": sys.version,
        "benchmark_wheel_backend": run(
            [sys.executable, "-c", "import poetry.core; print(poetry.core.__version__)"]
        ),
        "source_sha256": final_hash,
        "installed_sha256": installed_hash,
        "fixture": fixture,
        "import": {
            "source": "real official zoxide.exe, synthetic legacy z fixture imported into isolated zoxide DB",
            "zoxide_version": zoxide_version,
            "zoxide_sha256": hashlib.sha256(args.zoxide.read_bytes()).hexdigest(),
            "stdout": import_output,
            "seconds": import_seconds,
            "timing_scenario": "repeat merge after resuming searches"
            if args.resume_search
            else "initial merge",
        },
        "search": results,
        "transitions": shells,
        "methodology": "Real pcd.exe installed from wheel in a local C: .venv; copied dependencies on C:. All imported paths exist. Official zoxide DB seeded using synthetic legacy z path|rank|timestamp records; pcd import invokes real zoxide query. Fresh-process searches include launch/import/exit; two warmups. Transition timings are repeated alternating real directory changes inside an existing isolated -NoProfile PowerShell session; three warmups, shell startup/integration excluded. Separate state copies per mode with the same imported 100k rows. p90 nearest rank. Warm filesystem caches; no interactive picker, no terminal rendering. No installed user profile changed. Antivirus state is not controlled.",
    }
    if args.search_only:
        output["methodology"] = (
            "Real pcd.exe installed from wheel in a local C: .venv; copied dependencies on C:. "
            "All imported paths exist. Official zoxide DB seeded using synthetic legacy z "
            "path|rank|timestamp records; pcd import invokes real zoxide query. "
            "Fresh-process searches include launch/import/exit; two warmups. "
            "Broad query times initial resolution via the installed package with no picker. "
            "p90 nearest rank. Warm filesystem caches; no interactive picker, no terminal "
            "rendering or shell transition measurements. No installed user profile changed. "
            "Antivirus state is not controlled."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(str(args.output), flush=True)


if __name__ == "__main__":
    main()
