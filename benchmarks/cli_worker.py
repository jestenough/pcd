"""Fresh-process benchmark worker; source version is selected through PYTHONPATH."""

from __future__ import annotations

import json
import os
import sys
import time
import tracemalloc
from pathlib import Path


def peak_rss() -> int:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("faults", wintypes.DWORD),
                ("peak", ctypes.c_size_t),
                ("working", ctypes.c_size_t),
                ("paged_peak", ctypes.c_size_t),
                ("paged", ctypes.c_size_t),
                ("nonpaged_peak", ctypes.c_size_t),
                ("nonpaged", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t),
                ("pagefile_peak", ctypes.c_size_t),
            ]

        counters = Counters()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        if not psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), ctypes.sizeof(counters)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.peak)

    else:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)


def prepare(size: int, history_size: int) -> None:
    from pcd_cli.catalog import ProjectCatalog
    from pcd_cli.models import Project, ProjectSource

    root = Path.cwd() / "repos"
    root.mkdir(exist_ok=True)
    for index in (0, max(size - 1, 0)):
        (root / f"project-{index:06d}").mkdir(exist_ok=True)
    catalog = ProjectCatalog.create()
    start = time.perf_counter()
    catalog.cache.save(
        Project(
            f"project-{i:06d}",
            root / f"project-{i:06d}",
            root / f"project-{i:06d}",
            ProjectSource.MANUAL,
        )
        for i in range(size)
    )
    elapsed = time.perf_counter() - start
    catalog.history.path.parent.mkdir(parents=True, exist_ok=True)
    catalog.history.path.write_text(
        json.dumps(
            {
                str(root / f"project-{i:06d}"): {"used_at": i + 1, "count": 1}
                for i in range(max(0, size - history_size), size)
            }
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "save_ms": elapsed * 1000,
                "history_path": str(catalog.history.path),
                "cache_path": str(catalog.cache.path),
                "config_path": str(catalog.config.path),
                "cache_bytes": sum(
                    path.stat().st_size
                    for path in catalog.cache.path.parent.glob("projects.jsonl*")
                ),
            }
        )
    )


def prepare_tree(size: int, shape: str) -> None:
    from pcd_cli.catalog import ProjectCatalog

    root = Path.cwd() / "tree"
    for index in range(size):
        group = root if shape == "wide" else root / f"group-{index // 25:04d}"
        (group / f"repo-{index:06d}" / ".git").mkdir(parents=True)
    if size == 0:
        root.mkdir()
    catalog = ProjectCatalog.create()
    if not catalog.config.add_root(root) or len(catalog.refresh()) != size:
        raise RuntimeError("Tree fixture does not match requested size")
    target = Path.cwd() / "add-target"
    target.mkdir()
    catalog.history.path.parent.mkdir(parents=True, exist_ok=True)
    catalog.history.path.write_text("{}", encoding="utf-8")
    print(
        json.dumps(
            {
                "config_path": str(catalog.config.path),
                "cache_path": str(catalog.cache.path),
                "history_path": str(catalog.history.path),
                "target": str(target),
                "projects": size,
            }
        )
    )


def run_history(action: str) -> None:
    from pcd_cli.catalog import ProjectCatalog

    catalog = ProjectCatalog.create()
    if action == "load":
        print(len(catalog.history.load()))
    else:
        catalog.history.record(Path.cwd() / "repos" / "project-000000")


def run_cli() -> None:
    from pcd_cli.cli import cli

    if os.environ.get("PCD_BENCH_PICKER") == "1":
        from pcd_cli.picker import ProjectPicker

        # Exercise the same initial ranking as run(), without waiting for a human.
        def select(self: ProjectPicker) -> None:
            self._matching_projects(self.query)
            return None

        ProjectPicker.run = select  # type: ignore[method-assign]
    cli.main(prog_name="pcd")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "prepare":
            prepare(int(sys.argv[2]), int(sys.argv[3]))
        elif sys.argv[1] == "prepare-tree":
            prepare_tree(int(sys.argv[2]), sys.argv[3])
        elif sys.argv[1] == "history":
            run_history(sys.argv[2])
        else:
            del sys.argv[1]
            run_cli()
    finally:
        if tracemalloc.is_tracing() or os.environ.get("PCD_BENCH_MEMORY") == "1":
            peak = tracemalloc.get_traced_memory()[1] if tracemalloc.is_tracing() else 0
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            print(
                json.dumps({"python_peak_bytes": peak, "rss_peak_bytes": peak_rss()}),
                file=sys.stderr,
            )
