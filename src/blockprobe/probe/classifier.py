# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evidence-based classification of probe observations.

Ordered exactly as FR-7 requires: DNS evidence first, then connection evidence,
then signature rules (block, dead, positive, challenge), then generic content
markers. A verdict is never based on the HTTP status code alone, and an
unmatched page is reported with ``needs_review`` instead of a silent guess.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections import Counter
from collections.abc import Iterable
from importlib.resources import files
from importlib.resources.abc import Traversable
from urllib.parse import urlsplit

from blockprobe.core.enums import (
    BlockerHint,
    Confidence,
    ReasonCode,
    SignatureKind,
    Verdict,
)
from blockprobe.core.models import (
    Classification,
    HttpFetchResult,
    ProbeObservation,
    SignatureRule,
)
from blockprobe.portals.generic_extractor import strip_tags
from blockprobe.probe.interception import first_known_blocker

_LOGGER = logging.getLogger("blockprobe.probe.classifier")

REGULATOR_HOSTS = ("adm.gov.it", "mef.gov.it")
KIND_ORDER = (
    SignatureKind.BLOCK,
    SignatureKind.DEAD,
    SignatureKind.POSITIVE,
    SignatureKind.CHALLENGE,
)

POSITIVE_MARKERS: tuple[str, ...] = (
    r"\bscommess[ae]\b",
    r"\bgioch[io]\s+(?:legale|d'azzardo|a\s+distanza)\b",
    r"\bcasin(?:o|ò)\b",
    r"\bslot(?:\s+machine)?s?\b",
    r"\bpoker\b",
    r"\bbonus\b",
    r"\bgioco\s+responsabile\b",
    r"\b18\+",
    r"\bodds\b",
    r"\bbetting\b",
    r"\bbet\b",
    r"\bgambling\b",
    r"\bapuestas\b",
    r"\bparis\s+sportifs\b",
    r"\bcotes\b",
    r"\bjeu\s+(?:d'argent|en\s+ligne)\b",
    r"\bspiel(?:en|bank)\b",
    r"\bwetten\b",
    r"\bjackpot\b",
    r"\brtp\b",
    r"\bfree\s*spin\b",
    r"\bvideo\s*slot\b",
    r"\bammissione\s+al\s+gioco\b",
)

DEAD_MARKERS: tuple[str, ...] = (
    r"domain\s+(?:for\s+sale|expired|is\s+parked)",
    r"buy\s+this\s+domain",
    r"this\s+domain\s+is\s+for\s+sale",
    r"\bparked\b",
    r"coming\s+soon",
    r"account\s+suspended",
    r"site\s+(?:not\s+found|temporarily\s+unavailable|under\s+construction)",
    r"page\s+not\s+found",
    r"default\s+(?:web\s+page|website)",
    r"welcome\s+to\s+nginx",
    r"apache2?\s+(?:ubuntu\s+)?default",
    r"no\s+site\s+configured",
    r"service\s+temporarily\s+unavailable",
    r"server\s+not\s+found",
)

CHALLENGE_MARKERS: tuple[str, ...] = (
    r"just\s+a\s+moment",
    r"attention\s+required",
    r"cf-chl",
    r"challenge-platform",
    r"checking\s+your\s+browser",
    r"verify\s+you\s+are\s+human",
    r"\bcaptcha\b",
    r"enable\s+javascript\s+and\s+cookies",
    r"ddos\s+protection\s+by",
)

# Generic wording for a block/denial page. These are not brand signatures and
# are only accepted when no gambling marker is present, so a normal operator
# error page cannot be turned into a blocker claim by itself.
BLOCKER_PAGE_MARKERS: tuple[str, ...] = (
    r"\baccess\s+(?:is\s+)?(?:denied|blocked|restricted)\b",
    r"\b(?:site|website|content|page|service)\s+(?:is\s+|has\s+been\s+)?"
    r"(?:blocked|restricted|unavailable|not\s+available)\b",
    r"\bblocked\s+by\s+(?:your|the|a)\b",
    r"\b(?:parental|content|network|dns)\s+(?:controls?|filter(?:ing)?)\b",
    r"\b(?:gambling|betting)\s+(?:is\s+)?(?:blocked|restricted|prohibited|not\s+allowed)\b",
    r"\bself[- ]exclusion\b",
    r"\bself[- ]excluded\b",
    r"\baccesso\s+(?:negato|bloccato)\b",
    r"\bsito\s+(?:bloccato|non\s+disponibile)\b",
    r"\bcontenuti?\s+(?:bloccat|filtrat)[oi]\b",
    r"\bgioco\s+(?:vietato|bloccato)\b",
    r"\bbloqueado\b",
    r"\bacceso\s+denegado\b",
    r"\bzugriff\s+verweigert\b",
)

