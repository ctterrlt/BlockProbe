#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install and smoke-test the wheel produced by ``scripts/build.py python``."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


def validate_wheel(wheel: Path) -> None:
    """Reject universal tags and pure metadata for a wheel with a native library."""
    try:
        _, _, python_tag, abi_tag, platform_tag = wheel.name[:-4].rsplit("-", 4)
    except ValueError as exc:
        raise SystemExit(f"Wheel filename is malformed: {wheel.name}") from exc
    if python_tag != "py3" or abi_tag != "none" or platform_tag == "any":
        raise SystemExit(f"Expected a platform-specific py3-none wheel, got {wheel.name}")

    if os.name == "nt":
        library = "blockprobe/_native/blockprobe_core.dll"
    elif sys.platform == "darwin":
        library = "blockprobe/_native/libblockprobe_core.dylib"
    else:
        library = "blockprobe/_native/libblockprobe_core.so"

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        if library not in names:
            raise SystemExit(f"Wheel is missing its host Rust library: {library}")
        wheel_metadata = next(
            (name for name in names if name.endswith(".dist-info/WHEEL")), None
        )
        if wheel_metadata is None:
            raise SystemExit("Wheel is missing its WHEEL metadata")
        metadata = archive.read(wheel_metadata).decode("utf-8")
        if "Root-Is-Purelib: false" not in metadata:
            raise SystemExit("Wheel metadata incorrectly marks the package as pure Python")
    print(f"Validated native wheel tag: {python_tag}-{abi_tag}-{platform_tag}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    wheels = sorted((root / "dist" / "packages" / "python").glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected exactly one wheel, found {len(wheels)}")
    validate_wheel(wheels[0])

    with tempfile.TemporaryDirectory(prefix="blockprobe-wheel-smoke-") as temp:
        env_dir = Path(temp) / "venv"
        data_dir = Path(temp) / "data"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        scripts = "Scripts" if os.name == "nt" else "bin"
        python = env_dir / scripts / ("python.exe" if os.name == "nt" else "python")
        cli = env_dir / scripts / ("blockprobe.exe" if os.name == "nt" else "blockprobe")
        subprocess.run(
            [str(python), "-m", "pip", "install", str(wheels[0])],
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-c",
                "from blockprobe.probe.rust_engine import _library; _library()",
            ],
            check=True,
        )
        environment = os.environ.copy()
        environment["BLOCKPROBE_DATA_DIR"] = str(data_dir)
        environment["BLOCKPROBE_CONFIG"] = str(data_dir / "config.toml")
        subprocess.run([str(cli), "version"], check=True, env=environment)
        subprocess.run([str(cli), "self-check"], check=True, env=environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
