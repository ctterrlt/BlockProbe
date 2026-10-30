# SPDX-License-Identifier: AGPL-3.0-or-later
"""First-run language selection follows the latest Flet change event."""

from __future__ import annotations

from types import SimpleNamespace

from blockprobe.ui.consent import _selected_language


def test_consent_language_prefers_changed_control_over_stale_dropdown_value() -> None:
    event = SimpleNamespace(data=None, control=SimpleNamespace(value="it"))

    assert _selected_language(event, "en") == "it"


def test_consent_language_prefers_supported_event_data() -> None:
    event = SimpleNamespace(data="fr", control=SimpleNamespace(value="it"))

    assert _selected_language(event, "en") == "fr"


def test_consent_language_ignores_unsupported_event_values() -> None:
    event = SimpleNamespace(data="unsupported", control=SimpleNamespace(value=None))

    assert _selected_language(event, "en") == "en"
