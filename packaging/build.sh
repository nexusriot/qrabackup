#!/usr/bin/env bash
# qrabackup — hermetic release builds in Docker.
#
#   packaging/build.sh [TARGET ...]
#
# Targets:
#   linux-amd64   standalone binary + .deb + .rpm  (x86_64)
#   linux-arm64   standalone binary + .deb + .rpm  (aarch64; QEMU on x86 hosts)
#   windows       qrabackup.exe + NSIS setup installer (x64, built under Wine)
#   all           all of the above (default)
#
# Each target builds a pinned builder image (base image by digest, apt from a
# fixed snapshot, pinned Python deps), then runs the build in a container with
# --network none and the repo mounted read-only. Output: dist/<target>/.
#
# Environment:
#   VERSION            version string (default: git describe, else __version__)
#   SOURCE_DATE_EPOCH  timestamp for reproducible metadata (default: last commit)
#   DOCKER             docker CLI to use (default: docker)
#
# Cross-arch (linux-arm64 on an x86 host) needs QEMU binfmt handlers:
#   docker run --privileged --rm tonistiigi/binfmt --install arm64
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
DOCKER="${DOCKER:-docker}"

APPVER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' qrabackup_app/__init__.py)"
VERSION="${VERSION:-$(git describe --tags --dirty 2>/dev/null || echo "$APPVER")}"
VERSION="${VERSION#v}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || echo 0)}"
export VERSION SOURCE_DATE_EPOCH

targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(all)
if [ "${targets[0]}" = all ]; then
    targets=(linux-amd64 linux-arm64 windows)
fi

need_binfmt() {  # $1 = docker platform, e.g. linux/arm64
    local host
    host="$(uname -m)"
    case "$1:$host" in
        linux/amd64:x86_64|linux/arm64:aarch64|linux/arm64:arm64) return 0 ;;
    esac
    if ! "$DOCKER" run --rm --platform "$1" --network none \
            ubuntu:22.04 true >/dev/null 2>&1; then
        cat >&2 <<MSG
error: cannot run $1 containers on this $host host.
Register QEMU binfmt handlers first (one-time, needs --privileged):
    docker run --privileged --rm tonistiigi/binfmt --install ${1#linux/}
MSG
        exit 1
    fi
}

build_image() {  # $1 platform, $2 dockerfile, $3 tag
    echo "== image $3 ($1)"
    "$DOCKER" buildx build --load --platform "$1" \
        -f "$2" -t "$3" "$ROOT/packaging"
}

run_build() {  # $1 platform, $2 image, $3 out dir, $4 in-container script
    mkdir -p "$3"
    "$DOCKER" run --rm --platform "$1" --network none \
        -e VERSION -e SOURCE_DATE_EPOCH \
        -v "$ROOT:/src:ro" -v "$3:/out" \
        "$2" bash -c "\"$4\"; rc=\$?; chown -R $(id -u):$(id -g) /out; exit \$rc"
}

for t in "${targets[@]}"; do
    case "$t" in
        linux-amd64|linux-arm64)
            arch="${t#linux-}"
            plat="linux/$arch"
            need_binfmt "$plat"
            img="qrabackup-builder-linux:$arch"
            build_image "$plat" packaging/linux/Dockerfile "$img"
            echo "== build $t (VERSION=$VERSION)"
            run_build "$plat" "$img" "$ROOT/dist/$t" \
                /src/packaging/linux/build-in-container.sh
            ;;
        windows)
            img="qrabackup-builder-windows:x64"
            build_image linux/amd64 packaging/windows/Dockerfile "$img"
            echo "== build $t (VERSION=$VERSION)"
            run_build linux/amd64 "$img" "$ROOT/dist/windows-x64" \
                /src/packaging/windows/build-in-container.sh
            ;;
        *)
            echo "unknown target: $t (linux-amd64 | linux-arm64 | windows | all)" >&2
            exit 2
            ;;
    esac
done

echo
echo "== artifacts"
( cd "$ROOT/dist" && find . -mindepth 2 -maxdepth 2 -type f \
    \( -name '*.deb' -o -name '*.rpm' -o -name '*.exe' -o -name 'qrabackup-*' \) \
    -exec sha256sum {} + | sed 's#  \./#  #' > SHA256SUMS ) || true
cat "$ROOT/dist/SHA256SUMS"
