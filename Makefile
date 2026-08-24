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

.PHONY: build deb run test clean version help

## build: PyInstaller standalone binary -> dist/qrabackup
build:
	$(BUILD)

## deb: Debian package for the host architecture
deb:
	$(DEB)

## run: run from source (provisions .venv on first use)
run:
	$(BUILD) --run

## test: run the unit test suite (offscreen, no display needed)
test:
	$(BUILD) --test

## clean: remove build/, dist/, .venv and PyInstaller caches
clean:
	rm -rf build $(DIST) .venv __pycache__ *.spec.bak
	@echo "cleaned."

## version: print the resolved version string
version:
	@echo "$(VERSION)"

## help: list available targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'
