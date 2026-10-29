# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layout-independent extraction (FR-3) against saved pages.

The ADM fixture is the real saved page 1 of the Concessionari table. The
acceptance rule is exact: the 20 hosts below, nothing else, with the hostnames
kept exactly as listed. The second fixture proves a different layout (no table,
plain text, JSON in a script) yields the same kind of result, and that
JavaScript property chains never become candidates.
"""

from __future__ import annotations

from blockprobe.core.enums import Origin
from blockprobe.core.models import Portal
from blockprobe.portals.generic_extractor import (
    extract_candidates,
    extract_page,
    find_ajax_content_url,
)

ADM_EXPECTED_HOSTS = {
    "www.admiralbet.it",
    "www.starcasino.it",
    "www.betsson.it",
    "www.betfair.it",
    "www.betflag.it",
    "www.betpoint.it",
    "www.sunbet.it",
    "www.bgame.it",
    "www.netbet.it",
    "online.casinosanremo.it",
    "www.casinoveneziaonline.it",
    "www.codere.it",
    "www.zonagioco.it",
    "www.domusbet.it",
    "www.eplay24.it",
    "www.sportium.it",
    "www.betwin360.it",
    "www.eurobet.it",
    "www.bwin.it",
    "www.giocodigitale.it",
}

ALT_EXPECTED_HOSTS = {
    "www.alphacasino.it",
    "online.betmaestro.it",
    "pokerzone.it",
    "www.slotvillage.it",
}


def test_adm_page1_yields_exactly_the_twenty_expected_hosts(
    adm_page1: str, adm_portal: Portal
) -> None:
    candidates = extract_candidates(adm_page1, adm_portal.start_url, adm_portal)
    hosts = {candidate.host for candidate in candidates}
    assert hosts == ADM_EXPECTED_HOSTS
    # The bare apex host is dropped when a more specific host exists
    # (codere.it vs www.codere.it), so no separate apex row remains.
    assert "codere.it" not in hosts
    domains = {candidate.domain for candidate in candidates}
    expected_domains = {
        host.split(".", 1)[1] if host.count(".") > 1 else host for host in hosts
    }
    assert domains == expected_domains


def test_adm_candidates_keep_the_table_as_the_best_origin(
    adm_page1: str, adm_portal: Portal
) -> None:
    candidates = extract_candidates(adm_page1, adm_portal.start_url, adm_portal)
    assert {candidate.origin for candidate in candidates} == {Origin.TABLE_TEXT}


def test_adm_page_is_not_mistaken_for_an_ajax_shell(
    adm_page1: str, adm_portal: Portal
) -> None:
    page = extract_page(adm_page1, f"{adm_portal.start_url}?pager=1", adm_portal)
    assert page.candidates
    assert page.content_hash
    assert find_ajax_content_url(adm_page1, adm_portal.start_url) is None


def test_different_layout_yields_only_real_hosts(
    alt_layout_page: str, adm_portal: Portal
) -> None:
    candidates = extract_candidates(
        alt_layout_page, "https://portal.example/operators", adm_portal
    )
    assert {candidate.host for candidate in candidates} == ALT_EXPECTED_HOSTS
    origins = {candidate.host: candidate.origin for candidate in candidates}
    assert origins["www.alphacasino.it"] is Origin.ANCHOR
    assert origins["online.betmaestro.it"] is Origin.TEXT
    assert origins["pokerzone.it"] is Origin.SCRIPT
    assert origins["www.slotvillage.it"] is Origin.SCRIPT


def test_javascript_property_chains_are_never_candidates(
    alt_layout_page: str, adm_portal: Portal
) -> None:
    candidates = extract_candidates(
        alt_layout_page, "https://portal.example/operators", adm_portal
    )
    hosts = {candidate.host for candidate in candidates}
    for noise in (
        "liferay.data",
        "event.page",
        "window.analytics",
        "init.es",
        "anchor.search",
        "frontend-js-spa-web@3.0.35",
    ):
        assert noise not in hosts


def test_denylist_removes_search_networks_and_analytics(
    alt_layout_page: str, adm_portal: Portal
) -> None:
    candidates = extract_candidates(
        alt_layout_page, "https://portal.example/operators", adm_portal
    )
    domains = {candidate.domain for candidate in candidates}
    assert "matomo.org" not in domains
    assert "facebook.com" not in domains


def test_find_ajax_content_url_reads_plain_liferay_fragment() -> None:
    html = (
        "<script>"
        "var url = 'https://www.adm.gov.it/portale/x?p_p_id=example_portlet"
        "&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view';"
        "</script>"
    )
    found = find_ajax_content_url(html, "https://www.adm.gov.it/portale/x")
    assert found is not None
    assert "p_p_lifecycle=2" in found
    assert found.startswith("https://www.adm.gov.it/portale/x?")


def test_find_ajax_content_url_decodes_javascript_escapes() -> None:
    html = (
        "<script>var url = 'https:\\x2f\\x2fwww.adm.gov.it\\x2fportale\\x2fx\\x3fp_p_id=example_portlet"
        "\\x26p_p_lifecycle\\x3d2\\x26p_p_state\\x3dnormal';</script>"
    )
    found = find_ajax_content_url(html, "https://www.adm.gov.it/portale/x")
    assert found is not None
    assert found.startswith("https://www.adm.gov.it/portale/x?")
    assert "p_p_lifecycle=2" in found
    assert "p_p_state=normal" in found


def test_find_ajax_content_url_returns_none_without_a_fragment() -> None:
    assert find_ajax_content_url("<p>No portlet here</p>", "https://portal.example/") is None
