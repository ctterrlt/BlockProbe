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


def test_private_dns_hostname_uses_exact_local_provider_catalog_match() -> None:
    provider = vpn.dns_provider_for_hostname("DNS.ADGuard-DNS.COM.")
    assert provider is not None
    assert provider.name == "AdGuard DNS"
    assert "94.140.14.14" in provider.servers
    assert vpn.dns_provider_for_hostname("custom.example") is None


def test_vpn_provider_names_are_hints_not_verdicts() -> None:
    names = vpn.vpn_provider_names(["tailscale0", "nordlynx", "tun0"])
    assert any("Tailscale" in name for name in names)
    assert any("NordVPN" in name for name in names)
    assert any("Generic VPN/tunnel" in name for name in names)


def test_android_dns_servers_read_active_network_properties(monkeypatch) -> None:
    properties = {
        "net.dns1": " 192.0.2.53\n",
        "net.dns2": "2001:db8::53\n",
    }
    monkeypatch.setattr(vpn, "_is_android_runtime", lambda: True)
    monkeypatch.setattr(
        vpn,
        "_run",
        lambda command, timeout=3.0: properties.get(command[1], ""),
    )

    assert vpn.system_dns_servers() == ["192.0.2.53", "2001:db8::53"]


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


def test_known_dns_providers_expose_conservative_filter_hints() -> None:
    profiles = vpn.dns_provider_profile(
        ["94.140.14.15", "1.1.1.1", "9.9.9.9", "192.0.2.53"]
    )
    assert [profile.name for profile in profiles] == [
        "AdGuard DNS Family",
        "Cloudflare",
        "Quad9 (malware and phishing filtering)",
    ]
    assert vpn.dns_filter_categories(["94.140.14.15"]) == [
        "ads",
        "trackers",
        "phishing",
        "adult",
    ]

    notes = vpn.dns_provider_notes(["94.140.14.15"])
    assert len(notes) == 1
    assert "AdGuard DNS Family is a filtering resolver" in notes[0]
    assert "ad and tracker filtering" in notes[0]
    assert vpn.dns_provider_notes(["192.0.2.53"]) == []


def test_known_dns_provider_labels_cover_ipv6_and_keep_evidence_format() -> None:
    assert (
        vpn.resolver_provider("2606:4700:4700::1112")
        == "Cloudflare for Families (malware)"
    )
    assert (
        vpn.resolver_provider("2606:4700:4700::1113")
        == "Cloudflare for Families (malware and adult content)"
    )
    assert vpn.dns_filter_evidence(["1.1.1.2"]) == [
        "1.1.1.2 (Cloudflare for Families (malware))"
    ]
    assert vpn.dns_filter_evidence(["9.9.9.9"]) == []


def test_known_dns_provider_categories_cover_quad9_and_ipv6_endpoints() -> None:
    assert vpn.dns_filter_categories(["9.9.9.9"]) == ["malware", "phishing"]
    assert vpn.dns_filter_evidence(["9.9.9.9"]) == []

    assert vpn.resolver_provider("2a0d:2a00:1::") == "CleanBrowsing Family"
    family = vpn.dns_provider_profile(["185.228.168.168"])[0]
    assert family.categories == ("adult", "malware", "phishing")
    assert vpn.dns_filter_categories(["185.228.168.168"]) == [
        "adult",
        "malware",
        "phishing",
    ]
    assert vpn.resolver_provider("2a10:50c0::bad1:ff") == "AdGuard DNS Family"
    assert vpn.dns_filter_evidence(["2a0d:2a00:1::"]) == [
        "2a0d:2a00:1:: (CleanBrowsing Family)"
    ]
    assert vpn.dns_filter_evidence(["2a10:50c0::bad1:ff"]) == [
        "2a10:50c0::bad1:ff (AdGuard DNS Family)"
    ]


def test_known_dns_providers_expose_documented_hostnames() -> None:
    adguard = vpn.dns_provider_profile(["94.140.14.14"])[0]
    assert adguard.hostname == "dns.adguard-dns.com"
    assert adguard.name == "AdGuard DNS"
    assert adguard.matches("94.140.14.14")
    assert adguard.matches("2a10:50c0::ad1:ff")
    assert not adguard.matches("192.0.2.53")

    family = vpn.dns_provider_profile(["94.140.14.15"])[0]
    assert family.hostname == "family.adguard-dns.com"
    assert family.categories == ("ads", "trackers", "phishing", "adult")

    cloudflare = vpn.dns_provider_profile(["1.1.1.2"])[0]
    assert cloudflare.hostname == "security.cloudflare-dns.com"
    assert cloudflare.categories == ("malware", "phishing")

    quad9 = {
        profile.id: profile
        for profile in vpn.dns_provider_profile(
            ["9.9.9.9", "9.9.9.10", "9.9.9.11"]
        )
    }
    assert quad9["quad9"].hostname == "dns.quad9.net"
    assert quad9["quad9-unfiltered"].hostname == "dns10.quad9.net"
    assert quad9["quad9-unfiltered"].categories == ()
    assert quad9["quad9-ecs"].hostname == "dns11.quad9.net"

    tailscale = vpn.dns_provider_profile(["100.100.100.100"])[0]
    assert tailscale.hostname == ""


def test_android_network_snapshot_enriches_scan_dns_context() -> None:
    context = NetworkContext(resolvers=["10.0.2.3"])

    merged = system_dns.merge_android_network_context(
        context,
        ("94.140.14.14", "not-an-ip", "94.140.14.14"),
        private_dns_hostname="dns.adguard-dns.com",
        private_dns_active=True,
    )

    assert merged is context
    assert context.resolvers == ["94.140.14.14", "10.0.2.3"]
    assert context.resolver_labels[0] == (
        "dns.adguard-dns.com — AdGuard DNS — 94.140.14.14"
    )
    assert context.dns_filters == ["94.140.14.14 (AdGuard DNS)"]
    assert context.private_dns == "dns.adguard-dns.com"


def test_private_dns_hostname_adds_local_endpoint_and_filter_hints() -> None:
    context = NetworkContext(resolvers=["10.0.2.3"])

    system_dns.merge_android_network_context(
        context,
        (),
        private_dns_hostname="dns.adguard-dns.com",
        private_dns_active=True,
    )

    assert context.resolvers == ["10.0.2.3"]
    assert any(
        label.startswith("dns.adguard-dns.com — AdGuard DNS — 94.140.14.14")
        for label in context.resolver_labels
    )
    assert context.dns_filters == ["dns.adguard-dns.com (AdGuard DNS)"]
