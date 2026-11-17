"""Profile core operations against isolated fixtures, optionally from another checkout."""

from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import io
import json
import platform
import pstats
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def operations(home: Path, size: int, root_count: int) -> dict[str, Callable[[], object]]:
    # Import after selecting the source tree so both revisions use identical fixtures.
    from pcd_cli.cache import ProjectCache
    from pcd_cli.cli.roots import RootListTable
    from pcd_cli.config import Config
    from pcd_cli.history import UsageHistory
    from pcd_cli.models import Discovery, Project, ProjectSettings, ProjectSource
    from pcd_cli.scanner import ProjectScanner
    from pcd_cli.search import rank_matches

    root = home / "repos"
    roots = [root / f"group-{index}" for index in range(root_count)]
    for path in roots:
        path.mkdir(parents=True)
    data = [
        Project(
            name=f"project-{index:06d}",
            path=roots[index % root_count] / f"project-{index:06d}",
            display_path=roots[index % root_count] / f"project-{index:06d}",
            source=ProjectSource.DISCOVERED,
            discoveries=(Discovery(root, roots[index % root_count] / f"project-{index:06d}"),),
        )
        for index in range(size)
    ]
    settings = ProjectSettings((root, *roots), (), True, False, frozenset())
    scanner = ProjectScanner(settings)
    all_roots = scanner._all_roots()
    selected = scanner._scan_roots(all_roots)

    def project_discoveries() -> dict[Path, Project]:
        projects = {project.path: project for project in data}
        scanner._add_nested_discoveries(projects, all_roots, selected)
        return projects

    counted = list(project_discoveries().values())
    cache = ProjectCache(home / "projects.jsonl")
    cache.save(counted)
    config = Config(home / "config.toml")
    config.path.write_text(json_config(root), encoding="utf-8")
    history = UsageHistory(home / "history.json")
    history.path.write_text(
        json.dumps(
            {
                str(item.path): {"used_at": index, "count": 1}
                for index, item in enumerate(data[:2048])
            }
        ),
        encoding="utf-8",
    )
    # Scanning gets real filesystem fixtures; the larger search set stays synthetic.
    for project in data[:250]:
        (project.path / ".git").mkdir(parents=True)

    return {
        "roots": lambda: RootListTable(settings.roots, counted).render(),
        "nested_discoveries": project_discoveries,
        "cache_save": lambda: cache.save(counted),
        "cache_load": cache.load,
        "cache_exact": lambda: cache.find_exact(data[-1].name),
        "cache_prefix": lambda: cache.find_prefix("project-000"),
        "search_broad": lambda: rank_matches(data, "project"),
        "search_fuzzy": lambda: rank_matches(data, "pjt999"),
        "search_miss": lambda: rank_matches(data, "zzzz"),
        "history_load": history.load,
        "history_record": lambda: history.record(data[-1].path),
        "config_load": config.load,
        "scan": lambda: list(scanner.scan()),
    }


def json_config(root: Path) -> str:
    # JSON string escaping is also valid for a TOML basic string.
    return f"roots = [{json.dumps(str(root))}]\n"


def measure(operation: Callable[[], object], repeats: int) -> dict[str, object]:
    operation()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000)

    profiler = cProfile.Profile()
    profiler.runcall(operation)
    report = io.StringIO()
    pstats.Stats(profiler, stream=report).strip_dirs().sort_stats("cumulative").print_stats(15)
    gc.collect()
    tracemalloc.start()
    try:
        operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "samples_ms": samples,
        "median_ms": statistics.median(samples),
        "python_peak_bytes": peak,
        "profile": report.getvalue(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--roots", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--operations", nargs="+", help="Only profile the named operations")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.size, args.roots, args.repeats) < 1:
        parser.error("size, roots and repeats must be positive")
    source = args.source.resolve() / "src"
    if not (source / "pcd_cli").is_dir():
        parser.error("source must contain src/pcd_cli")
    sys.path.insert(0, str(source))
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*.py")):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    results = {}
    with tempfile.TemporaryDirectory(prefix="pcd-profile-") as directory:
        cases = operations(Path(directory), args.size, args.roots)
        selected = args.operations or list(cases)
        if unknown := set(selected) - cases.keys():
            parser.error(f"unknown operations: {', '.join(sorted(unknown))}")
        for name in selected:
            operation = cases[name]
            results[name] = measure(operation, args.repeats)
            print(f"{name}: {results[name]['median_ms']:.2f} ms", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "python": sys.version,
                "platform": platform.platform(),
                "source_sha256": digest.hexdigest(),
                "size": args.size,
                "roots": args.roots,
                "operations": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
