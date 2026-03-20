# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for rp-fetch.

Usage:
    uv run pyinstaller rp-fetch.spec

This produces a single self-contained executable at dist/rp-fetch.
"""

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Collect all rp_fetch submodules so nothing is missed
hiddenimports = collect_submodules("rp_fetch") + [
    "tomllib",
    "tomli_w",
    "httpx",
    "httpx._transports",
    "httpx._transports.default",
    "httpcore",
    "httpcore._async",
    "httpcore._backends",
    "httpcore._backends.anyio",
    "h11",
    "anyio",
    "anyio._backends",
    "anyio._backends._asyncio",
    "pydantic",
    "pydantic.deprecated",
    "pydantic_core",
    "typer",
    "typer.main",
    "click",
    "rich",
    "rich.progress",
    "rich.table",
    "rich.console",
    "rich.prompt",
    "shellingham",
]

a = Analysis(
    ["src/rp_fetch/cli.py"],
    pathex=[],
    binaries=[],
    datas=[],
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
