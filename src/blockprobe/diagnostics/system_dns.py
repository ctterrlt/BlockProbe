# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only system DNS, hosts-file and IPv6 diagnostics.

Everything here runs with ordinary user rights. Android Private DNS and browser
Secure DNS (DoH) cannot be read reliably without elevated rights, so the app
reports what it cannot see instead of guessing.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

from blockprobe import __version__
from blockprobe.core.models import NetworkContext, utc_now
from blockprobe.probe.dns import system_resolvers

_LOGGER = logging.getLogger("blockprobe.diagnostics.system_dns")


def hosts_path() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/drivers/etc/hosts"
    return Path("/etc/hosts")


def hosts_entries(limit: int = 50) -> list[str]:
    """Return non-comment hosts-file entries, without trying to change them."""
    path = hosts_path()
    entries: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            clean = line.split("#", 1)[0].strip()
            if not clean:
                continue
            entries.append(clean[:200])
            if len(entries) >= limit:
                break
    except OSError:
        return []
    return entries


def ipv6_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        infos = socket.getaddrinfo("localhost", None, socket.AF_INET6)
        return bool(infos)
    except OSError:
        return False


def private_dns_hint() -> str:
    if os.environ.get("ANDROID_ROOT") or sys.platform.startswith("android"):
        return (
            "Android Private DNS cannot be read without elevated rights; check "
            "Settings > Network > Private DNS in the browser/device yourself."
        )
    return (
        "Not inspectable on this platform; Android Private DNS is checked only on Android."
    )


def browser_doh_hint() -> str:
    """Look for Chrome/Chromium DnsOverHttps policy files on Linux."""
    policy_paths = [
        Path("/etc/opt/chrome/policies/managed"),
        Path("/etc/chromium/policies/managed"),
        Path("/etc/opt/chromium/policies/managed"),
    ]
    for directory in policy_paths:
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "DnsOverHttpsMode" in text or "dns_over_https" in text.lower():
                return f"A Secure DNS policy is present in {path}."
    return (
        "Unknown. A browser can use DNS-over-HTTPS and bypass a DNS-based "
        "blocker; BlockProbe uses the system resolver only."
    )


def collect_network_context() -> NetworkContext:
    from blockprobe.diagnostics.vpn_proxy_detect import (
        dns_filter_evidence,
        hosts_block_entries,
        proxy_environment,
        resolver_details,
        system_dns_servers,
        system_proxy,
        vpn_interfaces,
        vpn_provider_names,
    )

    interfaces = vpn_interfaces()
    resolvers = system_dns_servers()
    filters = dns_filter_evidence(resolvers)
    notes = [
        "BlockProbe uses the system resolver and the device's own network path.",
        "Python is not Chrome: browser DoH, Private DNS, QUIC and another "
        "VPN/profile can produce different results.",
        "Resolver and VPN names are inferred from local configuration only; "
        "no lookup leaves the device for this diagnostic.",
    ]
    if filters:
        notes.append(
            "A resolver with content filtering is active. Its policy is local "
            "and may or may not include gambling domains."
        )
    return NetworkContext(
        timestamp=utc_now(),
        os_name=f"{sys.platform} {os.uname().release if hasattr(os, 'uname') else ''}".strip(),
        app_version=__version__,
        resolvers=resolvers or system_resolvers(),
        resolver_labels=resolver_details(resolvers),
        dns_filters=filters,
        hosts_entries=hosts_entries(),
        hosts_block_entries=hosts_block_entries(),
        proxies=list(dict.fromkeys([*proxy_environment(), *system_proxy()])),
        vpn_interfaces=interfaces,
        vpn_providers=vpn_provider_names(interfaces),
        ipv6=ipv6_available(),
        private_dns=private_dns_hint(),
        browser_doh=browser_doh_hint(),
        notes=notes,
    )


def getprop(name: str, timeout: float = 2.0) -> str | None:
    """Best-effort Android ``getprop`` reader; never raises."""
    try:
        result = subprocess.run(
            ["getprop", name],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None
