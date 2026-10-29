# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live BetBlocker refresh: safe extraction and append-only storage."""

from __future__ import annotations

import sqlite3

import pytest

from blockprobe.core.config import Settings
from blockprobe.core.models import HttpFetchResult
from blockprobe.probe import betblocker
from blockprobe.storage import build_storage


@pytest.fixture()
def storage(temp_settings: Settings):
    backend = build_storage(temp_settings)
    yield backend
    backend.close()


def test_known_live_page_sentences_match_and_filler_is_ignored() -> None:
    body = """
    <html><head><title>Blocked by BetBlocker</title></head><body>
    <h1>Blocked by BetBlocker</h1>
    <p>This domain was blocked by BetBlocker application.</p>
    <p>If you think that is wrong, please contact us.</p>
    <p>Get the App</p>
    </body></html>
    """
    new, matched = betblocker.extract_live_patterns(body, "Blocked by BetBlocker")
    assert len(matched) >= 3
    assert all("Get the App" not in pattern for pattern in new)
    assert all("please contact" not in pattern for pattern in new)


def test_new_sentence_is_escaped_and_flagged_for_review() -> None:
    body = (
        "<html><body><p>Betting is blocked on this device by "
        "BetBlocker [test].</p></body></html>"
    )
    new, _ = betblocker.extract_live_patterns(body, "")
    assert len(new) == 1
    assert "Betting" in new[0].pattern
    assert new[0].needs_review is True
    assert new[0].source == betblocker.BETBLOCKER_SOURCE
    # Page text is stored as an escaped literal, not an executable expression.
    assert r"\[test\]" in new[0].pattern


async def test_refresh_adds_verified_defaults_once_and_never_removes(
    monkeypatch: pytest.MonkeyPatch,
    storage,
) -> None:
    body = (
        "<html><head><title>Blocked by BetBlocker</title></head><body>"
        "<p>This domain was blocked by BetBlocker application.</p>"
        "</body></html>"
    )

    async def fake_fetch(url: str, **_: object) -> HttpFetchResult:
        return HttpFetchResult(
            url=url,
            final_url=url,
            status=200,
            body=body,
            body_bytes=len(body.encode("utf-8")),
            title="Blocked by BetBlocker",
        )

    monkeypatch.setattr(betblocker, "fetch", fake_fetch)
    first = await betblocker.refresh_betblocker_signatures(storage)
    assert first.fetched is True
    assert len(first.new_patterns) >= 4
    assert len(storage.list_dynamic_signatures()) >= 4

    second = await betblocker.refresh_betblocker_signatures(storage)
    assert second.fetched is True
    assert second.new_patterns == []
    assert len(storage.list_dynamic_signatures()) >= 4
    assert set(first.new_patterns) <= {
        signature.pattern for signature in storage.list_dynamic_signatures()
    }


def test_dynamic_signature_table_rejects_direct_delete(
    temp_settings: Settings,
    storage,
) -> None:
    storage.add_dynamic_signatures(list(betblocker.BETBLOCKER_DEFAULT_PATTERNS))
    connection = sqlite3.connect(temp_settings.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM dynamic_signatures")
    finally:
        connection.close()
    assert len(storage.list_dynamic_signatures()) >= 4

    report = storage.delete_all_data()
    assert report.removed_signatures >= 4
    assert storage.list_dynamic_signatures() == []
