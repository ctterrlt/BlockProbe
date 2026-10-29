# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cache keys, TTL and tag invalidation (FR-10)."""

from __future__ import annotations

import time

from blockprobe.cache import build_cache
from blockprobe.cache.embedded import EmbeddedTTLCache
from blockprobe.cache.keys import CACHE_SCHEMA_VERSION, cache_key, tag_index_key
from blockprobe.core.config import Settings


def test_cache_key_contains_schema_and_generation() -> None:
    key = cache_key("sites", 7, "portal", "adm")
    assert key.startswith(f"bp:s{CACHE_SCHEMA_VERSION}:g7:sites:")
    assert key == cache_key("sites", 7, "portal", "adm")
    assert key != cache_key("sites", 8, "portal", "adm")
    assert key != cache_key("runs", 7, "portal", "adm")


def test_cache_key_is_stable_and_hashed() -> None:
    key = cache_key("results", 1, ["a", "b"], {"x": 1})
    assert "['a', 'b']" not in key
    assert key == cache_key("results", 1, ["a", "b"], {"x": 1})


def test_tag_index_key() -> None:
    assert tag_index_key("run:abc") == "bp:tag:run:abc"


def test_embedded_cache_set_get_and_delete(tmp_path) -> None:
    cache = EmbeddedTTLCache(tmp_path / "cache")
    cache.set("k", {"value": 1}, ttl_seconds=60)
    assert cache.get("k") == {"value": 1}
    cache.delete("k")
    assert cache.get("k") is None
    cache.close()


def test_embedded_cache_tag_invalidation(tmp_path) -> None:
    cache = EmbeddedTTLCache(tmp_path / "cache")
    cache.set("a", 1, ttl_seconds=60, tags=("run:r1",))
    cache.set("b", 2, ttl_seconds=60, tags=("run:r2",))
    cache.set("c", 3, ttl_seconds=60, tags=("run:r1", "run:r2"))
    assert cache.get("a") == 1
    cache.delete_tag("run:r1")
    assert cache.get("a") is None
    assert cache.get("c") is None
    assert cache.get("b") == 2
    cache.close()


def test_embedded_cache_ttl_expires(tmp_path) -> None:
    cache = EmbeddedTTLCache(tmp_path / "cache")
    cache.set("short", "value", ttl_seconds=0.2)
    assert cache.get("short") == "value"
    time.sleep(0.4)
    assert cache.get("short") is None
    cache.close()


def test_same_value_under_a_new_generation_is_unreachable(tmp_path) -> None:
    cache = EmbeddedTTLCache(tmp_path / "cache")
    old_key = cache_key("sites", 1, "all")
    new_key = cache_key("sites", 2, "all")
    cache.set(old_key, "stale", ttl_seconds=60)
    assert cache.get(old_key) == "stale"
    assert cache.get(new_key) is None
    cache.close()


def test_build_cache_returns_the_embedded_cache(temp_settings: Settings) -> None:
    cache = build_cache(temp_settings)
    assert isinstance(cache, EmbeddedTTLCache)
    stats = cache.stats()
    assert stats
    cache.close()
