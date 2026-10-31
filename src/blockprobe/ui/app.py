# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flet application shell: page setup, navigation and control wiring.

Screens live in dedicated modules (``consent``, ``run``, ``portals``,
``results``, ``logs``, ``settings``, ``help``). This module composes them,
routes every button through :mod:`blockprobe.ui.actions`, and follows the
active run from the database so progress survives restarts. No probing or
storage logic lives here.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import flet as ft

from blockprobe import APP_NAME, __version__
from blockprobe.core.models import RunSummary
from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import (
    ACCENT_DIM,
    BACKGROUND,
    BAD,
    BORDER,
    CARD,
    MUTED,
    OK,
    SURFACE,
    TEXT,
    apply_page_theme,
    apply_palette,
    button,
    dark_for_mode,
    text,
)
from blockprobe.ui.consent import consent_view
from blockprobe.ui.help import help_view
from blockprobe.ui.logs import LogsScreen
from blockprobe.ui.portals import SourcesScreen
from blockprobe.ui.results import ResultsScreen
from blockprobe.ui.run import RunScreen
from blockprobe.ui.settings import SettingsScreen

_LOGGER = logging.getLogger("blockprobe.ui.app")


class BlockProbeUi(
    RunScreen,
    SourcesScreen,
    ResultsScreen,
    LogsScreen,
    SettingsScreen,
):
    """Holds the controls so actions and the poller can update them."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._alive = True
        self._poller_started = False
        destinations = [
            (ft.Icons.PLAY_CIRCLE_OUTLINE, t("Run")),
            (ft.Icons.TRAVEL_EXPLORE, t("Sources")),
            (ft.Icons.ARTICLE_OUTLINED, t("Results")),
            (ft.Icons.TERMINAL, t("Logs")),
            (ft.Icons.SETTINGS, t("Settings")),
            (ft.Icons.HELP_OUTLINE, t("Help")),
        ]
        self.nav = ft.NavigationRail(
            selected_index=0,
            destinations=[
                ft.NavigationRailDestination(icon=icon, label=label)
                for icon, label in destinations
            ],
            extended=True,
            min_extended_width=172,
            bgcolor=BACKGROUND,
            indicator_color=ACCENT_DIM,
            on_change=self._on_nav,
        )
        self.nav_divider = ft.VerticalDivider(width=1, color=BORDER)
        self.mobile_nav = ft.NavigationBar(
            selected_index=0,
            destinations=[
                *(
                    ft.NavigationBarDestination(
                        icon=icon,
                        label=t("Scan") if index == 0 else label,
                    )
                    for index, (icon, label) in enumerate(destinations[:4])
                ),
                ft.NavigationBarDestination(icon=ft.Icons.MORE_HORIZ, label=t("More")),
            ],
            label_behavior=ft.NavigationBarLabelBehavior.ONLY_SHOW_SELECTED,
            bgcolor=CARD,
            indicator_color=ACCENT_DIM,
            elevation=0,
            on_change=self._on_mobile_nav,
        )
        self.content_area = ft.Container(expand=True, padding=20)
        self.screen_title = text(t("Run"), 24, TEXT, weight=ft.FontWeight.BOLD)
        self.screen_description = text(
            t("Start with a portal update, then run a scan. Results stay on this device."),
            13,
            MUTED,
        )
        self.page_heading = ft.Column(
            [self.screen_title, self.screen_description], spacing=3
        )
        self.status_title = text(t("Ready"), 20, TEXT, weight=ft.FontWeight.BOLD)
        self.status_detail = text(
            t("Start with a portal update, then run a scan. Results stay on this device."),
            13,
        )
        self.snack: ft.SnackBar | None = None
        self.theme_menu: ft.PopupMenuButton | None = None
        self._theme_menu_items: dict[str, ft.PopupMenuItem] = {}
        self._action_sequence = 0
        self._last_poll_signature: tuple[object, ...] | None = None
        self._last_controls_signature: tuple[object, ...] | None = None
        self.views: list[ft.Control] = []
        self.current_screen = 0
        self._compact_layout = False
        self._last_results_poll = 0.0
        self.page.on_resize = self._on_resize

        self._setup_run()
        self._setup_sources()
        self._setup_results()
        self._setup_logs()
        self._setup_settings()

    # ------------------------------------------------------------------ helpers

    def _on_snack_dismiss(self, _event: Any) -> None:
        # Flet dispatches dismissal after the control has been removed from
        # its dialog stack. Ignore an older snackbar's late callback.
        if getattr(_event, "control", None) is self.snack:
            self.snack = None

    def _dismiss_snack(self) -> None:
        """Remove the current snackbar before a newer result is shown.

        ``pop_dialog()`` only marks the snackbar closed; Flutter can keep the
        old snackbar in its queue until the dismiss animation finishes, so the
        next result appeared behind or after the previous one. Remove the
        tracked control from Flet's dialog stack immediately as well.
        """
        previous = self.snack
        self.snack = None
        if previous is None:
            return
        previous.open = False
        dialogs = getattr(self.page, "_dialogs", None)
        try:
            if dialogs is not None and previous in dialogs.controls:
                dialogs.controls.remove(previous)
                dialogs.update()
            else:
                self.page.update()
        except Exception:
            _LOGGER.exception("could not remove the previous action banner")

    def apply(self, result: actions.ActionResult, sequence: int | None = None) -> None:
        if sequence is None:
            self._action_sequence += 1
            sequence = self._action_sequence
        if sequence != self._action_sequence:
            return
        self.status_title.value = result.title
        # Keep technical details in the relevant screen. Global feedback should
        # stay short and never expose filesystem paths or run identifiers.
        self.status_detail.value = ""
        self._apply_live_theme()
        mode = actions.runtime().settings.config.app.theme_mode
        dark = dark_for_mode(self.page, mode)
        self._dismiss_snack()
        snack = ft.SnackBar(
            content=text(
                result.title,
                14,
                "#0B1713" if dark else "#FFFFFF",
            ),
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=12,
            duration=3500,
            bgcolor=("#61B58A" if dark else OK) if result.ok else ("#D77C74" if dark else BAD),
            on_dismiss=self._on_snack_dismiss,
        )
        self.snack = snack
        try:
            self.page.show_dialog(snack)
        except Exception:
            _LOGGER.exception("could not show action feedback")
        try:
            self.page.update()
        except Exception:
            _LOGGER.exception("could not update page after action feedback")

    def run_async(self, action: Callable[[], actions.ActionResult]) -> None:
        self._action_sequence += 1
        sequence = self._action_sequence

        def work() -> None:
            try:
                result = action()
            except Exception:
                _LOGGER.exception("UI action failed")
                result = actions.ActionResult(
                    t("Action failed"),
                    t("The action could not be completed. Check Logs for details."),
                    ok=False,
                )
            # Only an action that explicitly cleared the consent record returns
            # to the first-run flow. Re-checking consent after every action
            # could turn a transient storage read error into a false prompt.
            if result.consent_cleared:
                self._show_consent()
                return
            try:
                self.refresh_run_controls()
            except Exception:
                _LOGGER.exception("could not refresh run controls after an action")
            try:
                self.apply(result, sequence)
            except Exception:
                _LOGGER.exception("could not display the completed action result")
                try:
                    self.status_title.value = result.title
                    self.status_detail.value = ""
                    self.page.update()
                except Exception:
                    _LOGGER.exception("could not show the action result fallback")

        self.page.run_thread(work)

    def _show_consent(self) -> None:
        """Return to first-run consent if local data deletion cleared it."""
        self._alive = False
        self._dismiss_snack()
        self.page.clean()
        self.page.add(
            ft.SafeArea(
                content=consent_view(self.page, lambda: _enter_main(self.page)),
                expand=True,
            )
        )
        _apply_saved_theme(self.page)
        self.page.update()

    def _wrap(self, action: Callable[[], actions.ActionResult]) -> Callable[[Any], None]:
        def on_click(_event: Any) -> None:
            self.run_async(action)

        return on_click

    def _unique_export_path(self, folder: Path, file_name: str) -> Path:
        """Return a non-destructive destination inside ``folder``."""
        destination = folder / file_name
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        for index in range(2, 10000):
            candidate = folder / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        # A directory with 10,000 exports of the same name is unlikely, but a
        # random suffix still avoids replacing an existing file.
        return folder / f"{stem}-{uuid4().hex[:12]}{suffix}"

    def _finish_export(
        self,
        result: actions.ActionResult,
        on_result: Callable[[actions.ActionResult], None] | None,
    ) -> None:
        """Publish an export result without letting a status label break it."""
        if on_result is not None:
            try:
                on_result(result)
            except Exception:
                _LOGGER.exception("could not update the export status label")
        self.apply(result)

    def _save_export_fallback(
        self,
        file_name: str,
        payload: bytes | None,
        result: actions.ActionResult,
        on_result: Callable[[actions.ActionResult], None] | None,
        *,
        failure_title: str,
        cause: BaseException | None = None,
        source_path: Path | None = None,
        recovery_note: str | None = None,
    ) -> None:
        """Save an export in the local reports folder when the picker fails.

        Desktop/mobile file pickers are platform services and can be missing,
        blocked by the OS or fail mid-save. Keeping a copy in the app's own
        reports folder means a finished export is never lost silently.
        """
        destination: Path | None = None
        try:
            folder = actions.runtime().settings.reports_dir
            folder.mkdir(parents=True, exist_ok=True)
            # Use exclusive creation to avoid replacing a previous export if
            # two requests have the same timestamp or arrive concurrently.
            for _attempt in range(10_001):
                candidate = self._unique_export_path(folder, file_name)
                try:
                    output = candidate.open("xb")
                except FileExistsError:
                    continue
                try:
                    with output:
                        if payload is not None:
                            output.write(payload)
                        elif source_path is not None:
                            with source_path.open("rb") as source:
                                shutil.copyfileobj(source, output)
                        else:
                            raise OSError("No export data was available for fallback")
                    destination = candidate
                    break
                except BaseException:
                    # A short write or copy failure must not leave a corrupt
                    # file that looks like a completed fallback export.
                    try:
                        candidate.unlink()
                    except OSError:
                        _LOGGER.exception("could not remove a partial export fallback")
                    raise
            if destination is None:
                raise OSError("Could not reserve a unique export path")
        except Exception as exc:
            _LOGGER.exception("could not save the export fallback")
            detail = f"{type(exc).__name__}: {exc}"
            if cause is not None:
                detail += f"\n{type(cause).__name__}: {cause}"
            self._finish_export(
                actions.ActionResult(failure_title, detail, ok=False), on_result
            )
            return

        note = recovery_note or t(
            "The save dialog failed, so the file was kept in the app's "
            "reports folder."
        )
        completed = actions.ActionResult(result.title, f"{destination}\n{note}", ok=result.ok)
        self._finish_export(completed, on_result)

    def _export_to_file(
        self,
        action: Callable[[Path], actions.ActionResult],
        extension: str,
        *,
        file_stem: str = "blockprobe",
        failure_title: str | None = None,
        on_result: Callable[[actions.ActionResult], None] | None = None,
    ) -> Callable[[Any], Awaitable[None]]:
        """Run an export and let the platform save dialog choose its destination."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        file_name = f"{file_stem}-{timestamp}.{extension}"
        failed_title = failure_title or t("Report not written")

        async def on_click(_event: Any) -> None:
            result: actions.ActionResult | None = None
            payload = b""
            source: Path | None = None
            try:
                # The native picker needs bytes, so the report is rendered to a
                # short-lived private folder first and removed afterwards.
                with tempfile.TemporaryDirectory(
                    prefix="blockprobe-export-", ignore_cleanup_errors=True
                ) as folder:
                    source = Path(folder) / file_name
                    try:
                        result = action(source)
                        if not result.ok:
                            if source.is_file():
                                self._save_export_fallback(
                                    file_name,
                                    None,
                                    result,
                                    on_result,
                                    failure_title=failed_title,
                                    source_path=source,
                                    recovery_note=t(
                                        "The export action reported a failure; a copy of its "
                                        "output was kept for inspection."
                                    ),
                                )
                            else:
                                self._finish_export(result, on_result)
                            return
                        payload = source.read_bytes()
                    except Exception as exc:
                        _LOGGER.exception("could not prepare the export file")
                        try:
                            has_output = source.is_file()
                        except Exception:
                            _LOGGER.exception("could not inspect the temporary export")
                            has_output = False
                        if has_output:
                            interrupted = actions.ActionResult(
                                result.title if result is not None else failed_title,
                                f"{type(exc).__name__}: {exc}",
                                ok=False,
                            )
                            self._save_export_fallback(
                                file_name,
                                None,
                                interrupted,
                                on_result,
                                failure_title=failed_title,
                                cause=exc,
                                source_path=source,
                                recovery_note=t(
                                    "The export was recovered to the reports folder after a "
                                    "preparation error."
                                ),
                            )
                        else:
                            failed = actions.ActionResult(
                                failed_title, f"{type(exc).__name__}: {exc}", ok=False
                            )
                            self._finish_export(failed, on_result)
                        return
            except Exception as exc:
                _LOGGER.exception("could not create the temporary export file")
                failed = actions.ActionResult(
                    failed_title, f"{type(exc).__name__}: {exc}", ok=False
                )
                self._finish_export(failed, on_result)
                return

            if result is None:  # defensive: action() must return a result
                _LOGGER.error("export action returned no result")
                return
            saved_path: str | None = None
            picker_error: BaseException | None = None
            try:
                saved_path = await ft.FilePicker().save_file(
                    dialog_title=t("Export file"),
                    file_name=file_name,
                    file_type=ft.FilePickerFileType.CUSTOM,
                    allowed_extensions=[extension],
                    src_bytes=payload,
                )
            except Exception as exc:
                # Missing plugin, unsupported platform, permission denied or a
                # picker crash: keep the export in the app's reports folder.
                _LOGGER.exception("could not save the export through the platform picker")
                picker_error = exc

            if picker_error is not None:
                self._save_export_fallback(
                    file_name,
                    payload,
                    result,
                    on_result,
                    failure_title=failed_title,
                    cause=picker_error,
                )
                return

            # Web saves trigger a browser download and do not return a path.
            # A missing native path means the user cancelled the dialog.
            if not saved_path and not self.page.web:
                cancelled = actions.ActionResult(t("Export cancelled"), "")
                if on_result is not None:
                    try:
                        on_result(cancelled)
                    except Exception:
                        _LOGGER.exception("could not update the export status label")
                try:
                    self.page.update()
                except Exception:
                    _LOGGER.exception("could not update the page after export cancellation")
                return
            if not saved_path or saved_path == file_name:
                destination_detail = file_name
            elif saved_path.startswith(("/document/", "content://")):
                # Android's Storage Access Framework returns an opaque document
                # id rather than a user-visible path. The filename and the
                # selected location are the useful information there.
                destination_detail = (
                    f"{file_name}\n{t('Saved to the selected location.')}"
                )
            else:
                destination_detail = f"{file_name}\n{saved_path}"
            completed = actions.ActionResult(result.title, destination_detail)
            self._finish_export(completed, on_result)

        return on_click

    def _on_nav(self, _event: Any) -> None:
        index = int(self.nav.selected_index or 0)
        self._show_screen(index)

    def _on_mobile_nav(self, _event: Any) -> None:
        index = int(self.mobile_nav.selected_index or 0)
        self._show_screen(6 if index == 4 else index)

    def _show_screen(self, index: int) -> None:
        self.current_screen = index
        if index == 0:
            self._build_source_checkboxes()
        self.nav.selected_index = index if 0 <= index < len(self.views) else 0
        self.mobile_nav.selected_index = index if 0 <= index < 4 else 4
        content = self.more_view if index == 6 else self.views[index]
        self.content_area.content = ft.Column(
            [self.page_heading, content],
            spacing=20,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            expand=True,
        )
        labels = [t("Run"), t("Sources"), t("Results"), t("Logs"), t("Settings"), t("Help")]
        descriptions = [
            t("Check how this device reaches the selected websites."),
            t("Choose regulator sources and maintain your own list."),
            t("Review verdicts, evidence and network health."),
            t("Inspect recent local activity and export a diagnostic log."),
            t("Adjust scan behavior and local data controls."),
            t("Read about scope, privacy and responsible use."),
            t("More tools and information."),
        ]
        self.screen_title.value = labels[index] if index < 6 else t("More")
        self.screen_description.value = descriptions[index]
        if index == 1:
            self.refresh_portal_texts()
            self._reload_custom()
        if index == 2:
            self.refresh_results()
        if index == 3:
            self.refresh_logs()
        self._apply_live_theme()
        self.page.update()

    def _build_theme_menu(self) -> ft.PopupMenuButton:
        """Expose the appearance choice on every tool, not only in Settings."""
        mode = actions.runtime().settings.config.app.theme_mode
        self._theme_menu_items.clear()
        choices = (
            ("system", t("System default"), ft.Icons.BRIGHTNESS_AUTO),
            ("light", t("Light"), ft.Icons.LIGHT_MODE),
            ("dark", t("Dark"), ft.Icons.DARK_MODE),
        )
        for value, label, icon in choices:
            item = ft.PopupMenuItem(
                content=label,
                icon=icon,
                checked=value == mode,
                on_click=lambda _event, choice=value: self._set_theme(choice),
            )
            self._theme_menu_items[value] = item
        self.theme_menu = ft.PopupMenuButton(
            icon=ft.Icons.PALETTE_OUTLINED,
            icon_color=TEXT,
            icon_size=22,
            tooltip=t("Appearance"),
            menu_position=ft.PopupMenuPosition.UNDER,
            items=list(self._theme_menu_items.values()),
        )
        return self.theme_menu

    def _more_view(self) -> ft.Control:
        from blockprobe.ui.components import button, card, hero

        self.more_theme_dropdown = ft.Dropdown(
            label=t("Appearance"),
            value=actions.runtime().settings.config.app.theme_mode,
            dense=True,
            options=[
                ft.DropdownOption(key="system", text=t("System default")),
                ft.DropdownOption(key="light", text=t("Light")),
                ft.DropdownOption(key="dark", text=t("Dark")),
            ],
            on_select=self._change_theme,
        )
        return ft.Column(
            [
                hero(
                    t("More tools"),
                    t("Open settings or read the privacy and responsible-use notes."),
                    icon=ft.Icons.MORE_HORIZ,
                ),
                card(t("Appearance"), self.more_theme_dropdown),
                card(
                    t("Settings and help"),
                    ft.Row(
                        [
                            button(
                                t("Settings"),
                                ft.Icons.SETTINGS,
                                lambda _e: self._show_screen(4),
                            ),
                            button(
                                t("Help"),
                                ft.Icons.HELP_OUTLINE,
                                lambda _e: self._show_screen(5),
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                ),
                ft.Container(height=16),
            ],
            spacing=16,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    # -------------------------------------------------------------------- views

    def build(self) -> ft.Control:
        self.views = [
            self._run_view(),
            self._sources_view(),
            self._results_view(),
            self._logs_view(),
            self._settings_view(),
            help_view(),
        ]
        self.more_view = self._more_view()
        self.content_area.content = ft.Column(
            [self.page_heading, self.views[0]],
            spacing=20,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            expand=True,
        )
        self.refresh_portal_texts()
        self.refresh_results()
        self.refresh_logs()
        self.version_badge = ft.Container(
            content=text(f"v{__version__}", 11, MUTED, mono=True),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            bgcolor=SURFACE,
            border_radius=8,
        )
        header = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Image(
                            src="icon.png",
                            width=48,
                            height=48,
                            fit=ft.BoxFit.CONTAIN,
                            semantics_label=APP_NAME,
                        ),
                        width=48,
                        height=48,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        [
                            text(APP_NAME, 16, TEXT, weight=ft.FontWeight.BOLD),
                            text(t("On-device network check"), 11, MUTED),
                        ],
                        spacing=1,
                        expand=True,
                    ),
                    self._build_theme_menu(),
                    self.version_badge,
                ],
                spacing=8,
            ),
            padding=ft.Padding.symmetric(horizontal=22, vertical=12),
            border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
        )
        body = ft.Row(
            [
                self.nav,
                self.nav_divider,
                self.content_area,
            ],
            expand=True,
            spacing=0,
        )
        self.status_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Container(width=8, height=8, bgcolor=OK, border_radius=4),
                    self.status_title,
                    self.status_detail,
                ],
                spacing=10,
                wrap=True,
            ),
            padding=ft.Padding.symmetric(horizontal=22, vertical=11),
            bgcolor=BACKGROUND,
            border=ft.Border.only(top=ft.BorderSide(1, BORDER)),
        )
        root = ft.Column(
            [
                header,
                body,
                self.status_bar,
                self.mobile_nav,
            ],
            expand=True,
            spacing=0,
        )
        self._sync_responsive_layout()
        return root

    def _on_resize(self, _event: Any) -> None:
        self._sync_responsive_layout()
        try:
            self.page.update()
        except Exception:
            pass

    def _sync_responsive_layout(self) -> None:
        width = getattr(self.page, "width", None) or 1024
        compact = width < 760
        self._compact_layout = compact
        self.nav.visible = not compact
        self.nav_divider.visible = not compact
        self.mobile_nav.visible = compact
        self.content_area.padding = 12 if compact else 20
        self.screen_title.size = 24 if compact else 28
        self.screen_title.visible = not (compact and self.current_screen == 0)
        self.screen_description.visible = not compact
        self.status_bar.visible = not compact
        self.version_badge.visible = not compact
        self.status_title.size = 15 if compact else 20
        self.refresh_run_layout(compact)
        self.refresh_results_layout(compact)
        self.refresh_settings_layout(compact)

    def _apply_live_theme(self) -> None:
        """Reapply the selected palette after a screen replaces its controls."""
        mode = actions.runtime().settings.config.app.theme_mode
        apply_page_theme(self.page, mode, update=False)

    # -------------------------------------------------------------------- polls

    def start_poller(self) -> None:
        """Follow the stored run from a daemon thread and update the screens."""
        if self._poller_started:
            return
        self._poller_started = True

        def loop() -> None:
            while self._alive:
                time.sleep(1.5)
                try:
                    summary = actions.latest_run_summary()
                    active_run = actions.runtime().scan.active_run
                    active = active_run.id if active_run is not None else None
                    changed = self._render_summary(summary)
                    changed = self.refresh_run_controls(
                        summary,
                        active=active_run is not None,
                    ) or changed
                    if (
                        active is not None
                        and summary is not None
                        and self.current_screen == 2
                    ):
                        checked = summary.blocked + summary.unreachable + summary.opened
                        key = (
                            active,
                            checked,
                            summary.blocked,
                            summary.unreachable,
                            summary.opened,
                            summary.needs_review,
                            summary.status.value,
                        )
                        query_active = bool((self.results_search.value or "").strip())
                        filters_active = (
                            bool(self.leaks_only_switch.value)
                            or str(self.verdict_filter.value or "all") != "all"
                        )
                        now = time.monotonic()
                        if (
                            key != self._results_key
                            and not query_active
                            and not filters_active
                            and not self._results_search_dirty
                            and self._result_page_index == 0
                            and now - self._last_results_poll >= 3.0
                        ):
                            self.refresh_results(update_page=False)
                            self._last_results_poll = now
                            self._apply_live_theme()
                            changed = True
                    if changed:
                        self.page.update()
                except Exception:
                    _LOGGER.exception("UI poll failed")
                    continue

        thread = threading.Thread(target=loop, name="blockprobe-poller", daemon=True)
        thread.start()

    def rebuild_ui(self, result: actions.ActionResult | None = None) -> None:
        """Rebuild every screen; used after a language change.

        The old poller stops on its next tick, a fresh shell renders with the
        new language, and an optional result message is shown in its status bar.
        """
        self._alive = False
        page = self.page
        page.clean()
        mode = actions.runtime().settings.config.app.theme_mode
        _apply_saved_theme(page, update=False)
        shell = _main_shell(page)
        dark = dark_for_mode(page, mode)
        apply_palette(shell, dark=dark)
        page.add(ft.SafeArea(content=shell, expand=True))
        _apply_saved_theme(page, update=False)
        page.update()
        new_ui = getattr(page, "data", None)
        if isinstance(new_ui, BlockProbeUi):
            if result is not None:
                new_ui.apply(result)
            new_ui.start_poller()

    def _render_summary(self, summary: RunSummary | None) -> bool:
        # A previously stored run must not replace the result of a failed new
        # Start request. Keep the current error until the user tries again.
        if self._run_start_failed:
            return False
        signature: tuple[object, ...] = (
            (
                summary.run_id,
                summary.status.value,
                summary.total,
                summary.started_at,
                summary.finished_at,
                summary.blocked,
                summary.unreachable,
                summary.opened,
                summary.needs_review,
            )
            if summary is not None
            else (None,)
        )
        if signature == self._last_poll_signature:
            return False
        self._last_poll_signature = signature
        if summary is None:
            self.summary_text.value = t("No run stored yet.")
            if not self._run_feedback_visible:
                self.progress_text.value = t("No scan is running.")
                self.progress_bar.value = 0
            return True
        self.summary_text.value = t(
            "{status}: {total} websites — {opened} opened, {blocked} blocked, "
            "{unreachable} unreachable, {needs_review} need review.",
            status=t(summary.status.value.title()),
            total=summary.total,
            opened=summary.opened,
            blocked=summary.blocked,
            unreachable=summary.unreachable,
            needs_review=summary.needs_review,
        )
        if summary.status.value in {"pending", "running"}:
            self.progress_text.value = t(
                "Checking {count} websites…", count=summary.total
            )
            self.progress_bar.value = None
        elif summary.status.value == "paused":
            self.progress_text.value = t(
                "Paused. Resume when the network is available."
            )
            self.progress_bar.value = 0
        else:
            self.progress_text.value = t("Run {status}.", status=t(summary.status.value.title()))
            self.progress_bar.value = 1
        return True


