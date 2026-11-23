# Packaging & release builds

All release artifacts are built by `packaging/build.sh` inside Docker
containers, so the result does not depend on the build host's Python, Qt or
distribution.

```bash
packaging/build.sh [linux-amd64] [linux-arm64] [windows] [all]   # default: all
# or: make dist | dist-linux-amd64 | dist-linux-arm64 | dist-windows
```

| Target        | Builder image                       | Artifacts in `dist/<target>/` |
|---------------|-------------------------------------|-------------------------------|
| `linux-amd64` | `packaging/linux/Dockerfile` (amd64) | `qrabackup-<ver>-linux-amd64`, `qrabackup_<ver>_amd64.deb`, `qrabackup-<ver>-<rel>.x86_64.rpm` |
| `linux-arm64` | `packaging/linux/Dockerfile` (arm64) | `qrabackup-<ver>-linux-arm64`, `qrabackup_<ver>_arm64.deb`, `qrabackup-<ver>-<rel>.aarch64.rpm` |
| `windows`     | `packaging/windows/Dockerfile`      | `qrabackup-<ver>-windows-x64.exe` (portable), `qrabackup-<ver>-windows-x64-setup.exe` (installer) |

`dist/SHA256SUMS` lists checksums for everything under `dist/*/`.

## Why the build is hermetic

- **Base images are pinned by digest** (`BASE_IMAGE` build arg).
- **apt installs from a fixed snapshot date** (`APT_SNAPSHOT` build arg):
  `snapshot.ubuntu.com` for Linux and `snapshot.debian.org` for Windows/Wine.
  The package set never changes until you bump the date.
- **Python build dependencies are pinned**, including transitive ones, in
  `packaging/requirements-{linux,windows}.txt` (installed with `--no-deps`,
  then `pip check`).
- **Windows CPython** comes from the official `python` NuGet package, which is
  a plain zip of a full install. It is verified by sha256, and no MSI has to
  run under Wine.
- **The build step runs with `--network none`**, with the repo mounted
  read-only. Sources are copied into the container before building.
- `SOURCE_DATE_EPOCH` (default: last commit time) fixes file mtimes in the
  .deb and the rpm build time.

`VERSION` defaults to `git describe --tags --dirty`, falling back to
`__version__`. Override it with `VERSION=1.2.3 packaging/build.sh`. RPM
versions cannot contain `-`, so `0.5.0-3-gabc-dirty` becomes Version `0.5.0`,
Release `0.3.gabc.dirty`.

## What each Linux build does

`packaging/linux/build-in-container.sh` runs these steps in order:

1. Run the full unit-test suite (offscreen Qt) against the bundled toolchain.
2. Build the PyInstaller onefile binary.
3. Smoke-test the frozen binary: it must start and stay up offscreen for 5s.
4. Build the `.deb` with `scripts/build-deb.sh` (shared with `make deb`).
5. Build the `.rpm` with `rpmbuild`. Strip and debuginfo post-processing are
   disabled, because they would corrupt the onefile payload.

The Linux builder is Ubuntu 22.04 and uses the distro's `python3-pyqt5`.
PyPI ships no aarch64 PyQt5 wheels, and using the distro package keeps amd64
and arm64 identical. The binary needs **glibc ≥ 2.35**, which means:
Ubuntu 22.04+, Debian 12+, Fedora 36+, RHEL/Alma/Rocky 10, and openSUSE
Tumbleweed. RHEL 9 and Debian 11 are too old.

The packages declare the system X11/xcb/GL libraries that Qt's xcb platform
plugin loads at runtime. The `.deb` names them as Debian packages; the `.rpm`
names them by soname, so the same rpm resolves on Fedora, EL and openSUSE.

## arm64 on an x86-64 host

Docker needs QEMU binfmt handlers to run arm64 containers. Registering them is
a one-time host change and needs `--privileged`:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

`packaging/build.sh linux-arm64` detects missing handlers and prints this
command. Emulated builds are several times slower than native ones. On an
arm64 host (Graviton, Ampere, Apple Silicon running Linux), the build runs
natively.

## Windows

PyInstaller runs under Wine against Windows CPython 3.11 (3.12+ needs a newer Wine than Debian 12 ships) with the official
PyQt5 wheels. The unit tests for the pure-logic modules also run under Wine;
the Qt UI tests need a display and are skipped. `makensis` builds an installer
that:

- installs to `Program Files\Qrabackup`;
- adds Start-menu shortcuts and an Add/Remove Programs entry;
- supports a silent uninstall (`/S`);
- keeps `%APPDATA%\qrabackup` on uninstall, because it holds credentials.

Windows on ARM is not built, because PyQt5 has no `win_arm64` wheels. The x64
build runs there under emulation.

The executables are **not code-signed**, so SmartScreen will warn on first
run. Sign them with `osslsigncode`, or with `signtool` on a Windows host,
before distributing.

## Updating pins

1. Bump `BASE_IMAGE` digests. Get them with
   `docker buildx imagetools inspect ubuntu:22.04`.
2. Bump `APT_SNAPSHOT` to a recent `YYYYMMDDTHHMMSSZ`.
3. Update `packaging/requirements-*.txt`. Resolve the new versions in a
   throwaway container, then copy the output of `pip freeze`.
4. Run `make dist` and check that the smoke test and `pip check` pass.
