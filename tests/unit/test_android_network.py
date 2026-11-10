# SPDX-License-Identifier: AGPL-3.0-or-later
"""Android active-network adapter tests (no device and no network access).

The Flet/Kotlin bridge is replaced with small fakes; these tests pin the
Python contract the profile/scan code relies on: tolerant parsing, stable
error codes, one service per page and no exception leakage.
"""

from __future__ import annotations

from typing import Any

import pytest

from blockprobe import android_network


class FakeService:
    def __init__(self, payload: Any = None, *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    async def read_state(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


class FakePage:
    """Stand-in page that only needs attribute storage."""


def test_is_android_detects_the_python_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANDROID_ARGUMENT", raising=False)
    monkeypatch.delenv("ANDROID_ROOT", raising=False)

    class DesktopSys:
        platform = "linux"

    monkeypatch.setattr(android_network, "sys", DesktopSys())
    assert android_network.is_android() is False

    monkeypatch.setenv("ANDROID_ARGUMENT", "/data/user/0")
    assert android_network.is_android() is True


def test_parse_payload_keeps_order_and_drops_bad_entries() -> None:
    snapshot = android_network.parse_network_payload(
        {
            "available": True,
            "dns_servers": ["1.1.1.1", " 1.1.1.1 ", "", None, "2606:4700::1111"],
            "private_dns_active": True,
            "private_dns_hostname": "  one.one.one.one ",
            "transports": ["wifi", "", "wifi"],
        }
    )
    assert snapshot.available is True
    assert snapshot.dns_servers == ("1.1.1.1", "2606:4700::1111")
    assert snapshot.private_dns_active is True
    assert snapshot.private_dns_hostname == "one.one.one.one"
    assert snapshot.transports == ("wifi",)
    assert snapshot.has_private_dns is True


def test_parse_payload_tolerates_non_mapping_and_bad_types() -> None:
    assert android_network.parse_network_payload(None).error == "invalid_response"
    snapshot = android_network.parse_network_payload(
        {"available": "yes", "dns_servers": "1.1.1.1", "error": ""}
    )
    assert snapshot.available is False
    assert snapshot.dns_servers == ()
    assert snapshot.error is None


async def test_read_active_network_is_a_noop_off_android(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_network, "is_android", lambda: False)
    service = FakeService({"available": True})
    snapshot = await android_network.read_active_network(service=service)
    assert snapshot.available is False
    assert service.calls == 0


async def test_read_active_network_parses_the_platform_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_network, "is_android", lambda: True)
    service = FakeService(
        {
            "available": True,
            "dns_servers": ["94.140.14.14", "2a10:50c0::ad1:ff"],
            "private_dns_active": True,
            "private_dns_hostname": "dns.adguard-dns.com",
            "transports": ["wifi"],
        }
    )
    snapshot = await android_network.read_active_network(service=service)
    assert snapshot.available is True
    assert snapshot.dns_servers == ("94.140.14.14", "2a10:50c0::ad1:ff")
    assert snapshot.private_dns_hostname == "dns.adguard-dns.com"


async def test_read_active_network_reports_errors_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_network, "is_android", lambda: True)

    broken = FakeService(error=RuntimeError("bridge gone"))
    snapshot = await android_network.read_active_network(service=broken)
    assert snapshot.available is False
    assert snapshot.error == "RuntimeError"

    empty = FakeService(None)
    snapshot = await android_network.read_active_network(service=empty)
    assert snapshot.error == "invalid_response"


async def test_read_active_network_without_page_or_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_network, "is_android", lambda: True)
    monkeypatch.setattr(android_network, "_page_or_none", lambda page: None)
    snapshot = await android_network.read_active_network()
    assert snapshot.available is False
    assert snapshot.error == "no_page"


def test_network_service_is_created_once_per_page() -> None:
    page = FakePage()
    first = android_network.network_service(page)
    second = android_network.network_service(page)
    assert first is not None
    assert first is second
    assert page._blockprobe_android_network_service is first  # type: ignore[attr-defined]

    other = android_network.network_service(FakePage())
    assert other is not None
    assert other is not first


def test_network_service_returns_none_without_a_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_network, "_page_or_none", lambda page: None)
    assert android_network.network_service() is None


def test_describe_network_keeps_raw_servers_and_private_dns() -> None:
    snapshot = android_network.AndroidNetwork(
        available=True,
        dns_servers=("9.9.9.9",),
        private_dns_active=True,
        private_dns_hostname="dns.quad9.net",
        transports=("wifi",),
    )
    evidence = android_network.describe_network(snapshot)
    assert evidence
    assert evidence.servers == ("9.9.9.9",)
    assert evidence.private_dns_hostname == "dns.quad9.net"
    assert evidence.private_dns_active is True

    empty = android_network.describe_network(android_network.AndroidNetwork())
    assert not empty
