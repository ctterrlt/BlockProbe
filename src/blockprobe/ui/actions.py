# SPDX-License-Identifier: AGPL-3.0-or-later
"""UI actions shared by the Flet app and the CLI.

Each action returns an :class:`ActionResult` and contains no Flet imports, so the
same behaviour can be exercised from the CLI and from tests. Actions that are not
implemented yet say so explicitly; they never invent scan results.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from platformdirs import user_data_dir

from blockprobe import APP_NAME, __version__


@dataclass(frozen=True)
class ActionResult:
    """A short status update shown in the UI or printed by the CLI."""

    title: str
    detail: str


def data_directory() -> Path:
    """Return the per-user directory where BlockProbe stores local data."""
    return Path(user_data_dir(APP_NAME, appauthor=False))


def ensure_data_directory() -> Path:
    """Create the data directory if needed and return it."""
    path = data_directory()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _flet_version() -> str:
    try:
        return metadata.version("flet")
    except metadata.PackageNotFoundError:
        return "not installed"


def environment_summary() -> str:
    """Return the runtime information shown in the Environment card."""
    return "\n".join(
        (
            f"BlockProbe {__version__}",
            f"Python {platform.python_version()}",
            f"Platform {platform.platform()}",
            f"Flet {_flet_version()}",
            f"Data directory {data_directory()}",
        )
    )


def run_self_check() -> ActionResult:
    """Verify that the UI can create and use its local data directory."""
    try:
        path = ensure_data_directory()
    except OSError as exc:
        return ActionResult(
            "Self-check failed",
            f"Could not create the data directory at {data_directory()}.\n\n{exc}",
        )

    return ActionResult(
        "Self-check passed",
        "\n".join(
            (
                "The interface is running and the data directory is writable.",
                "",
                f"Version: {__version__}",
                f"Python: {platform.python_version()}",
                f"Platform: {platform.platform()}",
                f"Flet: {_flet_version()}",
                f"Data directory: {path}",
            )
        ),
    )


def open_data_directory() -> ActionResult:
    """Create the local data directory and report its location."""
    try:
        path = ensure_data_directory()
    except OSError as exc:
        return ActionResult(
            "Could not open the data directory",
            f"{data_directory()}\n\n{exc}",
        )

    return ActionResult(
        "Data directory ready",
        f"{path}\n\nScan data will be stored here once storage is implemented.",
    )


def refresh_portals() -> ActionResult:
    """Refresh the selected portal lists. Implemented in milestone M1."""
    return ActionResult(
        "Portal refresh is not implemented",
        "Milestone M1 adds ADM extraction, pagination and append-only list updates.",
    )


def start_scan() -> ActionResult:
    """Start a probe run. Implemented in milestone M2."""
    return ActionResult(
        "Probe engine is not implemented",
        "Milestone M2 adds DNS, TCP/TLS and HTTP probing with evidence-based classification.",
    )


def export_report() -> ActionResult:
    """Export results. Implemented after the probe engine stores results."""
    return ActionResult(
        "Reporting is not implemented",
        "CSV, JSON and sanitised Markdown exports arrive once storage and runs are implemented.",
    )


def capture_signature() -> ActionResult:
    """Capture a block-page signature. Implemented in a later milestone."""
    return ActionResult(
        "Signature capture is not implemented",
        "Signatures must come from verified live responses and are added in a later milestone.",
    )
