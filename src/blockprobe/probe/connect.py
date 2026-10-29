# SPDX-License-Identifier: AGPL-3.0-or-later
"""TCP/TLS connection probes with two retries and millisecond timing."""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import time

from blockprobe.core.models import ConnectResult

_LOGGER = logging.getLogger("blockprobe.probe.connect")


def _ssl_context() -> ssl.SSLContext:
    # Verification is intentionally disabled: a block page or a misconfigured
    # site may present a certificate that does not match. The app only reads
    # content, never submits credentials, so it can record the TLS failure and
    # still fetch the page in a second request.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, ConnectionRefusedError):
        return "connect_refused"
    if isinstance(exc, ConnectionResetError):
        return "connect_reset"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "connect_timeout"
    if isinstance(exc, ssl.SSLError):
        return "tls_failure"
    if isinstance(exc, socket.gaierror):
        return "dns_failure"
    if isinstance(exc, OSError):
        return "connect_failure"
    return "connect_failure"


async def connect(
    host: str,
    ip: str,
    port: int,
    timeout: float = 6.0,
    retries: int = 2,
    use_tls: bool = True,
) -> ConnectResult:
    """Open a TCP/TLS connection, retrying transient failures."""
    last_error: Exception | None = None
    start = time.perf_counter()
    attempts = 0
    for attempt in range(retries + 1):
        attempts += 1
        writer = None
        try:
            ssl_context = _ssl_context() if use_tls else None
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=ip,
                    port=port,
                    ssl=ssl_context,
                    server_hostname=host if use_tls else None,
                ),
                timeout=timeout,
            )
            del reader
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return ConnectResult(
                host=host,
                ip=ip,
                port=port,
                ok=True,
                connect_ms=elapsed_ms,
                tls=use_tls,
                attempts=attempts,
            )
        except Exception as exc:
            # CancelledError derives from BaseException, so cancellation is not
            # swallowed and retried here; scans must stop promptly when asked.
            last_error = exc
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            if attempt < retries:
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    kind = _classify_error(last_error) if last_error else "connect_failure"
    return ConnectResult(
        host=host,
        ip=ip,
        port=port,
        ok=False,
        connect_ms=elapsed_ms,
        error_kind=kind,
        error=f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error",
        tls=use_tls,
        attempts=attempts,
    )
