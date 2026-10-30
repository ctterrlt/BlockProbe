# SPDX-License-Identifier: AGPL-3.0-or-later
"""Translate the app's SemVer-style version to valid Python package metadata."""

from __future__ import annotations

import re
from pathlib import Path

_VERSION = re.compile(
    r"(?P<core>\d+\.\d+\.\d+)(?:-(?P<stage>alpha|beta|gamma|rc)(?:\.(?P<serial>\d+))?)?"
)
_PEP440_STAGE = {"alpha": "a", "beta": "b", "gamma": "rc", "rc": "rc"}


def pep440_version() -> str:
    """Return the PEP 440 equivalent while keeping VERSION as canonical."""
    version_path = Path(__file__).resolve().parents[1] / "src" / "blockprobe" / "VERSION"
    value = version_path.read_text(encoding="utf-8").strip()
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError(f"Unsupported application version: {value}")
    core = match.group("core")
    stage = match.group("stage")
    if stage is None:
        return core
    serial = int(match.group("serial") or 0)
    normalized_stage = _PEP440_STAGE[stage]
    return f"{core}{normalized_stage}{serial}"
