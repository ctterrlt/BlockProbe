# SPDX-License-Identifier: AGPL-3.0-or-later
"""Embedded TTL cache backed by diskcache.

This is the default on every platform, including mobile: diskcache is pure
Python and SQLite-backed. Corrupt or expired entries are treated as a miss and
the caller reads the database instead.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from blockprobe.cache.base import Cache
from blockprobe.cache.keys import tag_index_key
from blockprobe.core.errors import CacheError

_LOGGER = logging.getLogger("blockprobe.cache.embedded")


class EmbeddedTTLCache(Cache):
    def __init__(self, directory: Path, size_limit_bytes: int = 128 * 1024 * 1024) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            import diskcache
        except ImportError as exc:  # pragma: no cover - dependency is in pyproject
            raise CacheError("The embedded cache requires the diskcache package") from exc
        self._cache = diskcache.Cache(
            str(self.directory),
            size_limit=size_limit_bytes,
            eviction_policy="least-recently-used",
        )
        self._available = True

    @property
    def enabled(self) -> bool:
        return self._available

    def get(self, key: str, default: Any = None) -> Any:
        try:
            raw = self._cache.get(key, default=None)
        except Exception as exc:  # pragma: no cover - disk failure fallback
            _LOGGER.debug("cache get failed: %s", exc)
            self._available = False
            return default
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
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
            self._cache.set(key, payload, expire=ttl_seconds)
            for tag in tags:
                index_key = tag_index_key(tag)
                raw = self._cache.get(index_key, default=None)
                try:
                    keys = set(json.loads(raw)) if raw else set()
                except (TypeError, ValueError):
                    keys = set()
                keys.add(key)
                self._cache.set(index_key, json.dumps(sorted(keys)), expire=None)
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("cache set failed: %s", exc)
            self._available = False

    def delete(self, key: str) -> None:
        try:
            self._cache.delete(key)
        except Exception:  # pragma: no cover
            pass

    def delete_tag(self, tag: str) -> None:
        index_key = tag_index_key(tag)
        try:
            raw = self._cache.get(index_key, default=None)
            keys = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            keys = []
        except Exception:  # pragma: no cover
            keys = []
        for key in keys:
            self.delete(str(key))
        self.delete(index_key)

    def clear(self) -> None:
        try:
            self._cache.clear()
        except Exception:  # pragma: no cover
            pass

    def close(self) -> None:
        try:
            self._cache.close()
        except Exception:  # pragma: no cover
            pass

    def stats(self) -> dict[str, Any]:
        try:
            return {
                "backend": "embedded",
                "directory": str(self.directory),
                "size_bytes": int(self._cache.volume()),
                "items": len(self._cache),
                "enabled": self._available,
            }
        except Exception:  # pragma: no cover
            return {"backend": "embedded", "directory": str(self.directory), "enabled": False}
