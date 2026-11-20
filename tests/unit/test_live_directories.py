from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import pcd_cli.live_directories as live
from pcd_cli.lookup import LookupEntry

if TYPE_CHECKING:
    from collections.abc import Iterator


def entries(paths: list[Path]) -> Iterator[LookupEntry[int]]:
    return iter(
        LookupEntry(index, path.name.casefold(), len(path.name), str(path))
        for index, path in enumerate(paths)
    )


def test_dense_checks_follow_live_directory_types_without_stale_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"item-{index:02d}" for index in range(40)]
    for path in paths[:30]:
        path.mkdir()
    for path in paths[30:35]:
        path.touch()
    original = Path.is_dir
    checked: list[Path] = []

    def check(path: Path) -> bool:
        checked.append(path)
        return original(path)

    monkeypatch.setattr(Path, "is_dir", check)
    assert {
        entry.path for entry in live.live_directories(entries(paths), lambda entry: entry.path)
    } == {str(path) for path in paths[:30]}
    assert checked == paths[35:]
    paths[0].rmdir()
    paths[0].touch()
    paths[30].unlink()
    paths[30].mkdir()
    paths[35].mkdir()
    assert {
        entry.path for entry in live.live_directories(entries(paths), lambda entry: entry.path)
    } == {str(path) for path in [*paths[1:31], paths[35]]}


def test_dense_siblings_are_enumerated_once_across_search_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"item-{index:04d}" for index in range(700)]
    for path in paths:
        path.mkdir()
    original = os.scandir
    scanned: list[Path] = []

    @contextmanager
    def scan(path: str) -> Iterator[Iterator[os.DirEntry[str]]]:
        scanned.append(Path(path))
        with original(path) as children:
            yield children

    monkeypatch.setattr(os, "scandir", scan)
    found = live.live_directories(entries(paths), lambda entry: entry.path)
    assert [entry.path for entry in found] == [str(path) for path in paths]
    assert scanned == [tmp_path]


@pytest.mark.parametrize("error", [PermissionError, FileNotFoundError, NotImplementedError])
def test_failed_enumeration_falls_back_to_individual_live_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    paths = [tmp_path / f"item-{index:03d}" for index in range(600)]
    paths[0].mkdir()
    calls = 0

    def scan(_path: str) -> Iterator[os.DirEntry[str]]:
        nonlocal calls
        calls += 1
        raise error("unavailable")

    monkeypatch.setattr(os, "scandir", scan)
    assert [
        entry.path for entry in live.live_directories(entries(paths), lambda entry: entry.path)
    ] == [str(paths[0])]
    assert calls == 1


def test_huge_parent_enumeration_is_capped_and_still_checks_every_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(300):
        (tmp_path / f"noise-{index:03d}").touch()
    paths = [tmp_path / f"target-{index:03d}" for index in range(20)]
    for path in paths:
        path.mkdir()
    original = os.scandir
    visited = 0

    @contextmanager
    def scan(path: str) -> Iterator[Iterator[os.DirEntry[str]]]:
        with original(path) as children:
            ordered = sorted(children, key=lambda entry: entry.name)

        def counted() -> Iterator[os.DirEntry[str]]:
            nonlocal visited
            for child in ordered:
                visited += 1
                yield child

        yield counted()

    monkeypatch.setattr(os, "scandir", scan)
    assert [
        entry.path for entry in live.live_directories(entries(paths), lambda entry: entry.path)
    ] == [str(path) for path in paths]
    assert visited == 257


def test_sparse_candidates_do_not_enumerate_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"parent-{index}" / "item" for index in range(20)]
    paths[0].mkdir(parents=True)

    def scan(_path: str) -> Iterator[os.DirEntry[str]]:
        raise AssertionError("Sparse candidates must retain individual checks")

    monkeypatch.setattr(os, "scandir", scan)
    assert [
        entry.path for entry in live.live_directories(entries(paths), lambda entry: entry.path)
    ] == [str(paths[0])]


def test_missing_enumerated_spelling_uses_original_path_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"item-{index:03d}" for index in range(20)]
    for path in paths:
        path.mkdir()
    original = os.scandir

    @contextmanager
    def scan(path: str) -> Iterator[Iterator[os.DirEntry[str]]]:
        with original(path) as children:
            # A lookup spelling can differ from enumeration on a case-folding
            # or Unicode-normalizing filesystem; never infer absence from it.
            yield (child for child in children if child.name != paths[0].name)

    monkeypatch.setattr(os, "scandir", scan)
    assert [
        entry.path for entry in live.live_directories(entries(paths), lambda entry: entry.path)
    ] == [str(path) for path in paths]


def test_dense_checks_follow_directory_symlinks_and_reject_broken_targets(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    folder = tmp_path / "siblings"
    folder.mkdir()
    paths = [folder / f"item-{index:03d}" for index in range(20)]
    for path in paths[:18]:
        path.mkdir()
    try:
        paths[18].symlink_to(target, target_is_directory=True)
        paths[19].symlink_to(tmp_path / "missing", target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable")
    assert [
        entry.path for entry in live.live_directories(entries(paths), lambda entry: entry.path)
    ] == [str(path) for path in paths[:19]]
