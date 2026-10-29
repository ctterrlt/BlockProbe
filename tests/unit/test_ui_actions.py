# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action layer and UI shell smoke tests (no network access).

The actions are the single implementation behind the CLI and the Flet app. The
UI test builds every screen against a fake page object, so a broken control
argument fails the suite without opening a window.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blockprobe.core.config import load_config
from blockprobe.services import reset_runtime
from blockprobe.ui import actions


class FakePage:
    """Minimal stand-in for ft.Page: records calls instead of rendering."""

    def __init__(self) -> None:
        self.data = None
        self.title = ""
        self.theme_mode = None
        self.bgcolor = None
        self.padding = 0
        self.horizontal_alignment = None
        self.controls: list = []
        self.updates = 0
        self.dialog = None

    def add(self, *controls) -> None:  # type: ignore[no-untyped-def]
        self.controls.extend(controls)

    def clean(self) -> None:
        self.controls.clear()

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:  # type: ignore[no-untyped-def]
        self.dialog = dialog

    def pop_dialog(self):  # type: ignore[no-untyped-def]
        dialog, self.dialog = self.dialog, None
        return dialog

    def run_thread(self, handler, *args, **kwargs):  # type: ignore[no-untyped-def]
        return handler(*args, **kwargs)


@pytest.fixture()
def action_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = tmp_path / "config.toml"
    # The suite pins the English source strings; language behaviour has its own
    # tests in test_i18n.py and test_config.py.
    config_path.write_text('[app]\nlanguage = "en"\n', encoding="utf-8")
    monkeypatch.setenv("BLOCKPROBE_CONFIG", str(config_path))
    monkeypatch.setenv("BLOCKPROBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("BLOCKPROBE_DEBUG", raising=False)
    monkeypatch.delenv("GS_DEBUG", raising=False)
    reset_runtime()
    yield tmp_path
    reset_runtime()


def test_self_check_passes(action_env: Path) -> None:
    result = actions.run_self_check()
    assert result.ok is True
    assert "SQLite" in result.detail
    assert str(action_env / "data") in result.detail


def test_consent_gates_network_actions(action_env: Path) -> None:
    assert actions.consent_required() is True
    refresh = actions.refresh_portals()
    assert refresh.ok is False
    assert "Consent" in refresh.title
    capture = actions.capture_signature("example.com")
    assert capture.ok is False
    assert "Consent" in capture.title
    accepted = actions.accept_consent()
    assert accepted.ok is True
    assert actions.consent_required() is False


def test_custom_domains_flow(action_env: Path) -> None:
    assert actions.consent_required() is True  # custom list needs no consent
    result = actions.add_custom_domains("https://www.one-casino.it/some/path\nnot a domain")
    assert result.ok is True
    listed = actions.list_custom_domains()
    assert "one-casino.it" in listed.detail

    preview = actions.preview_scan(actions.ScanRequest(all_sources=True, with_custom=True))
    assert preview.ok is True
    assert "Websites to probe: 1" in preview.detail

    removed = actions.remove_custom_domain("one-casino.it")
    assert removed.ok is True
    assert "one-casino.it" not in actions.list_custom_domains().detail


def test_rejected_input_never_silently_passes(action_env: Path) -> None:
    result = actions.add_custom_domains("localhost\n192.168.1.1\n")
    assert result.ok is False
    assert "localhost" in result.detail


def test_settings_are_persisted(action_env: Path) -> None:
    result = actions.save_settings(concurrency=5, timeout_seconds=4, refresh_ttl_hours=12)
    assert result.ok is True
    reloaded = load_config(action_env / "config.toml")
    assert reloaded.config.probe.concurrency == 5
    assert reloaded.config.probe.timeout_seconds == 4
    assert reloaded.config.portals.refresh_ttl_hours == 12

    bad = actions.save_settings(concurrency=0)
    assert bad.ok is False
    bad_url = actions.save_settings(control_url="ftp://example.com")
    assert bad_url.ok is False


def test_reset_and_delete_need_the_exact_word(action_env: Path) -> None:
    refused = actions.reset_website_list("reset")
    assert refused.ok is False
    assert "RESET" in refused.detail

    allowed = actions.reset_website_list("RESET")
    assert allowed.ok is True

    refused_delete = actions.delete_all_data("nope")
    assert refused_delete.ok is False
    deleted = actions.delete_all_data("RESET ALL")
    assert deleted.ok is True


def test_portals_and_diagnostics_work_offline(action_env: Path) -> None:
    listed = actions.list_portals()
    assert listed.ok is True
    assert "ADM" in listed.detail

    result = actions.diagnose()
    assert result.ok is True
    assert "Operating system" in result.detail


def test_report_without_a_run_is_refused(action_env: Path) -> None:
    result = actions.export_report("markdown")
    assert result.ok is False
    assert "No scan" in result.detail


def test_ui_shell_builds_without_a_window(action_env: Path) -> None:
    from blockprobe.ui import app as ui_app

    page = FakePage()
    consent = ui_app.build_app(page)  # type: ignore[arg-type]
    assert consent is not None
    assert page.data is None

    assert actions.accept_consent().ok is True
    shell = ui_app.build_app(page)  # type: ignore[arg-type]
    assert shell is not None
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    # Every screen is built and refreshable without touching the network.
    assert len(ui.views) == 6
    ui.refresh_results()
    ui.refresh_logs()
    ui.refresh_portal_texts()
    ui._reload_custom()
    assert ui.source_checkboxes
    assert any(box.data == "all" for box in ui.source_checkboxes.values())
    request = ui._request()
    assert request.all_sources is True

    # No websites stored and no automatic update: the start must refuse
    # honestly instead of probing anything.
    ui.auto_refresh_switch.value = False
    preview = ui._start_scan()
    assert preview.ok is False
    assert "No websites" in preview.detail
