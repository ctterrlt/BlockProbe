# SPDX-License-Identifier: AGPL-3.0-or-later
"""Neutral-control timelines and VPN/DNS interference risk."""

from __future__ import annotations

from blockprobe.core.enums import Confidence, ReasonCode, Verdict
from blockprobe.core.models import ResultRow, utc_now
from blockprobe.probe.health import ControlCheck, assess_probe_health


def result(
    domain: str,
    verdict: Verdict,
    reason: ReasonCode,
    checked_at: int,
    *,
    needs_review: bool = False,
) -> ResultRow:
    return ResultRow(
        run_id="run-1",
        domain=domain,
        verdict=verdict,
        reason_code=reason.value,
        confidence=Confidence.LOW,
        blocker_hint=None,
        needs_review=needs_review,
        evidence={},
        checked_at=checked_at,
    )


def control(sequence: int, ok: bool, after: str, checked_at: int) -> ControlCheck:
    return ControlCheck(
        url="https://control.example/",
        ok=ok,
        status=204 if ok else None,
        error=None if ok else "connect error",
        after_domain=after,
        sequence=sequence,
        checked_at=checked_at,
    )


def test_control_failure_marks_later_results_for_recheck() -> None:
    rows = [
        result("a.example", Verdict.OPENED, ReasonCode.UNCLASSIFIED, 1),
        result("b.example", Verdict.OPENED, ReasonCode.UNCLASSIFIED, 2),
        result(
            "c.example",
            Verdict.UNREACHABLE,
            ReasonCode.DNS_FAILURE,
            3,
            needs_review=True,
        ),
        result("d.example", Verdict.OPENED, ReasonCode.UNCLASSIFIED, 4),
    ]
    controls = [
        control(1, True, "", 1),
        control(2, False, "b.example", 3),
    ]
    health = assess_probe_health(rows, controls, {})
    assert health.first_failure_after == "b.example"
    assert health.control_failures == 1
    assert health.affected_domains == ["c.example", "d.example"]
    assert health.recheck_domains == ["c.example"]
    assert health.risk_percent > 0
    assert health.false_positive_suspected is True


def test_all_controls_failing_sets_a_high_floor() -> None:
    rows = [result("a.example", Verdict.OPENED, ReasonCode.UNCLASSIFIED, 1)]
    controls = [
        control(1, False, "a.example", 1),
        control(2, False, "a.example", 2),
    ]
    health = assess_probe_health(rows, controls, {})
    assert health.risk_percent >= 82
    assert health.risk_level == "high"
    assert health.control_failures == 2


def test_known_filtering_dns_raises_risk_substantially() -> None:
    context = {
        "dns_filters": ["208.67.222.123 (OpenDNS FamilyShield)"],
        "resolver_labels": ["208.67.222.123 (OpenDNS FamilyShield)"],
    }
    health = assess_probe_health([], [], context)
    assert health.risk_percent >= 72
    assert health.risk_level == "high"
    assert health.false_positive_suspected is True
    assert any("filtering DNS" in reason for reason in health.reasons)
    assert "filtering DNS" in health.summary


def test_vpn_alone_stays_low_without_failed_controls_or_unreachable_sites() -> None:
    rows = [
        result("a.example", Verdict.OPENED, ReasonCode.UNCLASSIFIED, 1),
        result("b.example", Verdict.BLOCKED, ReasonCode.BLOCK_SIGNATURE, 2),
    ]
    context = {
        "vpn_interfaces": ["tailscale0"],
        "vpn_providers": ["Tailscale via tailscale0"],
    }
    health = assess_probe_health(rows, [], context)
    assert health.risk_percent <= 15
    assert health.risk_level == "low"
    assert any("VPN" in reason for reason in health.reasons)


def test_hosts_file_sinkhole_entries_are_evidence() -> None:
    context = {"hosts_block_entries": 5}
    health = assess_probe_health(
        [result("a.example", Verdict.OPENED, ReasonCode.UNCLASSIFIED, utc_now())],
        [],
        context,
    )
    assert health.risk_percent > 0
    assert any("hosts file" in reason for reason in health.reasons)
