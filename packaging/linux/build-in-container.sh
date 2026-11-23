#!/usr/bin/env bash
# Runs INSIDE the Linux builder image (packaging/linux/Dockerfile), with the
# repo mounted read-only at /src and the output dir at /out. Network is off.
#
#   1. copy the sources to a scratch tree (the mount stays pristine)
#   2. run the unit tests (offscreen Qt) against the bundled toolchain
#   3. PyInstaller onefile binary
#   4. .deb (scripts/build-deb.sh) and .rpm (rpmbuild) around that binary
set -euo pipefail

SRC=/src
OUT=/out
APP=qrabackup
: "${VERSION:?VERSION must be set}"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
export HOME=/tmp/home
mkdir -p "$HOME"

DEB_ARCH="$(dpkg --print-architecture)"            # amd64 | arm64
RPM_ARCH="$(uname -m)"                              # x86_64 | aarch64

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -a "$SRC/qrabackup.py" "$SRC/qrabackup.spec" "$SRC/conftest.py" \
      "$SRC/README.md" "$SRC/qrabackup_app" "$SRC/scripts" "$SRC/tests" "$WORK/"
find "$WORK" -name __pycache__ -prune -exec rm -rf {} +
cd "$WORK"

echo ">> [$DEB_ARCH] unit tests"
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t . 2>&1 | tail -n 3

echo ">> [$DEB_ARCH] PyInstaller (VERSION=$VERSION)"
python -m PyInstaller --noconfirm --clean \
    --distpath "$WORK/dist" --workpath "$WORK/build/pyi" qrabackup.spec
BIN="$WORK/dist/$APP"
[ -x "$BIN" ] || { echo "error: $BIN not produced" >&2; exit 1; }

# Smoke test: the frozen binary must start, import Qt and build the window.
echo ">> [$DEB_ARCH] smoke test (offscreen, 5s)"
set +e
QT_QPA_PLATFORM=offscreen XDG_CONFIG_HOME="$WORK/cfg" timeout 5 "$BIN"
rc=$?
set -e
if [ "$rc" -ne 124 ]; then
    echo "error: frozen binary exited early with code $rc" >&2
    exit 1
fi

mkdir -p "$OUT"
install -m 0755 "$BIN" "$OUT/${APP}-${VERSION}-linux-${DEB_ARCH}"

echo ">> [$DEB_ARCH] .deb"
BIN="$BIN" NO_REBUILD=1 OUTDIR="$OUT" ARCH="$DEB_ARCH" VERSION="$VERSION" \
    fakeroot ./scripts/build-deb.sh >/dev/null

echo ">> [$DEB_ARCH] .rpm"
# RPM Version may not contain '-': "0.5.0-3-gabc1234-dirty" becomes
# Version 0.5.0, Release 0.3.gabc1234.dirty (sorts before a tagged 0.5.0-1).
v="${VERSION#v}"
case "$v" in
    [0-9]*) base="${v%%-*}"; rest="${v#"$base"}" ;;
    *)      base="0.0.0";    rest="-git-$v" ;;
esac
if [ -n "$rest" ]; then
    release="0$(printf '%s' "$rest" | tr -c 'A-Za-z0-9.\n' '.')"
else
    release="1"
fi
TOP="$WORK/rpmbuild"
mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS,BUILDROOT}
cp "$BIN" scripts/deb/qrabackup.desktop README.md "$TOP/SOURCES/"
sed -e "s/@RPM_VERSION@/$base/" -e "s/@RPM_RELEASE@/$release/" \
    -e "s/@RPM_ARCH@/$RPM_ARCH/" \
    "$SRC/packaging/linux/rpm.spec.in" > "$TOP/SPECS/$APP.spec"
rpmbuild --quiet -bb --nodeps \
    --define "_topdir $TOP" \
    --define "_buildhost qrabackup-builder" \
    --define "use_source_date_epoch_as_buildtime 1" \
    --define "clamp_mtime_to_source_date_epoch 1" \
    "$TOP/SPECS/$APP.spec"
cp "$TOP"/RPMS/*/*.rpm "$OUT/"

echo ">> [$DEB_ARCH] artifacts:"
( cd "$OUT" && ls -1 )
