# SPDX-License-Identifier: AGPL-3.0-or-later
"""The production scan path must use the compiled Rust engine."""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import threading
import time

import pytest

from blockprobe.core.enums import Confidence, ReasonCode, Verdict
from blockprobe.core.models import Classification
from blockprobe.probe import dns
from blockprobe.probe.rust_engine import (
    _check_abi_version,
    _library,
    aggregate_indices,
    is_sinkhole_ip,
    system_resolve,
)


def _item(
    verdict: Verdict,
    confidence: Confidence,
    reason: ReasonCode,
    *,
    review: bool = False,
    hint: str | None = None,
) -> Classification:
    return Classification(
        verdict=verdict,
        reason_code=reason.value,
        confidence=confidence,
        needs_review=review,
        blocker_hint=hint,
    )


def _python_reference(items: list[Classification]) -> tuple[int, bool, list[int]]:
    def confident_open(index: int) -> bool:
        item = items[index]
        return (
            item.verdict is Verdict.OPENED
            and not item.needs_review
            and item.reason_code not in {"empty_page", "unclassified"}
        )

    def first_best(indices: list[int], key):  # type: ignore[no-untyped-def]
        return max(indices, key=lambda index: (key(index), -index))

    opened = [i for i, item in enumerate(items) if item.verdict is Verdict.OPENED]
    confident = [i for i in opened if confident_open(i)]
    uncertain = [i for i in opened if i not in confident]
    blocked = [i for i, item in enumerate(items) if item.verdict is Verdict.BLOCKED]
    if confident:
        chosen = first_best(confident, lambda i: items[i].confidence.rank)
    elif blocked:
        chosen = first_best(
            blocked,
            lambda i: (items[i].confidence.rank, bool(items[i].blocker_hint)),
        )
    elif opened:
        chosen = first_best(opened, lambda i: items[i].confidence.rank)
    else:
        ranks = {Verdict.OPENED: 3, Verdict.BLOCKED: 2, Verdict.UNREACHABLE: 1}
        chosen = first_best(
            list(range(len(items))),
            lambda i: (ranks[items[i].verdict], items[i].confidence.rank),
        )
    conflict = items[chosen].verdict is Verdict.BLOCKED and bool(uncertain)
    return chosen, conflict, uncertain if conflict else []


def test_rust_aggregate_matches_reference_for_mixed_probe_results() -> None:
    cases = [
        _item(Verdict.OPENED, Confidence.HIGH, ReasonCode.POSITIVE_MARKERS),
        _item(
            Verdict.OPENED,
            Confidence.LOW,
            ReasonCode.UNCLASSIFIED,
            review=True,
        ),
        _item(Verdict.OPENED, Confidence.LOW, ReasonCode.EMPTY_PAGE, review=True),
        _item(
            Verdict.BLOCKED,
            Confidence.HIGH,
            ReasonCode.BLOCK_SIGNATURE,
            hint="betblocker",
        ),
        _item(Verdict.BLOCKED, Confidence.MEDIUM, ReasonCode.DNS_SINKHOLE),
        _item(Verdict.UNREACHABLE, Confidence.HIGH, ReasonCode.CONNECT_TIMEOUT),
    ]
    for items in itertools.product(cases, repeat=3):
        variants = list(items)
        assert aggregate_indices(variants) == _python_reference(variants)


def test_rust_ip_classification_matches_python_for_supported_ranges() -> None:
    addresses = [
        "0.0.0.0",
        "10.1.2.3",
        "127.0.0.1",
        "169.254.1.2",
        "172.16.0.1",
        "192.168.1.1",
        "192.0.2.1",
        "198.51.100.8",
        "203.0.113.9",
        "224.0.0.1",
        "240.0.0.1",
        "::",
        "::1",
        "fe80::1",
        "fc00::1",
        "2001:db8::1",
        "::ffff:192.168.1.1",
        "8.8.8.8",
        "1.1.1.1",
        "2606:4700:4700::1111",
    ]
    for value in addresses:
        address = ipaddress.ip_address(value)
        expected = (
            address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_private
            or address.is_reserved
            or address.is_multicast
        )
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            mapped = address.ipv4_mapped
            expected = (
                mapped.is_unspecified
                or mapped.is_loopback
                or mapped.is_link_local
                or mapped.is_private
                or mapped.is_reserved
                or mapped.is_multicast
            )
        assert is_sinkhole_ip(value) is expected, value


def test_platform_dns_fallback_uses_rust_resolver() -> None:
    result = dns._system_resolve("localhost")
    assert result is not None
    assert result.ips == system_resolve("localhost")
    assert result.resolver == "platform (getaddrinfo)"
    assert result.status == "sinkhole"


def test_rust_library_reports_the_expected_abi_version() -> None:
    assert _library().bp_abi_version() == 1


def test_rust_loader_rejects_an_unsupported_abi_version() -> None:
    with pytest.raises(RuntimeError, match="unsupported ABI version 2"):
        _check_abi_version(2)


def test_dns_work_stays_off_the_asyncio_ui_loop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    entered = threading.Event()
    release = threading.Event()

    def slow_resolve(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        entered.set()
        release.wait(timeout=2)
        return None

    monkeypatch.setattr(dns, "resolve_sync", slow_resolve)

    async def scenario() -> None:
        task = asyncio.create_task(dns.resolve("example.com"))
        assert await asyncio.to_thread(entered.wait, 1)
        # The loop must make progress while the blocking resolver is held.
        tick = asyncio.Event()
        asyncio.get_running_loop().call_soon(tick.set)
        await asyncio.wait_for(tick.wait(), timeout=0.1)
        release.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())


def test_rust_aggregation_cost_is_bounded_for_one_site() -> None:
    variants = [
        _item(Verdict.UNREACHABLE, Confidence.LOW, ReasonCode.CONNECT_TIMEOUT)
        for _ in range(6)
    ]
    started = time.perf_counter()
    for _ in range(2000):
        aggregate_indices(variants)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0
