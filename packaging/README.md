<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Packaging build entry points and platform requirements. -->

# Packaging

Build entry point: `python scripts/build.py TARGET` from the repository root.
Install Python 3.14+ and `python -m pip install -e ".[packaging]"` first.
Flet bundles the dependencies declared by `pyproject.toml` and Python 3.14.
`src/blockprobe/VERSION` is the single version source for Python metadata,
runtime display, Flet build version/build code, Windows installer and Arch
package artifact.

The current local development version is `1.2.1-alpha`. `python
scripts/build.py all` rebuilds the targets available on the current host and
cleans target-specific previous artifacts first.

| Target | Artifact | Host/tooling |
| --- | --- | --- |
| `python` | wheel and sdist | Python/pip; Hatchling build backend |
| `windows` | portable ZIP; `--installer` adds per-user Setup EXE | Windows; Inno Setup for installer |
| `macos` | app ZIP; `--installer` adds unsigned DMG | macOS; Xcode/Flutter toolchain |
| `linux` | portable tar.gz; `--installer` also creates Arch `.pkg.tar.zst` on Arch | Linux; Flutter toolchain; `makepkg` for Arch package |
| `apk`, `aab` | Android APK or App Bundle | Flet-managed Android toolchain; local packages use debug signing; release key supplied separately |
| `web` | static web bundle ZIP | Flet-managed Flutter toolchain |
| `ios-simulator` | simulator app ZIP | macOS and Xcode |
| Container | headless local web UI | Docker; see `Dockerfile` |

`packaging/windows/installer.iss` and `packaging/macos/build_dmg.sh` wrap the
Flet outputs. They do not sign or notarize. Local Linux desktop and Android
emulator checks use unsigned/debug artifacts. The Android 35 emulator was
checked at both 411dp and 360dp. The built web bundle was served at narrow and
wide browser sizes, but headless Chromium remained on the Flet startup screen,
so the web runtime still needs an interactive-browser check. The project owner
also exercises the app on Android. This is not a production-signed release. The release workflow
matrix covers Windows, macOS, Linux, APK, AAB, web and iOS simulator targets.
AppImage/Flatpak, Linux arm64,
physical-device iOS IPA, store submission and production signing remain
unimplemented. Android background scheduling is not implemented. See
[`../docs/PACKAGING.md`](../docs/PACKAGING.md) for install instructions and
milestone status.

The Linux portable archive is distribution-independent at the installer level:
`install.sh` copies the app and license into the current user's XDG directories
and registers a desktop launcher. It does not call a distro package manager or
install system packages. On Arch Linux, `python scripts/build.py linux
--installer` also creates a package that can be installed through `pacman -U`,
`yay -U` or a graphical local-package installer. Debian `.deb` and Fedora
`.rpm` packages are not built.
