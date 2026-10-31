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


class FakeDialogs:
    """Minimal dialog stack so banner replacement can be asserted."""

    def __init__(self) -> None:
        self.controls: list = []

    def update(self) -> None:
        pass


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
        self.web = False
        self._dialogs = FakeDialogs()

    def add(self, *controls) -> None:  # type: ignore[no-untyped-def]
        self.controls.extend(controls)

    def clean(self) -> None:
        self.controls.clear()

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:  # type: ignore[no-untyped-def]
        self.dialog = dialog
        self._dialogs.controls.append(dialog)

    def pop_dialog(self):  # type: ignore[no-untyped-def]
        dialog, self.dialog = self.dialog, None
        if dialog in self._dialogs.controls:
            self._dialogs.controls.remove(dialog)
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


def test_consent_storage_failure_is_not_reported_as_missing_consent(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_consent() -> bool:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(actions.runtime().scan, "consent_required", broken_consent)
    with pytest.raises(RuntimeError, match="database is locked"):
        actions.consent_required()
    result = actions.refresh_portals()
    assert result.ok is False
    assert "Consent required" not in result.title
    assert "consent" in result.title.casefold()
    scan_result = actions.start_scan(actions.ScanRequest(all_sources=True))
    assert scan_result.ok is False
    assert scan_result.title == "Could not check the consent record"
    resume_result = actions.resume_scan()
    assert resume_result.ok is False
    assert resume_result.title == "Could not check the consent record"


def test_consent_language_selection_applies_before_acceptance(action_env: Path) -> None:
    from types import SimpleNamespace

    from blockprobe.i18n import get_language
    from blockprobe.ui.consent import consent_view

    page = FakePage()
    consent = consent_view(page, lambda: None)  # type: ignore[arg-type]
    language = consent.content.controls[1]  # type: ignore[union-attr]
    assert language.on_select is not None
    language.value = "it"

    language.on_select(
        SimpleNamespace(name="select", data=None, control=language)
    )

    assert actions.runtime().settings.config.app.language == "it"
    assert get_language() == "it"
    assert actions.consent_required() is True
    assert page.updates == 1
    rebuilt_consent = page.controls[0].content
    assert rebuilt_consent.content.controls[2].value == "Prima della prima richiesta di rete"


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


def test_custom_website_and_portal_feed_are_separate_sources(action_env: Path) -> None:
    custom = actions.add_custom_domains("single-site.it")
    assert custom.ok is True
    portal = actions.add_portal_definition(
        "Example Regulator",
        "https://regulator.gov.it/blocked-sites",
        "IT",
    )
    assert portal.ok is True

    app_runtime = actions.runtime()
    choices = app_runtime.portal_choices()
    assert any(label == "Example Regulator" for _, label in choices)
    assert [site.domain for site in app_runtime.storage.list_custom_sites()] == ["single-site.it"]
    assert app_runtime.storage.list_portals()


def test_scan_preview_tracks_custom_only_selection(action_env: Path) -> None:
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    assert actions.add_custom_domains("example.com").ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)
    assert "Portal lists older than the refresh window" in ui.preview_text.value

    ui.source_checkboxes["custom"].value = True
    ui._apply_source_change("custom", True)

    assert ui.source_checkboxes["all"].value is False
    assert "Sources: none" in ui.preview_text.value
    assert "Portal lists older than the refresh window" not in ui.preview_text.value


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
    assert refused_delete.consent_cleared is False
    deleted = actions.delete_all_data("RESET ALL")
    assert deleted.ok is True
    assert deleted.consent_cleared is True


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
    assert ui._run_feedback_visible is True
    assert ui.progress_text.visible is True
    assert "No websites" in ui.progress_text.value
    previous_run = actions.latest_run_summary()
    assert previous_run is None
    from blockprobe.core.enums import RunStatus
    from blockprobe.core.models import RunSummary

    ui._render_summary(
        RunSummary(
            run_id="previous",
            status=RunStatus.COMPLETED,
            total=1,
            started_at=1,
            finished_at=2,
        )
    )
    assert "No websites" in ui.progress_text.value


def test_new_action_banner_replaces_the_previous_one(action_env: Path) -> None:
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    ui.apply(actions.ActionResult("first", ""))
    first = ui.snack
    assert first is not None
    assert page._dialogs.controls == [first]

    ui.apply(actions.ActionResult("second", ""))
    assert ui.snack is not None
    assert ui.snack is not first
    assert page._dialogs.controls == [ui.snack]
    assert first.open is False


