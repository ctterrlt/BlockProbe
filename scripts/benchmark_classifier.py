#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare Python and Rust built-in response-marker scans on a fixed body."""

from __future__ import annotations

import argparse
import re
import statistics
import time

from blockprobe.core.models import DnsResult, HttpFetchResult, ProbeObservation
from blockprobe.portals.generic_extractor import strip_tags
from blockprobe.probe.classifier import (
    BLOCKER_PAGE_MARKERS,
    CHALLENGE_MARKERS,
    DEAD_MARKERS,
    GAMBLING_BLOCK_MARKERS,
    POSITIVE_MARKERS,
    _matched_builtin_patterns,
    classify,
)
from blockprobe.probe.rust_engine import _compile_pattern_set

PATTERN_GROUPS = (
    CHALLENGE_MARKERS,
    GAMBLING_BLOCK_MARKERS,
    POSITIVE_MARKERS,
    BLOCKER_PAGE_MARKERS,
    DEAD_MARKERS,
)
FILLER = "neutral observation text alpha beta gamma delta epsilon; "


def response_body(target_bytes: int) -> str:
    """Build an exact-size ASCII response with no built-in marker hits."""
    prefix = "<html><title>Ordinary page</title><body>"
    suffix = "</body></html>"
    fixed = len(prefix) + len(suffix)
    if target_bytes <= fixed:
        raise ValueError(f"body size must exceed {fixed} bytes")
    filler_bytes = target_bytes - fixed
    payload = (FILLER * ((filler_bytes + len(FILLER) - 1) // len(FILLER)))[:filler_bytes]
    body = prefix + payload + suffix
    assert len(body.encode("utf-8")) == target_bytes
    return body


def searchable_body(body: str) -> str:
    return "Ordinary page\n" + strip_tags(body)


def python_reference(text: str) -> int:
    """The previous per-pattern Python loop used for the comparison."""
    count = 0
    for patterns in PATTERN_GROUPS:
        count += sum(re.search(pattern, text, re.IGNORECASE | re.DOTALL) is not None for pattern in patterns)
    return count


def rust_matcher(text: str) -> int:
    return sum(len(_matched_builtin_patterns(patterns, text)) for patterns in PATTERN_GROUPS)


def cpu_seconds_per_call(function, iterations: int, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        started = time.process_time()
        for _ in range(iterations):
            function()
        samples.append((time.process_time() - started) / iterations)
    return statistics.median(samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bytes", type=int, default=200_068)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    body = response_body(args.bytes)
    searchable = searchable_body(body)

    _compile_pattern_set.cache_clear()
    cold_started = time.process_time()
    rust_matcher(searchable)
    cold_seconds = time.process_time() - cold_started

    python_seconds = cpu_seconds_per_call(lambda: python_reference(searchable), args.iterations)
    rust_seconds = cpu_seconds_per_call(lambda: rust_matcher(searchable), args.iterations)

    observation = ProbeObservation(
        domain="example.com",
        host="example.com",
        scheme="https",
        dns=DnsResult(host="example.com", status="ok", ips=["93.184.216.34"]),
        fetch=HttpFetchResult(
            url="https://example.com/",
            final_url="https://example.com/",
            status=200,
            headers={},
            body=body,
            body_bytes=len(body.encode("utf-8")),
            title="Ordinary page",
        ),
    )
    full_started = time.process_time()
    result = None
    for _ in range(args.iterations):
        result = classify(observation)
    full_seconds = (time.process_time() - full_started) / args.iterations

    speedup = python_seconds / rust_seconds if rust_seconds else float("inf")
    print(f"response_bytes={args.bytes}")
    print(f"iterations_per_batch={args.iterations} repeats=5")
    print(f"rust_first_use_cpu_ms={cold_seconds * 1000:.3f}")
    print(f"python_marker_scan_cpu_ms={python_seconds * 1000:.3f}")
    print(f"rust_marker_scan_cpu_ms={rust_seconds * 1000:.3f}")
    print(f"marker_scan_speedup={speedup:.2f}x")
    print(f"full_classify_with_rust_cpu_ms={full_seconds * 1000:.3f}")
    print(f"full_classification={result.verdict.value if result else 'none'}")
    print("network_http_dns_included=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
