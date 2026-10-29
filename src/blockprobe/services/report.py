# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local reports and run comparison (FR-10).

Report files are written to the per-user reports directory and contain only
stored evidence: no cookies, no raw response bodies and no query strings.
Markdown never renders a domain as a clickable link, so a blocked website is
not one accidental tap away.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blockprobe import __version__
from blockprobe.core.compliance import scope_lines, scope_payload, scope_summary
from blockprobe.core.config import Settings
from blockprobe.core.enums import RunStatus, Verdict
from blockprobe.core.errors import ValidationError
from blockprobe.core.models import ResultRow, RunComparison, RunRecord, RunSummary, utc_iso, utc_now
from blockprobe.probe.health import ProbeHealth, assess_probe_health
from blockprobe.storage.base import ResultFilter, Storage

_LOGGER = logging.getLogger("blockprobe.services.report")

FORMATS = ("md", "markdown", "csv", "json")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _sanitize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    clean = dict(evidence or {})
    clean.pop("snippet", None)
    return clean


def _result_payload(result: ResultRow) -> dict[str, Any]:
    return {
        "domain": result.domain,
        "verdict": result.verdict.value,
        "verdict_label": result.verdict.label,
        "reason_code": result.reason_code,
        "reason": result.reason_label,
        "confidence": result.confidence.value,
        "blocker_hint": result.blocker_hint,
        "needs_review": result.needs_review,
        "sources": result.sources,
        "hosts": result.hosts,
        "title": result.title,
        "redirect_chain": result.redirect_chain,
        "checked_at": result.checked_at,
        "checked_at_iso": utc_iso(result.checked_at),
        "evidence": _sanitize_evidence(result.evidence),
    }


