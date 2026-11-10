# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read the active Android network DNS state through the bundled Flet extension.

The extension is a small Kotlin plugin (``ConnectivityManager``/``LinkProperties``)
shipped inside this package under ``blockprobe/flutter/blockprobe_android_network``.
It only answers questions about the network the device is currently using; it has
no network access of its own and it never changes system settings.

Everything here degrades to an ``available=False`` snapshot on non-Android
platforms, on an older ABI that lacks the plugin, or when the platform call
fails. Callers must treat this as advisory evidence: the profile still shows
the open-settings action, and a failure must never abort a scan.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import flet as ft

if TYPE_CHECKING:
    from flet.controls.page import Page

SERVICE_TYPE = "BlockProbeAndroidNetwork"
"""Flet control type served by the bundled Dart extension."""

_PAGE_ATTRIBUTE = "_blockprobe_android_network_service"


@dataclass(frozen=True)
class AndroidNetwork:
    """Active-network DNS state reported by Android.

    ``available`` means the platform answered for the active network. The DNS
    list may still be empty when Android has not exposed resolvers for that
    network, so consumers must not treat an empty list as "no filtering".
    """

    available: bool = False
    dns_servers: tuple[str, ...] = ()
    private_dns_active: bool = False
    private_dns_hostname: str | None = None
    transports: tuple[str, ...] = ()
    error: str | None = None

    @property
    def has_private_dns(self) -> bool:
        """Whether Private DNS is active, with or without a known hostname."""
        return self.private_dns_active or bool(self.private_dns_hostname)


def is_android() -> bool:
    """Return True when running inside an Android Python runtime."""
    if sys.platform == "android":
        return True
    return bool(os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_ROOT"))


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[str] = []
    for entry in value:
        text = _text(entry)
        if text and text not in items:
            items.append(text)
    return tuple(items)


def parse_network_payload(payload: object) -> AndroidNetwork:
    """Build a snapshot from the extension's response, tolerating bad fields."""
    if not isinstance(payload, dict):
        return AndroidNetwork(error="invalid_response")
    return AndroidNetwork(
        available=payload.get("available") is True,
        dns_servers=_text_tuple(payload.get("dns_servers")),
        private_dns_active=payload.get("private_dns_active") is True,
        private_dns_hostname=_text(payload.get("private_dns_hostname")),
        transports=_text_tuple(payload.get("transports")),
        error=_text(payload.get("error")),
    )


@ft.control(SERVICE_TYPE)
class AndroidNetworkService(ft.Service):
    """Bridge to the Kotlin ``blockprobe_android_network`` method channel.

    Construct it inside a Page context; Flet registers user services with the
    current page automatically. The adapter below does that lazily and reuses
    one instance per page.
    """

    async def read_state(self) -> dict[str, Any] | None:
        """Ask the platform for the active network state.

        Returns ``None`` when the extension is missing or answers with a
        non-mapping value, so callers fall back instead of guessing.
        """
        result = await self._invoke_method("read_state")
        return result if isinstance(result, dict) else None

    async def open_network_settings(self) -> bool:
        """Open Android's wireless settings through the native plugin."""
        return await self._invoke_method("open_network_settings") is True


def _page_or_none(page: Page | None) -> Page | None:
    if page is not None:
        return page
    try:
        return ft.context.page
    except RuntimeError:
        return None


def network_service(page: Page | None = None) -> AndroidNetworkService | None:
    """Return the page's network service, creating and registering it once.

    Returns ``None`` when there is no page context, which keeps unit tests and
    headless callers from touching the Flet client.
    """
    current = _page_or_none(page)
    if current is None:
        return None
    cached = getattr(current, _PAGE_ATTRIBUTE, None)
    if isinstance(cached, AndroidNetworkService):
        return cached
    service = AndroidNetworkService()
    setattr(current, _PAGE_ATTRIBUTE, service)
    return service


async def read_active_network(
    page: Page | None = None,
    *,
    service: AndroidNetworkService | None = None,
) -> AndroidNetwork:
    """Read the active network state, never raising on platform failure."""
    if not is_android():
        return AndroidNetwork()
    active = service if service is not None else network_service(page)
    if active is None:
        return AndroidNetwork(error="no_page")
    try:
        payload = await active.read_state()
    except Exception as exc:  # platform bridge failures must not abort a scan
        return AndroidNetwork(error=type(exc).__name__)
    return parse_network_payload(payload)


async def open_android_network_settings(page: Page | None = None) -> bool:
    """Open the device's wireless settings without launching a shell command."""
    if not is_android():
        return False
    service = network_service(page)
    if service is None:
        return False
    try:
        return await service.open_network_settings()
    except Exception:
        return False


@dataclass(frozen=True)
class AndroidDnsEvidence:
    """Display summary derived from an :class:`AndroidNetwork` snapshot.

    The profile screen still resolves hostnames through the local DNS provider
    catalog; this only carries the raw platform answer so the caller can show
    ``hostname — IP`` without this module inventing provider names.
    """

    servers: tuple[str, ...] = ()
    private_dns_hostname: str | None = None
    transports: tuple[str, ...] = ()
    private_dns_active: bool = False
    error: str | None = None

    def __bool__(self) -> bool:
        return bool(self.servers or self.private_dns_hostname or self.private_dns_active)


def describe_network(snapshot: AndroidNetwork) -> AndroidDnsEvidence:
    """Return the fields the profile screen needs, without provider guesses."""
    return AndroidDnsEvidence(
        servers=snapshot.dns_servers,
        private_dns_hostname=snapshot.private_dns_hostname,
        transports=snapshot.transports,
        private_dns_active=snapshot.has_private_dns,
        error=snapshot.error,
    )
