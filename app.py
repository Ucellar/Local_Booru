import sys
import os
import ctypes
from pathlib import Path

# Chromium/WebEngine flags — must be set before any PySide6.QtWebEngine import.
_chromium_flags = [
    "--lang=en-US",
    "--disable-blink-features=AutomationControlled",
    "--enable-features=NetworkService",
    "--disable-features=SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure,"
    "CompressionDictionaryTransport,PersistentSharedDictionary",
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-gpu-shader-disk-cache",
    "--disable-software-rasterizer",
    "--disable-webgl",
    "--disable-3d-apis",
    "--disk-cache-size=67108864",
    "--media-cache-size=33554432",
    "--log-level=3",
    "--disable-logging",
]
_existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = ((_existing + " ") if _existing else "") + " ".join(_chromium_flags)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QT_OPENGL", "software")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QPixmapCache

from ui.main_window import MainWindow
from core.paths import ERROR_LOG_FILE

try:
    from core.image_safe import configure_pillow
    configure_pillow()
except Exception:
    pass

from core.tagger_engine import load_settings, cleanup_preview_cache


def _log_exception(exc_type, exc, tb):
    import traceback, time
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    text = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] " + text
    try:
        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    try:
        print(text, file=sys.stderr)
    except Exception:
        pass


def main() -> int:
    # ── Stability: logging + exception handler ──────────────────────────
    from core.stability import setup_logging, install_global_exception_handler
    setup_logging()
    import logging
    _applog = logging.getLogger("local_booru")
    _applog.info("=== Local Booru starting ===")

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalBooru.App")
    except Exception:
        pass

    # ── HiDPI / multi-resolution support ──────────────────────────────
    try:
        from PySide6.QtCore import Qt
        # Enable automatic HiDPI scaling (handles 4K, 1080p, 1440p etc.)
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    try:
        QPixmapCache.setCacheLimit(128 * 1024)  # 128 MB
    except Exception:
        pass
    app.setStyle("Fusion")
    app.setApplicationName("Local Booru")
    app.setOrganizationName("Local Booru")

    # Install global Qt + Python exception handler
    install_global_exception_handler(app)

    # Start thumbnail service (background QThreadPool, 3 workers)
    from core.thumb_service import ThumbnailService
    thumb_svc = ThumbnailService.instance(max_threads=3)
    try:
        from core.shutdown import register as _shutdown_register
        _shutdown_register("thumbnail service", thumb_svc.stop)
    except Exception:
        pass

    try:
        cleanup_preview_cache(load_settings())
    except Exception:
        pass

    icon_path = Path(__file__).parent / "assets" / "app_icon.ico"
    icon = None
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)

    # Clear oversized saved window dimensions before creating window
    try:
        from core.app_context import AppContext
        _ctx = AppContext()
        _s = _ctx.settings
        _saved_w = int(_s.get("window_w", 0))
        _saved_h = int(_s.get("window_h", 0))
        if _saved_w > 0 or _saved_h > 0:
            from PySide6.QtGui import QGuiApplication
            _screen = QGuiApplication.primaryScreen()
            if _screen:
                _avail = _screen.availableGeometry()
                if _saved_w > _avail.width() or _saved_h > _avail.height():
                    _s.pop("window_w", None)
                    _s.pop("window_h", None)
                    _ctx.save_settings()
    except Exception:
        pass

    # ── Optional filesystem watcher / incremental indexing ─────────────
    watcher = None
    try:
        from core.settings import load_settings as _ls
        from core.filesystem_watcher import LibraryWatcher
        from core.shutdown import register as _shutdown_register
        _settings_for_watch = _ls()
        watcher = LibraryWatcher(_settings_for_watch, log=lambda m: _applog.info(m))
        if watcher.start():
            _shutdown_register("library watcher", watcher.stop)
    except Exception as _we:
        _applog.warning("Watcher startup failed: %s", _we)

    # ── Stability: startup checks (DB integrity + backup) ──────────────
    try:
        from core.settings import load_settings as _ls
        from core.stability import run_startup_checks
        import threading
        _settings = _ls()
        threading.Thread(
            target=run_startup_checks,
            args=(_settings,),
            kwargs={"log": lambda m: _applog.info(m)},
            daemon=True
        ).start()
    except Exception as _se:
        _applog.warning("Startup checks failed: %s", _se)

    w = MainWindow()
    if icon:
        w.setWindowIcon(icon)
    w.show()

    # Register clean exit handler (Hydrus pattern: marks shutdown as OK)
    def _on_clean_exit():
        try:
            from core.settings import load_settings as _ls
            from core.stability import on_clean_exit
            on_clean_exit(_ls())
        except Exception:
            pass
    app.aboutToQuit.connect(_on_clean_exit)

    ret = app.exec()

    # Graceful shutdown: stop background pools, watcher and pooled DB handles.
    try:
        from core.shutdown import request_shutdown
        request_shutdown()
    except Exception:
        try:
            thumb_svc.stop()
        except Exception:
            pass

    return ret


if __name__ == "__main__":
    sys.exit(main())
