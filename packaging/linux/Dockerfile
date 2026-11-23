# syntax=docker/dockerfile:1
#
# Hermetic Linux builder for qrabackup (amd64 and arm64 from the same file).
#
#   * Base image pinned by multi-arch index digest (ubuntu:22.04 / jammy):
#     glibc 2.35 keeps the binary runnable on Ubuntu 22.04+, Debian 12+,
#     Fedora 36+, RHEL/Alma/Rocky 10.
#   * apt resolves against a fixed snapshot.ubuntu.com timestamp, so the
#     package set never drifts (override with --build-arg APT_SNAPSHOT=...).
#   * Python build deps are version-pinned (packaging/requirements-linux.txt).
#   * The build itself runs with --network none (see packaging/build.sh).
ARG BASE_IMAGE=ubuntu:22.04@sha256:f64b2ffc2f10ab8f8505aa56b2664bd846cbb9df87c7e2532e30f7667021c0e6
FROM ${BASE_IMAGE}

ARG APT_SNAPSHOT=20261120T000000Z
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# APT_SNAPSHOT must not predate BASE_IMAGE (its preinstalled packages must
# exist in the snapshot). snapshot.ubuntu.com/ubuntu/<timestamp> serves every
# architecture (amd64 and the ports arches such as arm64).
RUN set -eu; \
    if [ -n "${APT_SNAPSHOT}" ]; then \
        # snapshot.ubuntu.com is HTTPS-only: bootstrap the CA bundle from the
        # image's default archive first (affects TLS trust only — every
        # package is still verified against the archive's GPG signatures).
        apt-get update; \
        apt-get install -y --no-install-recommends ca-certificates; \
        base="https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT}"; \
        printf 'deb %s jammy main universe\ndeb %s jammy-updates main universe\ndeb %s jammy-security main universe\n' \
            "$base" "$base" "$base" > /etc/apt/sources.list; \
    fi; \
    apt-get update --error-on=any; \
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pyqt5 \
        binutils file dpkg-dev rpm fakeroot; \
    rm -rf /var/lib/apt/lists/*

COPY requirements-linux.txt /tmp/requirements-linux.txt
RUN python3 -m venv --system-site-packages /opt/venv \
 && /opt/venv/bin/pip install --no-deps -r /tmp/requirements-linux.txt \
 && /opt/venv/bin/pip check

ENV PATH=/opt/venv/bin:$PATH
WORKDIR /work
