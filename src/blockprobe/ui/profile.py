# SPDX-License-Identifier: AGPL-3.0-or-later
"""On-demand, read-only local network profile screen."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import flet as ft

from blockprobe.core.models import NetworkContext
from blockprobe.i18n import t
from blockprobe.ui.components import MUTED, button, card, hero, text


class ProfileScreen:
    """Mixin that displays local VPN, DNS and network configuration hints."""

    page: ft.Page

    def _setup_profile(self) -> None:
        self.profile_status = text(t("Profile has not been refreshed."), 12, MUTED)
        self.profile_refresh_button: ft.Button | None = None
        self.profile_content = ft.Column(
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.profile_root: ft.Column | None = None
        self._profile_request = 0
        self._profile_cards: list[ft.Container] = []

    def _profile_view(self) -> ft.Control:
        if self.profile_root is not None:
            return self.profile_root
        self.profile_refresh_button = button(
            t("Refresh profile"),
            ft.Icons.REFRESH,
            self._refresh_profile,
        )
        self.profile_root = ft.Column(
            [
                hero(
                    t("Device and network context"),
                    t("View local VPN, DNS, proxy and filtering signals."),
                    icon=ft.Icons.PERSON_SEARCH,
                ),
                self.profile_refresh_button,
                self.profile_status,
                self.profile_content,
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self.refresh_profile_layout(self._compact_layout)
        return self.profile_root

    def _refresh_profile(self, _event: Any = None) -> None:
        self._profile_request += 1
        request = self._profile_request
        self.profile_status.value = t("Reading local network settings…")
        if self.profile_refresh_button is not None:
            self.profile_refresh_button.disabled = True
        self.page.update()
        self.page.run_task(self._load_profile, request)

    async def _load_profile(self, request: int) -> None:
        """Collect configuration off the UI loop; render only after it returns."""
        try:
            # Import diagnostics only when the user opens or refreshes Profile.
            from blockprobe.diagnostics import collect_network_context

            context = await asyncio.to_thread(collect_network_context)
        except Exception:
            if request == self._profile_request:
                self.profile_status.value = t("Network profile could not be read.")
                self.profile_content.controls = []
                if self.profile_refresh_button is not None:
                    self.profile_refresh_button.disabled = False
                self.page.update()
            return
        if request != self._profile_request:
            return
        self._render_profile(context)

    def _render_profile(self, context: NetworkContext) -> None:
        updated = datetime.fromtimestamp(context.timestamp, UTC).astimezone().strftime("%Y-%m-%d %H:%M")
        self.profile_status.value = t("Profile updated: {time}", time=updated)

        def value_list(values: list[str]) -> ft.Column:
            return ft.Column(
                [text(value, 13, selectable=True) for value in (values or ["Not detected"])],
                spacing=5,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            )

        network_items = [
            f"VPN/tunnel hints: {', '.join(context.vpn_providers) or 'Not detected'}",
            f"Active interfaces: {', '.join(context.vpn_interfaces) or 'Not detected'}",
            f"Proxy settings: {', '.join(context.proxies) or 'Not detected'}",
        ]
        configured_resolvers = ", ".join(
            context.resolver_labels or context.resolvers
        ) or "Not detected"
        dns_items = [
            f"Configured resolvers: {configured_resolvers}",
            f"Known filtering DNS: {', '.join(context.dns_filters) or 'Not detected'}",
        ]
        other_items = [
            f"IPv6: {'available' if context.ipv6 else 'unavailable'}",
            f"Hosts-file block entries: {context.hosts_block_entries}",
            f"Android Private DNS: {context.private_dns}",
            f"Browser Secure DNS: {context.browser_doh}",
            f"Operating system: {context.os_name or 'Not detected'}",
            f"App version: {context.app_version or 'Not detected'}",
        ]
        self._profile_cards = [
            card(t("VPN and proxy"), value_list(network_items)),
            card(t("DNS settings"), value_list(dns_items)),
            card(t("Other network details"), value_list(other_items)),
            card(
                t("About these results"),
                text(
                    t(
                        "These are best-effort local hints. Some operating systems hide VPN "
                        "or Private DNS details. This profile makes no DNS lookup or "
                        "network request and never changes your network settings."
                    ),
                    12,
                    MUTED,
                ),
            ),
        ]
        self.profile_content.controls = self._profile_cards
        if self.profile_refresh_button is not None:
            self.profile_refresh_button.disabled = False
        self.refresh_profile_layout(self._compact_layout)
        self.page.update()

    def refresh_profile_layout(self, compact: bool) -> None:
        """Keep the profile readable at phone widths and usable with scaling."""
        if self.profile_root is None:
            return
        self.profile_root.spacing = 10 if compact else 14
        for item in self._profile_cards:
            item.padding = ft.Padding.symmetric(
                horizontal=12 if compact else 16,
                vertical=10 if compact else 14,
            )
