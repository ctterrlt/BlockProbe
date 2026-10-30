# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typer CLI for BlockProbe.

The CLI only orchestrates :mod:`blockprobe.ui.actions`; it never duplicates
business logic, so the terminal and the Flet app behave the same way.
"""

from __future__ import annotations

# Python 3.15+ defers these large modules until a command actually uses them.
# Python 3.14 ignores this compatibility declaration and imports them eagerly.
__lazy_modules__ = ["blockprobe.i18n", "blockprobe.ui.actions"]

from pathlib import Path

import typer

import blockprobe.i18n as i18n
import blockprobe.ui.actions as actions
from blockprobe import __version__

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Verify that gambling sites are still blocked on this device.",
)
signature_app = typer.Typer(help="Capture and inspect block-page signatures.")
portals_app = typer.Typer(help="Inspect the configured portal list.")
custom_app = typer.Typer(help="Manage the user's own website list.")
logs_app = typer.Typer(help="Export the local logs.")
consent_app = typer.Typer(help="First-run consent shown before any network activity.")
schedule_app = typer.Typer(help="User-level scheduled scans (off by default).")
app.add_typer(signature_app, name="signature")
app.add_typer(portals_app, name="portals")
app.add_typer(custom_app, name="custom")
app.add_typer(logs_app, name="logs")
app.add_typer(consent_app, name="consent")
app.add_typer(schedule_app, name="schedule")


def _emit(result: actions.ActionResult) -> None:
    typer.echo(result.title)
    if result.detail:
        typer.echo(result.detail)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the application version."""
    typer.echo(f"BlockProbe {__version__}")


@app.command("self-check")
def self_check() -> None:
    """Check storage, cache, portals and the local data directory."""
    _emit(actions.run_self_check())


@app.command()
def diagnose() -> None:
    """Show the device and network context used in reports."""
    _emit(actions.diagnose())


@app.command("scope")
def scope() -> None:
    """Show the privacy, age, cookie and legal scope of a scan."""
    _emit(actions.compliance_scope())


@consent_app.command("status")
def consent_status() -> None:
    """Show whether the first-run consent was recorded."""
    version = actions.consent_version()
    if actions.consent_required():
        typer.echo(
            i18n.t("Consent version {version} has not been accepted yet.", version=version)
        )
        typer.echo(i18n.t("Run 'blockprobe consent accept', or start the app."))
    else:
        typer.echo(
            i18n.t("Consent version {version} is recorded on this device.", version=version)
        )


@consent_app.command("accept")
def consent_accept(
    yes: bool = typer.Option(
        False, "--yes", help="Accept without the interactive confirmation."
    ),
) -> None:
    """Show the first-run texts and record consent (FR-1)."""
    for title, body in actions.consent_texts():
        typer.echo(title)
        typer.echo(body)
        typer.echo("")
    if not yes:
        answer = typer.prompt(i18n.t("Type I AGREE to confirm"))
        if answer.strip() != "I AGREE":
            typer.echo(i18n.t("Consent not recorded. Nothing was changed."))
            raise typer.Exit(code=1)
    _emit(actions.accept_consent())


@app.command()
def refresh(
    portal: str | None = typer.Option(
        None, "--portal", "-p", help="Portal id or start URL fragment to refresh."
    ),
) -> None:
    """Update the stored website list without probing (FR-5)."""
    _emit(actions.refresh_portals(portal))


@app.command()
def scan(
    portal: list[str] = typer.Option(
        None,
        "--portal",
        "-p",
        help="Portal id to include. Repeat for several portals. Default: all sources.",
    ),
    custom: bool = typer.Option(
        False, "--custom", help="Also include the user's own website list."
    ),
    custom_only: bool = typer.Option(
        False,
        "--custom-only",
        help="Probe only the user's own website list.",
    ),
    no_refresh: bool = typer.Option(
        False, "--no-refresh", help="Skip the automatic portal update."
    ),
    wait: bool = typer.Option(
        True, "--wait/--no-wait", help="Follow the run until it finishes."
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        help="Resume a paused run by id, or pass 'last' for the most recent run.",
    ),
    notify: bool = typer.Option(
        False,
        "--notify",
        help="Send a desktop notification about new leaks when the run finishes.",
    ),
) -> None:
    """Probe the stored website list and store verdicts (FR-6)."""
    if resume is not None:
        run_id = actions.active_run_id() if resume == "last" else resume
        if run_id is None:
            typer.echo(i18n.t("No stored run to resume."))
            raise typer.Exit(code=1)
        _emit(actions.resume_scan(run_id))
    else:
        if custom_only and portal:
            typer.echo(i18n.t("Use either --custom-only or --portal, not both."))
            raise typer.Exit(code=1)
        if custom_only:
            request = actions.ScanRequest(
                all_sources=False,
                portal_ids=(),
                with_custom=True,
                auto_refresh=not no_refresh,
            )
        else:
            request = actions.ScanRequest(
                all_sources=not portal,
                portal_ids=tuple(portal or ()),
                with_custom=custom or not portal,
                auto_refresh=not no_refresh,
            )
        _emit(actions.start_scan(request))
    if not wait:
        typer.echo(
            i18n.t("Use 'blockprobe scan --resume last' or the app to follow the run.")
        )
        return
    run_id = actions.active_run_id()
    if run_id is None:
        typer.echo(i18n.t("The run id could not be determined."))
        raise typer.Exit(code=1)

    def report_row(row) -> None:  # type: ignore[no-untyped-def]
        typer.echo(f"  {row.domain}: {row.verdict.label} — {row.reason_label}")

    _emit(actions.wait_for_run(run_id, on_result=report_row))
    if notify:
        note = actions.notify_latest_leaks(run_id)
        typer.echo(note.title)
        typer.echo(note.detail)


