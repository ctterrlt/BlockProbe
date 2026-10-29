#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Purpose: Package an already-built macOS application in an unsigned DMG.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 /path/to/BlockProbe.app /path/to/output.dmg" >&2
  exit 2
fi

app_path="$1"
output_path="$2"
if [[ ! -d "$app_path" || "$app_path" != *.app ]]; then
  echo "Expected an existing .app bundle: $app_path" >&2
  exit 1
fi
mkdir -p "$(dirname "$output_path")"
hdiutil create -volname "BlockProbe" -srcfolder "$app_path" -ov -format UDZO "$output_path"
