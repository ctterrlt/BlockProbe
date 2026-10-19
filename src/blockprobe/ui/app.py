# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flet application shell: page setup, theme and control wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from blockprobe import APP_NAME, __version__
from blockprobe.ui import actions

_ACCENT = "#38BDF8"
_BACKGROUND = "#0B1220"
_CARD = "#111827"
_BORDER = "#1F2937"
_TEXT = "#F8FAFC"
_MUTED = "#94A3B8"


def _card(title: str, body: ft.Control) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=_MUTED),
                body,
            ],
            spacing=10,
        ),
        padding=20,
        bgcolor=_CARD,
        border=ft.Border.all(1, _BORDER),
        border_radius=16,
    )


def _button(
    label: str,
    icon: ft.IconData,
    on_click: Callable[[Any], None],
    primary: bool = False,
) -> ft.Control:
    return ft.Button(
        content=label,
        icon=icon,
        on_click=on_click,
        bgcolor=_ACCENT if primary else "#1E293B",
        color=_BACKGROUND if primary else _TEXT,
    )


def main(page: ft.Page) -> None:
    """Build the BlockProbe window and connect its buttons to actions."""
    page.title = f"{APP_NAME} {__version__}"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = _BACKGROUND
    page.padding = 0
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER

    status_title = ft.Text("Ready", size=22, weight=ft.FontWeight.BOLD, color=_TEXT)
    status_detail = ft.Text(
        "Run a self-check to verify this installation. Portal collection, probing "
        "and reports are not implemented yet.",
        size=14,
        color=_MUTED,
        selectable=True,
    )

    def apply(result: actions.ActionResult) -> None:
        status_title.value = result.title
        status_detail.value = result.detail
        page.update()

    def handler(action: Callable[[], actions.ActionResult]) -> Callable[[Any], None]:
        def on_click(_event: Any) -> None:
            apply(action())

        return on_click

    content = ft.Container(
        content=ft.Column(
            [
                ft.Text("BlockProbe", size=34, weight=ft.FontWeight.BOLD, color=_TEXT),
                ft.Text(
                    "Local-first verification that gambling sites are still blocked "
                    "on this device.",
                    size=16,
                    color=_MUTED,
                ),
                _card(
                    "Status",
                    ft.Column([status_title, status_detail], spacing=6),
                ),
                ft.Row(
                    [
                        _button(
                            "Run self-check",
                            ft.Icons.CHECK_CIRCLE_OUTLINE,
                            handler(actions.run_self_check),
                            primary=True,
                        ),
                        _button(
                            "Refresh portal list",
                            ft.Icons.REFRESH,
                            handler(actions.refresh_portals),
                        ),
                        _button(
                            "Start scan",
                            ft.Icons.PLAY_ARROW,
                            handler(actions.start_scan),
                        ),
                        _button(
                            "Export report",
                            ft.Icons.DOWNLOAD,
                            handler(actions.export_report),
                        ),
                        _button(
                            "Open data folder",
                            ft.Icons.FOLDER_OPEN,
                            handler(actions.open_data_directory),
                        ),
                    ],
                    wrap=True,
                    spacing=12,
                    run_spacing=12,
                ),
                _card(
                    "Environment",
                    ft.Text(
                        actions.environment_summary(),
                        size=13,
                        color=_MUTED,
                        selectable=True,
                    ),
                ),
                ft.Text(
                    "Use only on devices you own or have explicit permission to test. "
                    "Results are indicative.",
                    size=12,
                    color="#64748B",
                ),
            ],
            spacing=20,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=32,
        expand=True,
    )

    page.add(ft.SafeArea(content=content, expand=True))
    page.update()
