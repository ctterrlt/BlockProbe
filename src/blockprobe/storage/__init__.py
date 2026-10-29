# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage construction.

BlockProbe ships exactly one storage backend: SQLite. It runs in-process on
every supported platform with no server, no Docker and no extra dependency, and
it is the only place where the append-only protections and the generation
counter live. There is no network storage backend.
"""

from __future__ import annotations

from blockprobe.core.config import Settings
from blockprobe.storage.base import (
    CustomDiff,
    ResetReport,
    ResultFilter,
    Storage,
    WipeReport,
)
from blockprobe.storage.sqlite_backend import SQLiteStorage

__all__ = [
    "CustomDiff",
    "ResetReport",
    "ResultFilter",
    "SQLiteStorage",
    "Storage",
    "WipeReport",
    "build_storage",
]


def build_storage(settings: Settings) -> Storage:
    """Open (and migrate) the local SQLite database."""
    storage = SQLiteStorage(settings.db_path)
    storage.initialize()
    return storage
