#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build BlockProbe Python and Flet distribution artifacts."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "packages"
TARGETS = (
    "python",
    "windows",
    "macos",
    "linux",
    "apk",
    "aab",
    "web",
    "ios-simulator",
    "all",
)


def project_version() -> str:
    """Read the one user-editable application version value."""
    value = (ROOT / "src" / "blockprobe" / "VERSION").read_text(encoding="utf-8").strip()
    if not value or any(char.isspace() for char in value):
        raise SystemExit("VERSION must contain one non-empty version string.")
    return value


def project_build_number() -> int:
    """Encode a three-part version as a monotonically increasing mobile code."""
    version = project_version()
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", version)
    if not match:
        raise SystemExit("Flet builds require VERSION in major.minor.patch[-prerelease] form.")
    parts = tuple(map(int, match.groups()))
    if any(part >= 1000 for part in parts):
        raise SystemExit("Each VERSION component must be less than 1000 for mobile builds.")
    build_number = parts[0] * 1_000_000 + parts[1] * 1_000 + parts[2]
    if build_number <= 0 or build_number > 2_100_000_000:
        raise SystemExit("VERSION produces an invalid Android build number.")
    return build_number


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a UTF-8, colour-free environment for native build tools.

    Flet uses Rich for progress output. On a Windows runner Rich can select the
    legacy console renderer and write a checkmark through the CP1252 codec,
    aborting an otherwise successful build. Forcing UTF-8 at the Python stream
    boundary keeps the same build deterministic on every runner.
    """
    env = os.environ.copy()
    if extra:
        env.update(extra)
    # These values are intentionally forced last: a caller's inherited
    # CP1252 stream must not reintroduce the Windows crash. The legacy Windows
    # stdio switch is enabled so PYTHONIOENCODING also applies to console
    # buffers; like other Python flag variables, any non-empty value enables it.
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONLEGACYWINDOWSSTDIO": "1",
            "NO_COLOR": "1",
        }
    )
    return env


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True, env=_subprocess_env(env))


def find_executable(name: str) -> str:
    local = Path(sys.executable).with_name(name + (".exe" if os.name == "nt" else ""))
    found = shutil.which(name) or (str(local) if local.is_file() else None)
    if not found:
        raise SystemExit(f"Required build tool not found: {name}")
    return found


def build_python() -> list[Path]:
    output = DIST / "python"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    # Run outside ROOT: the generated ROOT/build directory is a namespace
    # package that otherwise shadows the installed ``build`` frontend.
    run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--outdir",
            str(output),
            str(ROOT),
        ],
        cwd=Path(tempfile.gettempdir()),
    )
    return sorted(output.iterdir())


def build_flet(target: str, installer: bool, verbose: bool = False) -> list[Path]:
    host = platform.system().lower()
    required_host = {"windows": "windows", "macos": "darwin", "linux": "linux"}
    if target in required_host and host != required_host[target]:
        raise SystemExit(
            f"Building {target} requires a {required_host[target]} host; current host is {host}."
        )
    if target == "ios-simulator" and host != "darwin":
        raise SystemExit("Building an iOS simulator app requires macOS and Xcode.")

    output = ROOT / "build" / "flet" / target
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir = DIST / target
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True)
    flet_target = "ios-simulator" if target == "ios-simulator" else target
    command = [
            find_executable("flet"), "build", flet_target,
            "--python-version", "3.14",
            "--build-version", project_version(),
            "--build-number", str(project_build_number()),
            "-o", str(output),
            "--yes",
        ]
    if verbose:
        command.append("-v")
    run(command)
    if not output.exists():
        raise SystemExit(f"Flet reported success but did not create {output}")

    artifacts: list[Path] = []
    if target in {"apk", "aab"}:
        suffix = ".apk" if target == "apk" else ".aab"
        for built in output.rglob(f"*{suffix}"):
            dest = artifact_dir / built.name
            shutil.copy2(built, dest)
            artifacts.append(dest)
    elif target == "windows":
        archive = artifact_dir / "BlockProbe-windows-portable.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in output.rglob("*"):
                if item.is_file():
                    bundle.write(item, item.relative_to(output))
        artifacts.append(archive)
        if installer:
            iscc = shutil.which("ISCC.exe") or shutil.which("iscc")
            if not iscc and os.name == "nt":
                default_iscc = Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
                default_iscc /= "Inno Setup 6/ISCC.exe"
                if default_iscc.is_file():
                    iscc = str(default_iscc)
            if not iscc:
                raise SystemExit("Inno Setup compiler (ISCC.exe) is required for --installer.")
            env = os.environ | {
                "BLOCKPROBE_BUILD_DIR": str(output),
                "BLOCKPROBE_OUTPUT_DIR": str(artifact_dir),
                "BLOCKPROBE_VERSION": project_version(),
            }
            run([iscc, str(ROOT / "packaging" / "windows" / "installer.iss")], cwd=ROOT, env=env)
            artifacts.extend(sorted(artifact_dir.glob("*.exe")))
    elif target == "macos":
        apps = list(output.glob("*.app"))
        if not apps:
            raise SystemExit(f"No .app bundle found in {output}")
        archive = artifact_dir / "BlockProbe-macos.zip"
        ditto = shutil.which("ditto")
        if not ditto:
            raise SystemExit("macOS ditto is required to preserve the app bundle in the archive.")
        run([ditto, "-c", "-k", "--sequesterRsrc", "--keepParent", str(apps[0]), str(archive)])
        artifacts.append(archive)
        if installer:
            dmg = artifact_dir / "BlockProbe-macos.dmg"
            run(["sh", str(ROOT / "packaging" / "macos" / "build_dmg.sh"), str(apps[0]), str(dmg)])
            artifacts.append(dmg)
    elif target == "linux":
        artifacts.append(build_linux_archive(output, artifact_dir))
        if installer and Path("/etc/arch-release").is_file():
            artifacts.append(build_arch_package(output, artifact_dir))
        elif installer:
            print("Skipping Arch package: --installer requires an Arch Linux host with makepkg.")
    else:
        archive = artifact_dir / f"BlockProbe-{target}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in output.rglob("*"):
                if item.is_file():
                    bundle.write(item, item.relative_to(output))
        artifacts.append(archive)

    if not artifacts:
        raise SystemExit(f"Flet produced no distributable files for {target} under {output}")
    return artifacts


def build_linux_archive(output: Path, artifact_dir: Path) -> Path:
    """Create the portable Linux archive with its installer and license."""
    archive = artifact_dir / "BlockProbe-linux-portable.tar.gz"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(output, arcname="BlockProbe")
        bundle.add(ROOT / "packaging" / "linux" / "install.sh", arcname="BlockProbe/install.sh")
        bundle.add(ROOT / "LICENSE", arcname="BlockProbe/LICENSE")
    return archive


def build_arch_package(output: Path, artifact_dir: Path) -> Path:
    """Build an Arch package on Arch Linux using the installed makepkg toolchain."""
    makepkg = find_executable("makepkg")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blockprobe-arch-", dir=artifact_dir) as temp_name:
        package_root = Path(temp_name)
        shutil.copytree(output, package_root / "BlockProbe")
        shutil.copy2(ROOT / "LICENSE", package_root / "BlockProbe" / "LICENSE")
        pkgbuild = (ROOT / "packaging" / "linux" / "arch" / "PKGBUILD").read_text(encoding="utf-8")
        (package_root / "PKGBUILD").write_text(
            pkgbuild.replace("@VERSION@", project_version()), encoding="utf-8"
        )
        run([makepkg, "--force", "--nodeps", "--noconfirm"], cwd=package_root)
        built_packages = sorted(package_root.glob("*.pkg.tar.zst"))
        if len(built_packages) != 1:
            raise SystemExit(
                f"makepkg should produce one package in {package_root}, found {len(built_packages)}"
            )
        destination = artifact_dir / built_packages[0].name
        shutil.copy2(built_packages[0], destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument(
        "--installer",
        action="store_true",
        help="also build a host-native installer when supported (Arch package on Arch Linux)",
    )
    parser.add_argument("--verbose", action="store_true", help="show native build output")
    args = parser.parse_args()
    if args.target == "all":
        host_target = {
            "windows": "windows",
            "darwin": "macos",
            "linux": "linux",
        }.get(platform.system().lower())
        targets = ["python", *( [host_target] if host_target else []), "apk", "aab", "web"]
        if platform.system().lower() == "darwin":
            targets.append("ios-simulator")
        artifacts = []
        for target in targets:
            print(f"\n=== Building {target} ===", flush=True)
            artifacts.extend(
                build_python()
                if target == "python"
                else build_flet(target, args.installer, args.verbose)
            )
        omitted = sorted({"windows", "macos", "linux", "ios-simulator"} - set(targets))
        if omitted:
            print(
                "Host-only targets not built here: " + ", ".join(omitted),
                flush=True,
            )
    else:
        artifacts = (
            build_python()
            if args.target == "python"
            else build_flet(args.target, args.installer, args.verbose)
        )
    print("Built artifacts:")
    for artifact in artifacts:
        print(f"  {artifact.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
