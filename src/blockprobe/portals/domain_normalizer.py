# SPDX-License-Identifier: AGPL-3.0-or-later
"""Domain normalisation and validation using tldextract's offline snapshot.

Rules from FR-4 and FR-15: lowercase, IDNA/punycode, strip scheme, path, port
and trailing dot, reject IP addresses, localhost, private ranges and strings
that are not domains. The registered domain is the storage key; the hostname
exactly as listed is stored separately.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import tldextract

# suffix_list_urls=() keeps the extractor offline: it uses the bundled snapshot
# and never makes a network call just to normalise user input.
_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)

_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_SPLIT_RE = re.compile(r"[\s,;]+")
LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".test", ".invalid")


class DomainRejected(ValueError):
    """Raised when a value cannot be normalised into a public domain."""


@dataclass(frozen=True, slots=True)
class NormalizedDomain:
    host: str
    domain: str
    was_url: bool = False

    @property
    def is_subdomain(self) -> bool:
        return self.host != self.domain


@dataclass(frozen=True, slots=True)
class RejectedDomain:
    value: str
    reason: str


@dataclass(slots=True)
class DomainInput:
    accepted: list[NormalizedDomain]
    rejected: list[RejectedDomain]

    @property
    def domains(self) -> list[str]:
        return sorted({item.domain for item in self.accepted})

    @property
    def summary(self) -> str:
        if not self.accepted and not self.rejected:
            return "No domains were provided."
        parts = [f"{len(self.accepted)} accepted"]
        if self.rejected:
            parts.append(f"{len(self.rejected)} rejected")
        return ", ".join(parts)


def _idna(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise DomainRejected("not a valid internationalised domain name") from exc


def _reject_private(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise DomainRejected("IP addresses in private or reserved ranges are not allowed")
    raise DomainRejected("IP addresses are not allowed; use a domain name")


def normalize_domain(value: str) -> NormalizedDomain:
    """Normalise a URL, hostname or bare domain.

    Raises :class:`DomainRejected` with a user-readable reason when the value is
    empty, an IP address, localhost, private or syntactically invalid.
    """
    raw = (value or "").strip().strip("\"'")
    if not raw:
        raise DomainRejected("empty value")
    lowered = raw.lower()
    for prefix in ("mailto:", "tel:", "javascript:", "file:", "data:", "ftp:"):
        if lowered.startswith(prefix):
            raise DomainRejected(f"{prefix.rstrip(':')} links are not websites")

    was_url = "://" in lowered
    candidate = raw if was_url else f"http://{raw}"
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise DomainRejected("could not parse the value as a URL or domain") from exc

    host = parsed.hostname or ""
    host = host.strip().strip(".").lower()
    if not host:
        raise DomainRejected("no hostname found")
    if len(host) > 253:
        raise DomainRejected("hostname is too long")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(LOCAL_SUFFIXES):
        raise DomainRejected("localhost and local network names are not allowed")
    if host.endswith(".onion"):
        raise DomainRejected("Tor hidden services are not supported")

    _reject_private(host)
    host = _idna(host)
    _reject_private(host)
    if not _HOST_RE.match(host):
        raise DomainRejected("hostname contains characters that are not allowed")
    if "." not in host:
        raise DomainRejected("not a public domain name")
    parts = host.split(".")
    if any(not part for part in parts):
        raise DomainRejected("hostname contains an empty label")
    if len(parts[-1]) < 2:
        raise DomainRejected("top-level domain is too short")

    extracted = _EXTRACTOR(host)
    if not extracted.domain or not extracted.suffix:
        raise DomainRejected("not a recognised public domain")
    domain = f"{extracted.domain}.{extracted.suffix}".lower()
    _reject_private(domain)
    return NormalizedDomain(host=host, domain=domain, was_url=was_url)


def try_normalize_domain(value: str) -> NormalizedDomain | None:
    try:
        return normalize_domain(value)
    except DomainRejected:
        return None


def registered_domain(host: str) -> str:
    extracted = _EXTRACTOR(host)
    if not extracted.domain or not extracted.suffix:
        raise DomainRejected("not a recognised public domain")
    return f"{extracted.domain}.{extracted.suffix}".lower()


def parse_domain_input(text: str, max_entries: int = 10_000) -> DomainInput:
    """Parse pasted text: one domain per line, CSV or space separated."""
    accepted: list[NormalizedDomain] = []
    rejected: list[RejectedDomain] = []
    seen_hosts: set[str] = set()
    seen_domains: set[str] = set()
    count = 0
    for raw in _SPLIT_RE.split(text or ""):
        token = raw.strip().strip("\"'")
        if not token or token.startswith("#"):
            continue
        count += 1
        if count > max_entries:
            rejected.append(RejectedDomain(value="…", reason="too many entries in one import"))
            break
        try:
            item = normalize_domain(token)
        except DomainRejected as exc:
            rejected.append(RejectedDomain(value=token, reason=str(exc)))
            continue
        if item.host in seen_hosts:
            continue
        seen_hosts.add(item.host)
        seen_domains.add(item.domain)
        accepted.append(item)
    return DomainInput(accepted=accepted, rejected=rejected)


def is_public_domain(value: str) -> bool:
    try:
        normalize_domain(value)
    except DomainRejected:
        return False
    return True


def dedupe_hosts(domain: str, hosts: list[str]) -> list[str]:
    """Return clean hostnames for one registered domain, keeping listed order.

    A bare apex candidate is dropped when a more specific hostname for the same
    domain exists, because the apex and ``www`` variants are probed anyway.
    This also prevents a company name such as ``Codere.it S.r.l.`` from adding a
    spurious second hostname to the ADM fixture.
    """
    result: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        host = (host or "").strip().strip(".").lower()
        if not host or host in seen:
            continue
        try:
            normalized = normalize_domain(host)
        except DomainRejected:
            continue
        if normalized.domain != domain:
            continue
        seen.add(host)
        result.append(host)
    if domain in seen and any(host.endswith("." + domain) for host in result):
        result = [host for host in result if host != domain]
    return result
