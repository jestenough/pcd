"""Read-only environment checks, imported only when explicitly requested."""

import click


@click.command("doctor")
@click.pass_context
def doctor_command(ctx: click.Context) -> None:
    """Check config, cache, paths, and detectable shell integration without changes."""
    from pcd_cli.diagnostics import diagnose
    from pcd_cli.display import terminal_text
    from pcd_cli.filesystem import ApplicationPaths

    findings = diagnose(ApplicationPaths.resolve())
    for finding in findings:
        click.echo(terminal_text(f"{finding.status}: {finding.area}: {finding.message}"))
    issues = sum(finding.status == "ISSUE" for finding in findings)
    click.echo(f"Doctor found {issues} issue(s); no changes made.")
    ctx.exit(1 if issues else 0)
