<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Supported install and artifact build instructions. -->

# Packaging

BlockProbe supports source/Python-package installation and builds desktop and
Android artifacts through Flet. On 2026-11-10, the current source version
`1.4.0` (Android code `100400099`) built a universal APK and an AAB locally.
Both are local artifacts, not release artifacts. The APK uses Flet's debug
certificate and was installed on the configured Android 35 emulator. The AAB
manifest was verified locally but has not been checked with the protected
release key. The APK was exercised at 411x914dp,
360x800dp and 600x800dp. The Profile resolver, Android Settings handoff,
scheduled-scan explanation, mobile navigation and light/dark appearance were
checked. No physical-device ADB session was used. Production distribution
still requires the protected release key and successful hosted Release
validation; M6 remains open until a signed artifact is reviewed.

The latest Release CI Web job showed Cargo producing `blockprobe_core.wasm`
both at the release root and under `deps`; Emscripten omits the `lib` prefix
used by native library outputs. The builder now searches for that unprefixed
name as well as the optional `lib`-prefixed form and stages it under Pyodide's
`.so` loader name. Tests cover root and nested outputs. This workspace does not
have `emcc`, so a successful Emscripten rebuild and Web runtime load still need
confirmation from CI.

## Install from source or Python package

Python 3.14 or newer is required. pip installs the runtime dependencies declared
in the package metadata; the `desktop` extra adds Flet's desktop runtime.
Python installs require Python and pip on the device. They are not standalone
installers. The optional curl cross-check uses a system `curl` executable when
available; it is not a required dependency and is not bundled in Android builds.
Building from source also requires Rust and Cargo. `scripts/build.py` compiles
the Rust scan library before producing a Flet app. The wheel build hook also
compiles the host library offline when building a wheel from an sdist or source
tree that does not already contain it. Android builds need the Android NDK and
Rust targets for arm64-v8a, armeabi-v7a and x86_64; Web uses the matching
Emscripten/Pyodide target. Flet includes platform libraries, and Android
extracts the Python package so the lazy `ctypes` loader can open its library
from a filesystem path. The Rust toolchain is pinned in `rust-toolchain.toml`.

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
web UI. Wheels include the host platform's Rust library and are marked non-pure
with a `py3-none-<platform>` wheel tag. Source distributions include the Rust
workspace and build hook; installing from an sdist therefore requires Cargo.
Both include the bundled BetBlocker fallback signatures.

## Build artifacts

The current version is `1.4.0`. Mobile startup colors follow the
system appearance, and the splash screen uses the BlockProbe app icon. Android
and iOS use a three-site scan concurrency default; other platforms use eight.
Users can change scan concurrency in Settings, and the value is saved with the
other local settings.

The root `VERSION` file is canonical; `src/blockprobe/VERSION` is its packaged
runtime copy. An installed emulator app can lag behind
the working tree, so inspect the APK's manifest before treating a run as
current-source verification.

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
python scripts/build.py all                   # every target supported on this host
```

`all` rebuilds Python, the host-native desktop app, Android APK/AAB and web.
On macOS it also builds the iOS simulator app. Windows, macOS and iOS outputs
cannot be cross-built from Linux; run the command on each native host or use
the release workflow matrix. Each target's old generated build and artifact
directories are removed before rebuilding.

Desktop builds must run on their target operating system. Android builds run on
Linux/macOS/Windows with the Android toolchain Flet installs. iOS simulator
builds require macOS and Xcode. The Windows installer requires Inno Setup. The
macOS DMG helper wraps an existing `.app`; it does not sign, notarize, or submit
the app. Android release builds read the signing values from Flet's
`FLET_ANDROID_SIGNING_KEY_*` environment variables; the GitHub release workflow
maps protected repository secrets to them. Builds without a key use Flet's
debug certificate for local testing.

| Target | Artifact produced | Current limits |
| --- | --- | --- |
| Python | host-specific wheel and sdist | Python/pip; Rust/Cargo when building from source |
| Windows | portable ZIP; optional per-user Setup EXE | unsigned; built on Windows |
| macOS | app ZIP; optional DMG | unsigned and not notarized; built on macOS |
| Linux | portable tar.gz; optional Arch `.pkg.tar.zst` | x86_64 built locally; Arch package built on Arch Linux; `.deb`, `.rpm`, AppImage and Flatpak are not built |
| Android | universal APK or AAB | tag releases use the configured Android release key; local builds without signing variables use Flet's debug key; no Play Store submission |
| Web | static web bundle | hosting and base URL must be configured by the deployer |
| iOS | simulator app bundle | physical-device IPA, signing and App Store submission remain unimplemented |

### Android release signing

Create one long-lived upload keystore and keep a secure backup outside this
repository. This prompts for its passwords and certificate details:

```console
keytool -genkeypair -v -keystore blockprobe-upload.jks \
  -alias blockprobe -keyalg RSA -keysize 2048 -validity 10000
