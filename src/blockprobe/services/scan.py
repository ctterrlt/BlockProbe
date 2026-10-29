# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start, pause, resume and cancel probe runs (FR-6, FR-7, FR-8).

A run is prepared from the stored website list, a connectivity guard is checked
before any probing, and results are persisted domain by domain. The service
never probes when the network guard says the device is offline: a run requested
in that state is stored as paused so it can be resumed later.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from blockprobe import __version__
from blockprobe.cache.base import Cache
from blockprobe.core.config import Settings
from blockprobe.core.enums import RunStatus
from blockprobe.core.errors import ConsentRequired, ProbeError
from blockprobe.core.models import (
    Classification,
    ProbeAttemptRecord,
    RefreshSummary,
    ResultRow,
    RunRecord,
    ScanSelection,
    Site,
    utc_now,
)
from blockprobe.diagnostics import collect_network_context
from blockprobe.probe.betblocker import SignatureRefresh, refresh_betblocker_signatures
from blockprobe.probe.classifier import load_signatures, sort_rules
from blockprobe.probe.health import ControlCheck, ProbeHealth, assess_probe_health
from blockprobe.probe.runner import ProbeEngine, RunControl, engine_from_settings
from blockprobe.services.refresh import RefreshService
from blockprobe.storage.base import Storage

_LOGGER = logging.getLogger("blockprobe.services.scan")

# Bump this string whenever the first-run consent text changes so every user
# sees the new wording before the app touches the network.
CONSENT_VERSION = "2026-10-19.1"
GUARD_EVERY = 20


@dataclass(slots=True)
class ScanPreview:
    """What a run would do, shown before the user confirms it."""

    selection: ScanSelection
    total: int
    custom_count: int
    portal_labels: list[str] = field(default_factory=list)
    stale_portals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScanOutcome:
    run_id: str
    status: RunStatus
    total: int
    completed: int
    message: str
    health: dict[str, Any] = field(default_factory=dict)


