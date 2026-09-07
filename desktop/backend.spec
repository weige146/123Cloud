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
        # 共享SHA1库的 CA 证书（公钥可公开），随侧车分发；
        # Electron 启动侧车时若未设置 SHA1DB_SSL_CA 会自动指向它。
        (os.path.join(ROOT, "backend", "assets", "ca.pem"), "."),
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
        "pymysql",
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
