# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller --onedir spec for AntigravityGoBridge.exe.

Uses --onedir (not --onefile) so startup is instant and the bundled 138 MB
official `google.antigravity/bin/localharness.exe` runtime is never extracted
to %TEMP% on every launch.
"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

datas = []
datas += collect_data_files("google.antigravity", include_py_files=False)
datas += copy_metadata("google-antigravity")
datas += copy_metadata("google-genai")
datas += copy_metadata("fastapi")
datas += copy_metadata("pydantic")

binaries = []
binaries += collect_dynamic_libs("google.antigravity")

hiddenimports = []
hiddenimports += collect_submodules("agy_bridge")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("websockets")
hiddenimports += collect_submodules("anyio")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules(
    "google.antigravity",
    filter=lambda name: not name.endswith("_test") and not name.endswith(".test_utils"),
)
hiddenimports += [
    "multipart",
    "python_multipart",
    "qrcode",
]

a = Analysis(
    ["agy_bridge/entry_exe.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AntigravityGoBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AntigravityGoBridge",
)
