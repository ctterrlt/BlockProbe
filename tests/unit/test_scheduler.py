# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scheduled-scan renderers and install/remove safety (FR-12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blockprobe.scheduler import (
    ScheduleSpec,
    cron_line,
    default_command,
    detect_platform,
    install,
    launchd_plist,
    status,
    systemd_units,
    uninstall,
    validate,
    windows_task_xml,
)
from blockprobe.scheduler.notify import leaks_message, notify


def spec(**overrides: object) -> ScheduleSpec:
    values: dict[str, object] = {
        "frequency": "weekly",
        "time": "06:05",
        "weekday": 1,
        "command": "blockprobe scan --wait --notify",
    }
    values.update(overrides)
    return ScheduleSpec(**values)  # type: ignore[arg-type]


def test_default_command_runs_this_interpreter() -> None:
    command = default_command()
    assert "-m blockprobe" in command
    assert "scan" in command
    assert "--notify" in command


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("frequency", "hourly", "daily or weekly"),
        ("time", "25:00", "HH:MM"),
        ("time", "7:00", "HH:MM"),
        ("weekday", 0, "between 1"),
        ("weekday", 8, "between 1"),
        ("command", "   ", "command"),
    ],
)
def test_validate_rejects_bad_input(field: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate(spec(**{field: value}))


def test_systemd_timer_daily_and_weekly() -> None:
    weekly = systemd_units(spec(weekday=3))
    assert "OnCalendar=Wed *-*-* 06:05:00" in weekly["blockprobe-scan.timer"]
    assert "Persistent=true" in weekly["blockprobe-scan.timer"]
    assert "ExecStart=blockprobe scan --wait --notify" in weekly["blockprobe-scan.service"]

    daily = systemd_units(spec(frequency="daily"))
    assert "OnCalendar=*-*-* 06:05:00" in daily["blockprobe-scan.timer"]
    assert "Mon" not in daily["blockprobe-scan.timer"]


def test_launchd_weekday_conversion() -> None:
    weekly = launchd_plist(spec(weekday=7))
    assert "StartCalendarInterval" in weekly
    assert "<key>Weekday</key>" in weekly
    assert "<integer>0</integer>" in weekly  # launchd: Sunday is 0

    daily = launchd_plist(spec(frequency="daily", weekday=1))
    assert "Weekday" not in daily
    assert "<false/>" in daily


def test_windows_task_is_least_privilege() -> None:
    weekly = windows_task_xml(spec(weekday=7))
    assert "<Sunday/>" in weekly
    assert "<RunLevel>LeastPrivilege</RunLevel>" in weekly
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in weekly

    daily = windows_task_xml(spec(frequency="daily"))
    assert "ScheduleByDay" in daily
    assert "DaysOfWeek" not in daily


def test_cron_line_uses_sunday_zero() -> None:
    line = cron_line(spec(weekday=7))
    assert line.startswith("05 06 * * 0 blockprobe")
    daily = cron_line(spec(frequency="daily", weekday=7))
    assert daily.startswith("05 06 * * * blockprobe")


def test_platform_detection() -> None:
    assert detect_platform("android") == "android"
    assert detect_platform("win32") == "windows"
    assert detect_platform("darwin") == "macos"
    assert detect_platform("linux") == "systemd"
    assert detect_platform("freebsd14") == "systemd"
    assert detect_platform("sunos5") == "cron"


def test_android_scheduling_is_reported_as_unavailable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Android background scheduling is not implemented"):
        install(spec(), platform_name="android", directory=tmp_path)
    assert status(platform_name="android", directory=tmp_path).note.startswith(
        "Android background scheduling is not implemented"
    )
    assert uninstall(platform_name="android", directory=tmp_path) == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("platform_name", ["linux", "darwin", "win32", "sunos5"])
def test_install_status_uninstall_roundtrip(platform_name: str, tmp_path: Path) -> None:
    written = install(spec(), platform_name=platform_name, directory=tmp_path)
    assert written
    assert all(path.exists() for path in written)

    info = status(platform_name=platform_name, directory=tmp_path)
    assert info.installed
    assert info.platform == detect_platform(platform_name)
    assert info.activate
    assert info.note

    removed = uninstall(platform_name=platform_name, directory=tmp_path)
    assert removed == written
    assert not any(path.exists() for path in written)
    assert not status(platform_name=platform_name, directory=tmp_path).installed


def test_install_only_writes_files(tmp_path: Path) -> None:
    path = tmp_path / "blockprobe-scan.timer"
    install(spec(), platform_name="linux", directory=tmp_path)
    assert path.exists()
    assert "OnCalendar" in path.read_text(encoding="utf-8")
    # No activation command or unit is executed by install().


def test_schedule_dir_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLOCKPROBE_SCHEDULE_DIR", str(tmp_path))
    from blockprobe.scheduler import jobs_dir

    assert jobs_dir(platform_name="linux") == tmp_path


def test_notify_falls_back_to_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("blockprobe.scheduler.notify.shutil.which", lambda _name: None)
    assert notify("title", "body", platform_name="linux") == "logged"
    assert notify("title", "body", platform_name="win32") == "logged"


def test_notify_uses_notify_send_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "blockprobe.scheduler.notify.shutil.which", lambda _name: "/usr/bin/notify-send"
    )
    monkeypatch.setattr("blockprobe.scheduler.notify._run", lambda _command: True)
    assert notify("title", "body", platform_name="linux") == "notify-send"


def test_leaks_message_only_reports_new_leaks() -> None:
    assert leaks_message(opened=4, total=10, newly_leaking=0) == ""
    assert "2 website(s) are leaking now" in leaks_message(
        opened=4, total=10, newly_leaking=2
    )
    assert "4 of 10" in leaks_message(opened=4, total=10)
    assert "No website opened" in leaks_message(opened=0, total=10)
