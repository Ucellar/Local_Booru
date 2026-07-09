# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Local Booru
# Run: python -m PyInstaller Local_Booru.spec

import sys
from pathlib import Path
HERE = Path(SPECPATH)

# Optional AI runtime collection.  The spec must still be readable on machines
# where torch/transformers are not installed yet.
ai_hiddenimports = []
ai_datas = []
ai_binaries = []
try:
    from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs
    ai_hiddenimports += collect_submodules('transformers.models.clip')
    ai_hiddenimports += collect_submodules('safetensors')
    ai_datas += collect_data_files('transformers', include_py_files=False)
    ai_datas += collect_data_files('tokenizers', include_py_files=False)
    ai_binaries += collect_dynamic_libs('torch')
except Exception:
    pass

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[str(HERE)],
    binaries=ai_binaries,
    datas=[
        ('assets', 'assets'),
        ('core', 'core'),
        ('ui', 'ui'),
        ('tools', 'tools'),
    ] + ai_datas,
    hiddenimports=[
        # Core modules
        'core.tagger_engine', 'core.tagger.engine', 'core.tagger.hashing', 'core.tagger.tag_groups', 'core.tagger.cookies_io', 'core.tagger.atf_html',
    'core.tagger.filename_hints',
        'core.database.repository', 'core.database.schema', 'core.database.connection',
        'core.thumb_service', 'core.media_utils', 'core.image_safe',
        'core.settings', 'core.paths', 'core.app_context', 'core.task_manager',
        'core.favorites', 'core.i18n', 'core.tag_utils', 'core.library',
        # UI modules
        'ui.main_window', 'ui.gallery_page', 'ui.post_page', 'ui.tagger_page',
        'ui.tags_page', 'ui.settings_page', 'ui.downloader_page',
        'ui.duplicates_page', 'ui.manga_page', 'ui.games_page', 'ui.nomatch_page',
        'ui.styles.themes', 'ui.modules.registry',
        'ui.downloader', 'ui.downloader.page', 'ui.downloader.worker', 'ui.tagger', 'ui.tagger.workers',
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
        # Optional local NO_MATCH AI backend.  Needed when building an exe that
        # downloads/uses CLIP locally after first launch.
        'torch', 'transformers', 'transformers.models.clip',
        'transformers.models.clip.modeling_clip', 'transformers.models.clip.processing_clip',
        'safetensors',
    ] + ai_hiddenimports,
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
    console=True,           # v162: visible startup console/log window
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
