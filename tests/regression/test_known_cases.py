# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression entries for known real-world cases.

- ``bwin.it`` is an ADM-licensed operator: when it is reachable it must be
  reported as opened (a leak if a blocker is installed), never as blocked
  without block evidence, and it must stay on the ADM fixture list.
- ``amazon-slots.it`` may not be on the ADM licensed list at all: an unlicensed
  or mirror domain must be reported honestly (opened and needs-review when the
  page is unclassified), never silently marked blocked.

The exact verdict for a saved live capture is added only when a real response
has been recorded and reviewed: those tests are skipped until then.
"""

from __future__ import annotations

import pytest

from blockprobe.core.enums import ReasonCode, Verdict
from blockprobe.core.models import DnsResult, HttpFetchResult, Portal, ProbeObservation
from blockprobe.probe.classifier import classify
from tests.unit.test_generic_extractor import ADM_EXPECTED_HOSTS


def test_bwin_stays_on_the_adm_fixture(adm_page1: str, adm_portal: Portal) -> None:
    from blockprobe.portals.generic_extractor import extract_candidates

    hosts = {
        candidate.host
        for candidate in extract_candidates(adm_page1, adm_portal.start_url, adm_portal)
    }
    assert "www.bwin.it" in hosts
    assert "www.bwin.it" in ADM_EXPECTED_HOSTS


def test_an_unclassified_page_is_never_silently_blocked() -> None:
    """The amazon-slots case: no verified signature means no BLOCKED verdict."""
    body = "<html><title>Amazon Slots</title><body>" + "welcome " * 40 + "</body></html>"
    observation = ProbeObservation(
        domain="amazon-slots.it",
        host="www.amazon-slots.it",
        scheme="https",
        dns=DnsResult(host="www.amazon-slots.it", status="ok", ips=["203.0.113.10"]),
        fetch=HttpFetchResult(
            url="https://www.amazon-slots.it/",
            final_url="https://www.amazon-slots.it/",
            status=200,
            body=body,
            title="Amazon Slots",
        ),
    )
    result = classify(observation)
    assert result.verdict is Verdict.OPENED
    assert result.reason_code == ReasonCode.UNCLASSIFIED.value
    assert result.needs_review is True


@pytest.mark.skip(reason="no verified capture of bwin.it in the repository yet")
def test_bwin_it_opens_when_reachable() -> None:
    """Add the saved page under tests/fixtures/ and assert the real verdict."""


@pytest.mark.skip(reason="no verified capture of amazon-slots.it in the repository yet")
def test_amazon_slots_it_live_verdict() -> None:
    """Add the saved page under tests/fixtures/ and assert the real verdict."""
