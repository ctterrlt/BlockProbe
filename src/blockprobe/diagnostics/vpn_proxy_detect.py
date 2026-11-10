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
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
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

@dataclass(frozen=True)
class DnsProvider:
    """One locally known resolver profile; a hint, not a live blocklist."""

    id: str
    name: str
    hostname: str
    servers: tuple[str, ...]
    filtering: bool
    categories: tuple[str, ...]
    notes: str

    def matches(self, server: str) -> bool:
        """Return True when a detected resolver belongs to this profile."""
        return _clean_server(server) in self.servers


@lru_cache(maxsize=1)
def _load_dns_providers() -> tuple[DnsProvider, ...]:
    """Load the bundled provider catalog without any network access."""
    resource = files("blockprobe").joinpath("data", "dns_providers.toml")
    with resource.open("rb") as handle:
        payload = tomllib.load(handle)
    providers: list[DnsProvider] = []
    for item in payload.get("providers", []):
        servers = tuple(str(server) for server in item.get("servers", []))
        if not servers:
            continue
        providers.append(
            DnsProvider(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                hostname=str(item.get("hostname", "")),
                servers=servers,
                filtering=bool(item.get("filtering", False)),
                categories=tuple(str(category) for category in item.get("categories", [])),
                notes=str(item.get("notes", "")),
            )
        )
    return tuple(providers)


_DNS_PROVIDERS = _load_dns_providers()
_DNS_PROVIDER_BY_SERVER = {
    server: provider for provider in _DNS_PROVIDERS for server in provider.servers
}

# Local resolver aliases are not provider entries.
_DNS_LOCAL_LABELS = {
    "127.0.0.53": "systemd-resolved stub",
    "127.0.0.1": "local resolver",
    "::1": "local resolver",
}

_DNS_LABELS = {
    **_DNS_LOCAL_LABELS,
    **{server: provider.name for provider in _DNS_PROVIDERS for server in provider.servers},
}

# DNS services whose default/family profiles are explicitly designed to filter
# adult, malware or gambling content. A custom profile can be hosted on any IP;
# this list is evidence, not proof. Ordinary Quad9 and OpenDNS Home are not
# included because their malware filtering is not a gambling blocker.
_DNS_FILTERS = {
    server: provider.name
    for provider in _DNS_PROVIDERS
    if provider.filtering
    for server in provider.servers
}

_DNS_LINE = re.compile(
    r"^\s*(?:nameserver|Current DNS Server|DNS Servers)\s*:?\s*(.+)$",
    re.IGNORECASE,
)
_DNS_TOKEN = re.compile(r"[0-9a-fA-F:.%]+")


def _is_android_runtime() -> bool:
    return sys.platform.startswith("android") or bool(
        os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA")
    )


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
    # NO_PROXY is only a bypass list, not evidence that a proxy is active.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
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
    if _is_android_runtime():
        # Android may not expose its active per-network resolver in
        # resolv.conf. These read-only properties are a best-effort fallback;
        # a VPN or Private DNS profile can still be hidden from app processes.
        for index in range(1, 5):
            value = _run(["getprop", f"net.dns{index}"], timeout=0.5)
            servers.extend(_server_tokens(value))
    elif sys.platform == "darwin":
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


def dns_provider_profile(servers: list[str] | None = None) -> list[DnsProvider]:
    """Return known provider profiles for the configured resolvers.

    Only locally bundled knowledge is used. A custom profile hosted on a known
    IP is still reported as that provider's default profile, so callers must
    present the result as a hint, not as proof of the live policy.
    """
    values = servers if servers is not None else system_dns_servers()
    profiles: list[DnsProvider] = []
    seen: set[str] = set()
    for server in values:
        provider = _DNS_PROVIDER_BY_SERVER.get(_clean_server(server))
        if provider is not None and provider.id not in seen:
            profiles.append(provider)
            seen.add(provider.id)
    return profiles


def dns_provider_for_hostname(hostname: str | None) -> DnsProvider | None:
    """Match an exact Private DNS hostname against the local provider catalog."""
    normalized = (hostname or "").strip().rstrip(".").casefold()
    if not normalized:
        return None
    return next(
        (
            provider
            for provider in _DNS_PROVIDERS
            if provider.hostname.casefold() == normalized
        ),
        None,
    )


def dns_filter_categories(servers: list[str] | None = None) -> list[str]:
    """Return documented categories for every known resolver profile.

    This includes providers such as Quad9 whose malware filtering is real but
    is not counted by :func:`dns_filter_evidence` as a family/content filter.
    """
    return list(
        dict.fromkeys(
            category
            for provider in dns_provider_profile(servers)
            for category in provider.categories
        )
    )


def dns_provider_notes(servers: list[str] | None = None) -> list[str]:
    """Return conservative, local-only policy notes for known resolvers.

    Notes are English-only diagnostic text. The UI should translate the
    provider name, categories and caveat instead of displaying these verbatim.
    """
    notes: list[str] = []
    for provider in dns_provider_profile(servers):
        if provider.filtering:
            categories = ", ".join(provider.categories) or "unspecified"
            notes.append(
                f"{provider.name} is a filtering resolver (categories: {categories}). "
                f"{provider.notes}"
            )
        else:
            notes.append(f"{provider.name}: {provider.notes}")
    return notes


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
            # ``scutil --proxy`` includes disabled proxy keys too. Report only
            # active switches and never echo a raw server/credential value.
            active = {
                "HTTPEnable": "HTTP proxy enabled",
                "HTTPSEnable": "HTTPS proxy enabled",
                "SOCKSEnable": "SOCKS proxy enabled",
                "ProxyAutoConfigEnable": "automatic proxy configuration enabled",
                "ProxyAutoDiscoveryEnable": "automatic proxy discovery enabled",
            }
            found: list[str] = []
            for key, label in active.items():
                if re.search(rf"^\s*{key}\s*:\s*1\s*$", result.stdout, re.MULTILINE):
                    found.append(label)
            return found
        except (OSError, subprocess.SubprocessError):
            return []
    if sys.platform.startswith("win"):
        try:
            import winreg

            path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                if enabled:
                    # Do not copy the configured server or any user-info into
                    # diagnostics and reports; only record that one is active.
                    return ["Windows system proxy enabled"]
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
