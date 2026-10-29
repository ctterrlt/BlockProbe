# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in comparison against a user-configured DNS-over-HTTPS resolver.

The comparison answers one question: can a public resolver resolve a name that
the system resolver could not? It is off by default (``use_control_resolver``)
and only ever contacts the endpoint the user configured.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from blockprobe.probe.dns import resolve_sync

_LOGGER = logging.getLogger("blockprobe.diagnostics.resolver_compare")


async def control_resolve(host: str, endpoint: str, timeout: float = 5.0) -> bool:
    """Return True when the DoH resolver has an A/AAAA answer for ``host``."""
    if not endpoint:
        return False
    payload = {"name": host, "type": "A"}
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
            response = await client.get(
                endpoint,
                params=payload,
                headers={"Accept": "application/dns-json"},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        _LOGGER.debug("control resolver query failed for %s: %s", host, exc)
        return False
    answers = data.get("Answer") or []
    return any(
        int(answer.get("type", 0)) in {1, 28} and bool(answer.get("data"))
        for answer in answers
    )


async def compare(host: str, endpoint: str) -> dict[str, object]:
    """Compare the system resolver with the opt-in DoH resolver."""
    system = await asyncio.to_thread(resolve_sync, host, 5.0)
    control_ok = await control_resolve(host, endpoint)
    return {
        "host": host,
        "system_status": system.status,
        "system_ips": system.ips,
        "system_cnames": system.cnames,
        "control_resolves": control_ok,
        "endpoint": endpoint,
        "note": (
            "If the system resolver fails while the control resolver succeeds, "
            "a DNS filter may be blocking the name."
        ),
    }
