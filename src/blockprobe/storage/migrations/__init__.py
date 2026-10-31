# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned SQLite migrations.

Each migration is a list of SQL statements. The schema version is stored in the
``meta`` table; the application refuses to open a database written by a newer
version instead of guessing what changed.
"""

from __future__ import annotations

SCHEMA_VERSION = 3

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS portals (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT NOT NULL DEFAULT 'IT',
            start_url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            is_default INTEGER NOT NULL DEFAULT 0,
            strategy TEXT NOT NULL DEFAULT 'auto',
            max_pages INTEGER NOT NULL DEFAULT 50,
            rate_limit_per_sec REAL NOT NULL DEFAULT 1.0,
            respect_robots INTEGER NOT NULL DEFAULT 0,
            exclude_domains_json TEXT NOT NULL DEFAULT '[]',
            include_regex_json TEXT NOT NULL DEFAULT '[]',
            last_refreshed_at INTEGER NOT NULL DEFAULT 0,
            last_attempt_at INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sites (
            domain TEXT PRIMARY KEY,
            first_seen_at INTEGER NOT NULL DEFAULT 0,
            last_seen_at INTEGER NOT NULL DEFAULT 0,
            excluded INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS site_hosts (
            domain TEXT NOT NULL REFERENCES sites(domain) ON DELETE CASCADE,
            host TEXT NOT NULL,
            PRIMARY KEY (domain, host)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS site_portals (
            domain TEXT NOT NULL REFERENCES sites(domain) ON DELETE CASCADE,
            portal_id TEXT NOT NULL,
            first_seen_at INTEGER NOT NULL DEFAULT 0,
            last_seen_at INTEGER NOT NULL DEFAULT 0,
            source_url TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'text',
            PRIMARY KEY (domain, portal_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS custom_sites (
            domain TEXT PRIMARY KEY REFERENCES sites(domain) ON DELETE CASCADE,
            added_at INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS refresh_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portal_id TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            finished_at INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            pages INTEGER NOT NULL DEFAULT 0,
            found INTEGER NOT NULL DEFAULT 0,
            added INTEGER NOT NULL DEFAULT 0,
            error TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            started_at INTEGER NOT NULL,
            finished_at INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            sources_json TEXT NOT NULL DEFAULT '{}',
            total INTEGER NOT NULL DEFAULT 0,
            app_version TEXT NOT NULL DEFAULT '',
            os TEXT NOT NULL DEFAULT '',
            network_context_json TEXT NOT NULL DEFAULT '{}',
            debug INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS site_results (
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            domain TEXT NOT NULL,
            verdict TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            confidence TEXT NOT NULL,
            blocker_hint TEXT,
            needs_review INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            checked_at INTEGER NOT NULL,
            PRIMARY KEY (run_id, domain)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS probe_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            domain TEXT NOT NULL,
            host TEXT NOT NULL,
            scheme TEXT NOT NULL,
            ip TEXT NOT NULL DEFAULT '',
            dns_status TEXT NOT NULL DEFAULT '',
            connect_ms INTEGER NOT NULL DEFAULT 0,
            http_status INTEGER,
            redirect_chain_json TEXT NOT NULL DEFAULT '[]',
            rule_id TEXT,
            body_sha256 TEXT NOT NULL DEFAULT '',
            error TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS consents (
            version TEXT PRIMARY KEY,
            accepted_at INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_site_hosts_host ON site_hosts(host)",
        "CREATE INDEX IF NOT EXISTS idx_site_portals_portal ON site_portals(portal_id)",
        "CREATE INDEX IF NOT EXISTS idx_results_run_verdict ON site_results(run_id, verdict)",
        "CREATE INDEX IF NOT EXISTS idx_results_domain ON site_results(domain)",
        "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_refresh_portal ON refresh_log(portal_id, started_at DESC)",
        """
        CREATE TRIGGER IF NOT EXISTS sites_keep_portal_domains
        BEFORE DELETE ON sites
        WHEN EXISTS (SELECT 1 FROM site_portals WHERE domain = OLD.domain)
          AND COALESCE((SELECT value FROM meta WHERE key = 'reset_unlocked'), '0') <> '1'
        BEGIN
            SELECT RAISE(ABORT, 'append-only: use the guarded reset');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS site_portals_no_delete
        BEFORE DELETE ON site_portals
        WHEN COALESCE((SELECT value FROM meta WHERE key = 'reset_unlocked'), '0') <> '1'
        BEGIN
            SELECT RAISE(ABORT, 'append-only: use the guarded reset');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS site_hosts_no_delete
        BEFORE DELETE ON site_hosts
        WHEN EXISTS (SELECT 1 FROM site_portals WHERE domain = OLD.domain)
          AND COALESCE((SELECT value FROM meta WHERE key = 'reset_unlocked'), '0') <> '1'
        BEGIN
            SELECT RAISE(ABORT, 'append-only: use the guarded reset');
        END
        """,
    ],
    2: [
        # Run-level control-check timeline and the computed interference risk.
        # Existing databases keep their history and gain an empty assessment.
        "ALTER TABLE runs ADD COLUMN health_json TEXT NOT NULL DEFAULT '{}'",
    ],
    3: [
        # Append-only patterns learned from the live BetBlocker block page.
        # A normal run may add or re-see a pattern, never delete one; the
        # explicit privacy wipe unlocks the guard.
        """
        CREATE TABLE IF NOT EXISTS dynamic_signatures (
            pattern TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'medium',
            blocker_hint TEXT,
            needs_review INTEGER NOT NULL DEFAULT 0,
            first_seen_at INTEGER NOT NULL DEFAULT 0,
            last_seen_at INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_dynamic_signatures_source "
        "ON dynamic_signatures(source, last_seen_at DESC)",
        """
        CREATE TRIGGER IF NOT EXISTS dynamic_signatures_no_delete
        BEFORE DELETE ON dynamic_signatures
        WHEN COALESCE((SELECT value FROM meta WHERE key = 'reset_unlocked'), '0') <> '1'
        BEGIN
            SELECT RAISE(ABORT, 'append-only: use the guarded privacy wipe');
        END
        """,
    ],
}
