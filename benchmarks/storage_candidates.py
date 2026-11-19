"""Compare the legacy JSONL resolver with the production SQLite store."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcd_cli.lookup import LookupRequest
    from pcd_cli.models import Project

FORMATS = ("jsonl", "sqlite")
OPERATIONS = ("startup", "open", "exact", "prefix", "fuzzy-selective", "fuzzy-broad")


def build(kind: str, path: Path, projects: Sequence[Project]) -> None:
    if kind == "jsonl":
        from pcd_cli.cache import ProjectCache

        ProjectCache(path).save(projects)
    else:
        from pcd_cli.storage import SQLiteProjectStore

        SQLiteProjectStore(path).save(projects)


def request_for(operation: str, size: int) -> LookupRequest:
    from pcd_cli.lookup import LookupMode, LookupRequest

    if operation == "exact":
        return LookupRequest(f"PROJECT-{size - 1:06d}")
    if operation == "prefix":
        return LookupRequest("PROJECT-000", LookupMode.PREFIX, limit=None)
    if operation == "fuzzy-selective":
        return LookupRequest(f"pjt{(size - 1) % 1000:03d}")
    return LookupRequest("project")


def jsonl_lookup(path: Path, request: LookupRequest) -> list[Project]:
    from pcd_cli.cache import ProjectCache
    from pcd_cli.lookup import LookupMode
    from pcd_cli.search import rank_matches

    cache = ProjectCache(path)
    if request.mode is LookupMode.PREFIX:
        projects = cache.find_prefix(request.query)
        if projects is None:
            raise ValueError("invalid JSONL snapshot")
        return projects
    else:
        projects = cache.find_exact(request.query)
        if projects is not None and len(projects) == 1:
            return projects
        if projects is not None and not projects and request.mode is LookupMode.RESOLVE:
            # The 0.2.8 fuzzy fallback decoded the full catalog on its second pass.
            projects = cache.load()
    if projects is None:
        raise ValueError("invalid JSONL snapshot")
    return rank_matches(projects, request.query)[: request.limit]


def execute(kind: str, path: Path, operation: str, size: int) -> list[Project] | None:
    if kind == "jsonl":
        from pcd_cli.cache import ProjectCache

        if operation == "startup":
            return None
        if operation == "open":
            if ProjectCache(path)._read_header() is None:
                raise ValueError("invalid JSONL header")
            return None
        return jsonl_lookup(path, request_for(operation, size))
    from pcd_cli.storage import SQLiteProjectStore

    if operation == "startup":
        return None
    store = SQLiteProjectStore(path)
    if operation == "open":
        with store._snapshot():
            return None
    result = store.lookup(request_for(operation, size))
    if result is None:
        raise ValueError("invalid SQLite cache")
    return result.matches


def validate_sqlite_prefixes(home: Path) -> None:
    from pcd_cli.lookup import lookup_snapshot, LookupMode, LookupRequest, MemorySnapshot
    from pcd_cli.models import Project, ProjectSource
    from pcd_cli.storage import SQLiteProjectStore

    names = (
        "Straße",
        "STRASSE",
        "STRASSE\U0010ffff",
        "STRASSE\U0010ffff-extra",
        "STRASSE!",
        "other",
    )
    projects = [
        Project(name, home / str(index), home / str(index), ProjectSource.MANUAL)
        for index, name in enumerate(names)
    ]
    path = home / "unicode.sqlite"
    build("sqlite", path, projects)
    store = SQLiteProjectStore(path)
    for query in ("", "STRASSE", "straße\U0010ffff", "straße\U0010ffff-"):
        request = LookupRequest(query, LookupMode.PREFIX, limit=None)
        expected = lookup_snapshot(MemorySnapshot(projects), request)
        actual = store.lookup(request)
        if actual is None or actual.matches != expected.matches or actual.total != expected.total:
            raise ValueError(f"incorrect SQLite prefix results for {query!r}")


def expected_matches(projects: Sequence[Project], request: LookupRequest) -> list[Project]:
    from pcd_cli.lookup import LookupMode
    from pcd_cli.search import rank_matches

    key = request.query.casefold()
    eligible: Sequence[Project]
    if request.mode is LookupMode.PREFIX:
        eligible = [project for project in projects if project.name.casefold().startswith(key)]
        if request.limit is None:
            return sorted(
                eligible, key=lambda project: (project.name.casefold(), str(project.path))
            )
    else:
        exact = [project for project in projects if project.name.casefold() == key]
        eligible = projects if not exact and request.mode is LookupMode.RESOLVE else exact
    return rank_matches(eligible, request.query)[: request.limit]


def compare(sizes: list[int], repeats: int, output: Path) -> None:
    import sqlite3

    from pcd_cli.models import Discovery, Project, ProjectSource

    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="pcd-storage-") as directory:
        home = Path(directory)
        validate_sqlite_prefixes(home)
        for size in sizes:
            projects = [
                Project(
                    f"project-{i:06d}",
                    home / "repos" / str(i),
                    home / "repos" / str(i),
                    ProjectSource.DISCOVERED,
                    (Discovery(home / "repos", home / "repos" / str(i)),),
                )
                for i in range(size)
            ]
            paths: dict[str, Path] = {}
            for kind in FORMATS:
                folder = home / f"{kind}-{size}"
                folder.mkdir()
                path = folder / ("projects.jsonl" if kind == "jsonl" else "projects.sqlite3")
                paths[kind] = path
                start = time.perf_counter()
                build(kind, path, projects)
                elapsed = (time.perf_counter() - start) * 1000
                disk = {file.name: file.stat().st_size for file in folder.iterdir()}
                results.append(
                    {
                        "format": kind,
                        "size": size,
                        "operation": "build",
                        "ms": elapsed,
                        "disk_bytes": sum(disk.values()),
                        "disk_files": disk,
                    }
                )
            for operation in OPERATIONS[2:]:
                request = request_for(operation, size)
                expected = expected_matches(projects, request)
                for kind in FORMATS:
                    if execute(kind, paths[kind], operation, size) != expected:
                        raise ValueError(f"incorrect {kind} results for {operation} at {size}")
            for operation in OPERATIONS:
                samples: dict[str, list[float]] = {kind: [] for kind in FORMATS}
                for iteration in range(repeats + 1):
                    for kind in (
                        FORMATS[iteration % len(FORMATS) :] + FORMATS[: iteration % len(FORMATS)]
                    ):
                        command = [
                            sys.executable,
                            __file__,
                            "--worker",
                            kind,
                            str(paths[kind]),
                            operation,
                            str(size),
                        ]
                        start = time.perf_counter()
                        subprocess.run(command, check=True, capture_output=True, timeout=120)
                        if iteration:
                            samples[kind].append((time.perf_counter() - start) * 1000)
                for kind in FORMATS:
                    memory = {}
                    for mode in ("python", "rss"):
                        observation = subprocess.check_output(
                            [
                                sys.executable,
                                __file__,
                                "--memory",
                                mode,
                                "--worker",
                                kind,
                                str(paths[kind]),
                                operation,
                                str(size),
                            ],
                            text=True,
                            timeout=120,
                        )
                        memory[mode] = int(observation)
                    result = {
                        "format": kind,
                        "size": size,
                        "operation": operation,
                        "samples_ms": samples[kind],
                        "median_ms": statistics.median(samples[kind]),
                        "python_peak_bytes": memory["python"],
                        "rss_peak_bytes": memory["rss"],
                    }
                    results.append(result)
                    print(
                        f"{size:>6} {operation:16} {kind:8}: {result['median_ms']:.1f} ms",
                        flush=True,
                    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "python": sys.version,
                "platform": platform.platform(),
                "sqlite": sqlite3.sqlite_version,
                "sources_sha256": source_hashes(),
                "repeats": repeats,
                "formats": {
                    "jsonl": "0.2.8 lookup algorithm with the legacy cache codec and top-100 output",
                    "sqlite": "production SQLiteProjectStore",
                },
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def source_hashes() -> dict[str, str]:
    modules = ("pcd_cli.cache", "pcd_cli.lookup", "pcd_cli.search", "pcd_cli.storage", "cli_worker")
    paths = [Path(__file__)]
    for name in modules:
        source = importlib.import_module(name).__file__
        if source is None:
            raise ValueError(f"no source file for {name}")
        paths.append(Path(source))
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=4, metavar=("FORMAT", "PATH", "OPERATION", "SIZE"))
    parser.add_argument("--memory", choices=("python", "rss"))
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000, 100000])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/baselines/storage-candidates.json")
    )
    args = parser.parse_args()
    if args.worker:
        kind, path, operation, size = args.worker
        if args.memory == "python":
            tracemalloc.start()
        _result = execute(kind, Path(path), operation, int(size))
        if args.memory == "python":
            print(tracemalloc.get_traced_memory()[1])
        elif args.memory == "rss":
            worker = importlib.import_module("cli_worker")
            print(int(worker.peak_rss()))
        return
    if args.repeats < 1 or any(size < 1 for size in args.sizes):
        parser.error("sizes and repeats must be positive")
    compare(args.sizes, args.repeats, args.output)


if __name__ == "__main__":
    main()
