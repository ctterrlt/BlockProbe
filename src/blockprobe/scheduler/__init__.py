# SPDX-License-Identifier: AGPL-3.0-or-later
"""User-level scheduled scans (FR-12), off by default.

The module renders and installs per-user job files:

- Linux/FreeBSD: systemd user units (``~/.config/systemd/user``)
- macOS: launchd agent (``~/Library/LaunchAgents``)
- Windows: Task Scheduler XML (``%APPDATA%\\BlockProbe``)
- other POSIX: a cron line the user installs themselves

Installing writes files only. Activation stays explicit and is printed as the
exact command to run, because a scheduler that enables itself would be doing
something the user did not ask for. No job ever runs with root.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
WEEKDAYS_SYSTEMD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS_TASK = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
UNIT_NAME = "blockprobe-scan"
LAUNCHD_LABEL = "io.github.ctterrlt.blockprobe.scan"
TASK_NAME = "BlockProbeScan"


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    """A validated description of the scheduled scan."""

    frequency: str = "weekly"  # "daily" or "weekly"
    time: str = "20:00"  # HH:MM, 24-hour
    weekday: int = 1  # 1=Monday ... 7=Sunday
    command: str = ""
    log_path: str = ""


@dataclass(slots=True)
class ScheduleStatus:
    platform: str
    directory: Path
    files: list[Path] = field(default_factory=list)
    activate: str = ""
    note: str = ""

    @property
    def installed(self) -> bool:
        return bool(self.files) and all(path.exists() for path in self.files)


def default_command() -> str:
    """Return the command a scheduled job should run on this machine."""
    return f'"{sys.executable}" -m blockprobe scan --wait --notify'


def validate(spec: ScheduleSpec) -> ScheduleSpec:
    if spec.frequency not in {"daily", "weekly"}:
        raise ValueError("frequency must be daily or weekly")
    if not TIME_RE.match(spec.time):
        raise ValueError("time must be HH:MM in 24-hour format")
    if not 1 <= spec.weekday <= 7:
        raise ValueError("weekday must be between 1 (Monday) and 7 (Sunday)")
    if not spec.command.strip():
        raise ValueError("command must not be empty")
    return spec


def _hour_minute(spec: ScheduleSpec) -> tuple[str, str]:
    hour, minute = spec.time.split(":")
    return hour, minute


def systemd_units(spec: ScheduleSpec) -> dict[str, str]:
    hour, minute = _hour_minute(spec)
    if spec.frequency == "daily":
        calendar = f"*-*-* {hour}:{minute}:00"
    else:
        calendar = f"{WEEKDAYS_SYSTEMD[spec.weekday - 1]} *-*-* {hour}:{minute}:00"
    service = "\n".join(
        [
            "[Unit]",
            "Description=BlockProbe scheduled scan",
            "Documentation=https://github.com/ctterrlt/BlockProbe",
            "",
            "[Service]",
            "Type=oneshot",
            f"ExecStart={spec.command}",
            "",
        ]
    )
    timer = "\n".join(
        [
            "[Unit]",
            "Description=BlockProbe scheduled scan timer",
            "",
            "[Timer]",
            f"OnCalendar={calendar}",
            "Persistent=true",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    return {f"{UNIT_NAME}.service": service, f"{UNIT_NAME}.timer": timer}


def launchd_plist(spec: ScheduleSpec) -> str:
    hour, minute = _hour_minute(spec)
    interval_lines = [f"        <key>Hour</key>\n        <integer>{int(hour)}</integer>"]
    interval_lines.append(
        f"        <key>Minute</key>\n        <integer>{int(minute)}</integer>"
    )
    if spec.frequency == "weekly":
        weekday = 0 if spec.weekday == 7 else spec.weekday
        interval_lines.append(
            f"        <key>Weekday</key>\n        <integer>{weekday}</integer>"
        )
    arguments = "\n".join(
        f"    <string>{part}</string>" for part in shlex.split(spec.command)
    )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
            '<plist version="1.0">',
            "<dict>",
            "  <key>Label</key>",
            f"  <string>{LAUNCHD_LABEL}</string>",
            "  <key>ProgramArguments</key>",
            "  <array>",
            arguments,
            "  </array>",
            "  <key>RunAtLoad</key>",
            "  <false/>",
            "  <key>StartCalendarInterval</key>",
            "  <dict>",
            *interval_lines,
            "  </dict>",
            "</dict>",
            "</plist>",
            "",
        ]
    )


def windows_task_xml(spec: ScheduleSpec) -> str:
    hour, minute = _hour_minute(spec)
    start = f"2026-01-01T{hour}:{minute}:00"
    if spec.frequency == "daily":
        schedule = "\n".join(
            [
                "        <ScheduleByDay>",
                "          <DaysInterval>1</DaysInterval>",
                "        </ScheduleByDay>",
            ]
        )
    else:
        schedule = "\n".join(
            [
                "        <ScheduleByWeek>",
                "          <WeeksInterval>1</WeeksInterval>",
                "          <DaysOfWeek>",
                f"            <{WEEKDAYS_TASK[spec.weekday - 1]}/>",
                "          </DaysOfWeek>",
                "        </ScheduleByWeek>",
            ]
        )
    command = spec.command.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-16"?>',
            '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
            "  <RegistrationInfo>",
            "    <Description>BlockProbe scheduled scan (user-level, no admin rights)</Description>",
            "  </RegistrationInfo>",
            "  <Triggers>",
            "    <CalendarTrigger>",
            f"      <StartBoundary>{start}</StartBoundary>",
            "      <Enabled>true</Enabled>",
            schedule,
            "    </CalendarTrigger>",
            "  </Triggers>",
            "  <Principals>",
            '    <Principal id="Author">',
            '      <LogonType>InteractiveToken</LogonType>',
            "      <RunLevel>LeastPrivilege</RunLevel>",
            "    </Principal>",
            "  </Principals>",
            "  <Settings>",
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
            "    <StartWhenAvailable>true</StartWhenAvailable>",
            "  </Settings>",
            "  <Actions>",
            '    <Exec>',
            f"      <Command>{command}</Command>",
            "    </Exec>",
            "  </Actions>",
            "</Task>",
            "",
        ]
    )


def cron_line(spec: ScheduleSpec) -> str:
    hour, minute = _hour_minute(spec)
    if spec.frequency == "daily":
        return f"{minute} {hour} * * * {spec.command}"
    cron_weekday = spec.weekday % 7  # cron: 0=Sunday ... 6=Saturday
    return f"{minute} {hour} * * {cron_weekday} {spec.command}"


def detect_platform(platform_name: str | None = None) -> str:
    if platform_name is None and (
        os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_ARGUMENT")
    ):
        return "android"
    name = (platform_name or sys.platform).lower()
    if name.startswith("android"):
        return "android"
    if name.startswith("win"):
        return "windows"
    if name.startswith("darwin"):
        return "macos"
    if name.startswith(("linux", "freebsd", "openbsd", "netbsd")):
        return "systemd"
    return "cron"


def jobs_dir(platform_name: str | None = None, base: Path | None = None) -> Path:
    override = os.environ.get("BLOCKPROBE_SCHEDULE_DIR")
    if base is None and override:
        # Testing and portable setups: keep the job files out of the real home.
        return Path(override)
    platform_kind = detect_platform(platform_name)
    if platform_kind == "systemd":
        root = base or Path.home() / ".config"
        return root / "systemd" / "user"
    if platform_kind == "macos":
        root = base or Path.home()
        return root / "Library" / "LaunchAgents"
    if platform_kind == "windows":
        root = base or Path(os.environ.get("APPDATA", str(Path.home())))
        return root / "BlockProbe"
    root = base or Path.home() / ".config" / "blockprobe"
    return root


def job_files(platform_name: str | None = None, directory: Path | None = None) -> list[Path]:
    platform_kind = detect_platform(platform_name)
    target = directory or jobs_dir(platform_name)
    if platform_kind == "systemd":
        return [target / f"{UNIT_NAME}.service", target / f"{UNIT_NAME}.timer"]
    if platform_kind == "macos":
        return [target / f"{LAUNCHD_LABEL}.plist"]
    if platform_kind == "windows":
        return [target / f"{TASK_NAME}.xml"]
    return [target / "blockprobe-scan.cron"]


def activate_hint(platform_name: str | None = None, directory: Path | None = None) -> str:
    platform_kind = detect_platform(platform_name)
    target = directory or jobs_dir(platform_name)
    if platform_kind == "systemd":
        return (
            "systemctl --user daemon-reload && "
            f"systemctl --user enable --now {UNIT_NAME}.timer"
        )
    if platform_kind == "macos":
        return f"launchctl load {target / (LAUNCHD_LABEL + '.plist')}"
    if platform_kind == "windows":
        return f"schtasks /Create /TN {TASK_NAME} /XML \"{target / (TASK_NAME + '.xml')}\""
    cron = target / "blockprobe-scan.cron"
    return f"crontab -l | cat - {cron} | crontab -  # after reviewing the file"


def channel_hint(platform_name: str | None = None, directory: Path | None = None) -> str:
    platform_kind = detect_platform(platform_name)
    if platform_kind == "systemd":
        return "systemctl --user start blockprobe-scan.service  # to run it once now"
    if platform_kind == "macos":
        return "launchctl start io.github.ctterrlt.blockprobe.scan"
    if platform_kind == "windows":
        return f"schtasks /Run /TN {TASK_NAME}"
    return "run the command from the file manually to test it"


def install(
    spec: ScheduleSpec,
    platform_name: str | None = None,
    directory: Path | None = None,
) -> list[Path]:
    """Write the job files without enabling anything."""
    if detect_platform(platform_name) == "android":
        raise ValueError(
            "Android background scheduling is not implemented yet. Run scans manually."
        )
    validate(spec)
    target = directory or jobs_dir(platform_name)
    target.mkdir(parents=True, exist_ok=True)
    platform_kind = detect_platform(platform_name)
    if platform_kind == "systemd":
        for name, content in systemd_units(spec).items():
            (target / name).write_text(content, encoding="utf-8")
    elif platform_kind == "macos":
        (target / f"{LAUNCHD_LABEL}.plist").write_text(
            launchd_plist(spec), encoding="utf-8"
        )
    elif platform_kind == "windows":
        (target / f"{TASK_NAME}.xml").write_text(
            windows_task_xml(spec), encoding="utf-16"
        )
    else:
        (target / "blockprobe-scan.cron").write_text(cron_line(spec) + "\n", encoding="utf-8")
    return job_files(platform_name, target)


def uninstall(platform_name: str | None = None, directory: Path | None = None) -> list[Path]:
    """Delete only the files this module created; returns the removed paths."""
    if detect_platform(platform_name) == "android":
        return []
    removed: list[Path] = []
    for path in job_files(platform_name, directory):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def status(platform_name: str | None = None, directory: Path | None = None) -> ScheduleStatus:
    platform_kind = detect_platform(platform_name)
    target = directory or jobs_dir(platform_name)
    if platform_kind == "android":
        return ScheduleStatus(
            platform="android",
            directory=target,
            note="Android background scheduling is not implemented yet. Run scans manually.",
        )
    files = job_files(platform_name, target)
    result = ScheduleStatus(
        platform=platform_kind,
        directory=target,
        files=files,
        activate=activate_hint(platform_name, target),
        note=channel_hint(platform_name, target),
    )
    if platform_kind == "cron" and not result.installed:
        result.note = (
            "On this platform BlockProbe only renders a cron line; install it "
            "yourself so you stay in control of your crontab."
        )
    return result
