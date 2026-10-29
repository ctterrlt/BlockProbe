# SPDX-License-Identifier: AGPL-3.0-or-later
"""Best-effort VPN, DNS and proxy detection.

A VPN, a filtering DNS resolver or a proxy changes the network path and can
make a blocker look broken. Detection is read-only and local: environment
variables, interface names, the system resolver configuration and, where
available, NetworkManager or platform proxy settings. No lookup is sent to a
third party.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_VPN_PREFIXES = (
    "tun",
    "tap",
    "wg",
    "ppp",
    "utun",
    "ipsec",
    "nordlynx",
    "proton",
    "mullvad",
    "tailscale",
    "zt",
    "zerotier",
    "nebula",
)

_VPN_PROVIDERS = (
    ("nordlynx", "NordVPN"),
    ("proton", "Proton VPN"),
    ("mullvad", "Mullvad"),
    ("tailscale", "Tailscale"),
    ("zerotier", "ZeroTier"),
    ("nebula", "Nebula"),
    ("ipsec", "IPsec VPN"),
    ("utun", "macOS VPN/tunnel"),
    ("wg", "WireGuard"),
    ("tun", "Generic VPN/tunnel"),
    ("tap", "Generic VPN/tunnel"),
    ("ppp", "PPP/VPN"),
    ("zt", "ZeroTier"),
)

# DNS services whose default/family profiles are explicitly designed to filter
# adult, malware or gambling content. A custom profile can be hosted on any IP;
# this list is evidence, not proof. Ordinary Quad9 and OpenDNS Home are not
# included because their malware filtering is not a gambling blocker.
_DNS_FILTERS = {
    "208.67.222.123": "OpenDNS FamilyShield",
    "208.67.220.123": "OpenDNS FamilyShield",
    "185.228.168.168": "CleanBrowsing Family",
    "185.228.169.168": "CleanBrowsing Family",
    "185.228.168.10": "CleanBrowsing Adult",
    "185.228.169.11": "CleanBrowsing Adult",
    "1.1.1.3": "Cloudflare for Families (malware)",
    "1.0.0.3": "Cloudflare for Families (malware)",
    "1.1.1.2": "Cloudflare for Families (adult content)",
    "1.0.0.2": "Cloudflare for Families (adult content)",
    "94.140.14.15": "AdGuard Family",
    "94.140.15.16": "AdGuard Family",
}

_DNS_LABELS = {
    "127.0.0.53": "systemd-resolved stub",
    "127.0.0.1": "local resolver",
    "::1": "local resolver",
    "100.100.100.100": "Tailscale MagicDNS",
    "1.1.1.1": "Cloudflare",
    "1.0.0.1": "Cloudflare",
    "8.8.8.8": "Google",
    "8.8.4.4": "Google",
    "9.9.9.9": "Quad9 (malware filtering, not gambling-specific)",
    "149.112.112.112": "Quad9 (malware filtering, not gambling-specific)",
    "208.67.222.222": "OpenDNS Home",
    "208.67.220.220": "OpenDNS Home",
    "208.67.222.123": "OpenDNS FamilyShield",
    "208.67.220.123": "OpenDNS FamilyShield",
    "185.228.168.168": "CleanBrowsing Family",
    "185.228.169.168": "CleanBrowsing Family",
    "185.228.168.10": "CleanBrowsing Adult",
    "185.228.169.11": "CleanBrowsing Adult",
    "1.1.1.3": "Cloudflare for Families (malware)",
    "1.0.0.3": "Cloudflare for Families (malware)",
    "1.1.1.2": "Cloudflare for Families (adult content)",
    "1.0.0.2": "Cloudflare for Families (adult content)",
    "94.140.14.15": "AdGuard Family",
    "94.140.15.16": "AdGuard Family",
}

_DNS_LINE = re.compile(
    r"^\s*(?:nameserver|Current DNS Server|DNS Servers)\s*:?\s*(.+)$",
    re.IGNORECASE,
)
_DNS_TOKEN = re.compile(r"[0-9a-fA-F:.%]+")


def _mask(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "configured"
    if not parts.scheme or not parts.hostname:
        return url if len(url) < 40 else "configured"
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def proxy_environment() -> list[str]:
    values: list[str] = []
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        value = os.environ.get(name) or os.environ.get(name.lower())
        if value:
            values.append(f"{name}={_mask(value)}")
    return values


def _run(command: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout


def _clean_server(value: str) -> str:
    server = value.strip().split("#", 1)[0].strip()
    server = server.split("%", 1)[0].strip()
    if server.startswith("[") and "]" in server:
        server = server[1 : server.index("]")]
    if server.count(":") == 1:
        host, _, port = server.partition(":")
        if port.isdigit():
            server = host
    return server.rstrip(".")


def _server_tokens(text: str) -> list[str]:
    values: list[str] = []
    for match in _DNS_TOKEN.finditer(text):
        server = _clean_server(match.group(0))
        if server and server not in values:
            values.append(server)
    return values


def system_dns_servers() -> list[str]:
    """Return active resolver IPs from local configuration, including upstreams.

    On systemd-resolved systems ``/etc/resolv.conf`` usually contains only the
    stub ``127.0.0.53``; ``resolvectl status`` exposes the real per-link
    upstreams. Nothing here performs a DNS query.
    """
    servers: list[str] = []
    if sys.platform == "darwin":
        for line in _run(["scutil", "--dns"]).splitlines():
            if "nameserver" in line and ":" in line:
                servers.extend(_server_tokens(line.split(":", 1)[1]))
    elif sys.platform.startswith("win"):
        for line in _run(["ipconfig", "/all"]).splitlines():
            if "DNS Servers" in line and ":" in line:
                servers.extend(_server_tokens(line.split(":", 1)[1]))
    else:
        for path in (Path("/etc/resolv.conf"), Path("/run/systemd/resolve/resolv.conf")):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                match = _DNS_LINE.match(line)
                if match:
                    servers.extend(_server_tokens(match.group(1)))
        for line in _run(["resolvectl", "status"]).splitlines():
            match = _DNS_LINE.match(line)
            if match:
                servers.extend(_server_tokens(match.group(1)))
        for line in _run(
            ["nmcli", "-t", "-f", "IP4.DNS,IP6.DNS", "device", "show"]
        ).splitlines():
            if ":" in line:
                servers.extend(_server_tokens(line.split(":", 1)[1]))

    if not servers:
        try:
            servers = [
                _clean_server(str(item))
                for item in __import__(
                    "blockprobe.probe.dns", fromlist=["system_resolvers"]
                ).system_resolvers()
            ]
        except Exception:
            servers = []
    return list(dict.fromkeys(server for server in servers if server))


def resolver_provider(server: str) -> str:
    """Describe one resolver using local knowledge only."""
    cleaned = _clean_server(server)
    if cleaned in _DNS_LABELS:
        return _DNS_LABELS[cleaned]
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError:
        return "unknown resolver"
    if address.is_loopback or address.is_unspecified:
        return "local resolver"
    if address.is_private or address.is_link_local:
        return "local network resolver"
    return "public resolver"


def resolver_details(servers: list[str] | None = None) -> list[str]:
    values = servers if servers is not None else system_dns_servers()
    return [f"{server} ({resolver_provider(server)})" for server in values]


def dns_filter_evidence(servers: list[str] | None = None) -> list[str]:
    """Return only resolvers that are known to filter content by default."""
    values = servers if servers is not None else system_dns_servers()
    evidence: list[str] = []
    for server in values:
        cleaned = _clean_server(server)
        provider = _DNS_FILTERS.get(cleaned)
        if provider:
            evidence.append(f"{cleaned} ({provider})")
    return list(dict.fromkeys(evidence))


def vpn_interfaces() -> list[str]:
    names: list[str] = []
    try:
        for index, name in socket.if_nameindex():
            del index
            lowered = name.lower()
            if any(lowered.startswith(prefix) for prefix in _VPN_PREFIXES):
                names.append(name)
    except OSError:
        return []
    return names


def vpn_provider_names(interfaces: list[str] | None = None) -> list[str]:
    """Map local tunnel interface names to likely providers.

    This is a naming hint, not an ownership proof: WireGuard, Tailscale and
    corporate tunnels all share the same kernel interfaces.
    """
    names: list[str] = []
    values = interfaces if interfaces is not None else vpn_interfaces()
    for interface in values:
        lowered = interface.lower()
        for prefix, provider in _VPN_PROVIDERS:
            if lowered.startswith(prefix):
                names.append(f"{provider} via {interface}")
                break
    for line in _run(
        ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"]
    ).splitlines():
        name, _, connection_type = line.partition(":")
        if connection_type.lower() in {"vpn", "wireguard", "tun"}:
            names.append(f"NetworkManager profile {name} ({connection_type})")
    return list(dict.fromkeys(names))


def _hosts_path() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/drivers/etc/hosts"
    return Path("/etc/hosts")


def hosts_block_entries(limit: int = 10_000) -> int:
    """Count non-comment hosts-file entries that point a name at a sinkhole.

    Localhost entries and the standard IPv6 loopback alias are ignored. This
    can reveal a manual or third-party hosts-based blocker without reading
    website bodies.
    """
    count = 0
    try:
        lines = _hosts_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    for line in lines:
        clean = line.split("#", 1)[0].strip()
        if not clean:
            continue
        parts = clean.split()
        if len(parts) < 2:
            continue
        address = _clean_server(parts[0])
        hosts = [host.lower() for host in parts[1:]]
        standard_loopback = {
            "localhost",
            "localhost.localdomain",
            "ip6-localhost",
            "ip6-loopback",
        }
        if set(hosts) <= standard_loopback:
            continue
        if address in {"0.0.0.0", "127.0.0.1", "::", "::1"}:
            count += 1
            if count >= limit:
                break
    return count


def system_proxy() -> list[str]:
    """Platform proxy hints that are not visible through environment variables."""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["scutil", "--proxy"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            lines = [
                line.strip()
                for line in result.stdout.splitlines()
                if "Enable" in line or "Proxy" in line or "Server" in line
            ]
            return lines[:10]
        except (OSError, subprocess.SubprocessError):
            return []
    if sys.platform.startswith("win"):
        try:
            import winreg

            path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                if enabled:
                    server, _ = winreg.QueryValueEx(key, "ProxyServer")
                    return [f"ProxyServer={server}"]
        except OSError:
            return []
    return []


def collect() -> dict[str, object]:
    interfaces = vpn_interfaces()
    resolvers = system_dns_servers()
    return {
        "proxies": proxy_environment() + system_proxy(),
        "vpn_interfaces": interfaces,
        "vpn_providers": vpn_provider_names(interfaces),
        "resolvers": resolvers,
        "resolver_labels": resolver_details(resolvers),
        "dns_filters": dns_filter_evidence(resolvers),
        "hosts_block_entries": hosts_block_entries(),
    }
