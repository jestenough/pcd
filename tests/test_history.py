import json
from pathlib import Path

import pytest

from pcd_cli.history import UsageHistory


def test_history_touch_increments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter((10.0, 20.0))
    monkeypatch.setattr("pcd_cli.history.time.time", lambda: next(times))

    history = UsageHistory(tmp_path / "history.json")
    path = tmp_path / "repo"

    history.record(path)
    history.record(path)

    item = history.load()[path]
    assert item.count == 2
    assert item.used_at == 20.0


def test_history_corruption_is_ignored(tmp_path: Path) -> None:
    history = UsageHistory(tmp_path / "history.json")
    history.path.write_text("not-json", encoding="utf-8")

    assert history.load() == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"/x": 1},
        {"/x": {"used_at": True, "count": 1}},
        {"/x": {"used_at": 1, "count": 0}},
        {"/x": {"used_at": "x", "count": 1}},
    ],
)
def test_history_rejects_invalid_rows(tmp_path: Path, payload: object) -> None:
    history = UsageHistory(tmp_path / "history.json")
    history.path.write_text(json.dumps(payload), encoding="utf-8")

    assert history.load() == {}


def test_history_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history = UsageHistory(tmp_path / "history.json", limit=2)
    times = iter((1.0, 2.0, 3.0))
    monkeypatch.setattr("pcd_cli.history.time.time", lambda: next(times))

    history.record(Path("/one"))
    history.record(Path("/two"))
    history.record(Path("/three"))

    assert set(history.load()) == {Path("/two"), Path("/three")}


def test_history_trims_existing_data_to_new_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = UsageHistory(tmp_path / "history.json", limit=2)
    history.path.write_text(
        json.dumps(
            {
                "/one": {"used_at": 1.0, "count": 1},
                "/two": {"used_at": 2.0, "count": 1},
                "/three": {"used_at": 3.0, "count": 1},
                "/four": {"used_at": 4.0, "count": 1},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("pcd_cli.history.time.time", lambda: 5.0)

    history.record(Path("/five"))

    assert set(history.load()) == {Path("/four"), Path("/five")}
