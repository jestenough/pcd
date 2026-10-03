from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import pcd_cli.locking as locking

if TYPE_CHECKING:
    from pathlib import Path
    from typing import BinaryIO


def test_file_lock_creates_persistent_sibling(tmp_path: Path) -> None:
    resource = tmp_path / "nested" / "config.toml"
    lock_path = resource.with_name(f".{resource.name}.lock")

    with locking.file_lock(resource):
        assert lock_path.exists()

    assert lock_path.exists()


def test_file_lock_releases_after_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def acquire(_stream: BinaryIO) -> None:
        events.append("acquire")

    def release(_stream: BinaryIO) -> None:
        events.append("release")

    monkeypatch.setattr(locking, "acquire", acquire)
    monkeypatch.setattr(locking, "release", release)

    with pytest.raises(RuntimeError), locking.file_lock(tmp_path / "resource"):
        raise RuntimeError("stop")

    assert events == ["acquire", "release"]