# Deliberately narrower than the generic markers above: each pattern contains a
# gambling word and a block word, so an ordinary page that merely mentions
# gambling is not enough. These are still reported as ``needs_review`` because
# geo-blocking can use the same wording.
GAMBLING_BLOCK_MARKERS: tuple[str, ...] = (
    r"\b(?:gambling|betting|casino|poker)\s+(?:website|site|service|content)\s+"
    r"(?:is\s+|was\s+|has\s+been\s+)?(?:blocked|restricted|prohibited|not\s+available)\b",
    r"\b(?:this\s+)?(?:gambling|betting)\s+(?:website|site|service)\s+"
    r"(?:is\s+|was\s+|has\s+been\s+)?(?:blocked|restricted|prohibited)\b",
    r"\b(?:sito|portale)\s+(?:di\s+gioco|scommesse)\s+"
    r"(?:bloccato|vietato|non\s+disponibile)\b",
    r"\b(?:gioco|scommesse)\s+(?:online\s+)?(?:bloccat[oi]|vietat[oi]|proibit[oi])\b",
    r"\bsitio\s+de\s+(?:juegos|apuestas)\s+(?:bloqueado|restringido|prohibido)\b",
    r"\bsite\s+de\s+(?:jeux|paris)\s+(?:bloqué|restreint|interdit)\b",
    r"\bsite\s+de\s+(?:jogos|apostas)\s+(?:bloqueado|restrito|proibido)\b",
    r"\b(?:glücksspiel|wetten)\s*(?:seite|website)?\s*"
    r"(?:blockiert|gesperrt|verboten)\b",
)

_STOPWORDS = {
    "the",
    "and",
    "for",
    "you",
    "your",
    "with",
    "that",
    "this",
    "from",
    "have",
    "not",
    "are",
    "was",
    "were",
    "che",
    "per",
    "con",
    "del",
    "della",
    "dei",
    "delle",
    "gli",
    "una",
    "uno",
    "sono",
    "come",
    "più",
    "https",
    "http",
    "www",
    "com",
    "it",
}


def load_signatures(directories: Iterable[Traversable]) -> list[SignatureRule]:
    """Load user and bundled rules from ``signatures/*.toml``.

    Rules work from filesystem directories and package resources, including
    zip-based mobile bundles. An identical bundled rule is not duplicated if
    a source-checkout copy is also present.
    """
    rules: list[SignatureRule] = []
    seen_ids: set[str] = set()
    packaged = files("blockprobe").joinpath("data", "signatures")
    for directory in [*directories, packaged]:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if not path.is_file() or not path.name.endswith(".toml"):
                continue
            try:
                with path.open("rb") as handle:
                    data = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                _LOGGER.warning("skipping signature file %s: %s", path, exc)
                continue
            for raw_rule in data.get("rules") or []:
                if not isinstance(raw_rule, dict):
                    continue
                rule = SignatureRule.from_toml(
                    raw_rule, origin=path.name.removesuffix(".toml")
                )
                if rule.id in seen_ids:
                    continue
                seen_ids.add(rule.id)
                rules.append(rule)
    return rules


def sort_rules(rules: list[SignatureRule]) -> list[SignatureRule]:
    """Sort by kind order while keeping file order inside each kind."""
    order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    return sorted(rules, key=lambda rule: order.get(rule.kind, len(KIND_ORDER)))


def _match_patterns(patterns: list[str], text: str) -> str | None:
    if not patterns or not text:
        return None
    for pattern in patterns:
        try:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return pattern
        except re.error:
            if pattern.lower() in text.lower():
                return pattern
    return None


def _header_text(fetch: HttpFetchResult) -> str:
    return "\n".join(f"{key}: {value}" for key, value in fetch.headers.items())


def _redirect_text(fetch: HttpFetchResult) -> str:
    return "\n".join(fetch.redirect_chain)


