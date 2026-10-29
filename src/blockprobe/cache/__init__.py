# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cache construction.

BlockProbe ships exactly one cache: a small in-process TTL cache backed by the
local disk. It must work on every target, including phones, with no external
service, so there is no Redis or network backend. Probe verdicts are never
cached; cached views are invalidated through the database generation counter
(see ``cache/keys.py`` and ``docs/ARCHITECTURE.md``).
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
