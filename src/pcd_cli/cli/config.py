from __future__ import annotations

import os
import shlex
import subprocess
from typing import TYPE_CHECKING

import click

from pcd_cli.config import InvalidConfigError

if TYPE_CHECKING:
    from pcd_cli.catalog import ProjectCatalog


@click.group("config")
def config_commands() -> None:
    """User configuration commands."""


@config_commands.command("path")
@click.pass_obj
def print_config_path(catalog: ProjectCatalog) -> None:
    """Print the path to the user-editable configuration file."""
    click.echo(catalog.config.path)


@config_commands.command("show")
@click.pass_obj
def show_config(catalog: ProjectCatalog) -> None:
    """Print the effective user configuration."""
    click.echo(catalog.config.effective_toml(), nl=False)


@config_commands.command("edit")
@click.pass_obj
def edit_config(catalog: ProjectCatalog) -> None:
    """Open the user configuration in $VISUAL or $EDITOR."""
    try:
        editor = catalog.config.load().editor
    except InvalidConfigError:
        editor = None

    editor = editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")

    if editor is None:
        raise click.UsageError("Set $VISUAL or $EDITOR before running `pcd config edit`")

    try:
        command = shlex.split(editor)
    except ValueError as exc:
        raise click.UsageError(f"Invalid editor command: {exc}") from exc
    if not command:
        raise click.UsageError("Set $VISUAL or $EDITOR before running `pcd config edit`")

    catalog.config.ensure_exists()

    result = subprocess.run([*command, str(catalog.config.path)], check=False)
    if result.returncode:
        raise click.ClickException(f"Editor exited with status {result.returncode}")


@config_commands.command("validate")
@click.pass_obj
def validate_config(catalog: ProjectCatalog) -> None:
    """Validate the user configuration."""
    try:
        catalog.config.load()
    except InvalidConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Configuration is valid: {catalog.config.path}")
