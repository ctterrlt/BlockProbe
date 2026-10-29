"""Linux portable archive and per-user installer regression tests."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest
from scripts.build import build_linux_archive

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "packaging" / "linux" / "install.sh"


@pytest.fixture
def portable_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "BlockProbe"
    (bundle / "share" / "applications").mkdir(parents=True)
    (bundle / "share" / "icons" / "hicolor" / "128x128" / "apps").mkdir(parents=True)
    (bundle / "blockprobe").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bundle / "blockprobe").chmod(0o755)
    (bundle / "share" / "applications" / "io.github.ctterrlt.blockprobe.desktop").write_text(
        "[Desktop Entry]\nName=BlockProbe\nExec=blockprobe\nIcon=io.github.ctterrlt.blockprobe\n",
        encoding="utf-8",
    )
    (bundle / "share" / "icons" / "hicolor" / "128x128" / "apps" / "icon.png").write_bytes(
        b"icon"
    )
    return bundle


def test_linux_archive_contains_bundle_installer_and_license(
    tmp_path: Path, portable_bundle: Path
) -> None:
    archive = build_linux_archive(portable_bundle, tmp_path / "dist")

    with tarfile.open(archive, "r:gz") as tar:
        members = set(tar.getnames())
        assert "BlockProbe/blockprobe" in members
        assert "BlockProbe/install.sh" in members
        assert "BlockProbe/LICENSE" in members
        assert tar.extractfile("BlockProbe/LICENSE") is not None


def test_arch_package_declares_license_runtime_dependencies_and_launcher() -> None:
    recipe = (ROOT / "packaging" / "linux" / "arch" / "PKGBUILD").read_text(encoding="utf-8")

    assert "license=('AGPL-3.0-or-later')" in recipe
    assert "depends=('gtk3' 'libepoxy' 'libxkbcommon')" in recipe
    assert '"$startdir/BlockProbe/LICENSE"' in recipe
    assert "Exec=/usr/bin/blockprobe-desktop %U" in recipe


def test_linux_installer_copies_app_license_and_desktop_entry(
    tmp_path: Path, portable_bundle: Path
) -> None:
    (portable_bundle / "install.sh").write_bytes(INSTALLER.read_bytes())
    (portable_bundle / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    data_home = tmp_path / "data home"
    bin_home = tmp_path / "bin & home | tools"
    result = subprocess.run(
        ["sh", str(portable_bundle / "install.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path / "home"),
            "XDG_DATA_HOME": str(data_home),
            "XDG_BIN_HOME": str(bin_home),
        },
    )

    assert result.returncode == 0, result.stderr
    assert (data_home / "blockprobe" / "blockprobe").is_file()
    installed_license = data_home / "licenses" / "blockprobe" / "LICENSE"
    assert installed_license.read_bytes() == (ROOT / "LICENSE").read_bytes()
    desktop_entry = data_home / "applications" / "io.github.ctterrlt.blockprobe.desktop"
    desktop_text = desktop_entry.read_text(encoding="utf-8")
    assert f'Exec="{bin_home}/blockprobe-desktop" %U' in desktop_text
    assert (data_home / "icons" / "hicolor" / "128x128" / "apps" / "icon.png").is_file()
    assert (bin_home / "blockprobe-desktop").stat().st_mode & 0o111


@pytest.mark.parametrize(
    ("binary_exists", "license_exists", "error_fragment"),
    [
        (False, True, "Could not find the packaged executable"),
        (True, False, "Could not find the BlockProbe license file"),
    ],
)
def test_linux_installer_rejects_incomplete_bundle(
    tmp_path: Path,
    binary_exists: bool,
    license_exists: bool,
    error_fragment: str,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    installer = bundle / "install.sh"
    installer.write_bytes(INSTALLER.read_bytes())
    if binary_exists:
        executable = bundle / "blockprobe"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
    if license_exists:
        (bundle / "LICENSE").write_text("license", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(installer)],
        check=False,
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path / "home")},
    )

    assert result.returncode != 0
    assert error_fragment in result.stderr
