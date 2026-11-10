#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build and stage the Rust scan library for Python/Flet packaging."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "blockprobe"
ANDROID_ABIS = {
    "arm64-v8a": ("aarch64-linux-android", "aarch64-linux-android"),
    "armeabi-v7a": ("armv7-linux-androideabi", "armv7a-linux-androideabi"),
    "x86_64": ("x86_64-linux-android", "x86_64-linux-android"),
}
IOS_SIM_TARGETS = {
    "arm64": "aarch64-apple-ios-sim",
    "x86_64": "x86_64-apple-ios",
}


def library_name(target: str | None = None) -> str:
    os_name = platform.system().lower()
    if target and "windows" in target:
        return "blockprobe_core.dll"
    if target and "apple" in target:
        return "libblockprobe_core.dylib"
    if os_name == "windows":
        return "blockprobe_core.dll"
    if os_name == "darwin":
        return "libblockprobe_core.dylib"
    return "libblockprobe_core.so"


def build(target: str | None, destination: Path, *, release: bool = True) -> Path:
    command = [
        "cargo",
        "build",
        "--offline",
        "--manifest-path",
        str(ROOT / "Cargo.toml"),
        "--lib",
    ]
    if release:
        command.append("--release")
    if target:
        command.extend(("--target", target))
    subprocess.run(command, cwd=ROOT, check=True)
    profile = "release" if release else "debug"
    source_dir = ROOT / "target" / target / profile if target else ROOT / "target" / profile
    source = source_dir / library_name(target)
    if not source.is_file():
        raise SystemExit(f"Cargo did not produce the Rust library: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def android_ndk() -> Path:
    for key in ("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT"):
        value = os.environ.get(key)
        if value and Path(value).is_dir():
            return Path(value)
    sdk_roots = [
        Path(value)
        for key in ("ANDROID_SDK_ROOT", "ANDROID_HOME")
        if (value := os.environ.get(key))
    ]
    sdk_roots.extend((Path.home() / "Android" / "sdk", Path("/opt/android-sdk")))
    for sdk in sdk_roots:
        ndk_root = sdk / "ndk"
        versions = (
            sorted(path for path in ndk_root.iterdir() if path.is_dir())
            if ndk_root.is_dir()
            else []
        )
        if versions:
            return versions[-1]
    raise SystemExit("Android Rust builds require ANDROID_NDK_HOME or an installed SDK NDK.")


def build_android() -> list[Path]:
    ndk = android_ndk()
    host_tag = "linux-x86_64" if sys.platform.startswith("linux") else "darwin-x86_64"
    bin_dir = ndk / "toolchains" / "llvm" / "prebuilt" / host_tag / "bin"
    api = os.environ.get("BLOCKPROBE_ANDROID_API", "24")
    outputs = []
    for abi, (target, clang_prefix) in ANDROID_ABIS.items():
        linker = bin_dir / f"{clang_prefix}{api}-clang"
        if not linker.is_file():
            raise SystemExit(f"Android NDK linker not found: {linker}")
        env = os.environ.copy()
        env[f"CARGO_TARGET_{target.upper().replace('-', '_')}_LINKER"] = str(linker)
        subprocess.run(
            [
                "cargo",
                "build",
                "--offline",
                "--manifest-path",
                str(ROOT / "Cargo.toml"),
                "--lib",
                "--release",
                "--target",
                target,
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        source = ROOT / "target" / target / "release" / "libblockprobe_core.so"
        destination = PACKAGE / "_native" / "android" / abi / source.name
        if not source.is_file():
            raise SystemExit(f"Cargo did not produce Android library: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        outputs.append(destination)
    return outputs


def build_ios_simulator() -> list[Path]:
    if platform.system() != "Darwin":
        raise SystemExit("iOS simulator Rust libraries require a macOS host.")
    outputs = []
    for arch, target in IOS_SIM_TARGETS.items():
        subprocess.run(
            [
                "cargo",
                "build",
                "--offline",
                "--manifest-path",
                str(ROOT / "Cargo.toml"),
                "--lib",
                "--release",
                "--target",
                target,
            ],
            cwd=ROOT,
            check=True,
        )
        source = ROOT / "target" / target / "release" / "libblockprobe_core.dylib"
        destination = PACKAGE / "_native" / "ios-simulator" / arch / source.name
        if not source.is_file():
            raise SystemExit(f"Cargo did not produce iOS simulator library: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        outputs.append(destination)
    return outputs


def stage_pyodide_side_module(build_dir: Path) -> Path:
    """Stage Cargo's Emscripten side module under Pyodide's ``.so`` name."""

    def is_side_module(path: Path) -> bool:
        if not path.is_file() or path.suffix not in {".wasm", ".so"}:
            return False
        module_stem = path.stem.removeprefix("lib")
        return module_stem == "blockprobe_core" or module_stem.startswith(
            "blockprobe_core-"
        )

    candidates = sorted(
        (
            path for path in build_dir.rglob("*") if is_side_module(path)
        ),
        key=lambda path: (
            len(path.relative_to(build_dir).parts),
            path.suffix != ".wasm",
            str(path),
        ),
    )
    source = candidates[0] if candidates else None
    destination = (
        PACKAGE
        / "_native"
        / "emscripten"
        / "pyemscripten-2026.0-wasm32"
        / "libblockprobe_core.so"
    )
    if source is None:
        observed = sorted(
            path.relative_to(build_dir).as_posix()
            for path in build_dir.rglob("*")
            if path.is_file()
            and (
                "blockprobe_core" in path.name
                or path.suffix in {".wasm", ".so", ".a"}
            )
        )
        details = ", ".join(observed[:30]) if observed else "no candidate artifacts found"
        raise SystemExit(
            "Cargo did not produce a Pyodide Rust side module under "
            f"{build_dir}; discovered: {details}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def build_pyodide() -> Path:
    """Build the Rust C-ABI side module for Flet's Python 3.14 web runtime."""
    target = "wasm32-unknown-emscripten"
    env = os.environ.copy()
    env["CARGO_TARGET_WASM32_UNKNOWN_EMSCRIPTEN_LINKER"] = "emcc"
    flags = env.get("RUSTFLAGS", "").strip()
    side_module_flag = "-C link-arg=-sSIDE_MODULE=2"
    if side_module_flag not in flags:
        env["RUSTFLAGS"] = f"{flags} {side_module_flag}".strip()
    subprocess.run(
        [
            "cargo",
            "build",
            "--offline",
            "--manifest-path",
            str(ROOT / "Cargo.toml"),
            "--lib",
            "--release",
            "--target",
            target,
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    return stage_pyodide_side_module(ROOT / "target" / target / "release")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android", action="store_true", help="Build all APK ABIs with the Android NDK")
    parser.add_argument(
        "--ios-simulator",
        action="store_true",
        help="Build arm64 and x86_64 iOS simulator libraries on macOS",
    )
    parser.add_argument("--web", action="store_true", help="Build the Pyodide 2026.0 WASM side module")
    parser.add_argument("--debug", action="store_true", help="Build an unoptimized library for local tests")
    args = parser.parse_args()
    if sum((args.android, args.ios_simulator, args.web)) > 1:
        parser.error("choose one of --android, --ios-simulator or --web")
    if args.android:
        outputs = build_android()
    elif args.ios_simulator:
        outputs = build_ios_simulator()
    elif args.web:
        outputs = [build_pyodide()]
    else:
        name = library_name()
        outputs = [build(None, PACKAGE / "_native" / name, release=not args.debug)]
    for path in outputs:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
