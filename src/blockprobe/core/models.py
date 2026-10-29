# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core data models shared by storage, services, CLI and UI.

The models are deliberately plain dataclasses: they are easy to serialise to
SQLite rows or JSON and they contain no Flet or network code. Dates are UTC Unix
seconds as required by the data model in Agents.md.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blockprobe.core.enums import (
    Confidence,
    Origin,
    ReasonCode,
    RefreshStatus,
    RunStatus,
    SignatureKind,
    Verdict,
)

# The built-in ADM portal gets a stable UUID so repeated fresh installs, tests
# and the bundled TOML template always refer to the same database row.
ADM_PORTAL_UUID = "44d93121-094d-4b4c-9e87-1c59380ec20f"
ADM_START_URL = (
    "https://www.adm.gov.it/portale/monopoli/giochi/gioco_distanza/"
    "gioco_dist_concessionari"
)


def utc_now() -> int:
    """Return the current UTC time as Unix seconds."""
    return int(datetime.now(UTC).timestamp())


def utc_iso(timestamp: int | float | None) -> str:
    """Format a UTC Unix timestamp for humans and exports."""
    if not timestamp:
        return ""
    return datetime.fromtimestamp(float(timestamp), UTC).isoformat(timespec="seconds")


def json_default(value: Any) -> Any:
    """JSON encoder fallback for dataclasses, paths and enums."""
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serialisable")


def to_json(value: Any) -> str:
    return json.dumps(value, default=json_default, ensure_ascii=False)


@dataclass(slots=True)
class Portal:
    """A portal definition plus its local refresh state."""

    uuid: str
    name: str
    start_url: str
    country: str = "IT"
    enabled: bool = True
    default: bool = False
    strategy: str = "auto"
    max_pages: int = 50
    rate_limit_per_sec: float = 1.0
    respect_robots: bool = True
    exclude_domains: tuple[str, ...] = ()
    include_regex: tuple[str, ...] = ()
    last_refreshed_at: int = 0
    last_attempt_at: int = 0
    last_error: str | None = None
    source_path: Path | None = None

    @property
    def id(self) -> str:
        return self.uuid

    @property
    def slug(self) -> str:
        if self.source_path is not None:
            return self.source_path.stem
        return self.uuid

    def is_stale(self, ttl_hours: int, now: int | None = None) -> bool:
        """A future date counts as stale, exactly like a never-updated one."""
        current = utc_now() if now is None else now
        if self.last_refreshed_at <= 0 or self.last_refreshed_at > current:
            return True
        return (current - self.last_refreshed_at) >= ttl_hours * 3600

    def in_retry_cooldown(self, cooldown_minutes: int, now: int | None = None) -> bool:
        current = utc_now() if now is None else now
        if not self.last_error or self.last_attempt_at <= 0:
            return False
        return (current - self.last_attempt_at) < cooldown_minutes * 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "start_url": self.start_url,
            "country": self.country,
            "enabled": self.enabled,
            "default": self.default,
            "strategy": self.strategy,
            "max_pages": self.max_pages,
            "rate_limit_per_sec": self.rate_limit_per_sec,
            "respect_robots": self.respect_robots,
            "exclude_domains": list(self.exclude_domains),
            "include_regex": list(self.include_regex),
            "last_refreshed_at": self.last_refreshed_at,
            "last_attempt_at": self.last_attempt_at,
            "last_error": self.last_error,
            "source_path": str(self.source_path) if self.source_path else None,
        }

    @classmethod
    def from_toml(cls, data: dict[str, Any], source_path: Path | None = None) -> Portal:
        source = data.get("source") or {}
        if not isinstance(source, dict):
            source = {}
        exclude = source.get("exclude_domains") or []
        include = source.get("include_regex") or []
        uuid_value = str(data.get("uuid") or uuid.uuid4())
        return cls(
            uuid=uuid_value,
            name=str(data.get("name") or "Unnamed portal"),
            start_url=str(source.get("start_url") or data.get("start_url") or ""),
            country=str(source.get("country") or data.get("country") or "IT"),
            enabled=bool(data.get("enabled", True)),
            default=bool(data.get("default", False)),
            strategy=str(source.get("strategy") or "auto"),
            max_pages=max(1, int(source.get("max_pages") or 50)),
            rate_limit_per_sec=max(0.1, float(source.get("rate_limit_per_sec") or 1.0)),
            respect_robots=bool(source.get("respect_robots", True)),
            exclude_domains=tuple(str(item).lower() for item in exclude),
            include_regex=tuple(str(item) for item in include),
            source_path=source_path,
        )

    def to_toml(self) -> str:
        """Render the definition as the project's TOML template."""
        lines = [
            "# SPDX-License-Identifier: AGPL-3.0-or-later",
            "# Purpose: BlockProbe portal definition.",
            f"uuid = {self.uuid!r}",
            f"name = {self.name!r}",
            f"enabled = {str(self.enabled).lower()}",
            f"default = {str(self.default).lower()}",
            "",
            "[source]",
            f"country = {self.country!r}",
            f"start_url = {self.start_url!r}",
            f"strategy = {self.strategy!r}",
            f"max_pages = {self.max_pages}",
            f"rate_limit_per_sec = {self.rate_limit_per_sec:g}",
            f"respect_robots = {str(self.respect_robots).lower()}",
            f"exclude_domains = {json.dumps(list(self.exclude_domains))}",
            f"include_regex = {json.dumps(list(self.include_regex))}",
            "",
        ]
        return "\n".join(lines)


