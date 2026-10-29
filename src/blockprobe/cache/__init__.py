# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cache construction.

BlockProbe ships one small in-process JSON TTL cache. Values are transient and
never written to disk; probe verdicts are never cached. Cached views are
invalidated through the database generation counter (see ``cache/keys.py``).
"""

from __future__ import annotations

from blockprobe.cache.base import Cache
from blockprobe.cache.embedded import EmbeddedTTLCache
from blockprobe.cache.keys import CACHE_SCHEMA_VERSION, cache_key, tag_index_key
from blockprobe.core.config import Settings

__all__ = [
    "CACHE_SCHEMA_VERSION",
    "Cache",
    "EmbeddedTTLCache",
    "build_cache",
    "cache_key",
    "tag_index_key",
]


def build_cache(settings: Settings) -> Cache:
    """Return the embedded cache sized according to the configuration."""
    return EmbeddedTTLCache(
        settings.cache_dir,
        size_limit_bytes=max(8, settings.config.cache.size_limit_mb) * 1024 * 1024,
    )
