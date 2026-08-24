# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for qrabackup.
# Build with: pyinstaller qrabackup.spec

import pathlib
import platform
import re
from PyInstaller.utils.hooks import collect_all

# __version__ is the single source of truth; read it rather than restating it
# (the package cannot be imported here — importing ui pulls in PyQt5).
APP_VERSION = re.search(
    r'^__version__ = "([^"]+)"',
    pathlib.Path("qrabackup_app/__init__.py").read_text(), re.M,
).group(1)

datas, binaries, hiddenimports = collect_all("PyQt5")

a = Analysis(
    ["qrabackup.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5.QtWebEngine",
        "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.Qt3DCore",
        "PyQt5.Qt3DRender",
        "PyQt5.QtQuick",
        "PyQt5.QtQml",
        "PyQt5.QtMultimedia",
        "PyQt5.QtBluetooth",
        "PyQt5.QtNfc",
        "PyQt5.QtLocation",
        "PyQt5.QtSensors",
        "PyQt5.QtWebSockets",
        "PyQt5.QtDesigner",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="qrabackup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # no terminal window; set True to see stderr
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS .app bundle
if platform.system() == "Darwin":
    app = BUNDLE(
        exe,
        name="qrabackup.app",
        icon=None,
        bundle_identifier="com.example.qrabackup",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": APP_VERSION,
        },
    )
