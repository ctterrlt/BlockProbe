# SPDX-License-Identifier: AGPL-3.0-or-later
"""The editable VERSION file feeds runtime and package metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

from blockprobe import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_version_comes_from_the_single_source_file() -> None:
    version_file = ROOT / "src" / "blockprobe" / "VERSION"
    assert __version__ == version_file.read_text(encoding="utf-8").strip()

    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["hatch"]["version"] == {
        "source": "code",
        "path": "scripts/hatch_version.py",
        "expression": "pep440_version()",
    }

