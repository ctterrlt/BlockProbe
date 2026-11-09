# SPDX-License-Identifier: AGPL-3.0-or-later
"""Variant aggregation must not discard verified block evidence (FR-6/FR-7)."""

from __future__ import annotations

from blockprobe.core.enums import Confidence, ReasonCode, Verdict
from blockprobe.core.models import Classification
from blockprobe.probe.variants import aggregate


def make(
    verdict: Verdict,
    reason_code: ReasonCode,
    confidence: Confidence,
    *,
    needs_review: bool = False,
    blocker_hint: str | None = None,
    rule_id: str | None = None,
    evidence: dict[str, object] | None = None,
) -> Classification:
    return Classification(
        verdict=verdict,
        reason_code=reason_code.value,
        confidence=confidence,
        blocker_hint=blocker_hint,
        needs_review=needs_review,
        rule_id=rule_id,
        evidence=evidence or {},
    )


def test_uncertain_opened_does_not_override_a_verified_block() -> None:
    blocked = make(
        Verdict.BLOCKED,
        ReasonCode.BLOCK_SIGNATURE,
        Confidence.HIGH,
        blocker_hint="betblocker",
        rule_id="betblocker-blocked-by",
        evidence={"matched_rule": {"id": "betblocker-blocked-by"}},
    )
    uncertain = make(
        Verdict.OPENED,
        ReasonCode.UNCLASSIFIED,
        Confidence.LOW,
        needs_review=True,
        evidence={"http": {"status": 200}},
    )

    result = aggregate("example.com", [blocked, uncertain])

    assert result.verdict is Verdict.BLOCKED
    assert result.reason_code == ReasonCode.BLOCK_SIGNATURE.value
    assert result.rule_id == "betblocker-blocked-by"
    assert result.needs_review is True
    assert result.evidence["uncertain_opened_variants"] == [
        {
            "reason_code": ReasonCode.UNCLASSIFIED.value,
            "confidence": Confidence.LOW.value,
            "http_status": 200,
            "note": (
                "This variant loaded a page without block evidence, but that "
                "is not proof of an open site. The verified block evidence is "
                "kept and the result needs review."
            ),
        }
    ]
    assert result.evidence["variants"]


def test_empty_page_does_not_override_a_verified_block() -> None:
    blocked = make(
        Verdict.BLOCKED,
        ReasonCode.DNS_SINKHOLE,
        Confidence.HIGH,
        blocker_hint="dns_filter",
    )
    empty = make(
        Verdict.OPENED,
        ReasonCode.EMPTY_PAGE,
        Confidence.LOW,
        needs_review=True,
    )

    result = aggregate("example.com", [empty, blocked])

    assert result.verdict is Verdict.BLOCKED
    assert result.needs_review is True


def test_confident_positive_markers_still_override_a_block() -> None:
    blocked = make(
        Verdict.BLOCKED,
        ReasonCode.BLOCK_SIGNATURE,
        Confidence.HIGH,
        blocker_hint="betblocker",
        rule_id="betblocker-blocked-by",
    )
    opened = make(
        Verdict.OPENED,
        ReasonCode.POSITIVE_MARKERS,
        Confidence.HIGH,
    )

    result = aggregate("example.com", [blocked, opened])

    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.POSITIVE_MARKERS.value
    assert result.needs_review is False
    assert "uncertain_opened_variants" not in result.evidence


def test_confident_challenge_page_still_overrides_a_block() -> None:
    blocked = make(
        Verdict.BLOCKED,
        ReasonCode.BLOCK_SIGNATURE,
        Confidence.HIGH,
        blocker_hint="betblocker",
    )
    challenge = make(
        Verdict.OPENED,
        ReasonCode.CHALLENGE_PAGE,
        Confidence.MEDIUM,
    )

    result = aggregate("example.com", [blocked, challenge])

    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.CHALLENGE_PAGE.value


def test_all_uncertain_opened_variants_keep_the_previous_behaviour() -> None:
    first = make(
        Verdict.OPENED,
        ReasonCode.UNCLASSIFIED,
        Confidence.LOW,
        needs_review=True,
    )
    second = make(
        Verdict.OPENED,
        ReasonCode.EMPTY_PAGE,
        Confidence.LOW,
        needs_review=True,
    )

    result = aggregate("example.com", [first, second])

    assert result.verdict is Verdict.OPENED
    assert result.needs_review is True
    assert "uncertain_opened_variants" not in result.evidence