def builtin_adm_portal() -> Portal:
    """The shipped ADM definition used when no portal directory is available."""
    return Portal(
        uuid=ADM_PORTAL_UUID,
        name="ADM - Concessionari gioco a distanza",
        start_url=ADM_START_URL,
        country="IT",
        enabled=True,
        default=True,
        exclude_domains=("adm.gov.it",),
        source_path=None,
    )


@dataclass(slots=True)
class Site:
    domain: str
    hosts: list[str] = field(default_factory=list)
    portal_ids: list[str] = field(default_factory=list)
    portal_names: list[str] = field(default_factory=list)
    first_seen_at: int = 0
    last_seen_at: int = 0
    excluded: bool = False
    is_custom: bool = False

    @property
    def source_labels(self) -> list[str]:
        labels = list(self.portal_names)
        if self.is_custom:
            labels.append("Custom")
        return labels


@dataclass(slots=True)
class ExtractedDomain:
    host: str
    domain: str
    origin: Origin
    source_url: str = ""
    context: str = ""


@dataclass(slots=True)
class PageExtraction:
    url: str
    candidates: list[ExtractedDomain] = field(default_factory=list)
    next_links: list[str] = field(default_factory=list)
    stated_total: int | None = None
    content_hash: str = ""
    title: str = ""
    text_length: int = 0

    @property
    def domains(self) -> set[str]:
        return {candidate.domain for candidate in self.candidates}


@dataclass(slots=True)
class RefreshDiff:
    portal_id: str
    portal_name: str
    status: RefreshStatus
    pages: int = 0
    found: int = 0
    added: int = 0
    new_domains: list[str] = field(default_factory=list)
    already_known: list[str] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    started_at: int = 0
    finished_at: int = 0

    @property
    def ok(self) -> bool:
        return self.status is RefreshStatus.OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "portal_id": self.portal_id,
            "portal_name": self.portal_name,
            "status": self.status.value,
            "pages": self.pages,
            "found": self.found,
            "added": self.added,
            "new_domains": self.new_domains,
            "already_known": self.already_known,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "warnings": self.warnings,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def message(self) -> str:
        if self.status is RefreshStatus.OK:
            return (
                f"{self.portal_name}: {self.found} domains on {self.pages} pages, "
                f"{self.added} new"
            )
        if self.status is RefreshStatus.EMPTY:
            return f"{self.portal_name}: no domains found — existing list kept"
        if self.status is RefreshStatus.COOLDOWN:
            return f"{self.portal_name}: retry cooldown active — kept the stored list"
        if self.status is RefreshStatus.SKIPPED:
            return f"{self.portal_name}: update skipped"
        return f"{self.portal_name}: update failed — {self.error or 'unknown error'}"


