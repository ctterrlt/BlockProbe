<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Packaging build entry points and platform requirements. -->

# Packaging

BlockProbe checks user-selected websites to help assess website filters. The
built-in regulator feeds focus on gambling domains; custom-site scans also
support personalized parental-control and adult-content-filter checks.

Build entry point: `python scripts/build.py TARGET` from the repository root.
Install Python 3.14+ and `python -m pip install -e ".[packaging]"` first.
Flet bundles the dependencies declared by `pyproject.toml` and Python 3.14.
Root `VERSION` is the canonical version source for package metadata, Flet
build version/build code, Windows installer and Arch package artifact.
`src/blockprobe/VERSION` is the synchronized runtime/package copy.
Hatchling converts SemVer-style prerelease labels to PEP 440 package metadata
(`1.4.0-alpha` becomes `1.4.0a0`).
Android build codes reserve ordered slots for alpha, beta, gamma, release
candidates and stable versions, so a later prerelease can update an earlier
one without changing the package identity or signing key.

For an emulator smoke build, verify `versionName` and `versionCode` in the APK
manifest against this source version and the build-code mapping. A previous
APK left installed on an emulator may report an older version; it does not
change the repository's canonical version.

The current source version is `1.4.0-alpha`; its Android code is
`100400010`. The earlier debug APK used for emulator UI checks was
`1.3.0-beta` (`100300020`), so it does not verify the current source.
```console
python scripts/build.py all
```

This rebuilds the targets available on the current host and cleans
target-specific previous artifacts first.

| Target | Artifact | Host/tooling |
| --- | --- | --- |
| `python` | host-specific `py3-none-<platform>` wheel and sdist | Python/pip; Rust/Cargo 1.98.1 for source builds |
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
checked at both 411dp and 360dp. An earlier web bundle was served at narrow and
wide browser sizes, but headless Chromium remained on the Flet startup screen.
The latest Release Web build showed Cargo emits `blockprobe_core.wasm` without
the `lib` prefix, at the release root and under `deps`. The builder now accepts
the unprefixed module name and the optional `lib` prefix, but this change still
needs an Emscripten runner to verify. Web packaging and runtime loading remain
unverified. The project owner
also exercises the app on Android. This is not a production-signed release.
The Release workflow builds and smoke-tests separate Windows, macOS and Linux
Python wheels, uploads one sdist, and builds Windows, macOS, Linux, APK, AAB,
web and iOS simulator targets. The Linux wheel was built and its installed
Rust library was loaded locally; Windows/macOS wheel checks are configured in
CI and remain unverified until that workflow runs.
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
