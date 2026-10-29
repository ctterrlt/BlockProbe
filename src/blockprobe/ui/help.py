# SPDX-License-Identifier: AGPL-3.0-or-later
"""Help screen: about, privacy summary and responsible use (FR-13)."""

from __future__ import annotations

import flet as ft

from blockprobe import __version__
from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import TEXT, card, text


def help_view() -> ft.Control:
    """Build the help screen. Stateless, so it is a plain builder."""
    lines: list[ft.Control] = []
    for title, body in actions.consent_texts():
        lines.append(
            ft.Column(
                [
                    text(title, 13, TEXT, weight=ft.FontWeight.BOLD),
                    text(body, 12),
                ],
                spacing=4,
            )
        )
    lines.append(
        text(
            t(
                "BlockProbe {version} — AGPL-3.0-or-later, no warranty, results are "
                "indicative. Sources: official regulator lists such as ADM and the user's "
                "own list. Verified BetBlocker rules are included; the live block page "
                "is also fetched on each run and new patterns are appended for review.",
                version=__version__,
            ),
            12,
        )
    )
    lines.append(
        text(
            t(
                "Scope: no age verification, no cookies, no uploads, no website "
                "terms, no images, no GDPR/US-law claim."
            ),
            12,
        )
    )
    return ft.Column(
        [card(t("About, privacy and permission"), ft.Column(lines, spacing=12))],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