def _all_redirect_urls(observation: ProbeObservation) -> list[str]:
    urls: list[str] = []
    for fetch in (observation.fetch, observation.embedded_fetch):
        if fetch is None:
            continue
        urls.extend(fetch.embedded_redirects)
        urls.extend(fetch.redirect_chain)
        if fetch.final_url:
            urls.append(fetch.final_url)
    return urls


def _interception_payload(observation: ProbeObservation) -> dict[str, object]:
    fetch = observation.fetch
    embedded = observation.embedded_fetch
    if fetch is None:
        return {}
    styles: list[str] = []
    if fetch.redirect_chain:
        styles.append("http_redirect")
    if fetch.redirect_statuses:
        styles.append("302_or_equivalent")
    if fetch.embedded_redirects:
        styles.append("embedded_navigation")
    if fetch.reopens_on_return:
        styles.append("reopens_on_return")
    payload: dict[str, object] = {
        "redirect_chain": list(fetch.redirect_chain),
        "redirect_statuses": list(fetch.redirect_statuses),
        "embedded_targets": list(fetch.embedded_redirects),
        "reopens_on_return": bool(fetch.reopens_on_return),
        "styles": styles,
    }
    if embedded is not None:
        payload["embedded_final_url"] = embedded.final_url
        payload["embedded_status"] = embedded.status
        payload["embedded_title"] = embedded.title
        payload["embedded_redirect_chain"] = list(embedded.redirect_chain)
    return payload


def _embedded_block_evidence(
    observation: ProbeObservation,
    rules: list[SignatureRule],
    evidence: dict[str, object],
) -> Classification | None:
    """Classify a page reached from a meta-refresh or window.open target."""
    embedded = observation.embedded_fetch
    if embedded is None:
        return None
    evidence["embedded_fetch"] = {
        "final_url": embedded.final_url,
        "status": embedded.status,
        "title": embedded.title,
        "redirect_chain": embedded.redirect_chain,
        "error": embedded.error,
    }
    for rule in rules:
        match = _check_rule(rule, embedded)
        if match is None:
            continue
        if rule.kind is not SignatureKind.BLOCK:
            continue
        evidence["embedded_matched_rule"] = {
            "id": rule.id,
            "field": match,
            "note": rule.note,
        }
        return Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.BLOCKER_REDIRECT.value,
            confidence=rule.confidence,
            blocker_hint=rule.blocker_hint or BlockerHint.UNKNOWN.value,
            needs_review=rule.needs_review,
            rule_id=rule.id,
            title=embedded.title,
            evidence=evidence,
        )
    visible = strip_tags(embedded.body)
    marker = _match_patterns(list(BLOCKER_PAGE_MARKERS), f"{embedded.title}\n{visible}")
    if marker:
        evidence["embedded_blocker_marker"] = marker
        return Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.BLOCKER_REDIRECT.value,
            confidence=Confidence.MEDIUM,
            blocker_hint=BlockerHint.UNKNOWN.value,
            needs_review=True,
            title=embedded.title,
            evidence=evidence,
        )
    return None


def _check_rule(rule: SignatureRule, fetch: HttpFetchResult) -> str | None:
    for field, patterns in (
        ("title", rule.title_patterns),
        ("body", rule.body_patterns),
        ("headers", rule.header_patterns),
        ("redirect", rule.redirect_patterns),
    ):
        text = {
            "title": fetch.title,
            "body": fetch.body,
            "headers": _header_text(fetch),
            "redirect": _redirect_text(fetch),
        }[field]
        matched = _match_patterns(patterns, text)
        if matched is not None:
            return f"{field}:{matched}"
    return None


def _snippet(body: str, limit: int = 2000) -> str:
    if not body:
        return ""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    text = strip_tags(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def sniff_key_phrases(body: str, limit: int = 20) -> list[str]:
    """Return the most frequent meaningful phrases from visible text."""
    text = _snippet(body, limit=200_000).lower()
    words = re.findall(r"[a-zàèéìòù0-9][a-zàèéìòù0-9'+-]{2,}", text)
    counts: Counter[str] = Counter()
    for index in range(len(words) - 1):
        first, second = words[index], words[index + 1]
        if first in _STOPWORDS or second in _STOPWORDS:
            continue
        if len(first) + len(second) < 8:
            continue
        counts[f"{first} {second}"] += 1
    phrases: list[str] = []
    for phrase, count in counts.most_common(limit * 2):
        if count < 2:
            continue
        if phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) >= limit:
            break
    if not phrases:
        for word in words:
            if word in _STOPWORDS or len(word) < 5:
                continue
            if word not in phrases:
                phrases.append(word)
            if len(phrases) >= limit:
                break
    return phrases


