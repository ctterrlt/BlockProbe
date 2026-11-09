# SPDX-License-Identifier: AGPL-3.0-or-later
"""The editable VERSION file feeds runtime and package metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

from blockprobe import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_version_comes_from_the_single_source_file() -> None:
    source_version_file = ROOT / "VERSION"
    version_file = ROOT / "src" / "blockprobe" / "VERSION"
    source_version = source_version_file.read_text(encoding="utf-8").strip()
    assert version_file.read_text(encoding="utf-8").strip() == source_version
    assert __version__ == source_version

    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["hatch"]["version"] == {
        "source": "code",
        "path": "scripts/hatch_version.py",
        "expression": "pep440_version()",
    }


def test_hatch_and_build_metadata_use_the_root_version() -> None:
    from scripts.build import project_build_number, project_version
    from scripts.hatch_version import pep440_version

    assert pep440_version() == "1.4.0a0"
    assert project_version() == "1.4.0-alpha"
    assert project_build_number() == 100400010

