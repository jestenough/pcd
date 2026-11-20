"""Profile and compare first/next/previous windows on one real Windows fixture.

The fixture is seeded directly with the same path|rank inputs used by the zoxide
import benchmark. Import itself and the interactive picker are outside this test.
Source snapshots and runtime should reside on the same local filesystem.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import platform
import pstats
import statistics
import subprocess
import sys
import time
from pathlib import Path


def source_hash(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*.py")):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--query", default="service")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--repeats", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 0:
        parser.error("--repeats must be positive, or zero for a single lookup")
    if args.reference and not args.validate:
        parser.error("--reference requires --validate")
    if args.repeats:
        samples: list[float] = []
        body_samples: list[float] = []
        for repetition in range(-2, args.repeats):
            started = time.perf_counter()
            completed = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--source",
                    str(args.source),
                    "--root",
                    str(args.root),
                    "--query",
                    args.query,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            elapsed = (time.perf_counter() - started) * 1000
            result = json.loads(completed.stdout)
            if result["total"] != 99_997 or result["window"] != 100:
                raise RuntimeError(f"Unexpected first window: {result}")
            if repetition >= 0:
                samples.append(elapsed)
                body_samples.append(result["lookup_ms"])
        report = {
            "source_sha256": source_hash(args.source / "pcd_cli"),
            "source": str(args.source),
            "fixture": str(args.root),
            "query": args.query,
            "python": sys.version,
            "platform": platform.platform(),
            "total": 99_997,
            "window": 100,
            "samples_ms": samples,
            "lookup_samples_ms": body_samples,
            "median_ms": statistics.median(samples),
            "p90_ms": sorted(samples)[math.ceil(len(samples) * 0.9) - 1],
            "lookup_median_ms": statistics.median(body_samples),
            "repeats": args.repeats,
            "warmups": 2,
            "methodology": "Fresh Python process on C: with source/dependencies copied to C:. "
            "Includes launch, imports, initial resolution and process exit; lookup_ms separately "
            "times ProjectCatalog.resolve. Same 100k existing Windows directories and SQLite "
            "visits seeded directly with synthetic import scores 1+index%97; two manual projects. "
            "No zoxide import is timed. First 100 only; no picker rendering. Warm filesystem "
            "caches, uncontrolled antivirus. p90 nearest rank.",
        }
        rendered = json.dumps(report, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered)
        return
    sys.path.insert(0, str(args.source))

    from pcd_cli.catalog import ProjectCatalog
    from pcd_cli.config import Config
    from pcd_cli.history import UsageHistory
    from pcd_cli.lookup import LookupRequest
    from pcd_cli.models import Project, ProjectSource
    from pcd_cli.storage import SQLiteProjectStore
    from pcd_cli.visits import DirectoryVisits

    root: Path = args.root
    catalog = ProjectCatalog(
        Config(root / "config.toml"),
        SQLiteProjectStore(root / "projects.sqlite3"),
        UsageHistory(root / "directories.sqlite3"),
    )
    if args.prepare:
        root.mkdir(parents=True, exist_ok=True)
        scores: dict[Path, float] = {}
        for index in range(100_000):
            name = f"service-{index:06d}"
            if index == 99_997:
                name = "quartz-xylophone-violet-73142"
            elif index == 99_998:
                name = "navigation-alpha"
            elif index == 99_999:
                name = "navigation-beta"
            path = root / "directories" / f"team-{index // 1000:03d}" / name
            path.mkdir(parents=True, exist_ok=True)
            scores[path] = float(1 + index % 97)
        DirectoryVisits(catalog.history.path).merge(scores)
        catalog.cache.save(
            Project(path.name, path, path, ProjectSource.MANUAL) for path in tuple(scores)[-2:]
        )
        print(json.dumps({"prepared": len(scores), "all_destinations_exist": True}))
        return

    # Profile a single operation separately from ordinary wall-clock samples.
    profile = cProfile.Profile() if args.profile else None
    started = time.perf_counter()
    if profile:
        profile.enable()
    first = catalog.resolve(args.query)
    if profile:
        profile.disable()
    elapsed = time.perf_counter() - started
    output: dict[str, object] = {
        "lookup_ms": elapsed * 1000,
        "total": first.total,
        "window": len(first.matches),
        "offset": first.offset,
    }
    if args.validate:
        second = catalog.lookup(
            LookupRequest(args.query, after=first.last_rank), include_directories=True
        )
        previous = catalog.lookup(
            LookupRequest(args.query, before=second.first_rank), include_directories=True
        )
        output.update(
            source_sha256=source_hash(args.source / "pcd_cli"),
            query=args.query,
            first=[str(project.path) for project in first.matches],
            next=[str(project.path) for project in second.matches],
            previous=[str(project.path) for project in previous.matches],
            totals=[first.total, second.total, previous.total],
            offsets=[first.offset, second.offset, previous.offset],
            ranks=[
                list(rank) if rank is not None else None
                for rank in (first.first_rank, first.last_rank, second.first_rank, second.last_rank)
            ],
        )
        if output["first"] != output["previous"]:
            raise RuntimeError("Previous page does not recover the complete first page")
        if args.reference:
            reference = json.loads(args.reference.read_text(encoding="utf-8"))
            compared = ("query", "first", "next", "previous", "totals", "offsets", "ranks")
            for key in compared:
                if output[key] != reference[key]:
                    raise RuntimeError(f"Ordered-window reference mismatch: {key}")
            output["reference"] = str(args.reference)
            output["reference_source_sha256"] = reference["source_sha256"]
            output["reference_matches"] = True
    if profile:
        # pstats exposes its tuple-keyed statistics as an implementation detail;
        # its textual report is stable and keeps every call count and timer.
        import io

        output.update(
            source_sha256=source_hash(args.source / "pcd_cli"),
            source=str(args.source),
            fixture=str(root),
            query=args.query,
        )
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(65)
        output["profile_cumulative"] = stream.getvalue()
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("tottime").print_stats(45)
        output["profile_self"] = stream.getvalue()
    rendered = json.dumps(output, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
