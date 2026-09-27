# -*- mode: python ; coding: utf-8 -*-
"""Freeze the local OCR engine for embedding in the Electron portable EXE."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

PROJECT = Path(SPECPATH).resolve().parent
TESSERACT_SRC = Path(os.environ.get("TESSERACT_SRC", r"C:\Program Files\Tesseract-OCR"))
if not (TESSERACT_SRC / "tesseract.exe").is_file():
    raise RuntimeError(f"Tesseract build input missing: {TESSERACT_SRC}")

datas = [
    (str(PROJECT / "assets" / "tray_icon.png"), "assets"),
    (str(PROJECT / "LICENSE"), "."),
    (str(PROJECT / "NOTICE"), "."),
    (str(PROJECT / "README.md"), "."),
    # The onboarding doc must travel with the zip: it is the only instructions a
    # friend receives, and there is no README on a desktop.
    (str(PROJECT / "docs" / "GETTING-STARTED.md"), "."),
]

# Bundle Tesseract when it is present at build time.
tesseract_binaries = []
if TESSERACT_SRC.is_dir():
    for name in ("tesseract.exe",):
        candidate = TESSERACT_SRC / name
        if candidate.is_file():
            datas.append((str(candidate), "tesseract"))
    # The engine's own DLLs must sit beside tesseract.exe.
    for dll in TESSERACT_SRC.glob("*.dll"):
        datas.append((str(dll), "tesseract"))
    tessdata = TESSERACT_SRC / "tessdata"
    if tessdata.is_dir():
        for trained in ("eng.traineddata", "osd.traineddata"):
            candidate = tessdata / trained
            if candidate.is_file():
                datas.append((str(candidate), "tesseract/tessdata"))
    # Ship the Apache-2.0 text alongside the bundled binary.
    for license_name in ("LICENSE", "LICENSE.txt"):
        candidate = TESSERACT_SRC / license_name
        if candidate.is_file():
            datas.append((str(candidate), "tesseract/LICENSE-tesseract.txt"))
            break

datas += collect_data_files("pystray")

a = Analysis(
    [str(PROJECT / "packaging" / "launcher.py")],
    pathex=[str(PROJECT / "src")],
    binaries=tesseract_binaries,
    datas=datas,
    hiddenimports=[
        "PIL._tkinter_finder",
        "pystray._win32",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "numpy", "matplotlib", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WardogsPresence",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # tray app — no console window
    disable_windowed_traceback=False,
)

# A second, console-enabled executable sharing the same bundle.
#
# Why this exists: the tray build has no console, so `--once`, `--dry-run` and
# `--validate` would print into nothing — and those are exactly the modes used
# to diagnose a misaligned capture region. Two small launchers over one
# collection of libraries costs almost nothing and keeps diagnostics usable
# without a dev Python on the friend's machine.
exe_debug = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WardogsPresence-debug",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    exe_debug,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WardogsPresence",
)
