# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cache key construction.

A key can never be served across a schema change or a database write because
both ``schema_version`` and ``db_generation`` are part of every key string.
"""

from __future__ import annotations

import hashlib

CACHE_SCHEMA_VERSION = 1
KEY_PREFIX = "bp"


def cache_key(
    namespace: str,
    generation: int,
    *parts: object,
    schema_version: int = CACHE_SCHEMA_VERSION,
) -> str:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"{KEY_PREFIX}:s{schema_version}:g{generation}:{namespace}:{digest}"


def tag_index_key(tag: str) -> str:
    return f"{KEY_PREFIX}:tag:{tag}"
