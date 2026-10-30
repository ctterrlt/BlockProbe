# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sources screen: portal lists and the user's own website list (FR-2, FR-15)."""

from __future__ import annotations

import time

import flet as ft

from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import (
    ACCENT_DIM,
    BAD,
    BORDER,
    MUTED,
    OK,
    SURFACE,
    TEXT,
    WARN,
    button,
    card,
    hero,
    text,
)


class SourcesScreen:
    """Mixin with the portals and custom-website screens."""

    page: ft.Page
    portals_text: ft.Column
    custom_input: ft.TextField
    custom_list: ft.Column
    portal_name_input: ft.TextField
    portal_url_input: ft.TextField
    portal_country_input: ft.TextField

    def _wrap(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def run_async(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def _setup_sources(self) -> None:
        self.portal_name_input = ft.TextField(label=t("Portal name"), text_size=14)
        self.portal_url_input = ft.TextField(
            label=t("Public regulator list URL"),
            hint_text=t("Paste the regulator's public list URL."),
            keyboard_type=ft.KeyboardType.URL,
            text_size=14,
        )
        self.portal_country_input = ft.TextField(
            label=t("Country code"), value="IT", max_length=2, text_size=14
        )
        self.portals_text = ft.Column(spacing=8)
        self.custom_input = ft.TextField(
            label=t("Add websites"),
            hint_text=t("Paste at least one domain, one per line."),
            multiline=True,
            min_lines=3,
            max_lines=6,
        )
        self.custom_list = ft.Column(spacing=4)

    def _sources_view(self) -> ft.Control:
        return ft.Column(
            [
                hero(
                    t("Your scan sources"),
                    t(
                        "Regulator feeds cover gambling sites; custom domains let "
                        "you test your own filter setup."
                    ),
                    icon=ft.Icons.TRAVEL_EXPLORE,
                ),
                card(
                    t("Portal lists"),
                    ft.Column(
                        [
                            self.portals_text,
                            ft.Row(
                                [
                                    button(
                                        t("Update website list"),
                                        ft.Icons.REFRESH,
                                        self._wrap(lambda: actions.refresh_portals()),
                                        primary=True,
                                    ),
                                    button(
                                        t("Refresh view"),
                                        ft.Icons.VISIBILITY,
                                        self._wrap(self._refresh_portals_view),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                        ],
                        spacing=10,
                    ),
                ),
                card(
                    t("Add a regulator portal feed"),
                    ft.Column(
                        [
                            text(
                                t(
                                    "A portal imports a published list. "
                                    "A custom website adds one domain only."
                                ),
                                13,
                            ),
                            self.portal_name_input,
                            self.portal_url_input,
                            self.portal_country_input,
                            button(
                                t("Add portal feed"),
                                ft.Icons.ADD_LINK,
                                self._wrap(self._add_portal),
                                primary=True,
                            ),
                        ],
                        spacing=10,
                    ),
                ),
                card(
                    t("Custom websites (never fetched, never deleted by an update)"),
                    ft.Column(
                        [
                            self.custom_input,
                            ft.Row(
                                [
                                    button(
                                        t("Add websites"),
                                        ft.Icons.ADD,
                                        self._wrap(self._add_custom),
                                        primary=True,
                                    ),
                                    button(
                                        t("Refresh list"),
                                        ft.Icons.VISIBILITY,
                                        self._wrap(self._reload_custom),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                            self.custom_list,
                        ],
                        spacing=10,
                    ),
                ),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            expand=True,
        )

    def _add_custom(self) -> actions.ActionResult:
        result = actions.add_custom_domains(self.custom_input.value or "")
        if result.ok:
            self.custom_input.value = ""
        self._reload_custom()
        return result

    def _add_portal(self) -> actions.ActionResult:
        result = actions.add_portal_definition(
            self.portal_name_input.value or "",
            self.portal_url_input.value or "",
            self.portal_country_input.value or "",
        )
        if result.ok:
            self.portal_name_input.value = ""
            self.portal_url_input.value = ""
            self.refresh_portal_texts()
        return result

    def _reload_custom(self) -> actions.ActionResult:
        result = actions.list_custom_domains()
        self.custom_list.controls = []
        try:
            sites = actions.runtime().storage.list_custom_sites()
        except Exception:
            sites = []
        for site in sites:
            row = ft.Row(
                [
                    ft.Container(
                        content=text(f"{site.domain} ({', '.join(site.hosts)})", 12, TEXT),
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        tooltip=t("Remove from the custom list (portal rows are kept)"),
                        icon_color=BAD,
                        data=site.domain,
                        on_click=self._remove_custom,
                    ),
                ],
                spacing=6,
                expand=True,
            )
            self.custom_list.controls.append(row)
        self.page.update()
        return result

    def _remove_custom(self, event: ft.ControlEvent) -> None:
        domain = str(event.control.data or "")
        self.run_async(lambda: self._remove_and_reload(domain))

    def _remove_and_reload(self, domain: str) -> actions.ActionResult:
        result = actions.remove_custom_domain(domain)
        self._reload_custom()
        return result

    def _refresh_portals_view(self) -> actions.ActionResult:
        self.refresh_portal_texts()
        try:
            self.page.update()
        except Exception:
            pass
        return actions.list_portals()

    def refresh_portal_texts(self) -> None:
        try:
            portals = actions.runtime().storage.list_portals()
        except Exception:
            portals = []
        self.portals_text.controls.clear()
        if not portals:
            self.portals_text.controls.append(text(t("No portals configured yet."), 13))
            return
        for portal in portals:
            state = t("enabled") if portal.enabled else t("disabled")
            if portal.last_refreshed_at:
                when = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(portal.last_refreshed_at)
                )
            else:
                when = t("never updated")
            status_color = (
                BAD if portal.last_error else OK if portal.last_refreshed_at else WARN
            )
            if portal.last_error:
                status_icon = ft.Icons.ERROR_OUTLINE
            elif portal.last_refreshed_at:
                status_icon = ft.Icons.CHECK_CIRCLE_OUTLINE
            else:
                status_icon = ft.Icons.SCHEDULE
            details = [f"{portal.country} · {state} · {when}"]
            if portal.last_error:
                details.append(t("last error: {error}", error=portal.last_error))
            self.portals_text.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                content=ft.Icon(status_icon, color=status_color, size=20),
                                width=38,
                                height=38,
                                alignment=ft.Alignment.CENTER,
                                bgcolor=ACCENT_DIM if not portal.last_error else "#F8E5E2",
                                border_radius=11,
                            ),
                            ft.Column(
                                [
                                    text(portal.name, 14, TEXT, weight=ft.FontWeight.W_600),
                                    text("\n".join(details), 12, BAD if portal.last_error else MUTED),
                                ],
                                spacing=3,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                    ),
                    padding=10,
                    bgcolor=SURFACE,
                    border=ft.Border.all(1, BORDER),
                    border_radius=11,
                )
            )