class ReportService:
    def __init__(self, settings: Settings, storage: Storage, cache=None) -> None:  # type: ignore[no-untyped-def]
        self.settings = settings
        self.storage = storage
        self.cache = cache

    # ------------------------------------------------------------------ loading

    def require_run(self, run_id: str | None) -> RunRecord:
        run = self.storage.get_run(run_id) if run_id else self.storage.latest_run()
        if run is None:
            raise ValidationError("No scan has been recorded yet.")
        return run

    def results(self, run_id: str, filters: ResultFilter | None = None) -> list[ResultRow]:
        return self.storage.get_results(run_id, filters or ResultFilter())

    def summary(self, run_id: str) -> RunSummary:
        return self.storage.get_run_summary(run_id)

    def compare(self, run_id: str, previous_run_id: str | None = None) -> RunComparison:
        current = self.storage.get_run(run_id)
        if current is None:
            raise ValidationError(f"Run {run_id} was not found.")
        previous: RunRecord | None = None
        if previous_run_id:
            previous = self.storage.get_run(previous_run_id)
        else:
            candidates = [
                run
                for run in self.storage.list_runs(limit=25)
                if run.id != run_id
                and run.status is RunStatus.COMPLETED
                and run.started_at <= current.started_at
            ]
            previous = candidates[0] if candidates else None
        current_rows = {row.domain: row for row in self.storage.get_results(run_id)}
        if previous is None:
            return RunComparison(
                run_id=run_id,
                previous_run_id=None,
                still_leaking=[
                    row for row in current_rows.values() if row.verdict is Verdict.OPENED
                ],
            )
        previous_rows = {row.domain: row for row in self.storage.get_results(previous.id)}
        comparison = RunComparison(run_id=run_id, previous_run_id=previous.id)
        for domain, row in current_rows.items():
            old = previous_rows.get(domain)
            if row.verdict is Verdict.OPENED:
                if old is None:
                    comparison.newly_leaking.append(row)
                elif old.verdict is Verdict.OPENED:
                    comparison.still_leaking.append(row)
                else:
                    comparison.newly_leaking.append(row)
            elif old is not None and old.verdict is Verdict.OPENED:
                comparison.resolved.append(row)
            if row.verdict is Verdict.BLOCKED and (old is None or old.verdict is not Verdict.BLOCKED):
                comparison.newly_blocked.append(row)
        for sort_list in (
            comparison.newly_leaking,
            comparison.newly_blocked,
            comparison.still_leaking,
            comparison.resolved,
        ):
            sort_list.sort(key=lambda item: item.domain)
        return comparison

    # ---------------------------------------------------------------- rendering

    def _health_for(self, run: RunRecord, results: list[ResultRow]) -> ProbeHealth:
        if run.health:
            return ProbeHealth.from_dict(run.health)
        return assess_probe_health(results, [], run.network_context)

    def render_json(
        self,
        run: RunRecord,
        summary: RunSummary,
        results: list[ResultRow],
        health: ProbeHealth | None = None,
    ) -> str:
        health = health or self._health_for(run, results)
        payload = {
            "product": "BlockProbe",
            "version": __version__,
            "generated_at": utc_now(),
            "generated_at_iso": utc_iso(utc_now()),
            "scope_summary": scope_summary(),
            "scope": scope_payload(),
            "run": {
                "id": run.id,
                "status": run.status.value,
                "started_at": run.started_at,
                "started_at_iso": utc_iso(run.started_at),
                "finished_at": run.finished_at,
                "finished_at_iso": utc_iso(run.finished_at),
                "total": run.total,
                "sources": run.sources,
                "app_version": run.app_version,
                "os": run.os_name,
                "debug": run.debug,
                "network_context": run.network_context,
                "health": health.to_dict(),
            },
            "summary": {
                "blocked": summary.blocked,
                "unreachable": summary.unreachable,
                "opened": summary.opened,
                "needs_review": summary.needs_review,
                "blocked_percent": summary.blocked_percent,
                "interference_risk_percent": health.risk_percent,
                "interference_risk_level": health.risk_level,
                "control_failures": health.control_failures,
                "first_control_failure_after": health.first_failure_after,
            },
            "results": [_result_payload(result) for result in results],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    def render_csv(
        self,
        results: list[ResultRow],
        run: RunRecord | None = None,
        health: ProbeHealth | None = None,
    ) -> str:
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        health = health or (self._health_for(run, results) if run is not None else None)
        recheck = set(health.recheck_domains) if health else set()
        writer.writerow(
            [
                "domain",
                "verdict",
                "reason_code",
                "reason",
                "confidence",
                "blocker_hint",
                "needs_review",
                "sources",
                "hosts",
                "checked_at_iso",
                "title",
                "redirect_chain",
                "interception_styles",
                "recheck_after_control_failure",
                "interference_risk_percent",
                "interference_risk_level",
                "first_control_failure_after",
                "scope",
            ]
        )
        for result in results:
            interception = result.evidence.get("interception") or {}
            styles = (
                "|".join(str(item) for item in interception.get("styles") or [])
                if isinstance(interception, dict)
                else ""
            )
            writer.writerow(
                [
                    result.domain,
                    result.verdict.value,
                    result.reason_code,
                    result.reason_label,
                    result.confidence.value,
                    result.blocker_hint or "",
                    "yes" if result.needs_review else "no",
                    " | ".join(result.sources),
                    " | ".join(result.hosts),
                    utc_iso(result.checked_at),
                    (result.title or "").replace("\n", " ").strip(),
                    " -> ".join(result.redirect_chain),
                    styles,
                    "yes" if result.domain in recheck else "no",
                    health.risk_percent if health else "",
                    health.risk_level if health else "",
                    health.first_failure_after if health else "",
                    scope_summary(),
                ]
            )
        return buffer.getvalue()

    def render_markdown(
        self,
        run: RunRecord,
        summary: RunSummary,
        results: list[ResultRow],
        comparison: RunComparison | None = None,
        health: ProbeHealth | None = None,
    ) -> str:
        def escape(value: str) -> str:
            return (value or "").replace("|", "\\|").replace("\n", " ").strip()

        def table(rows: list[ResultRow]) -> list[str]:
            lines = [
                "| Domain | Verdict | Why | Confidence | Source | Notes |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows:
                notes: list[str] = []
                if row.blocker_hint:
                    notes.append(escape(row.blocker_hint))
                if row.needs_review:
                    notes.append("needs review")
                if row.title:
                    notes.append(escape(row.title[:80]))
                lines.append(
                    "| {domain} | {verdict} | {reason} | {confidence} | {sources} | {notes} |".format(
                        domain=escape(row.domain),
                        verdict=row.verdict.short_label,
                        reason=escape(row.reason_label),
                        confidence=row.confidence.label,
                        sources=escape(", ".join(row.sources)),
                        notes=escape("; ".join(notes)),
                    )
                )
            return lines

        context = run.network_context or {}
        health = health or self._health_for(run, results)

        def context_list(key: str, fallback: str) -> str:
            values = context.get(key) or []
            return escape(", ".join(str(item) for item in values) or fallback)

        lines: list[str] = [
            "# BlockProbe report",
            "",
            f"- Run: `{run.id}`",
            f"- Started: {utc_iso(run.started_at)}",
            f"- Finished: {utc_iso(run.finished_at) or 'not finished'}",
            f"- Status: {run.status.value}",
            f"- BlockProbe: {run.app_version or __version__}",
            f"- Operating system: {escape(run.os_name) or 'unknown'}",
            "",
            "## Scope and privacy",
            "",
            *scope_lines(),
            "",
            "These statements describe the application's behaviour; they are not a "
            "legal certification or legal advice. No age verification, cookie "
            "consent, website terms, image download, upload or GDPR/US-law claim is "
            "part of a scan.",
            "",
            "## Summary",
            "",
            f"- Blocked: **{summary.blocked}**",
            f"- Unreachable: **{summary.unreachable}**",
            f"- Opened (leaks): **{summary.opened}**",
            f"- Needs review: **{summary.needs_review}**",
            f"- Blocked share: **{summary.blocked_percent}%**",
            f"- VPN/DNS interference risk: **{health.risk_percent}%** ({health.risk_level})",
            "",
            "## Probe reliability and interference risk",
            "",
            f"{health.summary}",
            "",
            "The percentage is calculated once at the end of the run from the "
            "complete result set. Detected VPN, proxy or filtering-DNS factors add "
            "risk; disabled, unknown or not-inspectable factors add nothing. It is "
            "a heuristic, not a diagnosis.",
            "",
        ]
        if health.reasons:
            lines.extend(f"- {escape(reason)}" for reason in health.reasons)
            lines.append("")
        if health.first_failure_after:
            lines.extend(
                [
                    f"**First neutral control failure after:** "
                    f"`{escape(health.first_failure_after)}`",
                    "",
                ]
            )
        elif health.controls:
            lines.extend(["No neutral control failure was recorded.", ""])
        else:
            lines.extend(
                [
                    "This run has no stored neutral-control timeline (an older run). "
                    "Use a new run to see the control-failure onset.",
                    "",
                ]
            )
        if health.controls:
            lines.extend(
                [
                    "| # | After domain | Control URL | Result | HTTP | Error |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for check in health.controls:
                lines.append(
                    "| {seq} | {after} | `{url}` | {result} | {status} | {error} |".format(
                        seq=check.sequence,
                        after=escape(check.after_domain) or "start",
                        url=escape(check.url),
                        result="pass" if check.ok else "fail",
                        status=check.status if check.status is not None else "",
                        error=escape(check.error or ""),
                    )
                )
            lines.append("")
        if health.recheck_domains:
            lines.extend(
                [
                    "Results recorded after the first control failure should be "
                    "rechecked manually:",
                    "",
                    *[f"- `{escape(domain)}`" for domain in health.recheck_domains[:200]],
                    "",
                ]
            )
        refresh = health.signature_refresh or {}
        if refresh:
            lines.extend(
                [
                    "## Dynamic BetBlocker patterns",
                    "",
                    f"- Live page: `{escape(str(refresh.get('url') or ''))}`",
                    f"- Fetched: {'yes' if refresh.get('fetched') else 'no'}",
                    f"- New patterns added: {len(refresh.get('new_patterns') or [])}",
                    f"- Live patterns matched: {len(refresh.get('matched_defaults') or [])}",
                    f"- Stored patterns: {refresh.get('total_patterns', 0)}",
                ]
            )
            if refresh.get("error"):
                lines.append(f"- Error: {escape(str(refresh['error']))}")
            lines.append("")
            if refresh.get("new_patterns"):
                lines.extend(
                    [f"- Added: `{escape(str(pattern))}`" for pattern in refresh["new_patterns"]]
                )
                lines.append("")
        leaks = [row for row in results if row.verdict is Verdict.OPENED]
        blocked = [row for row in results if row.verdict is Verdict.BLOCKED]
        unreachable = [row for row in results if row.verdict is Verdict.UNREACHABLE]

        if leaks:
            lines.extend(["## Websites that opened", "", *table(leaks), ""])
        else:
            lines.extend(
                [
                    "## Websites that opened",
                    "",
                    "No stored website opened in this run.",
                    "",
                ]
            )
        if blocked:
            lines.extend(["## Websites with block evidence", "", *table(blocked), ""])
        if unreachable:
            lines.extend(
                [
                    "## Destination unreachable",
                    "",
                    "These are not counted as blocked. They can be offline, geo-restricted "
                    "or filtered by something BlockProbe could not identify. When neutral "
                    "control checks also failed, treat them as possible network "
                    "interference rather than as proof of a blocker.",
                    "",
                    *table(unreachable),
                    "",
                ]
            )
        interception_rows: list[list[str]] = []
        for row in results:
            data = row.evidence.get("interception")
            if not isinstance(data, dict):
                continue
            styles = [str(item) for item in data.get("styles") or []]
            embedded_targets = [
                str(item) for item in data.get("embedded_targets") or []
            ]
            if not styles and not embedded_targets and not row.redirect_chain:
                continue
            details: list[str] = []
            statuses = data.get("redirect_statuses") or []
            if row.redirect_chain:
                details.append("chain: " + " -> ".join(row.redirect_chain))
            if statuses:
                details.append("statuses: " + ", ".join(str(item) for item in statuses))
            if embedded_targets:
                details.append("embedded: " + " | ".join(embedded_targets))
            if data.get("embedded_final_url"):
                details.append("embedded final: " + str(data["embedded_final_url"]))
            if data.get("reopens_on_return"):
                details.append("reopens when returning to the website")
            interception_rows.append(
                [
                    f"`{escape(row.domain)}`",
                    escape(", ".join(styles) or "redirect evidence"),
                    escape("; ".join(details)),
                ]
            )
        if interception_rows:
            lines.extend(
                [
                    "## Redirect, new-window and return evidence",
                    "",
                    "A known blocker landing page (for example "
                    "`https://betblocker.org/blocked/`) is direct evidence and is "
                    "reported as blocked without a body-regex check. Other rows show "
                    "302-style redirects, meta-refresh or `window.open` targets and "
                    "reopening behaviour observed in the static HTML.",
                    "",
                    "| Domain | Pattern | Details |",
                    "| --- | --- | --- |",
                    *[
                        f"| {domain} | {pattern} | {details} |"
                        for domain, pattern, details in interception_rows
                    ],
                    "",
                ]
            )
        if comparison is not None and comparison.previous_run_id:
            lines.extend(
                [
                    "## Compared with the previous run",
                    "",
                    f"- Previous run: `{comparison.previous_run_id}`",
                    f"- Newly leaking: {len(comparison.newly_leaking)}",
                    f"- Newly blocked: {len(comparison.newly_blocked)}",
                    f"- Still leaking: {len(comparison.still_leaking)}",
                    f"- Resolved: {len(comparison.resolved)}",
                    "",
                ]
            )
            if comparison.newly_leaking:
                lines.extend(["Newly leaking domains:", ""])
                lines.extend(f"- {escape(row.domain)}" for row in comparison.newly_leaking)
                lines.append("")
        review = [row for row in results if row.needs_review]
        if review:
            lines.extend(
                [
                    "## Needs review",
                    "",
                    "BlockProbe could not classify these with a verified signature. "
                    "Check them manually before drawing conclusions:",
                    "",
                    *[f"- {escape(row.domain)} ({escape(row.reason_label)})" for row in review],
                    "",
                ]
            )
        lines.extend(
            [
                "## Network context",
                "",
                f"- Resolvers: {context_list('resolvers', 'unknown')}",
                f"- Resolver details: {context_list('resolver_labels', 'unknown')}",
                f"- Filtering DNS evidence: {context_list('dns_filters', 'none detected')}",
                f"- Hosts-file sinkhole entries: {context.get('hosts_block_entries') or 0}",
                f"- Proxies: {context_list('proxies', 'none detected')}",
                f"- VPN interfaces: {context_list('vpn_interfaces', 'none detected')}",
                f"- VPN/tunnel hints: {context_list('vpn_providers', 'none detected')}",
                f"- IPv6 available: {'yes' if context.get('ipv6') else 'no'}",
                f"- Private DNS: {escape(str(context.get('private_dns') or 'unknown'))}",
                f"- Browser Secure DNS: {escape(str(context.get('browser_doh') or 'unknown'))}",
                "",
                "## How to read this report",
                "",
                "- A website is a leak only when a variant actually opened and content matched.",
                "- 'Unreachable' is not a block: the site can be down or geo-restricted.",
                "- A redirect or new window to a known blocker page is direct block evidence.",
                "- Failed neutral controls raise the interference risk; recheck the listed "
                "domains after disabling a VPN, proxy or private DNS filter.",
                "- BlockProbe does not verify age, set or store cookies, upload data, accept "
                "website terms, download images or make a GDPR/US-law compliance claim.",
                "- A single check with a VPN or proxy active says nothing about your normal setup.",
                "- No report is a medical or legal guarantee; BlockProbe is one more signal.",
                "",
                f"_Generated by BlockProbe {__version__} at {utc_iso(utc_now())}._",
                "",
            ]
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------ writing

    def generate(
        self,
        run_id: str | None = None,
        fmt: str = "md",
        destination: Path | None = None,
        filters: ResultFilter | None = None,
        include_comparison: bool = True,
    ) -> Path:
        fmt = (fmt or "md").lower()
        if fmt == "markdown":
            fmt = "md"
        if fmt not in {"md", "csv", "json"}:
            raise ValidationError(
                f"Unknown report format '{fmt}'. Use one of: md, csv, json."
            )
        run = self.require_run(run_id)
        summary = self.summary(run.id)
        results = self.results(run.id, filters)
        health = self._health_for(run, results)
        if fmt == "json":
            content = self.render_json(run, summary, results, health)
        elif fmt == "csv":
            content = self.render_csv(results, run, health)
        else:
            comparison = self.compare(run.id) if include_comparison else None
            content = self.render_markdown(run, summary, results, comparison, health)
        if destination is None:
            self.settings.reports_dir.mkdir(parents=True, exist_ok=True)
            destination = (
                self.settings.reports_dir
                / f"blockprobe-{run.id[:8]}-{_stamp()}.{fmt}"
            )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        _LOGGER.info(
            "report written",
            extra={"event": "report", "run_id": run.id, "domain": str(destination)},
        )
        return destination

    def export_domains(self, destination: Path | None = None) -> Path:
        if destination is None:
            self.settings.reports_dir.mkdir(parents=True, exist_ok=True)
            destination = self.settings.reports_dir / f"blockprobe-domains-{_stamp()}.txt"
        return self.storage.export_domain_list(Path(destination))


@dataclass(slots=True)
class ReportRequest:
    run_id: str | None = None
    fmt: str = "md"
    destination: Path | None = None
    leaks_only: bool = False
    needs_review: bool | None = None
