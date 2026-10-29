<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Supported install and artifact build instructions. -->

# Packaging

BlockProbe supports source/Python-package installation and builds desktop and
Android artifacts through Flet. The Python wheel and source archive, a Linux
portable tarball and a universal Android APK were built locally. The Linux
installer helper and a headless Linux app launch were smoke-checked. The APK
was verified with Android's APK signer and uses the default Android debug
certificate; it has not been installed on a physical device. The latest tag
run built Windows, macOS and Linux packages, while Android failed during its
runtime archive download. M6 remains open.

## Install from source or Python package

Python 3.14 or newer is required. pip installs the runtime dependencies declared
in the package metadata; the `desktop` extra adds Flet's desktop runtime.
Python installs require Python and pip on the device. They are not standalone
installers.

```console
python -m pip install blockprobe
blockprobe version
blockprobe self-check
```

For a local development install:

```console
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[desktop]"
python src/main.py
```

The same environment provides the CLI and `blockprobe serve --headless` local
web UI. The wheel and sdist include the bundled BetBlocker fallback signatures.

## Build artifacts

Install Python 3.14, then install the build extra:

```console
python -m pip install -e ".[packaging]"
python scripts/build.py python
```

This creates a wheel and sdist under `dist/packages/python/`. Flet packages the
application's declared project dependencies into a device runtime. All Flet
targets explicitly bundle Python 3.14, matching the minimum supported source
version; Python 3.15-only syntax is not used.

```console
python scripts/build.py windows --installer   # Windows + Inno Setup
python scripts/build.py macos --installer     # macOS; DMG is unsigned
python scripts/build.py linux                 # portable tar.gz
python scripts/build.py linux --installer     # also makes Arch package on Arch Linux
python scripts/build.py apk                   # Android APK
python scripts/build.py aab                   # Android App Bundle
python scripts/build.py web                   # web bundle
python scripts/build.py ios-simulator         # macOS + Xcode simulator app
```

Desktop builds must run on their target operating system. Android builds run on
Linux/macOS/Windows with the Android toolchain Flet installs. iOS simulator
builds require macOS and Xcode. The Windows installer requires Inno Setup. The
macOS DMG helper wraps an existing `.app`; it does not sign, notarize, or submit
the app. Android release signing keys must be supplied separately for a
publishable Play Store build. A debug APK can be installed for local testing.

| Target | Artifact produced | Current limits |
| --- | --- | --- |
| Python | wheel and sdist | Python/pip must already be installed |
| Windows | portable ZIP; optional per-user Setup EXE | unsigned; built on Windows |
| macOS | app ZIP; optional DMG | unsigned and not notarized; built on macOS |
| Linux | portable tar.gz; optional Arch `.pkg.tar.zst` | x86_64 built locally; Arch package built on Arch Linux; `.deb`, `.rpm`, AppImage and Flatpak are not built |
| Android | universal APK or AAB | APK locally built and signature-checked with debug certificate; no production signing key or Play Store submission |
| Web | static web bundle | hosting and base URL must be configured by the deployer |
| iOS | simulator app bundle | physical-device IPA, signing and App Store submission remain unimplemented |

CI is configured to build Python, Windows, macOS, Linux and Android APK
artifacts on a `v*` tag and attach them to the GitHub release. In the latest tag
run, Python, Windows, macOS and Linux package jobs passed. The APK job failed
while downloading the Android Python runtime archive; the workflow now caches
runtime archives and retries the APK build once after clearing the affected
arm64 archive. Separately, macOS CI failed because a test assumed a
refused local connection instead of allowing the timeout returned on macOS; the
test now accepts either transport failure. A local APK rebuild succeeded using
the cached runtime. These changes still need a new GitHub run. Release artifacts
are unsigned and are not published to app stores. Review
`.github/workflows/release.yaml` before creating a release tag.

### Linux installation

The default Linux download is a generic `tar.gz` containing a relocatable Flet
bundle, the AGPL license and `install.sh`. It is not a `.deb`, `.rpm` or Arch package,
so a graphical package manager or `pacman` cannot install the archive directly.
The helper installs per-user files without root and works independently of the
distribution's package manager. Extract it, then run the helper from the
extracted directory:

```console
tar -xzf BlockProbe-linux-portable.tar.gz
cd BlockProbe
./install.sh
```

The helper copies the complete bundle under `~/.local/share/blockprobe`, adds
a `blockprobe-desktop` launcher under `~/.local/bin`, installs the desktop
entry and icon for the current user, and places the license at
`~/.local/share/licenses/blockprobe/LICENSE`. The desktop environment then
shows BlockProbe in its application launcher. This same process is used on
Arch, Debian/Ubuntu, Fedora and other distributions; it does not invoke `yay`,
`pacman`, `apt` or `dnf`. On an Arch Linux build host, `python scripts/build.py
linux --installer` also creates a native `.pkg.tar.zst` with package metadata,
GTK dependencies and the AGPL license. Install it from the repository root with:

```console
sudo pacman -U dist/packages/linux/blockprobe-1.1.1-1-x86_64.pkg.tar.zst
```

A graphical package manager that supports local Arch packages can open this
file; `yay -U dist/packages/linux/blockprobe-1.1.1-1-x86_64.pkg.tar.zst` also
works. Pacman resolves the declared dependencies and keeps the license with the
installed package. Debian `.deb` and Fedora `.rpm` packages are not built. The
portable bundle must remain together because
its executable resolves the bundled Python runtime and libraries relative to
itself. Linux build dependencies are installed on Debian/Ubuntu with
`python scripts/install-linux-build-deps.py`; other distributions need the
equivalent GTK, GStreamer, clang/LLVM, linker and Ninja development packages.
The build helper discovers the build-tool package list from the installed Flet version.
The current Linux bundle was launched under Xvfb on the build host and remained
running until the smoke-test timeout. Run the app normally on a graphical Linux
desktop to use it; the smoke test did not exercise the full UI or network scan.

### Android installation

The local APK is `dist/packages/apk/blockprobe.apk`. It is a release-mode,
universal APK containing `arm64-v8a`, `armeabi-v7a` and `x86_64` native
libraries. Flet's default single APK targets all supported Android ABIs, which
increases download size; the current build is about 156 MB. It is signed with
the Android debug certificate for sideload testing. Production distribution
requires a privately managed release key and a device-install/runtime check.

## Optional container

The Dockerfile installs the declared desktop/web runtime dependencies and starts
the local web UI. Build from the repository root:

```console
docker build -f packaging/Dockerfile -t blockprobe .
docker run --rm -p 127.0.0.1:8550:8550 -v blockprobe-data:/data blockprobe
```

The container binds the web server inside the container to `0.0.0.0`; the
example publishes it only on the host loopback interface. Probe traffic follows
the container's network path, which may differ from the host device's VPN or
DNS path. Docker is optional and not required for ordinary installation.

## Scheduled scans

`blockprobe schedule install` writes user-level job files and prints the
activation command. Nothing is enabled automatically. The CLI and UI use the
same runtime renderer for systemd user units, launchd agents and Task Scheduler
jobs. Android background scheduling is not implemented; scans there must be
started by the user.

## Milestones

M6 is in progress: Python packages, Linux x86_64 and Android debug APK artifacts
were built locally, and the latest release run built Windows, macOS and Linux
packages. Android physical-device installation, Linux arm64, AppImage/Flatpak,
physical-device iOS packaging, production signing, notarization and store-ready
releases remain. M7 documentation is updated; the first release waits for M6
and a completed artifact review.
