# SPDX-License-Identifier: AGPL-3.0-or-later
"""Normalisation rules (FR-4): lowercase, IDNA, host vs registered domain."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from blockprobe.portals.domain_normalizer import (
    DomainRejected,
    normalize_domain,
    parse_domain_input,
    try_normalize_domain,
)


def test_normalises_url_to_host_and_registered_domain() -> None:
    item = normalize_domain("HTTPS://WWW.Example.COM:443/path?q=1#fragment")
    assert item.host == "www.example.com"
    assert item.domain == "example.com"
    assert item.was_url is True
    assert item.is_subdomain is True


def test_normalises_bare_hostname() -> None:
    item = normalize_domain("Online.CasinoSanRemo.IT.")
    assert item.host == "online.casinosanremo.it"
    assert item.domain == "casinosanremo.it"
    assert item.was_url is False


def test_drops_scheme_credentials_and_port() -> None:
    item = normalize_domain("http://user:pass@www.betfair.it:8080/login")
    assert item.host == "www.betfair.it"
    assert item.domain == "betfair.it"


def test_ignores_other_hosts_in_the_query_string() -> None:
    item = normalize_domain("https://example.com/redirect?to=evil.test")
    assert item.host == "example.com"


def test_idna_is_punycoded() -> None:
    item = normalize_domain("пример.рф")
    assert item.host == "xn--e1afmkfd.xn--p1ai"
    assert item.domain == "xn--e1afmkfd.xn--p1ai"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "localhost",
        "localhost.localdomain",
        "printer.local",
        "127.0.0.1",
        "0.0.0.0",
        "10.1.2.3",
        "192.168.1.10",
        "169.254.1.1",
        "::1",
        "fd00::1",
        "http://192.168.0.1/router",
        "http://10.0.0.5/",
        "something.onion",
        "this-hostname-is-way-too-long-" + "a" * 240 + ".example",
        "exa mple.com",
        "http://",
        "not_a_domain",
        "just one word",
        "example.c",
    ],
)
def test_rejects_unusable_values(value: str) -> None:
    with pytest.raises(DomainRejected):
        normalize_domain(value)
    assert try_normalize_domain(value) is None


def test_rejects_dangerous_link_schemes() -> None:
    for value in ("mailto:help@example.com", "javascript:alert(1)", "tel:+391234"):
        with pytest.raises(DomainRejected):
            normalize_domain(value)


def test_parse_domain_input_deduplicates_by_registered_domain() -> None:
    result = parse_domain_input(
        "www.one-casino.it, online.one-casino.it\n"
        "not a domain\n"
        "two-casino.it two-casino.it\n"
    )
    assert result.domains == ["one-casino.it", "two-casino.it"]
    assert result.rejected
    hosts = {item.host for item in result.accepted}
    assert hosts <= {"www.one-casino.it", "online.one-casino.it", "two-casino.it"}


@given(st.text(max_size=80))
@hypothesis_settings(max_examples=250, deadline=None)
def test_normalisation_never_crashes_and_keeps_invariants(value: str) -> None:
    try:
        item = normalize_domain(value)
    except DomainRejected:
        return
    assert item.host == item.host.lower()
    assert item.host == item.domain or item.host.endswith("." + item.domain)
    assert " " not in item.host
    assert item.host == item.host.strip(".")


@given(
    st.from_regex(
        r"[a-z0-9]([a-z0-9-]{0,20}[a-z0-9])?\.(com|org|net)",
        fullmatch=True,
    )
)
@hypothesis_settings(max_examples=100, deadline=None)
def test_registered_domains_round_trip(host: str) -> None:
    item = normalize_domain(host)
    assert item.host == item.domain == host
