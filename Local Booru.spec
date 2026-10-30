# -*- mode: python ; coding: utf-8 -*-
# Local Booru as one folder: «Local Booru.exe» and everything it needs beside it.
#   python -m pip install -r requirements.lock.txt pyinstaller
#   python -m PyInstaller --noconfirm --clean "Local Booru.spec"
# The program's own folders (assets, core, ui, webui, extensions) stay beside the exe (contents_directory "."):
# core/paths.py finds its assets there, the web pages their files.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("assets", "assets"), ("core", "core"), ("ui", "ui"), ("webui", "webui"), ("extensions", "extensions")]
datas += collect_data_files("PySide6")
hiddenimports = collect_submodules("core") + collect_submodules("ui") + collect_submodules("webui")
hiddenimports += ["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtWebEngineCore",
                  "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel", "PySide6.QtNetwork", "onnxruntime", "sqlite3"]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Local Booru",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets/app_icon.ico"],
    contents_directory=".",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Local Booru",
)