def _evidence(observation: ProbeObservation, debug: bool) -> dict[str, object]:
    fetch = observation.fetch
    connect = observation.connect
    evidence: dict[str, object] = {
        "dns": {
            "status": observation.dns.status,
            "ips": observation.dns.ips,
            "cnames": observation.dns.cnames,
            "resolver": observation.dns.resolver,
            "sinkhole_ips": observation.dns.sinkhole_ips,
            "error": observation.dns.error,
        },
        "connect": {
            "ok": connect.ok if connect else False,
            "connect_ms": connect.connect_ms if connect else 0,
            "error_kind": connect.error_kind if connect else None,
            "error": (connect.error or "")[:300] if connect else None,
            "tls": connect.tls if connect else False,
        },
        "http": {
            "status": fetch.status if fetch else None,
            "final_url": fetch.final_url if fetch else "",
            "redirect_chain": fetch.redirect_chain if fetch else [],
            "redirect_statuses": fetch.redirect_statuses if fetch else [],
            "embedded_redirects": fetch.embedded_redirects if fetch else [],
            "reopens_on_return": fetch.reopens_on_return if fetch else False,
            "body_sha256": fetch.body_sha256 if fetch else "",
            "body_bytes": fetch.body_bytes if fetch else 0,
            "truncated": fetch.truncated if fetch else False,
            "elapsed_ms": fetch.elapsed_ms if fetch else 0,
            "title": fetch.title if fetch else "",
            "headers": fetch.headers if fetch else {},
        },
    }
    if debug and fetch and fetch.body:
        evidence["snippet"] = _snippet(fetch.body)
    return evidence


def _status_of(fetch: HttpFetchResult | None) -> int | None:
    return fetch.status if fetch else None


