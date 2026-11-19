from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

import tomlkit

from pcd_cli.filesystem import atomic_write, canonical_path, file_lock
from pcd_cli.models import Project, ProjectSettings, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tomlkit import TOMLDocument

ROOT_KEYS = frozenset({"roots", "scan", "projects", "editor", "excluded_projects"})
SCAN_KEYS = frozenset({"hidden", "follow_symlinks", "exclude"})
PROJECT_KEYS = frozenset({"name", "path"})

DEFAULT_EXCLUDED_NAMES = frozenset(
    {
        ".cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
        "venv",
    }
)


class InvalidConfigError(RuntimeError):
    """The configuration exists but cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class Config:
    path: Path

    def load(self) -> ProjectSettings:
        return _decode_settings(self._read().unwrap())

    def effective_toml(self) -> str:
        settings = self.load()
        document = _default_document()
        document["roots"] = [str(item) for item in settings.roots]
        document["excluded_projects"] = sorted(str(item) for item in settings.excluded_projects)
        document["scan"]["hidden"] = settings.include_hidden
        document["scan"]["follow_symlinks"] = settings.follow_symlinks
        document["scan"]["exclude"] = sorted(settings.excluded_names)
        if settings.editor is not None:
            document["editor"] = settings.editor
        document["projects"] = [
            {"name": item.name, "path": str(item.display_path)} for item in settings.manual_projects
        ]
        return tomlkit.dumps(document)

    def ensure_exists(self) -> None:
        with file_lock(self.path):
            try:
                self.path.stat()
            except FileNotFoundError:
                self._write(_default_document())

    def add_root(self, root: Path) -> bool:
        display_path = root.expanduser()
        target = canonical_path(display_path)

        with file_lock(self.path):
            document = self._read()
            settings = _decode_settings(document.unwrap())
            if any(canonical_path(item) == target for item in settings.roots):
                return False

            document["roots"] = [str(item) for item in (*settings.roots, display_path)]
            self._write(document)
            return True

    def remove_root(self, root: Path) -> bool:
        target = canonical_path(root)

        with file_lock(self.path):
            document = self._read()
            settings = _decode_settings(document.unwrap())
            roots = tuple(item for item in settings.roots if canonical_path(item) != target)
            if len(roots) == len(settings.roots):
                return False

            document["roots"] = [str(item) for item in roots]
            self._write(document)
            return True

    def add_project(self, project: Project) -> bool:
        return self.add_manual(project) is not None

    def add_manual(self, project: Project) -> ProjectSettings | None:
        """Register a manual project and return the committed settings."""
        if project.source is not ProjectSource.MANUAL:
            raise ValueError("Only manual projects belong in the config")

        with file_lock(self.path):
            document = self._read()
            settings = _decode_settings(document.unwrap())
            if any(item.path == project.path for item in settings.manual_projects):
                return None

            document["projects"] = [
                {"name": item.name, "path": str(item.display_path)}
                for item in (*settings.manual_projects, project)
            ]
            if project.path in settings.excluded_projects:
                document["excluded_projects"] = sorted(
                    str(item) for item in settings.excluded_projects - {project.path}
                )
            self._write(document)

            return _decode_settings(document.unwrap())

    def remove_project(self, path: Path) -> bool:
        target = canonical_path(path)

        with file_lock(self.path):
            document = self._read()
            settings = _decode_settings(document.unwrap())
            projects = tuple(item for item in settings.manual_projects if item.path != target)
            if len(projects) == len(settings.manual_projects):
                return False

            document["projects"] = [
                {"name": item.name, "path": str(item.display_path)} for item in projects
            ]
            self._write(document)
            return True

    def exclude_project(self, path: Path) -> bool:
        """Persist a canonical discovery exclusion without touching the directory."""
        target = canonical_path(path)
        with file_lock(self.path):
            document = self._read()
            settings = _decode_settings(document.unwrap())
            if target in settings.excluded_projects or any(
                item.path == target for item in settings.manual_projects
            ):
                return False
            document["excluded_projects"] = sorted(
                str(item) for item in settings.excluded_projects | {target}
            )
            self._write(document)
            return True

    def restore_project(
        self, project: Project, *, manual: bool, expected: ProjectSettings
    ) -> ProjectSettings | None:
        """Atomically restore an excluded project, optionally registering it manually."""
        if project.source is not ProjectSource.MANUAL:
            raise ValueError("Only manual projects belong in the config")
        with file_lock(self.path):
            document = self._read()
            settings = _decode_settings(document.unwrap())
            if settings != expected:
                return None
            if project.path not in settings.excluded_projects or any(
                item.path == project.path for item in settings.manual_projects
            ):
                return None
            document["excluded_projects"] = sorted(
                str(item) for item in settings.excluded_projects - {project.path}
            )
            if manual:
                document["projects"] = [
                    {"name": item.name, "path": str(item.display_path)}
                    for item in (*settings.manual_projects, project)
                ]
            self._write(document)
            return _decode_settings(document.unwrap())

    def _read(self) -> TOMLDocument:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _default_document()
        except UnicodeDecodeError as exc:
            raise InvalidConfigError(f"Config is not valid UTF-8: {self.path}") from exc

        try:
            return tomlkit.parse(text)
        except tomlkit.exceptions.ParseError as exc:
            raise InvalidConfigError(f"Invalid TOML in {self.path}: {exc}") from exc

    def _write(self, document: TOMLDocument) -> None:
        with atomic_write(self.path) as stream:
            stream.write(tomlkit.dumps(document))


def _default_document() -> TOMLDocument:
    document = tomlkit.document()
    document.add(tomlkit.comment("pcd-cli configuration"))
    document["roots"] = []
    document["excluded_projects"] = []

    scan = tomlkit.table()
    scan["hidden"] = True
    scan["follow_symlinks"] = False
    scan["exclude"] = sorted(DEFAULT_EXCLUDED_NAMES)

    document["scan"] = scan
    document["projects"] = tomlkit.aot()

    return document


def _decode_settings(value: object) -> ProjectSettings:
    config = _table(value, "Config root must be a table")
    _validate_keys(config, ROOT_KEYS, "config")
    roots = tuple(
        _absolute_path(item, "root path") for item in _array(config.get("roots", []), "roots")
    )
    manual_projects = tuple(
        _decode_manual_project(item) for item in _array(config.get("projects", []), "projects")
    )
    excluded_paths = tuple(
        canonical_path(_absolute_path(item, "excluded project path"))
        for item in _array(config.get("excluded_projects", []), "excluded_projects")
    )
    if len(set(excluded_paths)) != len(excluded_paths):
        raise InvalidConfigError("Config contains duplicate excluded projects")
    if set(excluded_paths) & {project.path for project in manual_projects}:
        raise InvalidConfigError("Manual projects cannot also be excluded projects")
    scan = _table(config.get("scan", {}), "scan must be a table")
    _validate_keys(scan, SCAN_KEYS, "scan")
    include_hidden = _boolean(scan.get("hidden", True), "scan.hidden")
    follow_symlinks = _boolean(scan.get("follow_symlinks", False), "scan.follow_symlinks")
    excluded_names = frozenset(
        _text(item, "scan.exclude item")
        for item in _array(
            scan.get("exclude", list(DEFAULT_EXCLUDED_NAMES)),
            "scan.exclude",
        )
    )
    editor = _text(config["editor"], "editor") if "editor" in config else None

    if len({canonical_path(root) for root in roots}) != len(roots):
        raise InvalidConfigError("Config contains duplicate roots")

    if len({project.path for project in manual_projects}) != len(manual_projects):
        raise InvalidConfigError("Config contains duplicate manual projects")

    return ProjectSettings(
        roots=roots,
        manual_projects=manual_projects,
        include_hidden=include_hidden,
        follow_symlinks=follow_symlinks,
        excluded_names=excluded_names,
        editor=editor,
        excluded_projects=frozenset(excluded_paths),
    )


def _decode_manual_project(value: object) -> Project:
    project = _table(value, "project entries must be tables")
    _validate_keys(project, PROJECT_KEYS, "project")
    name = _text(project.get("name"), "project name")
    display_path = _absolute_path(project.get("path"), "project path")

    return Project(
        name=name,
        path=canonical_path(display_path),
        display_path=display_path,
        source=ProjectSource.MANUAL,
    )


def _table(value: object, message: str) -> Mapping[str, object]:
    if not _is_table(value):
        raise InvalidConfigError(message)

    return value


def _is_table(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _validate_keys(table: Mapping[str, object], allowed: frozenset[str], name: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise InvalidConfigError(f"Unknown {name} key(s): {joined}")


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidConfigError(f"{name} must be an array")

    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidConfigError(f"{name} must be a non-empty string")

    return value


def _absolute_path(value: object, name: str) -> Path:
    text = _text(value, name)
    if "\x00" in text:
        raise InvalidConfigError(f"{name} contains a null character")
    try:
        path = Path(text).expanduser()
    except RuntimeError as exc:
        raise InvalidConfigError(f"{name} contains an unknown home directory") from exc

    if not path.is_absolute():
        raise InvalidConfigError(f"{name} must be absolute or start with ~")

    return path


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidConfigError(f"{name} must be a boolean")

    return value
