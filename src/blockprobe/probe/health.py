# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run-level probe reliability and VPN/DNS-interference risk.

A rejected website is not automatically an error: the destination can be down,
geo-restricted, filtered by a DNS resolver or blocked by a VPN. This module
combines neutral control checks interleaved with the scan, transport-level
failure patterns and the detected network context into one transparent
interference signal, and lists the results that should be rechecked after the first control
failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from blockprobe.core.enums import ReasonCode, Verdict
from blockprobe.core.models import ResultRow, utc_now

# Reason codes that describe a transport / DNS problem rather than a verified
# blocker page. The names are stable strings in SQLite, so compare by value.
_TRANSPORT_REASONS = {
    ReasonCode.DNS_FAILURE.value,
    ReasonCode.CONNECT_REFUSED.value,
    ReasonCode.CONNECT_RESET.value,
    ReasonCode.CONNECT_TIMEOUT.value,
    ReasonCode.TLS_FAILURE.value,
    ReasonCode.CONNECT_FAILURE.value,
    ReasonCode.EMPTY_PAGE.value,
    ReasonCode.HTTP_ERROR.value,
}

@dataclass(slots=True)
class ControlCheck:
    """One request to a neutral, non-gambling website."""

    url: str
    ok: bool
    status: int | None = None
    error: str | None = None
    final_url: str = ""
    after_domain: str = ""
    sequence: int = 0
    checked_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "ok": self.ok,
            "status": self.status,
            "error": self.error,
            "final_url": self.final_url,
            "after_domain": self.after_domain,
            "sequence": self.sequence,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlCheck:
        return cls(
            url=str(data.get("url") or ""),
            ok=bool(data.get("ok", False)),
            status=int(data["status"]) if data.get("status") is not None else None,
            error=str(data.get("error") or "") or None,
            final_url=str(data.get("final_url") or ""),
            after_domain=str(data.get("after_domain") or ""),
            sequence=int(data.get("sequence") or 0),
            checked_at=int(data.get("checked_at") or 0),
        )


@dataclass(slots=True)
class ProbeHealth:
    """Transparent assessment shown in every CLI, UI and report summary."""

    risk_percent: int = 0
    risk_level: str = "low"
    summary: str = ""
    reasons: list[str] = field(default_factory=list)
    controls: list[ControlCheck] = field(default_factory=list)
    control_failures: int = 0
    first_failure_after: str = ""
    first_failure_at: int = 0
    false_positive_suspected: bool = False
    checked: int = 0
    blocked: int = 0
    unreachable: int = 0
    opened: int = 0
    affected_domains: list[str] = field(default_factory=list)
    recheck_domains: list[str] = field(default_factory=list)
    signature_refresh: dict[str, Any] = field(default_factory=dict)
    generated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_percent": self.risk_percent,
            "risk_level": self.risk_level,
            "summary": self.summary,
            "reasons": self.reasons,
            "controls": [control.to_dict() for control in self.controls],
            "control_failures": self.control_failures,
            "first_failure_after": self.first_failure_after,
            "first_failure_at": self.first_failure_at,
            "false_positive_suspected": self.false_positive_suspected,
            "checked": self.checked,
            "blocked": self.blocked,
            "unreachable": self.unreachable,
            "opened": self.opened,
            "affected_domains": self.affected_domains,
            "recheck_domains": self.recheck_domains,
            "signature_refresh": self.signature_refresh,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ProbeHealth:
        data = data or {}
        controls = [
            ControlCheck.from_dict(item)
            for item in data.get("controls") or []
            if isinstance(item, dict)
        ]
        refresh = data.get("signature_refresh")
        return cls(
            risk_percent=int(data.get("risk_percent") or 0),
            risk_level=str(data.get("risk_level") or "low"),
            summary=str(data.get("summary") or ""),
            reasons=[str(item) for item in data.get("reasons") or []],
            controls=controls,
            control_failures=int(data.get("control_failures") or 0),
            first_failure_after=str(data.get("first_failure_after") or ""),
            first_failure_at=int(data.get("first_failure_at") or 0),
            false_positive_suspected=bool(data.get("false_positive_suspected", False)),
            checked=int(data.get("checked") or 0),
            blocked=int(data.get("blocked") or 0),
            unreachable=int(data.get("unreachable") or 0),
            opened=int(data.get("opened") or 0),
            affected_domains=[str(item) for item in data.get("affected_domains") or []],
            recheck_domains=[str(item) for item in data.get("recheck_domains") or []],
            signature_refresh=dict(refresh) if isinstance(refresh, dict) else {},
            generated_at=int(data.get("generated_at") or 0),
        )


def _ordered(results: list[ResultRow]) -> list[ResultRow]:
    return sorted(results, key=lambda row: (row.checked_at, row.domain))


def _affected_after(
    ordered: list[ResultRow],
    first_failure: ControlCheck | None,
) -> list[ResultRow]:
    if first_failure is None or not ordered:
        return []
    if first_failure.after_domain:
        for index, row in enumerate(ordered):
            if row.domain == first_failure.after_domain:
                return ordered[index + 1 :]
    if first_failure.checked_at:
        return [row for row in ordered if row.checked_at >= first_failure.checked_at]
    return []


