# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared Flet widgets, colors and helpers used by every screen."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

ACCENT = "#76D6C1"
BACKGROUND = "#0B1115"
SURFACE = "#111A1F"
CARD = "#141F25"
BORDER = "#26363D"
TEXT = "#EDF4F3"
MUTED = "#9AAEB0"
OK = "#79D6A6"
WARN = "#E8BC75"
BAD = "#F08C8C"
ACCENT_DIM = "#1B3937"


def text(
    value: str,
    size: int = 13,
    color: str = MUTED,
    *,
    weight: ft.FontWeight | None = None,
    selectable: bool = False,
    mono: bool = False,
) -> ft.Text:
    return ft.Text(
        value,
        size=size,
        color=color,
        weight=weight,
        selectable=selectable,
        font_family="monospace" if mono else None,
    )


def card(title: str, body: ft.Control, *, expand: bool = False) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            [text(title, 12, MUTED, weight=ft.FontWeight.W_600), body],
            spacing=14,
        ),
        padding=ft.Padding.symmetric(horizontal=20, vertical=18),
        bgcolor=CARD,
        border=ft.Border.all(1, BORDER),
        border_radius=14,
        expand=expand,
    )


def hero(title: str, description: str, *, icon: ft.IconData) -> ft.Container:
    """Build a calm page introduction with one strong brand accent."""
    return ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    content=ft.Icon(icon, color=ACCENT, size=24),
                    width=48,
                    height=48,
                    alignment=ft.Alignment.CENTER,
                    bgcolor=ACCENT_DIM,
                    border_radius=13,
                ),
                ft.Column(
                    [
                        text(title, 24, TEXT, weight=ft.FontWeight.BOLD),
                        text(description, 13, MUTED),
                    ],
                    spacing=4,
                    expand=True,
                ),
            ],
            spacing=14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=4, vertical=6),
    )


def button(
    label: str,
    icon: ft.IconData,
    on_click: Callable[[Any], None],
    *,
    primary: bool = False,
) -> ft.Button:
    return ft.Button(
        content=label,
        icon=icon,
        on_click=on_click,
        bgcolor=ACCENT if primary else SURFACE,
        color=BACKGROUND if primary else TEXT,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            animation_duration=180,
        ),
    )
