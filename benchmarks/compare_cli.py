"""Compare working-tree CLI with a Git baseline in isolated, fresh processes."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("cli_worker.py")


def environment(source: Path, home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "PCD_WRAPPER",
        "_PCD_COMPLETE",
        "PYTHONHOME",
        "PCD_BENCH_PICKER",
        "PCD_BENCH_MEMORY",
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


def compare(baseline: str, sizes: list[int], repeats: int, output: Path) -> None:
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
        for size in sizes:
            for history_size in (0, 2048):
                homes = {
                    label: temporary / f"{label:_<9}-{size}-{history_size}"
                    for label in ("baseline", "candidate")
                }
                envs: dict[str, dict[str, str]] = {}
                histories: dict[str, tuple[Path, bytes]] = {}
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
                    histories[label] = (history_path, history_path.read_bytes())
                    results.append(
                        {
                            "size": size,
                            "history": history_size,
                            "version": label,
                            "operation": "save",
                            "measurement": setup,
                        }
                    )
                operations = {
                    "startup": ["--help"],
                    "exact-first": ["project-000000"],
                    "exact-last": [f"project-{size - 1:06d}"],
                    "fuzzy-broad": ["project-"],
                    "fuzzy-selective": [f"pjt{size - 1:06d}"],
                    "completion": [],
                }
                if size == 0:
                    operations = {"startup": ["--help"], "missing": ["missing"], "completion": []}
                for operation, arguments in operations.items():
                    timings: dict[str, list[float]] = {"baseline": [], "candidate": []}
                    for iteration in range(repeats + 1):
                        for label in (
                            ("baseline", "candidate")
                            if iteration % 2
                            else ("candidate", "baseline")
                        ):
                            env = envs[label].copy()
                            if operation == "fuzzy-broad":
                                env["PCD_BENCH_PICKER"] = "1"
                            if operation == "completion":
                                env.update(
                                    _PCD_COMPLETE="bash_complete",
                                    COMP_WORDS="pcd project-",
                                    COMP_CWORD="1",
                                )
                            history_path, history_bytes = histories[label]
                            history_path.write_bytes(history_bytes)
                            started = time.perf_counter()
                            result = invoke(["cli", *arguments], env, homes[label])
                            elapsed = (time.perf_counter() - started) * 1000
                            expected_code = (
                                3
                                if operation == "missing"
                                else (
                                    10
                                    if operation in ("exact-first", "exact-last", "fuzzy-selective")
                                    else 0
                                )
                            )
                            if result.returncode != expected_code:
                                raise RuntimeError(f"{operation}: {result.stderr}")
                            if iteration:
                                timings[label].append(elapsed)
                    for label, samples in timings.items():
                        env = envs[label].copy()
                        if operation == "fuzzy-broad":
                            env["PCD_BENCH_PICKER"] = "1"
                        if operation == "completion":
                            env.update(
                                _PCD_COMPLETE="bash_complete",
                                COMP_WORDS="pcd project-",
                                COMP_CWORD="1",
                            )
                        history_path, history_bytes = histories[label]
                        history_path.write_bytes(history_bytes)
                        memory = invoke(["cli", *arguments], env, homes[label], memory=True)
                        if memory.returncode != expected_code:
                            raise RuntimeError(memory.stderr)
                        measurement = json.loads(memory.stderr.splitlines()[-1])
                        history_path.write_bytes(history_bytes)
                        traced = invoke(["cli", *arguments], env, homes[label], trace=True)
                        if traced.returncode != expected_code:
                            raise RuntimeError(traced.stderr)
                        measurement["python_peak_bytes"] = json.loads(
                            traced.stderr.splitlines()[-1]
                        )["python_peak_bytes"]
                        results.append(
                            {
                                "size": size,
                                "history": history_size,
                                "operation": operation,
                                "version": label,
                                "samples_ms": samples,
                                "median_ms": statistics.median(samples),
                                "memory": measurement,
                            }
                        )
                        print(
                            f"{size:>6} history={history_size:>4} {operation:16} {label:9}: {statistics.median(samples):.1f} ms",
                            flush=True,
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
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path, default=Path("cli-comparison.json"))
    options = parser.parse_args()
    if options.repeats < 1 or any(size < 0 for size in options.sizes):
        parser.error("sizes must be nonnegative and repeats must be positive")
    compare(options.baseline, options.sizes, options.repeats, options.output)
