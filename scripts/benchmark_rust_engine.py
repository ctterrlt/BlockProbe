#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare Rust FFI aggregation with the Python reference on fixed variants."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time

from blockprobe.core.enums import Confidence, ReasonCode, Verdict
from blockprobe.core.models import Classification
from blockprobe.probe.rust_engine import aggregate_indices


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


def python_reference(items: list[Classification]) -> tuple[int, bool, list[int]]:
    opened = [i for i, item in enumerate(items) if item.verdict is Verdict.OPENED]
    confident_opened = [
        i
        for i in opened
        if not items[i].needs_review
        and items[i].reason_code not in {"empty_page", "unclassified"}
    ]
    uncertain_opened = [i for i in opened if i not in confident_opened]
    blocked = [i for i, item in enumerate(items) if item.verdict is Verdict.BLOCKED]

    def first_best(indices: list[int], key) -> int:  # type: ignore[no-untyped-def]
        return max(indices, key=lambda i: (key(items[i]), -i))

    if confident_opened:
        chosen = first_best(confident_opened, lambda item: item.confidence.rank)
    elif blocked:
        chosen = first_best(
            blocked,
            lambda item: (item.confidence.rank, bool(item.blocker_hint)),
        )
    elif opened:
        chosen = first_best(opened, lambda item: item.confidence.rank)
    else:
        ranks = {Verdict.OPENED: 3, Verdict.BLOCKED: 2, Verdict.UNREACHABLE: 1}
        chosen = first_best(
            list(range(len(items))),
            lambda item: (ranks[item.verdict], item.confidence.rank),
        )
    conflict = items[chosen].verdict is Verdict.BLOCKED and bool(uncertain_opened)
    return chosen, conflict, uncertain_opened if conflict else []


def measure(function, items: list[Classification], samples: int) -> dict[str, float]:  # type: ignore[no-untyped-def]
    for _ in range(500):
        function(items)
    latencies = []
    cpu_start = time.process_time_ns()
    for _ in range(samples):
        started = time.perf_counter_ns()
        function(items)
        latencies.append(time.perf_counter_ns() - started)
    cpu_ns = time.process_time_ns() - cpu_start
    ordered = sorted(latencies)
    return {
        "p50_us_per_site": statistics.median(ordered) / 1_000 / len(items),
        "p95_us_per_site": ordered[int(0.95 * (len(ordered) - 1))] / 1_000 / len(items),
        "cpu_us_per_site": cpu_ns / samples / 1_000 / len(items),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10_000)
    args = parser.parse_args()
    if args.samples < 100:
        parser.error("--samples must be at least 100")
    items = fixture()
    if aggregate_indices(items) != python_reference(items):
        raise SystemExit("Rust and Python aggregation results differ for the benchmark fixture")
    print(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "variants_per_site": len(items),
                "samples": args.samples,
                "python_reference": measure(python_reference, items, args.samples),
                "rust_ffi": measure(aggregate_indices, items, args.samples),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