@dataclass(slots=True)
class RefreshSummary:
    results: list[RefreshDiff] = field(default_factory=list)
    started_at: int = 0
    finished_at: int = 0

    @property
    def added_total(self) -> int:
        return sum(item.added for item in self.results)

    @property
    def found_total(self) -> int:
        return sum(item.found for item in self.results)

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.results if item.status is RefreshStatus.OK)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "added_total": self.added_total,
            "found_total": self.found_total,
            "results": [item.to_dict() for item in self.results],
        }


@dataclass(slots=True)
class RunRecord:
    id: str
    started_at: int
    status: RunStatus
    sources: dict[str, Any]
    total: int
    finished_at: int = 0
    app_version: str = ""
    os_name: str = ""
    network_context: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    debug: bool = False


@dataclass(slots=True)
class RunSummary:
    run_id: str
    status: RunStatus
    total: int
    started_at: int
    finished_at: int
    blocked: int = 0
    unreachable: int = 0
    opened: int = 0
    needs_review: int = 0

    @property
    def blocked_percent(self) -> float:
        checked = self.blocked + self.unreachable + self.opened
        if checked <= 0:
            return 0.0
        return round(self.blocked * 100.0 / checked, 1)


@dataclass(slots=True)
class ResultRow:
    run_id: str
    domain: str
    verdict: Verdict
    reason_code: str
    confidence: Confidence
    blocker_hint: str | None
    needs_review: bool
    evidence: dict[str, Any]
    checked_at: int
    hosts: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    title: str = ""
    redirect_chain: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.verdict.label

    @property
    def reason_label(self) -> str:
        try:
            return ReasonCode(self.reason_code).label
        except ValueError:
            return self.reason_code


@dataclass(slots=True)
class RunComparison:
    run_id: str
    previous_run_id: str | None
    newly_leaking: list[ResultRow] = field(default_factory=list)
    newly_blocked: list[ResultRow] = field(default_factory=list)
    still_leaking: list[ResultRow] = field(default_factory=list)
    resolved: list[ResultRow] = field(default_factory=list)


@dataclass(slots=True)
class NetworkContext:
    timestamp: int = 0
    os_name: str = ""
    app_version: str = ""
    resolvers: list[str] = field(default_factory=list)
    resolver_labels: list[str] = field(default_factory=list)
    dns_filters: list[str] = field(default_factory=list)
    hosts_entries: list[str] = field(default_factory=list)
    hosts_block_entries: int = 0
    proxies: list[str] = field(default_factory=list)
    vpn_interfaces: list[str] = field(default_factory=list)
    vpn_providers: list[str] = field(default_factory=list)
    ipv6: bool = False
    private_dns: str = "unknown"
    browser_doh: str = "unknown"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "os_name": self.os_name,
            "app_version": self.app_version,
            "resolvers": self.resolvers,
            "resolver_labels": self.resolver_labels,
            "dns_filters": self.dns_filters,
            "hosts_entries": self.hosts_entries,
            "hosts_block_entries": self.hosts_block_entries,
            "proxies": self.proxies,
            "vpn_interfaces": self.vpn_interfaces,
            "vpn_providers": self.vpn_providers,
            "ipv6": self.ipv6,
            "private_dns": self.private_dns,
            "browser_doh": self.browser_doh,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NetworkContext:
        data = data or {}
        return cls(
            timestamp=int(data.get("timestamp") or 0),
            os_name=str(data.get("os_name") or ""),
            app_version=str(data.get("app_version") or ""),
            resolvers=list(data.get("resolvers") or []),
            resolver_labels=list(data.get("resolver_labels") or []),
            dns_filters=list(data.get("dns_filters") or []),
            hosts_entries=list(data.get("hosts_entries") or []),
            hosts_block_entries=int(data.get("hosts_block_entries") or 0),
            proxies=list(data.get("proxies") or []),
            vpn_interfaces=list(data.get("vpn_interfaces") or []),
            vpn_providers=list(data.get("vpn_providers") or []),
            ipv6=bool(data.get("ipv6", False)),
            private_dns=str(data.get("private_dns") or "unknown"),
            browser_doh=str(data.get("browser_doh") or "unknown"),
            notes=list(data.get("notes") or []),
        )


