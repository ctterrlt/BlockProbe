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
            label=t("Search"), dense=True, on_submit=lambda _e: self.refresh_logs()
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
                            ft.ResponsiveRow(
                                [
                                    ft.Container(content=self.log_level, col={"xs": 12, "sm": 4, "md": 3}),
                                    ft.Container(content=self.log_search, col={"xs": 12, "sm": 8, "md": 3}),
                                    ft.Container(
                                        content=button(
                                            t("Apply"),
                                            ft.Icons.FILTER_ALT,
                                            self._wrap(self.refresh_logs),
                                        ),
                                        col={"xs": 12, "sm": 4, "md": 2},
                                    ),
                                    ft.Container(
                                        content=button(
                                            t("Export zip"),
                                            ft.Icons.DOWNLOAD,
                                            self._wrap(actions.export_logs),
                                        ),
                                        col={"xs": 12, "sm": 4, "md": 2},
                                    ),
                                    ft.Container(
                                        content=button(
                                            t("Clear view"),
                                            ft.Icons.DELETE_SWEEP,
                                            self._wrap(self._clear_logs),
                                        ),
                                        col={"xs": 12, "sm": 4, "md": 2},
                                    ),
                                ],
                                run_spacing=8,
                            ),
                            ft.Container(
                                content=self.log_view,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                    ),
                    expand=True,
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
            event = str(entry.get("event") or "")
            level_name = str(entry.get("level") or "INFO")
            message_key = {
                "runtime_ready": "Application ready",
                "run_started": "Scan started",
                "run_finished": "Scan completed",
                "progress": "Scan progress updated",
                "network_guard": "Connection check failed; scan results may be incomplete.",
                "network_control": "Connection check failed; scan results may be incomplete.",
                "initial_control_failed": "Connection check failed; scan results may be incomplete.",
                "portal_saved": "Portal definition saved",
                "portal_skip": "Portal definition skipped",
            }.get(event)
            if message_key is None:
                message_key = (
                    "An error was recorded. Export diagnostics for technical details."
                    if level_name == "ERROR"
                    else "Activity recorded"
                )
            color = BAD if level_name == "ERROR" else MUTED
            line = f"{entry.get('timestamp', '')} · {t(level_name.title())} · {t(message_key)}"
            self.log_view.controls.append(
                ft.Container(
                    content=text(line, 12, color),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    bgcolor="#FFFFFF",
                    border_radius=8,
                )
            )
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
