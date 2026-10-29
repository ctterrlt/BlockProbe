# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small in-process JSON TTL cache.

Values are serialized as JSON and held only in memory. This avoids executing
pickle data from a cache file and keeps cached views transient between runs.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from blockprobe.cache.base import Cache

_LOGGER = logging.getLogger("blockprobe.cache.embedded")


@dataclass(frozen=True)
class _Entry:
    payload: str
    expires_at: float | None
    tags: frozenset[str]


class EmbeddedTTLCache(Cache):
    """Thread-safe least-recently-used cache with optional entry expiry."""

    def __init__(self, directory: Path, size_limit_bytes: int = 128 * 1024 * 1024) -> None:
        # Keep the path for config/API compatibility; no cache files are written.
        self.directory = Path(directory)
        self._size_limit = max(1, size_limit_bytes)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._tag_keys: dict[str, set[str]] = {}
        self._size_bytes = 0
        self._available = True
        self._lock = RLock()

    @property
    def enabled(self) -> bool:
        return self._available

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return default
            if entry.expires_at is not None and entry.expires_at <= time.monotonic():
                self._remove(key)
                return default
            self._entries.move_to_end(key)
        try:
            return json.loads(entry.payload)
        except (TypeError, ValueError):  # defensive if memory is corrupted
            _LOGGER.debug("discarding invalid in-memory cache entry")
            self.delete(key)
            return default

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
        tags: tuple[str, ...] = (),
    ) -> None:
        try:
            payload = json.dumps(value, default=str, ensure_ascii=False)
            payload_size = len(payload.encode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as exc:
            _LOGGER.debug("cache serialization failed: %s", exc)
            return
        if payload_size > self._size_limit:
            self.delete(key)
            return
        normalized_tags = frozenset(tags)
        expires_at = None if ttl_seconds is None else time.monotonic() + max(0, ttl_seconds)
        with self._lock:
            self._remove(key)
            self._entries[key] = _Entry(payload, expires_at, normalized_tags)
            self._size_bytes += payload_size
            for tag in normalized_tags:
                self._tag_keys.setdefault(tag, set()).add(key)
            while self._size_bytes > self._size_limit and self._entries:
                self._remove(next(iter(self._entries)))

    def delete(self, key: str) -> None:
        with self._lock:
            self._remove(key)

    def delete_tag(self, tag: str) -> None:
        with self._lock:
            for key in tuple(self._tag_keys.get(tag, ())):
                self._remove(key)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._tag_keys.clear()
            self._size_bytes = 0

    def close(self) -> None:
        self.clear()
        self._available = False

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "embedded",
                "size_bytes": self._size_bytes,
                "size_limit_bytes": self._size_limit,
                "items": len(self._entries),
                "enabled": self._available,
            }

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        self._size_bytes -= len(entry.payload.encode("utf-8"))
        for tag in entry.tags:
            keys = self._tag_keys.get(tag)
            if keys is not None:
                keys.discard(key)
                if not keys:
                    del self._tag_keys[tag]
