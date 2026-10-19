<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Per-platform packaging matrix and build entry points. -->

# Packaging

| Target | Artifact | Tooling |
| --- | --- | --- |
| Windows 10+ | `.exe` or `.msi` installer | `flet build windows`, Inno Setup, WiX |
| macOS | signed and notarized `.dmg` | `flet build macos`, `packaging/macos/` |
| Linux | AppImage and Flatpak | `flet build linux`, AppImageBuilder, Flatpak |
| Android 8+ | `.apk` or `.aab` | `flet build apk` / `flet build aab` |
| iOS 15+ | `.ipa` (best effort) | `flet build ipa` |
| Python and FreeBSD | wheel and sdist | hatchling, pip or pipx |
| Containers | OCI image | `packaging/Dockerfile` |

`scripts/build.py` is the single entry point for release builds. Docker is optional and is not required for desktop or mobile installation.
