import click

from pcd_cli.integrations.shell import (
    detect_shell,
    render_shell_integration,
    Shell,
    shell_integration_active,
    ShellIntegration,
    ShellIntegrationError,
    ShellIntegrationState,
)


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
    integration = _shell_integration(shell)
    if integration.install():
        click.echo(f"Installed {integration.shell.value} integration in {integration.config_path}")
        click.echo(f"Reload the current shell with: {integration.reload_command()}")
        return

    state = integration.state()
    if state is ShellIntegrationState.MANAGED:
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
    """Show whether shell integration is configured and active."""
    integration = _shell_integration(shell)
    click.echo(f"Shell: {integration.shell.value}")
    click.echo(f"Config: {integration.config_path}")
    click.echo(f"Configured: {integration.state().value}")

    active = shell_integration_active()
    click.echo(f"Active in current shell: {'yes' if active else 'no'}")


@shell_commands.command("uninstall")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def uninstall_shell(shell: str | None) -> None:
    """Remove integration installed by `pcd shell install`."""
    integration = _shell_integration(shell)
    if integration.uninstall():
        click.echo(f"Removed shell integration from {integration.config_path}")
        return

    state = integration.state()
    if state is ShellIntegrationState.MANUAL:
        click.echo(f"Integration in {integration.config_path} is managed manually; left unchanged.")
        return

    click.echo(f"Shell integration is not installed in {integration.config_path}")


@shell_commands.command("init")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
@click.pass_context
def init_shell(ctx: click.Context, shell: str | None) -> None:
    """Print shell integration for manual dotfile management."""
    selected = _selected_shell(shell)
    click.echo(render_shell_integration(selected, _registered_command_names(ctx)), nl=False)


def _shell_integration(shell: str | None) -> ShellIntegration:
    return ShellIntegration.for_shell(_selected_shell(shell))


def _selected_shell(shell: str | None) -> Shell:
    if shell is not None:
        return Shell(shell.casefold())

    try:
        return detect_shell()
    except ShellIntegrationError as exc:
        raise click.UsageError(str(exc)) from exc


def _registered_command_names(ctx: click.Context) -> tuple[str, ...]:
    root = ctx.find_root()
    if not isinstance(root.command, click.Group):
        return ()

    return tuple(root.command.list_commands(root))
