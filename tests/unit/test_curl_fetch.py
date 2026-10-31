# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional curl cross-check stays bounded and cannot break the primary scan."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import flet as ft
import pytest

from blockprobe.core.enums import Confidence, Verdict
from blockprobe.core.models import (
    Classification,
    DnsResult,
    HttpFetchResult,
    ProbeObservation,
    ResultRow,
)
from blockprobe.probe import curl_fetch, runner
from blockprobe.probe.http_fetch import PROBE_USER_AGENT
from blockprobe.probe.variants import aggregate
from blockprobe.ui.results import ResultsScreen


class FakeProcess:
    def __init__(self, stdout: bytes, *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr


@pytest.mark.asyncio
async def test_curl_fetch_uses_safe_bounded_arguments_and_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"<html><title>Blocked</title><body>Access is blocked by your provider</body></html>"
    stdout = (
        body
        + b"\nBLOCKPROBE_CURL_META:200\thttps://example.com/blocked\t"
        + b"text/html; charset=utf-8\n"
    )
    monkeypatch.setattr(curl_fetch.shutil, "which", lambda _name: "/usr/bin/curl")
    calls: list[tuple[Any, ...]] = []
    processes: list[FakeProcess] = []
    head_stdout = (
        b"\nBLOCKPROBE_CURL_META:200\thttps://example.com/blocked\t"
        b"text/html; charset=utf-8\n"
    )

    async def create_process(*args: Any, **kwargs: Any) -> FakeProcess:
        calls.append(args)
        process = FakeProcess(head_stdout if len(calls) == 1 else stdout)
        process.args = args
        process.kwargs = kwargs
        header_path = Path(args[args.index("--dump-header") + 1])
        if len(calls) == 1:
            response_headers = (
                b"HTTP/1.1 302 Found\r\nLocation: /blocked\r\n\r\n"
                b"HTTP/2 200\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
            )
        else:
            response_headers = (
                b"HTTP/1.1 302 Found\r\nLocation: /blocked\r\n\r\n"
                b"HTTP/2 200\r\nContent-Type: text/html; charset=utf-8\r\n"
                b"Set-Cookie: private=discard-me\r\n\r\n"
            )
        header_path.write_bytes(response_headers)
        processes.append(process)
        return process

    monkeypatch.setattr(curl_fetch.asyncio, "create_subprocess_exec", create_process)
    result = await curl_fetch.fetch_with_curl(
        "https://example.com/", timeout=4, max_redirects=3, max_bytes=4096
    )

    assert result is not None
    assert result.status == 200
    assert result.final_url == "https://example.com/blocked"
    assert result.body == body.decode()
    assert result.headers["content-type"] == "text/html; charset=utf-8"
    assert result.redirect_chain == ["https://example.com/blocked"]
    assert result.redirect_statuses == [302]
    assert "set-cookie" not in result.headers
    assert result.body_sha256
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == "/usr/bin/curl"
    assert "--head" in calls[0]
    assert "--head" not in calls[1]
    assert "--disable" in calls[1]
    assert "--location" in calls[1]
    assert "--max-filesize" in calls[1]
    assert "4096" in calls[1]
    assert "=http,https" in calls[1]
    assert PROBE_USER_AGENT in calls[1]
    assert processes[-1].kwargs["stdin"] is curl_fetch.asyncio.subprocess.DEVNULL
    assert "shell" not in processes[-1].kwargs


@pytest.mark.asyncio
async def test_curl_fetch_is_optional_when_executable_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(curl_fetch.shutil, "which", lambda _name: None)

    async def should_not_start(*_args: Any, **_kwargs: Any) -> FakeProcess:
        raise AssertionError("curl subprocess must not start when curl is absent")

    monkeypatch.setattr(curl_fetch.asyncio, "create_subprocess_exec", should_not_start)
    assert await curl_fetch.fetch_with_curl("https://example.com/") is None


@pytest.mark.asyncio
async def test_curl_does_not_fetch_a_non_html_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(curl_fetch.shutil, "which", lambda _name: "/usr/bin/curl")
    calls: list[tuple[Any, ...]] = []

    async def create_process(*args: Any, **_kwargs: Any) -> FakeProcess:
        calls.append(args)
        process = FakeProcess(
            b"\nBLOCKPROBE_CURL_META:200\thttps://example.com/\t"
            b"application/octet-stream\n"
        )
        Path(args[args.index("--dump-header") + 1]).write_bytes(
            b"HTTP/2 200\r\nContent-Type: application/octet-stream\r\n\r\n"
        )
        return process

    monkeypatch.setattr(curl_fetch.asyncio, "create_subprocess_exec", create_process)
    result = await curl_fetch.fetch_with_curl("https://example.com/")

    assert result is not None
    assert result.error_kind == "head_only"
    assert result.body == ""
    assert len(calls) == 1
    assert "--head" in calls[0]


def _blocked_observation() -> tuple[ProbeObservation, Classification]:
    observation = ProbeObservation(
        domain="example.com",
        host="example.com",
        scheme="https",
        dns=DnsResult(host="example.com", status="ok"),
    )
    classification = Classification(
        verdict=Verdict.BLOCKED,
        reason_code="block_signature",
        confidence=Confidence.HIGH,
    )
    return observation, classification


@pytest.mark.asyncio
async def test_curl_disagreement_marks_blocked_result_for_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, classification = _blocked_observation()
    fetched = HttpFetchResult(
        url="https://example.com/",
        final_url="https://example.com/",
        status=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body="Welcome to Example",
        title="Welcome",
    )

    async def fake_curl(*_args: Any, **_kwargs: Any) -> HttpFetchResult:
        return fetched

    monkeypatch.setattr(runner, "fetch_with_curl", fake_curl)
    await runner.ProbeEngine(runner.EngineConfig())._curl_cross_check(
        observation, classification
    )

    assert classification.verdict is Verdict.BLOCKED
    assert classification.needs_review is True
    assert classification.evidence["curl_cross_check"]["status"] == "conflict"
    assert classification.evidence["curl_cross_check"]["verdict"] == Verdict.OPENED.value
    assert (
        classification.evidence["curl_cross_check"]["warning"]
        == "Warning: curl opened the website successfully."
    )


@pytest.mark.asyncio
async def test_curl_confirmation_keeps_blocked_result_without_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, classification = _blocked_observation()
    fetched = HttpFetchResult(
        url="https://example.com/",
        final_url="https://example.com/",
        status=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body="This website is blocked by your content filter",
    )

    async def fake_curl(*_args: Any, **_kwargs: Any) -> HttpFetchResult:
        return fetched

    monkeypatch.setattr(runner, "fetch_with_curl", fake_curl)
    await runner.ProbeEngine(runner.EngineConfig())._curl_cross_check(
        observation, classification
    )

    assert classification.verdict is Verdict.BLOCKED
    assert classification.needs_review is False
    assert classification.evidence["curl_cross_check"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_curl_unavailable_is_recorded_without_changing_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, classification = _blocked_observation()

    async def no_curl(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runner, "fetch_with_curl", no_curl)
    await runner.ProbeEngine(runner.EngineConfig())._curl_cross_check(
        observation, classification
    )

    assert classification.verdict is Verdict.BLOCKED
    assert classification.needs_review is False
    assert classification.evidence["curl_cross_check"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_non_html_curl_head_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, classification = _blocked_observation()
    fetched = HttpFetchResult(
        url="https://example.com/",
        final_url="https://example.com/",
        status=200,
        headers={"content-type": "application/octet-stream"},
        error_kind="head_only",
    )

    async def fake_curl(*_args: Any, **_kwargs: Any) -> HttpFetchResult:
        return fetched

    monkeypatch.setattr(runner, "fetch_with_curl", fake_curl)
    await runner.ProbeEngine(runner.EngineConfig())._curl_cross_check(
        observation, classification
    )

    assert classification.verdict is Verdict.BLOCKED
    assert classification.needs_review is False
    assert classification.evidence["curl_cross_check"]["status"] == "inconclusive"


@pytest.mark.asyncio
async def test_curl_is_not_run_for_non_blocked_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, classification = _blocked_observation()
    classification.verdict = Verdict.OPENED

    async def should_not_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("curl should only cross-check blocked results")

    monkeypatch.setattr(runner, "fetch_with_curl", should_not_run)
    await runner.ProbeEngine(runner.EngineConfig())._curl_cross_check(
        observation, classification
    )
    assert classification.evidence == {}


def test_variant_aggregate_keeps_any_curl_conflict_visible() -> None:
    confirmed = Classification(
        verdict=Verdict.BLOCKED,
        reason_code="block_signature",
        confidence=Confidence.HIGH,
        evidence={"curl_cross_check": {"status": "confirmed"}},
    )
    conflict = Classification(
        verdict=Verdict.BLOCKED,
        reason_code="block_signature",
        confidence=Confidence.HIGH,
        needs_review=True,
        evidence={
            "curl_cross_check": {
                "status": "conflict",
                "verdict": Verdict.OPENED.value,
                "http_status": 200,
                "warning": "Warning: curl opened the website successfully.",
            }
        },
    )

    result = aggregate("example.com", [confirmed, conflict])

    assert result.verdict is Verdict.BLOCKED
    assert result.needs_review is True
    assert result.evidence["curl_cross_checks"] == [
        {"primary_verdict": Verdict.BLOCKED.value, "status": "confirmed"},
        {
            "primary_verdict": Verdict.BLOCKED.value,
            "status": "conflict",
            "verdict": Verdict.OPENED.value,
            "http_status": 200,
            "warning": "Warning: curl opened the website successfully.",
        },
    ]
    assert result.evidence["curl_warnings"] == [
        "Warning: curl opened the website successfully."
    ]


def test_curl_open_warning_is_shown_in_desktop_and_mobile_results() -> None:
    warning = "Warning: curl opened the website successfully."
    row = ResultRow(
        run_id="run-1",
        domain="example.com",
        verdict=Verdict.BLOCKED,
        reason_code="block_signature",
        confidence=Confidence.HIGH,
        blocker_hint=None,
        needs_review=True,
        evidence={
            "curl_cross_check": {
                "status": "conflict",
                "verdict": Verdict.OPENED.value,
                "warning": warning,
            }
        },
        checked_at=1,
    )

    def text_values(control: ft.Control) -> list[str]:
        values = [control.value] if isinstance(control, ft.Text) and control.value else []
        children = list(getattr(control, "controls", []))
        content = getattr(control, "content", None)
        if isinstance(content, ft.Control):
            children.append(content)
        for child in children:
            values.extend(text_values(child))
        return values

    mobile = ResultsScreen._mobile_result_card(row)
    desktop = ResultsScreen._desktop_result_row(row)
    assert warning in text_values(mobile)
    assert warning in text_values(desktop.cells[2].content)