@dataclass(slots=True)
class DnsResult:
    host: str
    status: str
    ips: list[str] = field(default_factory=list)
    cnames: list[str] = field(default_factory=list)
    error: str | None = None
    sinkhole_ips: list[str] = field(default_factory=list)
    resolver: str = "system"

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.ips)

    @property
    def sinkhole(self) -> bool:
        return bool(self.sinkhole_ips)


@dataclass(slots=True)
class ConnectResult:
    host: str
    ip: str
    port: int
    ok: bool
    connect_ms: int = 0
    error_kind: str | None = None
    error: str | None = None
    tls: bool = False
    attempts: int = 0


@dataclass(slots=True)
class HttpFetchResult:
    url: str
    final_url: str
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    body_bytes: int = 0
    body_sha256: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    redirect_statuses: list[int] = field(default_factory=list)
    embedded_redirects: list[str] = field(default_factory=list)
    reopens_on_return: bool = False
    elapsed_ms: int = 0
    error: str | None = None
    error_kind: str | None = None
    encoding: str = ""
    truncated: bool = False
    title: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None


@dataclass(slots=True)
class Classification:
    verdict: Verdict
    reason_code: str
    confidence: Confidence
    blocker_hint: str | None = None
    needs_review: bool = False
    rule_id: str | None = None
    title: str = ""
    snippet: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason_code": self.reason_code,
            "confidence": self.confidence.value,
            "blocker_hint": self.blocker_hint,
            "needs_review": self.needs_review,
            "rule_id": self.rule_id,
            "title": self.title,
            "snippet": self.snippet,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class ProbeAttemptRecord:
    run_id: str
    domain: str
    host: str
    scheme: str
    ip: str = ""
    dns_status: str = ""
    connect_ms: int = 0
    http_status: int | None = None
    redirect_chain: list[str] = field(default_factory=list)
    rule_id: str | None = None
    body_sha256: str = ""
    error: str | None = None
    verdict: Verdict = Verdict.UNREACHABLE
    reason_code: str = ""
    confidence: Confidence = Confidence.LOW
    blocker_hint: str | None = None
    needs_review: bool = False


@dataclass(slots=True)
class SignatureRule:
    id: str
    kind: SignatureKind
    title_patterns: list[str] = field(default_factory=list)
    body_patterns: list[str] = field(default_factory=list)
    header_patterns: list[str] = field(default_factory=list)
    redirect_patterns: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    blocker_hint: str | None = None
    note: str = ""
    source: str = ""
    needs_review: bool = False

    @classmethod
    def from_toml(cls, data: dict[str, Any], origin: str = "") -> SignatureRule:
        kind_value = str(data.get("kind") or "block").lower()
        try:
            kind = SignatureKind(kind_value)
        except ValueError:
            kind = SignatureKind.BLOCK
        confidence_value = str(data.get("confidence") or "medium").lower()
        try:
            confidence = Confidence(confidence_value)
        except ValueError:
            confidence = Confidence.MEDIUM
        return cls(
            id=str(data.get("id") or f"rule-{origin}"),
            kind=kind,
            title_patterns=[str(item) for item in data.get("title") or []],
            body_patterns=[str(item) for item in data.get("body") or []],
            header_patterns=[str(item) for item in data.get("headers") or []],
            redirect_patterns=[str(item) for item in data.get("redirect") or []],
            confidence=confidence,
            blocker_hint=data.get("hint") or data.get("blocker_hint"),
            note=str(data.get("note") or ""),
            source=origin,
            needs_review=bool(data.get("needs_review", False)),
        )


