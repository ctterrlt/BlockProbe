# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP fetching for probes and signature capture.

Security rules: http/https only, no cookies, no Referer, a maximum body size,
five redirects, no JavaScript and no downloads. The page is decoded as text and
never rendered. HTTPS certificates and hostnames are verified against the
platform trust store; invalid or intercepted certificates are reported as TLS
failures instead of fetching untrusted response bodies.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx

from blockprobe.core.models import HttpFetchResult
from blockprobe.http_user_agent import CHROME_USER_AGENT

_LOGGER = logging.getLogger("blockprobe.probe.http")

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
# Compatibility alias for callers that previously imported the probe constant.
PROBE_USER_AGENT = CHROME_USER_AGENT
DEFAULT_HEADERS = {
    "User-Agent": PROBE_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _decoded(data: bytes, encoding: str | None) -> str:
    for candidate in (encoding, "utf-8", "latin-1"):
        if not candidate:
            continue
        try:
            return data.decode(candidate, errors="replace")
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


def _title(text: str) -> str:
    from blockprobe.portals.generic_extractor import extract_title

    return extract_title(text)


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE | re.DOTALL)
_META_REFRESH_RE = re.compile(r"http-equiv\s*=\s*[\"']?refresh", re.IGNORECASE)
_META_CONTENT_RE = re.compile(
    r"content\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE | re.DOTALL
)
_JS_REDIRECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"window\.open\s*\(\s*([\"'])(?P<url>.*?)\1", re.IGNORECASE | re.DOTALL
    ),
    re.compile(
        r"(?:window|document|self|top|parent)\.location(?:\.href)?\s*=\s*"
        r"([\"'])(?P<url>.*?)\1",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?<![.\w])location(?:\.href)?\s*=\s*([\"'])(?P<url>.*?)\1",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"location\.(?:assign|replace)\s*\(\s*([\"'])(?P<url>.*?)\1",
        re.IGNORECASE | re.DOTALL,
    ),
)
_REOPEN_EVENT_RE = re.compile(
    r"\b(?:visibilitychange|pageshow|pagehide|beforeunload|focus|blur)\b",
    re.IGNORECASE,
)
_ASSET_SUFFIXES = (
    ".js",
    ".mjs",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp4",
    ".webm",
    ".pdf",
    ".zip",
)


