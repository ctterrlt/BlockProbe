# SPDX-License-Identifier: AGPL-3.0-or-later
"""Report scope, controllable risk, interception and dynamic-signature fields."""

from __future__ import annotations

import csv
import json

import pytest

from blockprobe import __version__
from blockprobe.core.config import Settings
from blockprobe.core.enums import Confidence, RunStatus, Verdict
from blockprobe.core.models import ResultRow, ScanSelection, utc_now
from blockprobe.probe.health import ControlCheck, assess_probe_health
from blockprobe.services.report import ReportService
from blockprobe.storage import build_storage


@pytest.fixture()
def storage(temp_settings: Settings):
    backend = build_storage(temp_settings)
    yield backend
    backend.close()


def _row(run_id: str, domain: str, verdict: Verdict, reason: str) -> ResultRow:
    return ResultRow(
        run_id=run_id,
        domain=domain,
        verdict=verdict,
        reason_code=reason,
        confidence=Confidence.MEDIUM,
        blocker_hint="betblocker" if verdict is Verdict.BLOCKED else None,
        needs_review=verdict is Verdict.UNREACHABLE,
        evidence={
            "interception": {
                "redirect_chain": ["https://betblocker.org/blocked/"],
                "redirect_statuses": [302],
                "embedded_targets": ["https://betblocker.org/blocked/"],
                "reopens_on_return": True,
                "styles": ["http_redirect", "302_or_equivalent", "reopens_on_return"],
            }
        },
        checked_at=utc_now(),
        hosts=[domain],
        sources=["Test portal"],
        title="Blocked by BetBlocker",
        redirect_chain=["https://betblocker.org/blocked/"],
    )


def test_reports_include_scope_controls_risk_and_interception(
    temp_settings: Settings,
    storage,
) -> None:
    run = storage.create_run(
        selection=ScanSelection(all_sources=True),
        total=2,
        app_version=__version__,
        os_name="Linux",
        network_context={
            "resolvers": ["208.67.222.123"],
            "resolver_labels": ["208.67.222.123 (OpenDNS FamilyShield)"],
            "dns_filters": ["208.67.222.123 (OpenDNS FamilyShield)"],
            "vpn_interfaces": ["tailscale0"],
            "vpn_providers": ["Tailscale via tailscale0"],
            "browser_doh": "unknown",
        },
        debug=False,
    )
    opened = _row(run.id, "one.example", Verdict.OPENED, "unclassified")
    blocked = _row(run.id, "two.example", Verdict.BLOCKED, "blocker_redirect")
    blocked.needs_review = True
    blocked.evidence["curl_cross_check"] = {
        "status": "conflict",
        "verdict": Verdict.OPENED.value,
        "warning": "Warning: curl opened the website successfully.",
    }
    storage.save_site_result(opened, [])
    storage.save_site_result(blocked, [])
    storage.update_run_status(run.id, RunStatus.COMPLETED, utc_now())

    controls = [
        ControlCheck(
            url="https://www.google.com/generate_204",
            ok=True,
            status=204,
            sequence=1,
            checked_at=utc_now(),
        ),
        ControlCheck(
            url="https://www.wikipedia.org/",
            ok=False,
            error="connect error",
            after_domain="one.example",
            sequence=2,
            checked_at=utc_now(),
        ),
    ]
    health = assess_probe_health(
        [opened, blocked],
        controls,
        storage.get_run(run.id).network_context,
    )
    health.signature_refresh = {
        "url": "https://betblocker.org/blocked/",
        "fetched": True,
        "status": 200,
        "new_patterns": ["Blocked\\s+by\\s+BetBlocker"],
        "matched_defaults": ["Blocked\\s+by\\s+BetBlocker"],
        "total_patterns": 1,
    }
    storage.save_run_health(run.id, health.to_dict())

    service = ReportService(temp_settings, storage)
    saved = storage.get_run(run.id)
    summary = storage.get_run_summary(run.id)
    markdown = service.render_markdown(saved, summary, [opened, blocked])

    assert "## Scope and privacy" in markdown
    assert "Age verification: **not asked**" in markdown
    assert "No age verification" in markdown
    assert "GDPR/US-law claim" in markdown
    assert "Legal frameworks: **not applied or claimed**" in markdown
    assert "Filtering DNS evidence" in markdown
    assert "First neutral control failure after" in markdown
    assert "betblocker.org/blocked/" in markdown
    assert "302_or_equivalent" in markdown
    assert "reopens_on_return" in markdown
    assert "Dynamic BetBlocker patterns" in markdown
    assert "Warning: curl opened the website successfully." in markdown

    payload = json.loads(service.render_json(saved, summary, [opened, blocked]))
    assert payload["scope"]["age_verification"]["status"] == "not asked"
    assert payload["run"]["health"]["risk_percent"] == health.risk_percent
    assert payload["summary"]["interference_risk_percent"] == health.risk_percent
    assert payload["summary"]["first_control_failure_after"] == "one.example"
    exported_blocked = next(
        item for item in payload["results"] if item["domain"] == blocked.domain
    )
    assert (
        exported_blocked["evidence"]["curl_cross_check"]["warning"]
        == "Warning: curl opened the website successfully."
    )

    csv_text = service.render_csv([opened, blocked], saved)
    assert "scope" in csv_text.splitlines()[0]
    csv_blocked = next(
        row for row in csv.DictReader(csv_text.splitlines()) if row["domain"] == blocked.domain
    )
    assert csv_blocked["warnings"] == "Warning: curl opened the website successfully."
    assert "filtering dns" in markdown.lower()
