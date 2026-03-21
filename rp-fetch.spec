# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for rp-fetch.

Usage:
    uv run pyinstaller rp-fetch.spec

This produces a single self-contained executable at dist/rp-fetch.
"""

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Collect all rp_fetch submodules so nothing is missed
hiddenimports = (
    collect_submodules("rp_fetch")
    + collect_submodules("typer")
    + collect_submodules("click")
    + collect_submodules("rich")
    + collect_submodules("pydantic")
    + collect_submodules("httpx")
    + collect_submodules("httpcore")
    + [
        "tomllib",
        "tomli_w",
        "h11",
        "h11._connection",
        "h11._events",
        "h11._state",
        "anyio",
        "anyio._backends",
        "anyio._backends._asyncio",
        "sniffio",
        "pydantic_core",
        "shellingham",
        "certifi",
        "idna",
        "charset_normalizer",
        "markdown_it",
        "mdurl",
        "pygments",
    ]
)

# Collect data files needed by pydantic-core (validation schemas)
datas = collect_data_files("pydantic") + collect_data_files("pydantic_core")

a = Analysis(
    ["src/rp_fetch/cli.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "xmlrpc",
        "pydoc",
        "doctest",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rp-fetch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
