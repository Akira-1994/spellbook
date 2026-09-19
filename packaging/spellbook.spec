# -*- mode: python ; coding: utf-8 -*-
# Builds a single self-contained Spellbook.exe. The seed database is bundled
# read-only; user edits live in %LOCALAPPDATA%\Spellbook (see spellbook/config.py).
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = Path(SPEC).resolve().parents[1]
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "spellbook" / "__init__.py").read_text(encoding="utf-8")).group(1)
VERSION_TUPLE = tuple(int(part) for part in (VERSION.split(".") + ["0"] * 4)[:4])

# Shown in the exe's Properties > Details tab.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE),
    kids=[
        StringFileInfo([StringTable("040404B0", [
            StringStruct("FileDescription", "法術書"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "Spellbook"),
            StringStruct("OriginalFilename", "Spellbook.exe"),
            StringStruct("ProductName", "法術書 Spellbook"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0404, 1200])]),
    ],
)

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
    icon=str(ROOT / "packaging" / "spellbook.ico"),
    version=version_info,
)
