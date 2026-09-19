# -*- mode: python ; coding: utf-8 -*-
# Builds a single self-contained Spellbook.exe. The seed database is bundled
# read-only; user edits live in %LOCALAPPDATA%\Spellbook (see spellbook/config.py).
from pathlib import Path

ROOT = Path(SPEC).resolve().parents[1]

a = Analysis(
    [str(ROOT / "spellbook" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "spellbook" / "web"), "spellbook/web"),
        (str(ROOT / "spellbook" / "taxonomy.json"), "spellbook"),
        (str(ROOT / "data" / "spellbook.sqlite"), "data"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PyInstaller", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Spellbook",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
