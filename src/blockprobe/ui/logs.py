# SPDX-License-Identifier: AGPL-3.0-or-later
"""Logs screen: ring buffer view, level filter and zip export (FR-11)."""

from __future__ import annotations

import flet as ft

from blockprobe.i18n import t
from blockprobe.logging_setup import clear_logs, get_logs
from blockprobe.ui import actions
from blockprobe.ui.components import BAD, MUTED, button, card, hero, text


class LogsScreen:
    """Mixin with the logs screen."""

    page: ft.Page
    log_level: ft.Dropdown
    log_search: ft.TextField
    log_view: ft.ListView

    def _wrap(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def _setup_logs(self) -> None:
        self.log_level = ft.Dropdown(
            label=t("Level"),
            value="ALL",
            width=150,
            dense=True,
            options=[
                ft.DropdownOption(key="ALL", text=t("All levels")),
                ft.DropdownOption(key="DEBUG", text=t("Debug")),
                ft.DropdownOption(key="INFO", text=t("Info")),
                ft.DropdownOption(key="WARNING", text=t("Warning")),
                ft.DropdownOption(key="ERROR", text=t("Error")),
            ],
            on_select=lambda _e: self.refresh_logs(),
        )
        self.log_search = ft.TextField(
            label=t("Search"), dense=True, width=220, on_submit=lambda _e: self.refresh_logs()
        )
        self.log_view = ft.ListView(spacing=2, auto_scroll=True, expand=True)

    def _logs_view(self) -> ft.Control:
        return ft.Column(
            [
                hero(
                    t("Recent activity"),
                    t("Logs stay on this device and can be exported for troubleshooting."),
                    icon=ft.Icons.TERMINAL,
                ),
                card(
                    t("Logs"),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    self.log_level,
                                    self.log_search,
                                    button(
                                        t("Apply"),
                                        ft.Icons.FILTER_ALT,
                                        self._wrap(self.refresh_logs),
                                    ),
                                    button(
                                        t("Export zip"),
                                        ft.Icons.DOWNLOAD,
                                        self._wrap(actions.export_logs),
                                    ),
                                    button(
                                        t("Clear view"),
                                        ft.Icons.DELETE_SWEEP,
                                        self._wrap(self._clear_logs),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                            ft.Container(
                                content=self.log_view,
                                expand=True,
                                height=360,
                            ),
                        ],
                        spacing=10,
                    ),
                ),
            ],
            spacing=16,
            expand=True,
        )

    def refresh_logs(self) -> actions.ActionResult:
        level = None if self.log_level.value in {None, "ALL"} else str(self.log_level.value)
        search = self.log_search.value or None
        entries = get_logs(level=level, search=search, limit=300)
        self.log_view.controls = []
        for entry in entries:
            line = (
                f"{entry.get('timestamp', '')} [{entry.get('level', '')}] "
                f"{entry.get('logger', '')}: {entry.get('message', '')}"
            )
            if entry.get("event"):
                line += f"  event={entry['event']}"
            color = BAD if entry.get("level") == "ERROR" else MUTED
            self.log_view.controls.append(text(line, 11, color, mono=True))
        try:
            self.page.update()
        except Exception:
            pass
        return actions.ActionResult(
            t("Logs refreshed"), t("{count} entry(ies).", count=len(entries))
        )

    def _clear_logs(self) -> actions.ActionResult:
        clear_logs()
        self.log_view.controls = []
        try:
            self.page.update()
        except Exception:
            pass
        return actions.ActionResult(
            t("Cleared"), t("The in-memory log view was emptied.")
        )
