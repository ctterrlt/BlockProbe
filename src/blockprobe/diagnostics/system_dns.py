# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only system DNS, hosts-file and IPv6 diagnostics.

Everything here runs with ordinary user rights. The Android profile supplements
this Python-only snapshot through the bundled native network adapter, which
reads ``ConnectivityManager``/``LinkProperties`` under the normal install-time
``ACCESS_NETWORK_STATE`` permission. This module itself does not guess Android
Private DNS or browser Secure DNS (DoH) settings.
"""

from __future__ import annotations

import ipaddress
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
    """Return the Python collector's best-effort hint for Private DNS.

    The Android profile replaces this text with local ``LinkProperties`` data
    when its native adapter is available.
    """
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
            "and may or may not include the domains selected for this scan."
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


def merge_android_network_context(
    context: NetworkContext,
    dns_servers: tuple[str, ...] | list[str],
    *,
    private_dns_hostname: str | None = None,
    private_dns_active: bool = False,
) -> NetworkContext:
    """Merge Android's active-network snapshot into a scan's local context.

    The Kotlin bridge reads ``LinkProperties`` using the normal install-time
    network-state permission. The result enriches reports and DNS-filter
    evidence; it performs no lookup and does not change how probes resolve.
    """
    from blockprobe.diagnostics.vpn_proxy_detect import (
        dns_filter_evidence,
        dns_provider_for_hostname,
        dns_provider_profile,
        resolver_provider,
    )

    normalized: list[str] = []
    for value in dns_servers:
        raw = value.strip().split("%", 1)[0]
        try:
            server = ipaddress.ip_address(raw).compressed
        except ValueError:
            continue
        if server not in normalized:
            normalized.append(server)

    if normalized:
        context.resolvers = list(dict.fromkeys([*normalized, *context.resolvers]))
        providers = {
            server: provider
            for provider in dns_provider_profile(context.resolvers)
            for server in provider.servers
        }
        labels: list[str] = []
        for server in context.resolvers:
            provider = providers.get(server)
            if provider is not None:
                labels.append(f"{provider.hostname} — {provider.name} — {server}")
            else:
                labels.append(f"{server} ({resolver_provider(server)})")
        context.resolver_labels = labels
        context.dns_filters = dns_filter_evidence(context.resolvers)

    provider = dns_provider_for_hostname(private_dns_hostname)
    if provider is not None:
        known_endpoints = ", ".join(provider.servers)
        endpoint_label = (
            f"{provider.hostname} — {provider.name} — {known_endpoints}"
        )
        if endpoint_label not in context.resolver_labels:
            context.resolver_labels.append(endpoint_label)
        if provider.filtering and not any(
            provider.name in item for item in context.dns_filters
        ):
            context.dns_filters.append(f"{provider.hostname} ({provider.name})")

    if private_dns_hostname:
        context.private_dns = private_dns_hostname
    elif private_dns_active:
        context.private_dns = "enabled"
    return context


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
