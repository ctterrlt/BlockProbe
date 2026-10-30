#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Purpose: Prepare and collect the Flet Android APK inside F-Droid's build VM.
#
# F-Droid checks out the pinned Flutter SDK as a srclib and puts it on PATH.
# Flet's Android pipeline downloads the Python runtime and Android wheels, so
# the compile happens in prepare() and build() copies the finished APK into the
# output path recorded in the metadata. The source tree stays clean, which also
# keeps F-Droid's generated source tarball small.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/src/blockprobe/VERSION")"
VERSION_CODE="$(
  python3 - "$VERSION" <<'PY'
import re
import sys

match = re.fullmatch(
    r"(\d+)\.(\d+)\.(\d+)"
    r"(?:-(alpha|beta|gamma|rc)(?:\.(\d+))?)?"
    r"(?:\+[0-9A-Za-z.-]+)?",
    sys.argv[1],
)
if not match:
    raise SystemExit(f"VERSION is not major.minor.patch[-prerelease]: {sys.argv[1]}")
major, minor, patch = map(int, match.groups()[:3])
if any(part >= 1000 for part in (major, minor, patch)):
    raise SystemExit("Each VERSION component must be less than 1000")
stage, serial_text = match.groups()[3:]
stage_slot = {"alpha": 10, "beta": 20, "gamma": 30, "rc": 40}.get(stage, 99)
serial = int(serial_text or 0)
if serial > 9:
    raise SystemExit("Prerelease serials must be between 0 and 9")
if stage is not None:
    stage_slot += serial
code = (major * 1_000_000 + minor * 1_000 + patch) * 100 + stage_slot
if code <= 0 or code > 2_100_000_000:
    raise SystemExit("VERSION produces an invalid Android version code")
print(code)
PY
)"

FLET_VERSION="${BLOCKPROBE_FLET_VERSION:-1.0.5}"
BUILD_DIR="${BLOCKPROBE_FDROID_BUILD_DIR:-/tmp/blockprobe-fdroid-build}"
VENV="${BLOCKPROBE_FDROID_VENV:-/tmp/blockprobe-fdroid-venv}"

BUILD_DIR="$(realpath -m "$BUILD_DIR")"
VENV="$(realpath -m "$VENV")"

case "$BUILD_DIR" in
  "$ROOT" | "$ROOT"/*)
    echo "BLOCKPROBE_FDROID_BUILD_DIR must be outside the source tree" >&2
    exit 2
    ;;
esac

ensure_flet() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to install the Flet CLI" >&2
    exit 1
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "Flet 1.x requires Python 3.10 or newer" >&2
    exit 1
  }
  if [ -x "$VENV/bin/flet" ] && "$VENV/bin/flet" --version 2>/dev/null | grep -q "Flet: $FLET_VERSION"; then
    return
  fi
  echo "Installing flet[cli]==$FLET_VERSION into $VENV"
  python3 -m venv --clear "$VENV"
  "$VENV/bin/python" -m pip install --disable-pip-version-check --no-input --upgrade pip
  "$VENV/bin/python" -m pip install --disable-pip-version-check --no-input "flet[cli]==$FLET_VERSION"
}

prepare() {
  ensure_flet
  rm -rf "$BUILD_DIR"
  mkdir -p "$BUILD_DIR"
  echo "Copying $ROOT to $BUILD_DIR"
  tar \
    --exclude=.git \
    --exclude=.venv \
    --exclude=build \
    --exclude=dist \
    --exclude='__pycache__' \
    --exclude=.pytest_cache \
    --exclude=.ruff_cache \
    --exclude=.mypy_cache \
    -C "$ROOT" -cf - . | tar -C "$BUILD_DIR" -xf -

  export PATH="$VENV/bin:$PATH"
  export FLET_CLI_NO_RICH_OUTPUT=1
  cd "$BUILD_DIR"

  echo "Building BlockProbe $VERSION ($VERSION_CODE) with Flet $FLET_VERSION"
  # F-Droid checks out Flutter as a bare srclib, without the engine artifacts
  # that the official Flutter archive includes. Flet skips its own SDK install
  # when it finds this SDK on PATH, so populate the Android artifacts first.
  flutter precache --android
  flet build apk \
    --yes \
    --no-rich-output \
    --python-version 3.14 \
    --build-version "$VERSION" \
    --build-number "$VERSION_CODE" \
    --no-compile-app \
    --no-compile-packages \
    -o out

  if [ ! -d "$BUILD_DIR/out" ]; then
    echo "Flet did not create $BUILD_DIR/out" >&2
    exit 1
  fi
}

build() {
  local apk
  local count
  count="$(find "$BUILD_DIR/out" -maxdepth 1 -type f -name '*.apk' | wc -l)"
  if [ "$count" -ne 1 ]; then
    echo "Expected exactly one APK in $BUILD_DIR/out, found $count" >&2
    exit 1
  fi
  apk="$(find "$BUILD_DIR/out" -maxdepth 1 -type f -name '*.apk' -print -quit)"
  mkdir -p "$ROOT/build/flet/apk"
  cp "$apk" "$ROOT/build/flet/apk/blockprobe.apk"
  test -s "$ROOT/build/flet/apk/blockprobe.apk"
  sha256sum "$ROOT/build/flet/apk/blockprobe.apk"
}

case "${1:-}" in
  prepare)
    prepare
    ;;
  build)
    build
    ;;
  clean)
    rm -rf "$BUILD_DIR"
    ;;
  *)
    echo "usage: $0 {prepare|build|clean}" >&2
    exit 2
    ;;
esac
