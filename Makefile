# qrabackup — build orchestration
#
# Thin wrapper over scripts/*.sh. Every target shells out to the matching
# script so the Makefile and scripts never drift.
#
#   make              # standalone binary -> dist/qrabackup
#   make deb           # .deb for the host arch -> dist/qrabackup_<ver>_<arch>.deb
#   make run           # run from source in a local .venv
#   make test          # run the unit test suite (offscreen)
#   make clean         # remove build artifacts and .venv
#   make dist          # hermetic Docker builds: linux amd64+arm64 (bin/deb/rpm) + windows (exe/setup)
#   make dist-linux-amd64 | dist-linux-arm64 | dist-windows
#   make VERSION=1.2.3 deb   # override the version string
#
# VERSION defaults to `git describe`, falling back to the package's own
# __version__ when the checkout has no tags (or is not a checkout at all).
# Exported so build scripts inherit it.

SHELL    := /usr/bin/env bash
BUILD    := ./scripts/build.sh
DEB      := ./scripts/build-deb.sh
DIST     := dist
APPVER   := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' qrabackup_app/__init__.py)
VERSION  ?= $(shell git describe --tags --dirty 2>/dev/null || echo $(APPVER))
export VERSION

.DEFAULT_GOAL := build

.PHONY: build deb run test clean version help dist dist-linux-amd64 dist-linux-arm64 dist-windows

## build: PyInstaller standalone binary -> dist/qrabackup
build:
	$(BUILD)

## deb: Debian package for the host architecture
deb:
	$(DEB)

## dist: hermetic Docker release builds for every target -> dist/<target>/
dist:
	./packaging/build.sh all

## dist-linux-amd64: binary + .deb + .rpm for x86_64 (Docker)
dist-linux-amd64:
	./packaging/build.sh linux-amd64

## dist-linux-arm64: binary + .deb + .rpm for aarch64 (Docker, QEMU on x86)
dist-linux-arm64:
	./packaging/build.sh linux-arm64

## dist-windows: qrabackup.exe + NSIS installer for Windows x64 (Docker + Wine)
dist-windows:
	./packaging/build.sh windows

## run: run from source (provisions .venv on first use)
run:
	$(BUILD) --run

## test: run the unit test suite (offscreen, no display needed)
test:
	$(BUILD) --test

## clean: remove build/, dist/, .venv and PyInstaller caches
clean:
	rm -rf build $(DIST) .venv *.spec.bak
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "cleaned."

## version: print the resolved version string
version:
	@echo "$(VERSION)"

## help: list available targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'
