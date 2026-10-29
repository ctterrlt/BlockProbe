# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression checks for the native build orchestration."""

from __future__ import annotations

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
