# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hatch build hook for distributions that contain the Rust runtime library."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sysconfig
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Give native-library wheels platform-specific tags and metadata."""

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        del version
        if self.target_name == "wheel":
            build_data["pure_python"] = False
            platform_tag = sysconfig.get_platform().replace(".", "_").replace("-", "_")
            build_data["tag"] = f"py3-none-{platform_tag}"
            self._ensure_native_library()

    def _ensure_native_library(self) -> None:
        root = Path(self.root)
        package = root / "src" / "blockprobe"
        system = platform.system().lower()
        library = {
            "windows": "blockprobe_core.dll",
            "darwin": "libblockprobe_core.dylib",
        }.get(system, "libblockprobe_core.so")
        destination = package / "_native" / library
        try:
            subprocess.run(
                [
                    "cargo",
                    "build",
                    "--offline",
                    "--manifest-path",
                    str(root / "Cargo.toml"),
                    "--lib",
                    "--release",
                ],
                cwd=root,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Building a BlockProbe wheel from source requires Rust and Cargo."
            ) from exc
        source = root / "target" / "release" / library
        if not source.is_file():
            raise RuntimeError(f"Cargo did not produce the required Rust library: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
