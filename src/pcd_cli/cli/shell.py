import click

from pcd_cli.environment import current_platform, Platform
from pcd_cli.integrations.shell import (
    ConfiguredShell,
    detect_shell,
    invoking_shell,
    Shell,
    ShellChange,
    ShellIntegrationError,
)
from pcd_cli.integrations.shells.registry import driver_for


@click.group("shell")
def shell_commands() -> None:
    """Install and manage shell integration."""


@shell_commands.command("install")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def install_shell(shell: str | None) -> None:
    """Install persistent shell integration into the shell startup file."""
    platform = current_platform()
    selected = _select_shell(shell, platform, prompt=True)
    integration = ConfiguredShell.for_shell(selected, platform)
    result = integration.install()
    if result is ShellChange.CHANGED:
        click.echo(f"Installed {integration.shell.value} integration in {integration.config_path}")
        click.echo(f"Reload the current shell with: {integration.reload_command()}")
        return

    if result is ShellChange.UNCHANGED:
        click.echo(f"Shell integration is already installed in {integration.config_path}")
        return

    click.echo(
        f"Shell integration is already configured manually in {integration.config_path}; left unchanged."
    )


@shell_commands.command("status")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def shell_status(shell: str | None) -> None:
    """Show shell configuration and whether this invocation used a wrapper."""
    platform = current_platform()
    integration = ConfiguredShell.for_shell(_select_shell(shell, platform), platform)
    click.echo(f"Shell: {integration.shell.value}")
    click.echo(f"Config: {integration.config_path}")
    click.echo(f"Configured: {integration.state().value}")

    wrapper: Shell | None = invoking_shell()
    invoked = "no" if wrapper is None else f"yes ({wrapper.value})"
    click.echo(f"Invoked through wrapper: {invoked}")


@shell_commands.command("uninstall")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def uninstall_shell(shell: str | None) -> None:
    """Remove integration installed by `pcd shell install`."""
    platform = current_platform()
    integration = ConfiguredShell.for_shell(_select_shell(shell, platform), platform)
    result = integration.uninstall()
    if result is ShellChange.CHANGED:
        click.echo(f"Removed shell integration from {integration.config_path}")
        return

    if result is ShellChange.MANUAL:
        click.echo(f"Integration in {integration.config_path} is managed manually; left unchanged.")
        return

    click.echo(f"Shell integration is not installed in {integration.config_path}")


@shell_commands.command("init")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def init_shell(shell: str | None) -> None:
    """Print shell integration for manual dotfile management."""
    platform = current_platform()
    selected = _select_shell(shell, platform)
    click.echo(driver_for(selected, platform).render(), nl=False)


def _select_shell(shell: str | None, platform: Platform, *, prompt: bool = False) -> Shell:
    if shell is not None:
        return Shell(shell.casefold())

    try:
        return detect_shell(platform)
    except ShellIntegrationError as exc:
        if prompt and platform is Platform.WINDOWS:
            return _prompt_windows_shell()
        raise click.UsageError(str(exc)) from exc


def _prompt_windows_shell() -> Shell:
    choices = (
        ("PowerShell 7+", Shell.PWSH),
        ("Windows PowerShell 5.1", Shell.POWERSHELL),
        ("Command Prompt (cmd)", Shell.CMD),
        ("Git Bash", Shell.BASH),
    )

    click.echo("Select shell:")
    for index, (label, _shell) in enumerate(choices, start=1):
        click.echo(f"  {index}. {label}")

    choice = click.prompt(
        "Enter number",
        type=click.IntRange(1, len(choices)),
        show_choices=False,
    )

    return choices[choice - 1][1]
