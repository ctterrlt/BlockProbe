#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_DIR=${XDG_DATA_HOME:-"$HOME/.local/share"}/blockprobe
BIN_DIR=${XDG_BIN_HOME:-"$HOME/.local/bin"}
DESKTOP_DIR=${XDG_DATA_HOME:-"$HOME/.local/share"}/applications
SHARE_DIR=${XDG_DATA_HOME:-"$HOME/.local/share"}

if [ ! -x "$SOURCE_DIR/blockprobe" ]; then
    printf 'Could not find the packaged executable in %s\n' "$SOURCE_DIR" >&2
    exit 1
fi
if [ ! -f "$SOURCE_DIR/LICENSE" ]; then
    printf 'Could not find the BlockProbe license file in %s\n' "$SOURCE_DIR" >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR"
cp -R "$SOURCE_DIR"/. "$INSTALL_DIR"/
mkdir -p "$SHARE_DIR/licenses/blockprobe"
cp "$SOURCE_DIR/LICENSE" "$SHARE_DIR/licenses/blockprobe/LICENSE"

cat > "$BIN_DIR/blockprobe-desktop" <<EOF
#!/bin/sh
exec "$INSTALL_DIR/blockprobe" "\$@"
EOF
chmod 755 "$BIN_DIR/blockprobe-desktop"

if [ -d "$INSTALL_DIR/share" ]; then
    cp -R "$INSTALL_DIR/share/." "$SHARE_DIR/"
fi
for desktop_entry in "$DESKTOP_DIR"/*.desktop; do
    [ -f "$desktop_entry" ] || continue
    if grep -q 'io.github.ctterrlt.blockprobe' "$desktop_entry"; then
        escaped_bin_dir=$(printf '%s' "$BIN_DIR" | sed 's/[\\&|]/\\&/g')
        sed "s|^Exec=.*|Exec=\"$escaped_bin_dir/blockprobe-desktop\" %U|" \
            "$desktop_entry" > "$desktop_entry.tmp"
        mv "$desktop_entry.tmp" "$desktop_entry"
    fi
done

printf 'Installed BlockProbe. Launch it with: %s/blockprobe-desktop\n' "$BIN_DIR"
printf 'Desktop entries are installed under: %s\n' "$DESKTOP_DIR"
