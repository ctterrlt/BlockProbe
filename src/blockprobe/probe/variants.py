# SPDX-License-Identifier: AGPL-3.0-or-later
"""Probe variant construction and verdict aggregation.

A stored site has a registered domain and one or more hostnames exactly as the
portal listed them. FR-6 asks for three variants: the host as listed, the apex
and ``www``, tried over https first and http second. The aggregate verdict is a
leak if any variant opened, otherwise blocked if any variant has block
evidence, otherwise unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass

from blockprobe.core.enums import Confidence, Verdict
from blockprobe.core.models import Classification, Site

MODES = ("as_listed", "apex", "www")


@dataclass(frozen=True, slots=True)
class VariantTarget:
    host: str
    scheme: str
    mode: str

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}/"


def build_hosts(
    domain: str, listed_hosts: list[str], modes: list[str] | None = None
) -> list[tuple[str, str]]:
    """Return ``(host, mode)`` pairs in a stable order without duplicates."""
    selected = modes or list(MODES)
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(host: str, mode: str) -> None:
        host = (host or "").strip().strip(".").lower()
        if not host or host in seen:
            return
        seen.add(host)
        results.append((host, mode))

    if "as_listed" in selected:
        for host in listed_hosts:
            add(host, "as_listed")
    if "apex" in selected:
        add(domain, "apex")
    if "www" in selected:
        add(f"www.{domain}", "www")
    return results


def build_variants(
    site: Site,
    modes: list[str] | None = None,
    schemes: tuple[str, ...] = ("https", "http"),
) -> list[VariantTarget]:
    variants: list[VariantTarget] = []
    for host, mode in build_hosts(site.domain, site.hosts, modes):
        for scheme in schemes:
            variants.append(VariantTarget(host=host, scheme=scheme, mode=mode))
    return variants


def _rank(classification: Classification) -> tuple[int, int]:
    verdict_rank = {
        Verdict.OPENED: 3,
        Verdict.BLOCKED: 2,
        Verdict.UNREACHABLE: 1,
    }[classification.verdict]
    return verdict_rank, classification.confidence.rank


def aggregate(domain: str, classifications: list[Classification]) -> Classification:
    """Combine per-variant classifications into one site verdict."""
    if not classifications:
        return Classification(
            verdict=Verdict.UNREACHABLE,
            reason_code="unclassified",
            confidence=Confidence.LOW,
            needs_review=True,
            evidence={"note": "No variant could be probed."},
        )
    best = sorted(classifications, key=_rank, reverse=True)[0]
    opened = [item for item in classifications if item.verdict is Verdict.OPENED]
    blocked = [item for item in classifications if item.verdict is Verdict.BLOCKED]
    if opened:
        chosen = sorted(opened, key=lambda item: item.confidence.rank, reverse=True)[0]
    elif blocked:
        # Prefer an explicit blocker hint over an unknown one.
        chosen = sorted(
            blocked,
            key=lambda item: (item.confidence.rank, 1 if item.blocker_hint else 0),
            reverse=True,
        )[0]
    else:
        chosen = best
    evidence = dict(chosen.evidence)
    evidence["variants"] = [
        {
            "verdict": item.verdict.value,
            "reason_code": item.reason_code,
            "confidence": item.confidence.value,
            "blocker_hint": item.blocker_hint,
            "needs_review": item.needs_review,
            "title": item.title,
            "rule_id": item.rule_id,
        }
        for item in classifications
    ]
    return Classification(
        verdict=chosen.verdict,
        reason_code=chosen.reason_code,
        confidence=chosen.confidence,
        blocker_hint=chosen.blocker_hint,
        needs_review=chosen.needs_review,
        rule_id=chosen.rule_id,
        title=chosen.title,
        snippet=chosen.snippet,
        evidence=evidence,
    )
