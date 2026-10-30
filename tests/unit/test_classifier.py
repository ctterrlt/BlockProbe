# SPDX-License-Identifier: AGPL-3.0-or-later
"""Classification by content, never by status code alone (FR-7).

The synthetic rules used here exercise the engine mechanics; shipped rules are
covered separately from their bundled package resources.
"""

from __future__ import annotations

from blockprobe.core.enums import Confidence, ReasonCode, SignatureKind, Verdict
from blockprobe.core.models import (
    Classification,
    ConnectResult,
    DnsResult,
    DynamicSignature,
    HttpFetchResult,
    ProbeObservation,
    SignatureRule,
)
from blockprobe.probe.classifier import classify, load_signatures, sniff_key_phrases


def make_dns(
    host: str = "example.com",
    status: str = "ok",
    ips: list[str] | None = None,
    sinkhole: list[str] | None = None,
) -> DnsResult:
    return DnsResult(
        host=host,
        status=status,
        ips=list(ips if ips is not None else ["93.184.216.34"]),
        sinkhole_ips=list(sinkhole or []),
    )


def make_fetch(
    body: str = "",
    status: int | None = 200,
    title: str = "",
    redirect_chain: list[str] | None = None,
    error: str | None = None,
    error_kind: str | None = None,
    headers: dict[str, str] | None = None,
) -> HttpFetchResult:
    return HttpFetchResult(
        url="https://example.com/",
        final_url="https://example.com/",
        status=status,
        headers=headers or {},
        body=body,
        body_bytes=len(body.encode("utf-8")),
        redirect_chain=list(redirect_chain or []),
        error=error,
        error_kind=error_kind,
        title=title,
    )


def observe(
    fetch: HttpFetchResult | None = None,
    dns: DnsResult | None = None,
    connect: ConnectResult | None = None,
    control_resolves: bool | None = None,
) -> ProbeObservation:
    return ProbeObservation(
        domain="example.com",
        host="example.com",
        scheme="https",
        dns=dns or make_dns(),
        connect=connect,
        fetch=fetch,
        control_resolves=control_resolves,
    )


def embedded_fetch(body: str, title: str = "", final_url: str = "") -> HttpFetchResult:
    return HttpFetchResult(
        url=final_url or "https://embed.example/",
        final_url=final_url or "https://embed.example/",
        status=200,
        body=body,
        body_bytes=len(body.encode("utf-8")),
        title=title,
    )


def test_sinkhole_answer_is_blocked_with_high_confidence() -> None:
    observation = observe(dns=make_dns(ips=["0.0.0.0"], sinkhole=["0.0.0.0"]))
    result = classify(observation)
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.DNS_SINKHOLE.value
    assert result.confidence is Confidence.HIGH
    assert result.blocker_hint == "dns_filter"
    assert result.needs_review is False


def test_nxdomain_is_unreachable_and_flagged_for_review() -> None:
    observation = observe(dns=make_dns(status="nxdomain", ips=[]))
    result = classify(observation)
    assert result.verdict is Verdict.UNREACHABLE
    assert result.reason_code == ReasonCode.DNS_FAILURE.value
    assert result.needs_review is True


def test_nxdomain_with_control_resolver_is_a_blocked_dns_filter() -> None:
    observation = observe(
        dns=make_dns(status="nxdomain", ips=[]), control_resolves=True
    )
    result = classify(observation)
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.DNS_FILTERED.value
    assert result.confidence is Confidence.MEDIUM


def test_redirect_to_the_regulator_is_an_isp_blackout() -> None:
    fetch = make_fetch(
        body="<html><title>Accesso negato</title></html>",
        redirect_chain=["https://www.adm.gov.it/portale/blackout"],
    )
    result = classify(observe(fetch=fetch))
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.REGULATOR_REDIRECT.value
    assert result.blocker_hint == "isp_adm"


def test_verified_block_rule_matches_title_and_body() -> None:
    rule = SignatureRule(
        id="test-block-rule",
        kind=SignatureKind.BLOCK,
        title_patterns=[r"access\s+blocked"],
        blocker_hint="unknown",
        note="synthetic rule used by the test suite only",
    )
    fetch = make_fetch(
        body="<html><title>Access blocked</title><body>Stopped.</body></html>",
        title="Access blocked",
    )
    result = classify(observe(fetch=fetch), rules=[rule])
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.BLOCK_SIGNATURE.value
    assert result.rule_id == "test-block-rule"
    assert result.evidence["matched_rule"]["id"] == "test-block-rule"