class ScanService:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        refresh: RefreshService,
        cache: Cache | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.refresh = refresh
        self.cache = cache
        self._lock = threading.RLock()
        self._active_run: RunRecord | None = None
        self._control: RunControl | None = None
        self._thread: threading.Thread | None = None
        self._static_rules = load_signatures(settings.signature_dirs)
        self._rules = list(self._static_rules)
        self._engine = ProbeEngine(
            engine_from_settings(settings.config, settings.config.debug),
            self._rules,
        )
        self._reload_dynamic_rules()

    # ------------------------------------------------------- dynamic signatures

    def _reload_dynamic_rules(self) -> None:
        dynamic = [item.to_rule() for item in self.storage.list_dynamic_signatures()]
        self._rules = sort_rules([*self._static_rules, *dynamic])
        self._engine.rules = self._rules

    async def refresh_dynamic_signatures(self) -> SignatureRefresh:
        """Fetch the live BetBlocker page and append new patterns.

        Failure is recorded in the run health and never aborts a scan: the
        statically verified rules and any previously stored patterns remain in
        force.
        """
        refresh = SignatureRefresh()
        try:
            refresh = await refresh_betblocker_signatures(
                self.storage,
                timeout=float(self.settings.config.probe.timeout_seconds),
                max_bytes=max(262_144, self.settings.config.probe.body_cap_bytes),
            )
        except Exception as exc:  # pragma: no cover - network/storage safety net
            refresh.error = f"{type(exc).__name__}: {exc}"
            _LOGGER.exception("dynamic signature refresh crashed")
        finally:
            self._reload_dynamic_rules()
        return refresh

    # ------------------------------------------------------------------ consent

    @property
    def consent_version(self) -> str:
        return CONSENT_VERSION

    def consent_required(self) -> bool:
        record = self.storage.get_consent()
        return record is None or record.version != CONSENT_VERSION

    def accept_consent(self) -> None:
        self.storage.save_consent(CONSENT_VERSION)
        _LOGGER.info("consent recorded", extra={"event": "consent_accepted"})

    # --------------------------------------------------------------- selection

    def build_selection(
        self,
        portal_ids: list[str] | None = None,
        with_custom: bool = False,
        all_sources: bool = False,
    ) -> ScanSelection:
        if all_sources:
            return ScanSelection(all_sources=True, with_custom=True)
        return ScanSelection(
            all_sources=False,
            portal_ids=list(portal_ids or []),
            with_custom=bool(with_custom),
        )

    def preview(self, selection: ScanSelection) -> ScanPreview:
        sites = self.storage.select_sites(selection)
        labels = {portal.uuid: portal.name for portal in self.storage.list_portals()}
        portal_ids = {pid for site in sites for pid in site.portal_ids}
        custom_count = sum(1 for site in sites if site.is_custom)
        stale_portals: list[str] = []
        for portal in self.storage.list_portals():
            if (
                portal.enabled
                and portal.is_stale(self.settings.config.portals.refresh_ttl_hours)
                and (selection.all_sources or portal.uuid in selection.portal_ids)
            ):
                stale_portals.append(portal.name)
        return ScanPreview(
            selection=selection,
            total=len(sites),
            custom_count=custom_count,
            portal_labels=[labels[pid] for pid in sorted(portal_ids) if pid in labels],
            stale_portals=stale_portals,
        )

    def refresh_if_requested(
        self,
        selection: ScanSelection,
        *,
        auto_refresh: bool,
    ) -> RefreshSummary | None:
        if not auto_refresh or not self.settings.config.portals.auto_refresh_on_run:
            return None
        ids = None if selection.all_sources else selection.portal_ids_only
        if not selection.all_sources and not ids:
            return None
        return self.refresh.refresh(ids, only_stale=True)

    # ------------------------------------------------------------------ running

    def prepare_run(
        self,
        selection: ScanSelection,
        *,
        auto_refresh: bool = True,
    ) -> tuple[RunRecord, list[Site]]:
        if self.consent_required():
            raise ConsentRequired(
                "The first-run consent has not been accepted. The app never "
                "touches the network before that."
            )
        self.refresh_if_requested(selection, auto_refresh=auto_refresh)
        sites = self.storage.select_sites(selection)
        if not sites:
            raise ProbeError(
                "No websites are selected. Refresh a portal or add a custom "
                "website, then start the scan again."
            )
        network_context = collect_network_context()
        run = self.storage.create_run(
            selection=selection,
            total=len(sites),
            app_version=__version__,
            os_name=f"{platform.system()} {platform.release()}".strip(),
            network_context=network_context.to_dict(),
            debug=self.settings.config.debug,
        )
        return run, sites

    @property
    def active_run(self) -> RunRecord | None:
        with self._lock:
            return self._active_run

    @property
    def control(self) -> RunControl | None:
        with self._lock:
            return self._control

    def start_run(
        self,
        selection: ScanSelection,
        *,
        auto_refresh: bool = True,
    ) -> tuple[RunRecord, int]:
        with self._lock:
            if self._active_run is not None and self._thread is not None and self._thread.is_alive():
                raise ProbeError(
                    "A scan is already running. Pause, cancel or wait for it to finish."
                )
            run, sites = self.prepare_run(selection, auto_refresh=auto_refresh)
            self._start_background(run, sites)
            return run, len(sites)

    def resume_run(self, run_id: str) -> tuple[RunRecord, int]:
        with self._lock:
            if self._active_run is not None and self._thread is not None and self._thread.is_alive():
                raise ProbeError("Another scan is already running.")
            run = self.storage.get_run(run_id)
            if run is None:
                raise ProbeError(f"Run {run_id} was not found.")
            if run.status not in {RunStatus.PAUSED, RunStatus.PENDING, RunStatus.FAILED}:
                raise ProbeError(
                    f"Run {run_id} is {run.status.value} and cannot be resumed."
                )
            selection = self._selection_from_sources(run.sources)
            sites = self.storage.select_sites(selection)
            done = {result.domain for result in self.storage.get_results(run_id)}
            remaining = [site for site in sites if site.domain not in done]
            if not remaining:
                self.storage.update_run_status(run_id, RunStatus.COMPLETED, utc_now())
                raise ProbeError("This run has no unfinished domains left.")
            self.storage.update_run_status(run_id, RunStatus.RUNNING)
            self._start_background(run, remaining)
            return run, len(remaining)

    def _selection_from_sources(self, sources: dict[str, Any]) -> ScanSelection:
        portal_ids = [str(item) for item in sources.get("portal_ids") or []]
        return ScanSelection(
            all_sources=bool(sources.get("all")),
            portal_ids=portal_ids,
            with_custom=bool(sources.get("with_custom")),
        )

    def _start_background(self, run: RunRecord, sites: list[Site]) -> None:
        control = RunControl()
        self._active_run = run
        self._control = control
        thread = threading.Thread(
            target=self._thread_main,
            args=(run, sites, control),
            name=f"blockprobe-scan-{run.id[:8]}",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _thread_main(self, run: RunRecord, sites: list[Site], control: RunControl) -> None:
        try:
            outcome = asyncio.run(self.execute(run, sites, control))
            if self.cache is not None:
                self.cache.delete_tag(f"run:{run.id}")
            _LOGGER.info(
                "scan finished",
                extra={
                    "event": "run_finished",
                    "run_id": run.id,
                    "domain": outcome.status.value,
                },
            )
        except Exception as exc:  # pragma: no cover - background thread safety net
            _LOGGER.exception("scan crashed: %s", exc)
            try:
                self.storage.update_run_status(run.id, RunStatus.FAILED, utc_now())
            except Exception:
                pass
        finally:
            with self._lock:
                self._active_run = None
                self._control = None

    def _persist_health(
        self,
        run: RunRecord,
        controls: list[ControlCheck],
        refresh: SignatureRefresh,
    ) -> ProbeHealth:
        results = self.storage.get_results(run.id)
        health = assess_probe_health(results, controls, run.network_context)
        health.signature_refresh = refresh.to_dict()
        self.storage.save_run_health(run.id, health.to_dict())
        return health

    async def execute(
        self,
        run: RunRecord,
        sites: list[Site],
        control: RunControl,
    ) -> ScanOutcome:
        total = len(sites)
        storage = self.storage
        storage.update_run_status(run.id, RunStatus.RUNNING)
        baseline_completed = storage.count_results(run.id)
        run_total = max((storage.get_run(run.id) or run).total, baseline_completed + total)
        _LOGGER.info(
            "scan started",
            extra={"event": "run_started", "run_id": run.id, "domain": str(total)},
        )

        refresh = await self.refresh_dynamic_signatures()
        previous_health = ProbeHealth.from_dict(
            (storage.get_run(run.id) or run).health
        )
        controls = list(previous_health.controls)
        next_sequence = max((item.sequence for item in controls), default=0) + 1
        initial = await self._engine.check_control(
            self._engine.control_target(next_sequence),
            after_domain="",
            sequence=next_sequence,
        )
        controls.append(initial)
        if not initial.ok:
            # The run ends before any target is probed; this is the final
            # snapshot for this aborted attempt.
            health = self._persist_health(run, controls, refresh)
            storage.update_run_status(run.id, RunStatus.PAUSED)
            return ScanOutcome(
                run_id=run.id,
                status=RunStatus.PAUSED,
                total=run_total,
                completed=storage.count_results(run.id),
                message=(
                    "Network connectivity guard failed before the run started: "
                    f"{initial.url} did not answer ({initial.error or 'no status'})."
                ),
                health=health.to_dict(),
            )
        next_sequence += 1

        def on_result(site: Site, classification: Classification, attempts: list[ProbeAttemptRecord]) -> None:
            checked_at = utc_now()
            http_evidence = classification.evidence.get("http") or {}
            redirect_chain = (
                http_evidence.get("redirect_chain")
                if isinstance(http_evidence, dict)
                else []
            )
            result = ResultRow(
                run_id=run.id,
                domain=site.domain,
                verdict=classification.verdict,
                reason_code=classification.reason_code,
                confidence=classification.confidence,
                blocker_hint=classification.blocker_hint,
                needs_review=classification.needs_review,
                evidence=classification.evidence,
                checked_at=checked_at,
                hosts=list(site.hosts),
                sources=site.source_labels,
                title=classification.title,
                redirect_chain=[str(url) for url in redirect_chain or []],
            )
            storage.save_site_result(result, attempts)
            if self.cache is not None:
                try:
                    self.cache.delete_tag(f"run:{run.id}")
                except Exception:  # pragma: no cover - cache must never break a scan
                    pass

        def on_progress(
            completed: int,
            total_count: int,
            site: Site,
            classification: Classification,
        ) -> None:
            _LOGGER.debug(
                "scan progress",
                extra={
                    "event": "progress",
                    "domain": site.domain,
                    "verdict": classification.verdict.value,
                    "completed": completed,
                    "total": total_count,
                },
            )

        def on_control(check: ControlCheck) -> None:
            # Collect controls during the run. The risk percentage is computed
            # once after run_batch from the complete result set, so a partial
            # snapshot can never be mistaken for the final verdict.
            controls.append(check)

        await self._engine.run_batch(
            run.id,
            sites,
            control,
            on_result,
            on_progress,
            on_control=on_control,
            control_every=self.settings.config.probe.control_every,
            # ``run_batch`` increments before its first check; the last used
            # sequence is the initial control, so the next one is 2.
            control_start_sequence=next_sequence - 1,
        )
        completed = storage.count_results(run.id)
        if control.cancelled:
            status = RunStatus.CANCELLED
            storage.update_run_status(run.id, status, utc_now())
            message = f"Scan cancelled after {completed} of {run_total} domains."
        elif completed - baseline_completed < total:
            status = RunStatus.PAUSED
            storage.update_run_status(run.id, status)
            message = (
                f"Scan paused after {completed} of {run_total} domains because a "
                "neutral control check failed. Resume it when the network is back."
            )
        else:
            status = RunStatus.COMPLETED
            storage.update_run_status(run.id, status, utc_now())
            message = f"Scan finished: {completed} of {run_total} domains checked."
        health = self._persist_health(run, controls, refresh)
        return ScanOutcome(
            run_id=run.id,
            status=status,
            total=run_total,
            completed=completed,
            message=message,
            health=health.to_dict(),
        )

    # -------------------------------------------------------------- run control

    def pause(self) -> bool:
        control = self.control
        run = self.active_run
        if control is None or run is None:
            return False
        control.pause()
        self.storage.update_run_status(run.id, RunStatus.PAUSED)
        return True

    def resume(self) -> bool:
        control = self.control
        run = self.active_run
        if control is None or run is None:
            return False
        control.resume()
        self.storage.update_run_status(run.id, RunStatus.RUNNING)
        return True

    def cancel(self) -> bool:
        control = self.control
        run = self.active_run
        if control is None or run is None:
            return False
        control.cancel()
        return True

    def recover_stale_runs(self) -> int:
        """Mark runs left 'running' by a crash as paused so they can resume."""
        count = 0
        for run in self.storage.list_runs(limit=50):
            if run.status is RunStatus.RUNNING:
                self.storage.update_run_status(run.id, RunStatus.PAUSED)
                count += 1
        if count:
            _LOGGER.warning(
                "recovered interrupted runs",
                extra={"event": "recover_runs", "domain": str(count)},
            )
        return count

    # ------------------------------------------------------------ result shims

    def result_rows(self, run_id: str, filters=None) -> list[ResultRow]:  # type: ignore[no-untyped-def]
        from blockprobe.storage.base import ResultFilter

        filters = filters or ResultFilter()
        return self.storage.get_results(run_id, filters)

    def summary(self, run_id: str):  # type: ignore[no-untyped-def]
        return self.storage.get_run_summary(run_id)


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def process_id() -> int:
    try:
        return os.getpid()
    except OSError:  # pragma: no cover
        return 0


def new_run_id() -> str:
    return str(uuid.uuid4())