def test_normal_actions_do_not_reenter_consent(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    shown: list[bool] = []
    monkeypatch.setattr(actions, "consent_required", lambda: True)
    monkeypatch.setattr(ui, "_show_consent", lambda: shown.append(True))
    ui.run_async(lambda: actions.ActionResult("done", ""))
    assert shown == []
    assert ui.snack is not None
    assert ui.snack.content.value == "done"


def _export_ui(action_env: Path, monkeypatch: pytest.MonkeyPatch):
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)
    return ui_app, ui


class _FailingPicker:
    def __init__(self) -> None:
        pass

    async def save_file(self, **_kwargs) -> str | None:
        raise RuntimeError("platform picker unavailable")


class _CancellingPicker:
    def __init__(self) -> None:
        pass

    async def save_file(self, **_kwargs) -> str | None:
        return None


class _AndroidDocumentPicker:
    """Android SAF returns an opaque document id, not a filesystem path."""

    def __init__(self) -> None:
        pass

    async def save_file(self, **_kwargs) -> str | None:
        return "/document/5"


def test_global_theme_choice_updates_every_menu(action_env: Path) -> None:
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)
    assert ui.theme_menu is not None

    ui._set_theme("dark")
    assert actions.runtime().settings.config.app.theme_mode == "dark"
    assert ui.theme_dropdown.value == "dark"
    assert ui._theme_menu_items["dark"].checked is True
    assert ui._theme_menu_items["system"].checked is False
    assert "Dark" in (ui.theme_menu.tooltip or "")


def test_export_picker_failure_keeps_unique_local_copies(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    ui_app, ui = _export_ui(action_env, monkeypatch)
    monkeypatch.setattr(ui_app.ft, "FilePicker", _FailingPicker)
    statuses: list[actions.ActionResult] = []

    def write_export(destination: Path) -> actions.ActionResult:
        destination.write_text("export payload", encoding="utf-8")
        return actions.ActionResult("Report written", str(destination))

    export = ui._export_to_file(
        write_export, "txt", file_stem="fallback-test", on_result=statuses.append
    )
    asyncio.run(export(None))
    asyncio.run(export(None))

    reports = actions.runtime().settings.reports_dir
    files = sorted(reports.glob("fallback-test-*.txt"))
    assert len(files) == 2
    assert {path.read_text(encoding="utf-8") for path in files} == {"export payload"}
    assert len(statuses) == 2
    for status in statuses:
        assert str(reports) in status.detail
        assert "reports folder" in status.detail
    assert ui.snack is not None
    assert ui.snack.content.value == "Report written"


def test_export_hides_android_document_ids(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    ui_app, ui = _export_ui(action_env, monkeypatch)
    monkeypatch.setattr(ui_app.ft, "FilePicker", _AndroidDocumentPicker)
    statuses: list[actions.ActionResult] = []

    def write_export(destination: Path) -> actions.ActionResult:
        destination.write_text("export payload", encoding="utf-8")
        return actions.ActionResult("Report written", str(destination))

    export = ui._export_to_file(
        write_export, "txt", file_stem="android-test", on_result=statuses.append
    )
    asyncio.run(export(None))

    assert statuses
    detail = statuses[-1].detail
    assert "android-test-" in detail
    assert "Saved to the selected location." in detail
    assert "/document/5" not in detail


def test_export_cancel_does_not_create_a_fallback(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    ui_app, ui = _export_ui(action_env, monkeypatch)
    monkeypatch.setattr(ui_app.ft, "FilePicker", _CancellingPicker)
    statuses: list[actions.ActionResult] = []

    def write_export(destination: Path) -> actions.ActionResult:
        destination.write_text("export payload", encoding="utf-8")
        return actions.ActionResult("Report written", str(destination))

    export = ui._export_to_file(
        write_export, "txt", file_stem="cancelled-test", on_result=statuses.append
    )
    asyncio.run(export(None))

    reports = actions.runtime().settings.reports_dir
    assert not list(reports.glob("cancelled-test-*.txt"))
    assert statuses
    assert statuses[-1].title == "Export cancelled"


def test_export_preparation_error_recovers_generated_output(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    _, ui = _export_ui(action_env, monkeypatch)
    statuses: list[actions.ActionResult] = []

    def broken_export(destination: Path) -> actions.ActionResult:
        destination.write_text("partial output", encoding="utf-8")
        raise RuntimeError("renderer stopped after writing")

    export = ui._export_to_file(
        broken_export, "txt", file_stem="recovered-test", on_result=statuses.append
    )
    asyncio.run(export(None))

    reports = actions.runtime().settings.reports_dir
    files = list(reports.glob("recovered-test-*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "partial output"
    assert statuses
    assert statuses[-1].ok is False
    assert "preparation error" in statuses[-1].detail
