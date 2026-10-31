# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional, bounded cross-check using the system curl executable.

This is used only after HTTPX has evidence for a blocked verdict. It is an
independent HTTP/TLS implementation, not a browser, and absence or failure of
curl never prevents the primary probe from completing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

from blockprobe.core.models import HttpFetchResult
from blockprobe.probe.http_fetch import (
    PROBE_USER_AGENT,
    _decoded,
    _title,
)

_LOGGER = logging.getLogger("blockprobe.probe.curl")
_META_MARKER = b"\nBLOCKPROBE_CURL_META:"
_STATUS_LINE = re.compile(r"^HTTP/\S+\s+(\d{3})(?:\s|$)", re.IGNORECASE)
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([^\s;\"']+)", re.IGNORECASE)
_MAX_REDIRECTS = 5


def _response_headers(raw: bytes, initial_url: str) -> tuple[dict[str, str], list[str], list[int]]:
    """Read final response headers and redirect targets from curl's dump."""
    blocks: list[tuple[int, dict[str, str]]] = []
    current_status: int | None = None
    current_headers: dict[str, str] = {}

    for raw_line in raw.splitlines():
        line = raw_line.decode("latin-1", errors="replace").strip("\r")
        status_match = _STATUS_LINE.match(line)
        if status_match is not None:
            if current_status is not None:
                blocks.append((current_status, current_headers))
            current_status = int(status_match.group(1))
            current_headers = {}
            continue
        if not line:
            continue
        if current_status is None or ":" not in line:
            continue
        name, value = line.split(":", 1)
        current_headers[name.strip().lower()] = value.strip()

    if current_status is not None:
        blocks.append((current_status, current_headers))

    redirect_chain: list[str] = []
    redirect_statuses: list[int] = []
    current_url = initial_url
    for status, headers in blocks:
        location = headers.get("location")
        if status in {301, 302, 303, 307, 308} and location:
            current_url = urljoin(current_url, location)
            redirect_chain.append(current_url)
            redirect_statuses.append(status)

    final_headers: dict[str, str] = {}
    if blocks:
        allowed = {"content-type", "content-length", "server", "location", "x-powered-by", "cf-ray"}
        final_headers = {
            key: value for key, value in blocks[-1][1].items() if key in allowed
        }
    return final_headers, redirect_chain, redirect_statuses


def _error_for_exit(returncode: int, stderr: bytes, max_bytes: int) -> tuple[str, str]:
    message = stderr.decode("utf-8", errors="replace").strip()
    if returncode == 28:
        return "timeout", message or "curl timed out"
    if returncode == 6:
        return "dns_failure", message or "curl could not resolve the host"
    if returncode == 7:
        return "connect", message or "curl could not connect to the host"
    if returncode == 35:
        return "tls", message or "curl TLS negotiation failed"
    if returncode == 47:
        return "too_many_redirects", message or "curl exceeded the redirect limit"
    if returncode == 63:
        return "body_too_large", message or f"curl response exceeded {max_bytes} bytes"
    return "curl_error", message or f"curl exited with status {returncode}"


async def _run_curl_request(
    url: str,
    *,
    executable: str,
    head: bool,
    timeout: float = 10.0,
    max_redirects: int = _MAX_REDIRECTS,
    max_bytes: int = 262_144,
) -> HttpFetchResult:
    """Run one bounded curl request after the executable was located.

    Curl's own config file is disabled and arguments are passed without a
    shell. Redirect protocols are restricted to HTTP(S), downloads are capped,
    and response bodies are kept only in memory for classification. The
    caller uses HEAD before GET so non-HTML bodies are never fetched.
    """
    started = time.perf_counter()
    limit = max(1, int(max_bytes))
    timeout = max(0.1, float(timeout))
    try:
        with tempfile.TemporaryDirectory(prefix="blockprobe-curl-") as folder:
            headers_path = Path(folder) / "response-headers.txt"
            command = [
                executable,
                "--disable",
                "--silent",
                "--show-error",
                "--location",
                "--max-redirs",
                str(max(0, int(max_redirects))),
                "--max-time",
                f"{timeout:.3f}",
                "--connect-timeout",
                f"{min(timeout, 6.0):.3f}",
                "--max-filesize",
                str(limit),
                "--proto",
                "=http,https",
                "--proto-redir",
                "=http,https",
                "--dump-header",
                str(headers_path),
                "--user-agent",
                PROBE_USER_AGENT,
                "--header",
                "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "--header",
                "Accept-Language: en-US,en;q=0.9",
                "--write-out",
                (
                    "\\nBLOCKPROBE_CURL_META:%{http_code}\\t%{url_effective}"
                    "\\t%{content_type}\\n"
                ),
                "--url",
                url,
            ]
            if head:
                command.insert(command.index("--location") + 1, "--head")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout + 2.0
                )
            except TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
                return HttpFetchResult(
                    url=url,
                    final_url=url,
                    status=None,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    error="curl exceeded its overall timeout",
                    error_kind="timeout",
                )
            try:
                header_bytes = headers_path.read_bytes()
            except OSError:
                header_bytes = b""

    except (OSError, ValueError, RuntimeError) as exc:
        _LOGGER.warning("curl cross-check could not run: %s", exc)
        return HttpFetchResult(
            url=url,
            final_url=url,
            status=None,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
            error_kind="curl_execution",
        )

    body_bytes, marker, metadata = stdout.rpartition(_META_MARKER)
    if not marker:
        failure_kind, failure_detail = _error_for_exit(
            int(process.returncode or 0), stderr, limit
        )
        if process.returncode == 0:
            failure_kind, failure_detail = "curl_output", "curl did not return response metadata"
        return HttpFetchResult(
            url=url,
            final_url=url,
            status=None,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=failure_detail,
            error_kind=failure_kind,
        )

    fields = metadata.decode("utf-8", errors="replace").strip().split("\t")
    try:
        status = int(fields[0]) if fields and fields[0].isdigit() else None
    except ValueError:  # defensive against a malformed write-out
        status = None
    final_url = fields[1] if len(fields) > 1 and fields[1] else url
    content_type = fields[2] if len(fields) > 2 else ""
    headers, redirect_chain, redirect_statuses = _response_headers(header_bytes, url)
    if content_type:
        headers.setdefault("content-type", content_type)

    mime_type = content_type.split(";", 1)[0].strip().lower()
    is_html = mime_type in {"text/html", "application/xhtml+xml"}
    body_data = body_bytes if is_html and not head else b""
    encoding_match = _CHARSET.search(content_type)
    encoding = encoding_match.group(1) if encoding_match else None
    body = _decoded(body_data, encoding) if body_data else ""
    returncode = int(process.returncode or 0)
    error: str | None = None
    error_kind: str | None = None
    if returncode != 0:
        error_kind, error = _error_for_exit(returncode, stderr, limit)
    elif head:
        error_kind = "head_only"
    return HttpFetchResult(
        url=url,
        final_url=final_url,
        status=status,
        headers=headers,
        body=body,
        body_bytes=len(body_data),
        body_sha256=hashlib.sha256(body_data).hexdigest() if body_data else "",
        redirect_chain=redirect_chain,
        redirect_statuses=redirect_statuses,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        error=error,
        error_kind=error_kind,
        encoding=encoding or "",
        truncated=returncode == 63 or len(body_data) >= limit,
        title=_title(body),
    )


async def fetch_with_curl(
    url: str,
    *,
    timeout: float = 10.0,
    max_redirects: int = _MAX_REDIRECTS,
    max_bytes: int = 262_144,
) -> HttpFetchResult | None:
    """Check with system curl, or return ``None`` if curl is absent.

    A bodyless HEAD follows the URL first. Curl fetches a bounded body only
    when that response declares HTML; other content remains unrequested.
    """
    executable = shutil.which("curl") or shutil.which("curl.exe")
    if executable is None:
        return None
    head_result = await _run_curl_request(
        url,
        executable=executable,
        head=True,
        timeout=timeout,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
    )
    content_type = head_result.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (
        head_result.status is None
        or head_result.error_kind != "head_only"
        or content_type not in {"text/html", "application/xhtml+xml"}
    ):
        return head_result
    return await _run_curl_request(
        url,
        executable=executable,
        head=False,
        timeout=timeout,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
    )
