from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_measure_restores_mutable_fixture_before_each_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    compare_cli = importlib.import_module("benchmarks.compare_cli")
    homes = {label: tmp_path / label for label in ("baseline", "candidate")}
    snapshots: dict[str, dict[Path, bytes]] = {}
    for label, home in homes.items():
        home.mkdir()
        fixture = home / "history.json"
        fixture.write_bytes(b"original")
        snapshots[label] = {fixture: b"original"}
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
        fixture.write_bytes(b"modified")
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
