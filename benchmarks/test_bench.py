import gc
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import TYPE_CHECKING

from pcd_cli.cache import ProjectCache
from pcd_cli.models import Project, ProjectSettings, ProjectSource
from pcd_cli.scanner import ProjectScanner
from pcd_cli.search import rank_matches

if TYPE_CHECKING:
    from collections.abc import Callable


def projects(size: int) -> list[Project]:
    root = Path("/bench")
    return [
        Project(
            name=f"project-{index:06d}",
            path=root / str(index),
            display_path=root / str(index),
            source=ProjectSource.DISCOVERED,
        )
        for index in range(size)
    ]


def elapsed(func: Callable[[], object], repeats: int = 5) -> float:
    best = float("inf")

    for _ in range(repeats):
        start = time.perf_counter()
        func()
        best = min(best, time.perf_counter() - start)

    return best


def test_cache() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cache = ProjectCache(Path(directory) / "projects.jsonl")
        data = projects(100_000)
        cache.save(data)
        exact_time = elapsed(lambda: cache.find_exact("project-099999"), repeats=3)

        gc.collect()
        tracemalloc.start()
        start = time.perf_counter()
        loaded = cache.load()
        load_time = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    assert loaded is not None
    assert len(loaded) == 100_000
    assert exact_time < 1.0
    assert load_time < 5.0
    assert peak < 96 * 1024 * 1024

    print(f"cache exact 100k: {exact_time * 1_000:.2f} ms")
    print(f"cache load  100k: {load_time * 1_000:.2f} ms, {peak / 1024 / 1024:.2f} MiB")


def test_search() -> None:
    data = projects(10_000)
    fuzzy_time = elapsed(lambda: rank_matches(data, "pjt999"))

    assert fuzzy_time < 0.25
    print(f"fuzzy 10k: {fuzzy_time * 1_000:.2f} ms")


def test_memory() -> None:
    data = projects(1_000)
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()

    for _ in range(500):
        rank_matches(data, "project-000999")

    gc.collect()
    after = tracemalloc.take_snapshot()
    growth = sum(
        item.size_diff for item in after.compare_to(before, "lineno") if item.size_diff > 0
    )
    tracemalloc.stop()

    assert growth < 256 * 1024
    print(f"retained after 500 lookups: {growth / 1024:.2f} KiB")


def test_scan() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        for index in range(250):
            path = root / f"group-{index // 25}" / f"project-{index}"
            path.mkdir(parents=True)
            (path / ".git").mkdir()

        settings = ProjectSettings(
            roots=(root,),
            manual_projects=(),
            include_hidden=True,
            follow_symlinks=False,
            excluded_names=frozenset(),
        )
        scanner = ProjectScanner(settings)
        scan_time = elapsed(lambda: list(scanner.scan()), repeats=3)
        found = list(scanner.scan())

    assert len(found) == 250
    assert scan_time < 1.0
    print(f"scan 250 repos: {scan_time * 1_000:.2f} ms")
