# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression checks for platform-specific native Python wheels."""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest
from scripts import build_rust_engine
from scripts.smoke_wheel import validate_wheel


def _wheel(path: Path, *, platform_tag: str, purelib: bool = False) -> Path:
    path = path / f"blockprobe-1.4.0a0-py3-none-{platform_tag}.whl"
    if os.name == "nt":
        native_library = "blockprobe/_native/blockprobe_core.dll"
    elif sys.platform == "darwin":
        native_library = "blockprobe/_native/libblockprobe_core.dylib"
    else:
        native_library = "blockprobe/_native/libblockprobe_core.so"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "blockprobe-1.4.0a0.dist-info/WHEEL",
            f"Wheel-Version: 1.0\nRoot-Is-Purelib: {str(purelib).lower()}\n",
        )
        archive.writestr(native_library, b"native")
    return path


def test_native_wheel_validator_accepts_platform_tag_and_non_pure_metadata(
    tmp_path: Path,
) -> None:
    wheel = _wheel(tmp_path, platform_tag="linux_x86_64")

    validate_wheel(wheel)


def test_native_wheel_validator_rejects_universal_tag(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, platform_tag="any")

    with pytest.raises(SystemExit, match="platform-specific"):
        validate_wheel(wheel)


def test_native_wheel_validator_rejects_pure_python_metadata(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, platform_tag="linux_x86_64", purelib=True)

    with pytest.raises(SystemExit, match="pure Python"):
        validate_wheel(wheel)


def test_pyodide_side_module_wasm_is_staged_with_loader_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_dir = tmp_path / "target"
    build_dir.mkdir()
    source = build_dir / "libblockprobe_core.wasm"
    source.write_bytes(b"wasm-side-module")
    monkeypatch.setattr(build_rust_engine, "PACKAGE", tmp_path / "package")

    destination = build_rust_engine.stage_pyodide_side_module(build_dir)

    assert destination.name == "libblockprobe_core.so"
    assert destination.read_bytes() == source.read_bytes()
    assert destination.parent.name == "pyemscripten-2026.0-wasm32"
    assert destination.parent.parent.name == "emscripten"


def test_pyodide_side_module_reports_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"libblockprobe_core\.wasm.*libblockprobe_core\.so"):
        build_rust_engine.stage_pyodide_side_module(tmp_path)
