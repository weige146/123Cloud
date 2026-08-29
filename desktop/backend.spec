# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the 123Cloud backend sidecar.

Build (from desktop/):
  ../backend/.venv/bin/pyinstaller backend.spec --noconfirm \
      --distpath backend-dist --workpath backend-build
"""

import os

# SPECPATH is the directory containing this spec (desktop/) — the client root.
ROOT = os.path.abspath(SPECPATH)

a = Analysis(
    ["sidecar_entry.py"],
    pathex=[os.path.join(ROOT, "backend")],
    binaries=[],
    datas=[
        # The frozen sidecar serves the admin SPA itself at /admin.
        (os.path.join(ROOT, "web", "dist"), "adminweb"),
    ],
    hiddenimports=[
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "anyio._backends._asyncio",
        "telethon",
        "zoneinfo",
        "tzdata",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "pytest",
        "setuptools",
        "pip",
        "wheel",
        "IPython",
        "matplotlib",
        "numpy",
        "PyQt5",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cloudgateway",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="cloudgateway",
)
