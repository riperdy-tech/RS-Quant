# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for QuantDesk Desktop on Windows (§16.1)."""

import os
from pathlib import Path

block_cipher = None
root_dir = Path(SPECPATH).parent.resolve()

datas = [
    (str(root_dir / "web" / "dist"), "web/dist"),
    (str(root_dir / "configs"), "configs"),
    (str(root_dir / "fixtures"), "fixtures"),
]

# Only include files that actually exist
datas = [(src, dst) for src, dst in datas if os.path.exists(src)]

hidden_imports = [
    "uvicorn",
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
    "fastapi",
    "fastapi.responses",
    "pydantic",
    "pydantic_settings",
    "lightgbm",
    "pyarrow",
    "polars",
    "numpy",
    "sklearn",
    "cryptography",
    "argon2",
    "keyring",
    "zstandard",
    "structlog",
    "quantdesk",
    "quantdesk.api",
    "quantdesk.core",
    "quantdesk.features",
    "quantdesk.launcher",
    "quantdesk.observability",
    "quantdesk.persistence",
    "quantdesk.research",
    "quantdesk.risk",
    "quantdesk.simulation",
    "quantdesk.strategies",
    "quantdesk.supervisor",
    "quantdesk.venues",
]

a = Analysis(
    ["main.py"],
    pathex=[str(root_dir / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "hypothesis", "playwright"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuantDesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="QuantDesk",
)
