# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression checks for the native build orchestration."""

from __future__ import annotations

from scripts import build
from scripts.build import _subprocess_env


def test_build_subprocess_env_forces_utf8(monkeypatch) -> None:
    """Flet's Windows console output must not fail on non-CP1252 glyphs."""
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONLEGACYWINDOWSSTDIO", "1")
    monkeypatch.setenv("NO_COLOR", "0")

    env = _subprocess_env({"BLOCKPROBE_BUILD_DIR": "bundle"})

    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONLEGACYWINDOWSSTDIO"] == "1"
    assert env["NO_COLOR"] == "1"
    assert env["BLOCKPROBE_BUILD_DIR"] == "bundle"


def test_build_subprocess_env_preserves_extra_variables() -> None:
    env = _subprocess_env({"BLOCKPROBE_OUTPUT_DIR": "artifacts"})

    assert env["BLOCKPROBE_OUTPUT_DIR"] == "artifacts"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_build_number_tracks_the_canonical_version() -> None:
    version = build.project_version()
    parts = tuple(map(int, version.split("-", 1)[0].split(".")))
    assert len(parts) == 3
    stage = version.partition("-")[2].split(".", 1)[0]
    stage_slot = {"alpha": 10, "beta": 20, "gamma": 30, "rc": 40}.get(stage, 99)
    assert build.project_build_number() == (
        (parts[0] * 1_000_000 + parts[1] * 1_000 + parts[2]) * 100 + stage_slot
    )


def test_build_number_accepts_prerelease_versions(monkeypatch) -> None:
    monkeypatch.setattr(build, "project_version", lambda: "1.3.0-alpha")

    assert build.project_build_number() == 100_300_010


def test_prerelease_build_codes_increase_through_stable_release(monkeypatch) -> None:
    versions = [
        "1.2.3-alpha",
        "1.2.3-beta",
        "1.2.3-gamma",
        "1.2.3-rc",
        "1.2.3",
        "1.3.0-alpha",
    ]
    codes = []
    for version in versions:
        monkeypatch.setattr(build, "project_version", lambda version=version: version)
        codes.append(build.project_build_number())

    assert codes == sorted(set(codes))
    assert codes[2] > 1_002_003  # Older 1.2.3 alpha/beta tags used this code.


def test_build_number_rejects_unsupported_version_shape(monkeypatch) -> None:
    monkeypatch.setattr(build, "project_version", lambda: "1.1")
    try:
        build.project_build_number()
    except SystemExit as exc:
        assert "major.minor.patch" in str(exc)
    else:
        raise AssertionError("invalid Flet version was accepted")
