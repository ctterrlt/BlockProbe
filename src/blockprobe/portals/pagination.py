# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pagination discovery.

GET pagination is auto-discovered in this order: ``rel=next`` and anchors
labelled next / > / » / successiva / avanti, numbered pager links and common
query parameters (``page``, ``p``, ``pagina``, ``pager``, ``start``, ``offset``).
Simple HTML forms with hidden pagination fields are replayed as well. JavaScript
only pagination is out of scope for mobile and is documented as desktop-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

NEXT_LABELS = {
    "next",
    "next page",
    ">",
    ">>",
    "»",
    "\u203a",
    "successiva",
    "successivo",
    "avanti",
    "pagina successiva",
    "next »",
    "seguente",
    "weiter",
    "siguiente",
}
PAGE_KEYS = {"page", "p", "pagina", "pager", "pg", "start", "offset", "from", "skip"}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []
        self.forms: list[tuple[str, str, list[tuple[str, str]]]] = []
        self._href: str | None = None
        self._rel = ""
        self._text: list[str] = []
        self._form_action: str | None = None
        self._form_method = "get"
        self._form_fields: list[tuple[str, str]] = []
        self._in_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag == "a":
            self._href = values.get("href")
            self._rel = values.get("rel", "")
            self._text = []
        elif tag == "form":
            self._in_form = True
            self._form_action = values.get("action") or ""
            self._form_method = (values.get("method") or "get").lower()
            self._form_fields = []
        elif tag in {"input", "select", "textarea"} and self._in_form:
            name = values.get("name") or values.get("id")
            if name:
                self._form_fields.append((name, values.get("value", "")))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, self._rel, text))
            self._href = None
            self._rel = ""
            self._text = []
        elif tag == "form" and self._in_form:
            self.forms.append(
                (self._form_action or "", self._form_method, list(self._form_fields))
            )
            self._in_form = False

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


@dataclass(slots=True)
class FormRequest:
    url: str
    method: str
    data: dict[str, str]


@dataclass(slots=True)
class PaginationState:
    """Track visited URLs and pages that add no new domains."""

    visited: set[str] = field(default_factory=set)
    content_hashes: set[str] = field(default_factory=set)
    pages: int = 0
    empty_pages: int = 0

    def register(self, url: str, content_hash: str, new_domains: int) -> bool:
        """Return True when the caller should continue, False to stop."""
        self.visited.add(url)
        self.content_hashes.add(content_hash)
        self.pages += 1
        self.empty_pages = self.empty_pages + 1 if new_domains <= 0 else 0
        return self.empty_pages < 2

    def already_visited(self, url: str, content_hash: str = "") -> bool:
        if url in self.visited:
            return True
        return bool(content_hash and content_hash in self.content_hashes)


def _page_number(url: str) -> int | None:
    parts = urlsplit(url)
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in PAGE_KEYS:
            match = re.search(r"\d+", value or "")
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    return None
            if value == "":
                return 1
    return None


def _score_link(href: str, rel: str, text: str, current_url: str) -> int:
    lowered = text.strip().lower()
    rel_tokens = {token.lower() for token in re.split(r"\s+", rel or "") if token}
    score = 0
    if "next" in rel_tokens:
        score += 100
    if lowered in NEXT_LABELS:
        score += 90
    elif any(label and label in lowered for label in ("successiva", "next")):
        score += 60
    parts = urlsplit(href)
    keys = {key.lower() for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if keys & PAGE_KEYS:
        score += 70
    if re.fullmatch(r"\d{1,3}", lowered):
        score += 50
    if _page_number(href) is not None:
        score += 20
    if parts.scheme and parts.scheme not in {"http", "https"}:
        return 0
    if href.strip().startswith(("#", "mailto:", "tel:", "javascript:")):
        return 0
    if not parts.path and not parts.query:
        return 0
    if score == 0:
        # Sort and filter links have no pagination evidence; ignore them even if
        # they look like relative URLs.
        return 0
    if urljoin(current_url, href).rstrip("/") == current_url.rstrip("/"):
        return 0
    return score


def discover_next_links(html: str, current_url: str, max_links: int = 50) -> list[str]:
    parser = _LinkParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass

    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for href, rel, text in parser.links:
        if not href:
            continue
        score = _score_link(href, rel, text, current_url)
        if score <= 0:
            continue
        resolved = urljoin(current_url, href)
        if resolved in seen:
            continue
        seen.add(resolved)
        page = _page_number(resolved)
        scored.append((score, page if page is not None else 9999, resolved))

    scored.sort(key=lambda item: (-item[0], item[1]))
    ordered = [item[2] for item in scored]
    numeric = [item for item in ordered if _page_number(item) is not None]
    other = [item for item in ordered if _page_number(item) is None]
    numeric.sort(key=lambda url: _page_number(url) or 0)
    return (numeric + other)[:max_links]


def discover_form_pagination(
    html: str,
    current_url: str,
    max_pages: int = 3,
) -> list[FormRequest]:
    """Replay simple GET/POST pagination forms with incremented page fields."""
    parser = _LinkParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass

    requests: list[FormRequest] = []
    for action, method, fields in parser.forms:
        mapping = {name.lower(): (name, value) for name, value in fields}
        page_field = next(
            (mapping[key] for key in PAGE_KEYS if key in mapping), None
        )
        if page_field is None:
            continue
        name, value = page_field
        match = re.search(r"\d+", value or "")
        if not match:
            continue
        current = int(match.group(0))
        url = urljoin(current_url, action or current_url)
        payload = {field_name: field_value for field_name, field_value in fields}
        for page in range(current + 1, current + max_pages + 1):
            request_data = dict(payload)
            request_data[name] = re.sub(r"\d+", str(page), value, count=1)
            requests.append(
                FormRequest(
                    url=url,
                    method="post" if method == "post" else "get",
                    data=request_data,
                )
            )
    return requests


def page_number_from_url(url: str) -> int:
    return _page_number(url) or 1


def query_variants(url: str, key: str, pages: int) -> list[str]:
    """Build ``?key=N`` URLs for portals without discoverable pager links."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    results: list[str] = []
    for page in range(2, pages + 2):
        query[key] = str(page)
        results.append(
            urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
            )
        )
    return results
