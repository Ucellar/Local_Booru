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
from ui.login_browser import LoginBrowserDialog, open_br34, open_br34_multi
from ui.sites_widget import SitesWidget
from ui.memory_tools import bounded_append, set_bounded_log, soft_gc
from core.deleted_registry import should_skip_deleted_file, has_deleted_record_for_name
from core.paths import BROWSER_PROFILE_DIR, BROWSER_COOKIES_DIR


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
# Parser changes that must rescan only one source without replaying every lane.
# The suffix is part of the internal journal identity, not a visible domain.
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


def _ui_normalize_url(url: str):
    url = (url or "").strip().strip('\"\'')
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if "." in url and not any(ch.isspace() for ch in url):
        return "https://" + url
    return None

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
        self.paused=False
        # Python helper threads inside the site conveyor may outlive the visible
        # Qt page for a short moment during application shutdown.  Do not let
        # them call a deleted QThread C++ object through isInterruptionRequested()
        # or signal.emit(); that was the source of the libshiboken crash.
        self._hard_stop_event = threading.Event()

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

    def _emit_log(self, message):
        return self._safe_emit("log", str(message))

    def _emit_progress(self, value, total):
        return self._safe_emit("progress", int(value), int(total))

    def _emit_current_file(self, path):
        return self._safe_emit("current_file", str(path))

    def _emit_site_current(self, site, status, path):
        return self._safe_emit("site_current", str(site), str(status), str(path or ""))

    def _emit_done(self):
        return self._safe_emit("done")

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
            from core.database.connection import DatabaseWriteBlockedError, set_writes_blocked, writes_blocked_reason
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

        prior_global_status = dict(prior_global_status or {})
        existing_media_map = dict(existing_media_map or {})
        site_done_map = dict(site_done_map or {})
        interval = max(1.10, float(self.settings.get("tagger_site_interval_seconds", 1.10) or 1.10))
        low_power = bool(self.settings.get("tagger_low_power_mode", False))
        window = 1 if low_power else max(2, min(128, int(self.settings.get("tagger_conveyor_window", 32) or 32)))
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
        category_job_key = "flat-sites::tag-groups-v6-html-overlay-primary"
        category_hosts = {"gelbooru.com", "rule34.xxx", "xbooru.com", "hypnohub.net"}
        category_scheduled = set()
        deferred_sauce = {
            str(Path(path)): (Path(path), int(retry_at))
            for path, retry_at, _reason in (restored_saucenao or [])
        }
        active_fallback_tokens = set()
        sauce_wait_notice_for = 0
        next_token = 0
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
        # Make fallback services visible in the activity panel even while they
        # wait for an all-MD5 miss. Previously the table listed only MD5 lanes,
        # so a later SauceNAO/IQDB stall looked invisible to the user.
        if self.settings.get("enable_saucenao") and self.settings.get("saucenao_api_key"):
            self._emit_site_current("SauceNAO", "Ожидает промаха MD5", "")
        if self.settings.get("enable_iqdb"):
            self._emit_site_current("IQDB", "Ожидает промаха MD5", "")
        if self.settings.get("enable_danbooru_iqdb"):
            self._emit_site_current("Danbooru IQDB", "Ожидает промаха MD5", "")
        if self.settings.get("enable_e621_iqdb"):
            self._emit_site_current("e621 IQDB", "Ожидает промаха MD5", "")
        if self.settings.get("enable_ascii2d"):
            self._emit_site_current("Ascii2D", "Ожидает промаха MD5", "")
        if self.settings.get("enable_tineye"):
            self._emit_site_current("TinEye", "Ожидает промаха MD5", "")
        if category_enabled:
            try:
                seeded = seed_background_tag_enrichment(session_settings, job_key=category_job_key, hosts=tuple(sorted(category_hosts)))
                jobs = pending_tag_enrichments(session_settings, job_key=category_job_key)
                for job in jobs:
                    job_id = (str(job.get("original_path", "")), str(job.get("source_url", "")))
                    if job_id not in category_scheduled:
                        category_scheduled.add(job_id)
                        category_q.put(job)
                self._emit_site_current("Категории тегов", f"Фон: в очереди {len(jobs)}", "")
                if seeded or jobs:
                    self._emit_log(f"TAG CATEGORY BACKGROUND: html-overlay repair queued={len(jobs)} backfilled={seeded}; flat API tags are saved first")
            except Exception as e:
                self._emit_log(f"TAG CATEGORY BACKGROUND WARNING: cannot load queue: {e}")

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
                self._emit_site_current(shown, "MD5", str(path))
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
                token, path, sauce_retry_only = item
                _context["file"] = Path(path).name
                _context["retry_only"] = bool(sauce_retry_only)
                self._wait_if_paused_or_delay(0)
                if self.interruption_requested():
                    break
                self._emit_site_current("SauceNAO повтор" if sauce_retry_only else "Запасной поиск", "Ожидает результат", str(path))
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
                            source_host = urlparse(source_url).netloc.lower().replace("www.", "")
                            if source_host not in category_hosts and not (source_host == "api.rule34.xxx" and "rule34.xxx" in category_hosts):
                                continue
                            media_path = str(result_paths_for(session_settings, path, "tagged")["media_file"])
                            job_id = (str(path), source_url)
                            with persist_lock:
                                enqueue_tag_enrichment(session_settings, path, media_path, source_url, job_key=category_job_key)
                            if job_id not in category_scheduled:
                                category_scheduled.add(job_id)
                                category_q.put({"original_path": str(path), "media_path": media_path, "source_url": source_url, "job_key": category_job_key})
                                self._emit_log(f"  TAG CATEGORY BACKGROUND QUEUED [{source_host} fallback]: {path.name}")
                    retry_after = str(local.saucenao_retry_after_epoch()) if result == "retry_saucenao" else ""
                    event_q.put(("fallback", token, result, retry_after, _tineye_tagged_delta, _tineye_source_delta))
                except InterruptedError:
                    if self.interruption_requested():
                        break
                    event_q.put(("fallback", token, "error", "request cancelled", 0, 0))
                except Exception as e:
                    if self.interruption_requested():
                        break
                    event_q.put(("fallback", token, "error", str(e), 0, 0))
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
                self._emit_site_current("rule34.xxx variant", "image-key/SHA1", str(path))
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

        def category_loop():
            if not category_enabled:
                return
            category_settings = dict(session_settings)
            by_host = dict(category_settings.get("http_min_interval_by_host") or {})
            # Category lookup is deliberately low-priority and independent from
            # exact-MD5 lanes. Flat-tag sources collect first and are classified later.
            for background_host in category_hosts:
                by_host[background_host] = max(2.50, interval)
            category_settings["http_min_interval_by_host"] = by_host
            category_settings["_cancel_callback"] = self.interruption_requested
            local = Tagger(category_settings, live_log)
            local.cancel_callback = self.interruption_requested
            while not self.interruption_requested():
                try:
                    job = category_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if job is sentinel:
                    break
                original = Path(str(job.get("original_path") or ""))
                media_text = str(job.get("media_path") or "")
                media = Path(media_text) if media_text else Path("__missing_media__")
                source_url = str(job.get("source_url") or "")
                source_host = urlparse(source_url).netloc.lower().replace("www.", "")
                job_id = (str(original), source_url)
                if not source_url:
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
                    continue
                shown_host = source_host or "источник"
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
                                method="html_category_guarded" if (shown_host in category_hosts or shown_host == "api.rule34.xxx") else "api_category_refine",
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
                        with persist_lock:
                            retry_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, delay_seconds=300, error=str(e))
                        self._emit_log(f"  TAG CATEGORY BACKGROUND RETRY [{shown_host}]: {original.name}: {e}")
                finally:
                    category_scheduled.discard(job_id)

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
            category_thread = threading.Thread(target=_guarded_thread, args=("tag-categories", category_loop), daemon=True, name="site-conveyor-tag-categories")
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
                text = str(state.get("path") or "") + " " + Path(state.get("path")).name + " " + Path(state.get("path")).stem
            except Exception:
                text = str(state.get("path") or "")
            import re as _re
            if not _re.search(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])", text):
                return
            job_id = (str(state["path"]), md5v)
            if job_id in scheduled_rule34_variant_jobs:
                return
            scheduled_rule34_variant_jobs.add(job_id)
            active_rule34_variant_jobs.add(job_id)
            rule34_variant_q.put((job_id, state["path"], md5v))
            self._emit_site_current("rule34.xxx variant", "В очереди", str(state["path"]))
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
            if path_key in deferred_sauce:
                retry_at = int(deferred_sauce[path_key][1])
                left = max(0, retry_at - int(time.time()))
                self._emit_log(
                    f"  SAUCENAO RETRY ALREADY QUEUED: {state['path'].name}; "
                    f"IQDB/Ascii2D not repeated; retry in {left//60}m {left%60}s"
                )
                return False
            active_fallback_tokens.add(token)
            stats["reverse_started"] = int(stats.get("reverse_started", 0) or 0) + 1
            fallback_q.put((token, state["path"], False))
            state["phase"] = "fallback"
            return True

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
            base_site_key = str(lane_site_key.get(lane_key, "")).split("::", 1)[0]
            source_url = str(result.get("source") or "")
            if base_site_key not in category_hosts or not source_url:
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
                    category_q.put({"original_path": str(state["path"]), "media_path": media_path, "source_url": source_url, "job_key": category_job_key})
                    self._emit_log(f"  TAG CATEGORY BACKGROUND QUEUED [{base_site_key}]: {state['path'].name}")
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
                if not state.get("persisted_found"):
                    stats["tagged"] += 1
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
            nonlocal next_token
            if self.interruption_requested():
                return False
            next_token += 1
            token = next_token
            path = Path(path)
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
                }
                self._emit_log(f"[MD5:{path.name}] SITES UP TO DATE")
                if not queue_fallback_or_finish(token, states[token]):
                    event_q.put(("complete", token))
                return True

            self._emit_log(f"SEARCH [MD5]: {path.name}")
            search_img = video_frame_image(path)
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
                lookup_md5 = file_md5(search_img)
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

        def _fill_primary_window():
            nonlocal file_iter_exhausted
            if file_iter_exhausted or self.interruption_requested():
                return
            # Reverse/fallback is intentionally a side queue.  It must not occupy
            # the exact-MD5 conveyor window.  Only throttle when the side queue
            # backlog becomes very large, so we do not keep tens of thousands of
            # file states in RAM while SauceNAO/IQDB are chewing slowly.
            while (
                not file_iter_exhausted
                and _primary_inflight_count() < window
                and len(active_fallback_tokens) < reverse_backlog_limit
                and not self.interruption_requested()
            ):
                try:
                    next_path = next(file_iter)
                except StopIteration:
                    file_iter_exhausted = True
                    break
                if not submit_path(next_path):
                    break

        _fill_primary_window()

        completed = 0
        stopped = False
        while states or deferred_sauce or active_fallback_tokens or active_rule34_variant_jobs or not file_iter_exhausted:
            _fill_primary_window()
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
                    continue
                if state["tags"]:
                    if _maybe_start_variant_site_md5_phase(state):
                        continue
                    # The matching lanes have already saved metadata. At this point
                    # filename-phase misses are also final because at least one source
                    # verified the filename hash or rule34 image-key variant.
                    checkpoint_lane_results(state, finalize_misses=True)
                    if not state.get("persisted_found"):
                        stats["errors"] += 1
                elif state["phase"] == "filename":
                    real_md5 = file_md5(state["search_img"])
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
                    elif queue_fallback_or_finish(token, state):
                        continue
                else:
                    checkpoint_lane_results(state, finalize_misses=True)
                    if state["network_failed"]:
                        stats["deferred_network"] += 1
                        self._emit_log(f"  NETWORK TEMPORARY FAILURE: {state['path'].name} has unfinished site lanes; deferred")
                    elif queue_fallback_or_finish(token, state):
                        continue
                states.pop(token, None)
                completed += 1
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
                    self._emit_site_current("rule34.xxx variant", f"Найдено: {int(tag_count or 0)} тегов", str(job_id[0] if isinstance(job_id, tuple) else ""))
                    self._emit_log(f"[R34-VARIANT:{Path(job_id[0]).name if isinstance(job_id, tuple) else '?'}] DONE tags={int(tag_count or 0)} {source_url}")
                elif error_text:
                    self._emit_site_current("rule34.xxx variant", "Ошибка/нет совпадения", str(job_id[0] if isinstance(job_id, tuple) else ""))
                    self._emit_log(f"[R34-VARIANT:{Path(job_id[0]).name if isinstance(job_id, tuple) else '?'}] DONE no match: {error_text}")
                else:
                    self._emit_site_current("rule34.xxx variant", "Нет совпадения", str(job_id[0] if isinstance(job_id, tuple) else ""))
            elif event[0] == "complete":
                _, token = event
                state = states.pop(token, None)
                if state is None:
                    continue
                completed += 1
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
                    elif result == "nomatch":
                        stats["nomatch"] += 1
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
            category_q.put(sentinel)
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
        if not self._wait_for_db_writes_ready("перед запуском парсера"):
            self._emit_done()
            return
        root=Path(self.settings.get("root",""))
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
                self._emit_log(f"STATUS CHECK DB WARNING: {e}")
            if use_conveyor:
                try:
                    from core.services.scan_state_service import site_scan_status_many
                    site_done_map = site_scan_status_many(self.settings, files, active_site_keys, scan_revision=SITE_SCAN_REVISION)
                except Exception as e:
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
                    f"CATEGORY_DONE={stats.get('category_done',0)}"
                )
                if stopped:
                    self._emit_log("SUMMARY: stopped by user")
                try:
                    from core.parser_power_recovery import mark_parser_session_clean
                    mark_parser_session_clean(self.settings)
                except Exception:
                    pass
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
        try:
            from core.parser_power_recovery import mark_parser_session_clean
            mark_parser_session_clean(self.settings)
        except Exception:
            pass
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


class TaggerPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main; self.worker=None; self.browser_worker=None
        lay=QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(6); split=QSplitter(); lay.addWidget(split,3)
        left=QWidget(); left_lay=QHBoxLayout(left); left_lay.setContentsMargins(0, 0, 6, 0); left_lay.setSpacing(8); self.form_left=QFormLayout(); self.form_right=QFormLayout(); left_lay.addLayout(self.form_left,1); left_lay.addLayout(self.form_right,1); self._form_col=0
        row=QHBoxLayout(); self.root=QLineEdit(); self.choose_btn=QPushButton(); self.choose_btn.clicked.connect(self.choose); row.addWidget(self.root,1); row.addWidget(self.choose_btn)
        self.api=QLineEdit(); self.api.setEchoMode(QLineEdit.Password)
        self.sauce_state=QLabel("Нет данных")
        self.sauce_state.setWordWrap(True)
        self.min_sim=QDoubleSpinBox(); self.min_sim.setRange(50,99); self.min_sim.setSingleStep(0.5)
        self.skip=QCheckBox(); self.only_untagged=QCheckBox(); self.skip_copy_suffix=QCheckBox(); self.md5=QCheckBox(); self.sauce=QCheckBox(); self.ascii2d=QCheckBox()
        self.iqdb=QCheckBox(); self.danbooru_iqdb=QCheckBox(); self.e621_iqdb=QCheckBox(); self.tineye=QCheckBox(); self.low_power=QCheckBox(); self.bg_rule34_categories=QCheckBox()
        # v204: FuzzySearch/Fluffle removed from active parser UI/queue.
        # Hidden legacy widgets remain only so old layouts/settings code cannot crash.
        self.fuzzysearch=QCheckBox(); self.fluffle=QCheckBox()
        self.fuzzy_key=QLineEdit(); self.fuzzy_key.setEchoMode(QLineEdit.Password)
        self.fluffle_key=QLineEdit(); self.fluffle_key.setEchoMode(QLineEdit.Password)
        self.tineye_key=QLineEdit(); self.tineye_key.setEchoMode(QLineEdit.Password)
        # API endpoints are intentionally not exposed in the main parser form.
        # They are stable service defaults; the clickable service name opens the
        # key/API page instead of wasting UI rows on raw URLs.
        self.fuzzy_endpoint=QLineEdit(); self.fluffle_endpoint=QLineEdit()
        self.api.setPlaceholderText("API key")
        self.fuzzy_key.setPlaceholderText("API key не нужен")
        self.fuzzy_key.setVisible(False)
        self.fluffle_key.setPlaceholderText("API key не нужен")
        self.fluffle_key.setVisible(False)
        self.tineye_key.setPlaceholderText("TinEye API key")
        self.tineye_key.setVisible(False)
        self.site_interval=QDoubleSpinBox(); self.site_interval.setRange(1.10, 30.0); self.site_interval.setDecimals(2); self.site_interval.setSingleStep(0.10); self.site_interval.setSuffix(" с")
        self.conveyor_window=QSpinBox(); self.conveyor_window.setRange(2,128)
        # Keep bare indicators compact, but leave enough room for QSS borders.
        # Old fixedWidth(20) clipped 17px indicators with 2px borders in dark themes.
        for _cb in [self.skip,self.only_untagged,self.skip_copy_suffix,self.md5,
                    self.sauce,self.ascii2d,self.iqdb,self.danbooru_iqdb,self.e621_iqdb,self.tineye,self.low_power,self.bg_rule34_categories]:
            _cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            _cb.setFixedSize(23, 23)
        self.iqdb_min=QDoubleSpinBox(); self.iqdb_min.setRange(50,99); self.iqdb_min.setSingleStep(0.5)
        self.delay=QDoubleSpinBox(); self.delay.setRange(0,120); self.limit=QSpinBox(); self.limit.setRange(0,1000000); self.req_timeout=QSpinBox(); self.req_timeout.setRange(5,300); self.sauce_cooldown=QSpinBox(); self.sauce_cooldown.setRange(1,1440)
        self.form_rows=[]
        self.add_tip_row("Folder", row, "tip_root")
        self.add_api_service_row("SauceNAO", self.sauce, self.api, "tip_sauce_service", "saucenao")
        self.add_api_service_row("Danbooru IQDB", self.danbooru_iqdb, None, "tip_danbooru_iqdb", "danbooru_iqdb")
        self.add_api_service_row("TinEye", self.tineye, None, "tip_tineye", "tineye")
        for label,w,tip in [("SauceNAO состояние",self.sauce_state,"tip_saucenao_state"),("SauceNAO min similarity",self.min_sim,"tip_min_similarity"),("MD5 lookup",self.md5,"tip_md5"),("IQDB fuzzy fallback",self.iqdb,"tip_iqdb"),("e621 IQDB fallback",self.e621_iqdb,"tip_e621_iqdb"),("IQDB min similarity",self.iqdb_min,"tip_iqdb"),("Ascii2D fallback",self.ascii2d,"tip_ascii2d"),("Skip existing",self.skip,"tip_skip"),("Tag only untagged",self.only_untagged,"tip_only_untagged"),("Skip files ending (1)/(2)",self.skip_copy_suffix,"tip_skip_copy_suffix"),("Background tag groups",self.bg_rule34_categories,"tip_background_groups"),("Low power mode",self.low_power,"tip_low_power"),("Site interval",self.site_interval,"tip_site_interval"),("Conveyor window",self.conveyor_window,"tip_conveyor_window"),("Delay",self.delay,"tip_delay"),("Request timeout",self.req_timeout,"tip_delay"),("Sauce cooldown min",self.sauce_cooldown,"tip_sauce"),("Limit",self.limit,"tip_limit"),]: self.add_tip_row(label,w,tip)
        split.addWidget(left)
        right=QWidget(); rlay=QVBoxLayout(right); rlay.setContentsMargins(6, 0, 0, 0); rlay.setSpacing(4)
        self.sites_widget = SitesWidget()
        # Действия выбранного сайта открываются через контекстное меню таблицы.
        # Настройки всей страницы сохраняются единой нижней кнопкой страницы.
        self.sites_widget.login_selected_requested.connect(self.open_selected_login)
        self.sites_widget.all_login_btn.clicked.connect(self.open_all_logins)
        rlay.addWidget(self.sites_widget)
        split.addWidget(right); split.setSizes([520,820])
        row2=QHBoxLayout(); row2.setContentsMargins(0, 0, 0, 0); row2.setSpacing(6); self.save_btn=QPushButton(); self.save_btn.clicked.connect(self.sync); self.start=QPushButton(); self.start.setObjectName("ParserStartButton"); self.start.clicked.connect(self.run); self.pause_btn=QPushButton("PAUSE"); self.pause_btn.setObjectName("ParserPauseButton"); self.pause_btn.setCheckable(True); self.pause_btn.clicked.connect(self.pause_resume); self.pause_btn.setEnabled(False); self.stop_btn = QPushButton(); self.stop_btn.setObjectName("ParserStopButton"); self.stop_btn.clicked.connect(self.stop); self.stop_btn.setEnabled(False);
        for _btn in (self.save_btn, self.start, self.pause_btn, self.stop_btn):
            _btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row2.addWidget(_btn, 1)
        lay.addLayout(row2)
        self.progress=QProgressBar(); lay.addWidget(self.progress)
        self.console_preview_split = QSplitter(Qt.Horizontal)
        self._log_channel_buffers = {}
        self._log_channel_widgets = {}
        self._log_channel_meta = {}
        self._active_log_channels = []
        self._active_log_channel_set = set()
        self.console_panel = self._build_console_panel()
        self.preview_box=QLabel("Preview"); self.preview_box.setAlignment(Qt.AlignCenter); self.preview_box.setMinimumWidth(240)
        self.preview_box.setStyleSheet("border:1px solid #2f3541;border-radius:8px;")
        self.console_preview_split.addWidget(self.console_panel); self.console_preview_split.addWidget(self.preview_box)
        self.console_preview_split.setSizes([1180,250])
        lay.addWidget(self.console_preview_split,2)
        self._last_site_table = None
        self._last_site_row = -1
        self.low_power.toggled.connect(self.update_preview_visibility)
        self.load_values(); self.retranslate(); self.update_preview_visibility()

    def _build_console_panel(self):
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self.console_mode_buttons = []
        for idx, text in enumerate(("Общий", "Сетка", "Один сайт")):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, i=idx: self.set_console_mode(i))
            self.console_mode_buttons.append(btn)
            bar.addWidget(btn)
        self.single_log_combo = QComboBox()
        self.single_log_combo.currentTextChanged.connect(lambda _text: self._refresh_single_log_view())
        bar.addWidget(QLabel("Канал:"))
        bar.addWidget(self.single_log_combo, 1)
        outer.addLayout(bar)

        self.console_stack = QStackedWidget()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        set_bounded_log(self.log, int(self.main.settings.get("max_console_lines", 2500)))

        self.site_activity_table=QTableWidget(0,4)
        self.site_activity_table.setHorizontalHeaderLabels(["Сайт", "Состояние", "MD5", "Текущий файл"])
        self.site_activity_table.verticalHeader().setVisible(False)
        self.site_activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.site_activity_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.site_activity_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.site_activity_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.site_activity_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents)
        self.site_activity_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        self.site_activity_table.setMinimumWidth(520)
        self.site_activity_table.setMinimumHeight(28)
        self._site_activity_rows={}; self._site_activity_paths={}; self._site_activity_preview_labels={}
        self._site_activity_md5_by_name={}; self._site_activity_md5_by_path={}; self._site_activity_name_to_sites={}; self._site_activity_current_name_by_site={}

        # Общий режим: консоль и состояние должны быть рядом, а не
        # "состояние сверху / консоль снизу". Таблица состояния остаётся
        # сжимаемой обычным splitter'ом, поэтому отдельная кнопка скрытия
        # не нужна: потянул разделитель вправо — статус почти пропал.
        self.general_console_split = QSplitter(Qt.Horizontal)
        self.general_console_split.setChildrenCollapsible(True)
        self.log.setMinimumWidth(360)
        self.general_console_split.addWidget(self.log)
        self.general_console_split.addWidget(self.site_activity_table)
        self.general_console_split.setSizes([900, 360])
        self.console_stack.addWidget(self.general_console_split)

        self.log_grid_scroll = QScrollArea()
        self.log_grid_scroll.setWidgetResizable(True)
        self.log_grid_widget = QWidget()
        self.log_grid = QGridLayout(self.log_grid_widget)
        self.log_grid.setContentsMargins(0, 0, 0, 0)
        self.log_grid.setSpacing(6)
        self.log_grid_scroll.setWidget(self.log_grid_widget)
        self.console_stack.addWidget(self.log_grid_scroll)

        self.single_log = QPlainTextEdit()
        self.single_log.setReadOnly(True)
        set_bounded_log(self.single_log, int(self.main.settings.get("max_console_lines", 2500)))
        self.console_stack.addWidget(self.single_log)

        outer.addWidget(self.console_stack, 1)
        self.set_console_mode(int(self.main.settings.get("tagger_console_mode", 0) or 0), save=False)
        self._prepare_log_channels(reset=True)
        return panel

    def set_console_mode(self, index: int, save: bool = True):
        try:
            index = int(index)
        except Exception:
            index = 0
        # v259: отдельного режима «Статус» больше нет — таблица статусов
        # встроена в общий режим и сжимается обычным splitter'ом. Старое
        # сохранённое значение 3 мягко возвращаем в общий режим.
        if index >= 3:
            index = 0
        index = max(0, min(2, index))
        try:
            self.console_stack.setCurrentIndex(index)
            for i, btn in enumerate(getattr(self, "console_mode_buttons", [])):
                btn.setChecked(i == index)
            self.single_log_combo.setVisible(index == 2)
            if save:
                self.main.settings["tagger_console_mode"] = index
                save_settings(self.main.settings)
            if index == 2:
                self._refresh_single_log_view()
        except Exception:
            pass

    def _enabled_log_channels(self):
        channels = []
        seen = {}
        try:
            tagger = Tagger(self.main.settings, lambda _m: None)
            for site in tagger._all_enabled_site_configs():
                label = tagger._site_label(site)
                seen[label] = seen.get(label, 0) + 1
                shown = label if seen[label] == 1 else f"{label} ({seen[label]})"
                channels.append(shown)
        except Exception:
            try:
                sites = self.main.settings.get("sites", {}) if isinstance(self.main.settings, dict) else {}
                if isinstance(sites, dict):
                    for domain, cfg in sites.items():
                        if isinstance(cfg, dict) and bool(cfg.get("enabled", True)):
                            channels.append(str(cfg.get("name") or cfg.get("domain") or domain))
            except Exception:
                pass
        if bool(self.main.settings.get("enable_iqdb", True)):
            channels.append("IQDB")
        if bool(self.main.settings.get("enable_danbooru_iqdb", False)):
            channels.append("Danbooru IQDB")
        if bool(self.main.settings.get("enable_e621_iqdb", True)):
            channels.append("e621 IQDB")
        if bool(self.main.settings.get("enable_ascii2d", False)):
            channels.append("Ascii2D")
        if bool(self.main.settings.get("enable_saucenao", True)) and str(self.main.settings.get("saucenao_api_key") or "").strip():
            channels.append("SauceNAO")
        if bool(self.main.settings.get("enable_tineye", False)):
            channels.append("TinEye")
        if any(ch in channels for ch in ("IQDB", "Danbooru IQDB", "e621 IQDB", "Ascii2D", "TinEye", "SauceNAO")):
            channels.append("Запасной поиск")
        if bool(self.main.settings.get("tagger_background_tag_groups", self.main.settings.get("tagger_background_rule34_categories", True))):
            channels.append("Категории тегов")
        out = []
        used = set()
        for ch in channels:
            ch = str(ch or "").strip()
            if ch and ch not in used:
                out.append(ch); used.add(ch)
        return out

    def _prepare_log_channels(self, reset: bool = False):
        if reset:
            self._log_channel_buffers.clear()
            self._log_channel_widgets.clear()
            self._log_channel_meta.clear()
            try:
                self.log.clear()
                self.single_log.clear()
            except Exception:
                pass
        self._active_log_channels = self._enabled_log_channels()
        self._active_log_channel_set = set(self._active_log_channels)
        for channel in self._active_log_channels:
            self._ensure_log_channel(channel)
        self._rebuild_log_grid()
        self._refresh_channel_combo()
        self._reset_activity_rows()
        for channel in self._active_log_channels:
            self.update_site_activity(channel, "Ожидает", "")

    def _ensure_log_channel(self, channel: str):
        channel = str(channel)
        self._log_channel_buffers.setdefault(channel, [])
        if channel in self._log_channel_widgets:
            return self._log_channel_widgets[channel]
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        header = QLabel(channel)
        header.setStyleSheet("font-weight:700; padding:3px 6px; border:1px solid #2f3541; border-radius:5px;")
        status = QLabel("Ожидает")
        status.setStyleSheet("padding:1px 6px;")
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setMinimumHeight(140)
        set_bounded_log(edit, int(self.main.settings.get("max_console_lines", 2500)))
        lay.addWidget(header)
        lay.addWidget(status)
        lay.addWidget(edit, 1)
        self._log_channel_widgets[channel] = box
        self._log_channel_meta[channel] = {"header": header, "status": status, "log": edit}
        return box

    def _rebuild_log_grid(self):
        try:
            while self.log_grid.count():
                item = self.log_grid.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
            n = max(1, len(self._active_log_channels))
            if n <= 1:
                cols = 1
            elif n <= 4:
                cols = 2
            elif n <= 6:
                cols = 3
            else:
                cols = 3
            for i, channel in enumerate(self._active_log_channels):
                self.log_grid.addWidget(self._ensure_log_channel(channel), i // cols, i % cols)
            self.log_grid.setColumnStretch(cols, 0)
        except Exception:
            pass

    def _refresh_channel_combo(self):
        try:
            current = self.single_log_combo.currentText()
            self.single_log_combo.blockSignals(True)
            self.single_log_combo.clear()
            self.single_log_combo.addItems(self._active_log_channels)
            if current in self._active_log_channel_set:
                self.single_log_combo.setCurrentText(current)
            elif self._active_log_channels:
                self.single_log_combo.setCurrentIndex(0)
            self.single_log_combo.blockSignals(False)
            self._refresh_single_log_view()
        except Exception:
            pass

    def _reset_activity_rows(self):
        try:
            self.site_activity_table.setRowCount(0)
        except Exception:
            pass
        self._site_activity_rows = {}
        self._site_activity_paths = {}
        self._site_activity_preview_labels = {}
        self._site_activity_md5_by_name = {}
        self._site_activity_md5_by_path = {}
        self._site_activity_name_to_sites = {}
        self._site_activity_current_name_by_site = {}

    def _classify_log_channel(self, text: str):
        text = str(text or "")
        if text.startswith("[MD5:"):
            rest = text[5:]
            parts = rest.split(":", 2)
            if parts:
                return parts[0]
        upper = text.upper()
        if "DANBOORU IQDB" in upper:
            return "Danbooru IQDB"
        if "E621 IQDB" in upper:
            return "e621 IQDB"
        if "TINEYE" in upper:
            return "TinEye"
        if "SAUCENAO" in upper or "SAUCE" in upper:
            return "SauceNAO"
        if "ASCII2D" in upper:
            return "Ascii2D"
        if "IQDB" in upper:
            return "IQDB"
        if text.startswith("[REVERSE:") or text.startswith("[SAUCENAO-RETRY:"):
            return "Запасной поиск"
        if "TAG CATEGORY" in upper or "КАТЕГОР" in upper:
            return "Категории тегов"
        return None

    def _append_channel_log(self, channel: str, text: str):
        channel = str(channel or "").strip()
        if not channel or channel not in self._active_log_channel_set:
            return
        self._ensure_log_channel(channel)
        limit = int(self.main.settings.get("max_console_lines", 2500))
        buf = self._log_channel_buffers.setdefault(channel, [])
        buf.append(str(text))
        if len(buf) > limit:
            del buf[:len(buf)-limit]
        meta = self._log_channel_meta.get(channel) or {}
        edit = meta.get("log")
        if edit is not None:
            bounded_append(edit, text, limit)
        if self.console_stack.currentIndex() == 2 and self.single_log_combo.currentText() == channel:
            bounded_append(self.single_log, text, limit)

    def _refresh_single_log_view(self):
        try:
            channel = self.single_log_combo.currentText()
            self.single_log.clear()
            self.single_log.setPlainText("\n".join(self._log_channel_buffers.get(channel, [])))
            self.single_log.moveCursor(self.single_log.textCursor().End)
        except Exception:
            pass

    def _set_log_channel_status(self, channel: str, status: str, path: str = ""):
        channel = str(channel or "").strip()
        if channel not in self._active_log_channel_set:
            return
        self._ensure_log_channel(channel)
        meta = self._log_channel_meta.get(channel) or {}
        label = meta.get("status")
        if label is not None:
            name = Path(str(path)).name if path else "—"
            label.setText(f"{status} · {name}" if name != "—" else str(status))
            label.setToolTip(str(path or ""))


    def add_tip_row(self, label_key, widget, tip_key):
        lab = QLabel(self.main.t(label_key) + "  ?")
        lab.setToolTip(self.main.t(tip_key))
        _tc2 = self.main.settings.get("appearance","abyss") if hasattr(self,"main") else "abyss"
        _lmap = {"light": ("#1a1c2a","#5060d0"), "r34": ("#111111","#3a7a35"), "r34dark": ("#d6e4d3","#7fb06f"),
                 "win95": ("#000000","#000080"), "windows95": ("#000000","#000080"),
                 "ph": ("#f5f5f5","#ff9000"), "pornhub": ("#f5f5f5","#ff9000"),
                 "dark": ("#c0c8e0","#6c85e0"), "abyss": ("#c0c8e0","#6c85e0"),
                 "ember": ("#c8b090","#c87040"), "slate": ("#b0c8d0","#5a8a9f"),
                 "sakura": ("#e0b0d0","#d060a0")}
        _lc2, _hc2 = _lmap.get(_tc2, ("#c0c8e0","#6c85e0"))
        lab.setStyleSheet(f"QLabel{{font-weight:700;color:{_lc2};}} QLabel:hover{{color:{_hc2};}}")

        if hasattr(widget, "setToolTip"):
            widget.setToolTip(self.main.t(tip_key))

        target_form = self.form_left if getattr(self, "_form_col", 0) % 2 == 0 else self.form_right
        target_form.addRow(lab, widget)
        self._form_col = getattr(self, "_form_col", 0) + 1
        self.form_rows.append((lab, label_key, widget, tip_key))


    def add_api_service_row(self, label_key, checkbox, key_widget, tip_key, service):
        """Add compact service row: clickable name + enable checkbox + optional API key field.

        Public services such as Fluffle do not show a useless key field; docs/API
        URLs are baked into the clickable service name instead of wasting rows.
        """
        lab = QLabel(self.main.t(label_key))
        lab.setToolTip(self.main.t(tip_key))
        lab.setCursor(Qt.PointingHandCursor)
        lab.mousePressEvent = lambda _event, svc=service: self.open_external_api_doc(svc)
        _tc2 = self.main.settings.get("appearance","abyss") if hasattr(self,"main") else "abyss"
        _lmap = {"light": ("#1a1c2a","#5060d0"), "r34": ("#111111","#3a7a35"), "r34dark": ("#d6e4d3","#7fb06f"),
                 "win95": ("#000000","#000080"), "windows95": ("#000000","#000080"),
                 "ph": ("#f5f5f5","#ff9000"), "pornhub": ("#f5f5f5","#ff9000"),
                 "dark": ("#c0c8e0","#6c85e0"), "abyss": ("#c0c8e0","#6c85e0"),
                 "ember": ("#c8b090","#c87040"), "slate": ("#b0c8d0","#5a8a9f"),
                 "sakura": ("#e0b0d0","#d060a0")}
        _lc2, _hc2 = _lmap.get(_tc2, ("#c0c8e0","#6c85e0"))
        lab.setStyleSheet(f"QLabel{{font-weight:700;color:{_lc2};text-decoration:underline;}} QLabel:hover{{color:{_hc2};}}")

        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(checkbox, 0)
        if key_widget is not None:
            lay.addWidget(key_widget, 1)
        else:
            lay.addStretch(1)
        row.setToolTip(self.main.t(tip_key))
        if hasattr(checkbox, "setToolTip"):
            checkbox.setToolTip(self.main.t(tip_key))
        if key_widget is not None and hasattr(key_widget, "setToolTip"):
            key_widget.setToolTip(self.main.t(tip_key))

        target_form = self.form_left if getattr(self, "_form_col", 0) % 2 == 0 else self.form_right
        target_form.addRow(lab, row)
        self._form_col = getattr(self, "_form_col", 0) + 1
        self.form_rows.append((lab, label_key, row, tip_key))
        if not hasattr(self, "_api_service_rows"):
            self._api_service_rows = []
        self._api_service_rows.append((lab, label_key, row, tip_key, service, checkbox, key_widget))


    def apply_theme_style(self, theme_name: str | None = None):
        """Refresh parser page inline label styles after runtime theme switch."""
        theme_name = theme_name or self.main.settings.get("appearance", "abyss")
        colors = {
            "light": ("#1a1c2a", "#5060d0"),
            "r34": ("#111111", "#3a7a35"),
            "r34dark": ("#d6e4d3", "#7fb06f"),
            "win95": ("#000000", "#000080"),
            "windows95": ("#000000", "#000080"),
            "ph": ("#f5f5f5", "#ff9000"),
            "pornhub": ("#f5f5f5", "#ff9000"),
            "dark": ("#c0c8e0", "#6c85e0"),
            "abyss": ("#c0c8e0", "#6c85e0"),
            "ember": ("#c8b090", "#c87040"),
            "slate": ("#b0c8d0", "#5a8a9f"),
            "sakura": ("#e0b0d0", "#d060a0"),
        }
        fg, hover = colors.get(theme_name, colors["abyss"])
        try:
            for lab, label_key, widget, tip_key in getattr(self, "form_rows", []):
                lab.setStyleSheet(f"QLabel{{font-weight:700;color:{fg};background:transparent;}} QLabel:hover{{color:{hover};}}")
            for lab, *_rest in getattr(self, "_api_service_rows", []):
                lab.setStyleSheet(f"QLabel{{font-weight:700;color:{fg};background:transparent;text-decoration:underline;}} QLabel:hover{{color:{hover};}}")
        except Exception:
            pass
        try:
            if theme_name in ("win95", "windows95"):
                self.preview_box.setStyleSheet("border-top:2px solid #808080;border-left:2px solid #808080;border-bottom:2px solid #ffffff;border-right:2px solid #ffffff;border-radius:0px;background:#c0c0c0;color:#000000;")
            elif theme_name == "r34":
                self.preview_box.setStyleSheet("border:1px solid #6da36b;border-radius:0px;background:#b7e2af;color:#111111;")
            elif theme_name == "r34dark":
                self.preview_box.setStyleSheet("border:1px solid #345032;border-radius:0px;background:#171e15;color:#d6e4d3;")
            else:
                self.preview_box.setStyleSheet("border:1px solid #2f3541;border-radius:8px;")
        except Exception:
            pass

    def append_log(self, msg):
        _text = str(msg)
        self._observe_md5_log_line(_text)
        bounded_append(self.log, _text, int(self.main.settings.get("max_console_lines", 2500)))
        channel = self._classify_log_channel(_text)
        if channel:
            self._append_channel_log(channel, _text)
        if "SAUCENAO LIMITS:" in _text or "SAUCENAO COOLDOWN" in _text or "SAUCENAO RETRY QUEUED" in _text:
            self.refresh_saucenao_state()

    def trim_ui_memory(self):
        try:
            from PySide6.QtGui import QPixmapCache
            QPixmapCache.clear()
        except Exception:
            pass
        soft_gc()

    def bool_item(self, checked):
        it=QTableWidgetItem(); it.setFlags(it.flags()|Qt.ItemIsUserCheckable); it.setCheckState(Qt.Checked if checked else Qt.Unchecked); return it
    def load_values(self):
        s=self.main.settings; self.root.setText(s.get("root","C:/Local_Booru_Input")); self.api.setText(s.get("saucenao_api_key","")); self.min_sim.setValue(float(s.get("min_similarity",85))); self.skip.setChecked(bool(s.get("skip_existing",True))); self.only_untagged.setChecked(bool(s.get("tag_only_untagged",True))); self.skip_copy_suffix.setChecked(bool(s.get("skip_copy_suffix_files",True))); self.md5.setChecked(bool(s.get("enable_md5_lookup",True))); self.sauce.setChecked(bool(s.get("enable_saucenao",True))); self.ascii2d.setChecked(s.get("enable_ascii2d",False))
        self.iqdb.setChecked(bool(s.get("enable_iqdb",True))); self.danbooru_iqdb.setChecked(bool(s.get("enable_danbooru_iqdb",False))); self.e621_iqdb.setChecked(bool(s.get("enable_e621_iqdb",True))); self.fuzzysearch.setChecked(False); self.fluffle.setChecked(False); self.tineye.setChecked(bool(s.get("enable_tineye",False))); self.fuzzy_key.setText(""); self.fluffle_key.setText(""); self.fuzzy_endpoint.setText(""); self.fluffle_endpoint.setText(""); self.iqdb_min.setValue(float(s.get("iqdb_min_similarity",75))); self.delay.setValue(float(s.get("delay_seconds",8))); self.req_timeout.setValue(int(float(s.get("request_timeout_seconds",20)))); self.sauce_cooldown.setValue(int(float(s.get("saucenao_cooldown_seconds",3600))/60)); self.limit.setValue(int(s.get("limit_files",0)))
        self.bg_rule34_categories.setChecked(bool(s.get("tagger_background_tag_groups", s.get("tagger_background_rule34_categories", True)))); self.low_power.setChecked(bool(s.get("tagger_low_power_mode", False))); self.site_interval.setValue(max(1.10, float(s.get("tagger_site_interval_seconds", 1.10) or 1.10))); self.conveyor_window.setValue(int(s.get("tagger_conveyor_window",32) or 32)); self.update_preview_visibility()
        self.sites_widget.load(s)
        self.refresh_saucenao_state()

    def refresh_saucenao_state(self):
        try:
            from core.services.service_state import get_cooldown
            state = get_cooldown(self.main.settings, "saucenao")
            now = int(time.time())
            left = max(0, int(state.get("cooldown_until", 0) or 0) - now)
            _short = state.get("short_remaining", -1)
            _long = state.get("long_remaining", -1)
            short_rem = int(_short if _short is not None else -1)
            long_rem = int(_long if _long is not None else -1)
            counters = []
            if short_rem >= 0:
                counters.append(f"короткий: {short_rem}")
            if long_rem >= 0:
                counters.append(f"сутки: {long_rem}")
            quota = " · ".join(counters) if counters else "лимит ещё не получен"
            if left:
                status = f"пауза ещё {left//60}м {left%60}с"
            else:
                status = "готов"
            self.sauce_state.setText(f"{quota}; {status}")
        except Exception:
            self.sauce_state.setText("Состояние станет доступно после запроса")

    def retranslate(self):
        t=self.main.t; self.choose_btn.setText(t("Choose")); self.save_btn.setText(t("Save settings")); self.start.setText(t("START")); self.pause_btn.setText(t("RESUME") if self.pause_btn.isChecked() else t("PAUSE")); self.stop_btn.setText(t("STOP")); self.apply_tips()
        api_labs = {id(row[0]) for row in getattr(self, "_api_service_rows", [])}
        for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
            lab.setText(t(label_key) if id(lab) in api_labs else t(label_key) + "  ?")
            lab.setToolTip(t(tip_key))

            if hasattr(w, "setToolTip"):
                w.setToolTip(t(tip_key))
        for lab, label_key, row, tip_key, service, checkbox, key_widget in getattr(self, "_api_service_rows", []):
            lab.mousePressEvent = lambda _event, svc=service: self.open_external_api_doc(svc)
            if hasattr(checkbox, "setToolTip"):
                checkbox.setToolTip(t(tip_key))
            if hasattr(key_widget, "setToolTip"):
                key_widget.setToolTip(t(tip_key))
    def apply_tips(self):
        t=self.main.t
        pairs=[(self.root,"tip_root"),(self.api,"tip_sauce_service"),(self.sauce_state,"tip_saucenao_state"),(self.min_sim,"tip_min_similarity"),(self.md5,"tip_md5"),(self.sauce,"tip_sauce_service"),(self.iqdb,"tip_iqdb"),(self.danbooru_iqdb,"tip_danbooru_iqdb"),(self.e621_iqdb,"tip_e621_iqdb"),(self.tineye,"tip_tineye"),(self.tineye_key,"tip_tineye"),(self.delay,"tip_delay"),(self.req_timeout,"tip_delay"),(self.sauce_cooldown,"tip_sauce"),(self.limit,"tip_limit"),(self.skip,"tip_skip"),(self.only_untagged,"tip_only_untagged"),(self.skip_copy_suffix,"tip_skip_copy_suffix"),(self.bg_rule34_categories,"tip_background_groups"),(self.low_power,"tip_low_power"),(self.site_interval,"tip_site_interval"),(self.conveyor_window,"tip_conveyor_window")]
        for w,k in pairs: w.setToolTip(t(k))

    def _table_clicked(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def clear_site_selection(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def _selected_rows_for_table(self, table):
        rows = sorted({i.row() for i in table.selectedIndexes()})
        if rows:
            return rows
        row = table.currentRow()
        return [row] if row >= 0 else []

    def selected_login_urls(self):
        return self.sites_widget.selected_login_urls()

    def open_login_browser(self, url, sync_first=True):
        if sync_first:
            self.sync(show_message=False)
        norm = _ui_normalize_url(url)
        if not norm:
            self.append_log(f"SKIP INVALID LOGIN URL: {url!r}")
            return
        self.append_log(f"OPEN APP LOGIN BROWSER: {norm}")
        open_br34(norm, self, log_func=self.append_log)
        self.append_log("br34 OPENED / TAB ADDED")

    def open_selected_login(self):
        urls = self.selected_login_urls()
        if not urls:
            self.append_log("NO LOGIN URL SELECTED")
            return
        self.sync(show_message=False)
        normed = [_ui_normalize_url(u) for u in urls]
        normed = [u for u in normed if u]
        if not normed:
            self.append_log("NO VALID LOGIN URLs")
            return
        for u in normed:
            self.append_log(f"OPEN APP LOGIN BROWSER: {u}")
        open_br34_multi(normed, parent=self, log_func=self.append_log)
        self.append_log("br34 OPENED / ALL TABS ADDED")

    def open_all_logins(self):
        self.sync(show_message=False)
        urls = self.sites_widget.all_enabled_login_urls()
        if not urls:
            self.append_log("NO LOGIN URLS")
            return
        self.append_log(f"OPENING {len(urls)} LOGIN URLs IN br34 (all as tabs)")
        for i, u in enumerate(urls, 1):
            self.append_log(f"LOGIN URL {i}: {u}")
        open_br34_multi(urls, parent=self, log_func=self.append_log)
        self.append_log("br34 OPENED / ALL TABS ADDED")

    def browser_login(self):
        self.open_selected_login()


    def open_external_api_doc(self, service):
        service = str(service or "").lower().strip()
        if service == "saucenao":
            url = str(self.main.settings.get("saucenao_api_docs_url", "https://saucenao.com/user.php?page=search-api") or "https://saucenao.com/user.php?page=search-api")
        elif service == "danbooru_iqdb":
            url = str(self.main.settings.get("danbooru_iqdb_docs_url", "https://danbooru.iqdb.org/") or "https://danbooru.iqdb.org/")
        elif service == "tineye":
            url = "https://tineye.com/"
        else:
            url = ""
        if url:
            webbrowser.open(url)
            self.append_log(f"OPEN API DOCS: {url}")

    def choose(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.root.text())
        if f: self.root.setText(f)
    def add_custom(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def delete_custom(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def sync(self, show_message=True):
        s = self.main.settings
        s["root"] = self.root.text()
        s["saucenao_api_key"] = self.api.text()
        s["min_similarity"] = self.min_sim.value()
        s["skip_existing"] = self.skip.isChecked()
        s["tag_only_untagged"] = self.only_untagged.isChecked()
        s["skip_copy_suffix_files"] = self.skip_copy_suffix.isChecked()
        
        s.pop("mark_no_match", None)
        s["enable_md5_lookup"] = self.md5.isChecked()
        s["enable_saucenao"] = self.sauce.isChecked()
        s["enable_iqdb"] = self.iqdb.isChecked()
        s["enable_danbooru_iqdb"] = self.danbooru_iqdb.isChecked()
        s["enable_e621_iqdb"] = self.e621_iqdb.isChecked()
        # v204: Fluffle/FuzzySearch are removed. Purge legacy settings so old
        # configs cannot silently re-enable them.
        for _removed_key in (
            "enable_fuzzysearch", "fuzzysearch_api_key", "fuzzysearch_endpoint",
            "fuzzysearch_api_docs_url", "fuzzysearch_max_results",
            "enable_fluffle", "fluffle_api_key", "fluffle_endpoint",
            "fluffle_api_docs_url", "fluffle_max_results",
        ):
            s.pop(_removed_key, None)
        self.fuzzysearch.setChecked(False)
        self.fluffle.setChecked(False)
        s["enable_tineye"] = self.tineye.isChecked()
        # TinEye uses web scraping - no API key needed
        s["enable_ascii2d"] = self.ascii2d.isChecked()
        # ascii2d has no public API
        s.pop("tagger_site_conveyor_enabled", None)  # conveyor is fixed architecture
        s["tagger_background_tag_groups"] = self.bg_rule34_categories.isChecked()
        s["tagger_background_rule34_categories"] = self.bg_rule34_categories.isChecked()  # backward compatibility
        s["tagger_low_power_mode"] = self.low_power.isChecked()
        self.update_preview_visibility()
        s["tagger_site_interval_seconds"] = max(1.10, float(self.site_interval.value()))
        s["tagger_conveyor_window"] = int(self.conveyor_window.value())
        s["iqdb_min_similarity"] = self.iqdb_min.value()
        s["delay_seconds"] = self.delay.value()
        s["request_timeout_seconds"] = self.req_timeout.value()
        s["saucenao_cooldown_seconds"] = int(self.sauce_cooldown.value()) * 60
        s["limit_files"] = self.limit.value()
        # Retired live sidecar/cookie-mode controls are not part of the parser UI.
        for _retired in ("output_suffix", "sources_suffix", "tags_suffix", "use_browser_auth", "use_system_browser_cookies", "browser_auth_wait_seconds"):
            s.pop(_retired, None)

        collected_sites, collected_custom = self.sites_widget.collect()

        # Preserve hidden/advanced keys that the UI table does not expose
        # (api format, endpoints, parser mode, custom md5/tag settings, etc.).
        old_sites = s.get("sites") if isinstance(s.get("sites"), dict) else {}
        merged_sites = {}
        for domain, cfg in collected_sites.items():
            old = old_sites.get(domain, {}) if isinstance(old_sites.get(domain, {}), dict) else {}
            merged_sites[domain] = {**old, **cfg}

        old_custom_list = s.get("custom_sites") if isinstance(s.get("custom_sites"), list) else []
        old_custom = {}
        for item in old_custom_list:
            if isinstance(item, dict):
                key = (item.get("domain") or item.get("base_url") or item.get("name") or "").strip()
                if key:
                    old_custom[key] = item
        merged_custom = []
        for cfg in collected_custom:
            key = (cfg.get("domain") or cfg.get("base_url") or cfg.get("name") or "").strip()
            old = old_custom.get(key, {}) if isinstance(old_custom.get(key, {}), dict) else {}
            merged_custom.append({**old, **cfg})

        s["sites"] = merged_sites
        s["custom_sites"] = merged_custom
        # v131 site-manager metadata: presets may be removed and neutral view
        # preserves the manual drag-and-drop order used by the parser lanes.
        s["deleted_builtin_sites"] = self.sites_widget.deleted_builtin_sites()
        s["site_manual_order"] = self.sites_widget.manual_order()
        save_settings(s)
        if show_message:
            QMessageBox.information(self, self.main.t("Saved"), self.main.t("Settings saved"))
    def run(self):
        self.sync(show_message=False)
        self._prepare_log_channels(reset=True)
        self.start.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setChecked(False)
        self.stop_btn.setEnabled(True)
        try:
            self.main.settings["_parser_running"] = True
        except Exception:
            pass

        self.update_preview_visibility()
        # Warn if DB is still in safe mode after a crash
        try:
            from core.database.connection import writes_blocked, writes_blocked_reason
            if writes_blocked():
                self.append_log(f"  WARN: SQLite в безопасном режиме ({writes_blocked_reason()})")
                self.append_log("  Checkpoints будут пропущены пока фоновая проверка не завершится.")
                self.append_log("  Рекомендуется подождать 15-20 сек после запуска программы.")
        except Exception:
            pass
        try:
            from core.database.connection import writes_blocked
            if writes_blocked():
                self.append_log("  WARN: SQLite в безопасном режиме — checkpoints пропущены до завершения проверки")
        except Exception:
            pass
        self.worker = TaggerWorker(self.main.settings)
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_worker_progress)
        self.worker.current_file.connect(self.show_current_preview)
        self.worker.site_current.connect(self.update_site_activity)
        self.worker.done.connect(self.on_worker_done)
        self.worker.start()

    def _md5_from_path_or_cache(self, path: str) -> str:
        path = str(path or "")
        if not path:
            return "—"
        cached = self._site_activity_md5_by_path.get(path)
        if cached:
            return cached
        name = Path(path).name
        cached = self._site_activity_md5_by_name.get(name)
        if cached:
            return cached
        # Exact 32hex filename/stem is usually a booru/file MD5.  Do not treat
        # 40hex names as MD5 here: those are often source keys/SHA1-like names.
        stem = Path(name).stem.lower()
        if re.fullmatch(r"[0-9a-f]{32}", stem):
            self._remember_activity_md5(name, stem, path=path)
            return stem
        m = re.search(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", name.lower())
        if m:
            value = m.group(1)
            self._remember_activity_md5(name, value, path=path)
            return value
        return "ожидает"

    def _remember_activity_md5(self, name: str, md5: str, *, path: str = ""):
        md5 = str(md5 or "").lower().strip()
        if not re.fullmatch(r"[0-9a-f]{32}", md5):
            return
        name = Path(str(name or "")).name
        if name:
            self._site_activity_md5_by_name[name] = md5
        if path:
            self._site_activity_md5_by_path[str(path)] = md5
        # Refresh visible rows currently showing this file.
        for site in list((self._site_activity_name_to_sites.get(name) or set())):
            row = self._site_activity_rows.get(site)
            if row is not None and self.site_activity_table.item(row, 2) is not None:
                self.site_activity_table.item(row, 2).setText(md5)
                self.site_activity_table.item(row, 2).setToolTip(md5)

    def _prefix_name_from_log_line(self, text: str) -> str:
        text = str(text or "")
        m = re.match(r"^\[(MD5|REVERSE|SAUCENAO-RETRY):([^\]]+)\]", text)
        if not m:
            return ""
        payload = m.group(2)
        # [MD5:site:file] uses the last component as visible file name;
        # [MD5:file] and [REVERSE:file] are already just a file name.
        if m.group(1) == "MD5" and ":" in payload:
            payload = payload.rsplit(":", 1)[-1]
        return Path(payload).name

    def _observe_md5_log_line(self, text: str):
        """Update the live status table when parser logs discover real/site MD5.

        This is deliberately log-driven instead of hashing in the UI thread, so
        large videos do not freeze the interface just to fill the status table.
        """
        try:
            name = self._prefix_name_from_log_line(text)
            if not name:
                return
            md5 = ""
            patterns = (
                r"TRY REAL FILE MD5:\s*([0-9a-fA-F]{32})",
                r"REAL FILE MD5:\s*([0-9a-fA-F]{32})",
                r"TRY MD5 FROM FILENAME:\s*([0-9a-fA-F]{32})",
                r"TRY VARIANT SITE MD5 RELAY:\s*([0-9a-fA-F]{32})",
                r"ATF PIXEL HASH ASSET:.*?\bmd5=([0-9a-fA-F]{32})",
                r"\b(?:extracted_md5|md5)=([0-9a-fA-F]{32})\b",
            )
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    md5 = m.group(1).lower()
                    break
            if md5:
                self._remember_activity_md5(name, md5)
        except Exception:
            pass

    def update_site_activity(self, site, status, path):
        site = str(site); path = str(path or "")
        if site not in getattr(self, "_active_log_channel_set", set()):
            return
        self._set_log_channel_status(site, str(status), path)
        display_name = Path(path).name if path else "—"
        md5_text = self._md5_from_path_or_cache(path)
        old_name = self._site_activity_current_name_by_site.get(site, "")
        if old_name and old_name != display_name:
            try:
                self._site_activity_name_to_sites.get(old_name, set()).discard(site)
            except Exception:
                pass
        if display_name != "—":
            self._site_activity_name_to_sites.setdefault(display_name, set()).add(site)
            self._site_activity_current_name_by_site[site] = display_name
        else:
            self._site_activity_current_name_by_site.pop(site, None)
        row = self._site_activity_rows.get(site)
        if row is None:
            row = self.site_activity_table.rowCount(); self.site_activity_table.insertRow(row); self._site_activity_rows[site] = row
            self.site_activity_table.setItem(row, 0, QTableWidgetItem(site))
            self.site_activity_table.setItem(row, 1, QTableWidgetItem(str(status)))
            md5_item = QTableWidgetItem(str(md5_text))
            md5_item.setToolTip(str(md5_text))
            try:
                md5_item.setFont(self.site_activity_table.font())
            except Exception:
                pass
            self.site_activity_table.setItem(row, 2, md5_item)
            wrap = QWidget(); h = QHBoxLayout(wrap); h.setContentsMargins(2,2,2,2); h.setSpacing(6)
            thumb = QLabel(); thumb.setFixedSize(64,64); thumb.setAlignment(Qt.AlignCenter); thumb.setStyleSheet("border:1px solid #2f3541;border-radius:4px;")
            name = QLabel(display_name); name.setToolTip(path); name.setWordWrap(True)
            h.addWidget(thumb); h.addWidget(name, 1); self.site_activity_table.setCellWidget(row, 3, wrap); self.site_activity_table.setRowHeight(row, 70)
            self._site_activity_preview_labels[site] = (thumb, name)
        else:
            self.site_activity_table.item(row, 1).setText(str(status))
            if self.site_activity_table.item(row, 2) is not None:
                self.site_activity_table.item(row, 2).setText(str(md5_text))
                self.site_activity_table.item(row, 2).setToolTip(str(md5_text))
            thumb, name = self._site_activity_preview_labels[site]; name.setText(display_name); name.setToolTip(path)
            if not path:
                thumb.clear()
        self._site_activity_paths[site] = path
        if not path:
            return
        from core.thumb_service import ThumbnailService
        svc = ThumbnailService.instance()
        cached = svc.request(path, 64, 64, lambda received, pix, key=site: self._on_site_activity_preview(key, received, pix))
        if cached is not None and not cached.isNull():
            self._on_site_activity_preview(site, path, cached)

    def _on_site_activity_preview(self, site, path, pix):
        if self._site_activity_paths.get(site) != str(path):
            return
        labels = self._site_activity_preview_labels.get(site)
        if not labels or pix is None or pix.isNull():
            return
        thumb, _name = labels
        thumb.setPixmap(pix.scaled(thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def on_worker_progress(self, v, t):
        self.progress.setMaximum(max(1, t))
        self.progress.setValue(v)
        if v % 100 == 0:
            self.trim_ui_memory()

    def update_preview_visibility(self, *_args):
        """Exactly one activity view is visible: lightweight preview or site lanes."""
        try:
            conveyor_enabled = bool(self.md5.isChecked())
            low_power = bool(self.low_power.isChecked())
            # Old invariant kept for compatibility: show_lanes = conveyor_enabled and not low_power
            # Status moved into the console mode stack; legacy call was self.site_activity_table.setVisible(show_lanes).
            show_single_preview = bool(self.main.settings.get("show_search_preview", True)) and (low_power or not conveyor_enabled)
            self.preview_box.setVisible(show_single_preview)
            if low_power:
                self.preview_box.setToolTip("Щадящий режим: показывается только текущий файл; логи сайтов доступны в панели консоли")
            else:
                self.site_activity_table.setToolTip("Статус только включённых сайтов и включённых fallback-служб")
        except Exception:
            pass

    def show_current_preview(self, path):
        self.update_preview_visibility()
        self._current_preview_path = str(path)
        if not self.preview_box.isVisible():
            return
        # Show filename immediately, generate thumbnail in background
        self.preview_box.clear()
        self.preview_box.setText(Path(path).name)
        self.preview_box.setToolTip(str(path))
        # Use ThumbnailService so PIL/video work happens off UI thread
        from core.thumb_service import ThumbnailService
        svc = ThumbnailService.instance()
        size = self.preview_box.contentsRect().size()
        w = max(200, size.width() if size.width() > 20 else self.preview_box.width())
        h = max(200, size.height() if size.height() > 20 else self.preview_box.height())
        cached = svc.request(str(path), w, h, self._on_preview_ready)
        if cached is not None and not cached.isNull():
            self._on_preview_ready(str(path), cached)

    def _on_preview_ready(self, path: str, pix) -> None:
        # Called from UI thread by ThumbnailService
        if getattr(self, "_current_preview_path", None) != str(path):
            return  # stale — a newer file was requested
        if not self.preview_box.isVisible():
            return
        if pix.isNull():
            self.preview_box.setText(Path(path).name)
            return
        size = self.preview_box.contentsRect().size()
        if size.width() < 20 or size.height() < 20:
            size = self.preview_box.size()
        from PySide6.QtCore import Qt
        self.preview_box.clear()
        self.preview_box.setPixmap(pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.preview_box.setToolTip(str(path))

    # _preview_source_path and _render_preview kept for compatibility but no longer
    # called from show_current_preview.
    def _preview_source_path(self, path):
        return Path(path)

    def _render_preview(self, path):
        pass  # replaced by ThumbnailService async flow

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            p = getattr(self, "_current_preview_path", "")
            if p:
                QTimer.singleShot(0, lambda p=p: self._render_preview(p))
        except Exception:
            pass

    def on_worker_done(self):
        try:
            self.main.settings["_parser_running"] = False
        except Exception:
            pass
        self.refresh_saucenao_state()
        try:
            from core.light_backup import checkpoint_sqlite
            if bool(self.main.settings.get("sqlite_checkpoint_on_exit", True)):
                res = checkpoint_sqlite(self.main.settings, truncate=True, optimize=True)
                if res.get("ok"):
                    self.append_log("SQLite WAL checkpoint TRUNCATE: parser stopped/done")
        except Exception as e:
            self.append_log(f"SQLite checkpoint warning: {e}")
        self.trim_ui_memory()
        self.append_log("DONE")
        self.start.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText(self.main.t("PAUSE"))
        self.stop_btn.setEnabled(False)

    def pause_resume(self):
        if not self.worker or not self.worker.isRunning():
            return
        paused = self.pause_btn.isChecked()
        self.worker.set_paused(paused)
        self.pause_btn.setText(self.main.t("RESUME") if paused else self.main.t("PAUSE"))
        self.append_log("PAUSED" if paused else "RESUMED")

    def stop(self):
        if self.worker and self.worker.isRunning():
            # STOP must also break a paused worker immediately.
            self.pause_btn.setChecked(False)
            self.pause_btn.setText(self.main.t("PAUSE"))
            self.worker.set_paused(False)
            self.worker.requestInterruption()
            try:
                self.main.settings["_parser_running"] = False
            except Exception:
                pass
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.append_log("STOPPING: cancelling queued checks and reverse-search starts...")