def test_rule_priority_is_block_then_dead_then_positive_then_challenge() -> None:
    rules = [
        SignatureRule(id="challenge", kind=SignatureKind.CHALLENGE, body_patterns=[r"keep waiting"]),
        SignatureRule(id="positive", kind=SignatureKind.POSITIVE, body_patterns=[r"scommesse"]),
        SignatureRule(id="dead", kind=SignatureKind.DEAD, body_patterns=[r"page not found"]),
        SignatureRule(id="block", kind=SignatureKind.BLOCK, body_patterns=[r"blocked by"]),
    ]
    fetch = make_fetch(
        body="scommesse page not found keep waiting blocked by network",
    )
    result = classify(observe(fetch=fetch), rules=rules)
    assert result.rule_id == "block"


def test_positive_markers_and_brand_open_a_gambling_page() -> None:
    body = (
        "<html><title>Example Casino</title><body>"
        "Benvenuto su Example Casino: scommesse sportive, slot machine e "
        "casino online con bonus di benvenuto. Gioco responsabile 18+."
        "</body></html>"
    )
    result = classify(observe(fetch=make_fetch(body=body, title="Example Casino")))
    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.POSITIVE_MARKERS.value
    assert result.needs_review is False
    assert result.evidence["positive_markers"]


def test_brand_alone_never_counts_as_a_gambling_marker() -> None:
    body = (
        "<html><title>Example Domain</title><body>"
        "This domain is for use in documentation examples without needing "
        "permission. This is not a service; avoid relying on it for testing."
        "</body></html>"
    )
    result = classify(observe(fetch=make_fetch(body=body, title="Example Domain")))
    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.UNCLASSIFIED.value
    assert result.needs_review is True
    assert result.evidence["positive_markers"] == []


def test_challenge_page_means_the_site_is_reachable() -> None:
    body = (
        "<html><title>Just a moment...</title><body>"
        "<p>Enable JavaScript and cookies to continue. Checking your browser "
        "before granting access.</p></body></html>"
    )
    result = classify(observe(fetch=make_fetch(body=body, title="Just a moment...")))
    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.CHALLENGE_PAGE.value
    assert result.confidence is Confidence.MEDIUM


def test_unmatched_page_is_opened_with_needs_review() -> None:
    body = "<html><title>Hello</title><body>" + "ordinary text " * 20 + "</body></html>"
    result = classify(observe(fetch=make_fetch(body=body, title="Hello")))
    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.UNCLASSIFIED.value
    assert result.confidence is Confidence.LOW
    assert result.needs_review is True


def test_status_code_alone_never_blocks() -> None:
    result = classify(observe(fetch=make_fetch(body="Forbidden", status=403, title="403")))
    assert result.verdict is Verdict.UNREACHABLE
    assert result.reason_code == ReasonCode.UNCLASSIFIED.value
    assert result.needs_review is True


def test_404_and_500_are_unreachable_not_blocked() -> None:
    dead = classify(observe(fetch=make_fetch(body="Page not found", status=404)))
    assert dead.verdict is Verdict.UNREACHABLE
    assert dead.reason_code == ReasonCode.DEAD_PAGE.value

    broken = classify(observe(fetch=make_fetch(body="err", status=500)))
    assert broken.verdict is Verdict.UNREACHABLE
    assert broken.reason_code == ReasonCode.HTTP_ERROR.value


def test_empty_success_page_is_opened_but_needs_review() -> None:
    result = classify(observe(fetch=make_fetch(body="", status=200)))
    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.EMPTY_PAGE.value
    assert result.confidence is Confidence.LOW
    assert result.needs_review is True


def test_timeout_with_connect_evidence_is_unreachable_and_flagged() -> None:
    connect = ConnectResult(
        host="example.com",
        ip="93.184.216.34",
        port=443,
        ok=False,
        error_kind="connect_timeout",
        tls=True,
    )
    fetch = make_fetch(body="", status=None, error="timed out", error_kind="timeout")
    result = classify(observe(fetch=fetch, connect=connect))
    assert result.verdict is Verdict.UNREACHABLE
    assert result.reason_code == ReasonCode.CONNECT_TIMEOUT.value
    assert result.needs_review is True
    assert result.evidence["possibly_blocked"] is True


