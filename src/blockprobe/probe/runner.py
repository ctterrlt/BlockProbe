# SPDX-License-Identifier: AGPL-3.0-or-later
"""Async probe runner.

One worker pool scans the selected domains with bounded concurrency, per-host
politeness and support for pause, resume and cancel. Progress and results flow
through callbacks so the Flet UI, the CLI and tests share the same engine.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from blockprobe.core.enums import Confidence, Verdict
from blockprobe.core.models import (
    Classification,
    ProbeAttemptRecord,
    ProbeObservation,
    SignatureRule,
    Site,
    utc_now,
)
from blockprobe.probe.classifier import classify
from blockprobe.probe.connect import connect
from blockprobe.probe.dns import resolve
from blockprobe.probe.health import ControlCheck
from blockprobe.probe.http_fetch import fetch, looks_like_block_target
from blockprobe.probe.interception import known_blocker_for_url
from blockprobe.probe.variants import aggregate, build_variants

_LOGGER = logging.getLogger("blockprobe.probe.runner")

ResultCallback = Callable[[Site, Classification, list[ProbeAttemptRecord]], None]
ProgressCallback = Callable[[int, int, Site, Classification], None]
GuardCallback = Callable[[], Awaitable[bool]]
ControlCallback = Callable[[ControlCheck], None]


@dataclass(slots=True)
class EngineConfig:
    concurrency: int = 20
    timeout: float = 10.0
    connect_timeout: float = 6.0
    retries: int = 2
    max_redirects: int = 5
    body_cap_bytes: int = 262_144
    variants: list[str] = field(default_factory=lambda: ["as_listed", "apex", "www"])
    control_url: str = "https://www.google.com/generate_204"
    control_urls: list[str] = field(
        default_factory=lambda: [
            "https://www.google.com/generate_204",
            "https://www.wikipedia.org/",
            "https://www.cloudflare.com/",
        ]
    )
    control_every: int = 10
    max_embedded_fetches: int = 1
    use_control_resolver: bool = False
    control_resolver_url: str = "https://cloudflare-dns.com/dns-query"
    debug: bool = False


class RunControl:
    """Thread-safe pause/resume/cancel signals shared with the UI."""

    def __init__(self) -> None:
        self._paused = threading.Event()
        self._cancelled = threading.Event()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def cancel(self) -> None:
        self._cancelled.set()
        self._paused.clear()

    async def wait_if_paused(self) -> None:
        while self.paused and not self.cancelled:
            await asyncio.sleep(0.2)


class ProbeEngine:
    def __init__(self, config: EngineConfig, rules: list[SignatureRule] | None = None) -> None:
        self.config = config
        self.rules = rules or []
        self._guard_lock = asyncio.Lock()

    async def check_control(
        self,
        url: str | None = None,
        *,
        after_domain: str = "",
        sequence: int = 0,
    ) -> ControlCheck:
        """Request one neutral website and record the result as evidence.

        A control that returns an HTTP error still proves the network path is
        alive; only a transport failure is marked ``ok = False``. That keeps a
        bot-protection 403 from being mistaken for a dead connection.
        """
        target = url or self.config.control_url
        result = await fetch(
            target,
            timeout=min(8.0, self.config.timeout),
            max_redirects=2,
            max_bytes=16_384,
        )
        return ControlCheck(
            url=target,
            ok=result.status is not None and result.error is None,
            status=result.status,
            error=result.error,
            final_url=result.final_url,
            after_domain=after_domain,
            sequence=sequence,
            checked_at=utc_now(),
        )

    async def check_connectivity(self, url: str | None = None) -> bool:
        """Return True when the device still has a working outbound path."""
        check = await self.check_control(url)
        if check.ok:
            return True
        _LOGGER.warning(
            "connectivity guard failed: %s",
            check.error or "unknown error",
            extra={"event": "network_guard", "domain": check.url},
        )
        return False

    def control_target(self, sequence: int) -> str:
        urls = self.config.control_urls or [self.config.control_url]
        return urls[(max(1, sequence) - 1) % len(urls)]

    def _control_target(self, sequence: int) -> str:
        return self.control_target(sequence)

    async def _control_resolves(self, host: str) -> bool | None:
        if not self.config.use_control_resolver:
            return None
        try:
            from blockprobe.diagnostics.resolver_compare import control_resolve

            return await control_resolve(host, self.config.control_resolver_url)
        except Exception as exc:
            _LOGGER.debug("control resolver failed for %s: %s", host, exc)
            return None

    @staticmethod
    def _choose_embedded_target(
        targets: list[str], source_host: str
    ) -> str | None:
        """Pick a same-host blocker-like target; never fetch arbitrary third parties."""
        for target in targets:
            if known_blocker_for_url(target) is not None:
                # The URL alone is already definitive; do not contact it twice.
                continue
            try:
                from urllib.parse import urlsplit

                parts = urlsplit(target)
                if (
                    parts.scheme in {"http", "https"}
                    and (parts.hostname or "").lower().rstrip(".") == source_host.lower().rstrip(".")
                    and looks_like_block_target(target)
                ):
                    return target
            except ValueError:
                continue
        return None

    async def probe_once(
        self,
        domain: str,
        host: str,
        scheme: str,
    ) -> tuple[ProbeObservation, Classification]:
        dns_result = await resolve(host, timeout=self.config.timeout)
        observation = ProbeObservation(
            domain=domain,
            host=host,
            scheme=scheme,
            dns=dns_result,
        )
        if dns_result.sinkhole:
            classification = classify(observation, self.rules, self.config.debug)
            _LOGGER.debug(
                "dns sinkhole",
                extra={"event": "probe_dns", "domain": host, "run_id": None},
            )
            return observation, classification

        if not dns_result.ok:
            observation.control_resolves = await self._control_resolves(host)
        else:
            port = 443 if scheme == "https" else 80
            if dns_result.ips:
                observation.connect = await connect(
                    host,
                    dns_result.ips[0],
                    port,
                    timeout=self.config.connect_timeout,
                    retries=self.config.retries,
                    use_tls=scheme == "https",
                )
        # Retry transient resolver failures through the platform HTTP stack;
        # it can use a different resolver path. Definitive NXDOMAIN/no-answer
        # results need no second full request timeout.
        if dns_result.ok or dns_result.status in {"timeout", "servfail", "error"}:
            # The raw socket probe is diagnostic evidence only. HTTPX may
            # succeed through a different address or network path, so never
            # let one socket failure suppress the real website request.
            observation.fetch = await fetch(
                f"{scheme}://{host}/",
                timeout=self.config.timeout,
                max_redirects=self.config.max_redirects,
                max_bytes=self.config.body_cap_bytes,
            )
        if (
            observation.fetch is not None
            and observation.fetch.embedded_redirects
            and self.config.max_embedded_fetches > 0
        ):
            target = self._choose_embedded_target(
                observation.fetch.embedded_redirects, host
            )
            if target is not None:
                observation.embedded_fetch = await fetch(
                    target,
                    timeout=self.config.timeout,
                    max_redirects=self.config.max_redirects,
                    max_bytes=min(self.config.body_cap_bytes, 131_072),
                )
        classification = classify(observation, self.rules, self.config.debug)
        _LOGGER.debug(
            "probe result",
            extra={
                "event": "probe_attempt",
                "domain": host,
                "duration_ms": observation.connect.connect_ms if observation.connect else 0,
            },
        )
        return observation, classification

    @staticmethod
    def _attempt_record(
        run_id: str,
        observation: ProbeObservation,
        classification: Classification,
    ) -> ProbeAttemptRecord:
        fetch_result = observation.fetch
        return ProbeAttemptRecord(
            run_id=run_id,
            domain=observation.domain,
            host=observation.host,
            scheme=observation.scheme,
            ip=observation.dns.ips[0] if observation.dns.ips else "",
            dns_status=observation.dns.status,
            connect_ms=observation.connect.connect_ms if observation.connect else 0,
            http_status=fetch_result.status if fetch_result else None,
            redirect_chain=fetch_result.redirect_chain if fetch_result else [],
            rule_id=classification.rule_id,
            body_sha256=fetch_result.body_sha256 if fetch_result else "",
            error=(
                (observation.connect.error if observation.connect and not observation.connect.ok else None)
                or (fetch_result.error if fetch_result else None)
            ),
            verdict=classification.verdict,
            reason_code=classification.reason_code,
            confidence=classification.confidence,
            blocker_hint=classification.blocker_hint,
            needs_review=classification.needs_review,
        )

    async def probe_domain(
        self,
        run_id: str,
        site: Site,
    ) -> tuple[Classification, list[ProbeAttemptRecord]]:
        variants = build_variants(site, self.config.variants)
        attempts: list[ProbeAttemptRecord] = []
        classifications: list[Classification] = []
        skip_hosts: set[str] = set()
        for variant in variants:
            if variant.host in skip_hosts:
                continue
            observation, classification = await self.probe_once(
                site.domain, variant.host, variant.scheme
            )
            attempts.append(self._attempt_record(run_id, observation, classification))
            classifications.append(classification)
            if variant.scheme == "https" and (
                classification.verdict in {Verdict.OPENED, Verdict.BLOCKED}
                or not observation.dns.ok
            ):
                skip_hosts.add(variant.host)
            await asyncio.sleep(random.uniform(0.05, 0.2))
        return aggregate(site.domain, classifications), attempts

    async def run_batch(
        self,
        run_id: str,
        sites: list[Site],
        control: RunControl,
        on_result: ResultCallback,
        on_progress: ProgressCallback | None = None,
        guard: GuardCallback | None = None,
        guard_every: int = 20,
        on_control: ControlCallback | None = None,
        control_every: int | None = None,
        control_start_sequence: int = 0,
    ) -> bool:
        """Probe every site. Returns True when the run was cancelled.

        When ``on_control`` is provided, a neutral website on the configured
        rotation is requested after every ``control_every`` sites, outside the
        worker semaphore, and recorded with the last completed domain. That
        gives reports the domain after which a network/VPN/DNS failure began.
        """
        total = len(sites)
        completed = 0
        semaphore = asyncio.Semaphore(max(1, self.config.concurrency))
        cancelled = False
        control_sequence = control_start_sequence
        interval = max(1, control_every or self.config.control_every)
        batch_size = interval if on_control is not None else max(1, guard_every)

        async def worker(site: Site) -> None:
            nonlocal completed, cancelled
            if control.cancelled:
                cancelled = True
                return
            await control.wait_if_paused()
            if control.cancelled:
                cancelled = True
                return
            async with semaphore:
                if control.cancelled:
                    cancelled = True
                    return
                try:
                    classification, attempts = await self.probe_domain(run_id, site)
                except Exception as exc:  # pragma: no cover - a single domain must not kill the run
                    _LOGGER.exception("probe failed for %s: %s", site.domain, exc)
                    classification = Classification(
                        verdict=Verdict.UNREACHABLE,
                        reason_code="probe_error",
                        confidence=Confidence.LOW,
                        needs_review=True,
                        evidence={"error": f"{type(exc).__name__}: {exc}"},
                    )
                    attempts = []
                try:
                    on_result(site, classification, attempts)
                except Exception:  # pragma: no cover - UI callback must not kill the run
                    _LOGGER.exception("result callback failed for %s", site.domain)
                completed += 1
                if on_progress is not None:
                    on_progress(completed, total, site, classification)

        pending = list(sites)
        index = 0
        guard_failures = 0
        while index < len(pending) and not cancelled:
            if control.cancelled:
                cancelled = True
                break
            batch = pending[index : index + batch_size]
            index += len(batch)
            await asyncio.gather(*(worker(site) for site in batch))
            if cancelled or control.cancelled:
                cancelled = True
                break
            if on_control is not None:
                control_sequence += 1
                target = self._control_target(control_sequence)
                try:
                    check = await self.check_control(
                        target,
                        after_domain=batch[-1].domain,
                        sequence=control_sequence,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    _LOGGER.exception("neutral control failed unexpectedly: %s", exc)
                    check = ControlCheck(
                        url=target,
                        ok=False,
                        error=f"{type(exc).__name__}: {exc}",
                        after_domain=batch[-1].domain,
                        sequence=control_sequence,
                        checked_at=utc_now(),
                    )
                try:
                    on_control(check)
                except Exception:  # pragma: no cover - UI callback must not kill the run
                    _LOGGER.exception("control callback failed")
                if not check.ok:
                    guard_failures += 1
                    _LOGGER.warning(
                        "neutral control failed during run",
                        extra={
                            "event": "network_control",
                            "run_id": run_id,
                            "domain": check.url,
                        },
                    )
                    if guard_failures >= 2:
                        break
                else:
                    guard_failures = 0
            elif guard is not None and guard_every > 0:
                async with self._guard_lock:
                    healthy = await guard()
                if not healthy:
                    guard_failures += 1
                    _LOGGER.warning(
                        "network guard failed during run",
                        extra={"event": "network_guard", "run_id": run_id},
                    )
                    if guard_failures >= 2:
                        break
                else:
                    guard_failures = 0
        return cancelled or control.cancelled


def engine_from_settings(config, debug: bool = False) -> EngineConfig:  # type: ignore[no-untyped-def]
    return EngineConfig(
        concurrency=config.probe.concurrency,
        timeout=float(config.probe.timeout_seconds),
        connect_timeout=float(config.probe.connect_timeout_seconds),
        retries=config.probe.retries,
        max_redirects=config.probe.max_redirects,
        body_cap_bytes=config.probe.body_cap_bytes,
        variants=list(config.probe.variants),
        control_url=config.probe.control_url,
        control_urls=list(config.probe.control_urls),
        control_every=max(1, config.probe.control_every),
        use_control_resolver=config.probe.use_control_resolver,
        control_resolver_url=config.probe.control_resolver_url,
        debug=debug,
    )
