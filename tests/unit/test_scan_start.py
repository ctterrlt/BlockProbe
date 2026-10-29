# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed neutral control must not silently cancel the requested scan."""

from __future__ import annotations

import asyncio

import blockprobe.services as services
from blockprobe import __version__
from blockprobe.core.enums import Confidence, RunStatus, Verdict
from blockprobe.core.models import Classification, ScanSelection
from blockprobe.probe.betblocker import SignatureRefresh
from blockprobe.probe.health import ControlCheck
from blockprobe.probe.runner import RunControl
from blockprobe.services import build_runtime, reset_runtime
from blockprobe.ui import actions


def test_initial_control_failure_still_checks_selected_sites(
    temp_settings, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    reset_runtime()
    app_runtime = build_runtime(temp_settings)
    monkeypatch.setattr(services, "_runtime", app_runtime)
    assert actions.accept_consent().ok
    assert actions.add_custom_domains("scan-target.it").ok
    service = app_runtime.scan
    selection = ScanSelection(all_sources=False, with_custom=True)
    sites = app_runtime.storage.select_sites(selection)
    run = app_runtime.storage.create_run(
        selection=selection,
        total=len(sites),
        app_version=__version__,
        os_name="test",
        network_context={},
        debug=False,
    )
    checked: list[str] = []

    async def no_signature_network() -> SignatureRefresh:
        return SignatureRefresh(fetched=False)

    async def failed_neutral_check(*args, **kwargs) -> ControlCheck:  # type: ignore[no-untyped-def]
        return ControlCheck(url="https://control.invalid/", ok=False, error="offline")

    async def scan_selected_sites(run_id, selected, control, on_result, on_progress=None, **kwargs):  # type: ignore[no-untyped-def]
        for site in selected:
            checked.append(site.domain)
            on_result(
                site,
                Classification(
                    verdict=Verdict.UNREACHABLE,
                    reason_code="connect_timeout",
                    confidence=Confidence.MEDIUM,
                ),
                [],
            )
        return False

    monkeypatch.setattr(service, "refresh_dynamic_signatures", no_signature_network)
    monkeypatch.setattr(service._engine, "check_control", failed_neutral_check)
    monkeypatch.setattr(service._engine, "run_batch", scan_selected_sites)

    outcome = asyncio.run(service.execute(run, sites, RunControl()))
    assert outcome.status is RunStatus.COMPLETED
    assert checked == ["scan-target.it"]
    assert app_runtime.storage.count_results(run.id) == 1
    reset_runtime()