@app.command()
def report(
    output_format: str = typer.Option(
        "markdown",
        "--format",
        "-f",
        help="Export format: markdown, json or csv.",
    ),
    run: str | None = typer.Option(
        None, "--run", help="Run id to export. Default: the most recent run."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Destination file. Default: the reports directory."
    ),
    leaks_only: bool = typer.Option(
        False, "--leaks-only", help="Only export websites that opened."
    ),
) -> None:
    """Export the stored results (FR-8, FR-10)."""
    _emit(actions.export_report(output_format, run, output, leaks_only))


@app.command()
def pause() -> None:
    """Pause the active run."""
    _emit(actions.pause_scan())


@app.command()
def resume() -> None:
    """Resume the active or most recent paused run."""
    _emit(actions.resume_scan())


@app.command()
def cancel() -> None:
    """Cancel the active run."""
    _emit(actions.cancel_scan())


@portals_app.command("list")
def portals_list() -> None:
    """List the configured portals and their refresh state."""
    _emit(actions.list_portals())


@portals_app.command("update")
def portals_update(
    portal: str | None = typer.Option(None, "--portal", "-p", help="Portal id to update."),
) -> None:
    """Update the stored website list (same as ``blockprobe refresh``)."""
    _emit(actions.refresh_portals(portal))


@custom_app.command("list")
def custom_list() -> None:
    """List the user's own websites."""
    _emit(actions.list_custom_domains())


@custom_app.command("add")
def custom_add(
    text: str = typer.Argument(..., help="One or more domains, space or comma separated."),
) -> None:
    """Add domains to the user's own website list (FR-15)."""
    _emit(actions.add_custom_domains(text))


@custom_app.command("remove")
def custom_remove(
    domain: str = typer.Argument(..., help="Registered domain to remove."),
) -> None:
    """Remove a website from the user's own list. Portal rows are never deleted."""
    _emit(actions.remove_custom_domain(domain))


@signature_app.command("capture")
def signature_capture(
    domain: str = typer.Argument(..., help="Domain to capture."),
) -> None:
    """Capture a real block-page response fingerprint (FR-12)."""
    _emit(actions.capture_signature(domain))


@signature_app.command("list")
def signature_list() -> None:
    """List captured fingerprints, verified rules and dynamic patterns."""
    _emit(actions.list_signatures())


@signature_app.command("refresh")
def signature_refresh() -> None:
    """Fetch the live BetBlocker block page and append new patterns."""
    _emit(actions.refresh_betblocker_signatures())


@logs_app.command("export")
def logs_export(
    output: Path | None = typer.Option(None, "--output", "-o", help="Destination zip file."),
) -> None:
    """Export the ring buffer and the JSONL log as a zip archive."""
    _emit(actions.export_logs(output))


@schedule_app.command("status")
def schedule_status() -> None:
    """Show whether a user-level scheduled scan is installed."""
    _emit(actions.schedule_status())


@schedule_app.command("install")
def schedule_install(
    frequency: str = typer.Option(
        "weekly", "--frequency", "-f", help="daily or weekly."
    ),
    time: str = typer.Option("20:00", "--time", "-t", help="Local time, HH:MM (24h)."),
    weekday: int = typer.Option(
        1, "--weekday", "-w", help="1=Monday ... 7=Sunday, used when weekly."
    ),
    command: str = typer.Option(
        "",
        "--command",
        help="Command to run. Default: this interpreter with 'blockprobe scan --wait --notify'.",
    ),
) -> None:
    """Write the job files; nothing is enabled until you run the printed command."""
    _emit(actions.install_schedule(frequency, time, weekday, command))


@schedule_app.command("remove")
def schedule_remove() -> None:
    """Remove the job files written by 'schedule install'."""
    _emit(actions.remove_schedule())


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
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    if headless:
        asgi_app = ft.run(
            flet_main,
            assets_dir=str(assets_dir),
            export_asgi_app=True,
        )
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
            assets_dir=str(assets_dir),
            view=ft.AppView.WEB_BROWSER,
            host=host,
            port=port,
        )


def main() -> None:
    """Console entry point installed by the ``blockprobe`` script."""
    app()


if __name__ == "__main__":
    main()
