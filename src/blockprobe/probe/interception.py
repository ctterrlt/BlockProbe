# SPDX-License-Identifier: AGPL-3.0-or-later
"""Known interception redirects and URL helpers.

The project does not ship invented block-page fingerprints. This module only
contains redirect destinations that are explicitly known to be blocker landing
pages. Matching one is direct evidence, so the classifier returns ``blocked``
without waiting for a body regex.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from blockprobe.core.enums import BlockerHint

# Host, path prefix, blocker hint. The path prefix is matched case-insensitively.
KNOWN_BLOCKER_REDIRECTS: tuple[tuple[str, str, BlockerHint], ...] = (
    ("betblocker.org", "/blocked", BlockerHint.BETBLOCKER),
)


def _host_matches(host: str, expected: str) -> bool:
    return host == expected or host.endswith("." + expected)


def known_blocker_for_url(url: str) -> BlockerHint | None:
    """Return the blocker hint when a URL is a known blocker landing page."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    path = parts.path.lower() or "/"
    for expected_host, path_prefix, hint in KNOWN_BLOCKER_REDIRECTS:
        if _host_matches(host, expected_host) and path.startswith(path_prefix):
            return hint
    return None


def first_known_blocker(urls: list[str]) -> BlockerHint | None:
    for url in urls:
        hint = known_blocker_for_url(url)
        if hint is not None:
            return hint
    return None
