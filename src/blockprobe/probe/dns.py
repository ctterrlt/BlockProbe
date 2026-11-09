# SPDX-License-Identifier: AGPL-3.0-or-later
"""DNS resolution and sinkhole detection.

Uses the system resolver through dnspython. Sinkhole answers (0.0.0.0, ::,
loopback, private, link-local and reserved ranges) are recorded as evidence,
never as a guess: a DNS filter that returns a private address is direct
evidence of a block. The optional control resolver is only used when the user
opts in.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from blockprobe.core.models import DnsResult
from blockprobe.probe.rust_engine import is_sinkhole_ip as _rust_is_sinkhole_ip
from blockprobe.probe.rust_engine import system_resolve as _rust_system_resolve

_LOGGER = logging.getLogger("blockprobe.probe.dns")


def is_sinkhole_ip(value: str) -> bool:
    """Return True for addresses that a public website must never resolve to."""
    return _rust_is_sinkhole_ip(value)


def _resolver(timeout: float, nameservers: Iterable[str] | None = None):  # type: ignore[no-untyped-def]
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=not nameservers)
    if nameservers:
        resolver.nameservers = list(nameservers)
    resolver.lifetime = timeout
    resolver.timeout = min(timeout, 3.0)
    return resolver


def _system_resolve(host: str) -> DnsResult | None:
    """Use the OS resolver when dnspython cannot reach/configure DNS.

    Android exposes DNS through its network stack and commonly has no usable
    resolv.conf for Python libraries to read. ``getaddrinfo`` delegates to the
    platform resolver, which is also the path normal app connections use.
    """
    ips = _rust_system_resolve(host)
    if not ips:
        return None
    sinkholes = [ip for ip in ips if is_sinkhole_ip(ip)]
    return DnsResult(
        host=host,
        status="sinkhole" if sinkholes else "ok",
        ips=ips,
        sinkhole_ips=sinkholes,
        error=None,
        resolver="platform (getaddrinfo)",
    )


def resolve_sync(
    host: str,
    timeout: float = 6.0,
    nameservers: Iterable[str] | None = None,
) -> DnsResult:
    """Resolve A/AAAA/CNAME records using the system or a custom resolver."""
    import dns.exception
    import dns.resolver

    try:
        resolver = _resolver(timeout, nameservers)
    except Exception as exc:  # pragma: no cover - depends on host DNS setup
        fallback = _system_resolve(host) if nameservers is None else None
        return fallback or DnsResult(
            host=host, status="error", error=f"resolver unavailable: {exc}"
        )

    resolver_label = "system"
    try:
        if nameservers:
            resolver_label = ",".join(str(item) for item in nameservers)
        else:
            label = getattr(resolver, "nameservers", None)
            if label:
                resolver_label = ",".join(str(item) for item in label)
    except Exception:  # pragma: no cover
        pass

    ips: list[str] = []
    cnames: list[str] = []
    for record_type in ("A", "AAAA"):
        try:
            answer = resolver.resolve(host, record_type, raise_on_no_answer=False)
        except dns.resolver.NXDOMAIN:
            fallback = _system_resolve(host) if nameservers is None else None
            if fallback is not None:
                return fallback
            return DnsResult(
                host=host,
                status="nxdomain",
                error="NXDOMAIN",
                resolver=resolver_label,
            )
        except dns.resolver.NoNameservers as exc:
            fallback = _system_resolve(host) if nameservers is None else None
            if fallback is not None:
                return fallback
            return DnsResult(
                host=host,
                status="servfail",
                error=f"NoNameservers: {exc}",
                resolver=resolver_label,
            )
        except dns.exception.Timeout as exc:
            fallback = _system_resolve(host) if nameservers is None else None
            if fallback is not None:
                return fallback
            return DnsResult(
                host=host,
                status="timeout",
                error=f"DNS timeout: {exc}",
                resolver=resolver_label,
            )
        except Exception as exc:
            fallback = _system_resolve(host) if nameservers is None else None
            if fallback is not None:
                return fallback
            return DnsResult(
                host=host,
                status="error",
                error=f"{exc.__class__.__name__}: {exc}",
                resolver=resolver_label,
            )
        if answer.rrset is None:
            continue
        for record in answer:
            value = str(record.to_text()).strip()
            if record_type == "A" or record_type == "AAAA":
                ips.append(value)

    try:
        cname_answer = resolver.resolve(host, "CNAME", raise_on_no_answer=False)
        if cname_answer.rrset is not None:
            cnames = [str(record.to_text()).strip().rstrip(".") for record in cname_answer]
    except Exception:
        cnames = []

    # Deduplicate while keeping deterministic output.
    ips = list(dict.fromkeys(ip for ip in ips if ip))
    sinkholes = [ip for ip in ips if is_sinkhole_ip(ip)]
    if sinkholes:
        return DnsResult(
            host=host,
            status="sinkhole",
            ips=ips,
            cnames=cnames,
            sinkhole_ips=sinkholes,
            error=None,
            resolver=resolver_label,
        )
    if not ips:
        return DnsResult(
            host=host,
            status="no_answer",
            cnames=cnames,
            error="no A/AAAA answer",
            resolver=resolver_label,
        )
    return DnsResult(
        host=host,
        status="ok",
        ips=ips,
        cnames=cnames,
        error=None,
        resolver=resolver_label,
    )


async def resolve(
    host: str,
    timeout: float = 6.0,
    nameservers: Iterable[str] | None = None,
) -> DnsResult:
    """Async wrapper around :func:`resolve_sync` using a worker thread."""
    return await asyncio.to_thread(resolve_sync, host, timeout, nameservers)


def system_resolvers() -> list[str]:
    """Best-effort list of configured system resolvers."""
    import dns.resolver

    resolvers: list[str] = []
    try:
        resolver = dns.resolver.Resolver()
        resolvers = [str(item) for item in resolver.nameservers]
    except Exception:  # pragma: no cover
        pass
    return resolvers
