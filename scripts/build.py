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
FLET_BUILD_DIR = ROOT / "build" / "flutter"
ANDROID_FOREGROUND_SERVICE_TYPE = "dataSync"
"""Android foreground-service type used by the scan notification; keep in sync
with ``blockprobe.android_foreground.FOREGROUND_SERVICE_TYPES``."""
ANDROID_MANIFEST = Path("android/app/src/main/AndroidManifest.xml")
ANDROID_APP_GRADLE = Path("android/app/build.gradle.kts")
ANDROID_REQUIRED_MANIFEST_TOKENS = (
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
    "com.dexterous.flutterlocalnotifications.ForegroundService",
    'android:foregroundServiceType="dataSync"',
)
ANDROID_EXTENSION_NAME = "blockprobe_android_network"
ANDROID_PACKAGED_MANIFEST_TOKENS = (
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
    "com.dexterous.flutterlocalnotifications.ForegroundService",
)
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
    value = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not value or any(char.isspace() for char in value):
        raise SystemExit("VERSION must contain one non-empty version string.")
    packaged = (ROOT / "src" / "blockprobe" / "VERSION").read_text(encoding="utf-8").strip()
    if packaged != value:
        raise SystemExit("src/blockprobe/VERSION must match the root VERSION file.")
    return value


def project_build_number() -> int:
    """Encode core version and prerelease stage as a unique Android code."""
    version = project_version()
    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)"
        r"(?:-(alpha|beta|gamma|rc)(?:\.(\d+))?)?"
        r"(?:\+[0-9A-Za-z.-]+)?",
        version,
    )
    if not match:
        raise SystemExit("Flet builds require VERSION in major.minor.patch[-prerelease] form.")
    major, minor, patch = map(int, match.groups()[:3])
    stage, serial_text = match.groups()[3:]
    parts = (major, minor, patch)
    if any(part >= 1000 for part in parts):
        raise SystemExit("Each VERSION component must be less than 1000 for mobile builds.")
    stage_slots = {"alpha": 10, "beta": 20, "gamma": 30, "rc": 40}
    stage_slot = stage_slots.get(stage, 99)
    serial = int(serial_text or 0)
    if serial > 9:
        raise SystemExit("Prerelease serials must be between 0 and 9 for mobile builds.")
    if stage is not None:
        stage_slot += serial
    core_code = major * 1_000_000 + minor * 1_000 + patch
    build_number = core_code * 100 + stage_slot
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
    run([sys.executable, str(ROOT / "scripts" / "build_rust_engine.py")])
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


def patch_android_project(flutter_dir: Path) -> list[str]:
    """Patch the generated Android project for notifications and the scan service.

    ``flet build`` writes the manifest and Gradle files from its template, and
    ``flet-notifications`` needs entries that the Flet project options cannot
    express (the ``ForegroundService`` declaration and core-library desugaring).
    The patch is idempotent, so a rebuild that keeps the generated project only
    applies it once. Re-running the same Flet command afterwards is what makes
    Gradle assemble with the patched manifest.
    """
    manifest = flutter_dir / ANDROID_MANIFEST
    if not manifest.is_file():
        raise SystemExit(
            f"Cannot patch Android packaging: generated manifest missing at {manifest}."
        )
    try:
        from flet_notifications.patcher import (
            patch_foreground_service,
            patch_gradle,
            patch_manifest,
        )
    except ImportError as exc:  # pragma: no cover - environment failure
        raise SystemExit(
            "flet-notifications is required to package the scan foreground "
            f"service: {exc}. Install the project dependencies first."
        ) from exc

    changes = patch_manifest(manifest)
    gradle = flutter_dir / ANDROID_APP_GRADLE
    if gradle.is_file():
        changes += patch_gradle(gradle)
    changes += patch_foreground_service(manifest, ANDROID_FOREGROUND_SERVICE_TYPE)
    return changes


def verify_android_project(flutter_dir: Path) -> list[str]:
    """Fail the build when the Android plugin wiring is not in the project.

    A missing extension or foreground service would otherwise ship silently:
    the app imports fine, but the scan loses background priority and the
    profile cannot read the active DNS state.
    """
    manifest = flutter_dir / ANDROID_MANIFEST
    manifest_text = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
    missing = [token for token in ANDROID_REQUIRED_MANIFEST_TOKENS if token not in manifest_text]
    if missing:
        raise SystemExit(
            "Android manifest is missing required entries: "
            + ", ".join(missing)
            + f". Inspect {manifest}."
        )

    generated = flutter_dir / "lib" / "flet_generated.dart"
    generated_text = generated.read_text(encoding="utf-8") if generated.is_file() else ""
    if ANDROID_EXTENSION_NAME not in generated_text:
        raise SystemExit(
            f"{ANDROID_EXTENSION_NAME} is not registered in {generated}. "
            "The bundled Flutter extension was not discovered from the installed package."
        )

    pubspec = flutter_dir / "pubspec.yaml"
    pubspec_text = pubspec.read_text(encoding="utf-8") if pubspec.is_file() else ""
    if ANDROID_EXTENSION_NAME not in pubspec_text:
        raise SystemExit(
            f"{ANDROID_EXTENSION_NAME} is missing from {pubspec}; Flet did not add the "
            "local Flutter extension dependency."
        )

    return [
        "manifest: POST_NOTIFICATIONS + FOREGROUND_SERVICE_DATA_SYNC + ForegroundService(dataSync)",
        f"flet_generated.dart: {ANDROID_EXTENSION_NAME} extension registered",
        f"pubspec.yaml: {ANDROID_EXTENSION_NAME} path dependency present",
    ]


