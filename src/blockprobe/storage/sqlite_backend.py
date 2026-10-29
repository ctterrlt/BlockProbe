# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite storage backend.

The database is the source of truth. WAL mode is enabled, every query is
parameterised, the append-only triggers from the data model are installed and
the cache generation counter is incremented in the same transaction as any
write that can affect a cached view.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from blockprobe import __version__
from blockprobe.core.enums import Confidence, Origin, RefreshStatus, RunStatus, Verdict
from blockprobe.core.errors import StorageError
from blockprobe.core.models import (
    CapturedSignature,
    ConsentRecord,
    DynamicSignature,
    ExtractedDomain,
    Portal,
    ProbeAttemptRecord,
    RefreshDiff,
    ResultRow,
    RunRecord,
    RunSummary,
    ScanSelection,
    Site,
    utc_now,
)
from blockprobe.storage.base import (
    CustomDiff,
    ResetReport,
    ResultFilter,
    Storage,
    WipeReport,
)
from blockprobe.storage.migrations import MIGRATIONS, SCHEMA_VERSION

_LOGGER = logging.getLogger("blockprobe.storage.sqlite")


def _json_loads(value: str | bytes | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _split_joined(value: str | None) -> list[str]:
    if not value:
        return []
    return [item for item in str(value).split(",") if item]


class SQLiteStorage(Storage):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._generation = 0

    # ---------------------------------------------------------------- lifecycle

    def initialize(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                connection = sqlite3.connect(
                    self.path,
                    check_same_thread=False,
                    isolation_level=None,
                    timeout=10,
                )
            except sqlite3.Error as exc:
                raise StorageError(f"Could not open the database at {self.path}: {exc}") from exc
            connection.row_factory = sqlite3.Row
            self._conn = connection
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute("PRAGMA busy_timeout=5000")
                self._migrate(connection)
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'db_generation'"
                ).fetchone()
                self._generation = int(row["value"]) if row else 1
            except sqlite3.Error as exc:
                connection.close()
                self._conn = None
                raise StorageError(f"Could not initialise the database: {exc}") from exc

    def _migrate(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        current = int(row["value"]) if row else 0
        if current > SCHEMA_VERSION:
            raise StorageError(
                f"The database uses schema version {current}, but this build supports "
                f"version {SCHEMA_VERSION}. Update BlockProbe."
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            statements = MIGRATIONS.get(version)
            if not statements:
                continue
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise StorageError(f"Migration {version} failed: {exc}") from exc
        connection.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('db_generation', '1')"
        )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError("The database is not open.")
        return self._conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connection()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                if "append-only" in str(exc):
                    raise StorageError(
                        "The stored website list is append-only. Use the guarded reset."
                    ) from exc
                raise StorageError(str(exc)) from exc
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _read(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return list(self._connection().execute(query, parameters).fetchall())
            except sqlite3.Error as exc:
                raise StorageError(str(exc)) from exc

    def _scalar(self, query: str, parameters: tuple[Any, ...] = (), default: Any = 0) -> Any:
        rows = self._read(query, parameters)
        if not rows or rows[0][0] is None:
            return default
        return rows[0][0]

    @property
    def generation(self) -> int:
        return self._generation

    def restart_generation(self) -> int:
        with self._write() as connection:
            connection.execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                "WHERE key = 'db_generation'"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_generation'"
            ).fetchone()
            self._generation = int(row["value"]) if row else self._generation + 1
        return self._generation

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._read("SELECT value FROM meta WHERE key = ?", (key,))
        return row[0]["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # -------------------------------------------------------- dynamic signatures

    def add_dynamic_signatures(self, signatures: list[DynamicSignature]) -> list[str]:
        if not signatures:
            return []
        added: list[str] = []
        now = utc_now()
        with self._write() as connection:
            changed = False
            for signature in signatures:
                pattern = signature.pattern.strip()
                if not pattern:
                    continue
                existing = connection.execute(
                    "SELECT 1 FROM dynamic_signatures WHERE pattern = ?", (pattern,)
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO dynamic_signatures(
                            pattern, source, note, confidence, blocker_hint,
                            needs_review, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pattern,
                            signature.source,
                            signature.note,
                            signature.confidence.value
                            if isinstance(signature.confidence, Confidence)
                            else str(signature.confidence),
                            signature.blocker_hint,
                            1 if signature.needs_review else 0,
                            signature.first_seen_at or now,
                            now,
                        ),
                    )
                    added.append(pattern)
                else:
                    connection.execute(
                        "UPDATE dynamic_signatures SET last_seen_at = ? WHERE pattern = ?",
                        (now, pattern),
                    )
                changed = True
            if changed:
                connection.execute(
                    "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                    "WHERE key = 'db_generation'"
                )
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'db_generation'"
                ).fetchone()
                self._generation = int(row["value"]) if row else self._generation + 1
        return added

    def list_dynamic_signatures(self) -> list[DynamicSignature]:
        rows = self._read(
            "SELECT * FROM dynamic_signatures ORDER BY first_seen_at, pattern"
        )
        signatures: list[DynamicSignature] = []
        for row in rows:
            try:
                confidence = Confidence(str(row["confidence"]))
            except ValueError:
                confidence = Confidence.MEDIUM
            signatures.append(
                DynamicSignature(
                    pattern=str(row["pattern"]),
                    source=str(row["source"] or ""),
                    note=str(row["note"] or ""),
                    confidence=confidence,
                    blocker_hint=row["blocker_hint"],
                    needs_review=bool(row["needs_review"]),
                    first_seen_at=int(row["first_seen_at"] or 0),
                    last_seen_at=int(row["last_seen_at"] or 0),
                )
            )
        return signatures

    # ------------------------------------------------------------------- portals

    def upsert_portals(self, portals: list[Portal]) -> None:
        with self._write() as connection:
            for portal in portals:
                connection.execute(
                    """
                    INSERT INTO portals (
                        id, name, country, start_url, enabled, is_default, strategy,
                        max_pages, rate_limit_per_sec, respect_robots,
                        exclude_domains_json, include_regex_json,
                        last_refreshed_at, last_attempt_at, last_error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        country = excluded.country,
                        start_url = excluded.start_url,
                        enabled = excluded.enabled,
                        is_default = excluded.is_default,
                        strategy = excluded.strategy,
                        max_pages = excluded.max_pages,
                        rate_limit_per_sec = excluded.rate_limit_per_sec,
                        respect_robots = excluded.respect_robots,
                        exclude_domains_json = excluded.exclude_domains_json,
                        include_regex_json = excluded.include_regex_json
                    """,
                    (
                        portal.uuid,
                        portal.name,
                        portal.country,
                        portal.start_url,
                        1 if portal.enabled else 0,
                        1 if portal.default else 0,
                        portal.strategy,
                        portal.max_pages,
                        portal.rate_limit_per_sec,
                        1 if portal.respect_robots else 0,
                        json.dumps(list(portal.exclude_domains)),
                        json.dumps(list(portal.include_regex)),
                    ),
                )

    def _portal_from_row(self, row: sqlite3.Row) -> Portal:
        return Portal(
            uuid=str(row["id"]),
            name=str(row["name"]),
            start_url=str(row["start_url"]),
            country=str(row["country"]),
            enabled=bool(row["enabled"]),
            default=bool(row["is_default"]),
            strategy=str(row["strategy"]),
            max_pages=int(row["max_pages"]),
            rate_limit_per_sec=float(row["rate_limit_per_sec"]),
            respect_robots=bool(row["respect_robots"]),
            exclude_domains=tuple(_json_loads(row["exclude_domains_json"], [])),
            include_regex=tuple(_json_loads(row["include_regex_json"], [])),
            last_refreshed_at=int(row["last_refreshed_at"] or 0),
            last_attempt_at=int(row["last_attempt_at"] or 0),
            last_error=row["last_error"],
            source_path=None,
        )

    def list_portals(self) -> list[Portal]:
        rows = self._read(
            "SELECT * FROM portals ORDER BY is_default DESC, name COLLATE NOCASE"
        )
        return [self._portal_from_row(row) for row in rows]

    def get_portal(self, portal_id: str) -> Portal | None:
        rows = self._read("SELECT * FROM portals WHERE id = ?", (portal_id,))
        return self._portal_from_row(rows[0]) if rows else None

    def record_refresh(self, diff: RefreshDiff) -> None:
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO refresh_log(
                    portal_id, started_at, finished_at, status, pages, found, added, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diff.portal_id,
                    diff.started_at,
                    diff.finished_at,
                    diff.status.value,
                    diff.pages,
                    diff.found,
                    diff.added,
                    diff.error,
                ),
            )
            if diff.status is RefreshStatus.OK:
                connection.execute(
                    """
                    UPDATE portals
                    SET last_refreshed_at = ?, last_attempt_at = ?, last_error = NULL
                    WHERE id = ?
                    """,
                    (diff.finished_at, diff.finished_at, diff.portal_id),
                )
            elif diff.status in {RefreshStatus.ERROR, RefreshStatus.EMPTY}:
                connection.execute(
                    "UPDATE portals SET last_attempt_at = ?, last_error = ? WHERE id = ?",
                    (diff.finished_at, diff.error or diff.status.value, diff.portal_id),
                )

    def add_portal_domains(
        self,
        portal_id: str,
        candidates: list[ExtractedDomain],
        source_url: str,
    ) -> tuple[list[str], list[str]]:
        now = utc_now()
        new_domains: list[str] = []
        known_domains: list[str] = []
        best: dict[str, ExtractedDomain] = {}
        for candidate in candidates:
            current = best.get(candidate.domain)
            if current is None:
                best[candidate.domain] = candidate
                continue
            current_is_table = current.origin is Origin.TABLE_TEXT
            candidate_is_table = candidate.origin is Origin.TABLE_TEXT
            better_origin = candidate_is_table and not current_is_table
            same_origin_longer = (
                candidate_is_table == current_is_table
                and len(candidate.host) > len(current.host)
            )
            if better_origin or same_origin_longer:
                best[candidate.domain] = candidate

        with self._write() as connection:
            for domain, candidate in best.items():
                inserted_site = connection.execute(
                    "INSERT OR IGNORE INTO sites(domain, first_seen_at, last_seen_at, excluded) "
                    "VALUES (?, ?, ?, 0)",
                    (domain, now, now),
                ).rowcount
                if not inserted_site:
                    connection.execute(
                        "UPDATE sites SET last_seen_at = ? WHERE domain = ?", (now, domain)
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO site_hosts(domain, host) VALUES (?, ?)",
                    (domain, candidate.host),
                )
                inserted_link = connection.execute(
                    """
                    INSERT OR IGNORE INTO site_portals(
                        domain, portal_id, first_seen_at, last_seen_at, source_url, origin
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        domain,
                        portal_id,
                        now,
                        now,
                        candidate.source_url or source_url,
                        candidate.origin.value,
                    ),
                ).rowcount
                if inserted_link:
                    new_domains.append(domain)
                else:
                    known_domains.append(domain)
                    connection.execute(
                        """
                        UPDATE site_portals
                        SET last_seen_at = ?, source_url = ?, origin = ?
                        WHERE domain = ? AND portal_id = ?
                        """,
                        (now, candidate.source_url or source_url, candidate.origin.value, domain, portal_id),
                    )
            if best:
                connection.execute(
                    "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                    "WHERE key = 'db_generation'"
                )
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'db_generation'"
                ).fetchone()
                self._generation = int(row["value"]) if row else self._generation + 1
        return sorted(new_domains), sorted(known_domains)

    # --------------------------------------------------------------------- sites

    def _site_from_row(self, row: sqlite3.Row) -> Site:
        hosts = _split_joined(row["hosts"])
        names = _split_joined(row["portal_names"])
        portal_ids = _split_joined(row["portal_ids"])
        return Site(
            domain=str(row["domain"]),
            hosts=hosts,
            portal_ids=portal_ids,
            portal_names=names,
            first_seen_at=int(row["first_seen_at"] or 0),
            last_seen_at=int(row["last_seen_at"] or 0),
            excluded=bool(row["excluded"]),
            is_custom=bool(row["is_custom"]),
        )

    def list_sites(
        self,
        portal_ids: list[str] | None = None,
        with_custom: bool = True,
        include_excluded: bool = False,
    ) -> list[Site]:
        where: list[str] = []
        parameters: list[Any] = []
        if portal_ids is not None:
            if not portal_ids and not with_custom:
                return []
            parts: list[str] = []
            if portal_ids:
                placeholders = ",".join("?" for _ in portal_ids)
                parts.append(f"sp.portal_id IN ({placeholders})")
                parameters.extend(portal_ids)
            if with_custom:
                parts.append("cs.domain IS NOT NULL")
            where.append("(" + " OR ".join(parts) + ")")
        if not include_excluded:
            where.append("s.excluded = 0")
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        query = f"""
            SELECT
                s.domain AS domain,
                s.first_seen_at AS first_seen_at,
                s.last_seen_at AS last_seen_at,
                s.excluded AS excluded,
                MAX(CASE WHEN cs.domain IS NOT NULL THEN 1 ELSE 0 END) AS is_custom,
                GROUP_CONCAT(DISTINCT sh.host) AS hosts,
                GROUP_CONCAT(DISTINCT p.name) AS portal_names,
                GROUP_CONCAT(DISTINCT sp.portal_id) AS portal_ids
            FROM sites s
            LEFT JOIN site_hosts sh ON sh.domain = s.domain
            LEFT JOIN site_portals sp ON sp.domain = s.domain
            LEFT JOIN portals p ON p.id = sp.portal_id
            LEFT JOIN custom_sites cs ON cs.domain = s.domain
            {where_sql}
            GROUP BY s.domain
            ORDER BY s.domain COLLATE NOCASE
        """
        try:
            rows = self._read(query, tuple(parameters))
        except StorageError:
            # json_group_array FILTER requires a recent SQLite; fall back to a
            # portable query if the host has an older library.
            return self._list_sites_portable(portal_ids, with_custom, include_excluded)
        return [self._site_from_row(row) for row in rows]

    def _list_sites_portable(
        self,
        portal_ids: list[str] | None,
        with_custom: bool,
        include_excluded: bool,
    ) -> list[Site]:
        rows = self._read("SELECT domain FROM sites ORDER BY domain COLLATE NOCASE")
        result: list[Site] = []
        for row in rows:
            domain = str(row["domain"])
            hosts = [
                str(item["host"])
                for item in self._read(
                    "SELECT host FROM site_hosts WHERE domain = ? ORDER BY host", (domain,)
                )
            ]
            links = self._read(
                """
                SELECT sp.portal_id, p.name FROM site_portals sp
                LEFT JOIN portals p ON p.id = sp.portal_id
                WHERE sp.domain = ?
                """,
                (domain,),
            )
            portal_id_list = [str(link["portal_id"]) for link in links]
            portal_names = [str(link["name"]) for link in links if link["name"]]
            custom = bool(
                self._scalar("SELECT 1 FROM custom_sites WHERE domain = ?", (domain,), 0)
            )
            if portal_ids is not None and not (
                (set(portal_ids) & set(portal_id_list)) or (with_custom and custom)
            ):
                continue
            if not include_excluded and bool(
                self._scalar("SELECT excluded FROM sites WHERE domain = ?", (domain,), 0)
            ):
                continue
            site_row = self._read("SELECT * FROM sites WHERE domain = ?", (domain,))[0]
            result.append(
                Site(
                    domain=domain,
                    hosts=hosts,
                    portal_ids=portal_id_list,
                    portal_names=portal_names,
                    first_seen_at=int(site_row["first_seen_at"] or 0),
                    last_seen_at=int(site_row["last_seen_at"] or 0),
                    excluded=bool(site_row["excluded"]),
                    is_custom=custom,
                )
            )
        return result

    def select_sites(self, selection: ScanSelection) -> list[Site]:
        if selection.all_sources:
            return self.list_sites(None, with_custom=True)
        return self.list_sites(
            selection.portal_ids_only,
            with_custom=selection.with_custom,
        )

    def count_sites(self) -> int:
        return int(self._scalar("SELECT COUNT(*) FROM sites", (), 0))

    def count_custom_sites(self) -> int:
        return int(self._scalar("SELECT COUNT(*) FROM custom_sites", (), 0))

    def stale_site_count(self, days: int = 90) -> int:
        cutoff = utc_now() - days * 86400
        return int(
            self._scalar(
                """
                SELECT COUNT(*) FROM sites s
                WHERE s.excluded = 0
                  AND EXISTS (SELECT 1 FROM site_portals sp WHERE sp.domain = s.domain)
                  AND s.last_seen_at < ?
                """,
                (cutoff,),
                0,
            )
        )

    def add_custom_domains(
        self,
        domains: list[tuple[str, str]],
        note: str = "",
    ) -> CustomDiff:
        now = utc_now()
        diff = CustomDiff()
        with self._write() as connection:
            for domain, host in domains:
                connection.execute(
                    "INSERT OR IGNORE INTO sites(domain, first_seen_at, last_seen_at, excluded) "
                    "VALUES (?, ?, ?, 0)",
                    (domain, now, now),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO site_hosts(domain, host) VALUES (?, ?)",
                    (domain, host),
                )
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO custom_sites(domain, added_at, note) VALUES (?, ?, ?)",
                    (domain, now, note),
                ).rowcount
                if inserted:
                    diff.added.append(domain)
                else:
                    diff.existing.append(domain)
            if diff.added:
                connection.execute(
                    "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                    "WHERE key = 'db_generation'"
                )
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'db_generation'"
                ).fetchone()
                self._generation = int(row["value"]) if row else self._generation + 1
        return diff

    def list_custom_sites(self) -> list[Site]:
        return self.list_sites(None, with_custom=True)

    def remove_custom_site(self, domain: str) -> bool:
        with self._write() as connection:
            removed = connection.execute(
                "DELETE FROM custom_sites WHERE domain = ?", (domain,)
            ).rowcount
            if not removed:
                return False
            linked = connection.execute(
                "SELECT 1 FROM site_portals WHERE domain = ? LIMIT 1", (domain,)
            ).fetchone()
            if not linked:
                connection.execute("DELETE FROM site_hosts WHERE domain = ?", (domain,))
                connection.execute("DELETE FROM sites WHERE domain = ?", (domain,))
            connection.execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                "WHERE key = 'db_generation'"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_generation'"
            ).fetchone()
            self._generation = int(row["value"]) if row else self._generation + 1
        return True

    def set_excluded(self, domain: str, excluded: bool) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE sites SET excluded = ? WHERE domain = ?",
                (1 if excluded else 0, domain),
            )
            connection.execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                "WHERE key = 'db_generation'"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_generation'"
            ).fetchone()
            self._generation = int(row["value"]) if row else self._generation + 1

    # ---------------------------------------------------------------------- runs

    def _run_from_row(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=str(row["id"]),
            started_at=int(row["started_at"] or 0),
            finished_at=int(row["finished_at"] or 0),
            status=RunStatus(str(row["status"])),
            sources=_json_loads(row["sources_json"], {}),
            total=int(row["total"] or 0),
            app_version=str(row["app_version"] or ""),
            os_name=str(row["os"] or ""),
            network_context=_json_loads(row["network_context_json"], {}),
            health=_json_loads(row["health_json"], {}),
            debug=bool(row["debug"]),
        )

    def create_run(
        self,
        selection: ScanSelection,
        total: int,
        app_version: str,
        os_name: str,
        network_context: dict[str, Any],
        debug: bool,
    ) -> RunRecord:
        run_id = str(uuid.uuid4())
        started_at = utc_now()
        portal_labels: dict[str, str] = {}
        for portal in self.list_portals():
            portal_labels[portal.uuid] = portal.name
        sources = selection.to_dict(portal_labels)
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    id, started_at, finished_at, status, sources_json, total,
                    app_version, os, network_context_json, debug
                ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    started_at,
                    RunStatus.PENDING.value,
                    json.dumps(sources),
                    total,
                    app_version or __version__,
                    os_name,
                    json.dumps(network_context),
                    1 if debug else 0,
                ),
            )
        return RunRecord(
            id=run_id,
            started_at=started_at,
            status=RunStatus.PENDING,
            sources=sources,
            total=total,
            app_version=app_version or __version__,
            os_name=os_name,
            network_context=network_context,
            debug=debug,
        )

    def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        finished_at: int | None = None,
    ) -> None:
        with self._write() as connection:
            if finished_at is None:
                connection.execute(
                    "UPDATE runs SET status = ? WHERE id = ?", (status.value, run_id)
                )
            else:
                connection.execute(
                    "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
                    (status.value, finished_at, run_id),
                )

    def save_run_health(self, run_id: str, health: dict[str, Any]) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE runs SET health_json = ? WHERE id = ?",
                (json.dumps(health), run_id),
            )
            connection.execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                "WHERE key = 'db_generation'"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_generation'"
            ).fetchone()
            self._generation = int(row["value"]) if row else self._generation + 1

    def get_run(self, run_id: str) -> RunRecord | None:
        rows = self._read("SELECT * FROM runs WHERE id = ?", (run_id,))
        return self._run_from_row(rows[0]) if rows else None

    def latest_run(self) -> RunRecord | None:
        rows = self._read("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
        return self._run_from_row(rows[0]) if rows else None

    def list_runs(self, limit: int = 20) -> list[RunRecord]:
        rows = self._read(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (max(1, limit),)
        )
        return [self._run_from_row(row) for row in rows]

    def save_site_result(
        self,
        result: ResultRow,
        attempts: list[ProbeAttemptRecord],
    ) -> None:
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO site_results(
                    run_id, domain, verdict, reason_code, confidence, blocker_hint,
                    needs_review, evidence_json, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, domain) DO UPDATE SET
                    verdict = excluded.verdict,
                    reason_code = excluded.reason_code,
                    confidence = excluded.confidence,
                    blocker_hint = excluded.blocker_hint,
                    needs_review = excluded.needs_review,
                    evidence_json = excluded.evidence_json,
                    checked_at = excluded.checked_at
                """,
                (
                    result.run_id,
                    result.domain,
                    result.verdict.value if isinstance(result.verdict, Verdict) else result.verdict,
                    result.reason_code,
                    result.confidence.value
                    if isinstance(result.confidence, Confidence)
                    else result.confidence,
                    result.blocker_hint,
                    1 if result.needs_review else 0,
                    json.dumps(result.evidence),
                    result.checked_at,
                ),
            )
            connection.execute(
                "DELETE FROM probe_attempts WHERE run_id = ? AND domain = ?",
                (result.run_id, result.domain),
            )
            for attempt in attempts:
                connection.execute(
                    """
                    INSERT INTO probe_attempts(
                        run_id, domain, host, scheme, ip, dns_status, connect_ms,
                        http_status, redirect_chain_json, rule_id, body_sha256, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.run_id,
                        attempt.domain,
                        attempt.host,
                        attempt.scheme,
                        attempt.ip,
                        attempt.dns_status,
                        attempt.connect_ms,
                        attempt.http_status,
                        json.dumps(attempt.redirect_chain),
                        attempt.rule_id,
                        attempt.body_sha256,
                        attempt.error,
                    ),
                )
            connection.execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                "WHERE key = 'db_generation'"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_generation'"
            ).fetchone()
            self._generation = int(row["value"]) if row else self._generation + 1

    def _result_from_row(self, row: sqlite3.Row) -> ResultRow:
        evidence = _json_loads(row["evidence_json"], {})
        return ResultRow(
            run_id=str(row["run_id"]),
            domain=str(row["domain"]),
            verdict=Verdict(str(row["verdict"])),
            reason_code=str(row["reason_code"]),
            confidence=Confidence(str(row["confidence"])),
            blocker_hint=row["blocker_hint"],
            needs_review=bool(row["needs_review"]),
            evidence=evidence,
            checked_at=int(row["checked_at"] or 0),
            hosts=[str(host) for host in _json_loads(row["hosts"], [])],
            sources=[str(source) for source in _json_loads(row["sources"], [])],
            title=str(evidence.get("title") or ""),
            redirect_chain=[str(url) for url in evidence.get("redirect_chain") or []],
        )

    def get_results(self, run_id: str, filters: ResultFilter | None = None) -> list[ResultRow]:
        filters = filters or ResultFilter()
        where = ["r.run_id = ?"]
        parameters: list[Any] = [run_id]
        if filters.verdict is not None:
            where.append("r.verdict = ?")
            parameters.append(filters.verdict.value)
        if filters.leaks_only:
            where.append("r.verdict = ?")
            parameters.append(Verdict.OPENED.value)
        if filters.needs_review is not None:
            where.append("r.needs_review = ?")
            parameters.append(1 if filters.needs_review else 0)
        if filters.search:
            where.append("r.domain LIKE ?")
            parameters.append(f"%{filters.search.lower()}%")
        where_sql = " AND ".join(where)
        query = f"""
            SELECT
                r.run_id AS run_id,
                r.domain AS domain,
                r.verdict AS verdict,
                r.reason_code AS reason_code,
                r.confidence AS confidence,
                r.blocker_hint AS blocker_hint,
                r.needs_review AS needs_review,
                r.evidence_json AS evidence_json,
                r.checked_at AS checked_at,
                GROUP_CONCAT(DISTINCT sh.host) AS hosts,
                GROUP_CONCAT(DISTINCT p.name) AS portal_names,
                MAX(CASE WHEN cs.domain IS NOT NULL THEN 1 ELSE 0 END) AS is_custom
            FROM site_results r
            LEFT JOIN site_hosts sh ON sh.domain = r.domain
            LEFT JOIN site_portals sp ON sp.domain = r.domain
            LEFT JOIN portals p ON p.id = sp.portal_id
            LEFT JOIN custom_sites cs ON cs.domain = r.domain
            WHERE {where_sql}
            GROUP BY r.run_id, r.domain
            ORDER BY r.verdict DESC, r.domain COLLATE NOCASE
        """
        rows = self._read(query, tuple(parameters))
        results: list[ResultRow] = []
        for row in rows:
            sources = _split_joined(row["portal_names"])
            if row["is_custom"]:
                sources.append("Custom")
            data = dict(row)
            data["hosts"] = json.dumps(_split_joined(row["hosts"]))
            data["sources"] = json.dumps(sources)
            results.append(self._result_from_row(_RowProxy(data)))
        if filters.sources:
            wanted = {source.lower() for source in filters.sources}
            results = [
                result
                for result in results
                if wanted & {source.lower() for source in result.sources}
            ]
        return results

    def get_run_summary(self, run_id: str) -> RunSummary:
        run = self.get_run(run_id)
        rows = self._read(
            """
            SELECT verdict, COUNT(*) AS count FROM site_results
            WHERE run_id = ? GROUP BY verdict
            """,
            (run_id,),
        )
        counts = {str(row["verdict"]): int(row["count"]) for row in rows}
        needs_review = int(
            self._scalar(
                "SELECT COUNT(*) FROM site_results WHERE run_id = ? AND needs_review = 1",
                (run_id,),
                0,
            )
        )
        return RunSummary(
            run_id=run_id,
            status=run.status if run else RunStatus.PENDING,
            total=run.total if run else 0,
            started_at=run.started_at if run else 0,
            finished_at=run.finished_at if run else 0,
            blocked=counts.get(Verdict.BLOCKED.value, 0),
            unreachable=counts.get(Verdict.UNREACHABLE.value, 0),
            opened=counts.get(Verdict.OPENED.value, 0),
            needs_review=needs_review,
        )

    def count_results(self, run_id: str) -> int:
        return int(
            self._scalar("SELECT COUNT(*) FROM site_results WHERE run_id = ?", (run_id,), 0)
        )

    # -------------------------------------------------------------------- consent

    def get_consent(self) -> ConsentRecord | None:
        rows = self._read("SELECT * FROM consents ORDER BY accepted_at DESC LIMIT 1")
        if not rows:
            return None
        return ConsentRecord(
            version=str(rows[0]["version"]),
            accepted_at=int(rows[0]["accepted_at"] or 0),
        )

    def save_consent(self, version: str) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO consents(version, accepted_at) VALUES (?, ?) "
                "ON CONFLICT(version) DO UPDATE SET accepted_at = excluded.accepted_at",
                (version, utc_now()),
            )

    def clear_consent(self) -> None:
        with self._write() as connection:
            connection.execute("DELETE FROM consents")

    # -------------------------------------------------------------------- backups

    def backup_database(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(destination)
            try:
                self._connection().backup(connection)
                connection.commit()
            finally:
                connection.close()
        _LOGGER.info(
            "database backup written", extra={"event": "backup", "domain": str(destination)}
        )
        return destination

    def export_domain_list(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self._read(
            """
            SELECT s.domain, MAX(CASE WHEN cs.domain IS NOT NULL THEN 1 ELSE 0 END) AS is_custom
            FROM sites s LEFT JOIN custom_sites cs ON cs.domain = s.domain
            GROUP BY s.domain ORDER BY s.domain COLLATE NOCASE
            """
        )
        lines = [
            "# BlockProbe domain list export",
            f"# Generated: {utc_now()}",
            "# One domain per line. A trailing #c marks a custom website.",
        ]
        for row in rows:
            suffix = " #c" if row["is_custom"] else ""
            lines.append(f"{row['domain']}{suffix}")
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return destination

    def reset_list(
        self,
        backup_dir: Path,
        include_custom: bool,
        backup_required: bool = True,
    ) -> ResetReport:
        import datetime as _datetime

        stamp = _datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        report = ResetReport(include_custom=include_custom)
        if backup_required:
            report.backup_database = self.backup_database(
                backup_dir / f"blockprobe-reset-{stamp}.sqlite3"
            )
            report.backup_domains = self.export_domain_list(
                backup_dir / f"blockprobe-domains-{stamp}.txt"
            )

        with self._write() as connection:
            report.removed_links = int(
                connection.execute("SELECT COUNT(*) FROM site_portals").fetchone()[0]
            )
            total_hosts = int(connection.execute("SELECT COUNT(*) FROM site_hosts").fetchone()[0])
            total_sites = int(connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0])
            custom_total = int(
                connection.execute("SELECT COUNT(*) FROM custom_sites").fetchone()[0]
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('reset_unlocked', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'"
            )
            connection.execute("DELETE FROM site_portals")
            if include_custom:
                connection.execute("DELETE FROM custom_sites")
                connection.execute("DELETE FROM site_hosts")
                connection.execute("DELETE FROM sites")
                report.removed_hosts = total_hosts
                report.removed_sites = total_sites
                report.removed_custom = custom_total
            else:
                kept = [
                    str(row["domain"])
                    for row in connection.execute("SELECT domain FROM custom_sites").fetchall()
                ]
                report.kept_custom = len(kept)
                if kept:
                    placeholders = ",".join("?" for _ in kept)
                    removed_hosts = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM site_hosts WHERE domain NOT IN ({placeholders})",
                            kept,
                        ).fetchone()[0]
                    )
                    removed_sites = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM sites WHERE domain NOT IN ({placeholders})",
                            kept,
                        ).fetchone()[0]
                    )
                    connection.execute(
                        f"DELETE FROM site_hosts WHERE domain NOT IN ({placeholders})", kept
                    )
                    connection.execute(
                        f"DELETE FROM sites WHERE domain NOT IN ({placeholders})", kept
                    )
                else:
                    removed_hosts = total_hosts
                    removed_sites = total_sites
                    connection.execute("DELETE FROM site_hosts")
                    connection.execute("DELETE FROM sites")
                report.removed_hosts = removed_hosts
                report.removed_sites = removed_sites
            connection.execute(
                """
                UPDATE portals
                SET last_refreshed_at = 0, last_attempt_at = 0, last_error = NULL
                """
            )
            connection.execute("DELETE FROM meta WHERE key = 'reset_unlocked'")
            connection.execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                "WHERE key = 'db_generation'"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_generation'"
            ).fetchone()
            self._generation = int(row["value"]) if row else self._generation + 1
        _LOGGER.warning(
            "website list reset",
            extra={
                "event": "reset_list",
                "run_id": None,
                "domain": f"include_custom={include_custom}",
            },
        )
        return report

    def delete_all_data(self) -> WipeReport:
        report = WipeReport()
        with self._write() as connection:
            report.removed_results = int(
                connection.execute("SELECT COUNT(*) FROM site_results").fetchone()[0]
            )
            report.removed_runs = int(
                connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            )
            report.removed_sites = int(
                connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            )
            report.removed_custom = int(
                connection.execute("SELECT COUNT(*) FROM custom_sites").fetchone()[0]
            )
            report.removed_consents = int(
                connection.execute("SELECT COUNT(*) FROM consents").fetchone()[0]
            )
            report.removed_signatures = int(
                connection.execute("SELECT COUNT(*) FROM dynamic_signatures").fetchone()[0]
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('reset_unlocked', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'"
            )
            connection.execute("DELETE FROM probe_attempts")
            connection.execute("DELETE FROM site_results")
            connection.execute("DELETE FROM runs")
            connection.execute("DELETE FROM refresh_log")
            connection.execute("DELETE FROM custom_sites")
            connection.execute("DELETE FROM site_portals")
            connection.execute("DELETE FROM site_hosts")
            connection.execute("DELETE FROM sites")
            connection.execute("DELETE FROM consents")
            connection.execute("DELETE FROM dynamic_signatures")
            connection.execute(
                """
                UPDATE portals
                SET last_refreshed_at = 0, last_attempt_at = 0, last_error = NULL
                """
            )
            connection.execute("DELETE FROM meta WHERE key = 'reset_unlocked'")
            connection.execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                "WHERE key = 'db_generation'"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_generation'"
            ).fetchone()
            self._generation = int(row["value"]) if row else self._generation + 1
        _LOGGER.warning("all local data deleted", extra={"event": "delete_all_data"})
        return report

    def health_check(self) -> str:
        self._connection()
        size = self.path.stat().st_size if self.path.exists() else 0
        return (
            f"SQLite {sqlite3.sqlite_version} at {self.path} "
            f"({size} bytes, generation {self._generation})"
        )

    def save_signature(self, signature: CapturedSignature) -> Path:
        directory = self.path.parent / "signatures"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{signature.domain}.toml"
        path.write_text(signature.to_toml(), encoding="utf-8")
        return path


class _RowProxy(dict):
    """sqlite3.Row-like mapping for the portable result fallback."""

    def __getitem__(self, key: str) -> Any:  # type: ignore[override]
        return super().__getitem__(key)


def build_sqlite_storage(path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(path)
    storage.initialize()
    return storage
