# v404: worker/orchestrator split from ui/tagger_page.py.
# Keep Qt page code separate from long-running parser pipeline code.
from pathlib import Path
from urllib.parse import urlparse
import json
import re
import time
import webbrowser
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QPlainTextEdit,QProgressBar,QCheckBox,QDoubleSpinBox,QSpinBox,QLineEdit,QFileDialog,QGroupBox,QFormLayout,QSplitter,QTableWidget,QTableWidgetItem,QComboBox,QHeaderView,QMessageBox,QAbstractItemView,QSizePolicy,QStackedWidget,QScrollArea,QGridLayout
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from core.settings import save_settings, DEFAULT_SITES
from core.tagger import Tagger, MEDIA_EXTS, video_frame_image, output_processed_status, result_output_base, result_paths_for, has_copy_suffix, is_md5, file_md5, file_phash
from core.tagger.filename_hints import extract_rule34_40hex_key, filename_locator_bucket, is_generic_media_filename
from ui.login_browser import LoginBrowserDialog, open_br34, open_br34_multi
from ui.sites_widget import SitesWidget
from ui.memory_tools import bounded_append, set_bounded_log, soft_gc
from core.memory_guard import process_memory_snapshot, format_snapshot, soft_trim_memory

_NETWORK_EXCEPTION_MARKERS = (
    "read timed out", "connect timed out", "connection timed out", "timeouterror",
    "connection aborted", "connection reset", "failed to resolve", "getaddrinfo failed",
    "nameresolutionerror", "network is unreachable", "connection refused",
    "max retries exceeded", "ssleoferror", "unexpected_eof_while_reading",
)

def _looks_like_network_exception(error):
    text = str(error or "").lower()
    return any(marker in text for marker in _NETWORK_EXCEPTION_MARKERS)

SITE_SCAN_REVISION = 1

SITE_SCAN_KEY_REVISIONS = {
    "rule34.us": "remote-media-md5-v2",
    # v249: rule34.xxx auth and HTML-locator guards.  Missing/invalid API
    # credentials are not journaled as misses, and ignored md5= HTML pages are
    # no longer used as post locators.
    "rule34.xxx": "sample-image-key-locator-v6",
    "api.rule34.xxx": "sample-image-key-locator-v6",
}

def _site_scan_key(tagger, site):
    """Stable per-source identity used by the SQLite site scan journal."""
    site = site if isinstance(site, dict) else {}
    host = str(site.get("domain") or "").strip().lower().replace("www.", "")
    if not host:
        try:
            host = urlparse(tagger._site_root_from_cfg(site)).netloc.lower().replace("www.", "")
        except Exception:
            host = ""
    key = host or str(tagger._site_label(site)).strip().lower()
    suffix = SITE_SCAN_KEY_REVISIONS.get(key)
    return f"{key}::{suffix}" if suffix else key

