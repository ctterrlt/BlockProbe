# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typer CLI for BlockProbe."""

from __future__ import annotations

import typer

from blockprobe import __version__
from blockprobe.ui import actions

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Verify that gambling sites are still blocked on this device.",
)
signature_app = typer.Typer(help="Capture and inspect block-page signatures.")
app.add_typer(signature_app, name="signature")


@app.command()
def version() -> None:
    """Print the application version."""
    typer.echo(f"BlockProbe {__version__}")


@app.command("self-check")
def self_check() -> None:
    """Check the runtime and create the local data directory."""
    result = actions.run_self_check()
    typer.echo(result.title)
    typer.echo(result.detail)


@app.command()
def refresh(
    portal: str | None = typer.Option(None, help="Portal id to refresh."),
) -> None:
    """Refresh portal lists without probing."""
    result = actions.refresh_portals()
    typer.echo(result.title)
    typer.echo(result.detail)
    if portal:
        typer.echo(f"Requested portal: {portal}")
    raise typer.Exit(code=1)


@app.command()
def scan() -> None:
    """Probe the stored website list."""
    result = actions.start_scan()
    typer.echo(result.title)
    typer.echo(result.detail)
    raise typer.Exit(code=1)


@app.command()
def report(
    output_format: str = typer.Option(
        "markdown",
        "--format",
        help="Export format: markdown, json or csv.",
    ),
) -> None:
    """Export the latest results."""
    result = actions.export_report()
    typer.echo(result.title)
    typer.echo(result.detail)
    typer.echo(f"Requested format: {output_format}")
    raise typer.Exit(code=1)


@signature_app.command("capture")
def signature_capture(
    domain: str = typer.Argument(..., help="Domain to capture."),
) -> None:
    """Capture a real block-page response fingerprint."""
    result = actions.capture_signature()
    typer.echo(result.title)
    typer.echo(result.detail)
    typer.echo(f"Requested domain: {domain}")
    raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8550, help="HTTP port."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Serve without a desktop window or browser (for containers and CI).",
    ),
) -> None:
    """Run the Flet UI as a local web application."""
    import flet as ft

    from blockprobe.ui.app import main as flet_main

    typer.echo(f"BlockProbe web UI: http://{host}:{port}")
    if headless:
        asgi_app = ft.run(flet_main, export_asgi_app=True)
        try:
            import uvicorn
        except ImportError as exc:
            typer.echo(
                "The headless web UI requires the desktop extra. "
                'Install it with: python -m pip install -e ".[desktop]"',
                err=True,
            )
            raise typer.Exit(code=1) from exc
        uvicorn.run(asgi_app, host=host, port=port)
    else:
        ft.run(
            flet_main,
            view=ft.AppView.WEB_BROWSER,
            host=host,
            port=port,
        )


def main() -> None:
    """Console entry point installed by the ``blockprobe`` script."""
    app()


if __name__ == "__main__":
    main()
