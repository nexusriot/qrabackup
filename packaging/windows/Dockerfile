# syntax=docker/dockerfile:1
#
# Hermetic Windows (x86-64) builder for qrabackup: PyInstaller runs under Wine
# against a real CPython for Windows, and NSIS (native Linux makensis) wraps
# the resulting qrabackup.exe into a setup installer.
#
#   * Base image pinned by digest; apt from a fixed snapshot.debian.org time.
#   * CPython comes from the official "python" NuGet package (a plain zip of
#     a full install, so no MSI needs to run under Wine), sha256-verified.
#   * Python deps pinned in packaging/requirements-windows.txt (--no-deps).
#   * The build itself runs with --network none (see packaging/build.sh).
ARG BASE_IMAGE=debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
FROM ${BASE_IMAGE}

ARG APT_SNAPSHOT=20261120T000000Z
# 3.11, not 3.12+: CPython 3.12's _wmi module calls propsys.VariantToString,
# which Wine 8 (bookworm) does not implement, so pip/platform crash.
ARG PYTHON_VERSION=3.11.9
ARG PYTHON_NUPKG_SHA256=9283876d58c017e0e846f95b490da3bca0fc0a6ee1134b2870677cfb7eec3c67

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    WINEPREFIX=/opt/wine \
    WINEARCH=win64 \
    WINEDEBUG=-all \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN set -eu; \
    if [ -n "${APT_SNAPSHOT}" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources; \
        printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bookworm main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bookworm-updates main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s bookworm-security main\n' \
            "$APT_SNAPSHOT" "$APT_SNAPSHOT" "$APT_SNAPSHOT" > /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        wine wine64 nsis ca-certificates curl unzip; \
    rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    wine wineboot --init; wineserver -w; \
    # CPython 3.12 needs Windows 8.1+; Wine 8 reports Windows 7 by default.
    wine reg add 'HKCU\Software\Wine' /v Version /d win10 /f; wineserver -w; \
    curl -fsSL -o /tmp/python.nupkg \
        "https://www.nuget.org/api/v2/package/python/${PYTHON_VERSION}"; \
    echo "${PYTHON_NUPKG_SHA256}  /tmp/python.nupkg" | sha256sum -c -; \
    unzip -q /tmp/python.nupkg 'tools/*' -d /tmp/py; \
    mv /tmp/py/tools "$WINEPREFIX/drive_c/Python"; \
    rm -rf /tmp/py /tmp/python.nupkg

COPY requirements-windows.txt /tmp/requirements-windows.txt
RUN set -eu; \
    wine 'C:\Python\python.exe' -m pip install --no-deps --only-binary=:all: \
        -r 'Z:\tmp\requirements-windows.txt'; \
    wine 'C:\Python\python.exe' -m pip check; \
    wineserver -w

WORKDIR /work