def _longest_unreachable_streak(ordered: list[ResultRow]) -> int:
    longest = 0
    current = 0
    for row in ordered:
        if row.verdict is Verdict.UNREACHABLE:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def assess_probe_health(
    results: list[ResultRow],
    controls: list[ControlCheck] | None = None,
    network_context: dict[str, Any] | None = None,
) -> ProbeHealth:
    """Return a deterministic interference signal and recheck list for one run."""
    controls = sorted(controls or [], key=lambda item: item.sequence)
    network_context = network_context or {}
    ordered = _ordered(results)
    total = len(ordered)
    blocked = sum(1 for row in ordered if row.verdict is Verdict.BLOCKED)
    unreachable = sum(1 for row in ordered if row.verdict is Verdict.UNREACHABLE)
    opened = sum(1 for row in ordered if row.verdict is Verdict.OPENED)
    unreachable_ratio = (unreachable / total) if total else 0.0
    transport_failures = sum(
        1 for row in ordered if row.reason_code in _TRANSPORT_REASONS
    )
    failures = [control for control in controls if not control.ok]
    first_failure = failures[0] if failures else None
    affected = _affected_after(ordered, first_failure)
    affected_domains = [row.domain for row in affected]
    recheck_domains = [
        row.domain
        for row in affected
        if row.verdict is not Verdict.OPENED or row.needs_review
    ]
    longest_streak = _longest_unreachable_streak(ordered)

    reasons: list[str] = []
    score = 0.0
    if total:
        # Failure counts are correlated evidence: do not add a per-site penalty
        # on top of the same unreachable ratio and then call that a probability.
        score += min(18.0, unreachable_ratio * 30.0)
        if unreachable:
            reasons.append(
                f"{unreachable} of {total} checked websites were unreachable "
                f"({round(unreachable_ratio * 100, 1)}%)."
            )
    if transport_failures:
        reasons.append(
            f"{transport_failures} result(s) show a DNS, connection or empty-response "
            "failure rather than a verified block page."
        )
    if longest_streak >= 3:
        reasons.append(
            f"{longest_streak} consecutive websites were unreachable; that pattern "
            "is more typical of a network/VPN/DNS interruption than unrelated outages."
        )
    if controls:
        failure_ratio = len(failures) / len(controls)
        score += min(50.0, len(failures) * 12.0 + failure_ratio * 20.0)
        if failures:
            reasons.append(
                f"{len(failures)} of {len(controls)} neutral control checks failed."
            )
            if first_failure is not None:
                after = first_failure.after_domain or "the start of the run"
                reasons.append(
                    f"The first control failure was after {after}; later results may "
                    "be false positives."
                )
    if network_context.get("vpn_interfaces"):
        score += 8.0
        reasons.append("A VPN interface was detected on this device.")
    if network_context.get("vpn_providers"):
        score += 4.0
        reasons.append(
            "VPN/tunnel hint: "
            + ", ".join(str(item) for item in network_context["vpn_providers"])
            + "."
        )
    if network_context.get("proxies"):
        score += 6.0
        reasons.append("A proxy configuration was detected on this device.")
    dns_filters = [
        str(item) for item in network_context.get("dns_filters") or [] if str(item)
    ]
    if dns_filters:
        # A resolver that can filter describes configuration, not what
        # happened to these domains. Outcomes and neutral controls provide
        # evidence of active interference.
        reasons.append(
            "A DNS service with filtering features is configured: "
            + ", ".join(dns_filters)
            + ". This alone does not mean it blocked any checked website."
        )
    hosts_blocks = int(network_context.get("hosts_block_entries") or 0)
    if hosts_blocks:
        score += min(18.0, 6.0 + hosts_blocks)
        reasons.append(
            f"The hosts file contains {hosts_blocks} sinkhole entr"
            f"{'y' if hosts_blocks == 1 else 'ies'}; this can block domains "
            "independently of the installed blocker."
        )
    if controls and failures and len(failures) == len(controls):
        score = max(score, 70.0)
    # If independent neutral sites all work, target failures alone cannot
    # establish network-wide interference. Keep the signal below "high".
    if controls and not failures and not hosts_blocks:
        score = min(score, 39.0)
    if total == 0 and not failures and not hosts_blocks:
        score = 0.0
    if (
        total
        and unreachable == 0
        and not failures
        and not hosts_blocks
    ):
        score = min(score, 15.0)
    risk_percent = max(0, min(99, round(score)))
    if risk_percent >= 55:
        risk_level = "high"
        summary = (
            "Strong signs that a VPN, DNS filter or local blocker interfered "
            "with this run; treat failed results as suspect and recheck them."
        )
    elif risk_percent >= 25:
        risk_level = "elevated"
        summary = (
            "Some failures may come from a VPN, DNS filter or local blocker. "
            "Compare the neutral controls and recheck the listed domains."
        )
    else:
        risk_level = "low"
        summary = (
            "The scan found no strong network-wide interference evidence. "
            "Failed website checks may still need confirmation."
        )
    false_positive_suspected = bool(
        failures
        or hosts_blocks
        or (total >= 3 and unreachable_ratio >= 0.5)
        or (recheck_domains and risk_percent >= 25)
    )
    return ProbeHealth(
        risk_percent=risk_percent,
        risk_level=risk_level,
        summary=summary,
        reasons=reasons,
        controls=controls,
        control_failures=len(failures),
        first_failure_after=first_failure.after_domain if first_failure else "",
        first_failure_at=first_failure.checked_at if first_failure else 0,
        false_positive_suspected=false_positive_suspected,
        checked=total,
        blocked=blocked,
        unreachable=unreachable,
        opened=opened,
        affected_domains=affected_domains,
        recheck_domains=recheck_domains,
        generated_at=utc_now(),
    )
