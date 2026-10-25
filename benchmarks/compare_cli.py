"""Compare working-tree CLI with a Git baseline in isolated, fresh processes."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("cli_worker.py")


@dataclass(frozen=True)
class Operation:
    name: str
    args: tuple[str, ...]
    expected_code: int = 0
    kind: str = "cli"
    picker: bool = False
    completion: bool = False


def environment(source: Path, home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "PCD_WRAPPER",
        "_PCD_COMPLETE",
        "PYTHONHOME",
        "PCD_BENCH_PICKER",
        "PCD_BENCH_MEMORY",
        "PCD_BENCH_TRACE",
    ):
        env.pop(key, None)
    env.update(
        PYTHONPATH=str(source / "src"),
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "roaming"),
        LOCALAPPDATA=str(home / "local"),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_CACHE_HOME=str(home / "cache"),
        XDG_STATE_HOME=str(home / "state"),
        PCD_SHELL="1",
    )
    return env


def invoke(
    args: list[str], env: dict[str, str], home: Path, memory: bool = False, trace: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *(["-X", "tracemalloc"] if trace else []), str(WORKER), *args],
        cwd=home,
        env=env | {"PCD_BENCH_MEMORY": "1"} if memory else env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )


def run_operation(
    operation: Operation,
    env: dict[str, str],
    home: Path,
    *,
    memory: bool = False,
    trace: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = env.copy()
    if operation.picker:
        env["PCD_BENCH_PICKER"] = "1"
    if operation.completion:
        env.update(_PCD_COMPLETE="bash_complete", COMP_WORDS="pcd project-", COMP_CWORD="1")
    if operation.kind == "wrapper":
        if memory:
            env["PCD_BENCH_MEMORY"] = "1"
        if trace:
            env["PCD_BENCH_TRACE"] = "1"
        return subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                'eval "$(command pcd shell init bash)"; pcd "$1"',
                "bench",
                *operation.args,
            ],
            cwd=home,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    mode = "history" if operation.kind == "history" else "cli"
    return invoke([mode, *operation.args], env, home, memory=memory, trace=trace)


def measure(
    operation: Operation,
    homes: dict[str, Path],
    envs: dict[str, dict[str, str]],
    snapshots: dict[str, dict[Path, bytes]],
    repeats: int,
    *,
    size: int,
    history: int,
    shape: str = "cache",
) -> list[dict[str, object]]:
    def restore(label: str) -> None:
        for path, content in snapshots[label].items():
            path.write_bytes(content)

    def run(
        label: str, *, memory: bool = False, trace: bool = False
    ) -> subprocess.CompletedProcess[str]:
        result = run_operation(operation, envs[label], homes[label], memory=memory, trace=trace)
        if result.returncode != operation.expected_code:
            raise RuntimeError(f"{shape}/{operation.name}/{label}: {result.stderr}")
        return result

    timings: dict[str, list[float]] = {"baseline": [], "candidate": []}
    for iteration in range(repeats + 1):
        order = ("baseline", "candidate") if iteration % 2 else ("candidate", "baseline")
        for label in order:
            restore(label)
            started = time.perf_counter()
            run(label)
            elapsed = (time.perf_counter() - started) * 1000
            if iteration:
                timings[label].append(elapsed)
    measurements: list[dict[str, object]] = []
    for label, samples in timings.items():
        restore(label)
        memory = json.loads(run(label, memory=True).stderr.splitlines()[-1])
        restore(label)
        memory["python_peak_bytes"] = json.loads(run(label, trace=True).stderr.splitlines()[-1])[
            "python_peak_bytes"
        ]
        ordered = sorted(samples)
        measurement = {
            "size": size,
            "history": history,
            "shape": shape,
            "operation": operation.name,
            "version": label,
            "samples_ms": samples,
            "median_ms": statistics.median(samples),
            "p90_ms": ordered[math.ceil(0.9 * len(ordered)) - 1],
            "memory": memory,
        }
        measurements.append(measurement)
        print(
            f"{shape:6} {size:>6} history={history:>4} {operation.name:16} {label:9}: {measurement['median_ms']:.1f} ms",
            flush=True,
        )
    return measurements


def wrapper_shim(home: Path) -> None:
    binary = home / "bin"
    binary.mkdir()
    shim = binary / "pcd"
    python = shlex.quote(sys.executable)
    worker = shlex.quote(str(WORKER))
    shim.write_text(
        f'#!/bin/sh\nif [ "$PCD_BENCH_TRACE" = 1 ]; then\n'
        f'  exec {python} -X tracemalloc {worker} cli "$@"\n'
        f'fi\nexec {python} {worker} cli "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)


def compare(
    baseline: str, sizes: list[int], tree_sizes: list[int], repeats: int, output: Path
) -> None:
    revision = subprocess.check_output(["git", "rev-parse", baseline], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", revision, "src"], cwd=ROOT)
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="pcd-cli-comparison-") as directory:
        temporary = Path(directory)
        baseline_source = temporary / "baseline_"
        baseline_source.mkdir()
        candidate_source = temporary / "candidate"
        shutil.copytree(
            ROOT / "src",
            candidate_source / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        source_hash = hashlib.sha256()
        for path in sorted((candidate_source / "src").rglob("*.py")):
            source_hash.update(path.relative_to(candidate_source).as_posix().encode("utf-8"))
            source_hash.update(path.read_bytes())
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(baseline_source, filter="data")
        bash = os.name != "nt" and shutil.which("bash") is not None
        for size in sizes:
            for history_size in dict.fromkeys((0, min(2048, size))):
                homes = {
                    label: temporary / f"{label:_<9}-{size}-{history_size}"
                    for label in ("baseline", "candidate")
                }
                envs: dict[str, dict[str, str]] = {}
                snapshots: dict[str, dict[Path, bytes]] = {}
                for label, home in homes.items():
                    home.mkdir()
                    envs[label] = environment(
                        baseline_source if label == "baseline" else candidate_source, home
                    )
                    prepared = invoke(["prepare", str(size), str(history_size)], envs[label], home)
                    if prepared.returncode:
                        raise RuntimeError(prepared.stderr)
                    setup = json.loads(prepared.stdout)
                    history_path = Path(setup.pop("history_path"))
                    setup.pop("config_path")
                    setup.pop("cache_path")
                    snapshots[label] = {history_path: history_path.read_bytes()}
                    if bash:
                        wrapper_shim(home)
                        envs[label]["PATH"] = f"{home / 'bin'}{os.pathsep}{envs[label]['PATH']}"
                    results.append(
                        {
                            "size": size,
                            "history": history_size,
                            "version": label,
                            "operation": "save",
                            "measurement": setup,
                        }
                    )
                operations = [
                    Operation("startup", ("--help",)),
                    Operation("exact-first", ("project-000000",), 10),
                    Operation("exact-last", (f"project-{size - 1:06d}",), 10),
                    Operation("fuzzy-broad", ("project-",), picker=True),
                    Operation("fuzzy-selective", (f"pjt{size - 1:06d}",), 10),
                    Operation("completion", (), completion=True),
                ]
                if size == 0:
                    operations = [
                        operations[0],
                        Operation("missing", ("missing",), 3),
                        operations[-1],
                    ]
                operations.extend(
                    [
                        Operation("history-load", ("load",), kind="history"),
                        Operation("history-record", ("record",), kind="history"),
                    ]
                )
                if bash and size:
                    operations.append(
                        Operation("bash-wrapper", ("project-000000",), kind="wrapper")
                    )
                for operation in operations:
                    results.extend(
                        measure(
                            operation,
                            homes,
                            envs,
                            snapshots,
                            repeats,
                            size=size,
                            history=history_size,
                        )
                    )
        for shape in ("wide", "grouped"):
            for size in tree_sizes:
                homes = {
                    label: temporary / f"{label:_<9}-{shape}-{size}"
                    for label in ("baseline", "candidate")
                }
                envs = {}
                snapshots = {}
                for label, home in homes.items():
                    home.mkdir()
                    envs[label] = environment(
                        baseline_source if label == "baseline" else candidate_source, home
                    )
                    prepared = invoke(["prepare-tree", str(size), shape], envs[label], home)
                    if prepared.returncode:
                        raise RuntimeError(prepared.stderr)
                    setup = json.loads(prepared.stdout)
                    snapshots[label] = {
                        Path(path): Path(path).read_bytes()
                        for path in (
                            setup["config_path"],
                            setup["cache_path"],
                            setup["history_path"],
                        )
                    }
                for operation in (
                    Operation("refresh", ("refresh",)),
                    Operation("exact-miss", ("missing",), 3),
                    Operation("add", ("add", "add-target")),
                ):
                    results.extend(
                        measure(
                            operation,
                            homes,
                            envs,
                            snapshots,
                            repeats,
                            size=size,
                            history=0,
                            shape=shape,
                        )
                    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "baseline": revision,
                "candidate_source_sha256": source_hash.hexdigest(),
                "platform": platform.platform(),
                "python": sys.version,
                "repeats": repeats,
                "shell_wrapper": "bash" if bash else "unavailable",
                "tree_sizes": tree_sizes,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="master")
    parser.add_argument("--sizes", nargs="+", type=int, default=[0, 1000, 10000, 100000])
    parser.add_argument("--tree-sizes", nargs="+", type=int, default=[250, 2000])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path, default=Path("cli-comparison.json"))
    options = parser.parse_args()
    if options.repeats < 1 or any(size < 0 for size in (*options.sizes, *options.tree_sizes)):
        parser.error("sizes must be nonnegative and repeats must be positive")
    compare(options.baseline, options.sizes, options.tree_sizes, options.repeats, options.output)
