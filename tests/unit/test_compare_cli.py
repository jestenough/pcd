from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("inherited_overrides", [False, True])
def test_environment_isolates_application_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited_overrides: bool
) -> None:
    source = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(source))
    compare_cli = importlib.import_module("benchmarks.compare_cli")
    for key in ("WIN_PD_OVERRIDE_APPDATA", "WIN_PD_OVERRIDE_LOCAL_APPDATA"):
        if inherited_overrides:
            monkeypatch.setenv(key, str(tmp_path / "caller-data"))
        else:
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "benchmark-home"
    home.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from pathlib import Path; "
            "from pcd_cli.filesystem import ApplicationPaths; "
            "paths = ApplicationPaths.resolve(); "
            "print(json.dumps([str(path) for path in "
            "(Path.home(), paths.config, paths.cache, paths.history)]))",
        ],
        cwd=home,
        env=compare_cli.environment(source, home),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    paths = [Path(value) for value in json.loads(result.stdout)]
    assert all(path.is_relative_to(home) for path in paths), paths


def test_measure_restores_mutable_fixture_before_each_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    compare_cli = importlib.import_module("benchmarks.compare_cli")
    homes = {label: tmp_path / label for label in ("baseline", "candidate")}
    snapshots: dict[str, dict[Path, bytes | None]] = {}
    for label, home in homes.items():
        home.mkdir()
        fixture = home / "history.json"
        fixture.write_bytes(b"original")
        cache = home / "projects.jsonl"
        cache.write_bytes(b"cached")
        snapshots[label] = {
            fixture: b"original",
            cache: b"cached",
            home / "config.toml": None,
        }
    calls: list[tuple[str, bool, bool]] = []

    def run(
        _operation: object,
        _env: dict[str, str],
        home: Path,
        *,
        memory: bool = False,
        trace: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        fixture = home / "history.json"
        assert fixture.read_bytes() == b"original"
        assert (home / "projects.jsonl").read_bytes() == b"cached"
        assert not (home / "config.toml").exists()
        fixture.write_bytes(b"modified")
        (home / "projects.jsonl").write_bytes(b"refreshed")
        (home / "config.toml").write_bytes(b"created")
        calls.append((home.name, memory, trace))
        return subprocess.CompletedProcess(
            args=[],
            returncode=3,
            stdout="",
            stderr=json.dumps({"rss_peak_bytes": 1024, "python_peak_bytes": 512}),
        )

    monkeypatch.setattr(compare_cli, "run_operation", run)
    results = compare_cli.measure(
        compare_cli.Operation("exact-miss", ("missing",), 3),
        homes,
        {label: {} for label in homes},
        snapshots,
        2,
        size=5,
        history=0,
        shape="wide",
    )

    assert calls == [
        ("candidate", False, False),
        ("baseline", False, False),
        ("baseline", False, False),
        ("candidate", False, False),
        ("candidate", False, False),
        ("baseline", False, False),
        ("baseline", True, False),
        ("baseline", False, True),
        ("candidate", True, False),
        ("candidate", False, True),
    ]
    assert {item["version"] for item in results} == {"baseline", "candidate"}
    assert all(len(item["samples_ms"]) == 2 for item in results)
    assert all(
        item["memory"] == {"rss_peak_bytes": 1024, "python_peak_bytes": 512} for item in results
    )