def build_app(page: ft.Page) -> ft.Control:
    """Build the consent flow or the main shell for the given page."""
    try:
        if actions.consent_required():
            return consent_view(page, lambda: _enter_main(page))
    except Exception as exc:
        _LOGGER.exception("could not check the consent record during startup")
        return _consent_error_view(page, exc)
    return _main_shell(page)


def _consent_error_view(page: ft.Page, error: BaseException) -> ft.Control:
    """Show a retryable storage error instead of presenting false consent."""

    def retry(_event: Any) -> None:
        page.clean()
        main(page)

    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.ERROR_OUTLINE, size=36, color=BAD),
                text(t("Could not check the consent record"), 20, TEXT, weight=ft.FontWeight.BOLD),
                text(
                    t("Check Logs and retry. No network action can run until the consent record is checked."),
                    14,
                    MUTED,
                    selectable=True,
                ),
                text(f"{type(error).__name__}: {error}", 12, MUTED, selectable=True),
                button(t("Retry"), ft.Icons.REFRESH, retry, primary=True),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=14,
        ),
        expand=True,
        alignment=ft.Alignment.CENTER,
        padding=24,
    )


def _enter_main(page: ft.Page) -> None:
    shell = _main_shell(page)
    page.add(ft.SafeArea(content=shell, expand=True))
    _apply_saved_theme(page)
    page.update()
    ui = getattr(page, "data", None)
    if isinstance(ui, BlockProbeUi):
        ui.start_poller()


