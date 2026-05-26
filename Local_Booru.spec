# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Local Booru
# Run: python -m PyInstaller Local_Booru.spec

import sys
from pathlib import Path
HERE = Path(SPECPATH)

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[str(HERE)],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('core', 'core'),
        ('ui', 'ui'),
    ],
    hiddenimports=[
        # Core modules
        'core.tagger_engine', 'core.tagger.engine',
        'core.database.repository', 'core.database.schema', 'core.database.connection',
        'core.thumb_service', 'core.media_utils', 'core.image_safe',
        'core.settings', 'core.paths', 'core.app_context', 'core.task_manager',
        'core.favorites', 'core.i18n', 'core.tag_utils', 'core.library',
        # UI modules
        'ui.main_window', 'ui.gallery_page', 'ui.post_page', 'ui.tagger_page',
        'ui.tags_page', 'ui.settings_page', 'ui.downloader_page',
        'ui.duplicates_page', 'ui.manga_page', 'ui.games_page', 'ui.nomatch_page',
        'ui.styles.themes', 'ui.modules.registry',
        'ui.downloader', 'ui.downloader.page', 'ui.downloader.worker',
        # Qt
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtSvg', 'PySide6.QtNetwork',
        # Python stdlib
        'sqlite3', '_sqlite3',
        # Third-party
        'PIL', 'PIL.Image', 'PIL.ImageOps', 'PIL.ImageFilter',
        'bs4', 'requests', 'browser_cookie3',
        'imagehash', 'numpy', 'cv2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'tkinter', 'PyQt5', 'PyQt6'],
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
    name='Local Booru',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\icons\\app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Local Booru',
)
