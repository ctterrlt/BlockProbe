# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live BetBlocker block-page patterns.

The owner of BlockProbe asked for the BetBlocker block page to be fetched on
every run so that its wording does not silently go stale. The fetch is bounded,
never executes JavaScript, never stores cookies and never keeps the body. It
extracts only block-specific sentences, stores them in the local append-only
``dynamic_signatures`` table and reuses them on future runs.

Patterns are never removed by a normal run. Only the explicit privacy wipe in
Settings (or ``delete_all_data``) removes the table. The two patterns reported
by the owner are also shipped as normal verified rules in
``signatures/betblocker.toml`` so they keep working when the live page is
unreachable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from blockprobe.core.enums import BlockerHint, Confidence
from blockprobe.core.models import DynamicSignature, utc_now
from blockprobe.portals.generic_extractor import strip_tags
from blockprobe.probe.http_fetch import fetch
from blockprobe.storage.base import Storage

_LOGGER = logging.getLogger("blockprobe.probe.betblocker")

BETBLOCKER_BLOCK_URL = "https://betblocker.org/blocked/"
BETBLOCKER_SOURCE = "betblocker-page"

# Conservative, verified wording. The first two entries are the strings the
# owner reported from the live page. The third is the generic "This ... blocked"
# form, kept at medium confidence and flagged for review.
BETBLOCKER_DEFAULT_PATTERNS: tuple[DynamicSignature, ...] = (
    DynamicSignature(
        pattern=r"Blocked\s+by\s+BetBlocker",
        source=BETBLOCKER_SOURCE,
        note=(
            "Live BetBlocker title and heading. Re-checked on every run; the "
            "pattern is never removed automatically."
        ),
        confidence=Confidence.HIGH,
        blocker_hint=BlockerHint.BETBLOCKER.value,
    ),
    DynamicSignature(
        pattern=r"This\s+domain\s+was\s+blocked\s+by\s+BetBlocker(?:\s+application)?",
        source=BETBLOCKER_SOURCE,
        note=(
            "Live BetBlocker sentence: 'This domain was blocked by BetBlocker "
            "application.'"
        ),
        confidence=Confidence.HIGH,
        blocker_hint=BlockerHint.BETBLOCKER.value,
    ),
    DynamicSignature(
        pattern=r"This\s+[^.\n]{1,160}?\s+was\s+blocked\s+by\s+BetBlocker",
        source=BETBLOCKER_SOURCE,
        note=(
            "Owner-reported 'This ... blocked' form, scoped to BetBlocker so it "
            "cannot match an ordinary blocked page."
        ),
        confidence=Confidence.HIGH,
        blocker_hint=BlockerHint.BETBLOCKER.value,
    ),
    DynamicSignature(
        pattern=r"This\s+(?:website|site|domain|page)\s+(?:was|is|has\s+been)\s+blocked",
        source=BETBLOCKER_SOURCE,
        note=(
            "Generic 'This ... blocked' wording. Medium confidence and marked "
            "for review because geo-blocks can use similar text."
        ),
        confidence=Confidence.MEDIUM,
        blocker_hint=BlockerHint.BETBLOCKER.value,
        needs_review=True,
    ),
)

_BLOCK_WORDS = re.compile(
    r"\b(?:block(?:ed|ing)?|denied|deny|forbidden|restricted|prohibited|"
    r"self[- ]exclu(?:de|ded|sion)|not\s+available|unavailable)\b",
    re.IGNORECASE,
)
_BRAND_WORDS = re.compile(r"\bBetBlocker\b", re.IGNORECASE)
_GAMBLING_WORDS = re.compile(
    r"\b(?:gambl(?:e|ing)|bet(?:ting)?|casino|poker|slot(?:s| machine)?|"
    r"scommess[ae]|gioc[oi]|apuest[as]|paris|jeu|spiel|wetten|jogos?)\w*\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_INLINE_SPACE = re.compile(r"\s+")
_BLOCK_TAG = re.compile(
    r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|"
    r"footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_FILLER_PREFIXES = (
    "if you think",
    "please contact",
    "contact us",
    "click ",
    "download ",
    "privacy policy",
    "terms &",
    "terms and",
    "support",
    "faq",
)
_MAX_PATTERN_LENGTH = 180
_MAX_NEW_PATTERNS = 20


@dataclass(slots=True)
class SignatureRefresh:
    """What happened when the live BetBlocker page was inspected."""

    url: str = BETBLOCKER_BLOCK_URL
    fetched: bool = False
    status: int | None = None
    error: str | None = None
    checked_at: int = 0
    new_patterns: list[str] = field(default_factory=list)
    matched_defaults: list[str] = field(default_factory=list)
    total_patterns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "fetched": self.fetched,
            "status": self.status,
            "error": self.error,
            "checked_at": self.checked_at,
            "checked_at_iso": _iso(self.checked_at),
            "new_patterns": self.new_patterns,
            "matched_defaults": self.matched_defaults,
            "total_patterns": self.total_patterns,
        }


