# SPDX-License-Identifier: AGPL-3.0-or-later
"""UI actions shared by the Flet app and the CLI.

Each action returns an :class:`ActionResult` and contains no Flet imports, so the
CLI and the UI exercise exactly the same behaviour. Actions that still have no
implementation say so explicitly; they never invent scan results.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from platformdirs import user_data_dir

from blockprobe import APP_NAME, __version__
from blockprobe.core.compliance import SCOPE_FACTS, scope_summary
from blockprobe.core.enums import RunStatus
from blockprobe.core.errors import BlockProbeError, ConsentRequired
from blockprobe.core.models import Portal, ResultRow, RunComparison, RunSummary, ScanSelection
from blockprobe.i18n import LANGUAGES, set_language, t
from blockprobe.logging_setup import export_logs_zip
from blockprobe.portals.domain_normalizer import parse_domain_input
from blockprobe.portals.loader import ensure_portal_uuid, save_portal
from blockprobe.probe.health import ProbeHealth, assess_probe_health
from blockprobe.services import Runtime, get_runtime
from blockprobe.storage.base import ResultFilter

_LOGGER = logging.getLogger("blockprobe.ui.actions")


@dataclass(frozen=True)
class ActionResult:
    """A short status update shown in the UI or printed by the CLI."""

    title: str
    detail: str
    ok: bool = True


@dataclass(frozen=True)
class ScanRequest:
    """What the user selected on the Run screen."""

    all_sources: bool = True
    portal_ids: tuple[str, ...] = ()
    with_custom: bool = False
    auto_refresh: bool = True

    def to_selection(self) -> ScanSelection:
        if self.all_sources:
            return ScanSelection(all_sources=True, with_custom=True)
        return ScanSelection(
            all_sources=False,
            portal_ids=list(self.portal_ids),
            with_custom=self.with_custom,
        )


# The first-run texts (FR-1). Kept here, without any UI import, so the Flet
# consent screen, the CLI and the help screen show the same words. They are
# drafts pending human legal review and are not legal advice.
CONSENT_TEXTS: tuple[tuple[str, str], ...] = (
    (
        "What BlockProbe does",
        "It checks whether the gambling websites on official regulator lists are "
        "still reachable from this device. It stores the result locally so you can "
        "see what a blocker, a DNS filter or an ISP blackout is letting through.",
    ),
    (
        "What it does not do",
        "It does not block anything, it does not install filters, it is not a "
        "medical or legal service, and it never guarantees that a website is "
        "blocked. A 'website opened' result is a leak you may want to fix.",
    ),
    (
        "Privacy",
        "Everything is processed on this device. There is no telemetry, no account "
        "and no cloud. Only the checks you start leave the device: the requests to "
        "the portal lists and to the websites being probed. Cookies are never "
        "stored, response bodies are not kept (a hash and a short snippet are kept "
        "only in debug mode), and you can export or erase everything from Settings.",
    ),
    (
        "Terms",
        "BlockProbe is free software under AGPL-3.0-or-later, comes with no "
        "warranty, and is an open-source, non-commercial project made without any "
        "intention of profit. Results are indicative. All information stays on "
        "your personal device.",
    ),
    (
        "Permission",
        "Use BlockProbe only on devices you own or have the owner's explicit "
        "permission to test.",
    ),
)


def consent_texts() -> tuple[tuple[str, str], ...]:
    """Return the first-run texts (FR-1) in the configured language."""
    return tuple((t(title), t(body)) for title, body in CONSENT_TEXTS)


def runtime() -> Runtime:
    """Return the process-wide runtime, building it on first use."""
    return get_runtime()


def _failure(title: str, exc: BaseException) -> ActionResult:
    return ActionResult(title, f"{type(exc).__name__}: {exc}", ok=False)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def data_directory() -> Path:
    """Return the per-user directory where BlockProbe stores local data."""
    try:
        return runtime().settings.data_dir
    except Exception:
        return Path(user_data_dir(APP_NAME, appauthor=False))


def ensure_data_directory() -> Path:
    """Create the data directory if needed and return it."""
    path = data_directory()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _flet_version() -> str:
    try:
        return metadata.version("flet")
    except metadata.PackageNotFoundError:
        return "not installed"


def environment_summary() -> str:
    """Return the runtime information shown in the Environment card."""
    runtime_info = "\n".join(
        (
            f"BlockProbe {__version__}",
            f"Python {platform.python_version()}",
            f"Platform {platform.platform()}",
            f"Flet {_flet_version()}",
            f"Data directory {data_directory()}",
        )
    )
    try:
        app_runtime = runtime()
    except Exception:
        return runtime_info
    return "\n".join(
        (
            runtime_info,
            f"Portals {len(app_runtime.portals)}",
            f"Storage {app_runtime.storage.health_check()}",
        )
    )


def run_self_check() -> ActionResult:
    """Verify storage, cache, portals and the local data directory."""
    try:
        app_runtime = runtime()
        app_runtime.settings.ensure_directories()
        health = app_runtime.storage.health_check()
        sites = app_runtime.storage.count_sites()
        portals = app_runtime.storage.list_portals()
        cache_stats = app_runtime.cache.stats()
    except Exception as exc:
        return _failure(t("Self-check failed"), exc)
    lines = [
        t("The local runtime is ready."),
        "",
        f"Version: {__version__}",
        f"Python: {platform.python_version()}",
        f"Platform: {platform.platform()}",
        f"Flet: {_flet_version()}",
        f"Data directory: {data_directory()}",
        f"Storage: {health}",
        f"Websites stored: {sites}",
        f"Portals: {len(portals)}",
        f"Cache entries: {cache_stats.get('entries', 'n/a')}",
        scope_summary(),
    ]
    return ActionResult(t("Self-check passed"), "\n".join(lines))


def open_data_directory() -> ActionResult:
    """Create the local data directory and report its location."""
    try:
        path = ensure_data_directory()
    except OSError as exc:
        return _failure(t("Could not open the data directory"), exc)
    return ActionResult(t("Data directory ready"), str(path))


# ---------------------------------------------------------------- consent (FR-1)


def consent_version() -> str:
    return runtime().scan.consent_version


def consent_required() -> bool:
    try:
        return runtime().scan.consent_required()
    except Exception:
        return True


def accept_consent() -> ActionResult:
    try:
        runtime().scan.accept_consent()
    except Exception as exc:
        return _failure(t("Could not store the consent record"), exc)
    return ActionResult(
        t("Consent recorded"),
        t(
            "The consent version and timestamp were stored locally. "
            "You can erase everything from Settings at any time."
        ),
    )


# ------------------------------------------------------------------- portals (FR-2)


def portal_choices() -> list[tuple[str, str]]:
    try:
        return runtime().portal_choices()
    except Exception:
        return []


def portal_label(portal_id: str) -> str:
    try:
        return runtime().portal_label(portal_id)
    except Exception:
        return portal_id


def list_portals() -> ActionResult:
    try:
        portals = runtime().storage.list_portals()
    except Exception as exc:
        return _failure(t("Could not read the portal list"), exc)
    if not portals:
        return ActionResult(
            t("No portals configured"),
            t("Add a portal TOML file or use the built-in ADM definition."),
            ok=False,
        )
    lines: list[str] = []
    for portal in portals:
        state = "enabled" if portal.enabled else "disabled"
        refreshed = (
            "never updated" if not portal.last_refreshed_at else f"updated {portal.last_error or 'ok'}"
        )
        lines.append(f"{portal.name} [{portal.uuid}] — {state}, {refreshed}")
        lines.append(f"  {portal.start_url}")
    return ActionResult(t("Portals"), "\n".join(lines))


def refresh_portals(portal_id: str | None = None) -> ActionResult:
    """Manual 'Update website list' action; never starts a test run."""
    if consent_required():
        return ActionResult(
            t("Consent required"),
            t(
                "Open the app and accept the first-run notice before any network "
                "activity, including a portal update."
            ),
            ok=False,
        )
    app_runtime = runtime()
    portal_ids = [portal_id] if portal_id else None
    if portal_id and app_runtime.storage.get_portal(portal_id) is None:
        return ActionResult(
            t("Unknown portal"),
            t(
                "No portal with id {portal_id}. Use 'blockprobe portals list'.",
                portal_id=portal_id,
            ),
            ok=False,
        )
    try:
        summary = app_runtime.refresh.refresh(portal_ids, force=True)
    except Exception as exc:
        return _failure(t("Portal update failed"), exc)
    lines: list[str] = []
    for diff in summary.results:
        state = diff.status.value
        details = f"{diff.pages} page(s), {diff.found} found, {diff.added} new"
        if diff.error:
            details += f" — {diff.error}"
        if diff.warnings:
            details += f" — {diff.warnings[0]}"
        lines.append(f"{diff.portal_name}: {state} ({details})")
    failed = [diff for diff in summary.results if diff.status.value == "error"]
    title = t("Website list updated") if not failed else t("Website list update finished with errors")
    return ActionResult(title, "\n".join(lines) or t("No portals to update."), ok=not failed)


def add_portal_definition(name: str, start_url: str, country: str = "") -> ActionResult:
    """Save a public regulator feed definition, separately from custom sites."""
    clean_name = name.strip()
    clean_url = start_url.strip()
    clean_country = country.strip().upper() or "ZZ"
    if not clean_name or not clean_url.startswith(("http://", "https://")):
        return ActionResult(
            t("Portal not added"),
            t("Enter a portal name and a public http(s) list URL."),
            ok=False,
        )
    if len(clean_country) != 2 or not clean_country.isascii() or not clean_country.isalpha():
        return ActionResult(t("Portal not added"), t("Use a two-letter country code."), ok=False)
    app_runtime = runtime()
    portal = Portal(
        uuid=ensure_portal_uuid(),
        name=clean_name,
        start_url=clean_url,
        country=clean_country,
        enabled=True,
        default=False,
    )
    try:
        save_portal(portal, app_runtime.settings.user_portals_dir)
        app_runtime.portals = app_runtime.refresh.sync_portals()
    except Exception:
        _LOGGER.exception("portal definition could not be saved")
        return ActionResult(
            t("Portal not added"),
            t("Could not save this portal. Check the URL and try again."),
            ok=False,
        )
    return ActionResult(
        t("Portal feed added"),
        t("The feed is available as a scan source. Update it to import its domains."),
    )


# ------------------------------------------------------------------- scan (FR-6)


def preview_scan(request: ScanRequest) -> ActionResult:
    try:
        app_runtime = runtime()
        preview = app_runtime.scan.preview(request.to_selection())
    except Exception as exc:
        return _failure(t("Could not prepare the preview"), exc)
    if preview.total == 0:
        return ActionResult(
            t("Nothing to scan"),
            t(
                "No website matches the selection. Update the website list or add "
                "custom websites first."
            ),
            ok=False,
        )
    lines = [
        t("Websites to probe: {count}", count=preview.total),
        t("Custom websites: {count}", count=preview.custom_count),
        t("Sources: {labels}", labels=", ".join(preview.portal_labels) or "none"),
    ]
    if preview.stale_portals:
        lines.append(
            t(
                "Portal lists older than the refresh window: {labels}",
                labels=", ".join(preview.stale_portals),
            )
        )
        if request.auto_refresh:
            lines.append(t("They will be updated before the run."))
    return ActionResult(t("Scan preview"), "\n".join(lines))


def start_scan(request: ScanRequest) -> ActionResult:
    try:
        run, total = runtime().scan.start_run(
            request.to_selection(), auto_refresh=request.auto_refresh
        )
    except BlockProbeError as exc:
        _LOGGER.info("scan request rejected: %s", exc)
        if isinstance(exc, ConsentRequired):
            return ActionResult(
                t("Consent required"),
                t("Accept the first-run notice before scanning or updating websites."),
                ok=False,
            )
        detail = (
            t(
                "No websites are selected. Refresh a portal or add a custom website, "
                "then start the scan again."
            )
            if "No websites are selected." in str(exc)
            else t("Could not start the scan. Check the selected sources and try again.")
        )
        return ActionResult(
            t("Scan not started"),
            detail,
            ok=False,
        )
    except Exception:
        _LOGGER.exception("scan request failed")
        return ActionResult(
            t("Scan not started"),
            t("Could not start the scan. Check the selected sources and try again."),
            ok=False,
        )
    return ActionResult(
        t("Scan started"),
        t(
            "Run {run_id} is checking {count} websites in the background.",
            run_id=run.id,
            count=total,
        ),
    )


RUN_FINISHED_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.CANCELLED,
    RunStatus.FAILED,
    RunStatus.PAUSED,
}


def wait_for_run(
    run_id: str,
    on_result: Callable[[ResultRow], None] | None = None,
    poll_seconds: float = 1.0,
    timeout_seconds: float | None = None,
) -> ActionResult:
    """Wait for a run to finish, reporting each stored result once."""
    app_runtime = runtime()
    seen: set[str] = set()
    started = time.monotonic()
    while True:
        run = app_runtime.storage.get_run(run_id)
        if run is None:
            return ActionResult(
            t("Run not found"), t("No run with id {run_id}.", run_id=run_id), ok=False
        )
        if on_result is not None:
            for row in app_runtime.storage.get_results(run_id):
                if row.domain in seen:
                    continue
                seen.add(row.domain)
                on_result(row)
        if run.status in RUN_FINISHED_STATUSES:
            break
        if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
            return ActionResult(
                t("Still running"),
                t(
                    "Run {run_id} did not finish within {seconds} seconds. "
                    "Use 'blockprobe scan --resume' or the app to follow it.",
                    run_id=run_id,
                    seconds=f"{timeout_seconds:.0f}",
                ),
                ok=False,
            )
        time.sleep(max(0.1, poll_seconds))
    summary = app_runtime.storage.get_run_summary(run_id)
    latest = app_runtime.storage.get_run(run_id) or run
    health = (
        ProbeHealth.from_dict(latest.health)
        if latest.health
        else assess_probe_health(
            app_runtime.storage.get_results(run_id), [], latest.network_context
        )
    )
    return _summary_result(summary, run.status, health)


def _summary_result(
    summary: RunSummary,
    status: RunStatus,
    health: ProbeHealth | None = None,
) -> ActionResult:
    title = {
        RunStatus.COMPLETED: t("Scan finished"),
        RunStatus.CANCELLED: t("Scan cancelled"),
        RunStatus.FAILED: t("Scan failed"),
        RunStatus.PAUSED: t("Scan paused"),
    }.get(status, t("Scan state"))
    lines = [
        t("Run {run_id}: {status}", run_id=summary.run_id, status=status.value),
        t("Checked: {count}", count=summary.total),
        t("Blocked: {count}", count=summary.blocked),
        t("Unreachable: {count}", count=summary.unreachable),
        t("Opened (leaks): {count}", count=summary.opened),
        t("Needs review: {count}", count=summary.needs_review),
        t("Blocked share: {percent}%", percent=summary.blocked_percent),
    ]
    if health is not None:
        lines.append(
            t(
                "Interference signal: {percent}% ({level})",
                percent=health.risk_percent,
                level=health.risk_level,
            )
        )
        if health.control_failures:
            passed = len(health.controls) - health.control_failures
            lines.append(
                t(
                    "Neutral controls: {passed} passed, {failed} failed.",
                    passed=passed,
                    failed=health.control_failures,
                )
            )
        if health.first_failure_after:
            lines.append(
                t(
                    "First neutral control failure after: {domain}",
                    domain=health.first_failure_after,
                )
            )
        if health.recheck_domains:
            lines.append(
                t(
                    "Recheck these domains after the scan: {domains}",
                    domains=", ".join(health.recheck_domains[:12]),
                )
            )
    if status is RunStatus.PAUSED:
        lines.append(
            t(
                "The connectivity guard paused this run. Resume it when the "
                "network is back."
            )
        )
    return ActionResult(title, "\n".join(lines), ok=status is not RunStatus.FAILED)


def refresh_if_stale(request: ScanRequest) -> ActionResult:
    """Step 0 of a run: only refresh selected portals older than the TTL."""
    if not request.auto_refresh:
        return ActionResult(t("Automatic update skipped"), t("Disabled for this run."))
    app_runtime = runtime()
    selection = request.to_selection()
    portal_ids = None if selection.all_sources else selection.portal_ids_only
    if not selection.all_sources and not portal_ids:
        return ActionResult(t("Automatic update skipped"), t("The selection has no portals."))
    try:
        summary = app_runtime.refresh.refresh(portal_ids, only_stale=True)
    except Exception as exc:
        return _failure(t("Automatic update failed"), exc)
    lines = [f"{diff.portal_name}: {diff.status.value} ({diff.added} new)" for diff in summary.results]
    return ActionResult(t("Automatic update"), "\n".join(lines) or t("No stale portals."))


def pause_scan() -> ActionResult:
    if runtime().scan.pause():
        return ActionResult(t("Scan paused"), t("The run will resume from the next website."))
    return ActionResult(t("No active scan"), t("There is no run to pause."), ok=False)


def resume_scan(run_id: str | None = None) -> ActionResult:
    app_runtime = runtime()
    if run_id is None and app_runtime.scan.resume():
        return ActionResult(t("Scan resumed"), t("The active run continues."))
    if run_id is None:
        active = app_runtime.scan.active_run
        if active is not None:
            run_id = active.id
        else:
            paused = next(
                (
                    run
                    for run in app_runtime.storage.list_runs(limit=50)
                    if run.status is RunStatus.PAUSED
                ),
                None,
            )
            if paused is not None:
                run_id = paused.id
        if run_id is None:
            return ActionResult(
                t("No paused scan"),
                t("Start a run first, or pass the id of a stored run to resume."),
                ok=False,
            )
    try:
        run, remaining = app_runtime.scan.resume_run(run_id)
    except BlockProbeError as exc:
        return ActionResult(t("Scan not resumed"), str(exc), ok=False)
    except Exception as exc:
        return _failure(t("Scan not resumed"), exc)
    return ActionResult(
        t("Scan resumed"),
        t(
            "Run {run_id} still has {count} websites to check.",
            run_id=run.id,
            count=remaining,
        ),
    )


def cancel_scan() -> ActionResult:
    if runtime().scan.cancel():
        return ActionResult(t("Scan cancelled"), t("The active run stops after the current website."))
    return ActionResult(t("No active scan"), t("There is no run to cancel."), ok=False)


def latest_run_summary() -> RunSummary | None:
    try:
        run = runtime().storage.latest_run()
    except Exception:
        return None
    if run is None:
        return None
    return runtime().storage.get_run_summary(run.id)


def latest_run_health(run_id: str | None = None) -> ProbeHealth | None:
    """Return the stored reliability assessment for a run, if available."""
    try:
        app_runtime = runtime()
        run = (
            app_runtime.storage.get_run(run_id)
            if run_id
            else app_runtime.storage.latest_run()
        )
        if run is None:
            return None
        if run.health:
            return ProbeHealth.from_dict(run.health)
        return assess_probe_health(
            app_runtime.storage.get_results(run.id), [], run.network_context
        )
    except Exception:
        return None


def active_run_id() -> str | None:
    """Return the id of the active run, or the most recent stored run."""
    try:
        app_runtime = runtime()
        active = app_runtime.scan.active_run
        if active is not None:
            return active.id
        latest = app_runtime.storage.latest_run()
        return latest.id if latest is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------- results (FR-8)


def load_results(
    run_id: str | None = None,
    leaks_only: bool = False,
    search: str = "",
) -> list[ResultRow]:
    app_runtime = runtime()
    run = app_runtime.storage.get_run(run_id) if run_id else app_runtime.storage.latest_run()
    if run is None:
        return []
    filters = ResultFilter(leaks_only=leaks_only, search=search)
    return app_runtime.storage.get_results(run.id, filters)


def load_comparison(run_id: str | None = None) -> RunComparison | None:
    app_runtime = runtime()
    run = app_runtime.storage.get_run(run_id) if run_id else app_runtime.storage.latest_run()
    if run is None:
        return None
    try:
        return app_runtime.report.compare(run.id)
    except BlockProbeError:
        return None


def export_report(
    fmt: str = "markdown",
    run_id: str | None = None,
    destination: Path | None = None,
    leaks_only: bool = False,
) -> ActionResult:
    try:
        app_runtime = runtime()
        filters = ResultFilter(leaks_only=True) if leaks_only else None
        path = app_runtime.report.generate(
            run_id=run_id, fmt=fmt, destination=destination, filters=filters
        )
    except BlockProbeError as exc:
        return ActionResult(t("Report not written"), str(exc), ok=False)
    except Exception as exc:
        return _failure(t("Report not written"), exc)
    return ActionResult(t("Report written"), str(path))


def export_domain_list(destination: Path | None = None) -> ActionResult:
    try:
        path = runtime().report.export_domains(destination)
    except Exception as exc:
        return _failure(t("Domain list not written"), exc)
    return ActionResult(t("Domain list written"), str(path))


# ------------------------------------------------------------- custom list (FR-15)


def add_custom_domains(text: str) -> ActionResult:
    parsed = parse_domain_input(text)
    if not parsed.accepted:
        reasons = "\n".join(f"{item.value}: {item.reason}" for item in parsed.rejected[:10])
        return ActionResult(
            t("No website added"),
            reasons or "Paste at least one domain, one per line.",
            ok=False,
        )
    pairs = [(item.domain, item.host) for item in parsed.accepted]
    try:
        diff = runtime().storage.add_custom_domains(pairs, note="Added from the UI")
    except Exception as exc:
        return _failure(t("Could not save the custom websites"), exc)
    lines = [f"Added: {', '.join(diff.added)}"] if diff.added else []
    if diff.existing:
        lines.append(f"Already stored: {', '.join(diff.existing)}")
    if parsed.rejected:
        lines.append("Rejected:")
        lines.extend(f"  {item.value}: {item.reason}" for item in parsed.rejected[:10])
    return ActionResult(f"Custom websites saved ({len(diff.added)} new)", "\n".join(lines))


def list_custom_domains() -> ActionResult:
    try:
        sites = runtime().storage.list_custom_sites()
    except Exception as exc:
        return _failure(t("Could not read the custom websites"), exc)
    if not sites:
        return ActionResult(t("No custom websites"), t("Add domains from the Sources screen."))
    lines = [f"{site.domain} ({', '.join(site.hosts)})" for site in sites]
    return ActionResult(f"Custom websites ({len(sites)})", "\n".join(lines))


def remove_custom_domain(domain: str) -> ActionResult:
    try:
        removed = runtime().storage.remove_custom_site(domain)
    except Exception as exc:
        return _failure(t("Could not remove the custom website"), exc)
    if not removed:
        return ActionResult(
            t("Nothing removed"),
            t("{domain} is not a custom website.", domain=domain),
            ok=False,
        )
    return ActionResult(
        t("Custom website removed"),
        t("{domain} will not be probed anymore.", domain=domain),
    )


def set_site_excluded(domain: str, excluded: bool) -> ActionResult:
    try:
        runtime().storage.set_excluded(domain, excluded)
    except Exception as exc:
        return _failure(t("Could not update the website"), exc)
    action = t("excluded from runs") if excluded else t("included again")
    return ActionResult(
        t("Website updated"),
        t("{domain} is now {status}. Nothing was deleted.", domain=domain, status=action),
    )


# ------------------------------------------------------------ signature (FR-12)


def capture_signature(domain: str) -> ActionResult:
    if consent_required():
        return ActionResult(
            t("Consent required"),
            t(
                "Open the app and accept the first-run notice before any network "
                "activity, including a signature capture."
            ),
            ok=False,
        )
    try:
        result = runtime().signature.capture(domain)
    except BlockProbeError as exc:
        return ActionResult(t("Signature capture failed"), str(exc), ok=False)
    except Exception as exc:
        return _failure(t("Signature capture failed"), exc)
    return ActionResult(t("Signature captured"), result.message)


def list_signatures() -> ActionResult:
    try:
        app_runtime = runtime()
        files = app_runtime.signature.list_captured()
        active = app_runtime.signature.active_rule_count()
        dynamic = app_runtime.storage.list_dynamic_signatures()
    except Exception as exc:
        return _failure(t("Could not read the signatures"), exc)
    lines = [t("Captured fingerprints: {count}", count=len(files))]
    lines.extend(str(path) for path in files[:20])
    lines.append(t("Active verified rules: {count}", count=active))
    lines.append(
        t("Dynamic BetBlocker patterns stored: {count}", count=len(dynamic))
    )
    for signature in dynamic[:30]:
        review = t("review") if signature.needs_review else t("verified")
        lines.append(f"  [{signature.source or 'dynamic'}, {review}] {signature.pattern}")
    if dynamic:
        lines.append(
            t(
                "Dynamic patterns are append-only during normal use; only the "
                "explicit privacy wipe removes them."
            )
        )
    return ActionResult(t("Signatures"), "\n".join(lines))


def refresh_betblocker_signatures() -> ActionResult:
    """Fetch the live BetBlocker page now and append any new patterns."""
    if consent_required():
        return ActionResult(
            t("Consent required"),
            t(
                "Open the app and accept the first-run notice before any network "
                "activity, including a signature refresh."
            ),
            ok=False,
        )
    try:
        refresh = asyncio.run(runtime().scan.refresh_dynamic_signatures())
    except Exception as exc:
        return _failure(t("BetBlocker signature refresh failed"), exc)
    lines = [
        t("Live page: {url}", url=refresh.url),
        t("Fetched: {value}", value=t("yes") if refresh.fetched else t("no")),
        t("New patterns added: {count}", count=len(refresh.new_patterns)),
        t("Stored patterns: {count}", count=refresh.total_patterns),
    ]
    if refresh.error:
        lines.append(t("Error: {error}", error=refresh.error))
    lines.extend(f"  {pattern}" for pattern in refresh.new_patterns)
    title = (
        t("BetBlocker signatures refreshed")
        if refresh.fetched
        else t("BetBlocker signature refresh failed")
    )
    return ActionResult(title, "\n".join(lines), ok=refresh.fetched)


def compliance_scope() -> ActionResult:
    """Return the privacy and legal scope, without network or storage access."""
    lines = [scope_summary(), ""]
    lines.extend(
        f"{fact.title}: {fact.status} — {fact.statement}" for fact in SCOPE_FACTS
    )
    return ActionResult(t("Scope and privacy"), "\n".join(lines))


# ------------------------------------------------------------- diagnostics (M5)


def diagnose() -> ActionResult:
    from blockprobe.diagnostics import collect_network_context

    try:
        context = collect_network_context()
    except Exception as exc:
        return _failure(t("Diagnostics failed"), exc)
    lines = [
        f"Operating system: {context.os_name}",
        f"Resolvers: {', '.join(context.resolvers) or 'not detected'}",
        f"Resolver details: {', '.join(context.resolver_labels) or 'not detected'}",
        f"DNS filtering evidence: {', '.join(context.dns_filters) or 'none detected'}",
        f"Hosts file entries: {len(context.hosts_entries)} "
        f"({context.hosts_block_entries} sinkhole entries)",
        f"Proxies: {', '.join(context.proxies) or 'none detected'}",
        f"VPN interfaces: {', '.join(context.vpn_interfaces) or 'none detected'}",
        f"VPN/tunnel hints: {', '.join(context.vpn_providers) or 'none detected'}",
        f"IPv6 available: {'yes' if context.ipv6 else 'no'}",
        f"Private DNS: {context.private_dns}",
        f"Browser secure DNS: {context.browser_doh}",
        scope_summary(),
    ]
    try:
        dynamic = runtime().storage.list_dynamic_signatures()
        lines.append(f"Dynamic BetBlocker patterns stored: {len(dynamic)}")
    except Exception:
        lines.append("Dynamic BetBlocker patterns stored: unavailable")
    lines.extend(context.notes)
    return ActionResult(t("Device and network context"), "\n".join(lines))


def export_logs(destination: Path | None = None) -> ActionResult:
    app_runtime = runtime()
    if destination is None:
        destination = app_runtime.settings.logs_dir / f"blockprobe-logs-{_stamp()}.zip"
    try:
        path = export_logs_zip(destination)
    except Exception as exc:
        return _failure(t("Log export failed"), exc)
    return ActionResult(t("Logs exported"), str(path))


# ------------------------------------------------------------ scheduling (FR-12)


def schedule_status() -> ActionResult:
    from blockprobe.scheduler import status as job_status

    try:
        info = job_status()
    except Exception as exc:
        return _failure(t("Could not read the scheduled job"), exc)
    state = "installed" if info.installed else "not installed"
    lines = [
        f"Platform: {info.platform}",
        f"State: {state}",
        f"Directory: {info.directory}",
    ]
    for path in info.files:
        lines.append(f"  {'present' if path.exists() else 'missing'}: {path}")
    if info.installed:
        lines.append(f"To run it once now: {info.note}")
    elif info.platform == "android":
        lines.append(info.note)
    else:
        lines.append(f"To enable it: {info.activate}")
    lines.append(
        t(
            "Scheduled scans are off by default and always run as your user; "
            "BlockProbe never asks for administrator rights."
        )
    )
    return ActionResult(t("Scheduled scan"), "\n".join(lines))


def install_schedule(
    frequency: str = "weekly",
    time: str = "20:00",
    weekday: int = 1,
    command: str = "",
) -> ActionResult:
    from blockprobe.scheduler import (
        ScheduleSpec,
        default_command,
        install,
    )
    from blockprobe.scheduler import (
        status as job_status,
    )

    spec = ScheduleSpec(
        frequency=frequency,
        time=time,
        weekday=weekday,
        command=command.strip() or default_command(),
    )
    try:
        written = install(spec)
    except ValueError as exc:
        return ActionResult(t("Schedule not installed"), str(exc), ok=False)
    except OSError as exc:
        return _failure(t("Schedule not installed"), exc)
    info = job_status()
    lines = [f"Written: {path}" for path in written]
    lines.append(t("Nothing was enabled automatically."))
    lines.append(f"To enable it, run: {info.activate}")
    lines.append("Remove it any time from this screen or with 'blockprobe schedule remove'.")
    return ActionResult(t("Scheduled scan file written"), "\n".join(lines))


def remove_schedule() -> ActionResult:
    from blockprobe.scheduler import status as job_status
    from blockprobe.scheduler import uninstall

    try:
        removed = uninstall()
    except OSError as exc:
        return _failure(t("Could not remove the scheduled job"), exc)
    info = job_status()
    lines = [f"Removed: {path}" for path in removed] or [t("Nothing to remove.")]
    lines.append(t("If it was already enabled on your system, disable it with your tools:"))
    if info.activate:
        lines.append(f"  {info.activate}")
    return ActionResult(t("Scheduled scan files removed"), "\n".join(lines), ok=True)


def notify_latest_leaks(run_id: str | None = None) -> ActionResult:
    """Send the FR-12 notification, and only about new leaks when possible."""
    from blockprobe.scheduler.notify import leaks_message, notify

    summary = None
    if run_id:
        try:
            summary = runtime().storage.get_run_summary(run_id)
        except Exception:
            summary = None
    else:
        summary = latest_run_summary()
    if summary is None:
        return ActionResult(t("Nothing to notify"), t("No stored run was found."), ok=False)
    comparison = load_comparison(run_id)
    newly = None
    if comparison is not None and comparison.previous_run_id is not None:
        newly = len(comparison.newly_leaking)
    message = leaks_message(summary.opened, summary.total, newly)
    if not message:
        return ActionResult(t("No notification"), t("No new leaks since the previous run."))
    mechanism = notify("BlockProbe: leak found", message)
    title = t("Notification sent") if mechanism != "logged" else t("Notification logged")
    detail = message if mechanism == "logged" else f"{message}\nShown with: {mechanism}"
    return ActionResult(title, detail)


# ----------------------------------------------------------------- settings


def save_settings(
    debug: bool | None = None,
    language: str | None = None,
    concurrency: int | None = None,
    timeout_seconds: int | None = None,
    control_url: str | None = None,
    auto_refresh_on_run: bool | None = None,
    refresh_ttl_hours: int | None = None,
    use_control_resolver: bool | None = None,
    theme_mode: str | None = None,
) -> ActionResult:
    app_runtime = runtime()
    config = app_runtime.settings.config
    if debug is not None:
        config.app.debug = debug
    if language is not None:
        code = language.strip().lower()
        if code not in LANGUAGES:
            return ActionResult(
                t("Settings not saved"),
                t("Language must be one of: {options}.", options=", ".join(LANGUAGES)),
                ok=False,
            )
        config.app.language = code
        set_language(code)
    if theme_mode is not None:
        mode = theme_mode.strip().lower()
        if mode not in {"system", "light", "dark"}:
            return ActionResult(
                t("Settings not saved"),
                t("Choose system, light or dark appearance."),
                ok=False,
            )
        config.app.theme_mode = mode
    if concurrency is not None:
        if concurrency < 1 or concurrency > 100:
            return ActionResult(
                t("Settings not saved"),
                t("Concurrency must be between 1 and 100."),
                ok=False,
            )
        config.probe.concurrency = concurrency
    if timeout_seconds is not None:
        if timeout_seconds < 1 or timeout_seconds > 120:
            return ActionResult(
                t("Settings not saved"),
                t("Timeout must be between 1 and 120 seconds."),
                ok=False,
            )
        config.probe.timeout_seconds = timeout_seconds
    if control_url is not None:
        if not control_url.startswith(("http://", "https://")):
            return ActionResult(
                t("Settings not saved"),
                t("The control URL must start with http:// or https://."),
                ok=False,
            )
        config.probe.control_url = control_url
    if auto_refresh_on_run is not None:
        config.portals.auto_refresh_on_run = auto_refresh_on_run
    if refresh_ttl_hours is not None:
        if refresh_ttl_hours < 1:
            return ActionResult(
                t("Settings not saved"),
                t("The refresh window must be at least one hour."),
                ok=False,
            )
        config.portals.refresh_ttl_hours = refresh_ttl_hours
    if use_control_resolver is not None:
        config.probe.use_control_resolver = use_control_resolver
    try:
        app_runtime.settings.save()
    except BlockProbeError as exc:
        return ActionResult(t("Settings not saved"), str(exc), ok=False)
    return ActionResult(
        t("Settings saved"),
        t("Your changes will apply to the next scan."),
    )


# --------------------------------------------------------------- reset (FR-14)


def reset_website_list(confirm: str, include_custom: bool = False) -> ActionResult:
    app_runtime = runtime()
    safety = app_runtime.settings.config.safety
    expected = (
        safety.reset_all_confirm_word if include_custom else safety.reset_confirm_word
    )
    if confirm.strip() != expected:
        return ActionResult(
            t("Reset refused"),
            t(
                "Type {expected} exactly to confirm. Nothing was removed.",
                expected=repr(expected),
            ),
            ok=False,
        )
    try:
        report = app_runtime.storage.reset_list(
            app_runtime.settings.backups_dir,
            include_custom=include_custom,
            backup_required=safety.reset_backup_required,
        )
    except BlockProbeError as exc:
        return ActionResult(t("Reset refused"), str(exc), ok=False)
    except Exception as exc:
        return _failure(t("Reset failed"), exc)
    lines = [
        t("Removed {count} stored website(s).", count=report.removed_sites),
        t("Kept {count} custom website(s).", count=report.kept_custom),
    ]
    if report.backup_database:
        lines.append(t("Database backup: {path}", path=report.backup_database))
    if report.backup_domains:
        lines.append(t("Domain list backup: {path}", path=report.backup_domains))
    lines.append(t("Portal lists are fetched again on the next update."))
    return ActionResult(t("Website list reset"), "\n".join(lines))


def delete_all_data(confirm: str) -> ActionResult:
    app_runtime = runtime()
    expected = app_runtime.settings.config.safety.reset_all_confirm_word
    if confirm.strip() != expected:
        return ActionResult(
            t("Deletion refused"),
            t(
                "Type {expected} exactly to confirm. Nothing was removed.",
                expected=repr(expected),
            ),
            ok=False,
        )
    try:
        report = app_runtime.storage.delete_all_data()
        app_runtime.cache.clear()
        app_runtime.storage.clear_consent()
    except Exception as exc:
        return _failure(t("Deletion failed"), exc)
    lines = [
        t("Removed runs: {count}", count=report.removed_runs),
        t("Removed results: {count}", count=report.removed_results),
        t("Removed websites: {count}", count=report.removed_sites),
        t("The consent record was cleared, so the first-run flow appears again."),
    ]
    return ActionResult(t("All local data deleted"), "\n".join(lines))
