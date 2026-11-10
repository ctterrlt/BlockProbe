# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action layer and UI shell smoke tests (no network access).

The actions are the single implementation behind the CLI and the Flet app. The
UI test builds every screen against a fake page object, so a broken control
argument fails the suite without opening a window.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blockprobe import i18n
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
        self._theme_mode = None
        self.theme_mode_assignments = 0
        self.theme = None
        self.dark_theme = None
        self.bgcolor = None
        self.padding = 0
        self.horizontal_alignment = None
        self.width = None
        self.platform_brightness = None
        self.controls: list = []
        self.updates = 0
        self.clean_calls = 0
        self.dialog = None
        self.web = False
        self._dialogs = FakeDialogs()
        self.scroll_keys: list[str | int | float | bool | None] = []

    @property
    def theme_mode(self):  # type: ignore[no-untyped-def]
        return self._theme_mode

    @theme_mode.setter
    def theme_mode(self, value):  # type: ignore[no-untyped-def]
        self.theme_mode_assignments += 1
        self._theme_mode = value

    def add(self, *controls) -> None:  # type: ignore[no-untyped-def]
        self.controls.extend(controls)

    def clean(self) -> None:
        self.clean_calls += 1
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

    def run_task(self, handler, *args, **kwargs):  # type: ignore[no-untyped-def]
        import asyncio

        result = handler(*args, **kwargs)
        if hasattr(result, "__await__"):
            return asyncio.run(result)
        return result

    async def scroll_to(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.scroll_keys.append(kwargs.get("scroll_key"))


@pytest.fixture()
def action_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    previous_language = i18n.get_language()
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
    i18n.set_language(previous_language)


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
    app_runtime = actions.runtime()
    assert app_runtime.settings.config.probe.use_control_resolver is True
    assert app_runtime.scan._engine.config.use_control_resolver is True
    assert load_config(action_env / "config.toml").config.probe.use_control_resolver is True

    opted_out = actions.save_settings(use_control_resolver=False)
    assert opted_out.ok is True
    assert app_runtime.settings.config.probe.use_control_resolver is False
    assert app_runtime.scan._engine.config.use_control_resolver is False
    assert load_config(action_env / "config.toml").config.probe.use_control_resolver is False


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


def test_consent_accept_does_not_clear_the_visible_root(action_env: Path) -> None:
    from types import SimpleNamespace

    import flet as ft

    from blockprobe.ui.consent import consent_view

    page = FakePage()
    existing_root = ft.Container()
    page.controls.append(existing_root)
    accepted: list[bool] = []
    consent = consent_view(page, lambda: accepted.append(True))  # type: ignore[arg-type]
    agree_button = consent.content.controls[-1].controls[0].content  # type: ignore[union-attr]

    agree_button.on_click(SimpleNamespace())

    assert actions.consent_required() is False
    assert accepted == [True]
    assert page.controls == [existing_root]
    assert page.clean_calls == 0


def test_theme_objects_and_mode_are_stable_between_repaints(action_env: Path) -> None:
    import flet as ft

    from blockprobe.ui.components import apply_page_theme, palette_background

    page = FakePage()
    apply_page_theme(page, "light", update=False)
    light_theme = page.theme
    dark_theme = page.dark_theme
    first_mode_assignments = page.theme_mode_assignments

    apply_page_theme(page, "light", update=False)
    assert page.theme is light_theme
    assert page.dark_theme is dark_theme
    assert page.theme_mode_assignments == first_mode_assignments

    apply_page_theme(page, "dark", update=False)
    assert page.theme is light_theme
    assert page.dark_theme is dark_theme
    assert page.theme_mode is ft.ThemeMode.DARK
    assert page.theme_mode_assignments == first_mode_assignments + 1
    assert page.bgcolor == palette_background(dark=True)


def test_consent_startup_failure_shows_retryable_screen_without_blank_update(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import flet as ft

    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    page = FakePage()
    page.controls.append(ft.Container())

    def fail_to_build(_page):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated Android shell startup failure")

    monkeypatch.setattr(ui_app, "_main_shell", fail_to_build)
    ui_app._enter_main(page)  # type: ignore[arg-type]

    assert len(page.controls) == 1
    assert page.updates == 1
    assert page.clean_calls == 0
    assert isinstance(page.controls[0], ft.SafeArea)
    startup_view = page.controls[0].content
    error_controls = startup_view.content.controls
    assert error_controls[1].value == "BlockProbe could not start"
    assert "simulated Android shell startup failure" in error_controls[3].value
    assert error_controls[4].content == "Retry"


def test_consent_layout_starts_narrow_and_tracks_resize(action_env: Path) -> None:
    from types import SimpleNamespace

    from blockprobe.ui.consent import consent_view

    page = FakePage()
    page.width = None
    view = consent_view(page, lambda: None)  # type: ignore[arg-type]
    assert view.padding == 16
    assert page.on_resize is not None

    page.width = 820
    page.on_resize(SimpleNamespace(height=900))
    assert view.padding == 32
    page.on_resize(SimpleNamespace(width=360, height=800))
    assert view.padding == 16


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


def test_portal_accepts_a_bare_hostname(action_env: Path) -> None:
    result = actions.add_portal_definition(
        "Bare host regulator",
        "www.example.com/blocked-sites",
        "IT",
    )
    assert result.ok is True
    stored = [
        portal
        for portal in actions.runtime().storage.list_portals()
        if portal.name == "Bare host regulator"
    ]
    assert stored
    assert stored[0].start_url == "https://www.example.com/blocked-sites"

    plain = actions.add_portal_definition("Plain domain", "example.com", "IT")
    assert plain.ok is True
    assert any(
        portal.start_url == "https://example.com"
        for portal in actions.runtime().storage.list_portals()
        if portal.name == "Plain domain"
    )

    rejected = actions.add_portal_definition("Broken feed", "not a url", "IT")
    assert rejected.ok is False
    credential_url = actions.add_portal_definition(
        "Credential URL", "https://user:secret@example.com/feed", "IT"
    )
    assert credential_url.ok is False
    invalid_port = actions.add_portal_definition(
        "Invalid port", "https://example.com:invalid/feed", "IT"
    )
    assert invalid_port.ok is False


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


def test_ui_shell_builds_without_a_window(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # The flexible title column must stay under a Row, not a wrapping layout:
    # Flutter's Expanded child cannot be laid out inside Wrap on Android.
    assert ui.header.content.wrap is False
    assert ui.header.content.controls[1].expand is True
    assert len(ui.views) == 7
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
    pending: list[tuple[object, object]] = []
    monkeypatch.setattr(
        ui,
        "run_async",
        lambda action, *, on_result=None: pending.append((action, on_result)),
    )
    ui._start_scan_clicked()
    assert ui._run_feedback_visible is True
    assert ui.progress_text.visible is True
    assert "Preparing scan" in ui.progress_text.value
    assert ui.start_button is not None and ui.start_button.disabled is True
    assert len(pending) == 1
    action, on_result = pending[0]
    preview = action()  # type: ignore[operator]
    assert preview.ok is False  # type: ignore[union-attr]
    assert "No websites" in preview.detail  # type: ignore[union-attr]
    on_result(preview)  # type: ignore[operator]
    assert ui._run_start_failed is True
    assert "No websites" in ui.progress_text.value
    assert ui.start_button.disabled is False
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


def test_ui_layout_adapts_to_available_width(action_env: Path) -> None:
    from types import SimpleNamespace

    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    page = FakePage()
    page.width = None
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)
    assert ui._compact_layout is True
    assert ui._bottom_navigation is True
    assert ui.mobile_nav.visible is True
    assert ui.nav.visible is False

    # These are logical page widths, so devices using the same OS select
    # layouts from their actual available width instead of platform identity.
    bottom_cutover, table_cutover, expanded_cutover = ui._responsive_thresholds()

    def inner_width(width: float, navigation_width: float) -> float:
        gutter = max(8, min(20, round(width * 0.03))) * 2
        return width - navigation_width - gutter - 32

    rail_width = float(ui.nav.min_width or 68) + float(ui.nav_divider.width or 1)
    expanded_width = float(ui.nav.min_extended_width or 172) + float(
        ui.nav_divider.width or 1
    )
    assert inner_width(bottom_cutover, rail_width) >= 360
    assert inner_width(table_cutover, rail_width) >= 620
    assert inner_width(expanded_cutover, expanded_width) >= 600

    ui._on_resize(SimpleNamespace(width=320, height=640))
    assert ui.content_area.padding == 10
    assert ui.mobile_nav.visible is True
    assert all(slot.col == {"xs": 12, "sm": 6} for slot in ui._schedule_field_slots)

    logs_card = ui.views[3].controls[1]
    logs_row = logs_card.content.controls[1].controls[0]
    assert logs_row.controls[2].col["xs"] == 12
    assert logs_row.controls[3].col["xs"] == 12

    ui._on_resize(SimpleNamespace(width=390, height=844))
    assert ui.content_area.padding == 12
    assert ui.mobile_nav.visible is True

    ui._on_resize(SimpleNamespace(width=bottom_cutover - 1, height=800))
    assert ui._compact_layout is True
    assert ui.mobile_nav.visible is True

    ui._on_resize(SimpleNamespace(width=bottom_cutover, height=800))
    assert ui.nav.visible is True
    assert ui.nav.extended is False
    assert ui.mobile_nav.visible is False

    ui._on_resize(SimpleNamespace(width=table_cutover - 1, height=900))
    assert ui._compact_layout is True

    ui._on_resize(SimpleNamespace(width=table_cutover, height=900))
    assert ui._compact_layout is False
    assert ui.nav.visible is True
    assert ui.nav.extended is False

    ui._on_resize(SimpleNamespace(width=expanded_cutover, height=900))
    assert ui.nav.extended is True
    assert ui.content_area.padding == 20
    assert all(slot.col == {"xs": 12, "sm": 4} for slot in ui._schedule_field_slots)

    # A phone client can report its size only through the resize event.
    ui._on_resize(SimpleNamespace(width=360, height=800))
    assert ui._compact_layout is True
    assert ui.mobile_nav.visible is True
    assert ui.nav.visible is False


def test_system_theme_uses_event_brightness_and_recolors_on_resize(
    action_env: Path,
) -> None:
    from types import SimpleNamespace

    import flet as ft

    from blockprobe.ui import app as ui_app
    from blockprobe.ui.components import palette_background

    assert actions.accept_consent().ok is True
    assert actions.save_settings(theme_mode="system").ok is True
    page = FakePage()
    page.width = 1280
    page.platform_brightness = ft.Brightness.LIGHT
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    # The event carries the new brightness even when the page property is stale.
    ui_app._on_platform_brightness_change(
        page, SimpleNamespace(brightness=ft.Brightness.DARK)
    )
    assert ui._platform_brightness == ft.Brightness.DARK
    assert page.bgcolor == palette_background(dark=True)

    # Resizing rebuilds cards with light constants; the live theme must recolor
    # them with the last known system brightness before the page is painted.
    page.width = 360
    ui._on_resize(SimpleNamespace(width=360, height=800))
    assert page.bgcolor == palette_background(dark=True)


def test_android_schedule_actions_stay_enabled_and_explain_unavailability(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blockprobe import scheduler
    from blockprobe.ui import app as ui_app

    monkeypatch.setattr(
        scheduler,
        "status",
        lambda *_args, **_kwargs: scheduler.ScheduleStatus(
            platform="android",
            directory=Path("/tmp/blockprobe-schedule"),
        ),
    )
    assert actions.accept_consent().ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    assert ui._schedule_platform == "android"
    assert ui.schedule_install_button is not None
    assert ui.schedule_install_button.disabled is False
    assert ui.schedule_remove_button is not None
    assert ui.schedule_remove_button.disabled is False
    assert "Android" in ui.schedule_status.value

    result = ui._install_schedule()
    assert result.ok is False
    assert "Android" in result.detail
    result = ui._remove_schedule()
    assert result.ok is False
    assert "Android" in result.detail
    result = ui._schedule_state_action()
    assert result.ok is False
    assert "Android" in result.detail


def test_profile_refresh_shows_local_vpn_and_dns_context(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import blockprobe.diagnostics as diagnostics
    from blockprobe.core.models import NetworkContext
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    context = NetworkContext(
        timestamp=1_798_000_000,
        os_name="android",
        app_version="1.4.0",
        resolvers=["94.140.14.14"],
        resolver_labels=["94.140.14.14 (AdGuard DNS)"],
        dns_filters=["AdGuard DNS"],
        vpn_interfaces=["tun0"],
        vpn_providers=["Generic VPN/tunnel"],
        private_dns="Private DNS is configured",
        browser_doh="Unknown",
    )
    monkeypatch.setattr(diagnostics, "collect_network_context", lambda: context)
    page = FakePage()
    page.width = 320
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    ui._show_screen(6)

    assert ui.profile_root is not None
    assert ui.profile_root.scroll == ui_app.ft.ScrollMode.AUTO
    assert ui.profile_status.value.startswith("Profile updated:")
    assert len(ui.profile_content.controls) == 4
    details = ui.profile_content.controls[0].content.controls[1]
    assert any("Generic VPN/tunnel" in control.value for control in details.controls)
    dns = ui.profile_content.controls[1].content.controls[1]
    dns_values = dns.controls[0]
    resolver_line = next(
        control.value for control in dns_values.controls if "Configured resolvers" in control.value
    )
    assert "dns.adguard-dns.com" in resolver_line
    assert "AdGuard" in resolver_line
    assert "94.140.14.14" in resolver_line
    assert any("Known filter categories" in control.value for control in dns_values.controls)
    category_line = next(
        control.value
        for control in dns_values.controls
        if "Known filter categories" in control.value
    )
    assert "ads" in category_line and "trackers" in category_line
    other = ui.profile_content.controls[2].content.controls[1]
    assert any("unknown" in control.value for control in other.controls)


def test_android_profile_merges_native_dns_and_private_dns_name(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import blockprobe.diagnostics as diagnostics
    from blockprobe.android_network import AndroidNetwork
    from blockprobe.core.models import NetworkContext
    from blockprobe.ui import app as ui_app
    from blockprobe.ui import profile as profile_ui

    assert actions.accept_consent().ok is True
    context = NetworkContext(
        timestamp=1_798_000_000,
        os_name="android",
        app_version="1.4.0",
        private_dns="Private DNS could not be read by Python",
    )
    snapshot = AndroidNetwork(
        available=True,
        dns_servers=("94.140.14.14", "1.1.1.3", "not-an-ip"),
        private_dns_active=True,
        private_dns_hostname="dns.adguard.com",
    )

    async def read_state(*, page=None):  # type: ignore[no-untyped-def]
        return snapshot

    monkeypatch.setattr(diagnostics, "collect_network_context", lambda: context)
    monkeypatch.setattr(profile_ui, "is_android", lambda: True)
    monkeypatch.setattr(profile_ui, "read_active_network", read_state)
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    asyncio.run(ui._load_profile(0))

    assert context.resolvers == ["94.140.14.14", "1.1.1.3"]
    assert context.private_dns == "dns.adguard.com"
    dns = ui.profile_content.controls[1].content.controls[1]
    dns_values = dns.controls[0]
    resolver_line = next(
        control.value
        for control in dns_values.controls
        if "Configured resolvers" in control.value
    )
    assert "dns.adguard-dns.com" in resolver_line
    assert "AdGuard DNS" in resolver_line
    assert "94.140.14.14" in resolver_line
    other = ui.profile_content.controls[2].content.controls[1]
    private_dns_line = next(
        control.value
        for control in other.controls
        if "Android Private DNS" in control.value
    )
    assert "dns.adguard.com" in private_dns_line


def test_android_profile_falls_back_to_localized_unknown_when_native_state_is_unavailable(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import blockprobe.diagnostics as diagnostics
    from blockprobe.android_network import AndroidNetwork
    from blockprobe.core.models import NetworkContext
    from blockprobe.ui import app as ui_app
    from blockprobe.ui import profile as profile_ui

    assert actions.accept_consent().ok is True
    context = NetworkContext(
        timestamp=1_798_000_000,
        os_name="android",
        private_dns="Android Private DNS cannot be read without elevated rights",
    )

    async def read_state(*, page=None):  # type: ignore[no-untyped-def]
        return AndroidNetwork(available=False, error="unsupported_platform")

    monkeypatch.setattr(diagnostics, "collect_network_context", lambda: context)
    monkeypatch.setattr(profile_ui, "is_android", lambda: True)
    monkeypatch.setattr(profile_ui, "read_active_network", read_state)
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    asyncio.run(ui._load_profile(0))

    assert context.private_dns != "Android Private DNS cannot be read without elevated rights"
    other = ui.profile_content.controls[2].content.controls[1]
    private_dns_line = next(
        control.value
        for control in other.controls
        if "Android Private DNS" in control.value
    )
    assert "elevated rights" not in private_dns_line


def test_profile_settings_return_triggers_refresh(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from types import SimpleNamespace

    import flet as ft

    from blockprobe.diagnostics import system_dns
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    monkeypatch.setattr(system_dns, "open_android_network_settings", lambda: True)
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)
    ui.current_screen = 6

    asyncio.run(ui._open_device_network_settings())
    assert ui._profile_refresh_on_resume is True
    assert "Refresh the profile" in ui.profile_status.value

    refreshed: list[bool] = []
    monkeypatch.setattr(ui, "_refresh_profile", lambda: refreshed.append(True))
    ui._on_app_lifecycle_state_change(
        SimpleNamespace(state=ft.AppLifecycleState.RESUME)
    )
    assert refreshed == [True]
    assert ui._profile_refresh_on_resume is False


def test_open_android_network_settings_uses_system_settings_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from subprocess import CompletedProcess

    from blockprobe.diagnostics import system_dns

    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> CompletedProcess[str]:
        commands.append(command)
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("ANDROID_ROOT", "/system")
    monkeypatch.setattr(system_dns.subprocess, "run", fake_run)

    assert system_dns.open_android_network_settings() is True
    assert commands == [
        ["/system/bin/am", "start", "-a", "android.settings.SETTINGS"]
    ]


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_result_filter_reapplies_selected_palette(action_env: Path, mode: str) -> None:
    from blockprobe.ui import app as ui_app
    from blockprobe.ui.components import palette_background

    assert actions.accept_consent().ok is True
    assert actions.save_settings(theme_mode=mode).ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    ui.results_search.value = "sample"
    ui.refresh_results()

    assert page.bgcolor == palette_background(dark=mode == "dark")
    assert ui.results_empty.content.controls[1].color is not None


def test_manage_websites_scrolls_to_custom_list(action_env: Path) -> None:
    from blockprobe.ui import app as ui_app

    assert actions.accept_consent().ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)

    ui._manage_websites()

    assert ui.current_screen == 1
    assert page.scroll_keys == ["blockprobe-custom-sites"]


def test_android_scan_start_uses_localized_foreground_feedback_without_blocking_scan(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blockprobe import android_foreground, i18n
    from blockprobe.android_network import AndroidNetwork
    from blockprobe.ui import app as ui_app
    from blockprobe.ui import run as run_screen

    assert actions.accept_consent().ok is True
    i18n.set_language("it")
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)
    monkeypatch.setattr(run_screen, "is_android", lambda: True)
    requested: dict[str, object] = {}

    async def start_foreground(page_arg, **kwargs):  # type: ignore[no-untyped-def]
        requested.update(kwargs)
        return android_foreground.ForegroundScan(
            active=True,
            notifications_enabled=False,
        )

    monkeypatch.setattr(run_screen, "start_scan_foreground", start_foreground)
    async def read_network(*, page=None):  # type: ignore[no-untyped-def]
        return AndroidNetwork(
            available=True,
            dns_servers=("94.140.14.14",),
            private_dns_active=True,
            private_dns_hostname="dns.adguard-dns.com",
        )

    monkeypatch.setattr(run_screen, "read_active_network", read_network)
    scheduled: list[tuple[object, object]] = []

    def queue_action(action, *, on_result=None):  # type: ignore[no-untyped-def]
        scheduled.append((action, on_result))

    ui.run_async = queue_action
    requested_scan: list[actions.ScanRequest] = []

    def start_scan(scan_request: actions.ScanRequest) -> actions.ActionResult:
        requested_scan.append(scan_request)
        return actions.ActionResult("Scan started", "")

    monkeypatch.setattr(actions, "start_scan", start_scan)
    monkeypatch.setattr(ui, "_request", lambda: actions.ScanRequest(all_sources=True))

    ui._start_scan_clicked()

    assert requested["title"] == i18n.t("BlockProbe scan")
    assert requested["body"] == i18n.t("Checking selected websites…")
    assert len(scheduled) == 1
    scheduled[0][0]()  # type: ignore[operator]
    assert len(requested_scan) == 1
    assert requested_scan[0].android_dns_servers == ("94.140.14.14",)
    assert requested_scan[0].android_private_dns_hostname == "dns.adguard-dns.com"
    assert requested_scan[0].android_private_dns_active is True
    assert ui._foreground_scan_started is True
    assert ui.progress_text.value == i18n.t(
        "Android notifications are off. The scan can continue while "
        "BlockProbe is active, but background work may stop."
    )


def test_android_foreground_service_stops_after_pause_or_completion(
    action_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from types import SimpleNamespace

    from blockprobe.ui import app as ui_app
    from blockprobe.ui import run as run_screen

    assert actions.accept_consent().ok is True
    page = FakePage()
    ui_app.build_app(page)  # type: ignore[arg-type]
    ui = page.data
    assert isinstance(ui, ui_app.BlockProbeUi)
    stopped: list[object] = []

    async def stop_foreground(page_arg=None):  # type: ignore[no-untyped-def]
        stopped.append(page_arg)

    monkeypatch.setattr(run_screen, "stop_scan_foreground", stop_foreground)
    ui._foreground_scan_started = True
    ui._foreground_scan_run_id = "run-1"
    active_run = SimpleNamespace(id="run-1")
    paused = SimpleNamespace(
        run_id="run-1",
        status=SimpleNamespace(value="paused"),
    )

    changed = asyncio.run(ui._sync_android_foreground_scan(active_run, paused))

    assert changed is True
    assert stopped == [page]
    assert ui._foreground_scan_started is False
    assert ui._foreground_scan_run_id is None

    ui._foreground_scan_started = True
    ui._foreground_scan_run_id = "run-1"
    changed = asyncio.run(ui._sync_android_foreground_scan(None, None))
    assert changed is True
    assert stopped == [page, page]