def _aapt2() -> Path | None:
    """Locate ``aapt2`` from the Android SDK without requiring it."""
    sdk_roots = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "Android" / "sdk"),
        str(Path.home() / "Android" / "Sdk"),
    ]
    for root in sdk_roots:
        if not root:
            continue
        build_tools = Path(root) / "build-tools"
        if not build_tools.is_dir():
            continue
        versions = sorted(
            (item for item in build_tools.iterdir() if (item / "aapt2").is_file()),
            key=lambda item: item.name,
            reverse=True,
        )
        if versions:
            return versions[0] / "aapt2"
    return None


def verify_android_artifacts(artifacts: list[Path]) -> list[str]:
    """Check the built APK/AAB manifest when the Android SDK is available."""
    aapt2 = _aapt2()
    verified: list[str] = []
    for artifact in artifacts:
        if artifact.suffix == ".apk":
            if aapt2 is None:
                print(
                    f"Skipping packaged APK manifest inspection for {artifact.name}: "
                    "aapt2 not found. Verified the generated project only."
                )
                continue
            command = [
                str(aapt2),
                "dump",
                "xmltree",
                str(artifact),
                "--file",
                "AndroidManifest.xml",
            ]
            tool_name = "aapt2"
        elif artifact.suffix == ".aab":
            bundletool_value = os.environ.get("BUNDLETOOL_JAR")
            if not bundletool_value:
                print(
                    f"Skipping packaged AAB manifest inspection for {artifact.name}: "
                    "BUNDLETOOL_JAR is not set."
                )
                continue
            bundletool_jar = Path(bundletool_value).expanduser()
            if not bundletool_jar.is_file():
                raise SystemExit(
                    f"BUNDLETOOL_JAR does not point to a file: {bundletool_jar}"
                )
            java = shutil.which("java")
            if java is None:
                raise SystemExit("Java is required to inspect Android App Bundles.")
            command = [
                java,
                "-jar",
                str(bundletool_jar),
                "dump",
                "manifest",
                f"--bundle={artifact}",
            ]
            tool_name = "bundletool"
        else:
            continue
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"{tool_name} could not read {artifact.name}: {result.stderr.strip()}"
            )
        missing = [
            token for token in ANDROID_PACKAGED_MANIFEST_TOKENS if token not in result.stdout
        ]
        if missing:
            raise SystemExit(
                f"{artifact.name} manifest is missing: {', '.join(missing)}."
            )
        verified.append(
            f"{artifact.name}: packaged manifest declares INTERNET + "
            "ACCESS_NETWORK_STATE and the dataSync scan service"
        )
    return verified


def build_flet(target: str, installer: bool, verbose: bool = False) -> list[Path]:
    host = platform.system().lower()
    required_host = {"windows": "windows", "macos": "darwin", "linux": "linux"}
    if target in required_host and host != required_host[target]:
        raise SystemExit(
            f"Building {target} requires a {required_host[target]} host; current host is {host}."
        )
    if target == "ios-simulator" and host != "darwin":
        raise SystemExit("Building an iOS simulator app requires macOS and Xcode.")

    if target in {"apk", "aab"}:
        run([sys.executable, str(ROOT / "scripts" / "build_rust_engine.py"), "--android"])
    elif target == "ios-simulator":
        run([sys.executable, str(ROOT / "scripts" / "build_rust_engine.py"), "--ios-simulator"])
    elif target == "web":
        run([sys.executable, str(ROOT / "scripts" / "build_rust_engine.py"), "--web"])
    elif target in {"windows", "macos", "linux"}:
        run([sys.executable, str(ROOT / "scripts" / "build_rust_engine.py")])

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
    if target in {"apk", "aab"}:
        try:
            run(command)
        except subprocess.CalledProcessError:
            # Flet creates the native project before its first Gradle assemble.
            # flet-notifications needs desugaring and the foreground-service
            # manifest entry before that assemble can succeed, so patch the
            # generated project after a failed first pass and retry once.
            if not (FLET_BUILD_DIR / ANDROID_MANIFEST).is_file():
                raise
            changes = patch_android_project(FLET_BUILD_DIR)
            if not changes:
                raise
            print("Patched generated Android project after initial assemble failure:", flush=True)
            for change in changes:
                print(f"  + {change}", flush=True)
            run(command)
        else:
            changes = patch_android_project(FLET_BUILD_DIR)
            if changes:
                print("Patched generated Android project:", flush=True)
                for change in changes:
                    print(f"  + {change}", flush=True)
                # Flet reuses the generated project while its inputs are unchanged,
                # so this pass keeps the patched manifest and reassembles the app.
                run(command)
        for line in verify_android_project(FLET_BUILD_DIR):
            print(f"Verified Android wiring: {line}", flush=True)
    else:
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
        for line in verify_android_artifacts(artifacts):
            print(f"Verified Android artifact: {line}", flush=True)
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