def _is_http_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def extract_embedded_redirects(body: str, base_url: str, limit: int = 5) -> list[str]:
    """Return URLs a page would open or navigate to without executing scripts.

    Both ``<meta http-equiv="refresh">`` and the common inline JavaScript
    navigation patterns are inspected. Relative URLs are resolved against the
    final response URL. The result is evidence only: nothing is executed and the
    runner fetches at most one of these targets for classification.
    """
    if not body:
        return []
    targets: list[str] = []
    for tag in _META_TAG_RE.findall(body):
        if not _META_REFRESH_RE.search(tag):
            continue
        content = _META_CONTENT_RE.search(tag)
        if content is None:
            continue
        match = re.search(r"url\s*=\s*(.+)", content.group(1), re.IGNORECASE)
        if match:
            targets.append(match.group(1).strip().strip("\"'"))
    for pattern in _JS_REDIRECT_PATTERNS:
        for match in pattern.finditer(body):
            targets.append(match.group("url").strip())
    resolved: list[str] = []
    for target in targets:
        if not target or target.startswith(("#", "javascript:", "data:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, target)
        if not _is_http_url(absolute):
            continue
        path = urlsplit(absolute).path.lower()
        if path.endswith(_ASSET_SUFFIXES):
            continue
        if absolute not in resolved:
            resolved.append(absolute)
        if len(resolved) >= limit:
            break
    return resolved


def looks_like_block_target(url: str) -> bool:
    """Return True for paths commonly used by blocker landing pages."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    path = (parts.path or "").lower()
    query = (parts.query or "").lower()
    words = (
        "block",
        "denied",
        "deny",
        "restrict",
        "filter",
        "forbidden",
        "not-allowed",
        "self-exclusion",
        "self_exclusion",
        "vietato",
        "bloccat",
    )
    return any(word in path or word in query for word in words)


def detect_reopen_behaviour(body: str) -> bool:
    """Best-effort static hint that the page reopens a window on return.

    The probe never executes JavaScript. This only records that the fetched HTML
    combines an open/navigate call with focus, visibility or page-lifecycle
    events, which matches the reported "the blocker opens again when I come
    back" pattern and is stored as evidence, never as a verdict by itself.
    """
    if not body or not _REOPEN_EVENT_RE.search(body):
        return False
    return any(pattern.search(body) for pattern in _JS_REDIRECT_PATTERNS)


async def fetch(
    url: str,
    timeout: float = 10.0,
    max_redirects: int = 5,
    max_bytes: int = 262_144,
    headers: dict[str, str] | None = None,
) -> HttpFetchResult:
    """Fetch a URL, following redirects manually and recording the chain."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return HttpFetchResult(
            url=url,
            final_url=url,
            status=None,
            error="Only http and https URLs are supported",
            error_kind="invalid_url",
        )

    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    started = time.perf_counter()
    current = url
    chain: list[str] = []
    redirect_statuses: list[int] = []
    status: int | None = None
    response_headers: dict[str, str] = {}
    body = b""
    encoding: str | None = None
    truncated = False

    client = httpx.AsyncClient(
        verify=True,
        follow_redirects=False,
        timeout=httpx.Timeout(timeout, connect=min(timeout, 6.0)),
        trust_env=True,
    )
    try:
        for hop in range(max_redirects + 1):
            # A probe is not a browser session: never replay a Set-Cookie value.
            client.cookies.clear()
            try:
                request = client.build_request("GET", current, headers=merged_headers)
                response = await client.send(request, stream=True)
                client.cookies.clear()
            except httpx.TooManyRedirects as exc:
                return HttpFetchResult(
                    url=url,
                    final_url=current,
                    status=None,
                    redirect_chain=chain,
                    redirect_statuses=redirect_statuses,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    error=str(exc),
                    error_kind="too_many_redirects",
                )
            except httpx.TimeoutException as exc:
                return HttpFetchResult(
                    url=url,
                    final_url=current,
                    status=None,
                    redirect_chain=chain,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    error=f"timeout: {exc}",
                    error_kind="timeout",
                )
            except httpx.ConnectError as exc:
                return HttpFetchResult(
                    url=url,
                    final_url=current,
                    status=None,
                    redirect_chain=chain,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    error=f"connect error: {exc}",
                    error_kind="connect",
                )
            except httpx.HTTPError as exc:
                return HttpFetchResult(
                    url=url,
                    final_url=current,
                    status=None,
                    redirect_chain=chain,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    error=f"{exc.__class__.__name__}: {exc}",
                    error_kind="http",
                )

            status = response.status_code
            response_headers = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower()
                in {
                    "content-type",
                    "content-length",
                    "server",
                    "location",
                    "x-powered-by",
                    "cf-ray",
                }
            }
            location = response.headers.get("location")
            if status in _REDIRECT_STATUSES and location and hop < max_redirects:
                redirect_statuses.append(status)
                chain.append(urljoin(current, location))
                current = urljoin(current, location)
                await response.aclose()
                continue
            if status in _REDIRECT_STATUSES and location:
                await response.aclose()
                return HttpFetchResult(
                    url=url,
                    final_url=current,
                    status=status,
                    headers=response_headers,
                    redirect_chain=chain,
                    redirect_statuses=redirect_statuses,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    error="too many redirects",
                    error_kind="too_many_redirects",
                )

            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            disposition = response.headers.get("content-disposition", "").lower()
            if media_type not in {"text/html", "application/xhtml+xml"} or "attachment" in disposition:
                # Do not read file, image, script, or other non-page bodies.
                await response.aclose()
                break

            encoding = response.encoding
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    remaining = max_bytes - (size - len(chunk))
                    if remaining > 0:
                        chunks.append(chunk[:remaining])
                    truncated = True
                    break
                chunks.append(chunk)
            body = b"".join(chunks)
            await response.aclose()
            break
    finally:
        await client.aclose()

    text = _decoded(body, encoding)
    return HttpFetchResult(
        url=url,
        final_url=current,
        status=status,
        headers=response_headers,
        body=text,
        body_bytes=len(body),
        body_sha256=hashlib.sha256(body).hexdigest(),
        redirect_chain=chain,
        redirect_statuses=redirect_statuses,
        embedded_redirects=extract_embedded_redirects(text, current),
        reopens_on_return=detect_reopen_behaviour(text),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        error=None,
        error_kind=None,
        encoding=encoding or "",
        truncated=truncated,
        title=_title(text),
    )


async def fetch_many(
    urls: list[str],
    timeout: float = 10.0,
    concurrency: int = 5,
) -> list[HttpFetchResult | None]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def worker(url: str) -> HttpFetchResult:
        async with semaphore:
            return await fetch(url, timeout=timeout)

    return list(await asyncio.gather(*(worker(url) for url in urls)))
