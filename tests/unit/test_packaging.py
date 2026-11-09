# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression checks for platform-specific native Python wheels."""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest
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
