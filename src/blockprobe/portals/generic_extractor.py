# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layout-independent candidate extraction from portal pages.

Three passes, exactly as FR-3 requires:

1. every ``href``/``src``/``action`` attribute;
2. hostnames in visible text, including plain text with no anchor;
3. URLs and hostnames inside inline scripts and JSON.

Every candidate keeps its origin so the UI can explain where it came from. The
extractor never executes scripts and never renders a page.
"""

from __future__ import annotations

import hashlib
import html as html_module
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from blockprobe.core.enums import Origin
from blockprobe.core.models import ExtractedDomain, PageExtraction, Portal
from blockprobe.portals.domain_normalizer import DomainRejected, normalize_domain

# Infrastructure and platforms that appear on nearly every regulator page.
# They are excluded by default; individual portals can add their own sources.
DEFAULT_DENYLIST: tuple[str, ...] = (
    "adm.gov.it",
    "mef.gov.it",
    "governo.it",
    "agcom.it",
    "example.com",
    "example.org",
    "example.net",
    "w3.org",
    "schema.org",
    "iana.org",
    "icann.org",
    "gnu.org",
    "creativecommons.org",
    "github.com",
    "githubusercontent.com",
    "gitlab.com",
    "python.org",
    "pypi.org",
    "mozilla.org",
    "apache.org",
    "google.com",
    "google.it",
    "googleapis.com",
    "gstatic.com",
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "youtube.com",
    "ytimg.com",
    "facebook.com",
    "fbcdn.net",
    "instagram.com",
    "cdninstagram.com",
    "twitter.com",
    "x.com",
    "twimg.com",
    "linkedin.com",
    "licdn.com",
    "tiktok.com",
    "whatsapp.com",
    "telegram.org",
    "bing.com",
    "yahoo.com",
    "duckduckgo.com",
    "baidu.com",
    "jquery.com",
    "bootstrapcdn.com",
    "fontawesome.com",
    "cloudflare.com",
    "jsdelivr.net",
    "unpkg.com",
    "matomo.org",
    "piwik.pro",
    "sogei.it",
    "hotjar.com",
    "sentry.io",
    "gravatar.com",
    "wp.com",
    "wordpress.org",
    "cloudfront.net",
    "akamaihd.net",
    "vimeo.com",
    "addthis.com",
    "sharethis.com",
)

_URL_IN_TEXT_RE = re.compile(
    r"(?:https?://|www\.)[^\s\"'<>()\[\]{}|\\^`]+",
    re.IGNORECASE,
)
_HOST_RE = re.compile(
    r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})(?![\w.-])",
    re.IGNORECASE,
)
_QUOTED_VALUE_RE = re.compile(r"""(['"])([^'"]{1,255})\1""")
_TOTAL_RE = re.compile(
    r"(\d{1,3}(?:[.\s]\d{3})+|\d{1,7})\s*"
    r"(?:risultat[ie]|concessionari|elementi|records?|results?|domini|siti)",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_AJAX_URL_RE = re.compile(
    r"['\"](?P<url>[^'\"]*p_p_lifecycle=(?:2|\\x32|\\u0032)[^'\"]*)['\"]",
    re.IGNORECASE,
)
_JS_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})")


def _js_unescape(text: str) -> str:
    """Decode the ``\\x3d`` / ``\\u003d`` escapes Liferay embeds in scripts."""

    def replace(match: re.Match[str]) -> str:
        hex_value = match.group(1) or match.group(2)
        try:
            return chr(int(hex_value, 16))
        except (TypeError, ValueError):  # pragma: no cover - regex guarantees hex
            return match.group(0)

    return _JS_ESCAPE_RE.sub(replace, text)


@dataclass(slots=True)
class _Attribute:
    tag: str
    name: str
    value: str
    origin: Origin


