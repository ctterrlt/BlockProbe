# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pagination discovery and loop protection (FR-3)."""

from __future__ import annotations

from blockprobe.core.models import Portal
from blockprobe.portals.pagination import (
    FormRequest,
    PaginationState,
    discover_form_pagination,
    discover_next_links,
    page_number_from_url,
    query_variants,
)
from blockprobe.services.refresh import _resolve_page_url


def test_adm_fixture_pager_links_are_found_in_page_order(
    adm_page1: str, adm_portal: Portal
) -> None:
    links = discover_next_links(adm_page1, adm_portal.start_url)
    pager_links = [link for link in links if "pager=" in link]
    assert any("pager=2" in link for link in pager_links)
    assert any("pager=3" in link for link in pager_links)
    assert pager_links.index(next(link for link in pager_links if "pager=2" in link)) < (
        pager_links.index(next(link for link in pager_links if "pager=3" in link))
    )
    # Sort/filter links must never rank as pagination.
    assert not any("tipo_gioco" in link for link in links)


def test_different_layout_pagination_is_found_by_label(
    alt_layout_page: str,
) -> None:
    links = discover_next_links(alt_layout_page, "https://portal.example/operators")
    assert any("page=2" in link for link in links)


def test_portlet_query_parameters_survive_a_pager_click() -> None:
    base = (
        "https://www.adm.gov.it/portale/monopoli?p_p_id=example_portlet"
        "&p_p_lifecycle=2&p_p_state=normal"
    )
    merged = _resolve_page_url(
        base, "https://www.adm.gov.it/portale/monopoli?pager=2&"
    )
    assert "pager=2" in merged
    assert "p_p_id=example_portlet" in merged
    assert "p_p_lifecycle=2" in merged


def test_query_merge_only_happens_on_the_same_path() -> None:
    base = "https://portal.example/list?p_p_id=example_portlet"
    other = _resolve_page_url(base, "https://portal.example/other?pager=2")
    assert "p_p_id" not in other
    assert "pager=2" in other


def test_relative_pager_keeps_extra_filters() -> None:
    base = "https://portal.example/list?tipo_gioco=slot&p_p_id=example_portlet"
    merged = _resolve_page_url(base, "?pager=3&")
    assert "tipo_gioco=slot" in merged
    assert "p_p_id=example_portlet" in merged
    assert "pager=3" in merged


def test_pagination_state_stops_after_two_empty_pages() -> None:
    state = PaginationState()
    assert state.register("https://portal.example/list?pager=1", "h1", 20) is True
    assert state.register("https://portal.example/list?pager=2", "h2", 5) is True
    assert state.register("https://portal.example/list?pager=3", "h3", 0) is True
    assert state.register("https://portal.example/list?pager=4", "h4", 0) is False
    assert state.pages == 4
    assert state.already_visited("https://portal.example/list?pager=1") is True
    assert state.already_visited("", "h3") is True
    assert state.already_visited("https://portal.example/list?pager=9") is False


def test_form_pagination_replays_hidden_fields() -> None:
    html = """
    <form action="/operators" method="post">
      <input type="hidden" name="page" value="1">
      <input type="hidden" name="token" value="abc">
    </form>
    """
    requests = discover_form_pagination(html, "https://portal.example/operators", max_pages=2)
    assert len(requests) == 2
    assert all(isinstance(request, FormRequest) for request in requests)
    assert requests[0].method == "post"
    assert requests[0].data["page"] == "2"
    assert requests[0].data["token"] == "abc"
    assert requests[1].data["page"] == "3"


def test_query_variants_build_numbered_urls() -> None:
    variants = query_variants("https://portal.example/list?a=1", "page", 3)
    assert len(variants) == 3
    assert "page=2" in variants[0] and "a=1" in variants[0]
    assert "page=4" in variants[2]


def test_page_number_from_url() -> None:
    assert page_number_from_url("https://portal.example/list?pager=4") == 4
    assert page_number_from_url("https://portal.example/list") == 1
