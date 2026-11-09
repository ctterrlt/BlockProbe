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
from blockprobe.probe.rust_engine import aggregate_indices

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
    chosen_index, rust_conflict, uncertain_indices = aggregate_indices(classifications)
    chosen = classifications[chosen_index]
    uncertain_opened = [classifications[index] for index in uncertain_indices]
    evidence = dict(chosen.evidence)
    curl_checks = [
        {
            "primary_verdict": item.verdict.value,
            **item.evidence["curl_cross_check"],
        }
        for item in classifications
        if isinstance(item.evidence.get("curl_cross_check"), dict)
    ]
    if curl_checks:
        evidence["curl_cross_checks"] = curl_checks
        curl_warnings = [
            str(item["warning"])
            for item in curl_checks
            if isinstance(item.get("warning"), str) and item["warning"]
        ]
        if curl_warnings:
            evidence["curl_warnings"] = list(dict.fromkeys(curl_warnings))
    curl_conflict = any(item.get("status") == "conflict" for item in curl_checks)
    uncertain_opened_conflict = rust_conflict and chosen.verdict is Verdict.BLOCKED
    if uncertain_opened_conflict:
        evidence["uncertain_opened_variants"] = [
            {
                "reason_code": item.reason_code,
                "confidence": item.confidence.value,
                "http_status": item.evidence.get("http", {}).get("status")
                if isinstance(item.evidence.get("http"), dict)
                else None,
                "note": (
                    "This variant loaded a page without block evidence, but that "
                    "is not proof of an open site. The verified block evidence is "
                    "kept and the result needs review."
                ),
            }
            for item in uncertain_opened
        ]
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
        needs_review=chosen.needs_review or curl_conflict or uncertain_opened_conflict,
        rule_id=chosen.rule_id,
        title=chosen.title,
        snippet=chosen.snippet,
        evidence=evidence,
    )