@dataclass(slots=True)
class DynamicSignature:
    """A locally stored, append-only block-page pattern.

    Dynamic patterns are evidence extracted from a live blocker page. They are
    never deleted by a normal run; only the explicit privacy wipe removes them.
    """

    pattern: str
    source: str = ""
    note: str = ""
    confidence: Confidence = Confidence.MEDIUM
    blocker_hint: str | None = None
    needs_review: bool = False
    first_seen_at: int = 0
    last_seen_at: int = 0

    def to_rule(self) -> SignatureRule:
        digest = hashlib.sha256(self.pattern.encode("utf-8")).hexdigest()[:16]
        return SignatureRule(
            id=f"dynamic-{digest}",
            kind=SignatureKind.BLOCK,
            body_patterns=[self.pattern],
            confidence=self.confidence,
            blocker_hint=self.blocker_hint,
            note=self.note,
            source=self.source or "dynamic",
            needs_review=self.needs_review,
        )


@dataclass(slots=True)
class CapturedSignature:
    domain: str
    captured_at: int
    status: int | None
    title: str
    final_url: str
    redirect_chain: list[str]
    headers: dict[str, str]
    ips: list[str]
    body_sha256: str
    key_phrases: list[str]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "captured_at": self.captured_at,
            "captured_at_iso": utc_iso(self.captured_at),
            "status": self.status,
            "title": self.title,
            "final_url": self.final_url,
            "redirect_chain": self.redirect_chain,
            "headers": self.headers,
            "ips": self.ips,
            "body_sha256": self.body_sha256,
            "key_phrases": self.key_phrases,
            "note": self.note,
        }

    def to_toml(self) -> str:
        """Render a fingerprint that a human can review before turning it into a rule."""
        header_lines = "\n".join(f"{key} = {value!r}" for key, value in self.headers.items())
        phrase_lines = "\n".join(f"{phrase!r}," for phrase in self.key_phrases)
        redirect_lines = "\n".join(f"{url!r}," for url in self.redirect_chain)
        ip_lines = "\n".join(f"{ip!r}," for ip in self.ips)
        return "\n".join(
            [
                "# SPDX-License-Identifier: AGPL-3.0-or-later",
                "# Purpose: Captured response fingerprint. Review before adding rules.",
                "#",
                "# This file is evidence, not an active signature. Add verified [[rules]]",
                "# entries only when you can reproduce the response on your own device.",
                "",
                "[fingerprint]",
                f"domain = {self.domain!r}",
                f"captured_at = {self.captured_at}",
                f"captured_at_iso = {utc_iso(self.captured_at)!r}",
                f"status = {self.status!r}",
                f"title = {self.title!r}",
                f"final_url = {self.final_url!r}",
                f"body_sha256 = {self.body_sha256!r}",
                f"note = {self.note!r}",
                "",
                "redirect_chain = [",
                redirect_lines,
                "]",
                "",
                "ips = [",
                ip_lines,
                "]",
                "",
                "key_phrases = [",
                phrase_lines,
                "]",
                "",
                "[fingerprint.headers]",
                header_lines,
                "",
                "# Example rule, disabled until a real response is verified:",
                "# [[rules]]",
                "# id = 'example-block-page'",
                "# kind = 'block'",
                "# hint = 'unknown'",
                '# title = ["example blocker"]',
                '# body = ["access denied"]',
                "# confidence = 'high'",
                "",
            ]
        )


@dataclass(slots=True)
class ConsentRecord:
    version: str
    accepted_at: int


@dataclass(slots=True)
class ScanSelection:
    """The parsed source selection for a run."""

    all_sources: bool
    portal_ids: list[str] = field(default_factory=list)
    with_custom: bool = False

    @property
    def portal_ids_only(self) -> list[str]:
        return [] if self.all_sources else self.portal_ids

    def to_dict(self, labels: dict[str, str] | None = None) -> dict[str, Any]:
        labels = labels or {}
        return {
            "all": self.all_sources,
            "portal_ids": self.portal_ids,
            "with_custom": self.with_custom,
            "portal_labels": [labels.get(pid, pid) for pid in self.portal_ids],
        }


@dataclass(slots=True)
class ProbeObservation:
    domain: str
    host: str
    scheme: str
    dns: DnsResult
    connect: ConnectResult | None = None
    fetch: HttpFetchResult | None = None
    embedded_fetch: HttpFetchResult | None = None
    control_resolves: bool | None = None
    connect_hint: str | None = None
    protocol_error: str | None = None

    @property
    def sinkhole_ips(self) -> list[str]:
        return self.dns.sinkhole_ips