```

Encode the keystore as one base64 line (`base64 -w 0 blockprobe-upload.jks` on
GNU/Linux), then add these repository Actions secrets:

- `ANDROID_KEYSTORE_BASE64`: the one-line base64 keystore contents
- `ANDROID_KEYSTORE_PASSWORD`: the keystore password
- `ANDROID_KEY_PASSWORD`: the private-key password
- `ANDROID_KEY_ALIAS`: `blockprobe` for the example command

Do not rotate or replace this key for later APK/AAB updates. Tagged releases
fail before publication if any signing secret is missing or invalid. Manual
branch builds may omit all four secrets and use Flet's debug key for testing.
F-Droid builds the source independently and signs its own APK.

The release workflow builds one host-specific Python wheel on Linux, Windows
and macOS, plus one sdist from Linux. It uploads the wheels under distinct
artifact names; their platform tags keep release filenames distinct. The
workflow also builds Windows, macOS, Linux, Android APK/AAB, web and iOS
simulator artifacts on their supported runners. GitHub workflows install
through pinned `uv` with `uv.lock`; the Release job checks APK manifests with
`aapt2` and AAB manifests with bundletool 1.18.2. Both package verifiers require
`INTERNET`, `ACCESS_NETWORK_STATE` and the scan foreground-service entries.
Local YAML and shell checks pass, but the current worktree has not run on
GitHub Actions. Before these edits, the CI runs on `main` and `dev` passed,
Release run `42895680094` failed its seven native package jobs because it
installed an incompatible latest Flet CLI, and F-Droid run `42896467596` was
skipped after that Release failure. A manual workflow
run builds and uploads CI artifacts without publishing a release; a tag push
builds the matrix and publishes a release, marked as a prerelease when its tag
contains a hyphen. Android runtime downloads are cached and retried once after
a failed archive download. Tagged Android builds require these GitHub Actions
secrets: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_PASSWORD` and `ANDROID_KEY_ALIAS`. The same permanent key signs
the APK and AAB on each release. Manual non-tag builds can still use Flet's
debug key when those secrets are absent. Release signatures are checked before
the artifacts are uploaded. Keep the keystore backed up securely and reuse it
for every update; do not commit it to the repository. F-Droid builds and signs
its own APK independently.

The old 1.2.1-alpha GitHub release entry was removed while its tag and commit
were preserved. Other historical Git tags remain unchanged.
Review `.github/workflows/release.yaml` before creating a prerelease tag.

The separate `F-Droid` workflow renders the official metadata and opens or
updates the `fdroiddata` merge request. It never creates, edits or deletes
GitHub releases, tags or commits. See [FDROID.md](FDROID.md) for the one-time
GitLab setup, publication limits and local validation commands.

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
sudo pacman -U dist/packages/linux/blockprobe-*-1-x86_64.pkg.tar.zst
```

A graphical package manager that supports local Arch packages can open this
file; `yay -U dist/packages/linux/blockprobe-*-1-x86_64.pkg.tar.zst` also
works. Pacman resolves the declared dependencies and keeps the license with the
installed package. Debian `.deb` and Fedora `.rpm` packages are not built. The
portable bundle must remain together because
its executable resolves the bundled Python runtime and libraries relative to
itself. Linux build dependencies are installed on Debian/Ubuntu with
`python scripts/install-linux-build-deps.py`; other distributions need the
equivalent GTK, GStreamer, clang/LLVM, linker and Ninja development packages.
The build helper discovers the build-tool package list from the installed Flet version.
Release CI resolves that list first, restores the packages from an APT cache and
only runs a full install when a package is still missing; this avoids repeating
the multi-minute dependency download on every Linux build. The cache path is
configured but has not yet been exercised on GitHub Actions, so the timing
improvement is not measured there.
The current Linux bundle was launched under Xvfb on the build host and remained
running until the smoke-test timeout. Run the app normally on a graphical Linux
desktop to use it; the smoke test did not exercise the full UI or network scan.

### Android installation

The local APK is `dist/packages/apk/blockprobe.apk`. It is a release-mode,
universal APK containing `arm64-v8a`, `armeabi-v7a` and `x86_64` native
libraries. Flet's default single APK targets all supported Android ABIs, which
increases download size; the current build is about 159 MiB (167 MB decimal).
It is signed with the Android debug certificate for emulator testing.
Production distribution requires the protected release key and Release
workflow validation.
For local UI verification, install only on the configured AVD and pin ADB
commands to `emulator-5554`. A debug APK is a smoke-test artifact, not a release.

The APK and AAB manifests declare normal `INTERNET` and
`ACCESS_NETWORK_STATE` permissions. `INTERNET` allows network sockets;
`ACCESS_NETWORK_STATE` lets Profile read active network and resolver details.
Android grants both at installation, with no runtime prompt for either.
User-started scans run in a `dataSync` foreground service, and Android 13+
requests `POST_NOTIFICATIONS` at runtime. Denying notification permission does
not cancel the scan. Android scan scheduling is not implemented, and the
foreground service does not promise survival after process death or force-stop.
Inspect the built APK's manifest for these entries before treating a build as
verified.

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

M6 is in progress. Local checks cover the Linux platform wheel, Linux x86_64,
and Rust libraries for Android arm64-v8a, armeabi-v7a and x86_64. The Android
APK and AAB were separately built from source version 1.4.0; the APK manifest
passed `aapt2` verification and the AAB manifest passed bundletool 1.18.2
verification. The Android 35 Pixel 7 emulator was checked at 411x914dp,
360x800dp and 600x800dp. Profile showed the emulator resolver `10.0.2.3`; both
network permissions were granted at install. Mobile navigation, Android
Settings, the no-scheduling explanation and theme selection were exercised.
The Python 3.14 suite passed 344 tests, Rust passed 10 tests, and Ruff, MyPy,
Cargo format, YAML parsing and workflow shell syntax passed. No physical phone
was attached. The headless web smoke check
loads the bundle but remains on the Flet startup screen, so web runtime
interaction is not yet verified.
Windows, macOS and iOS simulator artifacts are configured in the native-host
CI matrix, but their native-library load checks have not run in this workspace.
The iOS simulator and Pyodide/Web dynamic-library paths remain unverified.
F-Droid recipe execution with the pinned Rust toolchain also remains
unverified. Linux arm64, AppImage/Flatpak, physical-device iOS
packaging, production signing, notarization and store-ready releases remain.
M7 documentation is updated; the first stable release waits for M6 and a
completed artifact review.