def classify(
    observation: ProbeObservation,
    rules: list[SignatureRule] | None = None,
    debug: bool = False,
) -> Classification:
    """Classify one host/scheme observation using evidence and verified rules."""
    rules = sort_rules(rules or [])
    evidence = _evidence(observation, debug)
    fetch = observation.fetch

    # 1. DNS evidence.
    if observation.dns.sinkhole:
        return Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.DNS_SINKHOLE.value,
            confidence=Confidence.HIGH,
            blocker_hint=BlockerHint.DNS_FILTER.value,
            needs_review=False,
            title=fetch.title if fetch else "",
            evidence=evidence,
        )
    if not observation.dns.ok:
        if observation.control_resolves:
            return Classification(
                verdict=Verdict.BLOCKED,
                reason_code=ReasonCode.DNS_FILTERED.value,
                confidence=Confidence.MEDIUM,
                blocker_hint=BlockerHint.DNS_FILTER.value,
                needs_review=False,
                evidence=evidence,
            )
        return Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=ReasonCode.DNS_FAILURE.value,
            confidence=Confidence.LOW,
            needs_review=True,
            evidence=evidence,
        )

    # 2. Connection evidence, kept even when an HTTP page was fetched anyway.
    if observation.connect and not observation.connect.ok:
        evidence["possibly_blocked"] = observation.connect.error_kind in {
            "connect_timeout",
            "connect_reset",
            "connect_refused",
        }

    # 3. Known interception destinations. A known blocker landing page is
    # direct evidence: it is matched before any body regex runs.
    interception = _interception_payload(observation)
    if interception:
        evidence["interception"] = interception
    all_urls = _all_redirect_urls(observation)
    known_hint = first_known_blocker(all_urls)
    if known_hint is not None:
        evidence["known_blocker_redirect"] = {
            "hint": known_hint.value,
            "urls": all_urls,
        }
        return Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.BLOCKER_REDIRECT.value,
            confidence=Confidence.HIGH,
            blocker_hint=known_hint.value,
            needs_review=False,
            title=fetch.title if fetch else "",
            evidence=evidence,
        )

    redirect_hosts = {
        (urlsplit(url).hostname or "").lower() for url in all_urls
    }
    if any(
        host == regulator or host.endswith("." + regulator)
        for host in redirect_hosts
        for regulator in REGULATOR_HOSTS
    ):
        return Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.REGULATOR_REDIRECT.value,
            confidence=Confidence.HIGH,
            blocker_hint=BlockerHint.ISP_ADM.value,
            needs_review=False,
            title=fetch.title if fetch else "",
            evidence=evidence,
        )

    embedded_classification = _embedded_block_evidence(observation, rules, evidence)
    if embedded_classification is not None:
        return embedded_classification

    if fetch is not None:
        for rule in rules:
            match = _check_rule(rule, fetch)
            if match is None:
                continue
            evidence["matched_rule"] = {"id": rule.id, "field": match, "note": rule.note}
            if rule.kind is SignatureKind.BLOCK:
                return Classification(
                    verdict=Verdict.BLOCKED,
                    reason_code=ReasonCode.BLOCK_SIGNATURE.value,
                    confidence=rule.confidence,
                    blocker_hint=rule.blocker_hint or BlockerHint.UNKNOWN.value,
                    needs_review=rule.needs_review,
                    rule_id=rule.id,
                    title=fetch.title,
                    evidence=evidence,
                )
            if rule.kind is SignatureKind.DEAD:
                return Classification(
                    verdict=Verdict.UNREACHABLE,
                    reason_code=ReasonCode.DEAD_PAGE.value,
                    confidence=rule.confidence,
                    needs_review=False,
                    rule_id=rule.id,
                    title=fetch.title,
                    evidence=evidence,
                )
            if rule.kind is SignatureKind.POSITIVE:
                return Classification(
                    verdict=Verdict.OPENED,
                    reason_code=ReasonCode.POSITIVE_MARKERS.value,
                    confidence=rule.confidence,
                    needs_review=False,
                    rule_id=rule.id,
                    title=fetch.title,
                    evidence=evidence,
                )
            if rule.kind is SignatureKind.CHALLENGE:
                return Classification(
                    verdict=Verdict.OPENED,
                    reason_code=ReasonCode.CHALLENGE_PAGE.value,
                    confidence=rule.confidence,
                    needs_review=False,
                    rule_id=rule.id,
                    title=fetch.title,
                    evidence=evidence,
                )

    # 4. Transport-level failures where no HTTP response exists.
    if fetch is not None and fetch.error and fetch.status is None:
        error_kind = fetch.error_kind or "connect_failure"
        reason = {
            "timeout": ReasonCode.CONNECT_TIMEOUT,
            "connect": ReasonCode.CONNECT_FAILURE,
            "http": ReasonCode.CONNECT_FAILURE,
        }.get(error_kind, ReasonCode.CONNECT_FAILURE)
        if observation.connect and observation.connect.error_kind == "tls_failure":
            reason = ReasonCode.TLS_FAILURE
        needs_review = bool(evidence.get("possibly_blocked"))
        return Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=reason.value,
            confidence=Confidence.LOW if needs_review else Confidence.MEDIUM,
            needs_review=needs_review,
            evidence=evidence,
        )

    status = _status_of(fetch)
    body = fetch.body if fetch else ""
    visible = strip_tags(body)
    visible_length = len(visible)
    title = fetch.title if fetch else ""
    searchable = f"{title}\n{visible}"

    # 5. Generic content markers when no rule matched.
    challenge = _match_patterns(list(CHALLENGE_MARKERS), searchable)
    if challenge and status is not None and 200 <= status < 300:
        evidence["challenge_marker"] = challenge
        return Classification(
            verdict=Verdict.OPENED,
            reason_code=ReasonCode.CHALLENGE_PAGE.value,
            confidence=Confidence.MEDIUM,
            needs_review=False,
            title=title,
            evidence=evidence,
        )
    gambling_block = _match_patterns(list(GAMBLING_BLOCK_MARKERS), searchable)
    if gambling_block:
        evidence["gambling_block_marker"] = gambling_block
        return Classification(
            verdict=Verdict.BLOCKED,
            reason_code=ReasonCode.BLOCKER_PAGE.value,
            confidence=Confidence.MEDIUM,
            blocker_hint=BlockerHint.UNKNOWN.value,
            needs_review=True,
            title=title,
            evidence=evidence,
        )
    matched_markers = [
        marker
        for marker in POSITIVE_MARKERS
        if re.search(marker, searchable, re.IGNORECASE | re.DOTALL)
    ]
    marker_score = len(matched_markers)
    evidence["positive_markers"] = matched_markers
    # A generic denial page is useful evidence only when the page does not also
    # contain normal gambling content. It is reported as needs-review because
    # the wording is not a verified brand fingerprint.
    if marker_score == 0:
        blocker_marker = _match_patterns(list(BLOCKER_PAGE_MARKERS), searchable)
        if blocker_marker:
            evidence["blocker_marker"] = blocker_marker
            return Classification(
                verdict=Verdict.BLOCKED,
                reason_code=ReasonCode.BLOCKER_PAGE.value,
                confidence=(
                    Confidence.MEDIUM if visible_length >= 64 else Confidence.LOW
                ),
                blocker_hint=BlockerHint.UNKNOWN.value,
                needs_review=True,
                title=title,
                evidence=evidence,
            )

    if status is not None and 200 <= status < 300:
        brand_label = (observation.domain.split(".")[0] or "").lower()
        brand_hit = bool(
            len(brand_label) >= 4
            and re.search(rf"\b{re.escape(brand_label)}\b", title, re.IGNORECASE)
        )
        score = marker_score + (1 if brand_hit else 0)
        if brand_hit:
            evidence["brand_title_match"] = brand_label
        # The brand alone is never enough: a domain label in the title only
        # strengthens a page that already contains at least one real marker.
        if (
            marker_score >= 3
            or (marker_score >= 2 and visible_length >= 800)
            or (marker_score >= 1 and brand_hit and visible_length >= 300)
        ):
            confidence = Confidence.HIGH if score >= 4 else Confidence.MEDIUM
            return Classification(
                verdict=Verdict.OPENED,
                reason_code=ReasonCode.POSITIVE_MARKERS.value,
                confidence=confidence,
                needs_review=False,
                title=title,
                evidence=evidence,
            )
        dead = _match_patterns(list(DEAD_MARKERS), searchable)
        if dead:
            evidence["dead_marker"] = dead
            return Classification(
                verdict=Verdict.UNREACHABLE,
                reason_code=ReasonCode.DEAD_PAGE.value,
                confidence=Confidence.MEDIUM,
                needs_review=False,
                title=title,
                evidence=evidence,
            )
        if visible_length < 64:
            return Classification(
                verdict=Verdict.UNREACHABLE,
                reason_code=ReasonCode.EMPTY_PAGE.value,
                confidence=Confidence.MEDIUM,
                needs_review=False,
                title=title,
                evidence=evidence,
            )
        evidence["note"] = (
            "The page loaded but no verified signature or gambling marker matched."
        )
        return Classification(
            verdict=Verdict.OPENED,
            reason_code=ReasonCode.UNCLASSIFIED.value,
            confidence=Confidence.LOW,
            needs_review=True,
            title=title,
            evidence=evidence,
        )

    if status in {404, 410}:
        return Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=ReasonCode.DEAD_PAGE.value,
            confidence=Confidence.MEDIUM,
            needs_review=False,
            title=title,
            evidence=evidence,
        )
    if status is not None and status >= 500:
        return Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=ReasonCode.HTTP_ERROR.value,
            confidence=Confidence.LOW,
            needs_review=True,
            title=title,
            evidence=evidence,
        )
    if status in {401, 403, 451}:
        evidence["note"] = (
            f"HTTP {status} without a verified block signature. This can be a "
            "geo-block, an anti-bot rule or a blocker."
        )
        return Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=ReasonCode.UNCLASSIFIED.value,
            confidence=Confidence.LOW,
            needs_review=True,
            title=title,
            evidence=evidence,
        )
    if not body:
        return Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code=ReasonCode.EMPTY_PAGE.value,
            confidence=Confidence.LOW,
            needs_review=True,
            title=title,
            evidence=evidence,
        )
    return Classification(
        verdict=Verdict.UNREACHABLE,
        reason_code=ReasonCode.UNCLASSIFIED.value,
        confidence=Confidence.LOW,
        needs_review=True,
        title=title,
        evidence=evidence,
    )
