# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared Flet widgets, colors and helpers used by every screen."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

ACCENT = "#176B5B"
BACKGROUND = "#F4F7F6"
SURFACE = "#FFFFFF"
CARD = "#FFFFFF"
BORDER = "#DCE5E2"
TEXT = "#172522"
MUTED = "#53635F"
OK = "#23754F"
WARN = "#8A5B0A"
BAD = "#A33B36"
ACCENT_DIM = "#DCEDE8"

_DARK_PALETTE = {
    ACCENT: "#67B8A7",
    BACKGROUND: "#111816",
    SURFACE: "#202A27",
    BORDER: "#374640",
    TEXT: "#E6EFEB",
    MUTED: "#A8B6B0",
    OK: "#61B58A",
    WARN: "#D8AD62",
    BAD: "#D77C74",
    ACCENT_DIM: "#29463E",
}
_LIGHT_PALETTE = {dark: light for light, dark in _DARK_PALETTE.items()}


def apply_palette(controls: object, *, dark: bool) -> None:
    """Apply semantic light/dark colors to an existing Flet control tree."""
    colors = _DARK_PALETTE if dark else _LIGHT_PALETTE
    seen: set[int] = set()

    def visit(value: object) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, str):
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
            return
        if isinstance(value, ft.BorderSide):
            if value.color in colors:
                value.color = colors[value.color]
            return
        if isinstance(value, ft.Border):
            for side in (value.top, value.right, value.bottom, value.left):
                visit(side)
            return
        if isinstance(value, ft.Control):
            for name in ("color", "bgcolor", "indicator_color", "icon_color"):
                current = getattr(value, name, None)
                if isinstance(current, str) and current in colors:
                    setattr(value, name, colors[current])
            border = getattr(value, "border", None)
            if isinstance(border, ft.Border):
                visit(border)
            for child in vars(value).values():
                if isinstance(child, (ft.Control, list, tuple, dict)):
                    visit(child)

    visit(controls)


def palette_background(*, dark: bool) -> str:
    return _DARK_PALETTE[BACKGROUND] if dark else BACKGROUND


def apply_page_theme(page: ft.Page, mode: str, *, update: bool = True) -> None:
    """Follow the OS appearance in system mode and recolor explicit controls."""
    page.theme = ft.Theme(color_scheme_seed=ACCENT, use_material3=True)
    page.dark_theme = ft.Theme(
        color_scheme_seed=ACCENT,
        use_material3=True,
    )
    page.theme_mode = {
        "system": ft.ThemeMode.SYSTEM,
        "light": ft.ThemeMode.LIGHT,
        "dark": ft.ThemeMode.DARK,
    }[mode]
    platform_brightness = getattr(page, "platform_brightness", None)
    dark = mode == "dark" or (
        mode == "system" and platform_brightness == ft.Brightness.DARK
    )
    apply_palette(
        [page.controls, getattr(page, "overlay", []), getattr(page, "dialog", None)],
        dark=dark,
    )
    page.bgcolor = palette_background(dark=dark)
    if update:
        page.update()


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
        padding=ft.Padding.symmetric(horizontal=16, vertical=14),
        bgcolor=CARD,
        border=ft.Border.all(1, BORDER),
        border_radius=12,
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
                        text(title, 22, TEXT, weight=ft.FontWeight.BOLD),
                        text(description, 14, MUTED),
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