class _Scanner(HTMLParser):
    """Collect attributes, table text and plain text without a DOM library."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attributes: list[_Attribute] = []
        self.text_parts: list[str] = []
        self.table_parts: list[str] = []
        self.scripts: list[str] = []
        self._table_depth = 0
        self._raw_depth = 0
        self._current_raw_tag = ""
        self._current_script: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
        if tag in {"script", "style"}:
            self._raw_depth += 1
            self._current_raw_tag = tag
            self._current_script = []
        origin_by_attr = {
            "href": Origin.ANCHOR,
            "src": Origin.SCRIPT,
            "action": Origin.FORM,
            "data-url": Origin.SCRIPT,
            "data-href": Origin.ANCHOR,
        }
        for name, value in attrs:
            if value is None:
                continue
            origin = origin_by_attr.get(name.lower())
            if origin is not None:
                self.attributes.append(_Attribute(tag, name.lower(), value, origin))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table" and self._table_depth > 0:
            self._table_depth -= 1
        if tag in {"script", "style"} and self._raw_depth > 0:
            self._raw_depth -= 1
            if tag == "script":
                self.scripts.append("".join(self._current_script))
            self._current_raw_tag = ""
            self._current_script = []

    def handle_data(self, data: str) -> None:
        if self._raw_depth > 0:
            if self._current_raw_tag == "script":
                self._current_script.append(data)
            return
        if not data.strip():
            return
        self.text_parts.append(data)
        if self._table_depth > 0:
            self.table_parts.append(data)


def strip_tags(value: str) -> str:
    """Return visible text from a small HTML fragment."""
    scanner = _Scanner()
    try:
        scanner.feed(value)
        scanner.close()
    except Exception:  # pragma: no cover - HTMLParser is forgiving, but never crash
        pass
    return html_module.unescape(" ".join(scanner.text_parts))


def extract_title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", html_module.unescape(match.group(1))).strip()[:300]


def content_hash(html: str) -> str:
    normalized = re.sub(r"\s+", " ", html or "").strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def estimated_total(html: str) -> int | None:
    match = _TOTAL_RE.search(strip_tags(html))
    if not match:
        return None
    raw = re.sub(r"[.\s]", "", match.group(1))
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 0 < value < 10_000_000 else None


def find_ajax_content_url(html: str, base_url: str) -> str | None:
    """Find a Liferay-style ``p_p_lifecycle=2`` fragment URL.

    Some regulator portals render the useful table through an asynchronous
    portlet. Fetching the fragment declared in the page is layout-independent:
    the URL is read from the page, not hard-coded to ADM. Both plain and
    JavaScript-escaped (``\\x3d``) query strings are understood.
    """
    candidates = [html or "", _js_unescape(html or "")]
    for text in candidates:
        for match in _AJAX_URL_RE.finditer(text):
            raw = html_module.unescape(match.group("url")).replace("\\/", "/")
            raw = _js_unescape(raw)
            if not raw:
                continue
            resolved = urljoin(base_url, raw)
            parts = urlsplit(resolved)
            if parts.scheme in {"http", "https"} and parts.hostname:
                return resolved
    return None


def _is_denied(host: str, domain: str, portal: Portal) -> bool:
    denied = set(DEFAULT_DENYLIST) | set(portal.exclude_domains or ())
    for item in denied:
        item = item.strip().lower().lstrip(".")
        if not item:
            continue
        if host == item or host.endswith("." + item) or domain == item or domain.endswith("." + item):
            return True
    if portal.start_url:
        try:
            portal_host = urlsplit(portal.start_url).hostname or ""
        except ValueError:
            portal_host = ""
        if portal_host and (host == portal_host or host.endswith("." + portal_host)):
            return True
        try:
            portal_domain = normalize_domain(portal.start_url).domain
        except DomainRejected:
            portal_domain = ""
        if portal_domain and (
            domain == portal_domain or domain.endswith("." + portal_domain)
        ):
            return True
    return False


def _include_allowed(host: str, portal: Portal) -> bool:
    patterns = [pattern for pattern in portal.include_regex or () if pattern]
    if not patterns:
        return True
    for pattern in patterns:
        try:
            if re.search(pattern, host, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _candidate(value: str, origin: Origin, base_url: str, portal: Portal) -> ExtractedDomain | None:
    value = html_module.unescape(value or "").strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith(("mailto:", "tel:", "javascript:", "file:", "data:", "#")):
        return None
    if value.startswith("//"):
        value = "http:" + value
    try:
        normalized = normalize_domain(value)
    except DomainRejected:
        return None
    if _is_denied(normalized.host, normalized.domain, portal):
        return None
    if not _include_allowed(normalized.host, portal):
        return None
    source = value if value.lower().startswith(("http://", "https://")) else base_url
    return ExtractedDomain(
        host=normalized.host,
        domain=normalized.domain,
        origin=origin,
        source_url=source[:500],
        context=value[:200],
    )


def _prune_apex(candidates: dict[str, ExtractedDomain]) -> None:
    """Drop a bare apex candidate when a more specific host exists for it."""
    for host, candidate in list(candidates.items()):
        if host != candidate.domain:
            continue
        if any(
            other != host and other.endswith("." + candidate.domain)
            for other in candidates
        ):
            del candidates[host]


def extract_candidates(
    html: str,
    base_url: str,
    portal: Portal,
) -> list[ExtractedDomain]:
    """Run the three extraction passes and return one candidate per hostname."""
    scanner = _Scanner()
    try:
        scanner.feed(html or "")
        scanner.close()
    except Exception:
        pass

    found: dict[str, ExtractedDomain] = {}

    def add(value: str, origin: Origin) -> None:
        candidate = _candidate(value, origin, base_url, portal)
        if candidate is None:
            return
        existing = found.get(candidate.host)
        if existing is None:
            found[candidate.host] = candidate
            return
        priority = {
            Origin.TABLE_TEXT: 5,
            Origin.ANCHOR: 4,
            Origin.FORM: 3,
            Origin.TEXT: 2,
            Origin.SCRIPT: 1,
            Origin.CUSTOM: 0,
        }
        if priority.get(candidate.origin, 0) > priority.get(existing.origin, 0):
            found[candidate.host] = candidate

    # Pass 1: attributes.
    for attribute in scanner.attributes:
        raw = attribute.value.strip()
        if attribute.origin is Origin.ANCHOR and raw.lower().startswith(
            ("mailto:", "tel:", "javascript:", "file:", "#")
        ):
            continue
        add(raw, attribute.origin)

    # Pass 2: visible text, including table cells.
    table_text = "\n".join(scanner.table_parts)
    general_text = "\n".join(scanner.text_parts)
    for match in _HOST_RE.finditer(table_text):
        add(match.group(1), Origin.TABLE_TEXT)
    for match in _URL_IN_TEXT_RE.finditer(table_text):
        add(match.group(0), Origin.TABLE_TEXT)
    for match in _HOST_RE.finditer(general_text):
        add(match.group(1), Origin.TEXT)
    for match in _URL_IN_TEXT_RE.finditer(general_text):
        add(match.group(0), Origin.TEXT)

    # Pass 3: inline scripts and JSON. Bare hostnames are only trusted when a
    # quoted string is exactly a hostname (a JSON array of domains); a JS
    # property chain such as ``Liferay.Data`` or ``event.page`` never matches.
    script_text = "\n".join(scanner.scripts)
    for match in _URL_IN_TEXT_RE.finditer(script_text):
        add(match.group(0), Origin.SCRIPT)
    for match in _QUOTED_VALUE_RE.finditer(script_text):
        quoted = match.group(2)
        if quoted == quoted.strip() and _HOST_RE.fullmatch(quoted):
            add(quoted, Origin.SCRIPT)

    _prune_apex(found)
    return sorted(found.values(), key=lambda item: (item.domain, item.host))


def extract_page(html: str, url: str, portal: Portal) -> PageExtraction:
    """Extract candidates and page metadata for one fetched page."""
    candidates = extract_candidates(html, url, portal)
    return PageExtraction(
        url=url,
        candidates=candidates,
        stated_total=estimated_total(html),
        content_hash=content_hash(html),
        title=extract_title(html),
        text_length=len(strip_tags(html)),
    )
