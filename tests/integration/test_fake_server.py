# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration test against a local fake server.

Emulates open, blocked, challenge, empty, redirect and unreachable (refused /
TLS failure) cases without touching the public network. Sinkhole detection is
covered at the same level because it needs no socket.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from blockprobe.core.enums import ReasonCode, SignatureKind, Verdict
from blockprobe.core.models import (
    DnsResult,
    ProbeObservation,
    SignatureRule,
)
from blockprobe.probe.classifier import classify
from blockprobe.probe.connect import connect
from blockprobe.probe.dns import is_sinkhole_ip
from blockprobe.probe.http_fetch import fetch

OPEN_BODY = (
    "<html><head><title>Casino Test</title></head><body>"
    "Benvenuto: scommesse sportive, casino online, slot machine e bonus di "
    "benvenuto. Gioco responsabile 18+."
    "</body></html>"
)
BLOCK_BODY = (
    "<html><head><title>Access blocked</title></head><body>"
    "This connection was stopped by the testing block page marker."
    "</body></html>"
)
CHALLENGE_BODY = (
    "<html><head><title>Just a moment...</title></head><body>"
    "Checking your browser before accessing. Enable JavaScript and cookies."
    "</body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        return

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/open")
            self.end_headers()
            return
        if route == "/open":
            status, body, content_type = 200, OPEN_BODY, "text/html; charset=utf-8"
        elif route == "/blocked":
            status, body, content_type = 200, BLOCK_BODY, "text/html; charset=utf-8"
        elif route == "/challenge":
            status, body, content_type = 200, CHALLENGE_BODY, "text/html; charset=utf-8"
        elif route == "/empty":
            status, body, content_type = 200, "", "text/html; charset=utf-8"
        else:
            status, body, content_type = 404, "<p>not found</p>", "text/html"
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


@pytest.fixture(scope="module")
def fake_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base
    server.shutdown()
    server.server_close()


def _observation(url: str, fetch_result) -> ProbeObservation:  # type: ignore[no-untyped-def]
    return ProbeObservation(
        domain="fake.test",
        host="fake.test",
        scheme="http",
        dns=DnsResult(host="fake.test", status="ok", ips=["127.0.0.1"]),
        fetch=fetch_result,
    )


def _reserved_port() -> tuple[socket.socket, int]:
    """Bind a port without listening and keep the socket open.

    Closing a socket and then connecting to its port is racy on Windows: the
    port can remain reserved and the connection reports a timeout instead of a
    refusal. Keeping the un-listened socket alive removes that race while still
    testing a non-listening TCP endpoint. Windows may still surface a timeout,
    which the test accepts as a transport failure rather than an opened site.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    return probe, int(probe.getsockname()[1])


async def test_open_page_is_classified_as_opened(fake_server: str) -> None:
    result = await fetch(f"{fake_server}/open")
    assert result.status == 200
    classification = classify(_observation(f"{fake_server}/open", result))
    assert classification.verdict is Verdict.OPENED
    assert classification.reason_code == ReasonCode.POSITIVE_MARKERS.value


async def test_block_page_rule_is_applied(fake_server: str) -> None:
    result = await fetch(f"{fake_server}/blocked")
    rule = SignatureRule(
        id="integration-block-rule",
        kind=SignatureKind.BLOCK,
        body_patterns=[r"testing block page marker"],
        blocker_hint="unknown",
        note="synthetic rule used by the integration test only",
    )
    classification = classify(_observation(f"{fake_server}/blocked", result), rules=[rule])
    assert classification.verdict is Verdict.BLOCKED
    assert classification.reason_code == ReasonCode.BLOCK_SIGNATURE.value
    assert classification.rule_id == "integration-block-rule"


async def test_challenge_page_counts_as_reachable(fake_server: str) -> None:
    result = await fetch(f"{fake_server}/challenge")
    classification = classify(_observation(f"{fake_server}/challenge", result))
    assert classification.verdict is Verdict.OPENED
    assert classification.reason_code == ReasonCode.CHALLENGE_PAGE.value


async def test_empty_page_is_not_a_leak(fake_server: str) -> None:
    result = await fetch(f"{fake_server}/empty")
    classification = classify(_observation(f"{fake_server}/empty", result))
    assert classification.verdict is Verdict.UNREACHABLE
    assert classification.reason_code == ReasonCode.EMPTY_PAGE.value


async def test_missing_page_is_dead(fake_server: str) -> None:
    result = await fetch(f"{fake_server}/missing")
    assert result.status == 404
    classification = classify(_observation(f"{fake_server}/missing", result))
    assert classification.verdict is Verdict.UNREACHABLE
    assert classification.reason_code == ReasonCode.DEAD_PAGE.value


async def test_redirect_chain_is_recorded(fake_server: str) -> None:
    result = await fetch(f"{fake_server}/redirect")
    assert result.status == 200
    assert result.redirect_chain
    assert result.final_url.endswith("/open")
    classification = classify(_observation(f"{fake_server}/redirect", result))
    assert classification.verdict is Verdict.OPENED


async def test_connect_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr("blockprobe.probe.connect.asyncio.open_connection", never)
    task = asyncio.create_task(
        connect("cancel.test", "127.0.0.1", 443, timeout=30, retries=2, use_tls=False)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)


async def test_refused_connection_is_unreachable() -> None:
    probe, port = _reserved_port()
    try:
        result = await connect(
            "closed.test",
            "127.0.0.1",
            port,
            timeout=1.0,
            retries=0,
            use_tls=False,
        )
    finally:
        probe.close()
    assert result.ok is False
    if sys.platform != "win32":
        assert result.error_kind == "connect_refused"
    else:
        # The Windows TCP stack can report a timeout for a reserved local port.
        # Both outcomes are transport failures, never an opened website.
        assert result.error_kind in {
            "connect_refused",
            "connect_timeout",
            "connect_failure",
        }


async def test_tls_failure_against_a_plain_http_port(fake_server: str) -> None:
    port = int(urlsplit(fake_server).port)
    result = await connect(
        "tls.test", "127.0.0.1", port, timeout=1.5, retries=0, use_tls=True
    )
    assert result.ok is False
    assert result.error_kind == "tls_failure"


def test_sinkhole_detection() -> None:
    for value in ("0.0.0.0", "::", "127.0.0.1", "10.0.0.1", "192.168.1.5", "169.254.9.9"):
        assert is_sinkhole_ip(value) is True
    for value in ("93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"):
        assert is_sinkhole_ip(value) is False