class TaggerWorker(QThread):
    log=Signal(str); progress=Signal(int,int); current_file=Signal(str); site_current=Signal(str,str,str); done=Signal()
    def __init__(self, settings):
        super().__init__()
        import threading
        self.settings=settings.copy()
        try:
            from core.parser_blueprint import apply_blueprint_runtime_settings
            self.settings = apply_blueprint_runtime_settings(self.settings)
        except Exception:
            pass
        self._apply_auto_performance_profile()
        self.paused=False
        # Python helper threads inside the site conveyor may outlive the visible
        # Qt page for a short moment during application shutdown.  Do not let
        # them call a deleted QThread C++ object through isInterruptionRequested()
        # or signal.emit(); that was the source of the libshiboken crash.
        self._hard_stop_event = threading.Event()
        # v339: high-volume parser logs can outrun the Qt UI event loop.
        # If every CHECK/MISS/category-overlay line is queued as an individual
        # Qt signal, the Python process can grow to tens of GB and Windows marks
        # the app as "Not responding" even though worker threads still run.
        # Keep detailed matching/error logs, but coalesce repetitive low-value
        # noise before it reaches Qt.
        self._ui_log_throttle_enabled = bool(self.settings.get("tagger_ui_log_throttle", True))
        try:
            self._ui_log_summary_interval = max(2.0, min(60.0, float(self.settings.get("tagger_ui_log_summary_interval_seconds", 5.0) or 5.0)))
        except Exception:
            self._ui_log_summary_interval = 5.0
        self._ui_log_suppressed_counts = {}
        self._ui_log_last_summary = time.time()
        self._last_current_file_emit = 0.0
        self._last_progress_emit = 0.0
        self._last_site_current_emit = {}
        self._last_ram_guard_check = 0.0
        self._ram_guard_tripped = False
        self._last_ram_guard_soft_warning = 0.0
        self._log_emitted_since_trim = 0

    def _apply_auto_performance_profile(self):
        """Clamp parser defaults to the machine RAM profile without saving settings.

        v411: a 64GB developer machine can tolerate a 64-file reverse window,
        but a 16GB PC cannot.  This runtime profile intentionally only mutates
        the worker's session copy; the user's app_settings.json is not changed.
        """
        try:
            snap = process_memory_snapshot()
            total_mb = float(snap.get("total_bytes") or 0) / (1024.0 * 1024.0)
        except Exception:
            total_mb = 0.0
        requested = str(self.settings.get("tagger_performance_profile", "auto") or "auto").strip().lower()
        if requested not in {"auto", "low_memory", "balanced", "performance"}:
            requested = "auto"
        if requested == "auto":
            if total_mb and total_mb <= 18 * 1024:
                profile = "low_memory"
            elif total_mb and total_mb <= 36 * 1024:
                profile = "balanced"
            else:
                profile = "performance"
        else:
            profile = requested
        self.settings["_tagger_resolved_performance_profile"] = profile
        self.settings["_tagger_detected_ram_mb"] = int(total_mb or 0)

        def _clamp_int(key, value):
            try:
                cur = int(self.settings.get(key, value) or value)
            except Exception:
                cur = int(value)
            self.settings[key] = max(1, min(cur, int(value)))

        def _set_if_empty(key, value):
            if key not in self.settings or self.settings.get(key) in (None, "", "auto", "default"):
                self.settings[key] = value

        if profile == "low_memory":
            _clamp_int("tagger_reverse_admit_window_files", 12)
            _clamp_int("local_total_workers", 3)
            _clamp_int("local_scan_workers", 1)
            _clamp_int("local_hash_workers", 2)
            _clamp_int("local_image_workers", 1)
            _clamp_int("local_video_workers", 1)
            _clamp_int("local_db_read_workers", 1)
            _clamp_int("local_tagger_workers", 1)
            _clamp_int("rule34_sha1_async_locator_workers", 1)
            _clamp_int("sqlite_cache_mb", 16)
            self.settings["sqlite_temp_store"] = "FILE"
            # pHash/video-frame warm-up is useful on large rigs but can allocate
            # many image buffers on 16GB machines.  MD5 cache still warms up.
            self.settings["local_preflight_phash"] = False
            self.settings["tagger_ui_log_summary_interval_seconds"] = max(2.0, float(self.settings.get("tagger_ui_log_summary_interval_seconds", 5.0) or 5.0))
            if total_mb:
                self.settings["tagger_ram_soft_limit_mb"] = int(max(4096, total_mb * 0.45))
                self.settings["tagger_ram_safe_hard_limit_mb"] = int(max(4096, total_mb * 0.45))
                self.settings["tagger_ram_emergency_process_limit_mb"] = int(max(6144, total_mb * 0.70))
                self.settings["tagger_ram_min_free_mb"] = int(max(1536, total_mb * 0.12))
        elif profile == "balanced":
            _clamp_int("tagger_reverse_admit_window_files", 32)
            _clamp_int("local_total_workers", 5)
            _clamp_int("local_hash_workers", 3)
            _clamp_int("local_image_workers", 2)
            _clamp_int("local_video_workers", 1)
            _clamp_int("sqlite_cache_mb", 32)
            _set_if_empty("sqlite_temp_store", "FILE")
            if total_mb:
                self.settings["tagger_ram_emergency_process_limit_mb"] = int(max(12288, total_mb * 0.60))
                self.settings["tagger_ram_min_free_mb"] = int(max(2048, total_mb * 0.10))
        else:
            # Performance profile keeps existing high-throughput defaults, but
            # v410's soft-pressure guard still trims instead of panic-stopping.
            _set_if_empty("tagger_reverse_admit_window_files", 64)

    def requestInterruption(self):
        try:
            self._hard_stop_event.set()
        except Exception:
            pass
        try:
            super().requestInterruption()
        except RuntimeError:
            # Qt C++ side is already gone; Python helper threads will see the
            # hard stop event and exit silently.
            pass

    def interruption_requested(self):
        try:
            if self._hard_stop_event.is_set():
                return True
        except Exception:
            return True
        try:
            if super().isInterruptionRequested():
                self._hard_stop_event.set()
                return True
        except RuntimeError:
            self._hard_stop_event.set()
            return True
        return False

    def _safe_emit(self, signal_name, *args):
        try:
            signal = getattr(self, signal_name)
            signal.emit(*args)
            return True
        except RuntimeError:
            try:
                self._hard_stop_event.set()
            except Exception:
                pass
            return False
        except Exception:
            return False

    def _log_throttle_bucket(self, message):
        text = str(message or "")
        upper = text.upper()
        # Always keep high-value lifecycle/errors.  v375 RAM-safe mode keeps
        # result spam compact; full forensic detail is still in file logs/errors.
        ram_safe = bool(self.settings.get("tagger_ram_safe_mode", True))
        keep_markers = (
            "CRITICAL", "TRACEBACK", "MEMORYERROR", "PARSER DB I/O ERROR",
            "SQLITE", "STOP", "START", "DONE", "SUMMARY",
            "LOCAL PREFLIGHT", "RAM GUARD", "COOLDOWN", "HASH SKIP",
        )
        if not ram_safe:
            keep_markers = keep_markers + (
                "ASYNC FEEDER", "PARSER ASYNC", "] MATCH ", " MATCH HTTPS://",
                "EXACT MD5 MERGE", "TAGS MERGED", "RULE34 IMAGE-KEY SITE MD5 RELAY TAGS",
                "SAUCENAO LIMITS", "SAUCE MATCH", "FOUND; EXACT-SITE", "NO MATCH; WAITING",
            )
        if any(marker in upper for marker in keep_markers):
            return ""
        if ram_safe and (
            "] MATCH " in upper
            or " MATCH HTTPS://" in upper
            or "TAGS [" in upper
            or "EXACT MD5 MERGE" in upper
            or "TAGS MERGED" in upper
            or "TAG SOURCE:" in upper
            or "MD5 MATCH:" in upper
            or "VARIANT MATCH" in upper
            or "SOURCE MD5 RELAY" in upper
            or "MD5 RELAY TAGS" in upper
            or "MD5 RELAY ACCEPTED" in upper
            or "DIRECT RELAY ACCEPTED" in upper
            or "ACCEPTED:" in upper
            or "MERGED SITES" in upper
        ):
            return "match/result details"
        if text.startswith("SEARCH [MD5]") or "] PHASH:" in text or "] TRY MD5" in text or "] TRY REAL FILE MD5" in text:
            return "md5 details"
        if text.startswith("[MD5:") and upper.rstrip().endswith(" CHECK"):
            return "site check"
        if (
            "NO EXACT JSON CANDIDATE" in upper
            or "JSON ONLY: NO EXACT" in upper
            or "DAPI JSON ONLY: NO EXACT" in upper
            or "JSON MD5 REJECT" in upper
            or "HTML FALLBACK DISABLED" in upper
            or "RESTRICTED HTML FALLBACK NOT ALLOWED" in upper
        ):
            return "site miss/noise"
        if (
            "TAG CATEGORY BACKGROUND QUEUED" in upper
            or "TAG CATEGORY BACKGROUND DONE" in upper
            or "TAG CATEGORY BACKGROUND SKIP" in upper
            or "TAG CATEGORY SOURCE:" in upper
        ):
            return "category overlay"
        if "NETWORK RETRY" in upper or "HTML CATEGORY OVERLAY ERROR" in upper:
            return "temporary network"
        if text.startswith("[R34-VARIANT:") and (
            "COOKIE BRIDGE" in upper
            or "PLAYWRIGHT START" in upper
            or "HOTLINK REQUESTS 403" in upper
            or "IMAGE KEY LOCATOR START" in upper
            or "MD5 CHECK:" in upper
        ):
            return "rule34 variant debug"
        if "SITES UP TO DATE" in upper:
            return "sites up-to-date"
        return ""

    def _flush_log_throttle_summary(self, *, force=False):
        if not self._ui_log_throttle_enabled:
            return
        now = time.time()
        if not force and now - float(self._ui_log_last_summary or 0.0) < self._ui_log_summary_interval:
            return
        counts = dict(self._ui_log_suppressed_counts or {})
        if not counts:
            self._ui_log_last_summary = now
            return
        self._ui_log_suppressed_counts.clear()
        self._ui_log_last_summary = now
        parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        self._safe_emit("log", f"LOG THROTTLE: скрыто шумных строк: {parts}")

    def _emit_log(self, message):
        text = str(message)
        self._log_emitted_since_trim += 1
        if self._log_emitted_since_trim >= 500:
            self._log_emitted_since_trim = 0
            self._memory_guard_check("log")
        if self._ui_log_throttle_enabled:
            bucket = self._log_throttle_bucket(text)
            if bucket:
                self._ui_log_suppressed_counts[bucket] = int(self._ui_log_suppressed_counts.get(bucket, 0) or 0) + 1
                self._flush_log_throttle_summary()
                return True
            self._flush_log_throttle_summary()
        return self._safe_emit("log", text)

    def _emit_progress(self, value, total):
        if self._ui_log_throttle_enabled:
            now = time.time()
            if now - self._last_progress_emit < 0.10 and int(value) not in (0, int(total)):
                return True
            self._last_progress_emit = now
        return self._safe_emit("progress", int(value), int(total))

    def _emit_current_file(self, path):
        if self._ui_log_throttle_enabled:
            now = time.time()
            if now - self._last_current_file_emit < 0.20:
                return True
            self._last_current_file_emit = now
        return self._safe_emit("current_file", str(path))

    def _emit_site_current(self, site, status, path):
        if self._ui_log_throttle_enabled:
            key = str(site)
            now = time.time()
            status_text = str(status)
            important = any(x in status_text.lower() for x in ("найден", "ошибка", "ключ", "стоп", "cooldown", "разложено"))
            last = self._last_site_current_emit.get(key, 0.0)
            if not important and now - float(last or 0.0) < 0.35:
                return True
            self._last_site_current_emit[key] = now
        return self._safe_emit("site_current", str(site), str(status), str(path or ""))

    def _emit_done(self):
        return self._safe_emit("done")

    def _memory_guard_check(self, stage="parser"):
        """Stop parser before the Python process can starve Windows RAM."""
        if not bool(self.settings.get("tagger_ram_guard_enabled", True)):
            return False
        if self._ram_guard_tripped:
            return True
        try:
            interval = max(1.0, min(60.0, float(self.settings.get("tagger_ram_guard_check_interval_seconds", 5.0) or 5.0)))
        except Exception:
            interval = 5.0
        now = time.time()
        if now - float(self._last_ram_guard_check or 0.0) < interval:
            return False
        self._last_ram_guard_check = now
        snap = process_memory_snapshot()
        rss_mb = float(snap.get("rss_bytes") or 0) / (1024.0 * 1024.0)
        free_mb = float(snap.get("available_bytes") or 0) / (1024.0 * 1024.0)
        total_mb = float(snap.get("total_bytes") or 0) / (1024.0 * 1024.0)
        load = float(snap.get("memory_load_percent") or 0.0)
        try:
            soft_limit_mb = float(self.settings.get("tagger_ram_soft_limit_mb", 24576) or 24576)
        except Exception:
            soft_limit_mb = 24576.0
        # v381: RAM-safe mode is an actual brake, not just a last-ditch fuse.
        # Existing user configs may still contain the old 24GB limit; clamp it
        # lower during parser runs until the leaking subsystem is proven fixed.
        try:
            if bool(self.settings.get("tagger_ram_safe_mode", True)):
                safe_cap = float(self.settings.get("tagger_ram_safe_hard_limit_mb", 12288) or 12288)
                if safe_cap > 0:
                    soft_limit_mb = min(soft_limit_mb, safe_cap)
        except Exception:
            soft_limit_mb = min(soft_limit_mb, 12288.0)
        try:
            min_free_mb = float(self.settings.get("tagger_ram_min_free_mb", 3072) or 3072)
        except Exception:
            min_free_mb = 3072.0
        try:
            load_limit = float(self.settings.get("tagger_ram_system_load_limit_percent", 94) or 94)
        except Exception:
            load_limit = 94.0
        try:
            trim_at_mb = float(self.settings.get("tagger_ram_trim_at_mb", 8192) or 8192)
            if rss_mb and trim_at_mb > 0 and rss_mb >= trim_at_mb:
                try:
                    from core.thumb_service import ThumbnailService
                    ThumbnailService.instance().clear_memory_cache()
                except Exception:
                    pass
                try:
                    soft_trim_memory(0.0)
                except Exception:
                    pass
        except Exception:
            pass
        over_rss = bool(rss_mb and rss_mb >= soft_limit_mb)
        low_free = bool(free_mb and free_mb <= min_free_mb)
        high_load = bool(load and load >= load_limit)

        # v410: do not kill a resumed reverse-only run only because the Python/Qt
        # working set is above the old safe-mode cap.  On Windows, RSS/working-set
        # can stay inflated after gallery thumbnails, logs, Pillow buffers or a
        # previous parser pass even when the system still has tens of GB free.
        # Treat process>soft_limit as a trimming warning; stop only when there is
        # real system pressure or a very high process hard limit is crossed.
        try:
            pressure_required = bool(self.settings.get("tagger_ram_guard_require_system_pressure", True))
        except Exception:
            pressure_required = True
        try:
            emergency_limit_mb = float(self.settings.get("tagger_ram_emergency_process_limit_mb", 0) or 0)
        except Exception:
            emergency_limit_mb = 0.0
        if emergency_limit_mb <= 0:
            profile = str(self.settings.get("_tagger_resolved_performance_profile", "performance") or "performance")
            if total_mb and profile == "low_memory":
                emergency_limit_mb = max(6144.0, total_mb * 0.70)
            elif total_mb and profile == "balanced":
                emergency_limit_mb = max(12288.0, total_mb * 0.60)
            elif total_mb and total_mb <= 18 * 1024:
                emergency_limit_mb = max(6144.0, total_mb * 0.70)
            else:
                emergency_limit_mb = max(32768.0, soft_limit_mb * 2.0)
        emergency_rss = bool(rss_mb and rss_mb >= emergency_limit_mb)

        if over_rss and pressure_required and not (low_free or high_load or emergency_rss):
            try:
                soft_trim_memory(0.0)
            except Exception:
                pass
            # Keep the UI/log readable: warn rarely, not every guard interval.
            try:
                warn_interval = max(30.0, min(600.0, float(self.settings.get("tagger_ram_guard_soft_warning_interval_seconds", 120) or 120)))
            except Exception:
                warn_interval = 120.0
            if now - float(getattr(self, "_last_ram_guard_soft_warning", 0.0) or 0.0) >= warn_interval:
                self._last_ram_guard_soft_warning = now
                self._safe_emit(
                    "log",
                    "RAM GUARD SOFT: process RSS is above soft cap but system RAM is OK; "
                    "trimmed and continuing: " + format_snapshot(snap) + f"; soft={soft_limit_mb:.0f}MB emergency={emergency_limit_mb:.0f}MB stage={stage}"
                )
            return False

        stop_for_rss = over_rss if not pressure_required else emergency_rss
        if stop_for_rss or low_free or high_load:
            self._ram_guard_tripped = True
            reason = []
            if stop_for_rss:
                limit_for_log = emergency_limit_mb if pressure_required else soft_limit_mb
                reason.append(f"process>{limit_for_log:.0f}MB")
            if low_free:
                reason.append(f"free<{min_free_mb:.0f}MB")
            if high_load:
                reason.append(f"system>{load_limit:.0f}%")
            self._safe_emit("log", "RAM GUARD: аварийная остановка парсера, чтобы не уронить Windows: " + ", ".join(reason))
            self._safe_emit("log", "RAM GUARD: " + format_snapshot(snap) + f"; stage={stage}")
            self._safe_emit("log", "RAM GUARD: результат уже записанный в SQLite остаётся; после перезапуска продолжай с skip_existing/tag_only_untagged")
            try:
                self.requestInterruption()
            except Exception:
                pass
            try:
                soft_trim_memory(0.0)
            except Exception:
                pass
            return True
        # Lightweight periodic GC/allocator trim while long network waits create garbage.
        try:
            soft_trim_memory(30.0)
        except Exception:
            pass
        return False

    def set_paused(self, paused):
        self.paused = bool(paused)

    def _wait_if_paused_or_delay(self, seconds=0):
        end = time.time() + max(0, float(seconds or 0))
        while not self.interruption_requested():
            if self.paused:
                time.sleep(0.25)
                continue
            if time.time() >= end:
                break
            time.sleep(min(0.25, end - time.time()))

    def _sqlite_database_locked(self, exc) -> bool:
        text = str(exc or "").lower()
        return "database is locked" in text or "database table is locked" in text or "database is busy" in text or "sqlite_busy" in text

    def _stop_startup_db_locked(self, context, exc=None):
        detail = f": {exc}" if exc is not None else ""
        self._emit_log(f"PARSER DB LOCKED: {context}{detail}")
        self._emit_log("PARSER DB LOCKED: очередь не будет строиться, чтобы не прогнать весь архив заново как пустой")
        self._emit_log("PARSER DB LOCKED: подожди 15-60 сек после аварийного завершения или перезапусти приложение")
        try:
            self.requestInterruption()
        except Exception:
            pass
        return False

    def _wait_for_parser_db_available(self, context="перед построением очереди"):
        """Require SQLite to be readable and briefly writable before queue build.

        v411: if status checks run while SQLite is still locked after crash
        recovery, they return empty maps and the parser requeues the whole
        archive.  That is worse than stopping.  Wait for a real read +
        BEGIN IMMEDIATE probe, then allow queue construction.
        """
        try:
            max_seconds = max(5.0, min(300.0, float(self.settings.get("tagger_db_startup_lock_wait_seconds", 90) or 90)))
        except Exception:
            max_seconds = 90.0
        started = time.time()
        last_log = 0.0
        last_exc = None
        while not self.interruption_requested():
            try:
                from core.database.connection import connect, ensure_initialized, close_thread_pooled_connections
                try:
                    close_thread_pooled_connections(self.settings)
                except Exception:
                    pass
                con = connect(self.settings)
                try:
                    ensure_initialized(con, settings=self.settings)
                    # Read probe: common tables may be absent only on first DB, so
                    # sqlite_master is the safest universal read.
                    con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                    try:
                        con.commit()
                    except Exception:
                        pass
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("ROLLBACK")
                finally:
                    try:
                        con.close()
                    except Exception:
                        pass
                return True
            except Exception as exc:
                last_exc = exc
                if not self._sqlite_database_locked(exc):
                    self._emit_log(f"PARSER DB STARTUP CHECK WARNING: {type(exc).__name__}: {exc}")
                    return True
                now = time.time()
                if now - last_log >= 10.0:
                    last_log = now
                    self._emit_log(f"SQLITE LOCK WAIT: {context}: {exc}")
                if now - started >= max_seconds:
                    return self._stop_startup_db_locked(context, last_exc)
                self._wait_if_paused_or_delay(0.5)
        return False

    def _wait_for_db_writes_ready(self, stage="parser"):
        """Wait while startup crash-recovery keeps SQLite in read-only mode.

        After a power loss the UI is allowed to open quickly, but writes remain
        blocked until the background quick_check/integrity_check finishes.  A
        parser run during that window used to crash on the first checkpoint
        write.  Now the worker simply waits, keeping the queued work intact.
        """
        try:
            from core.database.connection import writes_blocked, writes_blocked_reason
        except Exception:
            return True
        if not writes_blocked():
            return True
        reason = str(writes_blocked_reason() or "SQLite writes are temporarily blocked")
        self._emit_log(f"SQLITE RECOVERY WAIT: {stage}: {reason}")
        self._emit_log("SQLITE RECOVERY WAIT: парсер не будет писать checkpoints/теги, пока фоновая проверка БД не завершится")
        last_notice = 0.0
        while not self.interruption_requested():
            try:
                if not writes_blocked():
                    self._emit_log("SQLITE RECOVERY READY: фоновой проверкой разрешены записи; продолжаем парсер")
                    return True
                now = time.time()
                if now - last_notice >= 15.0:
                    last_notice = now
                    self._emit_log(f"SQLITE RECOVERY WAIT: всё ещё ждём БД: {writes_blocked_reason()}")
            except Exception:
                return True
            self._wait_if_paused_or_delay(0.5)
        self._emit_log("SQLITE RECOVERY WAIT: остановлено пользователем")
        return False

    def _sqlite_disk_io_error(self, exc):
        try:
            import sqlite3
            return isinstance(exc, sqlite3.OperationalError) and "disk i/o error" in str(exc).lower()
        except Exception:
            return "disk i/o error" in str(exc).lower()

    def _handle_parser_db_write_error(self, exc, context="SQLite write"):
        """Return True if the caller should suppress/retry handling."""
        try:
            from core.database.connection import DatabaseMissingError, DatabaseWriteBlockedError, set_writes_blocked, writes_blocked_reason
            if isinstance(exc, DatabaseMissingError):
                reason = f"SQLite database is missing during parser write: {context}: {exc}"
                try:
                    set_writes_blocked(reason)
                except Exception:
                    pass
                self._emit_log("PARSER DB MISSING ERROR: SQLite база не найдена; новая пустая БД не создана")
                self._emit_log("PARSER DB MISSING ERROR: проверь путь/диск с Local_Booru_Archive/settings/db")
                self.requestInterruption()
                return True
            if isinstance(exc, DatabaseWriteBlockedError):
                self._emit_log(f"SQLITE RECOVERY WAIT: {context}: {writes_blocked_reason() or exc}")
                return self._wait_for_db_writes_ready(context)
            if self._sqlite_disk_io_error(exc):
                reason = f"SQLite disk I/O error during parser write: {context}: {exc}"
                try:
                    set_writes_blocked(reason)
                except Exception:
                    pass
                self._emit_log("PARSER DB I/O ERROR: SQLite вернул disk I/O error; парсер остановлен безопасно")
                self._emit_log("PARSER DB I/O ERROR: не удаляй БД; перезапусти приложение и дождись фоновой проверки SQLite")
                self.requestInterruption()
                return True
        except Exception:
            pass
        return False


    def _run_site_conveyor_site_cursors(self, files, session_settings, writer, *, prior_global_status=None, existing_media_map=None, site_done_map=None, restored_saucenao=None):
        """True per-site cursor conveyor.

        Each enabled MD5 source owns its own cursor over the whole file list.
        A slow/backlog-heavy site such as ATF can keep scanning its own pending
        files without occupying a global file window and without starving
        rule34/gelbooru/danbooru/e621.  Per-file state is used only as a result
        merge/reverse-admission barrier, not as the unit that feeds all sites.
        """
        import queue
        import threading
        from core.services.scan_state_service import (
            mark_site_scanned, enqueue_reverse_retry, remove_reverse_retry,
            mark_reverse_branch_status, reverse_branch_status_many,
            processed_records_many, record_task_event,
        )

        sites = writer._all_enabled_site_configs()
        if not sites:
            self._emit_log("SITE CURSOR CONVEYOR: no enabled MD5 sites; using ordinary fallback path")
            return None

        files = [Path(p) for p in files]
        prior_global_status = dict(prior_global_status or {})
        existing_media_map = dict(existing_media_map or {})
        site_done_map = dict(site_done_map or {})
        interval = max(1.10, float(self.settings.get("tagger_site_interval_seconds", 1.10) or 1.10))
        low_power = bool(self.settings.get("tagger_low_power_mode", False))
        total_files = len(files)
        stats = {"tagged": 0, "nomatch": 0, "skipped": 0, "deferred_network": 0,
                 "deferred_saucenao": 0, "errors": 0, "site_checks": 0, "site_merged": 0,
                 "reverse_started": 0, "reverse_done": 0, "category_done": 0,
                 "reverse_queued_files": 0, "reverse_queued_tasks": 0,
                 "reverse_canceled": 0, "reverse_retry_queued": 0,
                 "reverse_initial_candidates": 0, "reverse_initial_queued": 0,
                 "tineye_tagged": 0, "tineye_source_only": 0}

        site_lanes = []
        used_labels = {}
        blueprint_site_runtime = dict(session_settings.get("_parser_blueprint_site_runtime") or {})
        for index, site in enumerate(sites):
            label = writer._site_label(site)
            used_labels[label] = used_labels.get(label, 0) + 1
            shown = label if used_labels[label] == 1 else f"{label} ({used_labels[label]})"
            site_key = _site_scan_key(writer, site)
            engine = str(site.get("engine") or site.get("type") or "")
            domain_key = str(site.get("domain") or "").lower().replace("www.", "")
            rt = blueprint_site_runtime.get(domain_key, {}) if isinstance(blueprint_site_runtime, dict) else {}
            try:
                site["_blueprint_min_delay_ms"] = max(0, int(rt.get("min_delay_ms", 0) or 0))
            except Exception:
                site["_blueprint_min_delay_ms"] = 0
            site_lanes.append((f"site-{index}", shown, site_key, engine, site))

        active_site_keys = [site_key for _lk, _shown, site_key, _engine, _site in site_lanes if site_key]
        active_site_set = set(active_site_keys)

        rule34_side_queue_enabled = bool(self.settings.get("rule34_variant_locator_side_queue_enabled", True))
        rule34_side_site = None
        for _lk, _shown, site_key, _engine, site in site_lanes:
            host_key = str(site_key or "").lower()
            if "rule34.xxx" in host_key or "api.rule34.xxx" in host_key:
                rule34_side_site = site
                break

        atf_side_site = None
        atf_side_shown = "booru.allthefallen.moe"
        atf_side_site_key = "booru.allthefallen.moe"
        atf_side_engine = "danbooru"
        for _lk, _shown, _site_key, _engine, _site in site_lanes:
            _text = " ".join(str(x or "") for x in (_shown, _site_key, _engine, (_site or {}).get("domain"), (_site or {}).get("base_url"), (_site or {}).get("login_url"))).lower()
            if "allthefallen" in _text or "booru.allthefallen.moe" in _text:
                atf_side_site = _site
                atf_side_shown = _shown
                atf_side_site_key = _site_key or "booru.allthefallen.moe"
                atf_side_engine = _engine or str((_site or {}).get("engine") or "danbooru")
                break

        reverse_branches = []
        def _add_reverse_branch(key, label, enabled):
            if enabled:
                reverse_branches.append((str(key), str(label)))
        _add_reverse_branch("iqdb", "IQDB", self.settings.get("enable_iqdb"))
        _add_reverse_branch("danbooru_iqdb", "Danbooru IQDB", self.settings.get("enable_danbooru_iqdb"))
        _add_reverse_branch("e621_iqdb", "e621 IQDB", self.settings.get("enable_e621_iqdb"))
        _add_reverse_branch("ascii2d", "Ascii2D", self.settings.get("enable_ascii2d"))
        _add_reverse_branch("saucenao", "SauceNAO", self.settings.get("enable_saucenao") and self.settings.get("saucenao_api_key"))
        _add_reverse_branch("tineye", "TinEye", self.settings.get("enable_tineye"))
        reverse_chain = [label for _key, label in reverse_branches]
        if rule34_side_queue_enabled and rule34_side_site:
            reverse_chain.append("rule34 40hex/SHA1")

        def _existing_is_found(path):
            status = str(prior_global_status.get(str(path)) or "").lower()
            return status in ("found", "tagged", "partial")

        per_file_remaining = {}
        per_file_had_network_defer = set()
        found_paths = set()
        for path in files:
            key = str(path)
            done_sites = set((site_done_map.get(key) or {}).keys())
            remaining = len([site_key for site_key in active_site_keys if site_key not in done_sites])
            per_file_remaining[key] = remaining
            if _existing_is_found(path):
                found_paths.add(key)

        pending_by_site = {}
        for _lk, shown, site_key, _engine, _site in site_lanes:
            pending_by_site[shown] = sum(1 for p in files if site_key not in set((site_done_map.get(str(p)) or {}).keys()))
        total_site_units = sum(pending_by_site.values())
        completed_site_units = 0
        reverse_queues = {key: queue.Queue() for key, _label in reverse_branches}
        rule34_variant_q = queue.Queue()
        final_nomatch_q = queue.Queue()
        reverse_scheduled = set()
        reverse_active = set()
        reverse_remaining = {}
        reverse_manifest = {}  # path -> diagnostic info for queued reverse work
        reverse_had_defer = set()
        reverse_defer_branch = {}
        reverse_retry_persisted = set()
        reverse_lock = threading.Lock()
        state_lock = threading.Lock()
        stats_lock = threading.Lock()
        persist_lock = threading.Lock()
        from collections import OrderedDict
        hash_cache = OrderedDict()
        hash_locks = {}
        hash_lock = threading.Lock()
        # Keep only a rolling cache.  A full-run path→hash dict is not worth
        # retaining on 10k–500k file runs; if another slow site reaches an
        # evicted file later it can recompute/read the persistent hash cache.
        try:
            hash_cache_max = max(256, min(4096, int(self.settings.get("tagger_runtime_hash_cache_items", 1024) or 1024)))
        except Exception:
            hash_cache_max = 1024
        stop_event = threading.Event()

        enabled_names = ", ".join(f"{shown} pending={pending_by_site.get(shown, 0)}" for _lk, shown, _sk, _eng, _site in site_lanes)
        self._emit_log(
            "SITE CONVEYOR TRUE PER-SITE CURSORS ACTIVE: "
            "each site has its own file cursor; ATF backlog cannot starve other MD5 lanes"
        )
        self._emit_log(
            f"SITE CURSORS ACTIVE: sites={len(site_lanes)} files={total_files} "
            f"site_pending_checks={total_site_units}; {enabled_names}"
        )
        if reverse_chain:
            self._emit_log("REVERSE CHAIN: " + " -> ".join(reverse_chain))
            self._emit_log("REVERSE ADMISSION: queued only after every enabled MD5 site has final miss/no-match for that file")
            self._emit_log("REVERSE TRUE PER-BRANCH QUEUES ACTIVE: IQDB/Danbooru IQDB/e621 IQDB/SauceNAO/TinEye/rule34 40hex each has its own queue")
            self._emit_log("SOURCE→MD5 RELAY: every reverse URL/40hex hit extracts verified MD5 and relays through the normal enabled MD5 parsers")
        else:
            self._emit_log("REVERSE CHAIN: disabled")
        if low_power:
            self._emit_log("LOW POWER MODE: per-site cursors active, but workers still run conservatively")

        for _lk, shown, _sk, _eng, _site in site_lanes:
            self._emit_site_current(shown, f"Свой курсор: pending {pending_by_site.get(shown, 0)}", "")
        for _branch_key, _branch_label in reverse_branches:
            self._emit_site_current(_branch_label, "Reverse cursor: ждёт MD5-miss", "")
        if rule34_side_queue_enabled and rule34_side_site:
            self._emit_site_current("rule34 40hex/SHA1", "Reverse cursor: ждёт MD5-miss", "")
        self._emit_site_current("source→MD5 relay", "Внутри каждой reverse-ветки", "")
        if atf_side_site and bool(self.settings.get("atf_pixel_hash_after_reverse_miss", True)):
            self._emit_site_current("ATF pixel_hash", "Финальный fallback после reverse-miss", "")
        self._emit_site_current("Финальная сборка", "Ждёт независимые site/reverse-results", "")

        local_preflight_thread = None
        try:
            from core.local_preflight import start_parser_local_preflight
            # A resumed reverse-only run already has all exact-MD5 site checks
            # journaled.  Running the local pHash/image preflight over thousands
            # of old files before reverse starts can burn 10+ GB RAM and trip the
            # RAM guard while it adds little value.  Keep exact MD5/reverse work
            # in the branch workers and skip this warm-up when no site cursor has
            # pending work.
            if total_site_units <= 0 and total_files > int(self.settings.get("local_preflight_reverse_only_skip_threshold", 256) or 256):
                self._emit_log(
                    f"LOCAL PREFLIGHT SKIP: reverse-only resumed run files={total_files}; "
                    "hash/phash warm-up disabled to avoid RAM spike"
                )
            elif str(self.settings.get("_tagger_resolved_performance_profile", "")) == "low_memory" and total_files > int(self.settings.get("local_preflight_low_memory_skip_threshold", 5000) or 5000):
                self._emit_log(
                    f"LOCAL PREFLIGHT SKIP: low-memory profile files={total_files}; "
                    "large-run warm-up disabled to avoid RAM spike"
                )
            else:
                def _preflight_stop():
                    return bool(self.interruption_requested() or stop_event.is_set())
                local_preflight_thread = start_parser_local_preflight(
                    files, session_settings, log=self._emit_log, stop_check=_preflight_stop
                )
        except Exception as _e:
            self._emit_log(f"LOCAL PREFLIGHT ERROR: {type(_e).__name__}: {_e}")

        def _stat_inc(key, amount=1):
            with stats_lock:
                stats[key] = int(stats.get(key, 0) or 0) + int(amount or 0)

        def lane_settings(site):
            cfg = dict(session_settings)
            host = str(site.get("domain") or urlparse(writer._site_root_from_cfg(site)).netloc).lower().replace("www.", "")
            by_host = dict(cfg.get("http_min_interval_by_host") or {})
            try:
                block_delay = float(site.get("_blueprint_min_delay_ms", 0) or 0) / 1000.0
            except Exception:
                block_delay = 0.0
            by_host[host] = max(interval, block_delay)
            cfg["http_min_interval_by_host"] = by_host
            cfg["_cancel_callback"] = self.interruption_requested
            # v382: ATF pixel_hash is no longer an inline exact-MD5 fallback.
            # It is a last-resort final branch after IQDB/Danbooru IQDB/e621 IQDB/
            # SauceNAO/TinEye/rule34-side queues all failed to find tags or an
            # authoritative source MD5.  Direct MD5 cursors and source→MD5 relay
            # must stay fast and must not block on ATF media_assets pixel_hash.
            cfg["atf_pixel_hash_after_exact_md5_miss"] = False
            cfg["_allow_atf_pixel_hash_inline"] = False
            # Heavy rule34 image-key/SHA1 locators are not part of the direct MD5
            # cursor.  They belong to side queues; direct site cursors must never
            # block on them.
            if "rule34.xxx" in host or "api.rule34.xxx" in host:
                cfg["_rule34_variant_locators_run_in_side_queue"] = True
            return cfg

        def _path_hash_lock(key):
            with hash_lock:
                lock = hash_locks.get(key)
                if lock is None:
                    lock = threading.Lock()
                    hash_locks[key] = lock
                return lock

        def _hash_info(path):
            key = str(path)
            lock = _path_hash_lock(key)
            with lock:
                cached = hash_cache.get(key)
                if cached is not None:
                    try:
                        hash_cache.move_to_end(key)
                    except Exception:
                        pass
                    return dict(cached)
                info = {"path": Path(path), "search_img": Path(path), "from_filename": False,
                        "filename_md5": "", "real_md5": "", "video_error": None, "hash_error": None}
                try:
                    search_img = video_frame_image(path)
                    info["search_img"] = search_img
                except Exception as exc:
                    info["video_error"] = exc
                    info["hash_error"] = exc
                    hash_cache[key] = dict(info)
                    try:
                        hash_cache.move_to_end(key)
                        while len(hash_cache) > hash_cache_max:
                            hash_cache.popitem(last=False)
                    except Exception:
                        pass
                    return dict(info)
                if is_md5(Path(path).stem):
                    info["from_filename"] = True
                    info["filename_md5"] = Path(path).stem.lower()
                try:
                    info["real_md5"] = file_md5(search_img)
                except Exception as exc:
                    info["hash_error"] = exc
                hash_cache[key] = dict(info)
                try:
                    hash_cache.move_to_end(key)
                    while len(hash_cache) > hash_cache_max:
                        old_key, _old = hash_cache.popitem(last=False)
                        hash_locks.pop(old_key, None)
                except Exception:
                    pass
                return dict(info)

        def _run_engine_once(local, site, md5, path):
            old_lookup_path = getattr(local, "_current_md5_lookup_path", "")
            local._current_md5_lookup_path = str(path)
            try:
                tags, source, groups = local.engine_by_md5(site, md5)
            finally:
                local._current_md5_lookup_path = old_lookup_path
            status = str(getattr(local, "_last_lookup_status", "") or "")
            method = str(getattr(local, "_last_lookup_match_method", "md5") or "md5")
            network_failed = local.transient_network_failed()
            return list(tags or []), str(source or ""), groups or {}, status, method, bool(network_failed)

        def _persist_site_match(path, shown, site_key, engine, checked_md5, tags, source, groups, method):
            per_source_groups = [{"url": source, "groups": groups or {"general": list(tags or [])}, "method": method or "md5"}] if source else []
            try:
                with persist_lock:
                    row = processed_records_many(session_settings, [path]).get(str(path), {})
                    status = str(row.get("status") or prior_global_status.get(str(path)) or "").lower()
                    media_path = str(row.get("media_path") or existing_media_map.get(str(path)) or "")
                    if status in ("found", "tagged", "partial") and media_path and Path(media_path).exists():
                        outcome = writer.merge_conveyor_match_into_existing(
                            media_path, path, list(tags or []),
                            [f"{method or 'md5'} {shown} {source}"] if source else [],
                            [groups] if groups else [], per_source_groups,
                        )
                    else:
                        outcome = writer.save_conveyor_match(
                            path, list(tags or []),
                            [f"{method or 'md5'} {shown} {source}"] if source else [],
                            [groups] if groups else [], per_source_groups,
                        )
                    if outcome != "tagged":
                        return False, f"persist outcome={outcome}"
                    try:
                        mark_site_scanned(
                            session_settings, path, site_key, engine=engine,
                            scan_revision=SITE_SCAN_REVISION, outcome="match",
                            checked_md5=checked_md5, source_url=source,
                        )
                    except Exception as e:
                        if self._handle_parser_db_write_error(e, f"site match checkpoint {site_key} {Path(path).name}"):
                            return False, str(e)
                        raise
                    try:
                        remove_reverse_retry(session_settings, path, service="saucenao")
                    except Exception:
                        pass
                with state_lock:
                    found_paths.add(str(path))
                    prior_global_status[str(path)] = "found"
                    try:
                        existing_media_map[str(path)] = str(result_paths_for(session_settings, path, "tagged")["media_file"])
                    except Exception:
                        pass
                _stat_inc("tagged", 1)
                return True, ""
            except Exception as e:
                return False, str(e)

        def _mark_site_miss(path, shown, site_key, engine, checked_md5, source=""):
            try:
                with persist_lock:
                    mark_site_scanned(
                        session_settings, path, site_key, engine=engine,
                        scan_revision=SITE_SCAN_REVISION, outcome="miss",
                        checked_md5=checked_md5, source_url=source,
                    )
                return True
            except Exception as e:
                handled = self._handle_parser_db_write_error(e, f"site miss checkpoint {site_key} {Path(path).name}")
                if not handled:
                    self._emit_log(f"[MD5:{shown}:{Path(path).name}] CHECKPOINT ERROR: {e}")
                return False

        def _should_reverse(path):
            if not reverse_chain:
                return False
            if str(path) in found_paths:
                return False
            old_status = str(prior_global_status.get(str(path)) or "").lower()
            if old_status and not (bool(self.settings.get("retry_nomatch", False)) and old_status in ("nomatch", "no_match")):
                return False
            return True

        reverse_filename_prefilter = {"key": 0, "generic": 0, "no_key": 0}
        reverse_stage_by_file = {}
        reverse_staged_enabled = bool(self.settings.get("tagger_reverse_staged_scheduler_enabled", True))
        reverse_branch_status_map = {}
        reverse_branch_terminal_statuses = {"done_miss", "done_match", "source_only", "skipped", "skipped_filename"}
        reverse_branch_keys_for_journal = [str(k).strip().lower() for k in reverse_queues.keys()]
        if rule34_side_queue_enabled and rule34_side_site:
            reverse_branch_keys_for_journal.append("rule34_40hex")
        try:
            if not bool(self.settings.get("retry_reverse_branch_status", False)):
                reverse_branch_status_map = reverse_branch_status_many(
                    session_settings, files, reverse_branch_keys_for_journal, scan_revision=SITE_SCAN_REVISION
                )
        except Exception as _e:
            reverse_branch_status_map = {}
            self._emit_log(f"REVERSE BRANCH JOURNAL LOAD ERROR: {type(_e).__name__}: {_e}")

        def _reverse_stage_for_branch(branch_key):
            if not reverse_staged_enabled:
                return 1
            key = str(branch_key or "").strip().lower()
            if key in ("e621_iqdb", "iqdb"):
                return 1
            if key == "saucenao":
                return 2
            return 3

        def _branch_status_key(branch_label_or_key):
            text = str(branch_label_or_key or "").strip().lower().replace(" ", "_").replace("/", "_")
            if "rule34" in text:
                return "rule34_40hex"
            if "e621" in text and "iqdb" in text:
                return "e621_iqdb"
            if "danbooru" in text and "iqdb" in text:
                return "danbooru_iqdb"
            if text == "saucenao" or "saucenao" in text:
                return "saucenao"
            if text == "tineye" or "tineye" in text:
                return "tineye"
            if text == "ascii2d" or "ascii2d" in text:
                return "ascii2d"
            if text == "iqdb" or text.endswith("_iqdb"):
                return text
            return text

        def _branch_terminal_status(path, branch_key):
            try:
                return str((reverse_branch_status_map.get(str(path)) or {}).get(str(branch_key).lower()) or "")
            except Exception:
                return ""

        def _branch_already_terminal(path, branch_key):
            status = _branch_terminal_status(path, branch_key)
            return bool(status in reverse_branch_terminal_statuses)

        def _persist_reverse_branch_status(path, branch_key, status, reason=""):
            bkey = _branch_status_key(branch_key)
            if not bkey:
                return False
            try:
                with persist_lock:
                    mark_reverse_branch_status(
                        session_settings, path, bkey,
                        status=str(status or ""), reason=str(reason or ""), scan_revision=SITE_SCAN_REVISION,
                    )
                try:
                    reverse_branch_status_map.setdefault(str(path), {})[bkey] = str(status or "")
                except Exception:
                    pass
                return True
            except Exception as _e:
                self._emit_log(f"[Reverse:{Path(path).name}] BRANCH JOURNAL ERROR [{bkey}]: {type(_e).__name__}: {_e}")
                return False

        def _result_to_branch_status(result, *, network_defer=False):
            if network_defer:
                return "deferred"
            r = str(result or "").lower()
            if r in ("tagged", "partial", "match"):
                return "done_match"
            if r in ("skip", "skipped"):
                return "skipped"
            if r in ("source_only", "skip_url"):
                return "source_only"
            if r in ("error",):
                return "error"
            return "done_miss"

        def _rule34_variant_key_for_path(path):
            # Filename-only locator branch.  Generic Telegram/phone names like
            # photo_2022-..., video_2024-..., 1.jpg carry no rule34 image key,
            # so they must not waste a rule34 40hex/SHA1 branch slot.  Pixel
            # reverse branches are still queued elsewhere.
            try:
                return extract_rule34_40hex_key(path)
            except Exception:
                return ""

        def _note_filename_locator_prefilter(path):
            try:
                bucket = filename_locator_bucket(path)
            except Exception:
                bucket = "no_key"
            with reverse_lock:
                reverse_filename_prefilter[bucket] = int(reverse_filename_prefilter.get(bucket, 0) or 0) + 1
            return bucket

        def _queue_final_nomatch(path, reason):
            if str(path) in found_paths:
                return False
            final_nomatch_q.put(Path(path))
            self._emit_site_current("Финальная сборка", f"No-match ждёт запись: {reason}", str(path))
            return True

        def _file_claimed(path) -> bool:
            try:
                return str(path) in found_paths
            except Exception:
                return False

        def _reverse_retry_service(branch_label: str, result: str = "") -> str:
            label = str(branch_label or "reverse").strip().lower().replace(" ", "_").replace("/", "_")
            if "saucenao" in label:
                return "saucenao"
            if "e621" in label and "iqdb" in label:
                return "e621_iqdb"
            if "danbooru" in label and "iqdb" in label:
                return "danbooru_iqdb"
            if "iqdb" in label:
                return "iqdb"
            if "tineye" in label:
                return "tineye"
            if "rule34" in label:
                return "rule34_40hex"
            return label or "reverse"

        def _queue_reverse_retry(path, branch_label, *, delay_seconds=300, reason="network_defer"):
            service = _reverse_retry_service(branch_label)
            retry_at = int(time.time() + max(60, int(delay_seconds or 300)))
            retry_key = (str(path), service)
            with reverse_lock:
                if retry_key in reverse_retry_persisted:
                    return False
                reverse_retry_persisted.add(retry_key)
            try:
                with persist_lock:
                    enqueue_reverse_retry(session_settings, path, service=service, retry_after=retry_at, reason=reason)
                _stat_inc("reverse_retry_queued", 1)
                self._emit_log(f"[{branch_label}:{Path(path).name}] REVERSE RETRY QUEUED: service={service} retry_after={retry_at} reason={reason}")
                return True
            except Exception as e:
                with reverse_lock:
                    reverse_retry_persisted.discard(retry_key)
                self._emit_log(f"[{branch_label}:{Path(path).name}] REVERSE RETRY QUEUE ERROR: {e}")
                _stat_inc("errors", 1)
                return False

        def _candidate_reverse_items_for_stage(path, stage):
            """Return queueable reverse work for the requested stage.

            v408: reverse is staged.  Cheap/pixel IQDB branches run first;
            SauceNAO/Pixiv donor runs only after fast miss; very slow/filename
            locators run last.  Completed branch-journal rows are skipped so a
            resumed run does not repeat 30–60s misses.
            """
            items = []
            for bkey, bq in reverse_queues.items():
                bkey_norm = str(bkey).strip().lower()
                if _reverse_stage_for_branch(bkey_norm) != int(stage):
                    continue
                if _branch_already_terminal(path, bkey_norm):
                    continue
                items.append((bkey_norm, str(dict(reverse_branches).get(bkey_norm) or bkey_norm), bq, None))
            if rule34_side_queue_enabled and rule34_side_site and _reverse_stage_for_branch("rule34_40hex") == int(stage):
                if not _branch_already_terminal(path, "rule34_40hex"):
                    bucket = _note_filename_locator_prefilter(path)
                    if bucket == "key":
                        variant_key = _rule34_variant_key_for_path(path)
                        if variant_key:
                            items.append(("rule34_40hex", "rule34 40hex/SHA1", rule34_variant_q, variant_key))
                    else:
                        _persist_reverse_branch_status(path, "rule34_40hex", "skipped_filename", f"filename_prefilter:{bucket}")
            return items

        def _queue_reverse_stage(path, stage, reason):
            key = str(path)
            items = _candidate_reverse_items_for_stage(path, stage)
            if not items:
                return False
            with reverse_lock:
                reverse_stage_by_file[key] = int(stage)
                reverse_remaining[key] = len(items)
                queued_branch_keys = []
                variant_key = ""
                for bkey, label, bq, extra in items:
                    if bkey == "rule34_40hex":
                        bq.put((Path(path), str(extra or "")))
                        variant_key = str(extra or "")
                    else:
                        bq.put(Path(path))
                    queued_branch_keys.append(bkey)
                reverse_manifest[key] = {
                    "path": str(path),
                    "reason": str(reason or ""),
                    "stage": int(stage),
                    "branches": int(len(items)),
                    "queued_branch_keys": list(queued_branch_keys),
                    "variant_key": str(variant_key or ""),
                }
            _stat_inc("reverse_queued_tasks", len(items))
            self._emit_site_current("Reverse side queue", f"Этап {stage}: {len(items)} веток; {reason}", str(path))
            return True

        def _queue_next_reverse_stage(path, reason, *, start_stage=1):
            max_stage = 3 if reverse_staged_enabled else 1
            for stage in range(max(1, int(start_stage or 1)), max_stage + 1):
                if _queue_reverse_stage(path, stage, reason):
                    return True
            return False

        def _queue_reverse(path, reason, *, log_admit=True):
            if not _should_reverse(path):
                return False
            key = str(path)
            with reverse_lock:
                if key in reverse_scheduled:
                    return False
                reverse_scheduled.add(key)
            if not _queue_next_reverse_stage(path, reason, start_stage=1):
                return _queue_final_nomatch(path, "no-reverse-branches-or-branch-journal-complete")
            _stat_inc("reverse_queued_files", 1)
            if log_admit:
                try:
                    st = int(reverse_stage_by_file.get(key, 1) or 1)
                    br = int(reverse_remaining.get(key, 0) or 0)
                except Exception:
                    st, br = 1, 0
                self._emit_log(f"[REVERSE-ADMIT:{Path(path).name}] all enabled MD5 sites are final miss; stage={st} branches={br}; reason={reason}")
            return True

        def _note_reverse_branch_final(path, branch_label, result, *, network_defer=False, branch_key=None):
            key = str(path)
            bkey_for_status = _branch_status_key(branch_key or branch_label)
            _persist_reverse_branch_status(path, bkey_for_status, _result_to_branch_status(result, network_defer=network_defer), result)
            should_finalize = False
            should_retry = False
            retry_reason = "network_defer"
            with reverse_lock:
                reverse_active.discard(f"{branch_label}:{key}")
                if network_defer:
                    reverse_had_defer.add(key)
                    reverse_defer_branch.setdefault(key, str(branch_label or "reverse"))
                    retry_reason = str(result or "network_defer")
                if result in ("tagged", "partial", "match"):
                    with state_lock:
                        found_paths.add(key)
                        prior_global_status[key] = "found"
                elif result in ("skip", "skipped"):
                    # If a reverse branch's Tagger.process_image skipped because
                    # the file is already archived, the file is terminal for the
                    # reverse scheduler.  Do not advance to SauceNAO/Danbooru
                    # IQDB/rule34 stages only to immediately SKIP ARCHIVED again.
                    try:
                        archived_status = str(prior_global_status.get(key) or output_processed_status(session_settings, Path(path)) or "").lower()
                    except Exception:
                        archived_status = str(prior_global_status.get(key) or "").lower()
                    if archived_status and not (bool(self.settings.get("retry_nomatch", False)) and archived_status in ("nomatch", "no_match")):
                        prior_global_status[key] = archived_status
                        reverse_had_defer.discard(key)
                        reverse_defer_branch.pop(key, None)
                        reverse_stage_by_file.pop(key, None)
                        if archived_status in ("found", "tagged", "partial"):
                            found_paths.add(key)
                remaining = int(reverse_remaining.get(key, 0) or 0)
                if remaining > 0:
                    remaining -= 1
                    reverse_remaining[key] = remaining
                if remaining == 0 and key not in found_paths:
                    if key in reverse_had_defer:
                        should_retry = True
                    else:
                        should_finalize = True
            if should_finalize:
                current_stage = int(reverse_stage_by_file.get(key, 1) or 1)
                if _queue_next_reverse_stage(path, f"stage-{current_stage}-miss", start_stage=current_stage + 1):
                    should_finalize = False
                    self._emit_site_current("Reverse side queue", f"Этап {current_stage + 1}: предыдущий miss", str(path))
            if should_retry:
                # A temporary network failure is not a final no-match.  Persist it
                # into reverse_retry_queue so the file survives app restart and is
                # visible in diagnostics instead of staying only in RAM.
                retry_branch = reverse_defer_branch.get(key, branch_label)
                _queue_reverse_retry(path, retry_branch, reason=retry_reason)
                self._emit_site_current("Reverse retry", f"Повтор позже: {retry_branch}", str(path))
            if should_finalize:
                _queue_final_nomatch(path, "all-reverse-branches-miss")

        def _note_site_final(path, *, final=True, network_defer=False):
            key = str(path)
            if network_defer:
                with state_lock:
                    per_file_had_network_defer.add(key)
                return
            should_queue = False
            with state_lock:
                remaining = int(per_file_remaining.get(key, 0) or 0)
                if remaining > 0:
                    remaining -= 1
                    per_file_remaining[key] = remaining
                if remaining == 0 and key not in found_paths and key not in per_file_had_network_defer:
                    should_queue = True
            if should_queue:
                _queue_reverse(path, "md5-all-sites-miss")

        # Files that are already fully journaled for all currently enabled MD5
        # sites are still eligible for reverse.  Do NOT enqueue them before the
        # reverse workers exist: on large resumed runs this could look like a
        # successful REVERSE-ADMIT in the log while the live branch workers then
        # started against empty queues.  Keep the candidate list here and feed it
        # after reverse/final workers have been started.
        already_journaled_reverse_candidates = [
            Path(path) for path in files
            if int(per_file_remaining.get(str(path), 0) or 0) == 0 and _should_reverse(path)
        ]

        progress_last = {"units": 0, "time": 0.0}
        def _emit_unit_progress(path=None):
            nonlocal completed_site_units
            now = time.time()
            if completed_site_units - progress_last["units"] >= 100 or now - progress_last["time"] >= 2.0 or completed_site_units >= total_site_units:
                progress_last["units"] = completed_site_units
                progress_last["time"] = now
                self._emit_progress(completed_site_units, max(1, total_site_units))
                if path is not None:
                    self._emit_current_file(str(path))

        def site_cursor_loop(lane_key, shown, site_key, engine, site):
            nonlocal completed_site_units
            def _lane_log(message):
                self._emit_log(f"[MD5:{shown}:{current_name[0]}] {str(message).strip()}")
            current_name = ["—"]
            local = Tagger(lane_settings(site), _lane_log)
            local.cancel_callback = self.interruption_requested
            pending_total = int(pending_by_site.get(shown, 0) or 0)
            checked_here = 0
            skipped_done = 0
            self._emit_log(f"SITE CURSOR START [{shown}]: pending={pending_total}; does not wait for other sites")
            for path in files:
                if self.interruption_requested() or stop_event.is_set() or self._memory_guard_check(f"site:{shown}"):
                    stop_event.set()
                    break
                key = str(path)
                if site_key in set((site_done_map.get(key) or {}).keys()):
                    skipped_done += 1
                    continue
                current_name[0] = Path(path).name
                self._wait_if_paused_or_delay(0)
                if self.interruption_requested() or stop_event.is_set():
                    break
                self._emit_site_current(shown, "MD5 direct", str(path))
                info = _hash_info(path)
                if info.get("hash_error") is not None:
                    self._emit_log(f"[MD5:{shown}:{Path(path).name}] HASH SKIP: {type(info['hash_error']).__name__}: {str(info['hash_error'])[:180]}")
                    _stat_inc("errors", 1)
                    with state_lock:
                        completed_site_units += 1
                    _emit_unit_progress(path)
                    continue
                attempts = []
                if info.get("from_filename") and info.get("filename_md5"):
                    attempts.append(("filename", str(info.get("filename_md5") or "")))
                real_md5 = str(info.get("real_md5") or "")
                if real_md5 and real_md5 not in [m for _phase, m in attempts]:
                    attempts.append(("real", real_md5))
                if not attempts:
                    self._emit_log(f"[MD5:{shown}:{Path(path).name}] HASH SKIP: no usable md5")
                    _stat_inc("errors", 1)
                    with state_lock:
                        completed_site_units += 1
                    _emit_unit_progress(path)
                    continue
                final_network_failed = False
                final_auth_required = False
                matched = False
                checked_md5 = ""
                for phase, md5 in attempts:
                    if self.interruption_requested() or stop_event.is_set():
                        break
                    checked_md5 = md5
                    if phase == "filename":
                        self._emit_log(f"[MD5:{shown}:{Path(path).name}] TRY MD5 FROM FILENAME: {md5}")
                    else:
                        self._emit_log(f"[MD5:{shown}:{Path(path).name}] TRY REAL FILE MD5: {md5}")
                    try:
                        local._reset_network_state()
                        tags, source, groups, lookup_status, method, network_failed = _run_engine_once(local, site, md5, path)
                    except InterruptedError:
                        if self.interruption_requested():
                            break
                        tags, source, groups, lookup_status, method, network_failed = [], "", {}, "", "md5", True
                    except Exception as e:
                        self._emit_log(f"[MD5:{shown}:{Path(path).name}] ERROR: {e}")
                        tags, source, groups, lookup_status, method, network_failed = [], "", {}, "", "md5", _looks_like_network_exception(e)
                    final_network_failed = bool(network_failed)
                    final_auth_required = lookup_status == "auth_required"
                    if tags:
                        matched = True
                        if method == "md5":
                            self._emit_log(f"[MD5:{shown}:{Path(path).name}] MATCH {source}")
                        else:
                            self._emit_log(f"[MD5:{shown}:{Path(path).name}] VARIANT MATCH {method} {source}")
                        ok, err = _persist_site_match(path, shown, site_key, engine, md5, tags, source, groups, method)
                        if ok:
                            self._emit_site_current(shown, f"Найдено: {len(tags)} тегов", str(path))
                            _stat_inc("site_checks", 1)
                            try:
                                site_done_map.setdefault(key, {})[site_key] = "match"
                            except Exception:
                                pass
                        else:
                            self._emit_log(f"[MD5:{shown}:{Path(path).name}] PERSIST ERROR: {err}")
                            _stat_inc("errors", 1)
                        break
                    if final_network_failed or final_auth_required:
                        break
                    # If filename-MD5 missed, immediately try the real bytes/frame
                    # for this same site.  Other sites do the same independently.
                    continue
                if not matched:
                    if final_auth_required:
                        self._emit_site_current(shown, "Нужен API ключ", str(path))
                        _note_site_final(path, network_defer=True)
                    elif final_network_failed:
                        self._emit_site_current(shown, "Сеть / повтор позже", str(path))
                        _stat_inc("deferred_network", 1)
                        _note_site_final(path, network_defer=True)
                    else:
                        if _mark_site_miss(path, shown, site_key, engine, checked_md5):
                            self._emit_site_current(shown, "Нет совпадения", str(path))
                            _stat_inc("site_checks", 1)
                            try:
                                site_done_map.setdefault(key, {})[site_key] = "miss"
                            except Exception:
                                pass
                            _note_site_final(path)
                else:
                    _note_site_final(path)
                checked_here += 1
                with state_lock:
                    completed_site_units += 1
                _emit_unit_progress(path)
                if checked_here and checked_here % 500 == 0:
                    self._emit_log(f"SITE CURSOR [{shown}]: checked={checked_here}/{pending_total}; skipped_done={skipped_done}")
            self._emit_site_current(shown, "Курсор завершён", "")
            self._emit_log(f"SITE CURSOR DONE [{shown}]: checked={checked_here}/{pending_total}; skipped_done={skipped_done}")

        def _base_reverse_settings():
            cfg = dict(session_settings)
            cfg["enable_md5_lookup"] = False
            cfg["_allow_reverse_md5_relay_lookup"] = True
            cfg["_cancel_callback"] = self.interruption_requested
            # v382: reverse branches may relay extracted source MD5s through ATF
            # exact MD5, but they must not trigger ATF pixel_hash.  pixel_hash is
            # only allowed after all reverse queues have failed.
            cfg["atf_pixel_hash_after_exact_md5_miss"] = False
            cfg["_allow_atf_pixel_hash_inline"] = False
            return cfg

        def _settings_for_reverse_branch(branch_key, branch_label):
            cfg = _base_reverse_settings()
            # One reverse service per queue.  URL→MD5 relay remains enabled inside
            # every branch, so an IQDB/SauceNAO/e621/TinEye URL still extracts a
            # verified MD5 and fans it through the normal MD5 parsers.
            for name in ("enable_iqdb", "enable_danbooru_iqdb", "enable_e621_iqdb", "enable_ascii2d", "enable_saucenao", "enable_tineye"):
                cfg[name] = False
            cfg["source_md5_relay_fast_saucenao_enabled"] = False
            cfg["source_md5_relay_fast_saucenao_force"] = False
            cfg["_skip_tineye_this_pass"] = True
            cfg["_reverse_branch_no_nomatch"] = True
            cfg["_reverse_branch_label"] = branch_label
            if branch_key == "iqdb":
                cfg["enable_iqdb"] = True
            elif branch_key == "danbooru_iqdb":
                cfg["enable_danbooru_iqdb"] = True
            elif branch_key == "e621_iqdb":
                cfg["enable_e621_iqdb"] = True
            elif branch_key == "ascii2d":
                cfg["enable_ascii2d"] = True
            elif branch_key == "saucenao":
                cfg["enable_saucenao"] = True
                cfg["source_md5_relay_fast_saucenao_enabled"] = bool(self.settings.get("source_md5_relay_fast_saucenao_enabled", True))
                cfg["source_md5_relay_fast_saucenao_force"] = bool(self.settings.get("source_md5_relay_fast_saucenao_force", True))
            elif branch_key == "tineye":
                cfg["enable_tineye"] = True
                cfg["_skip_tineye_this_pass"] = False
            return cfg

        def reverse_branch_loop(branch_key, branch_label, branch_q, worker_index=1):
            def _fallback_log(message):
                self._emit_log(f"[{branch_label}:{current_name[0]}] {str(message).strip()}")
            current_name = ["—"]
            current_path = [""]
            e621_cooldown_until = [0.0]
            local = Tagger(_settings_for_reverse_branch(branch_key, branch_label), _fallback_log)
            def _branch_cancelled():
                if self.interruption_requested() or stop_event.is_set():
                    return True
                key = str(current_path[0] or "")
                return bool(key and key in found_paths)
            local.cancel_callback = _branch_cancelled
            self._emit_log(f"REVERSE BRANCH START [{branch_label}]: own queue; waits only for MD5-miss admission")
            while not self.interruption_requested() and not stop_event.is_set():
                if self._memory_guard_check(f"reverse:{branch_label}"):
                    stop_event.set()
                    break
                if branch_key == "e621_iqdb" and e621_cooldown_until[0] > time.time():
                    wait_left = int(max(0, e621_cooldown_until[0] - time.time()))
                    self._emit_site_current(branch_label, f"Cooldown после 429: {wait_left}s", "")
                    self._wait_if_paused_or_delay(min(5.0, max(1.0, wait_left)))
                    continue
                item = branch_q.get()
                try:
                    if item is None:
                        break
                    path = Path(item)
                    current_name[0] = path.name
                    current_path[0] = str(path)
                    if _file_claimed(path):
                        _stat_inc("reverse_canceled", 1)
                        self._emit_site_current(branch_label, "Skip: уже найдено другой веткой", str(path))
                        _note_reverse_branch_final(path, branch_label, "skip", branch_key=branch_key)
                        continue
                    with reverse_lock:
                        reverse_active.add(f"{branch_label}:{str(path)}")
                    self._emit_site_current(branch_label, "Reverse queue: поиск", str(path))
                    _stat_inc("reverse_started", 1)
                    try:
                        before_tagged = int(getattr(local, "_tineye_tagged_total", 0) or 0)
                        before_source = int(getattr(local, "_tineye_source_only_total", 0) or 0)
                        result = local.process_image(path, persist_lock=persist_lock)
                        _stat_inc("tineye_tagged", max(0, int(getattr(local, "_tineye_tagged_total", 0) or 0) - before_tagged))
                        _stat_inc("tineye_source_only", max(0, int(getattr(local, "_tineye_source_only_total", 0) or 0) - before_source))
                        _stat_inc("reverse_done", 1)
                        if result in ("tagged", "partial"):
                            _stat_inc("tagged", 1)
                            self._emit_site_current(branch_label, "Reverse: найдено/слито", str(path))
                            self._emit_site_current("Финальная сборка", f"{branch_label}: теги/источники сохранены", str(path))
                            _note_reverse_branch_final(path, branch_label, result, branch_key=branch_key)
                        elif result == "retry_saucenao":
                            try:
                                retry_at = int(local.saucenao_retry_after_epoch() or (time.time() + float(self.settings.get("saucenao_cooldown_seconds", 3600) or 3600)))
                            except Exception:
                                retry_at = int(time.time() + 3600)
                            try:
                                with persist_lock:
                                    enqueue_reverse_retry(session_settings, path, service="saucenao", retry_after=retry_at, reason="api_cooldown")
                                _stat_inc("deferred_saucenao", 1)
                            except Exception as e:
                                self._emit_log(f"[{branch_label}:{path.name}] SAUCENAO RETRY QUEUE ERROR: {e}")
                            _note_reverse_branch_final(path, branch_label, result, network_defer=True, branch_key=branch_key)
                        elif result == "retry_network":
                            _stat_inc("deferred_network", 1)
                            if branch_key == "e621_iqdb":
                                try:
                                    cd = max(60, min(1800, int(self.settings.get("e621_iqdb_branch_cooldown_seconds", 300) or 300)))
                                except Exception:
                                    cd = 300
                                e621_cooldown_until[0] = time.time() + cd
                                self._emit_log(f"[{branch_label}:{path.name}] e621 IQDB cooldown: {cd}s after temporary failure/429; branch only")
                                _queue_reverse_retry(path, branch_label, delay_seconds=cd, reason="e621_iqdb_network_defer")
                            self._emit_site_current(branch_label, "Сеть / повтор позже", str(path))
                            _note_reverse_branch_final(path, branch_label, result, network_defer=True, branch_key=branch_key)
                        else:
                            self._emit_site_current(branch_label, "Miss", str(path))
                            _note_reverse_branch_final(path, branch_label, result, branch_key=branch_key)
                    except Exception as e:
                        if not self.interruption_requested():
                            _stat_inc("errors", 1)
                            self._emit_log(f"[{branch_label}:{path.name}] ERROR: {e}")
                        _note_reverse_branch_final(path, branch_label, "error", branch_key=branch_key)
                finally:
                    current_name[0] = "—"
                    current_path[0] = ""
                    branch_q.task_done()
            self._emit_log(f"REVERSE BRANCH DONE [{branch_label}]")

        def rule34_variant_loop():
            if not (rule34_side_queue_enabled and rule34_side_site):
                return
            variant_settings = dict(session_settings)
            variant_settings["_cancel_callback"] = self.interruption_requested
            variant_settings["_rule34_variant_locators_run_in_side_queue"] = False
            variant_settings["atf_pixel_hash_after_exact_md5_miss"] = False
            variant_settings["_allow_atf_pixel_hash_inline"] = False
            variant_settings["enable_md5_lookup"] = True
            by_host = dict(variant_settings.get("http_min_interval_by_host") or {})
            by_host["rule34.xxx"] = max(interval, float(by_host.get("rule34.xxx", 0) or 0))
            by_host["hl.rule34.xxx"] = max(interval, float(by_host.get("hl.rule34.xxx", 0) or 0))
            variant_settings["http_min_interval_by_host"] = by_host
            current_name = ["—"]
            current_path = [""]
            def _variant_log(message):
                self._emit_log(f"[rule34 40hex/SHA1:{current_name[0]}] {str(message).strip()}")
            local = Tagger(variant_settings, _variant_log)
            def _variant_cancelled():
                if self.interruption_requested() or stop_event.is_set():
                    return True
                key = str(current_path[0] or "")
                return bool(key and key in found_paths)
            local.cancel_callback = _variant_cancelled
            self._emit_log("REVERSE BRANCH START [rule34 40hex/SHA1]: own queue; waits only for MD5-miss admission")
            while not self.interruption_requested() and not stop_event.is_set():
                if self._memory_guard_check("reverse:rule34 40hex/SHA1"):
                    stop_event.set()
                    break
                item = rule34_variant_q.get()
                try:
                    if item is None:
                        break
                    path, variant_key = item
                    path = Path(path)
                    current_name[0] = path.name
                    current_path[0] = str(path)
                    if _file_claimed(path):
                        _stat_inc("reverse_canceled", 1)
                        self._emit_site_current("rule34 40hex/SHA1", "Skip: уже найдено другой веткой", str(path))
                        _note_reverse_branch_final(path, "rule34 40hex/SHA1", "skip", branch_key="rule34_40hex")
                        continue
                    with reverse_lock:
                        reverse_active.add(f"rule34 40hex/SHA1:{str(path)}")
                    self._emit_site_current("rule34 40hex/SHA1", "Ищет image-key/SHA1", str(path))
                    tags = []
                    source = ""
                    groups = {}
                    try:
                        old_lookup_path = getattr(local, "_current_md5_lookup_path", "")
                        local._current_md5_lookup_path = str(path)
                        local._reset_network_state()
                        try:
                            tags, source, groups = local._rule34xxx_image_key_locator_lookup(rule34_side_site, variant_key)
                            if not tags and bool(variant_settings.get("rule34_sha1_async_locator_enabled", True)):
                                tags, source, groups = local._rule34xxx_sha1_async_locator_lookup(rule34_side_site, variant_key)
                        finally:
                            local._current_md5_lookup_path = old_lookup_path
                        if tags:
                            all_tags = list(tags or [])
                            all_sources = [f"rule34_variant_side_queue rule34.xxx {source}"] if source else []
                            all_groups = [groups] if groups else []
                            per_source = [{"url": source, "groups": groups or {"general": list(tags)}, "method": "rule34_variant_side_queue"}] if source else []
                            for site_md5 in list(getattr(local, "_last_rule34_image_key_site_md5s", []) or []):
                                site_md5 = str(site_md5 or "").strip().lower()
                                if not is_md5(site_md5):
                                    continue
                                self._emit_log(f"[rule34 40hex/SHA1:{path.name}] SITE MD5 RELAY: {site_md5}")
                                old_lookup_path = getattr(local, "_current_md5_lookup_path", "")
                                local._current_md5_lookup_path = str(path)
                                try:
                                    relay_tags, relay_sources, relay_groups = local.md5_lookup_all(site_md5)
                                    relay_per_source = list(getattr(local, "_last_md5_source_tag_groups", []) or [])
                                finally:
                                    local._current_md5_lookup_path = old_lookup_path
                                if relay_tags:
                                    seen = set(map(str, all_tags))
                                    for t in relay_tags:
                                        if str(t) not in seen:
                                            seen.add(str(t)); all_tags.append(t)
                                    all_sources.extend([f"rule34_variant_md5_relay {site_md5} {src}" for src in list(relay_sources or [])])
                                    all_groups.extend([g for g in list(relay_groups or []) if g])
                                    per_source.extend(relay_per_source)
                            with persist_lock:
                                row = processed_records_many(session_settings, [path]).get(str(path), {})
                                media_path = str(row.get("media_path") or existing_media_map.get(str(path)) or "")
                                status = str(row.get("status") or prior_global_status.get(str(path)) or "").lower()
                                if status in ("found", "tagged", "partial") and media_path and Path(media_path).exists():
                                    outcome = writer.merge_conveyor_match_into_existing(media_path, path, all_tags, all_sources, all_groups, per_source)
                                else:
                                    outcome = writer.save_conveyor_match(path, all_tags, all_sources, all_groups, per_source)
                            if outcome == "tagged":
                                self._emit_log(f"[rule34 40hex/SHA1:{path.name}] MATCH {source} tags={len(all_tags)}")
                                with state_lock:
                                    found_paths.add(str(path))
                                    prior_global_status[str(path)] = "found"
                                _stat_inc("tagged", 1)
                                self._emit_site_current("rule34 40hex/SHA1", f"Найдено: {len(all_tags)} тегов", str(path))
                                _note_reverse_branch_final(path, "rule34 40hex/SHA1", "match", branch_key="rule34_40hex")
                            else:
                                self._emit_log(f"[rule34 40hex/SHA1:{path.name}] PERSIST ERROR: {outcome}")
                                _note_reverse_branch_final(path, "rule34 40hex/SHA1", "error", branch_key="rule34_40hex")
                        else:
                            self._emit_log(f"[rule34 40hex/SHA1:{path.name}] no DAPI-verified post")
                            _note_reverse_branch_final(path, "rule34 40hex/SHA1", "nomatch", branch_key="rule34_40hex")
                    except Exception as e:
                        if not self.interruption_requested():
                            self._emit_log(f"[rule34 40hex/SHA1:{path.name}] ERROR: {e}")
                            _stat_inc("errors", 1)
                        _note_reverse_branch_final(path, "rule34 40hex/SHA1", "error", branch_key="rule34_40hex")
                finally:
                    current_name[0] = "—"
                    current_path[0] = ""
                    rule34_variant_q.task_done()
            self._emit_log("REVERSE BRANCH DONE [rule34 40hex/SHA1]")

        final_settings = _base_reverse_settings()
        for name in ("enable_iqdb", "enable_danbooru_iqdb", "enable_e621_iqdb", "enable_ascii2d", "enable_saucenao", "enable_tineye"):
            final_settings[name] = False
        final_settings["source_md5_relay_fast_saucenao_enabled"] = False
        final_settings["source_md5_relay_fast_saucenao_force"] = False
        final_settings["_skip_tineye_this_pass"] = True
        final_settings["atf_pixel_hash_after_exact_md5_miss"] = False
        final_settings["_allow_atf_pixel_hash_inline"] = False
        final_atf_pixel_done = set()

        def _try_final_atf_pixel_hash_after_reverse(path, local):
            """Last-resort ATF pixel_hash check.

            v382 order:
              direct MD5 all sites -> reverse queues/SauceNAO/IQDB/source-MD5 relay
              -> ATF pixel_hash -> final no_match.

            This keeps ATF exact MD5 fast and prevents SauceNAO/IQDB MD5 relay
            from spawning expensive ATF media_assets pixel_hash lookups.
            """
            if not bool(session_settings.get("atf_pixel_hash_after_reverse_miss", True)):
                return "skip"
            if not bool(session_settings.get("atf_pixel_hash_locator_enabled", True)):
                return "skip"
            if not atf_side_site:
                return "skip"
            pkey = str(Path(path))
            if pkey in final_atf_pixel_done:
                return "skip"
            final_atf_pixel_done.add(pkey)
            if pkey in found_paths:
                return "skip"
            try:
                info = _hash_info(path)
                local_md5 = str(info.get("real_md5") or info.get("filename_md5") or "").strip().lower()
            except Exception:
                local_md5 = ""
            if not is_md5(local_md5):
                local_md5 = ""
            self._emit_site_current("ATF pixel_hash", "Финальный fallback после reverse-miss", str(path))
            self._emit_log(f"[ATF pixel_hash:{Path(path).name}] START AFTER REVERSE MISS")
            try:
                old_lookup_path = getattr(local, "_current_md5_lookup_path", "")
                local._current_md5_lookup_path = str(path)
                local._last_atf_pixel_hash_site_md5s = []
                local._last_variant_site_md5s = []
                local._reset_network_state()
                try:
                    tags, source, groups = local._atf_pixel_hash_locator_lookup(atf_side_site, local_md5)
                finally:
                    local._current_md5_lookup_path = old_lookup_path
            except Exception as e:
                self._emit_log(f"[ATF pixel_hash:{Path(path).name}] ERROR: {type(e).__name__}: {e}")
                if bool(getattr(local, "transient_network_failed", lambda: False)()):
                    return "defer"
                return "miss"
            if bool(getattr(local, "transient_network_failed", lambda: False)()):
                self._emit_log(f"[ATF pixel_hash:{Path(path).name}] NETWORK DEFER")
                return "defer"
            if not tags:
                self._emit_log(f"[ATF pixel_hash:{Path(path).name}] MISS")
                return "miss"
            site_md5 = ""
            try:
                for _x in list(getattr(local, "_last_atf_pixel_hash_site_md5s", []) or []):
                    _x = str(_x or "").strip().lower()
                    if is_md5(_x):
                        site_md5 = _x
                        break
            except Exception:
                site_md5 = ""
            checked_md5 = site_md5 or local_md5
            ok, err = _persist_site_match(
                Path(path), atf_side_shown, atf_side_site_key, atf_side_engine,
                checked_md5, list(tags or []), str(source or ""), groups or {}, "atf_pixel_hash_after_reverse",
            )
            if ok:
                self._emit_site_current("ATF pixel_hash", f"Найдено: {len(tags or [])} тегов", str(path))
                self._emit_log(f"[ATF pixel_hash:{Path(path).name}] MATCH md5={checked_md5 or '?'} tags={len(tags or [])} {source or ''}")
                return "tagged"
            self._emit_log(f"[ATF pixel_hash:{Path(path).name}] PERSIST ERROR: {err}")
            return "error"

        def final_nomatch_loop():
            def _final_log(message):
                self._emit_log(f"[FINAL-NOMATCH:{current_name[0]}] {str(message).strip()}")
            current_name = ["—"]
            local = Tagger(final_settings, _final_log)
            local.cancel_callback = self.interruption_requested
            while not self.interruption_requested() and not stop_event.is_set():
                if self._memory_guard_check("final-nomatch"):
                    stop_event.set()
                    break
                item = final_nomatch_q.get()
                try:
                    if item is None:
                        break
                    path = Path(item)
                    current_name[0] = path.name
                    if str(path) in found_paths:
                        continue
                    self._emit_site_current("Финальная сборка", "Reverse miss; проверка ATF pixel_hash", str(path))
                    atf_final = _try_final_atf_pixel_hash_after_reverse(path, local)
                    if atf_final == "tagged":
                        with state_lock:
                            found_paths.add(str(path))
                            prior_global_status[str(path)] = "found"
                        _stat_inc("tagged", 1)
                        continue
                    if atf_final == "defer":
                        _stat_inc("deferred_network", 1)
                        continue
                    self._emit_site_current("Финальная сборка", "No-match после reverse и ATF pixel_hash", str(path))
                    result = local.process_image(path, persist_lock=persist_lock)
                    if result == "nomatch":
                        _stat_inc("nomatch", 1)
                    elif result in ("tagged", "partial"):
                        with state_lock:
                            found_paths.add(str(path))
                            prior_global_status[str(path)] = "found"
                        _stat_inc("tagged", 1)
                except Exception as e:
                    if not self.interruption_requested():
                        self._emit_log(f"[FINAL-NOMATCH:{current_name[0]}] ERROR: {e}")
                        _stat_inc("errors", 1)
                finally:
                    current_name[0] = "—"
                    final_nomatch_q.task_done()

        threads = []
        for lane in site_lanes:
            lane_key, shown, site_key, engine, site = lane
            th = threading.Thread(target=site_cursor_loop, args=(lane_key, shown, site_key, engine, site), daemon=True, name=f"site-cursor-{shown}")
            th.start()
            threads.append(th)
        reverse_threads = []
        # One worker per reverse branch keeps IQDB/Danbooru IQDB/e621 IQDB/SauceNAO/TinEye independent.
        for branch_key, branch_label in reverse_branches:
            bq = reverse_queues.get(branch_key)
            if bq is None:
                continue
            th = threading.Thread(target=reverse_branch_loop, args=(branch_key, branch_label, bq, 1), daemon=True, name=f"reverse-branch-{branch_key}")
            th.start()
            reverse_threads.append((th, bq))
        if rule34_side_queue_enabled and rule34_side_site:
            th = threading.Thread(target=rule34_variant_loop, daemon=True, name="reverse-branch-rule34-variant")
            th.start()
            reverse_threads.append((th, rule34_variant_q))
        final_thread = threading.Thread(target=final_nomatch_loop, daemon=True, name="reverse-final-nomatch")
        final_thread.start()

        def _join_thread_until_dead(th, label, *, stopping=False):
            """Wait for helper worker shutdown before returning from the parser run.

            v406: the UI checkpoint/DONE is emitted from the Qt thread after
            TaggerWorker.done.  If a helper thread is still inside a long
            reverse lookup when this method returns, its queued log lines can
            appear after DONE and, worse, it may still touch SQLite/network after
            the shutdown checkpoint.  A short join(timeout=2) was not enough for
            SauceNAO/Pixiv/IQDB; wait cooperatively and keep the UI informed.
            """
            try:
                if th is None:
                    return True
                max_wait = 0.0
                try:
                    max_wait = float(self.settings.get("parser_helper_join_timeout_seconds", 180) or 180)
                except Exception:
                    max_wait = 180.0
                max_wait = max(10.0, min(1800.0, max_wait))
                started = time.time()
                last_log = 0.0
                while th.is_alive():
                    if stopping:
                        stop_event.set()
                    try:
                        th.join(timeout=0.5)
                    except RuntimeError:
                        break
                    if not th.is_alive():
                        break
                    now = time.time()
                    if now - last_log >= 10.0:
                        last_log = now
                        self._emit_log(f"WAIT WORKER EXIT [{label}]: still stopping/draining before DONE")
                    if now - started >= max_wait:
                        self._emit_log(
                            f"INTERNAL ERROR: helper worker still alive before DONE after {int(max_wait)}s [{label}]; "
                            "run will stay unclean"
                        )
                        _stat_inc("errors", 1)
                        return False
                return True
            except Exception as e:
                self._emit_log(f"INTERNAL ERROR: join helper failed [{label}]: {type(e).__name__}: {e}")
                _stat_inc("errors", 1)
                return False

        # v402/v409: resumed/already-journaled MD5-miss files must be fed into
        # live reverse queues after branch workers are online.  Do not enqueue
        # thousands of files at once: stage-1 IQDB/e621 queues can be slow and a
        # huge backlog retains per-file state, triggers RAM guard, and gives the
        # user another REVERSE=0/0-looking stop.  Keep a small active window and
        # top it up as reverse branches drain.
        try:
            reverse_admit_window_files = max(8, min(512, int(self.settings.get("tagger_reverse_admit_window_files", 64) or 64)))
        except Exception:
            reverse_admit_window_files = 64
        reverse_pending_initial = list(already_journaled_reverse_candidates)
        reverse_pending_index = 0
        initial_reverse_candidates = len(reverse_pending_initial)
        initial_reverse_queued = 0
        pre_reverse_stop = False

        def _active_reverse_file_count() -> int:
            try:
                with reverse_lock:
                    return sum(1 for _v in reverse_remaining.values() if int(_v or 0) > 0)
            except Exception:
                return 0

        def _queued_reverse_task_count() -> int:
            total = 0
            for _th, _q in reverse_threads:
                try:
                    total += int(_q.unfinished_tasks)
                except Exception:
                    pass
            try:
                total += int(final_nomatch_q.unfinished_tasks)
            except Exception:
                pass
            return total

        def _feed_initial_reverse_window(reason="already-all-sites-journaled") -> int:
            nonlocal reverse_pending_index, initial_reverse_queued
            queued_now = 0
            while reverse_pending_index < len(reverse_pending_initial):
                if self.interruption_requested() or stop_event.is_set():
                    break
                if _active_reverse_file_count() >= reverse_admit_window_files:
                    break
                path = reverse_pending_initial[reverse_pending_index]
                reverse_pending_index += 1
                if _queue_reverse(path, reason, log_admit=False):
                    initial_reverse_queued += 1
                    queued_now += 1
            if queued_now:
                _stat_inc("reverse_initial_queued", queued_now)
            return queued_now

        try:
            if initial_reverse_candidates:
                _stat_inc("reverse_initial_candidates", initial_reverse_candidates)
                queued_now = _feed_initial_reverse_window()
                queued_tasks_now = _queued_reverse_task_count()
                self._emit_log(
                    f"REVERSE-ADMIT WINDOW: candidates={initial_reverse_candidates} "
                    f"queued_now={queued_now} queued_total={initial_reverse_queued} "
                    f"pending={len(reverse_pending_initial) - reverse_pending_index} "
                    f"active_files={_active_reverse_file_count()} live_tasks={queued_tasks_now} "
                    f"window={reverse_admit_window_files} reason=already-all-sites-journaled"
                )
                if rule34_side_queue_enabled and rule34_side_site:
                    with reverse_lock:
                        _fp = dict(reverse_filename_prefilter)
                    self._emit_log(
                        "REVERSE FILENAME PREFILTER [rule34 40hex/SHA1]: "
                        f"key={int(_fp.get('key', 0) or 0)} "
                        f"generic_skip={int(_fp.get('generic', 0) or 0)} "
                        f"no_key_skip={int(_fp.get('no_key', 0) or 0)}"
                    )
                if initial_reverse_queued > 0 and queued_tasks_now <= 0:
                    self._emit_log(
                        "REVERSE QUEUE ERROR: admitted files but live reverse queues are empty; "
                        "parser will not mark this run as clean/DONE"
                    )
                    _stat_inc("errors", 1)
                    pre_reverse_stop = True
                    stop_event.set()
        except Exception as e:
            self._emit_log(f"REVERSE-ADMIT WINDOW ERROR: {type(e).__name__}: {e}")
            _stat_inc("errors", 1)
            pre_reverse_stop = True
            stop_event.set()


        stopped = bool(pre_reverse_stop)
        try:
            while any(th.is_alive() for th in threads):
                if self.interruption_requested() or self._memory_guard_check("site-join"):
                    stopped = True
                    stop_event.set()
                    break
                self._wait_if_paused_or_delay(0.25)
            for th in threads:
                if not _join_thread_until_dead(th, getattr(th, "name", "site-cursor"), stopping=bool(stopped)):
                    stopped = True
                    stop_event.set()
            if reverse_threads:
                # All direct MD5 cursors are done; each reverse branch drains its own queue.
                while not stopped and not self.interruption_requested():
                    if self._memory_guard_check("reverse-drain"):
                        stopped = True
                        stop_event.set()
                        break
                    if reverse_pending_index < len(reverse_pending_initial):
                        queued_more = _feed_initial_reverse_window()
                        if queued_more:
                            # Keep this log batched; do not print per-file REVERSE-ADMIT.
                            remaining_initial = len(reverse_pending_initial) - reverse_pending_index
                            if initial_reverse_queued <= queued_more or initial_reverse_queued % 500 < queued_more:
                                self._emit_log(
                                    f"REVERSE-ADMIT WINDOW: queued_total={initial_reverse_queued}/{initial_reverse_candidates} "
                                    f"pending={remaining_initial} active_files={_active_reverse_file_count()} "
                                    f"live_tasks={_queued_reverse_task_count()}"
                                )
                    unfinished = 0
                    alive_workers = 0
                    for _th, _q in reverse_threads:
                        try:
                            unfinished += int(_q.unfinished_tasks)
                        except Exception:
                            pass
                        try:
                            if _th.is_alive():
                                alive_workers += 1
                        except Exception:
                            pass
                    try:
                        unfinished += int(final_nomatch_q.unfinished_tasks)
                    except Exception:
                        pass
                    try:
                        if final_thread.is_alive():
                            alive_workers += 1
                    except Exception:
                        pass
                    if unfinished == 0 and reverse_pending_index >= len(reverse_pending_initial):
                        break
                    self._wait_if_paused_or_delay(0.25)
                if self.interruption_requested():
                    stopped = True
                    stop_event.set()
                # Stop idle branch workers only after all work/finalization queues
                # have drained.  Then wait until every helper really exits before
                # the outer run can emit done/checkpoint.
                for _th, _q in reverse_threads:
                    _q.put(None)
                final_nomatch_q.put(None)
                for th, _q in reverse_threads:
                    if not _join_thread_until_dead(th, getattr(th, "name", "reverse-branch"), stopping=bool(stopped)):
                        stopped = True
                        stop_event.set()
                if not _join_thread_until_dead(final_thread, getattr(final_thread, "name", "reverse-final-nomatch"), stopping=bool(stopped)):
                    stopped = True
                    stop_event.set()
            else:
                final_nomatch_q.put(None)
                if not _join_thread_until_dead(final_thread, getattr(final_thread, "name", "reverse-final-nomatch"), stopping=bool(stopped)):
                    stopped = True
                    stop_event.set()
        finally:
            if stopped or self.interruption_requested():
                self._emit_log("STOPPED")
            self._emit_progress(completed_site_units, max(1, total_site_units))
            # Drop large per-run state before the QThread finishes.  Without this,
            # Python can keep huge dict/list arenas in the process after STOP/DONE.
            try:
                hash_cache.clear(); hash_locks.clear()
                per_file_remaining.clear(); per_file_had_network_defer.clear()
                found_paths.clear(); reverse_scheduled.clear(); reverse_remaining.clear(); reverse_stage_by_file.clear(); reverse_manifest.clear()
                reverse_had_defer.clear(); reverse_defer_branch.clear(); reverse_retry_persisted.clear(); reverse_active.clear(); final_atf_pixel_done.clear()
                pending_by_site.clear(); site_done_map.clear(); existing_media_map.clear(); prior_global_status.clear()
            except Exception:
                pass
            try:
                if local_preflight_thread is not None and local_preflight_thread.is_alive():
                    stop_event.set()
                    local_preflight_thread.join(timeout=3.0)
                    if local_preflight_thread.is_alive():
                        self._emit_log("LOCAL PREFLIGHT: still stopping in background; log output suppressed by stop token")
            except Exception:
                pass
            try:
                soft_trim_memory(0.0)
            except Exception:
                pass
        return stats, bool(stopped or self.interruption_requested())

    def _run_site_conveyor(self, files, session_settings, writer, *, prior_global_status=None, existing_media_map=None, site_done_map=None, restored_saucenao=None):
        """Process missing file×site checks through one lane per enabled source.

        A global tagged/no_match record no longer means every future source was
        checked.  Each completed MD5 lane is journaled independently in SQLite;
        newly enabled sites therefore scan the existing archive once and merge
        any new metadata without forcing old sites or reverse search to rerun.
        """
        import queue
        import threading
        from core.services.scan_state_service import (mark_site_scanned, enqueue_reverse_retry, remove_reverse_retry, enqueue_tag_enrichment, seed_background_tag_enrichment, pending_tag_enrichments, complete_tag_enrichment, retry_tag_enrichment, record_task_event)

        sites = writer._all_enabled_site_configs()
        if not sites:
            self._emit_log("SITE CONVEYOR: no enabled MD5 sites; using ordinary fallback path")
            return None
        if bool(self.settings.get("tagger_true_per_site_cursors", True)) and not bool(self.settings.get("tagger_low_power_mode", False)):
            return self._run_site_conveyor_site_cursors(
                files, session_settings, writer,
                prior_global_status=prior_global_status,
                existing_media_map=existing_media_map,
                site_done_map=site_done_map,
                restored_saucenao=restored_saucenao,
            )

        prior_global_status = dict(prior_global_status or {})
        existing_media_map = dict(existing_media_map or {})
        site_done_map = dict(site_done_map or {})
        interval = max(1.10, float(self.settings.get("tagger_site_interval_seconds", 1.10) or 1.10))
        low_power = bool(self.settings.get("tagger_low_power_mode", False))
        async_conveyor_v2 = bool(self.settings.get("tagger_async_conveyor_v2", True))
        if low_power:
            window = 1
        elif async_conveyor_v2:
            # v371: keep the v369 parser as the base, but restore the intended
            # branch behaviour: a slow site (usually ATF/Danbooru/Cloudflare)
            # must not make fast MD5 lanes run out of files.  The old 100-state
            # UI setting was too small: fast danbooru/gelbooru/rule34/e621 lanes
            # drained their queues, then waited for the slowest branch.
            configured_state_limit = int(
                self.settings.get(
                    "tagger_async_conveyor_state_limit",
                    self.settings.get("tagger_conveyor_window", 4096),
                ) or 4096
            )
            min_state_limit = int(self.settings.get("tagger_md5_lane_min_state_limit", 4096) or 4096)
            window = max(512, min(20000, max(configured_state_limit, min_state_limit)))
        else:
            window = max(2, min(128, int(self.settings.get("tagger_conveyor_window", 32) or 32)))
        try:
            lane_backlog_limit = max(32, min(20000, int(self.settings.get("tagger_async_conveyor_lane_backlog", 2048) or 2048)))
        except Exception:
            lane_backlog_limit = 2048
        total = len(files)
        stats = {"tagged": 0, "nomatch": 0, "skipped": 0, "deferred_network": 0,
                 "deferred_saucenao": 0, "errors": 0, "site_checks": 0, "site_merged": 0,
                 "reverse_started": 0, "reverse_done": 0, "category_done": 0,
                 "tineye_tagged": 0, "tineye_source_only": 0}
        passive_checkpoint_every = max(0, int(self.settings.get("sqlite_passive_checkpoint_every", 500) or 0))
        last_passive_checkpoint_site_checks = 0
        event_q = queue.Queue()
        fallback_q = queue.Queue()
        category_q = queue.Queue()
        # v283: expensive rule34.xxx image-key/SHA1 locators run outside the
        # exact-MD5 site lane.  They may take 30+ seconds or hit long timeouts,
        # but they should not block the rule34 lane from checking the next MD5.
        rule34_variant_q = queue.Queue()
        active_rule34_variant_jobs = set()
        scheduled_rule34_variant_jobs = set()
        rule34_variant_matched_paths = set()
        persist_lock = threading.Lock()
        sentinel = object()
        states = {}
        category_enabled = bool(self.settings.get("tagger_background_tag_groups", self.settings.get("tagger_background_rule34_categories", True)))
        # v398: category recovery is source-scoped for every site that can
        # return classified tags, not just Gelbooru/rule34.  Danbooru, ATF and
        # e621 already expose clean groups through JSON, and old broken rows may
        # still be stuck entirely in general, so they need their own queues too.
        # This key deliberately invalidates older "done" rows from v8/v9.
        category_job_key = "site-categories::tag-groups-v10-per-site-current-run"
        category_capable_hosts = {
            "danbooru.donmai.us",
            "booru.allthefallen.moe",
            "gelbooru.com",
            "rule34.xxx",
            "e621.net",
            "e926.net",
            "xbooru.com",
            "hypnohub.net",
        }

        def _normalize_category_host(host):
            host = str(host or "").strip().lower().replace("www.", "")
            if host == "api.rule34.xxx":
                return "rule34.xxx"
            if host == "donmai.us":
                return "danbooru.donmai.us"
            if host == "allthefallen.moe":
                return "booru.allthefallen.moe"
            return host

        def _category_host_from_url(source_url):
            return _normalize_category_host(urlparse(str(source_url or "")).netloc)

        category_hosts = set(category_capable_hosts)
        category_scheduled = set()
        # v397/v398: category refinement must not be one serialized pipe.
        # Every category-capable site gets an independent low-priority queue and
        # worker, so rule34/Gelbooru/ATF/Danbooru/e621 cannot block each other.
        category_queues = {host: queue.Queue() for host in sorted(category_hosts)}

        def _category_queue_for_url(source_url):
            host = _category_host_from_url(source_url)
            return host, category_queues.get(host)

        def _put_category_job(job):
            host, q = _category_queue_for_url(str((job or {}).get("source_url") or ""))
            if q is None:
                return host, False
            q.put(job)
            return host, True

        def _pending_category_count():
            total_pending = 0
            for q in category_queues.values():
                try:
                    total_pending += int(getattr(q, "unfinished_tasks", 0) or 0)
                except Exception:
                    pass
            return total_pending
        deferred_sauce = {
            str(Path(path)): (Path(path), int(retry_at))
            for path, retry_at, _reason in (restored_saucenao or [])
        }
        active_fallback_tokens = set()
        sauce_wait_notice_for = 0
        next_token = 0
        submitted_count = 0
        last_feeder_notice = 0
        site_up_to_date_count = 0
        log_each_site_up_to_date = bool(self.settings.get("tagger_log_each_site_up_to_date", False))
        file_iter = iter(files)
        file_iter_exhausted = False

        site_lanes = []
        blueprint_site_runtime = dict(session_settings.get("_parser_blueprint_site_runtime") or {})
        blueprint_reverse_workers = max(1, min(16, int(session_settings.get("_parser_blueprint_reverse_workers", 1) or 1)))
        try:
            reverse_backlog_limit = max(
                64,
                min(20000, int(session_settings.get("tagger_reverse_side_queue_backlog", self.settings.get("tagger_reverse_side_queue_backlog", 4096)) or 4096)),
            )
        except Exception:
            reverse_backlog_limit = 4096
        # v369: direct MD5 is the first authoritative branch.  Reverse branches
        # (IQDB/SauceNAO/TinEye/etc.) must not start while the direct MD5 site
        # lanes for this file are still unresolved, otherwise the UI/log collapses
        # the pipeline back into one misleading REVERSE bucket and wastes SauceNAO
        # on files that may still be found by exact MD5.
        md5_first_before_reverse = bool(session_settings.get("tagger_md5_first_before_reverse", True))
        reverse_early_enabled = bool(session_settings.get("tagger_reverse_early_side_queue_enabled", True))
        if md5_first_before_reverse:
            reverse_early_enabled = False
        try:
            reverse_early_delay = max(0.0, min(600.0, float(session_settings.get("tagger_reverse_early_delay_seconds", 8.0) or 8.0)))
        except Exception:
            reverse_early_delay = 8.0
        try:
            reverse_early_min_misses_setting = max(1, min(32, int(session_settings.get("tagger_reverse_early_min_md5_misses", 2) or 2)))
        except Exception:
            reverse_early_min_misses_setting = 2
        lane_heartbeat = {}
        # v271: watchdog must distinguish idle site lanes from a real stuck HTTP task.
        lane_stage = {}
        lane_stall_warned = set()
        used_labels = {}
        for index, site in enumerate(sites):
            label = writer._site_label(site)
            used_labels[label] = used_labels.get(label, 0) + 1
            shown = label if used_labels[label] == 1 else f"{label} ({used_labels[label]})"
            site_key = _site_scan_key(writer, site)
            engine = str(site.get("engine") or site.get("type") or "")
            lane_key = f"site-{index}"
            domain_key = str(site.get("domain") or "").lower().replace("www.", "")
            rt = blueprint_site_runtime.get(domain_key, {}) if isinstance(blueprint_site_runtime, dict) else {}
            try:
                site["_blueprint_workers"] = max(1, min(32, int(rt.get("workers", 1) or 1)))
            except Exception:
                site["_blueprint_workers"] = 1
            try:
                site["_blueprint_min_delay_ms"] = max(0, int(rt.get("min_delay_ms", 0) or 0))
            except Exception:
                site["_blueprint_min_delay_ms"] = 0
            site["_blueprint_rate_group"] = str(rt.get("rate_group", "") or domain_key)
            site_lanes.append((lane_key, shown, site_key, engine, site, queue.Queue()))
            lane_heartbeat[lane_key] = time.time()
            lane_stage[lane_key] = "idle"

        rule34_side_queue_enabled = bool(self.settings.get("rule34_variant_locator_side_queue_enabled", True))
        rule34_side_site = None
        for _lk, _shown, _site_key, _engine, _site, _q in site_lanes:
            _host_key = str(_site_key or "").lower()
            if "rule34.xxx" in _host_key or "api.rule34.xxx" in _host_key:
                rule34_side_site = _site
                break

        enabled_names = ", ".join((shown + (f" x{int(_site.get('_blueprint_workers',1) or 1)}" if int(_site.get('_blueprint_workers',1) or 1) > 1 else "")) for _key, shown, _site_key, _engine, _site, _q in site_lanes)
        self._emit_log(
            f"SITE CONVEYOR ACTIVE: lanes={len(site_lanes)} minimum_interval={interval:.2f}s "
            f"window={window}; sites={enabled_names}"
        )
        if async_conveyor_v2 and not low_power:
            self._emit_log(
                f"PARSER ASYNC CONVEYOR V2: branch queues are independent; "
                f"state_limit={window} lane_backlog={lane_backlog_limit}; slow sites/reverse do not hold the feeder"
            )
        try:
            from core.local_parallel import snapshot as _local_thread_snapshot
            _lt = _local_thread_snapshot(session_settings)
            self._emit_log(
                "LOCAL THREADS: "
                f"total={_lt.get('total')} hash={_lt.get('hash')} image={_lt.get('image')} "
                f"video={_lt.get('video')} db_read={_lt.get('db_read')} background={_lt.get('background')}"
            )
        except Exception:
            pass
        try:
            from core.local_preflight import start_parser_local_preflight
            if str(self.settings.get("_tagger_resolved_performance_profile", "")) == "low_memory" and len(files) > int(self.settings.get("local_preflight_low_memory_skip_threshold", 5000) or 5000):
                self._emit_log(
                    f"LOCAL PREFLIGHT SKIP: low-memory profile files={len(files)}; "
                    "large-run warm-up disabled to avoid RAM spike"
                )
            else:
                start_parser_local_preflight(files, session_settings, log=self._emit_log, stop_check=self.interruption_requested)
        except Exception as _e:
            self._emit_log(f"LOCAL PREFLIGHT ERROR: {type(_e).__name__}: {_e}")
        try:
            if session_settings.get("parser_blueprint_enabled"):
                _warns = list(session_settings.get("_parser_blueprint_warnings") or [])
                if _warns:
                    self._emit_log("PARSER BLUEPRINT WARNINGS: " + str(len(_warns)) + " warning(s); full-access lets the user run anyway")
                    for _w in _warns[:8]:
                        self._emit_log("  BLUEPRINT WARN: " + str(_w))
        except Exception:
            pass
        self._emit_log("BRANCH MODEL ACTIVE: MD5 direct / IQDB / SauceNAO / TinEye / source→MD5 relay / rule34 40hex / merge are separate branches")
        self._emit_log("SITE CONVEYOR: per-site SQLite journal active; newly enabled sites scan old files without rerunning completed sources")
        if low_power:
            self._emit_log("LOW POWER MODE: per-site journal kept; window=1 and per-site previews hidden")
        self._emit_log("SITE CONVEYOR: existing per-site safety budgets are preserved; restricted sites may wait longer than the minimum interval")
        if rule34_side_queue_enabled and rule34_side_site:
            self._emit_log("rule34.xxx VARIANT LOCATORS: side queue active; image-key/SHA1 misses will not block exact-MD5 lane")
        reverse_chain = []
        if self.settings.get("enable_iqdb"):
            reverse_chain.append("IQDB")
        if self.settings.get("enable_danbooru_iqdb"):
            reverse_chain.append("Danbooru IQDB")
        if self.settings.get("enable_e621_iqdb"):
            reverse_chain.append("e621 IQDB")
        if self.settings.get("enable_ascii2d"):
            reverse_chain.append("Ascii2D")
        if self.settings.get("enable_saucenao") and self.settings.get("saucenao_api_key"):
            reverse_chain.append("SauceNAO")
        if self.settings.get("enable_tineye"):
            reverse_chain.append("TinEye")
        if reverse_chain:
            self._emit_log("REVERSE CHAIN: " + " -> ".join(reverse_chain))
            self._emit_log("SITE CONVEYOR: TinEye runs after SauceNAO when both are enabled; active reverse chain is logged above")
            if md5_first_before_reverse:
                self._emit_log("BRANCH MODEL: MD5 direct is first; reverse branches start only after enabled MD5 sites all miss")
                self._emit_log(
                    f"REVERSE BACKLOG: active limit={reverse_backlog_limit}; only files with completed MD5-miss state enter reverse_pending"
                )
            elif reverse_early_enabled and async_conveyor_v2 and not low_power:
                self._emit_log(
                    f"REVERSE ASYNC SIDE QUEUE: may start after {reverse_early_min_misses_setting}+ MD5 misses "
                    f"and {reverse_early_delay:.0f}s without a match; exact-site lanes keep running and later merge"
                )
                self._emit_log(
                    f"REVERSE BACKLOG: active limit={reverse_backlog_limit}; feeder continues and stores overflow as reverse_pending"
                )
            else:
                self._emit_log("SITE CONVEYOR: reverse fallbacks run only after all enabled MD5 site checks miss")
        else:
            self._emit_log("REVERSE CHAIN: disabled")
        if deferred_sauce:
            next_retry = min(value[1] for value in deferred_sauce.values())
            left = max(0, next_retry - int(time.time()))
            self._emit_log(
                f"SAUCENAO RETRY RESTORED: pending={len(deferred_sauce)}; "
                f"next retry in {left//60}m {left%60}s; IQDB/Ascii2D will not replay"
            )
        # v369 branch activity panel: every branch has its own row.  MD5 site
        # rows are ONLY direct exact-MD5 site lanes.  Reverse/search branches,
        # source→MD5 relay, rule34 40hex/SHA1 locator and final merge are separate
        # rows so the UI no longer pretends that everything is one REVERSE task.
        for _lk, _shown, _site_key, _engine, _site, _q in site_lanes:
            self._emit_site_current(_shown, "MD5 direct: ждёт файл", "")
        if self.settings.get("enable_iqdb"):
            self._emit_site_current("IQDB", "Reverse: ждёт MD5-miss", "")
        if self.settings.get("enable_danbooru_iqdb"):
            self._emit_site_current("Danbooru IQDB", "Reverse: ждёт MD5-miss", "")
        if self.settings.get("enable_e621_iqdb"):
            self._emit_site_current("e621 IQDB", "Reverse: ждёт MD5-miss", "")
        if self.settings.get("enable_ascii2d"):
            self._emit_site_current("Ascii2D", "Reverse: ждёт MD5-miss", "")
        if self.settings.get("enable_saucenao") and self.settings.get("saucenao_api_key"):
            self._emit_site_current("SauceNAO", "Reverse: ждёт MD5-miss", "")
        if self.settings.get("enable_tineye"):
            self._emit_site_current("TinEye", "Reverse: ждёт MD5-miss", "")
        self._emit_site_current("source→MD5 relay", "Ждёт URL-кандидат", "")
        if rule34_side_queue_enabled and rule34_side_site:
            self._emit_site_current("rule34 40hex/SHA1", "Ждёт image-key/SHA1", "")
        self._emit_site_current("Финальная сборка", "Ждёт результаты веток", "")
        if category_enabled:
            try:
                # v395: do NOT flood the live parser with the whole historical
                # category-backfill backlog.  That made fresh Gelbooru/rule34
                # matches sit behind thousands of old jobs, so the UI looked as
                # if category recovery never ran.  The live parser now gives
                # priority to category jobs produced by the current run only.
                # Old general-only records remain durable in tag_enrichment_queue
                # and can be repaired by a separate maintenance action/tool.
                if bool(session_settings.get("tagger_category_startup_seed_backlog", False)):
                    seeded = seed_background_tag_enrichment(session_settings, job_key=category_job_key, hosts=tuple(sorted(category_hosts)))
                    jobs = pending_tag_enrichments(session_settings, job_key=category_job_key, limit=int(session_settings.get("tagger_category_startup_seed_limit", 500) or 500))
                    for job in jobs:
                        job_id = (str(job.get("original_path", "")), str(job.get("source_url", "")))
                        if job_id not in category_scheduled:
                            category_scheduled.add(job_id)
                            _put_category_job(job)
                    self._emit_site_current("Категории тегов", f"Фон: старая очередь {len(jobs)}", "")
                    if seeded or jobs:
                        self._emit_log(f"TAG CATEGORY BACKGROUND: legacy backlog loaded queued={len(jobs)} backfilled={seeded}; current-run jobs still have priority only when backlog loading is disabled")
                else:
                    self._emit_site_current("Категории тегов", "Фон: ждёт новые совпадения", "")
                    self._emit_log("TAG CATEGORY BACKGROUND: current-run priority; old backlog is not loaded into live parser queue; per-site queues for every category-capable source enabled")
            except Exception as e:
                self._emit_log(f"TAG CATEGORY BACKGROUND WARNING: cannot initialize queue: {e}")

        def lane_settings(site):
            cfg = dict(session_settings)
            host = str(site.get("domain") or urlparse(writer._site_root_from_cfg(site)).netloc).lower().replace("www.", "")
            by_host = dict(cfg.get("http_min_interval_by_host") or {})
            try:
                block_delay = float(site.get("_blueprint_min_delay_ms", 0) or 0) / 1000.0
            except Exception:
                block_delay = 0.0
            by_host[host] = max(interval, block_delay)
            cfg["http_min_interval_by_host"] = by_host
            if rule34_side_queue_enabled and ("rule34.xxx" in host or "api.rule34.xxx" in host):
                cfg["_rule34_variant_locators_run_in_side_queue"] = True
            # Abort throttling immediately on STOP; otherwise a lane waiting for
            # its next permitted request looks as if the button did nothing.
            cfg["_cancel_callback"] = self.interruption_requested
            cfg["atf_pixel_hash_after_exact_md5_miss"] = False
            cfg["_allow_atf_pixel_hash_inline"] = False
            return cfg

        def live_log(message):
            if not self.interruption_requested():
                self._emit_log(str(message))

        def site_loop(lane_key, shown, site_key, engine, site, work_q):
            _context = {"file": "—"}
            def _lane_log(message):
                live_log(f"[MD5:{shown}:{_context['file']}] {str(message).strip()}")
            local = Tagger(lane_settings(site), _lane_log)
            local.cancel_callback = self.interruption_requested
            while not self.interruption_requested():
                try:
                    item = work_q.get(timeout=0.25)
                except queue.Empty:
                    lane_stage[lane_key] = "idle"
                    lane_heartbeat[lane_key] = time.time()
                    lane_stall_warned.discard(lane_key)
                    continue
                if item is sentinel:
                    break
                lane_heartbeat[lane_key] = time.time()
                lane_stage[lane_key] = "busy:get_task"
                lane_stall_warned.discard(lane_key)
                token, phase, md5, path = item
                _context["file"] = Path(path).name
                self._wait_if_paused_or_delay(0)
                if self.interruption_requested():
                    break
                self._emit_site_current(shown, "MD5 direct", str(path))
                self._emit_log(f"[MD5:{shown}:{Path(path).name}] CHECK")
                lane_stage[lane_key] = f"busy:{phase}:{Path(path).name}"
                local._reset_network_state()
                tags = []
                source = ""
                groups = {}
                error_text = ""
                lookup_status = ""
                extra_site_md5s = []
                match_method = "md5"
                try:
                    old_lookup_path = getattr(local, "_current_md5_lookup_path", "")
                    local._current_md5_lookup_path = str(path)
                    try:
                        tags, source, groups = local.engine_by_md5(site, md5)
                    finally:
                        local._current_md5_lookup_path = old_lookup_path
                    if self.interruption_requested():
                        break
                    lookup_status = str(getattr(local, "_last_lookup_status", "") or "")
                    match_method = str(getattr(local, "_last_lookup_match_method", "md5") or "md5")
                    try:
                        extra_site_md5s = []
                        for _attr in ("_last_variant_site_md5s", "_last_rule34_image_key_site_md5s", "_last_atf_pixel_hash_site_md5s"):
                            for _x in (getattr(local, _attr, []) or []):
                                _x = str(_x or "").strip().lower()
                                if _x and _x not in extra_site_md5s:
                                    extra_site_md5s.append(_x)
                    except Exception:
                        extra_site_md5s = []
                    if tags:
                        if match_method == "md5":
                            self._emit_log(f"[MD5:{shown}:{Path(path).name}] MATCH {source}")
                        else:
                            self._emit_log(f"[MD5:{shown}:{Path(path).name}] VARIANT MATCH {match_method} {source}")
                except InterruptedError:
                    if self.interruption_requested():
                        break
                    error_text = "request cancelled"
                except Exception as e:
                    if self.interruption_requested():
                        break
                    error_text = str(e)
                    self._emit_log(f"[MD5:{shown}:{Path(path).name}] ERROR: {e}")
                if self.interruption_requested():
                    break
                network_failed = local.transient_network_failed() or _looks_like_network_exception(error_text)
                if tags:
                    self._emit_site_current(shown, f"Найдено: {len(tags)} тегов", str(path))
                elif lookup_status == "auth_required":
                    self._emit_site_current(shown, "Нужен API ключ", str(path))
                elif network_failed:
                    self._emit_site_current(shown, "Сеть / повтор позже", str(path))
                elif error_text:
                    self._emit_site_current(shown, "Ошибка", str(path))
                else:
                    self._emit_site_current(shown, "Нет exact MD5", str(path))
                lane_heartbeat[lane_key] = time.time()
                lane_stage[lane_key] = "idle"
                event_q.put(("primary", token, lane_key, site_key, engine, phase, md5, tags, source, groups, network_failed, local.network_failure_summary(), lookup_status, extra_site_md5s, match_method))

        fallback_settings = dict(session_settings)
        fallback_settings["enable_md5_lookup"] = False
        # Reverse fallback intentionally disables the ordinary per-file MD5 pass
        # so that a file does not re-run the same site conveyor twice.  However,
        # when IQDB/Danbooru IQDB/SauceNAO discovers a NEW authoritative MD5 from
        # a supported post URL, that relay MD5 must still be allowed to go back
        # through the normal exact-MD5 site checks.
        fallback_settings["_allow_reverse_md5_relay_lookup"] = True
        fallback_settings["_cancel_callback"] = self.interruption_requested

        def fallback_loop():
            _context = {"file": "—", "retry_only": False}
            def _fallback_log(message):
                lane = "SAUCENAO-RETRY" if _context["retry_only"] else "REVERSE"
                live_log(f"[{lane}:{_context['file']}] {str(message).strip()}")
            local = Tagger(fallback_settings, _fallback_log)
            local.cancel_callback = self.interruption_requested
            local.activity_callback = lambda name, path, status: self._emit_site_current(name, status, path) if not self.interruption_requested() else None
            while not self.interruption_requested():
                try:
                    item = fallback_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is sentinel:
                    break
                if isinstance(item, tuple) and len(item) >= 4:
                    token, path, sauce_retry_only, detached_reverse = item[:4]
                else:
                    token, path, sauce_retry_only = item
                    detached_reverse = False
                _context["file"] = Path(path).name
                _context["retry_only"] = bool(sauce_retry_only)
                self._wait_if_paused_or_delay(0)
                if self.interruption_requested():
                    break
                if detached_reverse:
                    try:
                        st = states.get(token)
                        if st is not None and st.get("persisted_found"):
                            event_q.put(("fallback_detached", token, "skip_found", "", 0, 0))
                            continue
                    except Exception:
                        pass
                self._emit_site_current("SauceNAO" if sauce_retry_only else ("Reverse side queue" if detached_reverse else "Reverse side queue"), "Запущена ветка reverse", str(path))
                previous_retry_only = local.settings.get("_saucenao_retry_only", False)
                local.settings["_saucenao_retry_only"] = bool(sauce_retry_only)
                try:
                    # Keep reverse-search network waits outside the serialized
                    # persistence section. The Tagger acquires this lock only when
                    # it is ready to write a final result.
                    _tineye_tagged_before = int(getattr(local, "_tineye_tagged_total", 0) or 0)
                    _tineye_source_before = int(getattr(local, "_tineye_source_only_total", 0) or 0)
                    result = local.process_image(path, persist_lock=persist_lock)
                    _tineye_tagged_delta = max(0, int(getattr(local, "_tineye_tagged_total", 0) or 0) - _tineye_tagged_before)
                    _tineye_source_delta = max(0, int(getattr(local, "_tineye_source_only_total", 0) or 0) - _tineye_source_before)
                    if self.interruption_requested():
                        break
                    # Reverse-search result pages can also come from flat-tag
                    # sources. Store the found tags immediately, then classify
                    # those source tags in the same durable background queue.
                    if result == "tagged" and category_enabled:
                        for source_url in local.take_background_group_urls():
                            source_host = _category_host_from_url(source_url)
                            if source_host not in category_hosts:
                                continue
                            media_path = str(result_paths_for(session_settings, path, "tagged")["media_file"])
                            job_id = (str(path), source_url)
                            with persist_lock:
                                enqueue_tag_enrichment(session_settings, path, media_path, source_url, job_key=category_job_key)
                            if job_id not in category_scheduled:
                                category_scheduled.add(job_id)
                                _queued_host, _queued_ok = _put_category_job({"original_path": str(path), "media_path": media_path, "source_url": source_url, "job_key": category_job_key})
                                if _queued_ok:
                                    self._emit_log(f"  TAG CATEGORY BACKGROUND QUEUED [{_queued_host} fallback]: {path.name}")
                                else:
                                    category_scheduled.discard(job_id)
                    retry_after = str(local.saucenao_retry_after_epoch()) if result == "retry_saucenao" else ""
                    _event_name = "fallback_detached" if detached_reverse else "fallback"
                    event_q.put((_event_name, token, result, retry_after, _tineye_tagged_delta, _tineye_source_delta))
                except InterruptedError:
                    if self.interruption_requested():
                        break
                    _event_name = "fallback_detached" if detached_reverse else "fallback"
                    event_q.put((_event_name, token, "error", "request cancelled", 0, 0))
                except Exception as e:
                    if self.interruption_requested():
                        break
                    _event_name = "fallback_detached" if detached_reverse else "fallback"
                    event_q.put((_event_name, token, "error", str(e), 0, 0))
                finally:
                    local.settings["_saucenao_retry_only"] = previous_retry_only
                    _context["file"] = "—"
                    _context["retry_only"] = False

        def rule34_variant_loop():
            if not (rule34_side_queue_enabled and rule34_side_site):
                return
            variant_settings = dict(session_settings)
            variant_settings["_cancel_callback"] = self.interruption_requested
            # These settings remain enabled here; only foreground MD5 lanes skip them.
            variant_settings["_rule34_variant_locators_run_in_side_queue"] = False
            by_host = dict(variant_settings.get("http_min_interval_by_host") or {})
            by_host["rule34.xxx"] = max(interval, float(by_host.get("rule34.xxx", 0) or 0))
            by_host["hl.rule34.xxx"] = max(interval, float(by_host.get("hl.rule34.xxx", 0) or 0))
            variant_settings["http_min_interval_by_host"] = by_host

            def _variant_log(message):
                live_log(f"[R34-VARIANT:{_context['file']}] {str(message).strip()}")

            _context = {"file": "—"}
            local = Tagger(variant_settings, _variant_log)
            local.cancel_callback = self.interruption_requested
            while not self.interruption_requested():
                try:
                    item = rule34_variant_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is sentinel:
                    break
                job_id, path, md5 = item
                path = Path(path)
                _context["file"] = path.name
                self._wait_if_paused_or_delay(0)
                if self.interruption_requested():
                    break
                self._emit_site_current("rule34 40hex/SHA1", "Ищет image-key/SHA1", str(path))
                tags = []
                source = ""
                groups = {}
                err = ""
                try:
                    old_lookup_path = getattr(local, "_current_md5_lookup_path", "")
                    local._current_md5_lookup_path = str(path)
                    local._reset_network_state()
                    try:
                        # Run the slow opportunistic branch outside the rule34 exact-MD5 lane.
                        tags, source, groups = local._rule34xxx_image_key_locator_lookup(rule34_side_site, md5)
                        if not tags and bool(variant_settings.get("rule34_sha1_async_locator_enabled", True)):
                            tags, source, groups = local._rule34xxx_sha1_async_locator_lookup(rule34_side_site, md5)
                    finally:
                        local._current_md5_lookup_path = old_lookup_path
                    if tags:
                        def _merge_unique_items(*seqs):
                            merged = []
                            seen = set()
                            for seq in seqs:
                                for item in list(seq or []):
                                    key = str(item)
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    merged.append(item)
                            return merged

                        all_tags = list(tags or [])
                        all_sources = [f"rule34_variant_side_queue rule34.xxx {source}"] if source else []
                        all_groups = [groups] if groups else []
                        per_source = [{"url": source, "groups": groups or {"general": list(tags)}, "method": "rule34_variant_side_queue"}] if source else []

                        # Persist the verified rule34 hit immediately.  The MD5
                        # relay can take time; reverse fallback must already see
                        # this file as found and must not race it back into NO_MATCH.
                        with persist_lock:
                            media_path = ""
                            status = ""
                            try:
                                from core.services.scan_state_service import processed_records_many
                                row = processed_records_many(session_settings, [path]).get(str(path), {})
                                media_path = str(row.get("media_path") or "")
                                status = str(row.get("status") or "")
                            except Exception:
                                pass
                            if status in ("found", "tagged") and media_path and Path(media_path).exists():
                                outcome = writer.merge_conveyor_match_into_existing(
                                    media_path, path, all_tags, all_sources, all_groups, per_source,
                                )
                            else:
                                outcome = writer.save_conveyor_match(
                                    path, all_tags, all_sources, all_groups, per_source,
                                )
                        if outcome == "tagged":
                            self._emit_log(f"[R34-VARIANT:{path.name}] MATCH {source} tags={len(all_tags)} sources={len(all_sources)}")
                        else:
                            err = f"persist outcome={outcome}"
                            raise RuntimeError(err)

                        # Full-auto MD5 relay: image-key/hotlink gives us the
                        # authoritative rule34.xxx post MD5 (original/site MD5).
                        # Immediately run that MD5 through every enabled MD5 site
                        # so Gelbooru/Danbooru/e621/ATF tags are merged before
                        # reverse fallbacks can decide NO_MATCH.
                        site_md5s = []
                        try:
                            for _m in list(getattr(local, "_last_rule34_image_key_site_md5s", []) or []):
                                _m = str(_m or "").strip().lower()
                                if is_md5(_m) and _m != str(md5 or "").strip().lower() and _m not in site_md5s:
                                    site_md5s.append(_m)
                        except Exception:
                            site_md5s = []
                        for site_md5 in site_md5s:
                            if self.interruption_requested():
                                break
                            self._emit_log(f"[R34-VARIANT:{path.name}] TRY RULE34 IMAGE-KEY SITE MD5 RELAY: {site_md5}")
                            old_lookup_path = getattr(local, "_current_md5_lookup_path", "")
                            local._current_md5_lookup_path = str(path)
                            try:
                                relay_tags, relay_sources, relay_groups = local.md5_lookup_all(site_md5)
                                relay_per_source = list(getattr(local, "_last_md5_source_tag_groups", []) or [])
                            finally:
                                local._current_md5_lookup_path = old_lookup_path
                            if relay_tags:
                                before = len(set(map(str, all_tags)))
                                all_tags = _merge_unique_items(all_tags, relay_tags)
                                added = max(0, len(set(map(str, all_tags))) - before)
                                all_sources.extend([f"rule34_variant_md5_relay {site_md5} {src}" for src in list(relay_sources or [])])
                                all_groups.extend([g for g in list(relay_groups or []) if g])
                                per_source.extend(relay_per_source)
                                self._emit_log(
                                    f"[R34-VARIANT:{path.name}] RULE34 IMAGE-KEY SITE MD5 RELAY TAGS: "
                                    f"md5={site_md5} tags={len(relay_tags or [])} sources={len(relay_sources or [])} added_unique={added}"
                                )
                            else:
                                self._emit_log(f"[R34-VARIANT:{path.name}] RULE34 IMAGE-KEY SITE MD5 RELAY MISS: md5={site_md5}")

                        tags = _merge_unique_items(all_tags)
                        with persist_lock:
                            media_path = ""
                            status = ""
                            try:
                                from core.services.scan_state_service import processed_records_many
                                row = processed_records_many(session_settings, [path]).get(str(path), {})
                                media_path = str(row.get("media_path") or "")
                                status = str(row.get("status") or "")
                            except Exception:
                                pass
                            if status in ("found", "tagged") and media_path and Path(media_path).exists():
                                outcome = writer.merge_conveyor_match_into_existing(
                                    media_path, path, tags,
                                    all_sources,
                                    all_groups,
                                    per_source,
                                )
                            else:
                                outcome = writer.save_conveyor_match(
                                    path, tags,
                                    all_sources,
                                    all_groups,
                                    per_source,
                                )
                        if outcome == "tagged":
                            self._emit_log(f"[R34-VARIANT:{path.name}] MATCH {source} tags={len(tags)} sources={len(all_sources)}")
                        else:
                            err = f"persist outcome={outcome}"
                    else:
                        self._emit_log(f"[R34-VARIANT:{path.name}] no DAPI-verified post")
                except InterruptedError:
                    if self.interruption_requested():
                        break
                    err = "request cancelled"
                except Exception as e:
                    if self.interruption_requested():
                        break
                    err = str(e)
                finally:
                    _context["file"] = "—"
                event_q.put(("rule34_variant", job_id, bool(tags), err, str(source or ""), len(tags or [])))

        def category_loop(category_worker_host, category_q):
            if not category_enabled:
                return
            category_worker_host = str(category_worker_host or "").lower()
            category_settings = dict(session_settings)
            by_host = dict(category_settings.get("http_min_interval_by_host") or {})
            # Category lookup is deliberately low-priority and independent from
            # exact-MD5 lanes. Flat-tag sources collect first and are classified later.
            # v397: only throttle this worker's own host; each site has its own
            # queue/cooldown so rule34 429 cannot block Gelbooru category cleanup.
            if category_worker_host == "rule34.xxx":
                by_host[category_worker_host] = max(8.0, interval)
            else:
                by_host[category_worker_host] = max(3.0, interval)
            category_settings["http_min_interval_by_host"] = by_host
            category_settings["_cancel_callback"] = self.interruption_requested
            category_settings["_background_category_worker"] = True
            category_settings["tagger_background_category_dapi_fallback"] = True
            local = Tagger(category_settings, live_log)
            local.cancel_callback = self.interruption_requested
            category_cooldown_until = {}
            category_cooldown_notice = {}
            try:
                category_429_cooldown = max(60, min(3600, int(self.settings.get("tagger_category_overlay_429_cooldown_seconds", 900) or 900)))
            except Exception:
                category_429_cooldown = 900
            while not self.interruption_requested():
                try:
                    job = category_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if job is sentinel:
                    try:
                        category_q.task_done()
                    except Exception:
                        pass
                    break
                original = Path(str(job.get("original_path") or ""))
                media_text = str(job.get("media_path") or "")
                media = Path(media_text) if media_text else Path("__missing_media__")
                source_url = str(job.get("source_url") or "")
                source_host = _category_host_from_url(source_url)
                job_id = (str(original), source_url)
                if source_host != category_worker_host:
                    # Defensive: a job got routed to the wrong queue. Put it back
                    # into the correct site queue rather than processing it here.
                    _routed_host, _routed_ok = _put_category_job(job)
                    if not _routed_ok:
                        category_scheduled.discard(job_id)
                    try:
                        category_q.task_done()
                    except Exception:
                        pass
                    continue
                if not source_url:
                    try:
                        category_q.task_done()
                    except Exception:
                        pass
                    continue
                if not media.exists() or not media.is_file():
                    try:
                        from core.services.scan_state_service import processed_records_many
                        row = processed_records_many(session_settings, [original]).get(str(original), {})
                        media_text = str(row.get("media_path") or "")
                        media = Path(media_text) if media_text else Path("__missing_media__")
                    except Exception:
                        pass
                if not media.exists() or not media.is_file():
                    with persist_lock:
                        complete_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, status="stale", error="archived media missing")
                    category_scheduled.discard(job_id)
                    try:
                        category_q.task_done()
                    except Exception:
                        pass
                    continue
                shown_host = source_host or "источник"
                now_cool = time.time()
                cool_until = float(category_cooldown_until.get(source_host, 0.0) or 0.0)
                if cool_until > now_cool:
                    delay = max(30, int(cool_until - now_cool))
                    with persist_lock:
                        retry_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, delay_seconds=delay, error=f"{shown_host} category overlay cooldown")
                    # Keep the DB queue durable; do not keep thousands of cooldown jobs
                    # resident in RAM. They will be reseeded/retried later.
                    category_scheduled.discard(job_id)
                    try:
                        category_q.task_done()
                    except Exception:
                        pass
                    continue
                self._emit_site_current(f"{shown_host} категории", "Фоновая раскладка", str(original))
                try:
                    groups = local.grouped_tags_from_url(source_url)
                    if self.interruption_requested():
                        break
                    classified = sum(len(groups.get(k, []) or []) for k in ("artist", "character", "copyright", "species", "meta"))
                    if classified:
                        from core.database.storage import refine_source_tag_categories
                        with persist_lock:
                            refined = refine_source_tag_categories(
                                session_settings, media, source_url, groups,
                                method="per_site_category_refine_v10",
                            )
                            complete_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, status="done")
                        updated = int((refined or {}).get("updated", 0) or 0)
                        ignored = int((refined or {}).get("ignored", 0) or 0)
                        stats["category_done"] = int(stats.get("category_done", 0) or 0) + 1
                        self._emit_log(f"  TAG CATEGORY BACKGROUND DONE [{shown_host}]: {original.name} classified={updated} ignored_new={ignored}")
                        self._emit_site_current(f"{shown_host} категории", f"Разложено: {updated}; отброшено: {ignored}", str(original))
                    else:
                        with persist_lock:
                            complete_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, status="done", error="source provides no classified tags")
                        self._emit_log(f"  TAG CATEGORY BACKGROUND SKIP [{shown_host}]: {original.name} no classified tags")
                except InterruptedError:
                    break
                except Exception as e:
                    if not self.interruption_requested():
                        err_text = str(e)
                        err_lower = err_text.lower()
                        if "429" in err_lower or "too many requests" in err_lower:
                            delay = category_429_cooldown
                            category_cooldown_until[source_host] = time.time() + delay
                            last_notice = float(category_cooldown_notice.get(source_host, 0.0) or 0.0)
                            if time.time() - last_notice > 30.0:
                                category_cooldown_notice[source_host] = time.time()
                                self._emit_log(f"  CATEGORY OVERLAY COOLDOWN [{shown_host}]: HTTP 429; pause {delay//60}m {delay%60}s; DAPI tags already saved")
                        else:
                            delay = 300
                            self._emit_log(f"  TAG CATEGORY BACKGROUND RETRY [{shown_host}]: {original.name}: {e}")
                        with persist_lock:
                            retry_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, delay_seconds=delay, error=err_text)
                finally:
                    category_scheduled.discard(job_id)
                    try:
                        category_q.task_done()
                    except Exception:
                        pass

        def _guarded_thread(label, fn, *args):
            try:
                fn(*args)
            except RuntimeError as e:
                # Most commonly: the Qt worker object has already been deleted
                # during application shutdown.  Stop all lanes and suppress the
                # traceback so Python does not crash while finalizing CONOUT$.
                try:
                    self._hard_stop_event.set()
                except Exception:
                    pass
            except BaseException as e:
                try:
                    self._hard_stop_event.set()
                except Exception:
                    pass
                if not self.interruption_requested():
                    self._emit_log(f"WORKER THREAD ERROR [{label}]: {e}")

        threads = []
        for lane_key, shown, site_key, engine, site, work_q in site_lanes:
            try:
                _workers = max(1, min(32, int(site.get("_blueprint_workers", 1) or 1)))
            except Exception:
                _workers = 1
            for worker_index in range(_workers):
                worker_label = shown if _workers == 1 else f"{shown}#{worker_index + 1}"
                th = threading.Thread(target=_guarded_thread, args=(worker_label, site_loop, lane_key, shown, site_key, engine, site, work_q), daemon=True, name=f"site-conveyor-{lane_key}-{worker_index + 1}")
                th.start()
                threads.append(th)
        for worker_index in range(blueprint_reverse_workers):
            worker_label = "fallback" if blueprint_reverse_workers == 1 else f"fallback#{worker_index + 1}"
            reverse_thread = threading.Thread(target=_guarded_thread, args=(worker_label, fallback_loop), daemon=True, name=f"site-conveyor-fallback-{worker_index + 1}")
            reverse_thread.start()
            threads.append(reverse_thread)
        if blueprint_reverse_workers > 1:
            self._emit_log(f"PARSER BLUEPRINT: reverse fallback workers={blueprint_reverse_workers}")
        self._emit_log(f"PARSER BLUEPRINT: reverse side queue async; backlog_limit={reverse_backlog_limit}; MD5 conveyor will continue while reverse works")
        if rule34_side_queue_enabled and rule34_side_site:
            rule34_variant_thread = threading.Thread(target=_guarded_thread, args=("rule34-variant", rule34_variant_loop), daemon=True, name="site-conveyor-rule34-variant")
            rule34_variant_thread.start()
            threads.append(rule34_variant_thread)
        if category_enabled:
            self._emit_log(f"TAG CATEGORY BACKGROUND PER-SITE QUEUES ACTIVE: sites={len(category_queues)} ({', '.join(sorted(category_queues.keys()))})")
            for category_host, category_queue in sorted(category_queues.items()):
                category_thread = threading.Thread(
                    target=_guarded_thread,
                    args=(f"tag-categories-{category_host}", category_loop, category_host, category_queue),
                    daemon=True,
                    name=f"site-conveyor-tag-categories-{category_host}",
                )
                category_thread.start()
                threads.append(category_thread)

        lane_queue = {lane_key: work_q for lane_key, _shown, _key, _engine, _site, work_q in site_lanes}
        lane_name = {lane_key: shown for lane_key, shown, _key, _engine, _site, _q in site_lanes}
        lane_site_key = {lane_key: key for lane_key, _shown, key, _engine, _site, _q in site_lanes}
        lane_engine = {lane_key: engine for lane_key, _shown, _key, engine, _site, _q in site_lanes}

        def _is_rule34_lane(lane_key):
            key = str(lane_site_key.get(lane_key, "") or "").lower()
            name = str(lane_name.get(lane_key, "") or "").lower()
            return "rule34.xxx" in key or "api.rule34.xxx" in key or "rule34.xxx" in name

        def _queue_rule34_variant_locator(state, lane_key, checked_md5, lookup_status, network_failed, tags):
            if not (rule34_side_queue_enabled and rule34_side_site):
                return
            if not _is_rule34_lane(lane_key):
                return
            if tags or network_failed or lookup_status == "auth_required":
                return
            md5v = str(checked_md5 or "").strip().lower()
            if not md5v or len(md5v) != 32 or not all(c in "0123456789abcdef" for c in md5v):
                return
            # Only schedule when the file/path contains a plausible 40hex rule34
            # image key.  Pure 32hex MD5 filenames have nothing for this locator.
            try:
                if not extract_rule34_40hex_key(state.get("path")):
                    return
            except Exception:
                return
            job_id = (str(state["path"]), md5v)
            if job_id in scheduled_rule34_variant_jobs:
                return
            scheduled_rule34_variant_jobs.add(job_id)
            active_rule34_variant_jobs.add(job_id)
            rule34_variant_q.put((job_id, state["path"], md5v))
            self._emit_site_current("rule34 40hex/SHA1", "В очереди", str(state["path"]))
            self._emit_log(f"[R34-VARIANT:{state['path'].name}] QUEUED outside exact-MD5 lane md5={md5v}")

        def _state_already_promoted_by_side_queue(state):
            path_key = str(state.get("path") or "")
            if not path_key:
                return False
            if path_key in rule34_variant_matched_paths:
                return True
            try:
                from core.services.scan_state_service import processed_records_many
                row = processed_records_many(session_settings, [Path(path_key)]).get(path_key, {})
                status = str(row.get("status") or "").lower()
                if status in ("found", "tagged", "partial"):
                    media_path = str(row.get("media_path") or "")
                    if not media_path or Path(media_path).exists():
                        return True
            except Exception:
                pass
            return False

        def queue_fallback_or_finish(token, state):
            if self.interruption_requested():
                return False
            path_key = str(state["path"])
            if _state_already_promoted_by_side_queue(state):
                stats["skipped"] += 1
                self._emit_log(f"  REVERSE SKIP: {state['path'].name} already matched by side queue")
                return False
            old_status = str(state.get("prior_status") or "")
            if old_status and not bool(self.settings.get("retry_nomatch", False)):
                stats["skipped"] += 1
                self._emit_log(f"  SITE UPDATE COMPLETE: {state['path'].name} existing={old_status}; reverse fallback not repeated")
                return False
            if state.get("persisted_found"):
                return False
            if state.get("fallback_active"):
                return True
            if path_key in deferred_sauce:
                retry_at = int(deferred_sauce[path_key][1])
                left = max(0, retry_at - int(time.time()))
                self._emit_log(
                    f"  SAUCENAO RETRY ALREADY QUEUED: {state['path'].name}; "
                    f"IQDB/Ascii2D not repeated; retry in {left//60}m {left%60}s"
                )
                return False
            if len(active_fallback_tokens) >= reverse_backlog_limit:
                # v337: reverse is a side conveyor. A full reverse backlog must
                # not stop the file feeder. Keep this file as pending fallback
                # and enqueue it when a reverse slot is freed.
                state["phase"] = "fallback_pending"
                state["fallback_pending"] = True
                return True
            active_fallback_tokens.add(token)
            stats["reverse_started"] = int(stats.get("reverse_started", 0) or 0) + 1
            fallback_q.put((token, state["path"], False))
            state["phase"] = "fallback"
            state["fallback_pending"] = False
            return True

        def _drain_pending_fallbacks():
            if not _reverse_services_enabled() or self.interruption_requested():
                return 0
            slots = max(0, reverse_backlog_limit - len(active_fallback_tokens))
            if slots <= 0:
                return 0
            moved = 0
            # Keep insertion order: older files enter reverse before newer files.
            for _token, _state in list(states.items()):
                if moved >= slots or self.interruption_requested():
                    break
                if not _state or not _state.get("fallback_pending"):
                    continue
                if _state.get("persisted_found") or _state.get("fallback_active"):
                    _state["fallback_pending"] = False
                    continue
                active_fallback_tokens.add(_token)
                stats["reverse_started"] = int(stats.get("reverse_started", 0) or 0) + 1
                fallback_q.put((_token, _state["path"], False))
                _state["phase"] = "fallback"
                _state["fallback_pending"] = False
                moved += 1
            if moved:
                self._emit_site_current("Reverse side queue", f"Добавлено в очередь: {moved}", "")
            return moved

        def _reverse_services_enabled():
            return bool(reverse_chain)

        def _maybe_queue_detached_reverse(token, state, *, reason="early-md5-miss"):
            if not (async_conveyor_v2 and reverse_early_enabled and _reverse_services_enabled()):
                return False
            if self.interruption_requested() or low_power:
                return False
            if state is None or state.get("persisted_found") or state.get("fallback_active") or state.get("reverse_detached_queued"):
                return False
            if str(state.get("phase") or "").startswith("fallback"):
                return False
            if not state.get("waiting"):
                return False
            if len(active_fallback_tokens) >= reverse_backlog_limit:
                return False
            if time.time() - float(state.get("submitted_at") or 0) < reverse_early_delay:
                return False
            results = list((state.get("lane_results") or {}).values())
            clean_misses = [
                r for r in results
                if not r.get("tags") and not r.get("network_failed") and not r.get("auth_required")
            ]
            min_misses = min(max(1, int(reverse_early_min_misses_setting or 1)), max(1, len(state.get("active_keys") or [])))
            if len(clean_misses) < min_misses:
                return False
            active_fallback_tokens.add(token)
            state["fallback_active"] = True
            state["reverse_detached_queued"] = True
            stats["reverse_started"] = int(stats.get("reverse_started", 0) or 0) + 1
            fallback_q.put((token, state["path"], False, True))
            self._emit_log(
                f"[REVERSE-ASYNC:{state['path'].name}] QUEUED {reason}: "
                f"md5_misses={len(clean_misses)} waiting_sites={len(state.get('waiting') or [])}"
            )
            self._emit_site_current("Reverse side queue", "В очереди после MD5-miss", str(state["path"]))
            return True

        def _apply_detached_reverse_result(token, result, error_text, tineye_tagged_delta=0, tineye_source_delta=0):
            active_fallback_tokens.discard(token)
            stats["reverse_done"] = int(stats.get("reverse_done", 0) or 0) + 1
            stats["tineye_tagged"] = int(stats.get("tineye_tagged", 0) or 0) + int(tineye_tagged_delta or 0)
            stats["tineye_source_only"] = int(stats.get("tineye_source_only", 0) or 0) + int(tineye_source_delta or 0)
            state = states.get(token)
            if state is None:
                return
            state["fallback_active"] = False
            result = str(result or "")
            if result in ("tagged", "partial"):
                if not state.get("counted_found"):
                    stats["tagged"] += 1
                    state["counted_found"] = True
                state["persisted_found"] = True
                state["prior_status"] = "found"
                state["existing_media_path"] = _archive_path_after_match(state)
                state["detached_fallback_result"] = result
                self._emit_log(f"[REVERSE-ASYNC:{state['path'].name}] FOUND; exact-site branches may still merge extra tags")
                return
            if result == "retry_saucenao":
                try:
                    retry_at = int(error_text or (time.time() + float(self.settings.get("saucenao_cooldown_seconds", 3600) or 3600)))
                except Exception:
                    retry_at = int(time.time() + 3600)
                try:
                    with persist_lock:
                        enqueue_reverse_retry(session_settings, state["path"], service="saucenao", retry_after=retry_at, reason="api_cooldown")
                    deferred_sauce[str(state["path"])] = (state["path"], retry_at)
                    stats["deferred_saucenao"] += 1
                    left = max(0, retry_at - int(time.time()))
                    self._emit_log(f"[REVERSE-ASYNC:{state['path'].name}] SAUCENAO COOLDOWN; retry in {left//60}m {left%60}s")
                except Exception as _rw_e:
                    self._emit_log(f"[REVERSE-ASYNC:{state['path'].name}] retry queue error: {_rw_e}")
                state["detached_fallback_result"] = "retry_saucenao"
                return
            if result == "nomatch":
                state["detached_fallback_result"] = "nomatch"
                self._emit_log(f"[REVERSE-ASYNC:{state['path'].name}] NO MATCH; waiting exact-site branches before final status")
                return
            if result == "skip_found":
                state["detached_fallback_result"] = "skip_found"
                return
            state["detached_fallback_result"] = result or "error"
            if error_text:
                self._emit_log(f"[REVERSE-ASYNC:{state['path'].name}] ERROR: {error_text}")

        def _finalize_detached_reverse_if_ready(state):
            result = str(state.get("detached_fallback_result") or "")
            if not result:
                return False
            if state.get("persisted_found"):
                if not state.get("counted_found"):
                    stats["tagged"] += 1
                    state["counted_found"] = True
                return True
            if result == "nomatch":
                stats["nomatch"] += 1
                return True
            if result == "retry_saucenao":
                return True
            if result in ("retry_network", "error"):
                stats["deferred_network" if result == "retry_network" else "errors"] += 1
                return True
            return False

        def _archive_path_after_match(state):
            current = str(state.get("existing_media_path") or "")
            if current and Path(current).exists():
                return current
            try:
                return str(result_paths_for(session_settings, state["path"], "tagged")["media_file"])
            except Exception:
                return current

        def _enqueue_background_tag_job(state, lane_key, result):
            if not category_enabled:
                return
            base_site_key = _normalize_category_host(str(lane_site_key.get(lane_key, "")).split("::", 1)[0])
            source_url = str(result.get("source") or "")
            source_host = _category_host_from_url(source_url)
            if not source_url:
                return
            # Queue by the actual source URL host.  This matters for source→MD5
            # relay and reverse matches where a lane can save tags for a source
            # different from its own direct-MD5 lane.
            if source_host not in category_hosts and base_site_key not in category_hosts:
                return
            # Even if a source lane receives some category hints, keep the
            # background overlay for flat-tag booru sites.  Its job is to refine
            # the already-saved exact API tag set using guarded HTML/category
            # data, not to block the MD5 conveyor lane.
            media_path = _archive_path_after_match(state)
            job_id = (str(state["path"]), source_url)
            try:
                with persist_lock:
                    enqueue_tag_enrichment(session_settings, state["path"], media_path, source_url, job_key=category_job_key)
                if job_id not in category_scheduled:
                    category_scheduled.add(job_id)
                    _queued_host, _queued_ok = _put_category_job({"original_path": str(state["path"]), "media_path": media_path, "source_url": source_url, "job_key": category_job_key})
                    if _queued_ok:
                        self._emit_log(f"  TAG CATEGORY BACKGROUND QUEUED [{_queued_host}]: {state['path'].name}")
                    else:
                        category_scheduled.discard(job_id)
            except Exception as e:
                self._emit_log(f"  TAG CATEGORY QUEUE ERROR [{base_site_key}]: {state['path'].name}: {e}")

        def _persist_one_lane_match(state, lane_key, result):
            if lane_key in state.get("saved_match_keys", set()):
                return True
            tags = list(result.get("tags") or [])
            if not tags:
                return True
            source = str(result.get("source") or "")
            groups = result.get("groups") or {}
            result_method = str(result.get("method") or "md5")
            per_source_groups = [{"url": source, "groups": groups or {"general": tags}, "method": result_method}] if source else []
            try:
                with persist_lock:
                    if state.get("persisted_found") or (state.get("prior_status") in ("found", "tagged") and state.get("existing_media_path")):
                        outcome = writer.merge_conveyor_match_into_existing(
                            state.get("existing_media_path") or _archive_path_after_match(state),
                            state["path"], tags,
                            [f"{result_method} {lane_name.get(lane_key, lane_key)} {source}"] if source else [],
                            [groups] if groups else [],
                            per_source_groups,
                        )
                    else:
                        outcome = writer.save_conveyor_match(
                            state["path"], tags,
                            [f"{result_method} {lane_name.get(lane_key, lane_key)} {source}"] if source else [],
                            [groups] if groups else [],
                            per_source_groups,
                        )
                    if outcome != "tagged":
                        return False
                    try:
                        remove_reverse_retry(session_settings, state["path"], service="saucenao")
                    except Exception as _rr_e:
                        if self._handle_parser_db_write_error(_rr_e, f"remove SauceNAO retry {state['path'].name}"):
                            return False
                        raise
                self._emit_site_current("Финальная сборка", f"Сохранён source bundle: {lane_name.get(lane_key, lane_key)}", str(state["path"]))
                if not state.get("persisted_found"):
                    stats["tagged"] += 1
                    state["counted_found"] = True
                    if state.get("was_existing_found"):
                        stats["site_merged"] += 1
                state["persisted_found"] = True
                state["prior_status"] = "found"
                state["existing_media_path"] = _archive_path_after_match(state)
                state.setdefault("saved_match_keys", set()).add(lane_key)
                deferred_sauce.pop(str(state["path"]), None)
                _enqueue_background_tag_job(state, lane_key, result)
                return True
            except Exception as e:
                state["persistence_error"] = True
                self._emit_log(f"ERROR {state['path'].name}: {e}")
                return False

        def checkpoint_lane_results(state, *, finalize_misses=False):
            nonlocal last_passive_checkpoint_site_checks
            """Persist each completed source as soon as its result is final.

            Filename-derived misses are not final until either another site matched
            that filename or the real-file MD5 pass is known not to be needed.
            Matches and real-MD5 checks are durable immediately, so STOP/restart
            resumes each site lane from its own checkpoint instead of replaying the
            whole conveyor window.
            """
            checkpointed = state.setdefault("checkpointed_keys", set())
            for lane_key, result in list(state.get("lane_results", {}).items()):
                if lane_key in checkpointed or result.get("network_failed") or result.get("auth_required"):
                    continue
                final_for_lane = bool(result.get("tags")) or state.get("phase") == "real" or bool(finalize_misses)
                if not final_for_lane:
                    continue
                if result.get("tags") and not _persist_one_lane_match(state, lane_key, result):
                    continue
                checkpoint_ok = False
                for _checkpoint_attempt in range(2):
                    try:
                        with persist_lock:
                            mark_site_scanned(
                                session_settings, state["path"], lane_site_key[lane_key],
                                engine=lane_engine[lane_key], scan_revision=SITE_SCAN_REVISION,
                                outcome="match" if result.get("tags") else "miss",
                                checked_md5=result.get("md5", ""), source_url=result.get("source", ""),
                            )
                        checkpointed.add(lane_key)
                        checkpoint_ok = True
                        break
                    except Exception as _wr_e:
                        # If the app was restarted after power loss and the user
                        # starts the parser before SQLite health check finishes,
                        # wait instead of crashing.  If Windows/storage returns a
                        # real disk I/O error, stop safely and let recovery replay
                        # the last files next launch.
                        handled = self._handle_parser_db_write_error(
                            _wr_e,
                            f"site checkpoint {lane_site_key[lane_key]} {state['path'].name}",
                        )
                        if handled:
                            if self.interruption_requested():
                                return
                            continue
                        raise
                if not checkpoint_ok:
                    continue
                stats["site_checks"] += 1
                if passive_checkpoint_every and stats["site_checks"] - last_passive_checkpoint_site_checks >= passive_checkpoint_every:
                    try:
                        from core.light_backup import passive_checkpoint_sqlite
                        with persist_lock:
                            passive_checkpoint_sqlite(session_settings, optimize=False)
                        last_passive_checkpoint_site_checks = int(stats["site_checks"])
                        self._emit_log(f"SQLite WAL checkpoint PASSIVE: site_checks={stats['site_checks']}")
                    except Exception as _ck_e:
                        self._emit_log(f"SQLite WAL checkpoint warning: {_ck_e}")

        def submit_path(path):
            nonlocal next_token, site_up_to_date_count
            if self.interruption_requested():
                return False
            next_token += 1
            token = next_token
            path = Path(path)

            def _complete_hash_error(stage, exc):
                stats["errors"] = int(stats.get("errors", 0) or 0) + 1
                states[token] = {
                    "token": token,
                    "path": path, "search_img": path, "phase": "hash_error",
                    "first_was_filename": False, "md5": "",
                    "active_keys": [], "waiting": set(),
                    "tags": [], "sources": [], "groups": [], "network_failed": False,
                    "lane_results": {}, "prior_status": prior_global_status.get(str(path)),
                    "was_existing_found": prior_global_status.get(str(path)) in ("found", "tagged"),
                    "existing_media_path": existing_media_map.get(str(path), ""), "is_sauce_retry": False,
                    "checkpointed_keys": set(), "saved_match_keys": set(), "persisted_found": False,
                    "checked_md5s": set(), "pending_site_md5s": [],
                    "submitted_at": time.time(), "fallback_active": False,
                    "reverse_detached_queued": False, "detached_fallback_result": "",
                    "counted_found": False,
                }
                self._emit_log(
                    f"[MD5:{path.name}] HASH SKIP: {stage}: {type(exc).__name__}: {str(exc)[:180]}"
                )
                event_q.put(("complete", token))
                return True

            try:
                from core.parser_power_recovery import record_parser_file
                record_parser_file(session_settings, path)
            except Exception:
                pass
            already = set((site_done_map.get(str(path)) or {}).keys())
            active_keys = [lane_key for lane_key in lane_queue if lane_site_key[lane_key] not in already]
            if not active_keys:
                states[token] = {
                    "token": token,
                    "path": path, "search_img": path, "phase": "fallback_or_done",
                    "first_was_filename": False, "md5": "",
                    "active_keys": [], "waiting": set(),
                    "tags": [], "sources": [], "groups": [], "network_failed": False,
                    "lane_results": {}, "prior_status": prior_global_status.get(str(path)),
                    "was_existing_found": prior_global_status.get(str(path)) in ("found", "tagged"),
                    "existing_media_path": existing_media_map.get(str(path), ""), "is_sauce_retry": False,
                    "checkpointed_keys": set(), "saved_match_keys": set(), "persisted_found": False,
                    "checked_md5s": set(), "pending_site_md5s": [],
                    "submitted_at": time.time(), "fallback_active": False,
                    "reverse_detached_queued": False, "detached_fallback_result": "",
                    "counted_found": False,
                }
                site_up_to_date_count += 1
                if log_each_site_up_to_date:
                    self._emit_log(f"[MD5:{path.name}] SITES UP TO DATE; reverse-only queued")
                elif site_up_to_date_count == 1 or site_up_to_date_count % 500 == 0:
                    self._emit_log(f"[MD5] SITES UP TO DATE: {site_up_to_date_count} file(s); direct MD5 branches already journaled, eligible files go to reverse branches")
                if not queue_fallback_or_finish(token, states[token]):
                    event_q.put(("complete", token))
                return True

            self._emit_log(f"SEARCH [MD5]: {path.name}")
            try:
                search_img = video_frame_image(path)
            except MemoryError as exc:
                return _complete_hash_error("video frame extraction", exc)
            except Exception as exc:
                return _complete_hash_error("video frame extraction", exc)
            if search_img != path:
                self._emit_log(f"[MD5:{path.name}] VIDEO FRAME: {search_img.name}")
            img_phash = file_phash(search_img)
            if img_phash:
                self._emit_log(f"[MD5:{path.name}] PHASH: {img_phash}")
            from_filename = is_md5(path.stem)
            if from_filename:
                lookup_md5 = path.stem.lower()
                self._emit_log(f"[MD5:{path.name}] TRY MD5 FROM FILENAME: {lookup_md5}")
                phase = "filename"
            else:
                try:
                    lookup_md5 = file_md5(search_img)
                except MemoryError as exc:
                    return _complete_hash_error("real file MD5", exc)
                except Exception as exc:
                    return _complete_hash_error("real file MD5", exc)
                self._emit_log(f"[MD5:{path.name}] TRY REAL FILE MD5: {lookup_md5}")
                phase = "real"
            if active_keys and len(active_keys) < len(lane_queue):
                pending_names = ", ".join(lane_name.get(key, key) for key in active_keys)
                self._emit_log(f"[MD5:{path.name}] RESUME ONLY PENDING SITES: {pending_names}")
            states[token] = {
                "token": token,
                "path": path, "search_img": search_img, "phase": phase,
                "first_was_filename": from_filename, "md5": lookup_md5,
                "active_keys": active_keys, "waiting": set(active_keys),
                "tags": [], "sources": [], "groups": [], "network_failed": False,
                "lane_results": {}, "prior_status": prior_global_status.get(str(path)),
                "was_existing_found": prior_global_status.get(str(path)) in ("found", "tagged"),
                "existing_media_path": existing_media_map.get(str(path), ""), "is_sauce_retry": False,
                "checkpointed_keys": set(), "saved_match_keys": set(), "persisted_found": False,
                "checked_md5s": {lookup_md5.lower()} if lookup_md5 else set(), "pending_site_md5s": [],
                "submitted_at": time.time(), "fallback_active": False,
                "reverse_detached_queued": False, "detached_fallback_result": "",
                "counted_found": False,
            }
            for lane_key in active_keys:
                lane_queue[lane_key].put((token, phase, lookup_md5, path))
            return True

        def _maybe_start_variant_site_md5_phase(state):
            """After a variant locator found an authoritative site/original MD5,
            run that MD5 through all enabled MD5 lanes before reverse search.
            """
            if self.interruption_requested():
                return False
            pending = []
            seen = set(state.get("checked_md5s", set()) or set())
            for value in list(state.get("pending_site_md5s", []) or []):
                md5v = str(value or "").strip().lower()
                if not md5v or md5v in seen or len(md5v) != 32:
                    continue
                if not all(c in "0123456789abcdef" for c in md5v):
                    continue
                pending.append(md5v)
            if not pending:
                return False
            site_md5 = pending[0]
            state.setdefault("checked_md5s", set()).add(site_md5)
            # Keep already persisted variant metadata, but run all lanes again
            # with the authoritative site/original MD5. Misses from the local
            # derivative MD5 are not finalized before this second pass.
            state["phase"] = f"variant_site_md5:{site_md5}"
            state["md5"] = site_md5
            state["waiting"] = set(state.get("active_keys", set()))
            state["lane_results"] = {}
            state["network_failed"] = False
            self._emit_log(f"[MD5:{state['path'].name}] TRY VARIANT SITE MD5 RELAY: {site_md5}")
            for key in state.get("active_keys", set()):
                lane_queue[key].put((state["token"], state["phase"], site_md5, state["path"]))
            return True

        def submit_saucenao_retry(path):
            nonlocal next_token
            if self.interruption_requested():
                return False
            next_token += 1
            token = next_token
            path = Path(path)
            self._emit_log(f"SAUCENAO RETRY AFTER COOLDOWN: {path.name}")
            try:
                record_task_event(session_settings, "saucenao_retry", "started_after_cooldown", path.name)
            except Exception:
                pass
            states[token] = {
                "path": path, "phase": "fallback_saucenao", "waiting": set(),
                "prior_status": prior_global_status.get(str(path)),
                "existing_media_path": existing_media_map.get(str(path), ""),
                "is_sauce_retry": True,
            }
            active_fallback_tokens.add(token)
            stats["reverse_started"] = int(stats.get("reverse_started", 0) or 0) + 1
            fallback_q.put((token, path, True))
            return True

        def _primary_inflight_count():
            count = 0
            for _state in states.values():
                phase = str((_state or {}).get("phase") or "")
                if not phase.startswith("fallback"):
                    count += 1
            return count

        def _site_lane_backlog_ok():
            if not async_conveyor_v2 or low_power:
                return True
            try:
                return all(int(q.qsize()) < lane_backlog_limit for q in lane_queue.values())
            except Exception:
                return True

        def _fill_primary_window():
            nonlocal file_iter_exhausted, submitted_count, last_feeder_notice
            if file_iter_exhausted or self.interruption_requested():
                return
            # Reverse/fallback is intentionally a side queue.  In v336 the feeder
            # is allowed to run far ahead of slow site branches; window is a state
            # cap, and lane_backlog_limit is the per-site queue cap.
            while (
                not file_iter_exhausted
                and _primary_inflight_count() < window
                and _site_lane_backlog_ok()
                and not self.interruption_requested()
            ):
                try:
                    next_path = next(file_iter)
                except StopIteration:
                    file_iter_exhausted = True
                    break
                if not submit_path(next_path):
                    break
                submitted_count += 1
                if async_conveyor_v2 and not low_power and (submitted_count - last_feeder_notice >= 500 or submitted_count == total):
                    last_feeder_notice = submitted_count
                    try:
                        max_lane_q = max((int(q.qsize()) for q in lane_queue.values()), default=0)
                    except Exception:
                        max_lane_q = 0
                    try:
                        pending_reverse = sum(1 for _s in states.values() if (_s or {}).get("fallback_pending"))
                    except Exception:
                        pending_reverse = 0
                    self._emit_log(
                        f"ASYNC FEEDER: submitted={submitted_count}/{total} live_states={_primary_inflight_count()} "
                        f"max_site_queue={max_lane_q} reverse_backlog={len(active_fallback_tokens)} reverse_pending={pending_reverse}"
                    )

        _fill_primary_window()

        def _mark_parser_recovery_completed(_path):
            # Recovery list must track the latest finished files, not the first
            # files submitted into the conveyor window.  This is intentionally
            # best-effort and must never block parser completion.
            try:
                from core.parser_power_recovery import record_parser_file_completed
                record_parser_file_completed(session_settings, _path)
            except Exception:
                pass

        completed = 0
        stopped = False
        while states or deferred_sauce or active_fallback_tokens or active_rule34_variant_jobs or not file_iter_exhausted:
            _fill_primary_window()
            _drain_pending_fallbacks()
            now = int(time.time())
            retry_slots = max(0, window - _primary_inflight_count())
            if retry_slots:
                due_paths = [key for key, value in list(deferred_sauce.items()) if int(value[1]) <= now]
                for key in due_paths[:retry_slots]:
                    path, _retry_at = deferred_sauce.pop(key)
                    submit_saucenao_retry(path)
            if not states and deferred_sauce:
                next_due = min(int(value[1]) for value in deferred_sauce.values())
                if sauce_wait_notice_for != next_due:
                    left = max(0, next_due - int(time.time()))
                    self._emit_log(f"SAUCENAO QUEUE WAITING: {len(deferred_sauce)} file(s); automatic retry in {left//60}m {left%60}s")
                    sauce_wait_notice_for = next_due
                self._wait_if_paused_or_delay(min(1.0, max(0.05, next_due - time.time())))
                continue
            self._wait_if_paused_or_delay(0)
            if self.interruption_requested():
                stopped = True
                self._emit_log("STOPPED")
                break
            try:
                event = event_q.get(timeout=0.25)
            except queue.Empty:
                now_mono = time.time()
                for _lk, _last in list(lane_heartbeat.items()):
                    stage = str(lane_stage.get(_lk, "idle") or "idle")
                    if stage == "idle":
                        continue
                    if now_mono - float(_last or 0) > 120 and _lk not in lane_stall_warned:
                        lane_stall_warned.add(_lk)
                        self._emit_log(
                            f"WORKER WATCHDOG: {lane_name.get(_lk, _lk)} нет heartbeat >120s; "
                            f"stage={stage}; ждём timeout текущего запроса, новые задачи не считаются завершёнными"
                        )
                        self._emit_site_current(lane_name.get(_lk, _lk), f"Ожидает timeout: {stage}", "")
                # No events right now: old code would just wait.  v336 uses this
                # idle tick to start reverse side work for files that already have
                # enough exact-MD5 misses while slower site branches are still busy.
                for _tok, _st in list(states.items())[:256]:
                    try:
                        if _st.get("waiting"):
                            _maybe_queue_detached_reverse(_tok, _st, reason="idle-md5-miss-quorum")
                    except Exception:
                        pass
                continue
            if self.interruption_requested():
                stopped = True
                self._emit_log("STOPPED")
                break
            if event[0] == "primary":
                extra_site_md5s = []
                match_method = "md5"
                if len(event) >= 15:
                    _, token, lane_key, site_key, engine, phase, checked_md5, tags, source, groups, network_failed, network_summary, lookup_status, extra_site_md5s, match_method = event
                elif len(event) >= 13:
                    _, token, lane_key, site_key, engine, phase, checked_md5, tags, source, groups, network_failed, network_summary, lookup_status = event
                else:
                    _, token, lane_key, site_key, engine, phase, checked_md5, tags, source, groups, network_failed, network_summary = event
                    lookup_status = ""
                state = states.get(token)
                if state is None or phase != state["phase"]:
                    continue
                state["waiting"].discard(lane_key)
                if extra_site_md5s:
                    current_extra = list(state.get("pending_site_md5s", []) or [])
                    for _m in extra_site_md5s:
                        _m = str(_m or "").strip().lower()
                        if _m and _m not in current_extra:
                            current_extra.append(_m)
                    state["pending_site_md5s"] = current_extra
                state["lane_results"][lane_key] = {"tags": list(tags or []), "source": source, "groups": groups or {}, "md5": checked_md5, "network_failed": bool(network_failed), "auth_required": lookup_status == "auth_required", "method": match_method}
                _queue_rule34_variant_locator(state, lane_key, checked_md5, lookup_status, network_failed, tags)
                if network_failed:
                    state["network_failed"] = True
                if tags:
                    state["tags"].extend(tags)
                    if source:
                        _method_for_source = str(match_method or "md5")
                        state["sources"].append(f"{_method_for_source} {lane_name.get(lane_key, lane_key)} {source}")
                    if groups:
                        state["groups"].append(groups)
                    self._emit_site_current(lane_name.get(lane_key, lane_key), "Найдено", str(state["path"]))
                else:
                    if lookup_status == "auth_required":
                        self._emit_site_current(lane_name.get(lane_key, lane_key), "Нужен API ключ", str(state["path"]))
                    else:
                        self._emit_site_current(lane_name.get(lane_key, lane_key), "Нет совпадения", str(state["path"]))
                # A match is final immediately; a real-MD5 result is also final.
                # Checkpoint now, before slower lanes finish, so restart does not
                # replay fast-site work from the beginning of the conveyor window.
                checkpoint_lane_results(state, finalize_misses=False)
                if state["waiting"]:
                    _maybe_queue_detached_reverse(token, state, reason="md5-miss-quorum")
                    continue
                if state.get("persisted_found") and not state["tags"]:
                    checkpoint_lane_results(state, finalize_misses=True)
                    states.pop(token, None)
                    completed += 1
                    _mark_parser_recovery_completed(state["path"])
                    self._emit_current_file(str(state["path"]))
                    self._emit_progress(completed, total)
                    _fill_primary_window()
                    continue
                if state["tags"]:
                    if _maybe_start_variant_site_md5_phase(state):
                        continue
                    # The matching lanes have already saved metadata. At this point
                    # filename-phase misses are also final because at least one source
                    # verified the filename hash or rule34 image-key variant.
                    checkpoint_lane_results(state, finalize_misses=True)
                    if state.get("persisted_found"):
                        self._emit_site_current("Финальная сборка", "MD5 direct: готово, reverse не нужен", str(state["path"]))
                    if not state.get("persisted_found"):
                        stats["errors"] += 1
                elif state["phase"] == "filename":
                    try:
                        real_md5 = file_md5(state["search_img"])
                    except MemoryError as exc:
                        stats["errors"] += 1
                        self._emit_log(f"[MD5:{state['path'].name}] HASH SKIP: filename fallback real MD5: {type(exc).__name__}: {str(exc)[:180]}")
                        states.pop(token, None)
                        completed += 1
                        _mark_parser_recovery_completed(state["path"])
                        self._emit_current_file(str(state["path"]))
                        self._emit_progress(completed, total)
                        _fill_primary_window()
                        continue
                    except Exception as exc:
                        stats["errors"] += 1
                        self._emit_log(f"[MD5:{state['path'].name}] HASH SKIP: filename fallback real MD5: {type(exc).__name__}: {str(exc)[:180]}")
                        states.pop(token, None)
                        completed += 1
                        _mark_parser_recovery_completed(state["path"])
                        self._emit_current_file(str(state["path"]))
                        self._emit_progress(completed, total)
                        _fill_primary_window()
                        continue
                    if real_md5.lower() != state["md5"].lower():
                        state["phase"] = "real"
                        state["md5"] = real_md5
                        state["waiting"] = set(state["active_keys"])
                        state["lane_results"] = {}
                        state["checkpointed_keys"] = set()
                        state["network_failed"] = False
                        self._emit_log(f"[MD5:{state['path'].name}] TRY REAL FILE MD5: {real_md5}")
                        for key in state["active_keys"]:
                            lane_queue[key].put((token, "real", real_md5, state["path"]))
                        continue
                    checkpoint_lane_results(state, finalize_misses=True)
                    if state["network_failed"]:
                        stats["deferred_network"] += 1
                        self._emit_log(f"  NETWORK TEMPORARY FAILURE: {state['path'].name} has unfinished site lanes; deferred")
                    elif _finalize_detached_reverse_if_ready(state):
                        pass
                    elif queue_fallback_or_finish(token, state):
                        continue
                else:
                    checkpoint_lane_results(state, finalize_misses=True)
                    if state["network_failed"]:
                        stats["deferred_network"] += 1
                        self._emit_log(f"  NETWORK TEMPORARY FAILURE: {state['path'].name} has unfinished site lanes; deferred")
                    elif _finalize_detached_reverse_if_ready(state):
                        pass
                    elif queue_fallback_or_finish(token, state):
                        continue
                states.pop(token, None)
                completed += 1
                _mark_parser_recovery_completed(state["path"])
                self._emit_current_file(str(state["path"]))
                self._emit_progress(completed, total)
                _fill_primary_window()
            elif event[0] == "rule34_variant":
                _, job_id, matched, error_text, source_url, tag_count = event
                active_rule34_variant_jobs.discard(job_id)
                try:
                    scheduled_rule34_variant_jobs.discard(job_id)
                except Exception:
                    pass
                if matched:
                    try:
                        matched_path = str(Path(job_id[0])) if isinstance(job_id, tuple) else ""
                        if matched_path:
                            rule34_variant_matched_paths.add(matched_path)
                            for _st in states.values():
                                if str(_st.get("path") or "") == matched_path:
                                    _st["persisted_found"] = True
                                    _st["prior_status"] = "found"
                                    _st["network_failed"] = False
                    except Exception:
                        pass
                    self._emit_site_current("rule34 40hex/SHA1", f"Найдено: {int(tag_count or 0)} тегов", str(job_id[0] if isinstance(job_id, tuple) else ""))
                    self._emit_log(f"[R34-VARIANT:{Path(job_id[0]).name if isinstance(job_id, tuple) else '?'}] DONE tags={int(tag_count or 0)} {source_url}")
                elif error_text:
                    self._emit_site_current("rule34 40hex/SHA1", "Ошибка/нет совпадения", str(job_id[0] if isinstance(job_id, tuple) else ""))
                    self._emit_log(f"[R34-VARIANT:{Path(job_id[0]).name if isinstance(job_id, tuple) else '?'}] DONE no match: {error_text}")
                else:
                    self._emit_site_current("rule34 40hex/SHA1", "Нет совпадения", str(job_id[0] if isinstance(job_id, tuple) else ""))
            elif event[0] == "complete":
                _, token = event
                state = states.pop(token, None)
                if state is None:
                    continue
                completed += 1
                _mark_parser_recovery_completed(state["path"])
                self._emit_current_file(str(state["path"]))
                self._emit_progress(completed, total)
                _fill_primary_window()
            elif event[0] == "fallback_detached":
                if len(event) >= 6:
                    _, token, result, error_text, tineye_tagged_delta, tineye_source_delta = event
                else:
                    _, token, result, error_text = event
                    tineye_tagged_delta = 0
                    tineye_source_delta = 0
                _apply_detached_reverse_result(token, result, error_text, tineye_tagged_delta, tineye_source_delta)
                state = states.get(token)
                if state is not None and not state.get("waiting") and _finalize_detached_reverse_if_ready(state):
                    states.pop(token, None)
                    completed += 1
                    _mark_parser_recovery_completed(state["path"])
                    self._emit_current_file(str(state["path"]))
                    self._emit_progress(completed, total)
                _fill_primary_window()
            elif event[0] == "fallback":
                if len(event) >= 6:
                    _, token, result, error_text, tineye_tagged_delta, tineye_source_delta = event
                else:
                    _, token, result, error_text = event
                    tineye_tagged_delta = 0
                    tineye_source_delta = 0
                active_fallback_tokens.discard(token)
                stats["reverse_done"] = int(stats.get("reverse_done", 0) or 0) + 1
                stats["tineye_tagged"] = int(stats.get("tineye_tagged", 0) or 0) + int(tineye_tagged_delta or 0)
                stats["tineye_source_only"] = int(stats.get("tineye_source_only", 0) or 0) + int(tineye_source_delta or 0)
                state = states.pop(token, None)
                if state is None:
                    continue
                is_retry = bool(state.get("is_sauce_retry", False))
                path_key = str(state["path"])
                if result == "retry_saucenao":
                    try:
                        retry_at = int(error_text or (time.time() + float(self.settings.get("saucenao_cooldown_seconds", 3600) or 3600)))
                    except Exception:
                        retry_at = int(time.time() + 3600)
                    retry_write_ok = False
                    for _wr_attempt in range(2):
                        try:
                            with persist_lock:
                                enqueue_reverse_retry(session_settings, state["path"], service="saucenao", retry_after=retry_at, reason="api_cooldown")
                            retry_write_ok = True
                            break
                        except Exception as _rw_e:
                            if self._handle_parser_db_write_error(_rw_e, f"enqueue SauceNAO retry {state['path'].name}"):
                                if self.interruption_requested():
                                    break
                                continue
                            raise
                    if not retry_write_ok:
                        stopped = True
                        break
                    deferred_sauce[path_key] = (state["path"], retry_at)
                    stats["deferred_saucenao"] += 1
                    left = max(0, retry_at - int(time.time()))
                    self._emit_log(f"  SAUCENAO RETRY QUEUED: {state['path'].name}; automatic retry in {left//60}m {left%60}s")
                    if is_retry:
                        try:
                            record_task_event(session_settings, "saucenao_retry", "cooldown_again", state["path"].name)
                        except Exception:
                            pass
                else:
                    retry_remove_ok = False
                    for _wr_attempt in range(2):
                        try:
                            with persist_lock:
                                remove_reverse_retry(session_settings, state["path"], service="saucenao")
                            retry_remove_ok = True
                            break
                        except Exception as _rw_e:
                            if self._handle_parser_db_write_error(_rw_e, f"remove SauceNAO retry {state['path'].name}"):
                                if self.interruption_requested():
                                    break
                                continue
                            raise
                    if not retry_remove_ok:
                        stopped = True
                        break
                    deferred_sauce.pop(path_key, None)
                    if is_retry:
                        try:
                            record_task_event(session_settings, "saucenao_retry", f"completed_{result}", state["path"].name)
                        except Exception:
                            pass
                    if result == "tagged" or result == "partial":
                        stats["tagged"] += 1
                        self._emit_site_current("Финальная сборка", "Reverse: теги/источники сохранены", str(state["path"]))
                    elif result == "nomatch":
                        stats["nomatch"] += 1
                        self._emit_site_current("Финальная сборка", "No match после всех нужных веток", str(state["path"]))
                    elif result == "retry_network":
                        stats["deferred_network"] += 1
                    elif result == "skip":
                        stats["skipped"] += 1
                    else:
                        stats["errors"] += 1
                        if error_text:
                            self._emit_log(f"ERROR {state['path'].name}: {error_text}")
                if not is_retry:
                    completed += 1
                    _mark_parser_recovery_completed(state["path"])
                    self._emit_current_file(str(state["path"]))
                    self._emit_progress(completed, total)
                    _fill_primary_window()

        discarded = 0
        if stopped or self.interruption_requested():
            # Hard stop: discard requests already prepared inside the conveyor
            # window. Already-sent HTTP calls may return, but their logging and
            # persistence are suppressed after interruption.
            for _lane_key, _shown, _key, _engine, _site, work_q in site_lanes:
                while True:
                    try:
                        item = work_q.get_nowait()
                    except queue.Empty:
                        break
                    if item is not sentinel:
                        discarded += 1
            while True:
                try:
                    item = fallback_q.get_nowait()
                except queue.Empty:
                    break
                if item is not sentinel:
                    discarded += 1
            while True:
                try:
                    item = rule34_variant_q.get_nowait()
                except queue.Empty:
                    break
                if item is not sentinel:
                    discarded += 1
            # Background category jobs remain durable in SQLite and resume later.
            self._emit_log(f"STOP: discarded queued checks={discarded}; only already-sent HTTP calls may finish silently")
        for _lane_key, _shown, _key, _engine, _site, work_q in site_lanes:
            work_q.put(sentinel)
        fallback_q.put(sentinel)
        if rule34_side_queue_enabled and rule34_side_site:
            rule34_variant_q.put(sentinel)
        if category_enabled:
            if not stopped:
                pending_categories = _pending_category_count()
                if pending_categories:
                    self._emit_log(f"TAG CATEGORY BACKGROUND: waiting current-run jobs={pending_categories} before DONE")
                    self._emit_site_current("Категории тегов", f"Дожидается раскладки: {pending_categories}", "")
                last_category_wait_notice = 0.0
                while not self.interruption_requested():
                    remaining_categories = _pending_category_count()
                    if remaining_categories <= 0:
                        break
                    now_wait = time.time()
                    if now_wait - last_category_wait_notice > 5.0:
                        last_category_wait_notice = now_wait
                        self._emit_site_current("Категории тегов", f"Дожидается раскладки: {remaining_categories}", "")
                    time.sleep(0.25)
            for _category_queue in category_queues.values():
                _category_queue.put(sentinel)
        for th in threads:
            th.join(timeout=0.25 if stopped else 1.0)
        return stats, stopped


    def run(self):
        try:
            if self.settings.get("parser_blueprint_enabled"):
                if self.settings.get("_parser_blueprint_invalid"):
                    self._emit_log("PARSER BLUEPRINT ERROR: активный граф не прошёл проверку; используется текущая безопасная конфигурация")
                else:
                    self._emit_log("PARSER BLUEPRINT ACTIVE: " + str(self.settings.get("_parser_blueprint_active_name", "active")))
                    self._emit_log("PARSER BLUEPRINT PLAN: " + str(self.settings.get("_parser_blueprint_compiled_summary", "")))
                    if self.settings.get("_parser_blueprint_warnings"):
                        self._emit_log("PARSER BLUEPRINT FULL-ACCESS: warnings will not block run")
        except Exception:
            pass
        try:
            _prof = str(self.settings.get("_tagger_resolved_performance_profile", "performance") or "performance")
            _ram = int(self.settings.get("_tagger_detected_ram_mb", 0) or 0)
            if _ram:
                self._emit_log(f"PERFORMANCE PROFILE: {_prof}; detected_ram={_ram//1024}GB; reverse_window={self.settings.get('tagger_reverse_admit_window_files')}")
            else:
                self._emit_log(f"PERFORMANCE PROFILE: {_prof}; reverse_window={self.settings.get('tagger_reverse_admit_window_files')}")
        except Exception:
            pass
        if not self._wait_for_db_writes_ready("перед запуском парсера"):
            self._emit_done()
            return
        if not self._wait_for_parser_db_available("перед запуском парсера"):
            self._emit_done()
            return
        root=Path(self.settings.get("root",""))
        explicit_paths = list(self.settings.get("_parser_explicit_paths") or [])
        if explicit_paths:
            files=[]
            for raw in explicit_paths:
                p=Path(str(raw))
                try:
                    if p.is_dir():
                        files.extend([x for x in p.rglob("*") if x.suffix.lower() in MEDIA_EXTS])
                    elif p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                        files.append(p)
                except Exception as e:
                    self._emit_log(f"DROP INPUT WARNING: {p}: {e}")
            self._emit_log(f"SCAN FOUND MEDIA: {len(files)} files (drag-and-drop input)")
        else:
            files=[p for p in root.rglob("*") if p.suffix.lower() in MEDIA_EXTS] if root.exists() else []
            self._emit_log(f"SCAN FOUND MEDIA: {len(files)} files")
        # v281: rglob may surface the same managed file more than once when the
        # user retries a NO_MATCH bucket through symlinks/junctions or duplicated
        # input entries.  Processing the same path multiple times can make a
        # later NO_MATCH fallback race against an earlier FOUND promotion.
        try:
            seen_files = set()
            deduped_files = []
            for _p in files:
                try:
                    _key = str(Path(_p).resolve()).casefold()
                except Exception:
                    _key = str(_p).casefold()
                if _key in seen_files:
                    continue
                seen_files.add(_key)
                deduped_files.append(_p)
            if len(deduped_files) != len(files):
                self._emit_log(f"QUEUE PREP: duplicate path filter skipped {len(files) - len(deduped_files)}")
            files = deduped_files
        except Exception as _dedupe_e:
            self._emit_log(f"QUEUE PREP: duplicate path filter warning: {_dedupe_e}")
        try:
            if bool(self.settings.get("parser_disk_preflight_enabled", True)):
                from core.preflight import output_disk_info, format_bytes
                disk = output_disk_info(self.settings)
                if int(disk.get("free", 0) or 0) < int(disk.get("reserve", 0) or 0):
                    self._emit_log(
                        "STOP NO DISK SPACE: свободно "
                        + format_bytes(disk.get("free", 0))
                        + ", требуется резерв "
                        + format_bytes(disk.get("reserve", 0))
                    )
                    self._emit_done()
                    return
        except Exception as _space_e:
            self._emit_log(f"DISK PREFLIGHT WARNING: {_space_e}")
        # Не сканируем Local_Booru_Archive/output как обычный источник. Иначе один и тот же
        # файл может снова попасть в no_match/found второй копией. Исключение —
        # режим Retry NO_MATCH, когда корнем специально выбрана output/no_match/media.
        try:
            out_base = result_output_base(self.settings).resolve()
            root_resolved = root.resolve()
            is_retry_nm_root = root_resolved.name == "media" and root_resolved.parent.name == "no_match"
            # Do not recursively feed our own output back into APT when the user
            # selected a normal source folder. But if the selected root itself is
            # inside Local_Booru_Archive/output, assume the user deliberately wants to
            # rescan/repair that bucket.
            try:
                root_is_output = (root_resolved == out_base) or (out_base in root_resolved.parents)
            except Exception:
                root_is_output = False
            if not is_retry_nm_root and not root_is_output:
                before_output_filter = len(files)
                files = [x for x in files if out_base not in x.resolve().parents]
                skipped_output = before_output_filter - len(files)
                if skipped_output:
                    self._emit_log(f"SKIP OUTPUT FOLDER FILES: {skipped_output}")
        except Exception as e:
            self._emit_log(f"OUTPUT FILTER WARNING: {e}")

        if self.settings.get("skip_copy_suffix_files", True):
            before = len(files)
            files = [x for x in files if not has_copy_suffix(x)]
            skipped_copy = before - len(files)
            if skipped_copy:
                self._emit_log(f"SKIP COPY-SUFFIX FILES: {skipped_copy}")
                if not files and before:
                    self._emit_log("NO FILES LEFT AFTER COPY-SUFFIX FILTER. Disable 'Skip files ending (1)/(2)' if these copies must be scanned.")

        # Files intentionally deleted by the duplicate cleaner are remembered by
        # exact name+hash. Important: checking MD5 for every file here freezes APT
        # on large archives, so we only do the expensive check when the filename
        # exists in the deleted registry.
        kept = []
        deleted_skips = 0
        deleted_candidates = 0
        for x in files:
            try:
                if has_deleted_record_for_name(x, settings=self.settings):
                    deleted_candidates += 1
                    if should_skip_deleted_file(x, settings=self.settings):
                        deleted_skips += 1
                        continue
            except Exception:
                pass
            kept.append(x)
        files = kept
        self._emit_log("QUEUE PREP: deleted-cache filter done")
        if deleted_candidates:
            self._emit_log(f"DELETED-DUPLICATE CACHE CHECKED: {deleted_candidates}")
        if deleted_skips:
            self._emit_log(f"SKIP DELETED-DUPLICATE CACHE: {deleted_skips}")

        recovery_force_paths = []
        recovery_force_set = set()
        try:
            from core.parser_power_recovery import begin_parser_session
            recovery_force_paths = begin_parser_session(self.settings, root)
            if recovery_force_paths:
                existing_keys = {str(x) for x in files}
                added = 0
                # Put recovery files at the front so they are checked before the
                # long ordinary queue.  This is intentionally small: last 10 only.
                for _rp in reversed(recovery_force_paths):
                    if str(_rp) not in existing_keys and _rp.exists():
                        files.insert(0, _rp)
                        existing_keys.add(str(_rp))
                        added += 1
                recovery_force_set = {str(x) for x in recovery_force_paths if Path(x).exists()}
                self._emit_log(
                    f"POWER-LOSS RECOVERY: previous parser session was not closed cleanly; "
                    f"forcing recheck of last {len(recovery_force_set)} file(s)"
                    + (f"; re-added_to_queue={added}" if added else "")
                )
        except Exception as _recovery_e:
            self._emit_log(f"POWER-LOSS RECOVERY WARNING: {_recovery_e}")
            recovery_force_paths = []
            recovery_force_set = set()
        def processed_status(path):
            # SQLite is the only live source of processing state. Legacy sidecars
            # are handled only by the explicit importer/migration tools.
            return output_processed_status(self.settings, path)

        # The per-site conveyor is the supported MD5 architecture, not an end-user toggle.
        use_conveyor = bool(self.settings.get("enable_md5_lookup", True))
        active_site_keys = []
        site_done_map = {}
        prior_global_status = {}
        existing_media_map = {}
        restored_saucenao = []
        restored_saucenao_paths = set()
        if use_conveyor:
            try:
                _site_probe = Tagger(dict(self.settings), lambda _m: None)
                active_site_keys = list(dict.fromkeys(_site_scan_key(_site_probe, site) for site in _site_probe._all_enabled_site_configs()))
                active_site_keys = [x for x in active_site_keys if x]
                if not active_site_keys:
                    use_conveyor = False
            except Exception as e:
                self._emit_log(f"SITE STATUS WARNING: cannot read enabled sites: {e}")
                use_conveyor = False
            if use_conveyor:
                try:
                    from core.services.scan_state_service import pending_reverse_retry_paths
                    current_root_paths = {str(path) for path in files}
                    restored_saucenao = [
                        row for row in pending_reverse_retry_paths(self.settings, service="saucenao", limit=1000000)
                        if str(row[0]) in current_root_paths
                    ]
                    restored_saucenao_paths = {str(path) for path, _retry_at, _reason in restored_saucenao}
                except Exception as e:
                    if self._sqlite_database_locked(e):
                        self._stop_startup_db_locked("SAUCENAO retry restore", e)
                        self._emit_done()
                        return
                    self._emit_log(f"SAUCENAO RETRY RESTORE WARNING: {e}")
                    restored_saucenao = []
                    restored_saucenao_paths = set()

        if use_conveyor and recovery_force_set:
            try:
                from core.parser_power_recovery import force_recheck_sites
                removed = force_recheck_sites(self.settings, recovery_force_set, active_site_keys, scan_revision=SITE_SCAN_REVISION)
                self._emit_log(
                    f"POWER-LOSS RECOVERY: cleared site checkpoints for {len(recovery_force_set)} file(s); rows_removed={removed}"
                )
            except Exception as _force_e:
                self._emit_log(f"POWER-LOSS RECOVERY WARNING: cannot clear site checkpoints: {_force_e}")

        if self.settings.get("tag_only_untagged") or self.settings.get("skip_existing"):
            before_status = len(files)
            self._emit_log(f"STATUS CHECK: {before_status} files")
            status_map = {}
            try:
                from core.services.scan_state_service import processed_records_many
                processed_records = processed_records_many(self.settings, files)
                status_map = {path: row.get("status", "") for path, row in processed_records.items()}
                existing_media_map = {path: row.get("media_path", "") for path, row in processed_records.items()}
                self._emit_log(f"STATUS CHECK DB MATCHES: {len(status_map)}")
            except Exception as e:
                if self._sqlite_database_locked(e):
                    self._stop_startup_db_locked("STATUS CHECK", e)
                    self._emit_done()
                    return
                self._emit_log(f"STATUS CHECK DB WARNING: {e}")
            if use_conveyor:
                try:
                    from core.services.scan_state_service import site_scan_status_many
                    site_done_map = site_scan_status_many(self.settings, files, active_site_keys, scan_revision=SITE_SCAN_REVISION)
                except Exception as e:
                    if self._sqlite_database_locked(e):
                        self._stop_startup_db_locked("SITE STATUS CHECK", e)
                        self._emit_done()
                        return
                    self._emit_log(f"SITE STATUS DB WARNING: {e}")
                    site_done_map = {}
                active_set = set(active_site_keys)
                filtered = []
                completed_for_all_sites = 0
                new_site_pending = 0
                waiting_saucenao_only = 0
                for p in files:
                    path_key = str(p)
                    existing_status = status_map.get(path_key)
                    prior_global_status[path_key] = existing_status
                    done_sites = set((site_done_map.get(path_key) or {}).keys())
                    if path_key in recovery_force_set:
                        # Force the last files from an unclean shutdown through
                        # the normal found-check/merge path again.
                        done_sites = set()
                        site_done_map[path_key] = {}
                    has_pending_site = not active_set.issubset(done_sites)
                    retry_nomatch = bool(self.settings.get("retry_nomatch", False)) and existing_status in ("nomatch", "no_match")
                    has_saved_sauce_retry = path_key in restored_saucenao_paths and not existing_status
                    # A durable SauceNAO retry has already passed IQDB/Ascii2D in a
                    # previous run. Do not feed it through ordinary fallback again.
                    # It stays in deferred_sauce and is retried through SauceNAO only.
                    needs_new_result = not existing_status and not has_saved_sauce_retry
                    if has_pending_site or needs_new_result or retry_nomatch:
                        filtered.append(p)
                        if existing_status and has_pending_site:
                            new_site_pending += 1
                    elif has_saved_sauce_retry:
                        waiting_saucenao_only += 1
                    else:
                        completed_for_all_sites += 1
                files = filtered
                self._emit_log(f"SITE STATUS CHECK: enabled_sites={len(active_site_keys)} fully_checked_existing={completed_for_all_sites} pending_on_processed={new_site_pending}")
                if waiting_saucenao_only:
                    self._emit_log(f"SAUCENAO RETRY WAITING: {waiting_saucenao_only} file(s) excluded from normal MD5/IQDB/Ascii2D replay")
                skipped_status = before_status - len(files)
                if skipped_status:
                    self._emit_log(f"SKIP FILES ALREADY CHECKED BY ALL ENABLED SITES: {skipped_status}")
            else:
                filtered = []
                for p in files:
                    if status_map.get(str(p)) is None:
                        filtered.append(p)
                files = filtered
                skipped_status = before_status - len(files)
                if skipped_status:
                    self._emit_log(f"SKIP ALREADY PROCESSED SQL STATUS: {skipped_status}")

        self._emit_log("QUEUE PREP: status filters done")
        limit=int(self.settings.get("limit_files",0))
        if limit>0: files=files[:limit]

        self._emit_log(f"SEARCH QUEUE: {len(files)} files")
        if not files:
            self._emit_log("NO FILES TO SEARCH. Check root folder, copy-suffix filter, skip-existing, and retry-no-match settings.")

        from datetime import datetime as _dt
        _session_ts = _dt.now().strftime("%Y-%m-%d_%H-%M")
        _settings_with_session = dict(self.settings, session_folder=_session_ts)
        tagger=Tagger(_settings_with_session, lambda m:self._emit_log(str(m)))
        total=len(files)
        tagged=0
        nomatch=0
        skipped=0
        errors=0
        deferred_network=0
        stopped=False
        stopped_network=False

        if use_conveyor and (total > 0 or restored_saucenao):
            # TinEye is part of the normal per-file reverse chain now.  It runs
            # inside Tagger.process_image after SauceNAO, not as a second pass over
            # the whole No Match table.
            result = self._run_site_conveyor(files, _settings_with_session, tagger, prior_global_status=prior_global_status, existing_media_map=existing_media_map, site_done_map=site_done_map, restored_saucenao=restored_saucenao)

            if result is not None:
                stats, stopped = result

                tineye_tagged = int(stats.get("tineye_tagged", 0) or 0)
                tineye_source_only = int(stats.get("tineye_source_only", 0) or 0)

                self._emit_log(
                    f"SUMMARY: TAGGED={stats['tagged']} NO_MATCH={stats['nomatch']} "
                    f"SKIPPED={stats['skipped']} DEFERRED_NETWORK={stats['deferred_network']} "
                    f"DEFERRED_SAUCENAO={stats.get('deferred_saucenao', 0)} "
                    f"TINEYE_TAGGED={tineye_tagged} TINEYE_SOURCE_ONLY={tineye_source_only} "
                    f"SITE_CHECKS={stats.get('site_checks', 0)} MERGED_EXISTING={stats.get('site_merged', 0)} "
                    f"ERRORS={stats['errors']} TOTAL={total} "
                    f"REVERSE={stats.get('reverse_done',0)}/{stats.get('reverse_started',0)} "
                    f"REVERSE_QUEUED={stats.get('reverse_queued_files',0)}/{stats.get('reverse_queued_tasks',0)} "
                    f"REVERSE_CANCELED={stats.get('reverse_canceled',0)} "
                    f"REVERSE_RETRY_QUEUED={stats.get('reverse_retry_queued',0)} "
                    f"CATEGORY_DONE={stats.get('category_done',0)}"
                )
                if stopped:
                    self._emit_log("SUMMARY: stopped by user")
                clean_ok = (
                    not stopped
                    and int(stats.get("errors", 0) or 0) == 0
                    and int(stats.get("deferred_network", 0) or 0) == 0
                    and int(stats.get("deferred_saucenao", 0) or 0) == 0
                    and int(stats.get("reverse_retry_queued", 0) or 0) == 0
                    and int(stats.get("reverse_initial_candidates", 0) or 0) == int(stats.get("reverse_initial_queued", 0) or 0)
                )
                if clean_ok:
                    try:
                        from core.parser_power_recovery import mark_parser_session_clean
                        mark_parser_session_clean(self.settings)
                    except Exception:
                        pass
                else:
                    self._emit_log("SUMMARY: run is not marked clean; retry/deferred/error state remains durable for next run")
                self._emit_done()
                return
        elif bool(self.settings.get("tagger_low_power_mode", False)):
            self._emit_log("LOW POWER MODE: conveyor manually disabled; legacy one-file processing is active")

        network_retry_attempts = max(0, min(5, int(self.settings.get("network_retry_attempts", 2) or 2)))
        network_retry_delay = max(1.0, min(120.0, float(self.settings.get("network_retry_delay_seconds", 10) or 10)))

        try:
            from core.local_parallel import local_workers
            parallel_workers = local_workers(self.settings, "tagger_parallel_workers", int(self.settings.get("local_tagger_workers", 4) or 4), maximum=16)
        except Exception:
            parallel_workers = int(self.settings.get("tagger_parallel_workers", 4) or 4)
            parallel_workers = max(1, min(parallel_workers, 16))
        if bool(self.settings.get("tagger_low_power_mode", False)):
            parallel_workers = 1

        def _process_with_network_retry(local_tagger, path):
            for attempt in range(network_retry_attempts + 1):
                if self.interruption_requested():
                    return "skip"
                result = local_tagger.process_image(path)
                if result != "retry_network":
                    return result
                if attempt < network_retry_attempts:
                    pause = min(120.0, network_retry_delay * (2 ** attempt))
                    self._emit_log(
                        f"  NETWORK RETRY {attempt + 1}/{network_retry_attempts}: "
                        f"{Path(path).name} через {int(pause)} сек."
                    )
                    self._wait_if_paused_or_delay(pause)
            return "retry_network"

        def _process_one(path, worker_index=0):
            # One Tagger instance per worker.  The Tagger has session/cache state,
            # so sharing one instance across threads is not safe.
            local_settings = dict(_settings_with_session)
            local_tagger = Tagger(local_settings, lambda m: self._emit_log(str(m)))
            local_tagger.cancel_callback = self.interruption_requested
            try:
                from core.parser_power_recovery import record_parser_file
                record_parser_file(self.settings, path)
            except Exception:
                pass
            self._emit_current_file(str(path))
            return _process_with_network_retry(local_tagger, path)

        if parallel_workers <= 1 or total <= 1:
            for i,p in enumerate(files,1):
                self._wait_if_paused_or_delay(0)
                if self.interruption_requested():
                    self._emit_log("STOPPED")
                    stopped=True
                    break

                try:
                    try:
                        from core.parser_power_recovery import record_parser_file
                        record_parser_file(self.settings, p)
                    except Exception:
                        pass
                    self._emit_current_file(str(p))
                    if self.interruption_requested():
                        self._emit_log("STOPPED")
                        stopped=True
                        break
                    tagger.cancel_callback = self.interruption_requested
                    result = _process_with_network_retry(tagger, p)
                    if result == "tagged":
                        tagged += 1
                    elif result == "nomatch":
                        nomatch += 1
                    elif result == "skip":
                        skipped += 1
                    elif result == "retry_network":
                        deferred_network += 1
                        stopped_network = True
                        self._emit_log(
                            "NETWORK UNAVAILABLE: текущий файл отложен и НЕ отправлен в Брак. "
                            "Сканирование остановлено; запусти его снова после восстановления интернета/VPN."
                        )
                except Exception as e:
                    if _looks_like_network_exception(e):
                        deferred_network += 1
                        stopped_network = True
                        self._emit_log(
                            f"NETWORK ERROR {p.name}: {e}. "
                            "Файл отложен и НЕ отправлен в Брак."
                        )
                    else:
                        errors += 1
                        self._emit_log(f"ERROR {p.name}: {e}")

                try:
                    from core.parser_power_recovery import record_parser_file_completed
                    record_parser_file_completed(self.settings, p)
                except Exception:
                    pass
                self._emit_progress(i,total)
                if stopped_network:
                    break
                self._wait_if_paused_or_delay(float(self.settings.get("delay_seconds", 0) or 0))
        else:
            self._emit_log(f"PARALLEL TAGGER: {parallel_workers} workers enabled")
            from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
            pending = {}
            done_count = 0
            network_abort = False
            file_iter = iter(files)
            with ThreadPoolExecutor(max_workers=parallel_workers, thread_name_prefix="tagger") as ex:
                def submit_next():
                    if self.interruption_requested() or network_abort:
                        return False
                    try:
                        pth = next(file_iter)
                    except StopIteration:
                        return False
                    fut = ex.submit(_process_one, pth, len(pending) + 1)
                    pending[fut] = pth
                    return True

                for _ in range(parallel_workers):
                    submit_next()

                while pending:
                    self._wait_if_paused_or_delay(0)
                    if self.interruption_requested():
                        stopped = True
                        self._emit_log("STOPPED")
                        for fut in pending:
                            fut.cancel()
                        break
                    ready, _ = wait(list(pending.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
                    if not ready:
                        continue
                    for fut in ready:
                        pth = pending.pop(fut)
                        try:
                            result = fut.result()
                            if result == "tagged":
                                tagged += 1
                            elif result == "nomatch":
                                nomatch += 1
                            elif result == "skip":
                                skipped += 1
                            elif result == "retry_network":
                                deferred_network += 1
                                network_abort = True
                                stopped_network = True
                                self._emit_log(
                                    "NETWORK UNAVAILABLE: файл отложен и НЕ отправлен в Брак. "
                                    "Новые задачи не запускаются; перезапусти сканирование после восстановления интернета/VPN."
                                )
                        except Exception as e:
                            if network_abort and fut.cancelled():
                                pass
                            elif _looks_like_network_exception(e):
                                deferred_network += 1
                                network_abort = True
                                stopped_network = True
                                self._emit_log(
                                    f"NETWORK ERROR {Path(pth).name}: {e}. "
                                    "Файл отложен и НЕ отправлен в Брак."
                                )
                            else:
                                errors += 1
                                self._emit_log(f"ERROR {Path(pth).name}: {e}")
                        try:
                            from core.parser_power_recovery import record_parser_file_completed
                            record_parser_file_completed(self.settings, pth)
                        except Exception:
                            pass
                        done_count += 1
                        self._emit_progress(done_count,total)
                        self._wait_if_paused_or_delay(float(self.settings.get("delay_seconds", 0) or 0))
                        if network_abort:
                            for queued in list(pending.keys()):
                                queued.cancel()
                        submit_next()

        self._emit_log(
            f"SUMMARY: TAGGED={tagged} NO_MATCH={nomatch} SKIPPED={skipped} "
            f"DEFERRED_NETWORK={deferred_network} ERRORS={errors} TOTAL={total}"
        )
        if stopped:
            self._emit_log("SUMMARY: stopped by user")
        if stopped_network:
            self._emit_log("SUMMARY: stopped because network/VPN was unavailable; deferred files remain eligible for retry")
        clean_ok = (not stopped and not stopped_network and errors == 0 and deferred_network == 0)
        if clean_ok:
            try:
                from core.parser_power_recovery import mark_parser_session_clean
                mark_parser_session_clean(self.settings)
            except Exception:
                pass
        else:
            self._emit_log("SUMMARY: run is not marked clean; retry/deferred/error state remains durable for next run")
        self._emit_done()

class BrowserLoginWorker(QThread):
    log = Signal(str)

    def __init__(self, auth_url, wait_seconds):
        super().__init__()
        self.auth_url = auth_url
        self.wait_seconds = int(wait_seconds)

    def run(self):
        try:
            try:
                from playwright.sync_api import sync_playwright
            except Exception as e:
                self.log.emit(f"PLAYWRIGHT ERROR: {e}")
                self.log.emit("Install: pip install playwright && playwright install")
                return

            url = self.auth_url.strip()
            if not url:
                self.log.emit("BROWSER LOGIN ERROR: empty URL")
                return

            host = urlparse(url).netloc.lower().replace("www.", "") or "default"

            profile_dir = Path(BROWSER_PROFILE_DIR) / host
            cookies_dir = Path(BROWSER_COOKIES_DIR)
            profile_dir.mkdir(parents=True, exist_ok=True)
            cookies_dir.mkdir(parents=True, exist_ok=True)

            self.log.emit(f"BROWSER LOGIN: {url}")
            self.log.emit(f"PROFILE: {profile_dir}")

            pw = sync_playwright().start()
            context = None

            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    channel="msedge",
                )

                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="load", timeout=120000)

                for i in range(self.wait_seconds):
                    if self.isInterruptionRequested():
                        self.log.emit("BROWSER LOGIN STOPPED")
                        return
                    left = self.wait_seconds - i
                    if left % 5 == 0 or left <= 5:
                        self.log.emit(f"LOGIN WAIT: {left}s")
                    time.sleep(1)

                try:
                    cookies = context.cookies()
                except Exception:
                    cookies = []

                try:
                    user_agent = page.evaluate("navigator.userAgent")
                except Exception:
                    user_agent = None

                data = {
                    "cookies": cookies,
                    "user_agent": user_agent,
                }

                # Main new per-host cookie bundle.
                host_file = cookies_dir / f"{host}.json"
                host_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                # Compatibility fallback for older code.
                legacy_file = Path(BROWSER_COOKIES_DIR) / "browser_cookies.json"
                legacy_file.parent.mkdir(parents=True, exist_ok=True)
                legacy_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                self.log.emit(f"COOKIES SAVED [{host}]: {len(cookies)}")
                self.log.emit(f"COOKIE FILE: {host_file}")

            finally:
                try:
                    if context is not None:
                        context.close()
                except Exception:
                    pass
                try:
                    pw.stop()
                except Exception:
                    pass

        except Exception as e:
            self.log.emit(f"BROWSER LOGIN ERROR: {e}")

