# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local DNS/VPN classification and network-context serialisation."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from blockprobe.core.models import NetworkContext
from blockprobe.diagnostics import resolver_compare, system_dns
from blockprobe.diagnostics import vpn_proxy_detect as vpn
from blockprobe.http_user_agent import CHROME_USER_AGENT


def test_filtering_dns_is_separated_from_ordinary_resolvers() -> None:
    evidence = vpn.dns_filter_evidence(
        [
            "208.67.222.123",
            "9.9.9.9",
            "1.1.1.1",
            "100.100.100.100",
        ]
    )
    assert evidence == ["208.67.222.123 (OpenDNS FamilyShield)"]
    assert vpn.resolver_provider("10.1.2.3") == "local network resolver"
    assert vpn.resolver_provider("1.1.1.1") == "Cloudflare"
    assert vpn.resolver_provider("100.100.100.100") == "Tailscale MagicDNS"


def test_vpn_provider_names_are_hints_not_verdicts() -> None:
    names = vpn.vpn_provider_names(["tailscale0", "nordlynx", "tun0"])
    assert any("Tailscale" in name for name in names)
    assert any("NordVPN" in name for name in names)
    assert any("Generic VPN/tunnel" in name for name in names)


def test_hosts_sinkhole_count_ignores_standard_loopback_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "127.0.0.1 localhost\n"
        "::1 localhost ip6-localhost ip6-loopback\n"
        "0.0.0.0 blocked.example\n"
        "127.0.0.1 another.example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vpn, "_hosts_path", lambda: hosts)
    assert vpn.hosts_block_entries() == 2


def test_network_context_round_trip_keeps_new_fields() -> None:
    context = NetworkContext(
        resolvers=["208.67.222.123"],
        resolver_labels=["208.67.222.123 (OpenDNS FamilyShield)"],
        dns_filters=["208.67.222.123 (OpenDNS FamilyShield)"],
        hosts_block_entries=2,
        vpn_interfaces=["tailscale0"],
        vpn_providers=["Tailscale via tailscale0"],
    )
    restored = NetworkContext.from_dict(context.to_dict())
    assert restored.dns_filters == context.dns_filters
    assert restored.hosts_block_entries == 2
    assert restored.vpn_providers == context.vpn_providers


def test_no_proxy_bypass_list_is_not_reported_as_a_proxy(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)

    assert vpn.proxy_environment() == []


def test_system_proxy_is_included_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(vpn, "proxy_environment", lambda: ["HTTPS_PROXY=proxy.example:8080"])
    monkeypatch.setattr(vpn, "system_proxy", lambda: ["HTTP proxy enabled"])

    context = system_dns.collect_network_context()

    assert "HTTPS_PROXY=proxy.example:8080" in context.proxies
    assert "HTTP proxy enabled" in context.proxies


@respx.mock
async def test_control_resolver_uses_the_shared_chrome_user_agent() -> None:
    route = respx.get("https://resolver.example/dns-query").mock(
        return_value=httpx.Response(
            200,
            json={"Answer": [{"type": 1, "data": "192.0.2.1"}]},
        )
    )

    assert await resolver_compare.control_resolve(
        "example.com", "https://resolver.example/dns-query"
    )
    assert route.calls[0].request.headers["user-agent"] == CHROME_USER_AGENT
