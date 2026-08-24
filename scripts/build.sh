#!/usr/bin/env bash
# qrabackup — PyInstaller build
#
# Provisions a project-local .venv (so we never touch system site-packages),
# installs runtime + build deps, then runs PyInstaller via qrabackup.spec.
# Output: dist/qrabackup
#
# Usage:
#   scripts/build.sh            # build standalone binary
#   scripts/build.sh --run      # provision .venv and run from source
#   scripts/build.sh --test     # run the unit test suite (offscreen)
#
# PyInstaller does NOT cross-compile; the binary is for the host arch.

set -euo pipefail

cd "$(dirname "$0")/.."

ROOT="$PWD"
VENV="${VENV:-$ROOT/.venv}"
PY="${PY:-python3}"
APP="qrabackup"
SPEC="$ROOT/$APP.spec"

mode="build"
case "${1:-}" in
    --run)  mode="run" ;;
    --test) mode="test" ;;
esac

if [ ! -x "$VENV/bin/python" ]; then
    echo ">> creating venv in $VENV"
    "$PY" -m venv "$VENV"
fi

# shellcheck disable=SC1091
. "$VENV/bin/activate"

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r "$ROOT/requirements.txt"

if [ "$mode" = "run" ]; then
    exec python "$ROOT/$APP.py"
fi

if [ "$mode" = "test" ]; then
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
    exec python -m unittest discover -s "$ROOT/tests" -t "$ROOT" -v
fi

python -m pip install --quiet pyinstaller

echo ">> building $APP (VERSION=${VERSION:-unset})"
cd "$ROOT"
rm -rf build/pyi dist/"$APP"
python -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$ROOT/dist" \
    --workpath "$ROOT/build/pyi" \
    "$SPEC"

OUT="$ROOT/dist/$APP"
if [ ! -f "$OUT" ]; then
    echo "error: expected $OUT not produced" >&2
    exit 1
fi

echo
echo "Built: $OUT"
file "$OUT" || true
