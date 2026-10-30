# SPDX-License-Identifier: AGPL-3.0-or-later
"""Best-effort desktop notifications, only used for new leaks (FR-12).

``notify()`` never raises and never blocks a scan for long: when no notifier is
available it logs the message and returns ``"logged"``. The packaged builds can
adopt the platform notification APIs later; the CLI and the scheduler already
use this hook.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

_LOGGER = logging.getLogger("blockprobe.scheduler.notify")
_TIMEOUT_SECONDS = 5


def detect_platform(platform_name: str | None = None) -> str:
    name = (platform_name or sys.platform).lower()
    if name.startswith("win"):
        return "windows"
    if name.startswith("darwin"):
        return "macos"
    return "linux"


def _run(command: list[str]) -> bool:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify(title: str, message: str, platform_name: str | None = None) -> str:
    """Show a notification when possible and return the mechanism used."""
    platform_kind = detect_platform(platform_name)
    if platform_kind == "linux":
        if shutil.which("notify-send") and _run(
            ["notify-send", "--app-name", "BlockProbe", title, message]
        ):
            return "notify-send"
        _LOGGER.info("notification (no notifier available): %s — %s", title, message)
        return "logged"
    if platform_kind == "macos":
        script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
        if _run(["osascript", "-e", script]):
            return "osascript"
        _LOGGER.info("notification (no notifier available): %s — %s", title, message)
        return "logged"
    # Windows toasts require the packaged app; keep the message in the log.
    _LOGGER.info("notification: %s — %s", title, message)
    return "logged"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def leaks_message(opened: int, total: int, newly_leaking: int | None = None) -> str:
    """Build the notification body: only new leaks are worth interrupting for."""
    if newly_leaking is not None:
        if newly_leaking <= 0:
            return ""
        return (
            f"{newly_leaking} website(s) are leaking now that were blocked or "
            f"unreachable in the previous run ({opened} of {total} open in total)."
        )
    if opened <= 0:
        return (
            f"No website opened in the latest run ({total} checked): "
            "the filter blocked the selected sites on this network path."
        )
    return f"{opened} of {total} checked websites opened in the latest run."
