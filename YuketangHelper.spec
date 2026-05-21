# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Yuketang Helper.

Build command:
    pyinstaller YuketangHelper.spec

Output:
    dist/YuketangHelper (single executable)
"""

import sys
from pathlib import Path
import certifi
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)

# tzdata ships its tz database as package data (.ipv files under tzdata/zoneinfo/).
# Windows has no system tz database, so without these files ZoneInfo("Asia/Shanghai")
# raises ZoneInfoNotFoundError at startup. collect_data_files returns [] when tzdata
# isn't installed (macOS/Linux dev envs), so this is safe cross-platform.
_tzdata_datas = collect_data_files("tzdata")

a = Analysis(
    [str(ROOT / "app_entry.py")],
    pathex=[str(ROOT / "backend")],
    datas=[
        # Frontend build output -> bundled as "static/"
        (str(ROOT / "frontend" / "dist"), "static"),
        # Backend source files (imported at runtime)
        (str(ROOT / "backend"), "backend"),
        # CA certificates for SSL/TLS
        (certifi.where(), "certifi"),
        *_tzdata_datas,
    ],
    hiddenimports=[
        "certifi",
        # tzdata package is needed on Windows for zoneinfo to find IANA tz data
        # (no system /usr/share/zoneinfo). Loaded indirectly via importlib.resources,
        # so PyInstaller won't auto-detect it.
        "tzdata",
        # Lazy-imported in config._resolve_store_dir for frozen builds.
        "platformdirs",
        # Uvicorn internals that are dynamically imported
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # FastAPI / Starlette internals
        "multipart",
        "multipart.multipart",
        # Backend modules
        "config",
        "monitor",
        "lesson",
        "event_log",
        "ai_provider",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YuketangHelper",
    debug=False,
    strip=False,
    upx=True,
    console=True,  # Keep console window for logs; set to False to hide
    # icon="icon.ico",  # Uncomment and provide an icon file if desired
)