def _iso(timestamp: int) -> str:
    if not timestamp:
        return ""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _visible_fragments(body: str) -> list[str]:
    """Split block-level HTML before stripping tags.

    Joining every paragraph first merged navigation/footer text with the real
    block sentence and produced one over-specific pattern. Keeping block
    boundaries makes the extracted literals short and reviewable.
    """
    if not body:
        return []
    stripped = re.sub(
        r"<(script|style|noscript)\b.*?</\1>",
        " ",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    fragments: list[str] = []
    for fragment in _BLOCK_TAG.split(stripped):
        text = _INLINE_SPACE.sub(" ", strip_tags(fragment)).strip()
        if text:
            fragments.append(text)
    return fragments


def _matches(pattern: str, text: str) -> bool:
    try:
        return bool(re.search(pattern, text, re.IGNORECASE | re.DOTALL))
    except re.error:
        return pattern.lower() in text.lower()


def _is_safe_live_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if not (12 <= len(sentence) <= _MAX_PATTERN_LENGTH):
        return False
    if "http://" in lowered or "https://" in lowered or "javascript:" in lowered:
        return False
    if sentence.lower().startswith(_FILLER_PREFIXES):
        return False
    if not _BLOCK_WORDS.search(sentence):
        return False
    return bool(_BRAND_WORDS.search(sentence) or _GAMBLING_WORDS.search(sentence))


def extract_live_patterns(
    body: str,
    title: str = "",
    existing: list[str] | None = None,
) -> tuple[list[DynamicSignature], list[str]]:
    """Return (new dynamic patterns, matched built-in patterns).

    Only visible block-specific sentences are converted to patterns. They are
    escaped as literal text, so page content can never inject a regular
    expression. A pattern already covered by a built-in or stored rule is not
    duplicated.
    """
    text = "\n".join([title, *_visible_fragments(body)])
    matched_defaults = [
        signature.pattern
        for signature in BETBLOCKER_DEFAULT_PATTERNS
        if _matches(signature.pattern, text)
    ]
    known = [signature.pattern for signature in BETBLOCKER_DEFAULT_PATTERNS]
    known.extend(existing or [])
    candidates: list[DynamicSignature] = []
    seen: set[str] = set()
    for sentence in _SENTENCE_SPLIT.split(text):
        normalized = _INLINE_SPACE.sub(" ", sentence).strip()
        if not _is_safe_live_sentence(normalized):
            continue
        if any(_matches(pattern, normalized) for pattern in known):
            continue
        pattern = re.escape(normalized)
        if pattern in seen:
            continue
        seen.add(pattern)
        candidates.append(
            DynamicSignature(
                pattern=pattern,
                source=BETBLOCKER_SOURCE,
                note="Exact visible sentence captured from the live BetBlocker block page.",
                confidence=Confidence.MEDIUM,
                blocker_hint=BlockerHint.BETBLOCKER.value,
                needs_review=True,
            )
        )
        if len(candidates) >= _MAX_NEW_PATTERNS:
            break
    return candidates, matched_defaults


async def refresh_betblocker_signatures(
    storage: Storage,
    *,
    timeout: float = 10.0,
    max_bytes: int = 1_000_000,
) -> SignatureRefresh:
    """Fetch the live block page, append new patterns, never remove old ones."""
    refresh = SignatureRefresh(checked_at=utc_now())
    result = await fetch(
        BETBLOCKER_BLOCK_URL,
        timeout=timeout,
        max_redirects=3,
        max_bytes=max(64_000, max_bytes),
    )
    refresh.status = result.status
    if result.error is not None or result.status is None:
        refresh.error = result.error or "no HTTP status"
        refresh.total_patterns = len(storage.list_dynamic_signatures())
        _LOGGER.warning(
            "BetBlocker signature refresh failed",
            extra={"event": "dynamic_signature", "domain": BETBLOCKER_BLOCK_URL},
        )
        return refresh
    if not 200 <= result.status < 300:
        refresh.error = f"HTTP {result.status}"
        refresh.total_patterns = len(storage.list_dynamic_signatures())
        _LOGGER.warning(
            "BetBlocker signature refresh returned HTTP %s", result.status
        )
        return refresh

    existing = [signature.pattern for signature in storage.list_dynamic_signatures()]
    candidates, matched_defaults = extract_live_patterns(
        result.body, result.title, existing
    )
    verified = [
        signature
        for signature in BETBLOCKER_DEFAULT_PATTERNS
        if signature.pattern in matched_defaults
    ]
    added = storage.add_dynamic_signatures([*verified, *candidates])
    refresh.fetched = True
    refresh.new_patterns = added
    refresh.matched_defaults = matched_defaults
    refresh.total_patterns = len(storage.list_dynamic_signatures())
    _LOGGER.info(
        "BetBlocker signatures refreshed",
        extra={
            "event": "dynamic_signature",
            "domain": BETBLOCKER_BLOCK_URL,
            "count": str(len(added)),
        },
    )
    return refresh
