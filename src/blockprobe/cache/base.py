# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cache interface.

Cached values are views only: portal lists, counts, run summaries and rendered
result rows. Probe verdicts and response bodies are never stored here. Every
key carries the schema version and the database generation to make stale
entries unreachable, and callers must fall back to the database on any error.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Cache(ABC):
    @property
    @abstractmethod
    def enabled(self) -> bool:
        ...

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        ...

    @abstractmethod
    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
        tags: tuple[str, ...] = (),
    ) -> None:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def delete_tag(self, tag: str) -> None:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        ...