def _main_shell(page: ft.Page) -> ft.Control:
    ui = BlockProbeUi(page)
    page.data = ui  # keep a reference so the poller keeps updating this session
    return ui.build()


def main(page: ft.Page) -> None:
    """Build the BlockProbe window and connect its controls to actions."""
    page.title = f"{APP_NAME} {__version__}"
    # Apply the saved palette before Flet paints the consent or app screen.
    page.on_platform_brightness_change = lambda _event: _apply_saved_theme(
        page, update=True
    )
    page.padding = 0
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    try:
        _apply_saved_theme(page, update=False)
        root = build_app(page)
        page.add(ft.SafeArea(content=root, expand=True))
        _apply_saved_theme(page)
        page.update()
        ui = getattr(page, "data", None)
        if isinstance(ui, BlockProbeUi):
            ui.start_poller()
    except Exception as exc:
        _LOGGER.exception("application startup could not read local settings")
        try:
            page.clean()
            page.add(ft.SafeArea(content=_consent_error_view(page, exc), expand=True))
            page.update()
        except Exception:
            _LOGGER.exception("could not display the local storage error")


def _apply_saved_theme(page: ft.Page, *, update: bool = False) -> None:
    mode = actions.runtime().settings.config.app.theme_mode
    apply_page_theme(page, mode, update=update)
