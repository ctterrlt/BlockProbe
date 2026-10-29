# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flet application shell: page setup, navigation and control wiring.

Screens live in dedicated modules (``consent``, ``run``, ``portals``,
``results``, ``logs``, ``settings``, ``help``). This module composes them,
routes every button through :mod:`blockprobe.ui.actions`, and follows the
active run from the database so progress survives restarts. No probing or
storage logic lives here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import flet as ft

from blockprobe import APP_NAME, __version__
from blockprobe.core.models import RunSummary
from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import (
    ACCENT,
    ACCENT_DIM,
    BACKGROUND,
    BAD,
    BORDER,
    CARD,
    MUTED,
    OK,
    SURFACE,
    TEXT,
    text,
)
from blockprobe.ui.consent import consent_view
from blockprobe.ui.help import help_view
from blockprobe.ui.logs import LogsScreen
from blockprobe.ui.portals import SourcesScreen
from blockprobe.ui.results import ResultsScreen
from blockprobe.ui.run import RunScreen
from blockprobe.ui.settings import SettingsScreen


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
                *(ft.NavigationBarDestination(icon=icon, label=label) for icon, label in destinations[:4]),
                ft.NavigationBarDestination(icon=ft.Icons.MORE_HORIZ, label=t("More")),
            ],
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
            bgcolor=CARD,
            indicator_color=ACCENT_DIM,
            elevation=0,
            on_change=self._on_mobile_nav,
        )
        self.content_area = ft.Container(expand=True, padding=28)
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
        self.snack = ft.SnackBar(content=text("", 13, TEXT))
        self.views: list[ft.Control] = []
        self.current_screen = 0
        self._compact_layout: bool | None = None
        self.page.on_resize = self._on_resize

        self._setup_run()
        self._setup_sources()
        self._setup_results()
        self._setup_logs()
        self._setup_settings()

    # ------------------------------------------------------------------ helpers

    def apply(self, result: actions.ActionResult) -> None:
        self.status_title.value = result.title
        self.status_detail.value = result.detail.splitlines()[0] if result.detail else ""
        self.snack.content = text(f"{result.title}: {self.status_detail.value}", 13, TEXT)
        self.snack.bgcolor = OK if result.ok else BAD
        try:
            self.page.show_dialog(self.snack)
        except Exception:
            pass
        try:
            self.page.update()
        except Exception:
            pass

    def run_async(self, action: Callable[[], actions.ActionResult]) -> None:
        def work() -> None:
            self.apply(action())

        self.page.run_thread(work)

    def _wrap(self, action: Callable[[], actions.ActionResult]) -> Callable[[Any], None]:
        def on_click(_event: Any) -> None:
            self.run_async(action)

        return on_click

    def _on_nav(self, event: ft.ControlEvent) -> None:
        index = int(event.control.selected_index or 0)
        self._show_screen(index)

    def _on_mobile_nav(self, event: ft.ControlEvent) -> None:
        index = int(event.control.selected_index or 0)
        self._show_screen(6 if index == 4 else index)

    def _show_screen(self, index: int) -> None:
        self.current_screen = index
        self.nav.selected_index = index if 0 <= index < len(self.views) else 0
        self.mobile_nav.selected_index = index if 0 <= index < 4 else 4
        content = self.more_view if index == 6 else self.views[index]
        self.content_area.content = ft.Column(
            [self.page_heading, content], spacing=20, expand=True
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
        if index == 2:
            self.refresh_results()
        if index == 3:
            self.refresh_logs()
        if index == 4:
            self.refresh_portal_texts()
        self.page.update()

    def _more_view(self) -> ft.Control:
        from blockprobe.ui.components import button, card, hero

        return ft.Column(
            [
                hero(
                    t("More tools"),
                    t("Open settings or read the privacy and responsible-use notes."),
                    icon=ft.Icons.MORE_HORIZ,
                ),
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
            ],
            spacing=16,
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
            expand=True,
        )
        self.refresh_portal_texts()
        self.refresh_results()
        self.refresh_logs()
        header = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=text("BP", 16, BACKGROUND, weight=ft.FontWeight.BOLD),
                        width=40,
                        height=40,
                        alignment=ft.Alignment.CENTER,
                        bgcolor=ACCENT,
                        border_radius=12,
                    ),
                    ft.Column(
                        [
                            text(APP_NAME, 16, TEXT, weight=ft.FontWeight.BOLD),
                            text(t("On-device network check"), 11, MUTED),
                        ],
                        spacing=1,
                        expand=True,
                    ),
                    ft.Container(
                        content=text(f"v{__version__}", 11, MUTED, mono=True),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                        bgcolor=SURFACE,
                        border_radius=8,
                    ),
                ],
                spacing=12,
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

    def _on_resize(self, _event: ft.ControlEvent) -> None:
        self._sync_responsive_layout()
        try:
            self.page.update()
        except Exception:
            pass

    def _sync_responsive_layout(self) -> None:
        width = getattr(self.page, "width", None) or 1024
        compact = width < 760
        if compact == self._compact_layout and self.views:
            return
        self._compact_layout = compact
        self.nav.visible = not compact
        self.nav_divider.visible = not compact
        self.mobile_nav.visible = compact
        self.content_area.padding = 16 if compact else 28
        self.screen_title.size = 22 if compact else 28
        self.screen_description.visible = not compact
        self.status_title.size = 15 if compact else 20
        if hasattr(self, "control_url_field"):
            self.control_url_field.width = min(340, max(220, width - 64)) if compact else 340

    # -------------------------------------------------------------------- polls

    def start_poller(self) -> None:
        """Follow the stored run from a daemon thread and update the screens."""
        if self._poller_started:
            return
        self._poller_started = True

        def loop() -> None:
            while self._alive:
                time.sleep(1.0)
                try:
                    summary = actions.latest_run_summary()
                    active = actions.active_run_id()
                    self._render_summary(summary)
                    if (
                        active is not None
                        and summary is not None
                        and self.current_screen == 2
                    ):
                        key = (active, summary.total)
                        if key != self._results_key:
                            self.refresh_results()
                    self.page.update()
                except Exception:
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
        page.add(ft.SafeArea(content=_main_shell(page), expand=True))
        page.update()
        new_ui = getattr(page, "data", None)
        if isinstance(new_ui, BlockProbeUi):
            if result is not None:
                new_ui.status_title.value = result.title
                new_ui.status_detail.value = (
                    result.detail.splitlines()[0] if result.detail else ""
                )
            new_ui.start_poller()

    def _render_summary(self, summary: RunSummary | None) -> None:
        if summary is None:
            self.summary_text.value = t("No run stored yet.")
            self.progress_text.value = t("No scan is running.")
            self.progress_bar.value = 0
            return
        self.summary_text.value = t(
            "{status}: {total} websites — {opened} opened, {blocked} blocked, "
            "{unreachable} unreachable, {needs_review} need review.",
            status=summary.status.value,
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
            self.progress_text.value = t("Run {status}.", status=summary.status.value)
            self.progress_bar.value = 1


def build_app(page: ft.Page) -> ft.Control:
    """Build the consent flow or the main shell for the given page."""
    if actions.consent_required():
        return consent_view(page, lambda: _enter_main(page))
    return _main_shell(page)


def _enter_main(page: ft.Page) -> None:
    shell = _main_shell(page)
    page.add(ft.SafeArea(content=shell, expand=True))
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
    page.theme = ft.Theme(color_scheme_seed=ACCENT, use_material3=True)
    page.dark_theme = ft.Theme(color_scheme_seed=ACCENT, use_material3=True)
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BACKGROUND
    page.padding = 0
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.add(ft.SafeArea(content=build_app(page), expand=True))
    page.update()
    ui = getattr(page, "data", None)
    if isinstance(ui, BlockProbeUi):
        ui.start_poller()