def test_tls_failure_is_reported_as_such() -> None:
    connect = ConnectResult(
        host="example.com",
        ip="93.184.216.34",
        port=443,
        ok=False,
        error_kind="tls_failure",
        tls=True,
    )
    fetch = make_fetch(body="", status=None, error="tls", error_kind="connect")
    result = classify(observe(fetch=fetch, connect=connect))
    assert result.verdict is Verdict.UNREACHABLE
    assert result.reason_code == ReasonCode.TLS_FAILURE.value


def test_key_phrases_are_repeatable_words() -> None:
    body = "<p>" + "scommesse sportive e casino online. " * 3 + "</p>"
    phrases = sniff_key_phrases(body)
    assert any("scommesse" in phrase or "casino" in phrase for phrase in phrases)


def test_classification_to_dict_round_trip() -> None:
    result: Classification = classify(observe(fetch=make_fetch(body="x" * 100, status=200)))
    payload = result.to_dict()
    assert payload["verdict"] == result.verdict.value
    assert payload["reason_code"] == result.reason_code


def test_known_betblocker_redirect_blocks_before_body_regex() -> None:
    fetch = make_fetch(
        body="<html><body>ordinary words that do not mention any blocker</body></html>",
        redirect_chain=["https://betblocker.org/blocked/"],
    )
    result = classify(observe(fetch=fetch))
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.BLOCKER_REDIRECT.value
    assert result.blocker_hint == "betblocker"
    assert result.rule_id is None
    assert result.evidence["known_blocker_redirect"]["hint"] == "betblocker"


def test_bundled_betblocker_rules_load_without_project_files() -> None:
    rules = load_signatures([])
    ids = [rule.id for rule in rules]
    assert "betblocker-blocked-by" in ids
    assert "betblocker-domain-blocked" in ids
    assert ids.count("betblocker-blocked-by") == 1


def test_embedded_meta_target_to_known_blocker_is_blocked() -> None:
    page = make_fetch(
        body=(
            '<html><head><meta http-equiv="refresh" '
            'content="0; url=https://betblocker.org/blocked/"></head></html>'
        )
    )
    observation = observe(fetch=page)
    observation.embedded_fetch = embedded_fetch(
        "<html><title>Blocked by BetBlocker</title></html>",
        "Blocked by BetBlocker",
        "https://betblocker.org/blocked/",
    )
    result = classify(observation)
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.BLOCKER_REDIRECT.value
    assert result.blocker_hint == "betblocker"


def test_embedded_generic_block_page_is_flagged_not_trusted() -> None:
    page = make_fetch(body='<script>window.open("/blocked/")</script>')
    observation = observe(fetch=page)
    observation.embedded_fetch = embedded_fetch(
        "<html><title>Restricted</title><body>This website is blocked by the "
        "network administrator.</body></html>",
        "Restricted",
        "https://embed.example/blocked/",
    )
    result = classify(observation)
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.BLOCKER_REDIRECT.value
    assert result.needs_review is True


def test_gambling_block_marker_is_evidence_not_a_verified_fingerprint() -> None:
    body = (
        "<html><title>Restricted</title><body>"
        "This gambling website is blocked in your country. Access is denied."
        "</body></html>"
    )
    result = classify(observe(fetch=make_fetch(body=body, title="Restricted")))
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.BLOCKER_PAGE.value
    assert result.needs_review is True
    assert result.evidence["gambling_block_marker"]


def test_dynamic_betblocker_rule_is_used_by_the_classifier() -> None:
    signature = DynamicSignature(
        pattern=r"Blocked\s+by\s+BetBlocker",
        blocker_hint="betblocker",
        confidence=Confidence.HIGH,
        needs_review=False,
    )
    body = "<html><title>Blocked by BetBlocker</title><body>Blocked by BetBlocker</body></html>"
    result = classify(
        observe(fetch=make_fetch(body=body, title="Blocked by BetBlocker")),
        rules=[signature.to_rule()],
    )
    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.BLOCK_SIGNATURE.value
    assert result.blocker_hint == "betblocker"
    assert result.rule_id is not None and result.rule_id.startswith("dynamic-")
