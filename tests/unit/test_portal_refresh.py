# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portal refresh does not request robots.txt and shares the Chrome User-Agent."""

from __future__ import annotations

import httpx
import respx

from blockprobe.core.config import Settings
from blockprobe.core.enums import RefreshStatus
from blockprobe.core.models import Portal
from blockprobe.http_user_agent import CHROME_USER_AGENT
from blockprobe.services import refresh as refresh_module
from blockprobe.services.refresh import RefreshService
from blockprobe.storage import build_storage


@respx.mock
def test_portal_refresh_uses_chrome_ua_and_never_fetches_robots(
    temp_settings: Settings,
    monkeypatch,
) -> None:
    portal = Portal(
        uuid="11111111-2222-3333-4444-555555555555",
        name="Test portal",
        start_url="https://portal.example/list",
        # Legacy settings are ignored even when an old TOML file has true.
        respect_robots=True,
    )
    site_route = respx.get(portal.start_url).mock(
        return_value=httpx.Response(
            200,
            text='<html><body><a href="https://www.alphacasino.it/">Casino</a></body></html>',
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    )
    robots_route = respx.get("https://portal.example/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    monkeypatch.setattr(refresh_module, "load_portals", lambda _directories: [portal])
    storage = build_storage(temp_settings)
    try:
        summary = RefreshService(temp_settings, storage).refresh(force=True)
    finally:
        storage.close()

    assert site_route.called
    assert site_route.calls[0].request.headers["user-agent"] == CHROME_USER_AGENT
    assert not robots_route.called
    assert summary.results[0].status is RefreshStatus.OK
    assert summary.results[0].found == 1


def test_legacy_robots_option_is_ignored_when_loading_and_writing_portals() -> None:
    portal = Portal.from_toml(
        {
            "uuid": "11111111-2222-3333-4444-555555555555",
            "name": "Legacy portal",
            "source": {
                "start_url": "https://portal.example/list",
                "respect_robots": True,
            },
        }
    )

    assert portal.respect_robots is False
    assert "respect_robots" not in portal.to_toml()
