from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pcd_cli.cli import cli
from pcd_cli.config import Config
from pcd_cli.environment import Platform
from pcd_cli.filesystem import ApplicationPaths
from pcd_cli.integrations.shell import ConfiguredShell, Shell, ShellIntegrationError
from pcd_cli.models import Project, ProjectSource
from pcd_cli.storage import SQLiteProjectStore

if TYPE_CHECKING:
    from click.testing import CliRunner


@pytest.fixture(autouse=True)
def doctor_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inspect a Unix startup file in the isolated home, never real registry state."""
    integration = ConfiguredShell.for_path(Shell.BASH, Path.home() / ".bashrc", Platform.LINUX)
    integration.config_path.write_text('eval "$(command pcd shell init bash)"\n', encoding="utf-8")
    monkeypatch.setattr(ConfiguredShell, "detect", lambda: integration)
    monkeypatch.setattr("pcd_cli.diagnostics.shutil.which", lambda _name: "/test/bin/pcd")


def healthy_files() -> ApplicationPaths:
    paths = ApplicationPaths.resolve()
    Config(paths.config).ensure_exists()
    SQLiteProjectStore(paths.cache).save(())
    return paths


def snapshot(directory: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def test_doctor_healthy_is_read_only(runner: CliRunner, tmp_path: Path) -> None:
    paths = healthy_files()
    before = snapshot(tmp_path)

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "integrity verified" in result.output
    assert "0 issue(s); no changes made" in result.output
    assert snapshot(tmp_path) == before
    assert not paths.history.exists()


def test_doctor_absent_files_creates_nothing(runner: CliRunner, tmp_path: Path) -> None:
    before = snapshot(tmp_path)
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Config: Not found" in result.output
    assert "ISSUE: Cache: Not found" in result.output
    assert "OK: PATH:" in result.output
    assert "OK: Shell:" in result.output
    assert snapshot(tmp_path) == before


def test_doctor_aggregates_invalid_config_cache_and_path(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = healthy_files()
    paths.config.write_text("roots = [", encoding="utf-8")
    paths.cache.write_bytes(b"broken database")
    monkeypatch.setattr("pcd_cli.diagnostics.shutil.which", lambda _name: None)

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Config: Invalid TOML" in result.output
    assert "ISSUE: Cache:" in result.output
    assert "ISSUE: PATH: pcd executable not found" in result.output
    assert "Doctor found 3 issue(s)" in result.output


def test_doctor_reports_payload_corruption(runner: CliRunner, tmp_path: Path) -> None:
    paths = healthy_files()
    store = SQLiteProjectStore(paths.cache)
    store.save([Project("repo", tmp_path, tmp_path, ProjectSource.MANUAL)])
    with closing(sqlite3.connect(paths.cache)) as connection, connection:
        connection.execute("UPDATE projects SET payload_crc = 0")

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "Invalid project payload checksum" in result.output


def test_doctor_reports_missing_manual_project_even_outside_cache(
    runner: CliRunner, tmp_path: Path
) -> None:
    paths = healthy_files()
    missing = tmp_path / "missing-manual"
    Config(paths.config).add_project(Project("manual", missing, missing, ProjectSource.MANUAL))

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Project: manual: Missing directory:" in result.output
    assert str(missing) in result.output
    assert Config(paths.config).load().manual_projects[0].path == missing


def test_doctor_reports_missing_roots_exclusions_and_cached_projects(
    runner: CliRunner, tmp_path: Path
) -> None:
    paths = healthy_files()
    config = Config(paths.config)
    config.add_root(tmp_path / "missing-root")
    config.exclude_project(tmp_path / "missing-excluded")
    missing = tmp_path / "missing-cached"
    SQLiteProjectStore(paths.cache).save(
        [Project("cached", missing, missing, ProjectSource.DISCOVERED)]
    )

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Root: Missing directory:" in result.output
    assert "ISSUE: Exclusion: Missing directory:" in result.output
    assert "ISSUE: Project: cached: Missing directory:" in result.output


def test_doctor_reports_config_read_error_and_continues(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy_files()

    def unreadable(_config: Config) -> None:
        raise PermissionError("config access denied")

    monkeypatch.setattr(Config, "load", unreadable)
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "config access denied" in result.output
    assert "OK: Cache:" in result.output
    assert "OK: Shell:" in result.output


def test_doctor_shell_detection_is_explicitly_unknown(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy_files()

    def unknown() -> ConfiguredShell:
        raise ShellIntegrationError("no supported parent shell")

    monkeypatch.setattr(ConfiguredShell, "detect", unknown)
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "UNKNOWN: Shell: Cannot detect shell integration" in result.output
    assert "0 issue(s)" in result.output


def test_doctor_detects_missing_shell_integration(runner: CliRunner) -> None:
    healthy_files()
    (Path.home() / ".bashrc").unlink()

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Shell: bash: not installed" in result.output


def test_doctor_legacy_only_cache_is_not_migrated(runner: CliRunner, tmp_path: Path) -> None:
    paths = ApplicationPaths.resolve()
    Config(paths.config).ensure_exists()
    legacy = paths.cache.with_name("projects.jsonl")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("legacy fixture", encoding="utf-8")
    before = snapshot(tmp_path)

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "legacy cache" in result.output
    assert "run pcd refresh" in result.output
    assert snapshot(tmp_path) == before


def test_doctor_rejects_wal_without_creating_sidecars(runner: CliRunner, tmp_path: Path) -> None:
    paths = healthy_files()
    with closing(sqlite3.connect(paths.cache)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    before = snapshot(tmp_path)

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "unsupported WAL" in result.output
    assert snapshot(tmp_path) == before


def test_doctor_escapes_terminal_controls(runner: CliRunner, tmp_path: Path) -> None:
    paths = healthy_files()
    missing = tmp_path / "missing"
    Config(paths.config).add_project(
        Project("bad\x1b[31mname", missing, missing, ProjectSource.MANUAL)
    )

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "\x1b" not in result.output
    assert "bad\\x1b[31mname" in result.output


def test_doctor_reports_cached_path_replaced_by_file(runner: CliRunner, tmp_path: Path) -> None:
    paths = healthy_files()
    replaced = tmp_path / "former-project"
    replaced.write_text("file", encoding="utf-8")
    SQLiteProjectStore(paths.cache).save(
        [Project("former", replaced, replaced, ProjectSource.MANUAL)]
    )

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Project: former: Not a directory:" in result.output
    assert "OK: Projects:" not in result.output


def test_doctor_directory_access_error(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = healthy_files()
    blocked = tmp_path / "blocked"
    Config(paths.config).add_root(blocked)
    real_stat = Path.stat

    def denied(path: Path, *, follow_symlinks: bool = True) -> object:
        if path == blocked:
            raise PermissionError("root access denied")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied)
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "root access denied" in result.output
    assert "OK: Roots:" not in result.output


@pytest.mark.parametrize("operation", ["detect", "state", "installation_issue"])
def test_doctor_shell_read_errors(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    healthy_files()

    def denied(*_args: object) -> None:
        raise PermissionError("shell access denied")

    monkeypatch.setattr(ConfiguredShell, operation, denied)
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Shell: Cannot inspect shell integration: shell access denied" in result.output


def test_doctor_shell_policy_issue(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    healthy_files()
    monkeypatch.setattr(ConfiguredShell, "installation_issue", lambda _self: "Policy is Restricted")

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Shell: Policy is Restricted" in result.output


def test_doctor_malformed_shell_markers(runner: CliRunner) -> None:
    healthy_files()
    (Path.home() / ".bashrc").write_text("# >>> pcd shell integration >>>\n", encoding="utf-8")

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "ISSUE: Shell: Cannot inspect shell integration:" in result.output


def test_doctor_valid_directory_references(runner: CliRunner, tmp_path: Path) -> None:
    paths = healthy_files()
    config = Config(paths.config)
    config.add_root(tmp_path)
    config.exclude_project(Path.home())
    project = Project("test", tmp_path, tmp_path, ProjectSource.MANUAL)
    config.add_project(project)
    SQLiteProjectStore(paths.cache).save([project])

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "OK: Roots: Checked 1 root(s)" in result.output
    assert "OK: Projects: Checked 1 project reference(s)" in result.output


def test_doctor_reports_stale_cache_references(runner: CliRunner, tmp_path: Path) -> None:
    paths = healthy_files()
    directories = [tmp_path / name for name in ("excluded", "absent", "renamed", "orphan")]
    for directory in directories:
        directory.mkdir()
    excluded, absent, renamed, orphan = directories
    config = Config(paths.config)
    config.exclude_project(excluded)
    config.add_project(Project("absent", absent, absent, ProjectSource.MANUAL))
    config.add_project(Project("new-name", renamed, renamed, ProjectSource.MANUAL))
    SQLiteProjectStore(paths.cache).save(
        [
            Project("excluded", excluded, excluded, ProjectSource.DISCOVERED),
            Project("old-name", renamed, renamed, ProjectSource.MANUAL),
            Project("orphan", orphan, orphan, ProjectSource.MANUAL),
        ]
    )
    before = snapshot(tmp_path)

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "Excluded project still cached:" in result.output
    assert "Manual project absent absent from cache:" in result.output
    assert "Manual project new-name mismatches cache:" in result.output
    assert "Cached manual project no longer configured:" in result.output
    assert "Doctor found 4 issue(s)" in result.output
    assert snapshot(tmp_path) == before


def test_doctor_reads_committed_snapshot_during_refresh(runner: CliRunner) -> None:
    paths = healthy_files()
    with closing(sqlite3.connect(paths.cache)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE metadata SET project_count = 100")

        result = runner.invoke(cli, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "0 project(s); integrity verified" in result.output
        connection.rollback()


def test_doctor_integrity_check_failure(runner: CliRunner) -> None:
    paths = healthy_files()
    with closing(sqlite3.connect(paths.cache)) as connection, connection:
        connection.execute("CREATE TABLE invalid (value INTEGER)")
        connection.execute("INSERT INTO invalid VALUES (NULL)")
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql='CREATE TABLE invalid(value INTEGER NOT NULL)' "
            "WHERE name='invalid'"
        )

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "SQLite integrity check failed" in result.output
