# SPDX-License-Identifier: AGPL-3.0-or-later
"""Diagnostics for the 'why is it leaking?' question (FR-11)."""

from __future__ import annotations

from blockprobe.diagnostics.resolver_compare import compare, control_resolve
from blockprobe.diagnostics.system_dns import (
    browser_doh_hint,
    collect_network_context,
    hosts_entries,
    ipv6_available,
    private_dns_hint,
)
from blockprobe.diagnostics.vpn_proxy_detect import (
    dns_filter_evidence,
    hosts_block_entries,
    proxy_environment,
    resolver_details,
    resolver_provider,
    system_dns_servers,
    system_proxy,
    vpn_interfaces,
    vpn_provider_names,
)

__all__ = [
    "browser_doh_hint",
    "collect_network_context",
    "compare",
    "control_resolve",
    "dns_filter_evidence",
    "hosts_block_entries",
    "hosts_entries",
    "ipv6_available",
    "private_dns_hint",
    "proxy_environment",
    "resolver_details",
    "resolver_provider",
    "system_dns_servers",
    "system_proxy",
    "vpn_interfaces",
    "vpn_provider_names",
]
