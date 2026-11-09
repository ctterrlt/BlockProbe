#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare the Rust and Python system-resolver fallbacks on one host."""

from __future__ import annotations

import argparse
import json
import platform
import socket
import statistics
import sys
import time

from blockprobe.probe.rust_engine import system_resolve


def python_system_resolve(host: str) -> list[str]:
    ascii_host = host.encode("idna").decode("ascii")
    answers = socket.getaddrinfo(ascii_host, 0, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(answer[4][0] for answer in answers))


def measure(function, host: str, samples: int) -> dict[str, float]:  # type: ignore[no-untyped-def]
    for _ in range(50):
        function(host)
    timings = []
    cpu_start = time.process_time_ns()
    for _ in range(samples):
        start = time.perf_counter_ns()
        function(host)
        timings.append(time.perf_counter_ns() - start)
    cpu_ns = time.process_time_ns() - cpu_start
    ordered = sorted(timings)
    return {
        "p50_us": statistics.median(ordered) / 1_000,
        "p95_us": ordered[int(0.95 * (len(ordered) - 1))] / 1_000,
        "cpu_us": cpu_ns / samples / 1_000,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--samples", type=int, default=2_000)
    args = parser.parse_args()
    if args.samples < 100:
        parser.error("--samples must be at least 100")
    python_result = python_system_resolve(args.host)
    rust_result = system_resolve(args.host)
    if rust_result is None or set(rust_result) != set(python_result):
        raise SystemExit(
            f"Resolver outputs differ: Python={python_result!r}, Rust={rust_result!r}"
        )
    print(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "host": args.host,
                "samples": args.samples,
                "python_socket_getaddrinfo": measure(
                    python_system_resolve, args.host, args.samples
                ),
                "rust_ffi_getaddrinfo": measure(system_resolve, args.host, args.samples),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
