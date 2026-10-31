# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite storage: append-only list, guarded reset, runs and migration (FR-4, FR-5, FR-14)."""

from __future__ import annotations

import sqlite3

import pytest

from blockprobe import __version__
from blockprobe.core.config import Settings
from blockprobe.core.enums import Confidence, Origin, RunStatus, Verdict
from blockprobe.core.models import (
    ExtractedDomain,
    Portal,
    ProbeAttemptRecord,
    ResultRow,
    ScanSelection,
    utc_now,
)
from blockprobe.storage import build_storage


@pytest.fixture()
def storage(temp_settings: Settings):
    backend = build_storage(temp_settings)
    yield backend
    backend.close()


def candidate(host: str, domain: str) -> ExtractedDomain:
    return ExtractedDomain(host=host, domain=domain, origin=Origin.TABLE_TEXT)


def make_portal() -> Portal:
    return Portal(
        uuid="11111111-2222-3333-4444-555555555555",
        name="Test portal",
        start_url="https://portal.example/list",
    )


def make_result(run_id: str, domain: str, verdict: Verdict) -> ResultRow:
    reason = {
        Verdict.BLOCKED: "dns_sinkhole",
        Verdict.OPENED: "unclassified",
        Verdict.UNREACHABLE: "dns_failure",
    }[verdict]
    return ResultRow(
        run_id=run_id,
        domain=domain,
        verdict=verdict,
        reason_code=reason,
        confidence=Confidence.LOW,
        blocker_hint=None,
        needs_review=verdict is not Verdict.BLOCKED,
        evidence={"dns": {}},
        checked_at=utc_now(),
        hosts=[f"www.{domain}"],
        sources=["Test portal"],
    )


def test_health_check_and_generation(storage, temp_settings: Settings) -> None:
    assert "SQLite" in storage.health_check()
    portal = make_portal()
    storage.upsert_portals([portal])
    generation = storage.generation
    storage.add_portal_domains(
        portal.uuid,
        [candidate("www.one.example.com", "one.example.com")],
        "https://portal.example/list",
    )
    assert storage.generation > generation


def test_portal_domains_are_upserted_and_linked(storage) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    new_domains, known = storage.add_portal_domains(
        portal.uuid,
        [
            candidate("www.one.example.com", "one.example.com"),
            candidate("two.example.com", "two.example.com"),
        ],
        portal.start_url,
    )
    assert sorted(new_domains) == ["one.example.com", "two.example.com"]
    assert known == []

    new_again, known_again = storage.add_portal_domains(
        portal.uuid,
        [
            candidate("www.one.example.com", "one.example.com"),
            candidate("online.one.example.com", "one.example.com"),
            candidate("three.example.com", "three.example.com"),
        ],
        portal.start_url,
    )
    assert new_again == ["three.example.com"]
    assert known_again == ["one.example.com"]

    sites = {site.domain: site for site in storage.list_sites([portal.uuid])}
    assert set(sites) == {"one.example.com", "two.example.com", "three.example.com"}
    assert "online.one.example.com" in sites["one.example.com"].hosts
    assert sites["one.example.com"].portal_ids == [portal.uuid]


def test_website_list_is_append_only(storage, temp_settings: Settings) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    storage.add_portal_domains(
        portal.uuid, [candidate("one.example.com", "one.example.com")], portal.start_url
    )
    connection = sqlite3.connect(temp_settings.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM sites")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM site_hosts")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM site_portals")
    finally:
        connection.close()
    assert storage.count_sites() == 1


def test_exclusion_is_reversible_and_keeps_the_row(storage) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    storage.add_portal_domains(
        portal.uuid, [candidate("one.example.com", "one.example.com")], portal.start_url
    )
    storage.set_excluded("one.example.com", True)
    assert storage.count_sites() == 1
    assert storage.select_sites(ScanSelection(all_sources=True)) == []
    storage.set_excluded("one.example.com", False)
    assert len(storage.select_sites(ScanSelection(all_sources=True))) == 1


def test_custom_site_list_excludes_portal_domains(storage) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    storage.add_portal_domains(
        portal.uuid,
        [
            candidate("portal-one.example.com", "portal-one.example.com"),
            candidate("portal-two.example.com", "portal-two.example.com"),
        ],
        portal.start_url,
    )
    storage.add_custom_domains([("my-custom.example.com", "my-custom.example.com")])

    custom = storage.list_custom_sites()
    assert [site.domain for site in custom] == ["my-custom.example.com"]
    assert all(site.is_custom for site in custom)
    assert len(storage.list_sites(None, with_custom=True)) == 3


def test_custom_domains_are_tracked_and_removable(storage) -> None:
    diff = storage.add_custom_domains(
        [("custom.example.com", "www.custom.example.com")], note="manual"
    )
    assert diff.added == ["custom.example.com"]
    again = storage.add_custom_domains([("custom.example.com", "custom.example.com")])
    assert again.existing == ["custom.example.com"]

    selection = ScanSelection(all_sources=False, portal_ids=[], with_custom=True)
    sites = storage.select_sites(selection)
    assert [site.domain for site in sites] == ["custom.example.com"]
    assert sites[0].is_custom is True

    assert storage.remove_custom_site("custom.example.com") is True
    assert storage.remove_custom_site("custom.example.com") is False


