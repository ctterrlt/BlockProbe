# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage backend interface implemented by the local SQLite backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blockprobe.core.enums import RunStatus, Verdict
from blockprobe.core.models import (
    CapturedSignature,
    ConsentRecord,
    DynamicSignature,
    ExtractedDomain,
    Portal,
    ProbeAttemptRecord,
    RefreshDiff,
    ResultRow,
    RunRecord,
    RunSummary,
    ScanSelection,
    Site,
)


@dataclass(slots=True)
class ResultFilter:
    verdict: Verdict | None = None
    search: str = ""
    needs_review: bool | None = None
    leaks_only: bool = False
    sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CustomDiff:
    added: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ResetReport:
    backup_database: Path | None = None
    backup_domains: Path | None = None
    removed_sites: int = 0
    removed_hosts: int = 0
    removed_links: int = 0
    removed_custom: int = 0
    kept_custom: int = 0
    include_custom: bool = False


@dataclass(slots=True)
class WipeReport:
    removed_results: int = 0
    removed_runs: int = 0
    removed_sites: int = 0
    removed_custom: int = 0
    removed_consents: int = 0
    removed_signatures: int = 0


class Storage(ABC):
    """Persistence contract. All timestamps are UTC Unix seconds."""

    @abstractmethod
    def initialize(self) -> None:
        """Create or migrate the schema."""

    @abstractmethod
    def close(self) -> None:
        """Release resources. The database must remain usable after reopening."""

    @property
    @abstractmethod
    def generation(self) -> int:
        """Cache generation; incremented in the same transaction as writes."""

    @abstractmethod
    def upsert_portals(self, portals: list[Portal]) -> None:
        """Insert missing portal definitions and refresh their metadata."""

    @abstractmethod
    def list_portals(self) -> list[Portal]:
        ...

    @abstractmethod
    def get_portal(self, portal_id: str) -> Portal | None:
        ...

    @abstractmethod
    def record_refresh(self, diff: RefreshDiff) -> None:
        """Write a refresh log entry and update the portal date on success."""

    @abstractmethod
    def add_portal_domains(
        self,
        portal_id: str,
        candidates: list[ExtractedDomain],
        source_url: str,
    ) -> tuple[list[str], list[str]]:
        """Append new domains and links. Returns (new, already_known) domains."""

    @abstractmethod
    def list_sites(
        self,
        portal_ids: list[str] | None = None,
        with_custom: bool = True,
        include_excluded: bool = False,
    ) -> list[Site]:
        """Return stored sites with their hosts and source labels."""

    @abstractmethod
    def select_sites(self, selection: ScanSelection) -> list[Site]:
        """Apply the FR-6 source selection."""

    @abstractmethod
    def count_sites(self) -> int:
        ...

    @abstractmethod
    def count_custom_sites(self) -> int:
        ...

    @abstractmethod
    def stale_site_count(self, days: int = 90) -> int:
        """Portal-sourced domains not seen in any portal for N days."""

    @abstractmethod
    def add_custom_domains(
        self,
        domains: list[tuple[str, str]],
        note: str = "",
    ) -> CustomDiff:
        """Add (domain, host) pairs to the custom list and return the diff."""

    @abstractmethod
    def list_custom_sites(self) -> list[Site]:
        ...

    @abstractmethod
    def remove_custom_site(self, domain: str) -> bool:
        """Remove one custom entry; keep it if a portal also lists the domain."""

    @abstractmethod
    def set_excluded(self, domain: str, excluded: bool) -> None:
        ...

    @abstractmethod
    def create_run(
        self,
        selection: ScanSelection,
        total: int,
        app_version: str,
        os_name: str,
        network_context: dict[str, Any],
        debug: bool,
    ) -> RunRecord:
        ...

    @abstractmethod
    def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        finished_at: int | None = None,
    ) -> None:
        ...

    @abstractmethod
    def save_run_health(self, run_id: str, health: dict[str, Any]) -> None:
        """Persist the neutral-control timeline and the interference risk."""

    @abstractmethod
    def get_run(self, run_id: str) -> RunRecord | None:
        ...

    @abstractmethod
    def latest_run(self) -> RunRecord | None:
        ...

    @abstractmethod
    def list_runs(self, limit: int = 20) -> list[RunRecord]:
        ...

    @abstractmethod
    def save_site_result(
        self,
        result: ResultRow,
        attempts: list[ProbeAttemptRecord],
    ) -> None:
        ...

    @abstractmethod
    def get_results(self, run_id: str, filters: ResultFilter | None = None) -> list[ResultRow]:
        ...

    @abstractmethod
    def get_run_summary(self, run_id: str) -> RunSummary:
        ...

    @abstractmethod
    def count_results(self, run_id: str) -> int:
        ...

    @abstractmethod
    def get_consent(self) -> ConsentRecord | None:
        ...

    @abstractmethod
    def save_consent(self, version: str) -> None:
        ...

    @abstractmethod
    def clear_consent(self) -> None:
        ...

    @abstractmethod
    def add_dynamic_signatures(self, signatures: list[DynamicSignature]) -> list[str]:
        """Insert new append-only patterns and refresh their last-seen time.

        Returns the patterns that were not stored before. Existing patterns are
        never modified or removed by this method.
        """

    @abstractmethod
    def list_dynamic_signatures(self) -> list[DynamicSignature]:
        ...

    @abstractmethod
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        ...

    @abstractmethod
    def set_meta(self, key: str, value: str) -> None:
        ...

    @abstractmethod
    def restart_generation(self) -> int:
        """Increment and return the cache generation."""

    @abstractmethod
    def backup_database(self, destination: Path) -> Path:
        ...

    @abstractmethod
    def export_domain_list(self, destination: Path) -> Path:
        """Plain-text export of every stored domain, custom included."""

    @abstractmethod
    def reset_list(
        self,
        backup_dir: Path,
        include_custom: bool,
        backup_required: bool = True,
    ) -> ResetReport:
        ...

    @abstractmethod
    def delete_all_data(self) -> WipeReport:
        ...

    @abstractmethod
    def health_check(self) -> str:
        ...

    @abstractmethod
    def save_signature(self, signature: CapturedSignature) -> Path:
        ...
