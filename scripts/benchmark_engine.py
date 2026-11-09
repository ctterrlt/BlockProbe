#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare all production Rust scan primitives with Python references.

This measures warmed, in-process operations, not a complete network scan.
The resolver case uses the local system resolver and can vary with host setup.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import platform
import socket
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from typing import Any

from blockprobe.core.enums import Confidence, ReasonCode, Verdict
from blockprobe.core.models import Classification
from blockprobe.probe.rust_engine import (
    aggregate_indices,
    is_sinkhole_ip,
    system_resolve,
)

ADDRESSES = (
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
)


def fixture() -> list[Classification]:
    return [
        Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.BLOCK_SIGNATURE.value,
            confidence=Confidence.HIGH,
            blocker_hint="betblocker",
        ),
        Classification(
            verdict=Verdict.OPENED,
            reason_code=ReasonCode.UNCLASSIFIED.value,
            confidence=Confidence.LOW,
            needs_review=True,
        ),
        Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=ReasonCode.CONNECT_TIMEOUT.value,
            confidence=Confidence.MEDIUM,
        ),
        Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.DNS_SINKHOLE.value,
            confidence=Confidence.HIGH,
            blocker_hint="dns_filter",
        ),
        Classification(
            verdict=Verdict.OPENED,
            reason_code=ReasonCode.EMPTY_PAGE.value,
            confidence=Confidence.LOW,
            needs_review=True,
        ),
        Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=ReasonCode.CONNECT_RESET.value,
            confidence=Confidence.LOW,
        ),
    ]


def python_aggregate(items: list[Classification]) -> tuple[int, bool, list[int]]:
    opened = [i for i, item in enumerate(items) if item.verdict is Verdict.OPENED]
    confident_open = [
        i
        for i in opened
        if not items[i].needs_review
        and items[i].reason_code not in {"empty_page", "unclassified"}
    ]
    uncertain_open = [i for i in opened if i not in confident_open]
    blocked = [i for i, item in enumerate(items) if item.verdict is Verdict.BLOCKED]

    def best(indices: list[int], key: Callable[[int], Any]) -> int:
        return max(indices, key=lambda i: (key(i), -i))

    if confident_open:
        selected = best(confident_open, lambda i: items[i].confidence.rank)
    elif blocked:
        selected = best(
            blocked,
            lambda i: (items[i].confidence.rank, bool(items[i].blocker_hint)),
        )
    elif opened:
        selected = best(opened, lambda i: items[i].confidence.rank)
    else:
        rank = {Verdict.OPENED: 3, Verdict.BLOCKED: 2, Verdict.UNREACHABLE: 1}
        selected = best(
            list(range(len(items))),
            lambda i: (rank[items[i].verdict], items[i].confidence.rank),
        )
    conflict = items[selected].verdict is Verdict.BLOCKED and bool(uncertain_open)
    return selected, conflict, uncertain_open if conflict else []


def python_sinkhole(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
        or address.is_multicast
    )


def python_resolve(host: str) -> list[str] | None:
    try:
        ascii_host = host.encode("idna").decode("ascii")
        answers = socket.getaddrinfo(ascii_host, 0, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return None
    addresses = list(
        dict.fromkeys(
            answer[4][0] for answer in answers if isinstance(answer[4][0], str)
        )
    )
    return addresses or None


def combined_rust(items: list[Classification]) -> tuple[object, ...]:
    return (
        aggregate_indices(items),
        tuple(is_sinkhole_ip(value) for value in ADDRESSES),
    )


def combined_python(items: list[Classification]) -> tuple[object, ...]:
    return (
        python_aggregate(items),
        tuple(python_sinkhole(value) for value in ADDRESSES),
    )


def measure(
    function: Callable[[], object], samples: int, warmups: int
) -> dict[str, float | int]:
    for _ in range(warmups):
        function()
    elapsed: list[int] = []
    cpu_start = time.process_time_ns()
    for _ in range(samples):
        start = time.perf_counter_ns()
        function()
        elapsed.append(time.perf_counter_ns() - start)
    cpu_ns = time.process_time_ns() - cpu_start

    tracemalloc.start()
    tracemalloc.reset_peak()
    before, _ = tracemalloc.get_traced_memory()
    for _ in range(min(samples, 100)):
        function()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    elapsed.sort()
    return {
        "p50_us": statistics.median(elapsed) / 1_000,
        "p95_us": elapsed[int(0.95 * (len(elapsed) - 1))] / 1_000,
        "cpu_us_per_call": cpu_ns / samples / 1_000,
        "peak_traced_python_bytes": max(0, peak - before),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5_000)
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()
    if args.samples < 100:
        parser.error("--samples must be at least 100")

    items = fixture()
    python_classification = tuple(python_sinkhole(value) for value in ADDRESSES)
    rust_classification = tuple(is_sinkhole_ip(value) for value in ADDRESSES)
    if python_classification != rust_classification:
        raise SystemExit("Rust/Python sinkhole classification differs on fixture")
    if aggregate_indices(items) != python_aggregate(items):
        raise SystemExit("Rust/Python aggregate selection differs on fixture")
    python_answers = python_resolve(args.host)
    rust_answers = system_resolve(args.host)
    if python_answers is None or rust_answers is None:
        raise SystemExit(f"Could not resolve benchmark host {args.host!r}")
    if set(python_answers) != set(rust_answers):
        raise SystemExit(
            f"Rust/Python resolver answers differ: {rust_answers!r} != {python_answers!r}"
        )
    if combined_rust(items) != combined_python(items):
        raise SystemExit("Combined Rust/Python engine workload differs")

    workloads = {
        "aggregate_six_variants": (
            lambda: aggregate_indices(items),
            lambda: python_aggregate(items),
        ),
        "classify_twenty_ips": (
            lambda: tuple(is_sinkhole_ip(value) for value in ADDRESSES),
            lambda: tuple(python_sinkhole(value) for value in ADDRESSES),
        ),
        "system_resolve": (
            lambda: system_resolve(args.host),
            lambda: python_resolve(args.host),
        ),
        "combined_six_variant_engine": (
            lambda: combined_rust(items),
            lambda: combined_python(items),
        ),
    }
    results = {}
    for name, (rust_function, python_function) in workloads.items():
        results[name] = {
            "rust_ffi": measure(rust_function, args.samples, 500),
            "python_reference": measure(python_function, args.samples, 500),
        }

    print(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "host": args.host,
                "samples_per_workload": args.samples,
                "warmups_per_workload": 500,
                "fixtures": {
                    "variants_per_site": len(items),
                    "ip_addresses": len(ADDRESSES),
                    "resolved_addresses": rust_answers,
                },
                "memory_metric": (
                    "peak additional traced Python allocations across at most 100 calls; "
                    "does not include Rust native heap or process RSS"
                ),
                "workloads": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
