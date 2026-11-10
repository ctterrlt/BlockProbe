# SPDX-License-Identifier: AGPL-3.0-or-later
"""Android foreground-scan adapter tests (no device, no real notifications).

They pin the promise made to the user: the scan is never aborted because the
notification channel/permission failed, and the service type stays ``dataSync``
so the manifest patch and the runtime call cannot drift apart.
"""

from __future__ import annotations

from typing import Any

import pytest

from blockprobe import android_foreground


class NotificationFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FakeNotifications:
    def __init__(
        self,
        *,
        enabled: bool = True,
        granted: bool = True,
        start_error: Exception | None = None,
        state_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.enabled = enabled
        self.granted = granted
        self.start_error = start_error
        self.state_error = state_error
        self.stop_error = stop_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def are_notifications_enabled(self) -> bool:
        if self.state_error is not None:
            raise self.state_error
        self.calls.append(("are_notifications_enabled", {}))
        return self.enabled

    async def request_permissions(self) -> bool:
        self.calls.append(("request_permissions", {}))
        return self.granted

    async def create_notification_channel(self, channel: Any) -> None:
        self.calls.append(("create_notification_channel", {"id": channel.id, "name": channel.name}))

    async def start_foreground_service(self, **kwargs: Any) -> None:
        self.calls.append(("start_foreground_service", kwargs))
        if self.start_error is not None:
            raise self.start_error

    async def stop_foreground_service(self) -> None:
        self.calls.append(("stop_foreground_service", {}))
        if self.stop_error is not None:
            raise self.stop_error

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def _start_kwargs(notifications: FakeNotifications) -> dict[str, Any]:
    return next(args for name, args in notifications.calls if name == "start_foreground_service")


async def test_start_is_a_noop_off_android(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: False)
    notifications = FakeNotifications()
    status = await android_foreground.start_scan_foreground(
        title="Scanning", body="Checking sites", notifications=notifications
    )
    assert status.active is False
    assert status.reason == "not_android"
    assert notifications.calls == []


async def test_start_declares_the_data_sync_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)
    notifications = FakeNotifications()
    status = await android_foreground.start_scan_foreground(
        title="Scanning", body="Checking sites", channel_name="Scan", notifications=notifications
    )
    assert status.active is True
    assert status.notifications_enabled is True
    assert status.reason is None
    assert notifications.names() == [
        "are_notifications_enabled",
        "create_notification_channel",
        "start_foreground_service",
    ]
    channel = notifications.calls[1][1]
    assert channel == {"id": android_foreground.SCAN_CHANNEL_ID, "name": "Scan"}
    kwargs = _start_kwargs(notifications)
    assert kwargs["id"] == android_foreground.SCAN_NOTIFICATION_ID != 0
    assert kwargs["channel_id"] == android_foreground.SCAN_CHANNEL_ID
    assert kwargs["foreground_service_types"] == ["dataSync"]
    assert kwargs["title"] == "Scanning"
    assert kwargs["body"] == "Checking sites"


async def test_denied_notification_permission_still_runs_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)
    notifications = FakeNotifications(enabled=False, granted=False)
    status = await android_foreground.start_scan_foreground(
        title="Scanning", body="Checking sites", notifications=notifications
    )
    assert status.active is True
    assert status.notifications_enabled is False
    assert "request_permissions" in notifications.names()
    assert "start_foreground_service" in notifications.names()


async def test_start_failure_returns_a_reason_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)
    notifications = FakeNotifications(start_error=NotificationFailure("foreground_service_failed"))
    status = await android_foreground.start_scan_foreground(
        title="Scanning", body="Checking sites", notifications=notifications
    )
    assert status.active is False
    assert status.reason == "foreground_service_failed"
    assert status.notifications_enabled is True


async def test_state_failure_returns_the_exception_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)
    notifications = FakeNotifications(state_error=RuntimeError("no bridge"))
    status = await android_foreground.start_scan_foreground(
        title="Scanning", body="Checking sites", notifications=notifications
    )
    assert status.active is False
    assert status.reason == "RuntimeError"


async def test_start_without_a_page_or_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)
    monkeypatch.setattr(android_foreground, "_page_or_none", lambda page: None)
    status = await android_foreground.start_scan_foreground(title="Scanning", body="Checking sites")
    assert status.active is False
    assert status.reason == "no_page"


async def test_page_service_construction_failure_is_reported_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)

    def broken_service(_page):  # type: ignore[no-untyped-def]
        raise RuntimeError("service init failed")

    monkeypatch.setattr(android_foreground, "notifications_service", broken_service)
    status = await android_foreground.start_scan_foreground(
        page=object(), title="Scanning", body="Checking sites"
    )

    assert status.active is False
    assert status.reason == "RuntimeError"


async def test_stop_service_construction_failure_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)

    def broken_service(_page):  # type: ignore[no-untyped-def]
        raise RuntimeError("service init failed")

    monkeypatch.setattr(android_foreground, "notifications_service", broken_service)
    await android_foreground.stop_scan_foreground(page=object())


async def test_stop_swallows_platform_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: True)
    notifications = FakeNotifications(stop_error=RuntimeError("already stopped"))
    await android_foreground.stop_scan_foreground(notifications=notifications)
    assert notifications.names() == ["stop_foreground_service"]


async def test_stop_is_a_noop_off_android(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_foreground, "is_android", lambda: False)
    notifications = FakeNotifications()
    await android_foreground.stop_scan_foreground(notifications=notifications)
    assert notifications.calls == []


def test_notifications_service_is_created_once_per_page() -> None:
    class FakePage:
        pass

    page = FakePage()
    first = android_foreground.notifications_service(page)
    second = android_foreground.notifications_service(page)
    assert first is not None
    assert first is second


def test_notifications_service_returns_none_when_the_package_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_foreground, "_notifications_api", lambda: None)
    assert android_foreground.notifications_service(type("Page", (), {})()) is None
