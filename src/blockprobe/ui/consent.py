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
from blockprobe.ui.components import BACKGROUND, MUTED, TEXT, button, text

_LANGUAGE_FLAGS = {"it": "🇮🇹", "en": "🇬🇧", "fr": "🇫🇷", "es": "🇪🇸", "pt": "🇵🇹"}


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
        width=220,
        options=[
            ft.DropdownOption(key=code, text=f"{_LANGUAGE_FLAGS[code]} {name}")
            for code, name in available_languages().items()
        ],
    )

    def change_language(_event: Any) -> None:
        result = actions.save_settings(language=str(language.value or "en"))
        if not result.ok:
            return
        page.clean()
        page.add(ft.SafeArea(content=consent_view(page, on_accepted), expand=True))
        page.update()

    language.on_change = change_language

    def accept(_event: Any) -> None:
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
        page.show_dialog(dialog)

    return ft.Container(
        content=ft.Column(
            [
                text(f"{APP_NAME} {__version__}", 30, TEXT, weight=ft.FontWeight.BOLD),
                language,
                text(t("Before the first network request"), 16, MUTED),
                *blocks,
                ft.Row(
                    [
                        button(
                            t("I understand and agree"),
                            ft.Icons.CHECK,
                            accept,
                            primary=True,
                        ),
                        button(t("Decline"), ft.Icons.CLOSE, decline),
                    ],
                    spacing=12,
                    wrap=True,
                ),
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
        ),
        padding=40,
        expand=True,
        bgcolor=BACKGROUND,
    )
