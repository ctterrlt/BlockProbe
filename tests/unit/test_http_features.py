# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static redirect, new-window and cookie handling in the HTTP fetcher."""

from __future__ import annotations

import httpx
import respx

from blockprobe.probe.http_fetch import (
    PROBE_USER_AGENT,
    detect_reopen_behaviour,
    extract_embedded_redirects,
    fetch,
)


def test_meta_refresh_and_window_open_targets_are_extracted() -> None:
    body = """
    <html><head>
    <meta http-equiv="refresh" content="0; url=/blocked/">
    </head><body>
    <script>
      window.open("https://betblocker.org/blocked/", "_blank");
      location.href = "/away";
    </script>
    </body></html>
    """
    targets = extract_embedded_redirects(body, "https://example.com/start")
    assert targets[0] == "https://example.com/blocked/"
    assert "https://betblocker.org/blocked/" in targets
    assert "https://example.com/away" in targets


def test_asset_urls_are_not_treated_as_navigation() -> None:
    body = (
        '<script>window.open("/static/logo.png")</script>'
        '<meta http-equiv="refresh" content="0; URL=\'/app.js\'">'
    )
    assert extract_embedded_redirects(body, "https://example.com/") == []


def test_reopen_hint_requires_both_a_navigation_and_a_lifecycle_event() -> None:
    assert detect_reopen_behaviour(
        '<script>window.open("https://betblocker.org/blocked/");'
        'document.addEventListener("visibilitychange", () => {});</script>'
    )
    assert not detect_reopen_behaviour(
        '<script>window.open("https://betblocker.org/blocked/");</script>'
    )


@respx.mock
async def test_302_chain_is_recorded_and_cookies_are_not_replayed() -> None:
    start = respx.get("https://start.example/").mock(
        return_value=httpx.Response(
            302,
            headers={
                "Location": "https://final.example/blocked/",
                "Set-Cookie": "session=should-not-replay",
            },
        )
    )
    final = respx.get("https://final.example/blocked/").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<html><head><title>Blocked by BetBlocker</title></head>'
                "<body>This domain was blocked by BetBlocker application.</body>"
                "</html>"
            ),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    )

    result = await fetch("https://start.example/", timeout=3.0)

    assert start.called
    assert final.called
    assert result.status == 200
    assert result.redirect_statuses == [302]
    assert result.redirect_chain == ["https://final.example/blocked/"]
    assert result.embedded_redirects == []
    assert start.calls[0].request.headers["user-agent"] == PROBE_USER_AGENT
    assert final.calls[0].request.headers["user-agent"] == PROBE_USER_AGENT
    assert final.calls[0].request.headers.get("cookie") is None
    assert "set-cookie" not in result.headers


@respx.mock
async def test_non_html_file_response_body_is_not_read() -> None:
    respx.get("https://files.example/download").mock(
        return_value=httpx.Response(
            200,
            content=b"not an html page or an asset that should be downloaded",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "attachment; filename=report.bin",
            },
        )
    )

    result = await fetch("https://files.example/download")

    assert result.status == 200
    assert result.body == ""
    assert result.body_bytes == 0
