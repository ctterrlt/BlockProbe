# SPDX-License-Identifier: AGPL-3.0-or-later
"""Use cases shared by the Flet UI, CLI and local web UI.

The :class:`Runtime` owns the process-wide configuration, storage, cache and
services. Building it is the single place that configures logging, creates
directories, seeds the portal table and recovers interrupted runs.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from blockprobe.cache import build_cache
from blockprobe.cache.base import Cache
from blockprobe.core.config import Settings, load_config
from blockprobe.core.errors import StorageError
from blockprobe.core.models import Portal
from blockprobe.i18n import set_language
from blockprobe.logging_setup import configure_logging
from blockprobe.services.refresh import RefreshService
from blockprobe.services.report import ReportService
from blockprobe.services.scan import CONSENT_VERSION, ScanService
from blockprobe.services.signature import SignatureService
from blockprobe.storage import Storage, build_storage

_LOGGER = logging.getLogger("blockprobe.services")

__all__ = [
    "CONSENT_VERSION",
    "RefreshService",
    "ReportService",
    "Runtime",
    "ScanService",
    "SignatureService",
    "build_runtime",
    "get_runtime",
    "reset_runtime",
]


@dataclass
class Runtime:
    settings: Settings
    storage: Storage
    cache: Cache
    refresh: RefreshService
    scan: ScanService
    report: ReportService
    signature: SignatureService
    portals: list[Portal] = field(default_factory=list)

    def close(self) -> None:
        try:
            self.storage.close()
        finally:
            try:
                self.cache.close()
            except Exception:  # pragma: no cover - best-effort shutdown
                pass

    def portal_choices(self) -> list[tuple[str, str]]:
        """(id, label) pairs for dropdowns, default portal first."""
        return [(portal.uuid, portal.name) for portal in self.portals if portal.enabled]

    def portal_label(self, portal_id: str) -> str:
        for portal in self.portals:
            if portal.uuid == portal_id:
                return portal.name
        stored = self.storage.get_portal(portal_id)
        return stored.name if stored else portal_id


_runtime: Runtime | None = None
_runtime_lock = threading.RLock()


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or load_config()
    set_language(settings.config.app.language)
    settings.ensure_directories()
    configure_logging(settings.logs_dir, settings.config.debug)
    storage = build_storage(settings)
    cache = build_cache(settings)
    refresh = RefreshService(settings, storage)
    portals = refresh.sync_portals()
    scan = ScanService(settings, storage, refresh, cache)
    scan.recover_stale_runs()
    report = ReportService(settings, storage, cache)
    signature = SignatureService(settings, storage)
    runtime = Runtime(
        settings=settings,
        storage=storage,
        cache=cache,
        refresh=refresh,
        scan=scan,
        report=report,
        signature=signature,
        portals=portals,
    )
    _LOGGER.info(
        "runtime ready",
        extra={"event": "runtime_ready", "domain": str(settings.data_dir)},
    )
    return runtime


def get_runtime(settings: Settings | None = None) -> Runtime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = build_runtime(settings)
        return _runtime


def reset_runtime() -> None:
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close()
            _runtime = None


def storage_error_message(exc: StorageError) -> str:
    return str(exc)
