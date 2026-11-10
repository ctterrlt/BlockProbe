# SPDX-License-Identifier: AGPL-3.0-or-later
"""On-demand, read-only local network profile screen."""

from __future__ import annotations

import asyncio
import ipaddress
from datetime import UTC, datetime
from typing import Any

import flet as ft

from blockprobe.android_network import is_android, read_active_network
from blockprobe.core.models import NetworkContext
from blockprobe.i18n import t
from blockprobe.ui.components import MUTED, button, card, hero, text


def _valid_dns_servers(values: tuple[str, ...]) -> list[str]:
    """Normalize resolver IPs and ignore malformed extension payloads."""
    servers: list[str] = []
    for value in values:
        try:
            normalized = ipaddress.ip_address(value.strip()).compressed
        except ValueError:
            continue
        if normalized not in servers:
            servers.append(normalized)
    return servers


class ProfileScreen:
    """Mixin that displays local VPN, DNS and network configuration hints."""

    page: ft.Page
    _compact_layout: bool
    _apply_live_theme: Any
    apply: Any

    def _setup_profile(self) -> None:
        self.profile_status = text(t("Profile has not been refreshed."), 12, MUTED)
        self.profile_refresh_button: ft.Button | None = None
        self.profile_settings_button: ft.Button | None = None
        self.profile_content = ft.Column(
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.profile_root: ft.Column | None = None
        self._profile_request = 0
        self._profile_cards: list[ft.Control] = []
        self._profile_refresh_on_resume = False

    def _profile_view(self) -> ft.Control:
        if self.profile_root is not None:
            return self.profile_root
        self.profile_refresh_button = button(
            t("Refresh profile"),
            ft.Icons.REFRESH,
            self._refresh_profile,
        )
        self.profile_settings_button = button(
            t("Open device network settings"),
            ft.Icons.SETTINGS_OUTLINED,
            self._open_device_network_settings,
        )
        self.profile_settings_button.visible = is_android()
        self.profile_permission_hint = text(
            t(
                "Android grants network-state access when the app is installed. "
                "No runtime permission prompt is available."
            ),
            12,
            MUTED,
        )
        self.profile_permission_hint.visible = is_android()
        self.profile_root = ft.Column(
            [
                hero(
                    t("Device and network context"),
                    t("View local VPN, DNS, proxy and filtering signals."),
                    icon=ft.Icons.PERSON_SEARCH,
                ),
                self.profile_refresh_button,
                self.profile_settings_button,
                self.profile_permission_hint,
                text(
                    t("Refresh the profile after changing network settings."),
                    12,
                    MUTED,
                ),
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

    async def _open_device_network_settings(self, _event: Any = None) -> None:
        """Open Android Settings and refresh local signals on return."""
        from blockprobe.diagnostics.system_dns import open_android_network_settings

        self._profile_refresh_on_resume = True
        try:
            opened = await asyncio.to_thread(open_android_network_settings)
        except Exception:
            opened = False
        if opened:
            self.profile_status.value = t(
                "Refresh the profile after changing network settings."
            )
            self.page.update()
            return

        self._profile_refresh_on_resume = False
        from blockprobe.ui import actions

        self.apply(
            actions.ActionResult(
                t("Action failed"),
                t("The action could not be completed. Check Logs for details."),
                ok=False,
            )
        )

    async def _load_profile(self, request: int) -> None:
        """Collect configuration off the UI loop; render only after it returns."""
        try:
            # Import diagnostics only when the user opens or refreshes Profile.
            from blockprobe.diagnostics import collect_network_context

            context = await asyncio.to_thread(collect_network_context)
            if is_android():
                android_network = await read_active_network(page=self.page)
                active_servers = _valid_dns_servers(android_network.dns_servers)
                from blockprobe.diagnostics.system_dns import (
                    merge_android_network_context,
                )

                merge_android_network_context(
                    context,
                    active_servers,
                    private_dns_hostname=android_network.private_dns_hostname,
                    private_dns_active=android_network.private_dns_active,
                )
                if android_network.available:
                    context.private_dns = (
                        android_network.private_dns_hostname
                        or t("enabled" if android_network.has_private_dns else "disabled")
                    )
                else:
                    context.private_dns = t("unknown")
            else:
                # The Python diagnostic's explanatory text is technical English.
                # Keep the visible profile localized when Android's native data
                # source is not part of this platform.
                context.private_dns = t("unknown")
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
        from blockprobe.diagnostics import (
            dns_filter_categories,
            dns_provider_for_hostname,
            dns_provider_profile,
        )

        updated = datetime.fromtimestamp(context.timestamp, UTC).astimezone().strftime("%Y-%m-%d %H:%M")
        self.profile_status.value = t("Profile updated: {time}", time=updated)

        def value_list(values: list[str]) -> ft.Column:
            return ft.Column(
                [text(value, 13, selectable=True) for value in (values or [t("unknown")])],
                spacing=5,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            )

        network_items = [
            t(
                "VPN/tunnel hints: {value}",
                value=", ".join(context.vpn_providers) or t("unknown"),
            ),
            t(
                "Active interfaces: {value}",
                value=", ".join(context.vpn_interfaces) or t("unknown"),
            ),
            t(
                "Proxy settings: {value}",
                value=", ".join(context.proxies) or t("unknown"),
            ),
        ]
        provider_profiles = dns_provider_profile(context.resolvers)
        resolver_labels: list[str] = []
        for server in context.resolvers:
            provider = next(
                (item for item in provider_profiles if item.matches(server)),
                None,
            )
            if provider is not None:
                provider_name = " — ".join(
                    value for value in (provider.hostname, provider.name) if value
                )
                resolver_labels.append(f"{provider_name} — {server}")
                continue
            label = next(
                (
                    value
                    for value in context.resolver_labels
                    if value.startswith(server)
                ),
                server,
            )
            resolver_labels.append(label)
        configured_resolvers = ", ".join(
            resolver_labels or context.resolver_labels or context.resolvers
        ) or t("unknown")
        category_labels = {
            "adult": "adult content",
        }
        category_values = dns_filter_categories(context.resolvers)
        private_dns_provider = dns_provider_for_hostname(context.private_dns)
        if private_dns_provider is not None:
            category_values.extend(private_dns_provider.categories)
        categories = ", ".join(
            t(category_labels.get(category, category))
            for category in dict.fromkeys(category_values)
        ) or t("unknown")
        dns_items = [
            t("Configured resolvers: {value}", value=configured_resolvers),
            t(
                "Known filtering DNS: {value}",
                value=", ".join(context.dns_filters) or t("unknown"),
            ),
            t("Known filter categories: {categories}", categories=categories),
        ]
        private_dns_value = context.private_dns or t("unknown")
        if private_dns_provider is not None:
            private_dns_value = (
                f"{private_dns_provider.hostname} — {private_dns_provider.name} — "
                f"{', '.join(private_dns_provider.servers)}"
            )
        other_items = [
            t("IPv6: {value}", value=t("available" if context.ipv6 else "unavailable")),
            t("Hosts-file block entries: {count}", count=context.hosts_block_entries),
            t("Android Private DNS: {value}", value=private_dns_value),
            t("Browser Secure DNS: {value}", value=t("unknown")),
            t("Operating system: {value}", value=context.os_name or t("unknown")),
            t("App version: {value}", value=context.app_version or t("unknown")),
        ]
        self._profile_cards = [
            card(t("VPN and proxy"), value_list(network_items)),
            card(
                t("DNS settings"),
                ft.Column(
                    [
                        value_list(dns_items),
                        text(
                            t(
                                "DNS provider details are local hints and cannot confirm "
                                "which websites are blocked."
                            ),
                            12,
                            MUTED,
                        ),
                    ],
                    spacing=6,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
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
        self._apply_live_theme()
        self.page.update()

    def refresh_profile_layout(self, compact: bool) -> None:
        """Keep the profile readable at phone widths and usable with scaling."""
        if self.profile_root is None:
            return
        self.profile_root.spacing = 10 if compact else 14
        for item in self._profile_cards:
            if isinstance(item, ft.Container):
                item.padding = ft.Padding.symmetric(
                    horizontal=12 if compact else 16,
                    vertical=10 if compact else 14,
                )
