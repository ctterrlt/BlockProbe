# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portal list updates (FR-5).

One portal at a time, one process-wide lock, robots.txt respected, one request
per second per host, exponential backoff on 403/429 and an append-only result.
A failed or empty update never clears the stored list and never stops a scan.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from blockprobe.core.config import Settings
from blockprobe.core.enums import RefreshStatus
from blockprobe.core.errors import FetchError, RobotsDenied
from blockprobe.core.models import (
    ExtractedDomain,
    Portal,
    RefreshDiff,
    RefreshSummary,
    utc_now,
)
from blockprobe.portals.generic_extractor import extract_page, find_ajax_content_url
from blockprobe.portals.loader import load_portals
from blockprobe.portals.pagination import (
    PaginationState,
    discover_form_pagination,
    discover_next_links,
)
from blockprobe.portals.robots import USER_AGENT, fetch_robots
from blockprobe.storage.base import Storage

_LOGGER = logging.getLogger("blockprobe.services.refresh")

MAX_PORTAL_BYTES = 5 * 1024 * 1024


@dataclass
class _FetchedPage:
    url: str
    text: str
    status: int
    final_url: str
    error: str | None = None


@dataclass
class _Limiter:
    rate_per_sec: float = 1.0
    last_request: dict[str, float] = field(default_factory=dict)

    def wait(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        interval = 1.0 / max(0.1, self.rate_per_sec)
        now = time.monotonic()
        previous = self.last_request.get(host)
        if previous is not None:
            delay = interval - (now - previous)
            if delay > 0:
                time.sleep(delay)
        self.last_request[host] = time.monotonic()


def _decode(data: bytes, encoding: str | None) -> str:
    for candidate in (encoding, "utf-8", "latin-1"):
        if not candidate:
            continue
        try:
            return data.decode(candidate, errors="replace")
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


def _resolve_page_url(base: str, relative: str) -> str:
    """Join a pagination link while preserving the base query parameters.

    Portal fragments often identify the portlet in the query string. A pager
    link such as ``?pager=2&`` must keep ``p_p_id`` and the other fragment
    parameters. When the link points at the same path, the base query and the
    link query are merged (the link wins on conflicts), so filters such as
    ``tipo_gioco`` survive while ``pager`` advances.
    """
    if not relative:
        return base
    base_parts = urlsplit(base)
    joined = urljoin(base, relative)
    joined_parts = urlsplit(joined)
    same_target = (
        joined_parts.netloc == base_parts.netloc and joined_parts.path == base_parts.path
    )
    if same_target and base_parts.query and joined_parts.query:
        merged = dict(parse_qsl(base_parts.query, keep_blank_values=True))
        merged.update(dict(parse_qsl(joined_parts.query, keep_blank_values=True)))
        return urlunsplit(
            (
                joined_parts.scheme,
                joined_parts.netloc,
                joined_parts.path,
                urlencode(merged),
                joined_parts.fragment,
            )
        )
    return joined


class RefreshService:
    """Refresh selected portals and return a per-portal diff."""

    _lock = threading.Lock()

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        portals: list[Portal] | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.portals = portals if portals is not None else load_portals(settings.portal_dirs)

    def sync_portals(self) -> list[Portal]:
        """Seed or update the portal table from the TOML definitions."""
        self.portals = load_portals(self.settings.portal_dirs)
        self.storage.upsert_portals(self.portals)
        return self.portals

    def refresh(
        self,
        portal_ids: list[str] | None = None,
        *,
        force: bool = False,
        only_stale: bool = False,
    ) -> RefreshSummary:
        summary = RefreshSummary(started_at=utc_now())
        if not self._lock.acquire(blocking=False):
            _LOGGER.info(
                "portal refresh already running; skipping",
                extra={"event": "refresh_lock_busy"},
            )
            summary.finished_at = utc_now()
            return summary
        try:
            portals = self.sync_portals()
            selected = self._select(portals, portal_ids)
            if not selected:
                summary.finished_at = utc_now()
                return summary
            with httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.1"},
                follow_redirects=True,
                timeout=httpx.Timeout(20.0, connect=10.0),
                trust_env=True,
            ) as client:
                for portal in selected:
                    if not force and only_stale and not portal.is_stale(
                        self.settings.config.portals.refresh_ttl_hours
                    ):
                        diff = RefreshDiff(
                            portal_id=portal.uuid,
                            portal_name=portal.name,
                            status=RefreshStatus.SKIPPED,
                            started_at=utc_now(),
                            finished_at=utc_now(),
                        )
                        self.storage.record_refresh(diff)
                        summary.results.append(diff)
                        continue
                    if not force and portal.in_retry_cooldown(
                        self.settings.config.portals.refresh_retry_cooldown_minutes
                    ):
                        diff = RefreshDiff(
                            portal_id=portal.uuid,
                            portal_name=portal.name,
                            status=RefreshStatus.COOLDOWN,
                            error=portal.last_error,
                            started_at=utc_now(),
                            finished_at=utc_now(),
                        )
                        self.storage.record_refresh(diff)
                        summary.results.append(diff)
                        continue
                    diff = self._refresh_portal(client, portal)
                    self.storage.record_refresh(diff)
                    summary.results.append(diff)
        finally:
            self._lock.release()
        summary.finished_at = utc_now()
        return summary

    def _select(self, portals: list[Portal], portal_ids: list[str] | None) -> list[Portal]:
        enabled = [portal for portal in portals if portal.enabled]
        if portal_ids is None:
            return enabled
        wanted = {item.lower() for item in portal_ids if item}
        if not wanted:
            return []
        return [
            portal
            for portal in enabled
            if portal.uuid.lower() in wanted
            or portal.slug.lower() in wanted
            or portal.name.lower() in wanted
        ]

    def _refresh_portal(self, client: httpx.Client, portal: Portal) -> RefreshDiff:
        started = utc_now()
        started_monotonic = time.monotonic()
        pages = 0
        found: dict[str, ExtractedDomain] = {}
        warnings: list[str] = []
        limiter = _Limiter(
            rate_per_sec=min(
                portal.rate_limit_per_sec or 1.0,
                self.settings.config.portals.rate_limit_per_sec or 1.0,
            )
        )
        state = PaginationState()
        try:
            if portal.respect_robots:
                policy = fetch_robots(portal.start_url, client)
                if not policy.allowed:
                    raise RobotsDenied(policy.note or "robots.txt disallows this page")
                if policy.crawl_delay:
                    limiter.rate_per_sec = min(limiter.rate_per_sec, 1.0 / max(0.1, policy.crawl_delay))
                if policy.note:
                    warnings.append(policy.note)

            start_url = portal.start_url
            queue: list[str] = [start_url]
            visited: set[str] = set()
            content_url: str | None = None
            previous_count = len(
                self.storage.list_sites(portal_ids=[portal.uuid], with_custom=False)
            )
            page_limit = min(
                portal.max_pages, self.settings.config.portals.max_pages
            )
            while queue and pages < page_limit:
                current = queue.pop(0)
                if current in visited or state.already_visited(current):
                    continue
                visited.add(current)
                limiter.wait(current)
                page = self._fetch(client, current)
                if page.error or page.status >= 400:
                    if pages == 0:
                        raise FetchError(
                            f"HTTP {page.status}: {page.error or 'request failed'}"
                        )
                    warnings.append(f"page {pages + 1} failed: {page.error or page.status}")
                    break
                extraction = extract_page(page.text, page.final_url, portal)
                if not extraction.candidates and content_url is None:
                    ajax_url = find_ajax_content_url(page.text, page.final_url)
                    if ajax_url and ajax_url not in visited:
                        content_url = ajax_url
                        queue.insert(0, ajax_url)
                        visited.discard(page.final_url)
                        _LOGGER.info(
                            "portal content fragment discovered",
                            extra={"portal_id": portal.uuid, "event": "portal_fragment"},
                        )
                        continue
                pages += 1
                new_domains = 0
                for candidate in extraction.candidates:
                    if candidate.domain not in found:
                        found[candidate.domain] = candidate
                        new_domains += 1
                for link in discover_next_links(page.text, page.final_url):
                    resolved = _resolve_page_url(page.final_url, link)
                    if resolved not in visited and resolved not in queue:
                        queue.append(resolved)
                if not queue:
                    for form in discover_form_pagination(
                        page.text, page.final_url, max_pages=min(3, portal.max_pages - pages)
                    ):
                        if form.url not in visited:
                            # Form pagination is replayed as a GET when possible;
                            # POST replay is handled by the portal-specific future
                            # extractor. Log the discovery so the next maintainer
                            # knows the page uses a form.
                            warnings.append(
                                "form-based pagination detected; only the first pages "
                                "are read unless the portal supports GET pagination"
                            )
                            break
                # Keep following discovered pagination even when a page adds
                # no domains; some portals have blank pages before later pages
                # containing actual website entries.
                state.register(page.final_url, extraction.content_hash, new_domains)

            if pages >= page_limit and any(url not in visited for url in queue):
                warnings.append(
                    f"portal page limit ({page_limit}) reached; the imported list "
                    "may be incomplete"
                )

            if pages == 0:
                raise FetchError("no pages could be read")
            found_count = len(found)
            if found_count == 0:
                return RefreshDiff(
                    portal_id=portal.uuid,
                    portal_name=portal.name,
                    status=RefreshStatus.EMPTY,
                    pages=pages,
                    found=0,
                    error="0 domains found (layout change or an empty portal)",
                    warnings=warnings,
                    started_at=started,
                    finished_at=utc_now(),
                    duration_ms=int((time.monotonic() - started_monotonic) * 1000),
                )
            if previous_count and found_count < previous_count * 0.7:
                warnings.append(
                    f"refresh found {found_count} domains, more than 30% fewer than the "
                    f"{previous_count} already stored; the stored list was kept"
                )
            new_domains, known_domains = self.storage.add_portal_domains(
                portal.uuid, list(found.values()), start_url
            )
            return RefreshDiff(
                portal_id=portal.uuid,
                portal_name=portal.name,
                status=RefreshStatus.OK,
                pages=pages,
                found=found_count,
                added=len(new_domains),
                new_domains=new_domains,
                already_known=known_domains,
                warnings=warnings,
                started_at=started,
                finished_at=utc_now(),
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            )
        except (RobotsDenied, FetchError) as exc:
            return RefreshDiff(
                portal_id=portal.uuid,
                portal_name=portal.name,
                status=RefreshStatus.ERROR,
                pages=pages,
                found=len(found),
                error=str(exc),
                warnings=warnings,
                started_at=started,
                finished_at=utc_now(),
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            )
        except Exception as exc:  # pragma: no cover - unexpected portal failure
            _LOGGER.exception("portal refresh failed: %s", exc)
            return RefreshDiff(
                portal_id=portal.uuid,
                portal_name=portal.name,
                status=RefreshStatus.ERROR,
                pages=pages,
                found=len(found),
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
                started_at=started,
                finished_at=utc_now(),
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            )

    def _fetch(self, client: httpx.Client, url: str) -> _FetchedPage:
        last_error: str | None = None
        for attempt in range(3):
            try:
                with client.stream("GET", url) as response:
                    status = response.status_code
                    if status in {403, 429} and attempt < 2:
                        delay = 2**attempt
                        _LOGGER.warning(
                            "portal returned HTTP %s; backing off %ss",
                            status,
                            delay,
                            extra={"event": "portal_backoff", "domain": url},
                        )
                        time.sleep(delay)
                        continue
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_PORTAL_BYTES:
                            chunks.append(chunk[: max(0, MAX_PORTAL_BYTES - (size - len(chunk)))])
                            break
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    text = _decode(data, response.encoding)
                    return _FetchedPage(
                        url=url,
                        text=text,
                        status=status,
                        final_url=str(response.url),
                    )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
        return _FetchedPage(url=url, text="", status=0, final_url=url, error=last_error or "request failed")


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(json.dumps(text[:5000]).encode("utf-8")).hexdigest()
