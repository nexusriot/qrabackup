#!/usr/bin/env bash
# qrabackup — Debian package assembly
#
# Wraps the PyInstaller binary produced by scripts/build.sh into a .deb for the
# host architecture. Uses plain dpkg-deb; no debhelper needed.
#
# Output: dist/qrabackup_<version>_<arch>.deb

set -euo pipefail

cd "$(dirname "$0")/.."

ROOT="$PWD"
APP="qrabackup"
# Fall back to the package's own __version__ when the caller (or the Makefile's
# `git describe`) has nothing to offer — never to a literal that can go stale.
VERSION="${VERSION:-$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/qrabackup_app/__init__.py")}"
VERSION="${VERSION#v}"                       # drop leading "v"
# Debian requires Version to start with a digit. `git describe --always`
# falls back to a bare commit hash when no tags exist — prefix it so dpkg
# accepts it (e.g. "dee2e72-dirty" -> "0.0.0~git.dee2e72-dirty").
case "$VERSION" in
    [0-9]*) : ;;
    *)      VERSION="0.0.0~git.${VERSION}" ;;
esac
ARCH="${ARCH:-$(dpkg --print-architecture 2>/dev/null || uname -m)}"

BIN="$ROOT/dist/$APP"
# Rebuild when the binary is missing *or* stale: packaging a leftover binary
# from an earlier version produces a .deb whose control file and executable
# disagree about what they are.
stale=""
if [ ! -f "$BIN" ]; then
    stale=yes
elif [ "$ROOT/qrabackup.py" -nt "$BIN" ] || [ "$ROOT/qrabackup.spec" -nt "$BIN" ]; then
    stale=yes
elif [ -n "$(find "$ROOT/qrabackup_app" -name '*.py' -newer "$BIN" -print -quit)" ]; then
    stale=yes
fi
if [ -n "$stale" ]; then
    echo ">> binary $BIN missing or older than the sources; building first"
    "$ROOT/scripts/build.sh"
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/DEBIAN"
install -d "$STAGE/usr/bin"
install -d "$STAGE/usr/share/applications"
install -d "$STAGE/usr/share/doc/$APP"

install -m 0755 "$BIN"                             "$STAGE/usr/bin/$APP"
install -m 0644 "$ROOT/scripts/deb/$APP.desktop"   "$STAGE/usr/share/applications/$APP.desktop"

# changelog (gzipped, as required by lintian)
if [ -f "$ROOT/scripts/deb/changelog" ]; then
    gzip -9n -c "$ROOT/scripts/deb/changelog" \
        > "$STAGE/usr/share/doc/$APP/changelog.Debian.gz"
    chmod 0644 "$STAGE/usr/share/doc/$APP/changelog.Debian.gz"
fi

if [ -f "$ROOT/README.md" ]; then
    install -m 0644 "$ROOT/README.md" "$STAGE/usr/share/doc/$APP/README.md"
fi

INSTALLED_SIZE=$(du -sk "$STAGE/usr" | cut -f1)
sed -e "s/@VERSION@/${VERSION}/g" \
    -e "s/@ARCH@/${ARCH}/g" \
    -e "s/@SIZE@/${INSTALLED_SIZE}/g" \
    "$ROOT/scripts/deb/DEBIAN/control.tpl" > "$STAGE/DEBIAN/control"

( cd "$STAGE" && find usr -type f -print0 \
    | xargs -0 md5sum > "$STAGE/DEBIAN/md5sums" )
chmod 0644 "$STAGE/DEBIAN/md5sums"

mkdir -p "$ROOT/dist"
DEB="$ROOT/dist/${APP}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB"

echo
echo "Built: $DEB"
dpkg-deb -I "$DEB" || true
