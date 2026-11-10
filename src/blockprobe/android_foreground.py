# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep an active Android scan in a foreground service with a notification.

The thin adapter wraps the pinned ``flet-notifications`` API so the scan flow
does not depend on that package directly. It is deliberately honest about what
Android guarantees:

* A ``dataSync`` foreground service raises the process priority while the app
  is minimized, so the active Python scan keeps running in the common case.
* It does **not** survive force-stop, an explicit task removal, low-memory
  process death, or platform time limits for ``dataSync`` (Android 15 caps the
  type per 24 h and stops it when the cap is reached).
* On Android 13+ notifications need the runtime ``POST_NOTIFICATIONS``
  permission. If the user denies it, the service still runs but the ongoing
  notification is hidden. A denied permission must never fail the scan.

Every function returns a status object instead of raising, so the caller can
report the outcome and keep scanning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from blockprobe.android_network import is_android

if TYPE_CHECKING:
    from flet.controls.page import Page

SCAN_NOTIFICATION_ID = 4201
"""Non-zero notification id; Android rejects a zero foreground-service id."""

SCAN_CHANNEL_ID = "blockprobe_scan"
"""Android notification channel used for the ongoing scan notification."""

FOREGROUND_SERVICE_TYPES = ("dataSync",)
"""Manifest service type declared by the build patch; keep both in sync."""

_PAGE_ATTRIBUTE = "_blockprobe_notifications_service"


@dataclass(frozen=True)
class ForegroundScan:
    """Outcome of a foreground-service start or stop request.

    ``notifications_enabled`` is ``None`` when the platform could not answer.
    ``reason`` is a stable short code (``not_android``, ``no_page``, an error
    code from the notifications package, or an exception class name).
    """

    active: bool
    notifications_enabled: bool | None = None
    reason: str | None = None


def _reason(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return type(exc).__name__


def _notifications_api() -> tuple[Any, Any] | None:
    """Return the notifications service and channel classes, or ``None``.

    Imported lazily so ordinary desktop startup does not pay for a package that
    only matters on Android, and so a missing optional runtime degrades to a
    clear status instead of an import crash.
    """
    try:
        from flet_notifications import LocalNotifications, NotificationChannel
    except Exception:  # pragma: no cover - packaging guard, covered by status test
        return None
    return LocalNotifications, NotificationChannel


def _page_or_none(page: Page | None) -> Page | None:
    if page is not None:
        return page
    try:
        import flet as ft

        return ft.context.page
    except RuntimeError:
        return None


def notifications_service(page: Page | None = None) -> Any | None:
    """Return the page's ``LocalNotifications`` service, creating it once."""
    current = _page_or_none(page)
    if current is None:
        return None
    cached = getattr(current, _PAGE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    api = _notifications_api()
    if api is None:
        return None
    service = api[0]()
    setattr(current, _PAGE_ATTRIBUTE, service)
    return service


async def start_scan_foreground(
    page: Page | None = None,
    *,
    title: str,
    body: str,
    channel_name: str = "BlockProbe",
    notifications: Any | None = None,
) -> ForegroundScan:
    """Start the ongoing scan notification without ever breaking the scan."""
    if not is_android():
        return ForegroundScan(active=False, reason="not_android")
    try:
        service = notifications if notifications is not None else notifications_service(page)
    except Exception as exc:
        return ForegroundScan(active=False, reason=_reason(exc))
    if service is None:
        return ForegroundScan(active=False, reason="no_page")

    try:
        enabled = bool(await service.are_notifications_enabled())
    except Exception as exc:
        return ForegroundScan(active=False, reason=_reason(exc))
    if not enabled:
        try:
            enabled = bool(await service.request_permissions())
        except Exception:
            enabled = False

    api = _notifications_api()
    if api is not None:
        _, channel_type = api
        try:
            await service.create_notification_channel(channel_type(id=SCAN_CHANNEL_ID, name=channel_name))
        except Exception:
            # Channel creation is best effort; an existing channel is fine and a
            # failure here must not stop the scan.
            pass

    try:
        await service.start_foreground_service(
            id=SCAN_NOTIFICATION_ID,
            title=title,
            body=body,
            channel_id=SCAN_CHANNEL_ID,
            foreground_service_types=list(FOREGROUND_SERVICE_TYPES),
            show_progress=True,
            indeterminate=True,
        )
    except Exception as exc:
        return ForegroundScan(active=False, notifications_enabled=enabled, reason=_reason(exc))
    return ForegroundScan(active=True, notifications_enabled=enabled)


async def stop_scan_foreground(
    page: Page | None = None,
    *,
    notifications: Any | None = None,
) -> None:
    """Stop the ongoing scan notification; safe to call twice or after failure."""
    if not is_android():
        return
    try:
        service = notifications if notifications is not None else notifications_service(page)
    except Exception:
        return
    if service is None:
        return
    try:
        await service.stop_foreground_service()
    except Exception:
        # The service may already be gone (platform stop, timeout, restart).
        pass
