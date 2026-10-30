# SPDX-License-Identifier: AGPL-3.0-or-later
"""First-run consent and privacy screens (FR-1).

The same texts are printed by ``blockprobe consent accept`` so the terminal and
the window ask for the same thing. Nothing here touches the network: accepting
only writes a local consent record through the action layer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from blockprobe import APP_NAME, __version__
from blockprobe.i18n import available_languages, t
from blockprobe.ui import actions
from blockprobe.ui.components import (
    BACKGROUND,
    MUTED,
    TEXT,
    apply_page_theme,
    apply_palette,
    button,
    prepare_dialog,
    text,
)


def _selected_language(event: Any, dropdown_value: object) -> str:
    """Read the changed value from the Flet event before stale local state."""
    languages = available_languages()
    event_value = getattr(event, "data", None)
    if event_value in languages:
        return str(event_value)
    control_value = getattr(getattr(event, "control", None), "value", None)
    if control_value in languages:
        return str(control_value)
    if dropdown_value in languages:
        return str(dropdown_value)
    return "en"


def consent_texts() -> tuple[tuple[str, str], ...]:
    """Return the first-run texts shared with the CLI, translated."""
    return actions.consent_texts()


def consent_view(page: ft.Page, on_accepted: Callable[[], None]) -> ft.Control:
    """Build the full-screen consent flow.

    ``on_accepted`` is called after the consent record is stored; the app shell
    installs itself from there so this module keeps no UI state.
    """
    blocks: list[ft.Control] = []
    for title, body in consent_texts():
        blocks.append(
            ft.Column(
                [
                    text(title, 15, TEXT, weight=ft.FontWeight.BOLD),
                    text(body, 13),
                ],
                spacing=4,
            )
        )

    language = ft.Dropdown(
        label=t("Language"),
        value=actions.runtime().settings.config.app.language,
        options=[
            ft.DropdownOption(key=code, text=name)
            for code, name in available_languages().items()
        ],
    )

    def change_language(_event: Any) -> None:
        # Flet updates the event control before it updates the Python-side
        # Dropdown.value on some desktop clients. Read the changed control
        # first; otherwise this callback saves the previous locale and the
        # new locale only appears when the user later agrees to the terms.
        selected = _selected_language(_event, language.value)
        result = actions.save_settings(language=selected)
        if not result.ok:
            return
        mode = actions.runtime().settings.config.app.theme_mode
        apply_page_theme(page, mode, update=False)
        page.clean()
        view = consent_view(page, on_accepted)
        dark = mode == "dark" or (
            mode == "system"
            and getattr(page, "platform_brightness", None) == ft.Brightness.DARK
        )
        apply_palette(view, dark=dark)
        page.add(ft.SafeArea(content=view, expand=True))
        apply_page_theme(page, mode, update=False)
        page.update()

    # Flet 1.x Dropdown emits selection through on_select. Assigning an
    # on_change attribute silently leaves this callback disconnected on Linux,
    # so the locale only appeared to change when Agree saved the value later.
    language.on_select = change_language

    def accept(_event: Any) -> None:
        # Persist the selected locale again on the explicit continue action.
        # This also covers mobile clients that do not commit a dropdown change
        # until the user leaves the first-run screen.
        saved = actions.save_settings(language=str(language.value or "en"))
        if not saved.ok:
            dialog = ft.AlertDialog(
                modal=True,
                title=text(saved.title, 16, TEXT, weight=ft.FontWeight.BOLD),
                content=text(t("Could not save your language choice."), 13),
                actions=[
                    ft.TextButton(
                        content=t("Back"),
                        on_click=lambda _e: page.pop_dialog(),
                    )
                ],
            )
            prepare_dialog(
                page, dialog, actions.runtime().settings.config.app.theme_mode
            )
            page.show_dialog(dialog)
            return
        result = actions.accept_consent()
        if not result.ok:
            raise RuntimeError(result.detail)
        page.clean()
        on_accepted()

    def decline(_event: Any) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title=text(t("Consent declined"), 16, TEXT),
            content=text(
                t(
                    "BlockProbe never touches the network before consent. Close this "
                    "window to exit; you can start it again any time."
                ),
                13,
            ),
            actions=[ft.Button(content=t("Back"), on_click=lambda _e: page.pop_dialog())],
        )
        prepare_dialog(
            page, dialog, actions.runtime().settings.config.app.theme_mode
        )
        page.show_dialog(dialog)

    width = getattr(page, "width", None) or 600
    padding = 16 if width < 600 else 32
    return ft.Container(
        content=ft.Column(
            [
                text(f"{APP_NAME} {__version__}", 30, TEXT, weight=ft.FontWeight.BOLD),
                language,
                text(t("Before the first network request"), 16, MUTED),
                ft.Column(
                    [
                        *blocks,
                        text(
                            t(
                                "Translations are drafts pending human legal review; the English "
                                "wording prevails and none of this is legal advice."
                            ),
                            11,
                        ),
                    ],
                    spacing=16,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
                ft.ResponsiveRow(
                    [
                        ft.Container(
                            content=button(
                                t("I understand and agree"),
                                ft.Icons.CHECK,
                                accept,
                                primary=True,
                            ),
                            col={"xs": 12, "sm": 8},
                        ),
                        ft.Container(
                            content=button(t("Decline"), ft.Icons.CLOSE, decline),
                            col={"xs": 12, "sm": 4},
                        ),
                    ],
                    run_spacing=8,
                ),
            ],
            spacing=12,
            expand=True,
        ),
        padding=padding,
        expand=True,
        bgcolor=BACKGROUND,
    )