def test_run_results_and_summary(storage) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    storage.add_portal_domains(
        portal.uuid,
        [
            candidate("one.example.com", "one.example.com"),
            candidate("two.example.com", "two.example.com"),
            candidate("three.example.com", "three.example.com"),
        ],
        portal.start_url,
    )
    selection = ScanSelection(all_sources=True, with_custom=True)
    run = storage.create_run(
        selection=selection,
        total=3,
        app_version=__version__,
        os_name="Linux",
        network_context={"resolvers": ["127.0.0.53"]},
        debug=False,
    )
    assert run.status is RunStatus.PENDING
    storage.update_run_status(run.id, RunStatus.RUNNING)
    attempts = [
        ProbeAttemptRecord(
            run_id=run.id,
            domain="one.example.com",
            host="www.one.example.com",
            scheme="https",
            http_status=200,
            verdict=Verdict.OPENED,
            reason_code="unclassified",
        )
    ]
    storage.save_site_result(make_result(run.id, "one.example.com", Verdict.OPENED), attempts)
    storage.save_site_result(make_result(run.id, "two.example.com", Verdict.BLOCKED), [])
    storage.save_site_result(make_result(run.id, "three.example.com", Verdict.UNREACHABLE), [])
    storage.update_run_status(run.id, RunStatus.COMPLETED, utc_now())

    summary = storage.get_run_summary(run.id)
    assert (summary.total, summary.opened, summary.blocked, summary.unreachable) == (3, 1, 1, 1)
    assert storage.count_results(run.id) == 3

    leaks = storage.get_results(run.id, filters=_leaks_filter())
    assert [row.domain for row in leaks] == ["one.example.com"]
    search = storage.get_results(run.id, filters=_search_filter("two"))
    assert [row.domain for row in search] == ["two.example.com"]

    latest = storage.latest_run()
    assert latest is not None and latest.id == run.id


def _leaks_filter():
    from blockprobe.storage.base import ResultFilter

    return ResultFilter(leaks_only=True)


def _search_filter(term: str):
    from blockprobe.storage.base import ResultFilter

    return ResultFilter(search=term)


def test_guarded_reset_writes_backups_and_can_keep_custom(storage, temp_settings: Settings) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    storage.add_portal_domains(
        portal.uuid, [candidate("one.example.com", "one.example.com")], portal.start_url
    )
    storage.add_custom_domains([("mine.example.com", "mine.example.com")])

    report = storage.reset_list(
        temp_settings.backups_dir, include_custom=False, backup_required=True
    )
    assert report.backup_database is not None and report.backup_database.exists()
    assert report.backup_domains is not None and report.backup_domains.exists()
    assert report.removed_sites == 1
    assert report.kept_custom == 1
    assert storage.count_sites() == 1
    assert storage.count_custom_sites() == 1

    full = storage.reset_list(
        temp_settings.backups_dir, include_custom=True, backup_required=False
    )
    assert full.removed_custom == 1
    assert storage.count_sites() == 0


def test_delete_all_data_wipes_runs_and_sites(storage) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    storage.add_portal_domains(
        portal.uuid, [candidate("one.example.com", "one.example.com")], portal.start_url
    )
    storage.create_run(
        selection=ScanSelection(all_sources=False),
        total=1,
        app_version=__version__,
        os_name="Linux",
        network_context={},
        debug=False,
    )
    report = storage.delete_all_data()
    assert report.removed_runs == 1
    assert report.removed_sites == 1
    assert storage.count_sites() == 0
    assert storage.latest_run() is None


def test_consent_and_meta(storage) -> None:
    assert storage.get_consent() is None
    storage.save_consent("2026-10-19.1")
    record = storage.get_consent()
    assert record is not None and record.version == "2026-10-19.1"
    storage.set_meta("key", "value")
    assert storage.get_meta("key") == "value"
    assert storage.get_meta("missing", "fallback") == "fallback"


def test_export_domain_list(storage, temp_settings: Settings) -> None:
    portal = make_portal()
    storage.upsert_portals([portal])
    storage.add_portal_domains(
        portal.uuid, [candidate("one.example.com", "one.example.com")], portal.start_url
    )
    storage.add_custom_domains([("mine.example.com", "mine.example.com")])
    target = storage.export_domain_list(temp_settings.reports_dir / "domains.txt")
    text = target.read_text(encoding="utf-8")
    assert "one.example.com" in text
    assert "mine.example.com" in text


def test_data_survives_reopen(temp_settings: Settings) -> None:
    first = build_storage(temp_settings)
    portal = make_portal()
    first.upsert_portals([portal])
    first.add_portal_domains(
        portal.uuid, [candidate("one.example.com", "one.example.com")], portal.start_url
    )
    generation = first.generation
    first.close()

    second = build_storage(temp_settings)
    try:
        assert second.count_sites() == 1
        assert second.generation == generation
        assert second.list_portals()[0].name == "Test portal"
    finally:
        second.close()


def test_restart_generation_moves_forward(storage) -> None:
    generation = storage.generation
    new_generation = storage.restart_generation()
    assert new_generation > generation
    assert storage.generation == new_generation


def test_record_refresh_sets_portal_date(storage) -> None:
    from blockprobe.core.enums import RefreshStatus
    from blockprobe.core.models import RefreshDiff

    portal = make_portal()
    storage.upsert_portals([portal])
    before = storage.get_portal(portal.uuid)
    assert before is not None and before.last_refreshed_at == 0
    diff = RefreshDiff(
        portal_id=portal.uuid,
        portal_name=portal.name,
        status=RefreshStatus.OK,
        pages=1,
        found=2,
        added=2,
        started_at=utc_now(),
        finished_at=utc_now(),
    )
    storage.record_refresh(diff)
    after = storage.get_portal(portal.uuid)
    assert after is not None and after.last_refreshed_at > 0
    assert after.last_error is None
