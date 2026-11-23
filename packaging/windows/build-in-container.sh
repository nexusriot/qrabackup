#!/usr/bin/env bash
# Runs INSIDE the Windows builder image (packaging/windows/Dockerfile), with
# the repo mounted read-only at /src and the output dir at /out. Network off.
#
#   1. run the unit tests with Windows CPython under Wine (pure-logic modules)
#   2. PyInstaller -> qrabackup.exe
#   3. NSIS -> qrabackup-<ver>-windows-x64-setup.exe
set -euo pipefail

SRC=/src
OUT=/out
APP=qrabackup
PY='C:\Python\python.exe'
: "${VERSION:?VERSION must be set}"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"

WORK=/tmp/qrabuild
rm -rf "$WORK"; mkdir -p "$WORK"
trap 'wineserver -k 2>/dev/null || true' EXIT
cp -a "$SRC/qrabackup.py" "$SRC/qrabackup.spec" "$SRC/conftest.py" \
      "$SRC/README.md" "$SRC/qrabackup_app" "$SRC/tests" "$WORK/"
find "$WORK" -name __pycache__ -prune -exec rm -rf {} +
cd "$WORK"

echo ">> [windows] unit tests (Wine)"
# The Qt UI tests need a display; run the pure-logic suites only.
QT_QPA_PLATFORM=offscreen wine "$PY" -m unittest \
    tests.test_formats tests.test_model tests.test_parsers \
    tests.test_commands tests.test_settings 2>&1 | tail -n 3

echo ">> [windows] PyInstaller (VERSION=$VERSION)"
wine "$PY" -m PyInstaller --noconfirm --clean \
    --distpath 'Z:\tmp\qrabuild\dist' --workpath 'Z:\tmp\qrabuild\build' \
    qrabackup.spec
EXE="$WORK/dist/$APP.exe"
[ -f "$EXE" ] || { echo "error: $EXE not produced" >&2; exit 1; }

# VIProductVersion needs a purely numeric a.b.c.d.
v="${VERSION#v}"
num="$(printf '%s' "$v" | sed -n 's/^\([0-9]\+\(\.[0-9]\+\)\{0,3\}\).*/\1/p')"
num="${num:-0}"
while [ "$(printf '%s' "$num" | tr -cd . | wc -c)" -lt 3 ]; do num="$num.0"; done

mkdir -p "$OUT"
install -m 0644 "$EXE" "$OUT/${APP}-${VERSION}-windows-x64.exe"

echo ">> [windows] NSIS installer"
makensis -V2 \
    -DVERSION="$VERSION" -DVERSION4="$num" \
    -DEXE="$EXE" -DREADME="$WORK/README.md" \
    -DOUTFILE="$OUT/${APP}-${VERSION}-windows-x64-setup.exe" \
    "$SRC/packaging/windows/qrabackup.nsi"

echo ">> [windows] artifacts:"
( cd "$OUT" && ls -1 )
