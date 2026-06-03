from pathlib import Path
from urllib.parse import urlparse
import json
import time
import webbrowser
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QPlainTextEdit,QProgressBar,QCheckBox,QDoubleSpinBox,QSpinBox,QLineEdit,QFileDialog,QGroupBox,QFormLayout,QSplitter,QTableWidget,QTableWidgetItem,QComboBox,QHeaderView,QMessageBox,QAbstractItemView,QSizePolicy
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from core.settings import save_settings, DEFAULT_SITES
from core.tagger import Tagger, MEDIA_EXTS, video_frame_image, output_processed_status, result_output_base, result_paths_for, has_copy_suffix, is_md5, file_md5, file_phash
from ui.login_browser import LoginBrowserDialog, open_br34, open_br34_multi
from ui.sites_widget import SitesWidget
from ui.memory_tools import bounded_append, set_bounded_log, soft_gc
from core.deleted_registry import should_skip_deleted_file, has_deleted_record_for_name


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
    def __init__(self, settings): super().__init__(); self.settings=settings.copy(); self.paused=False
    def set_paused(self, paused):
        self.paused = bool(paused)

    def _wait_if_paused_or_delay(self, seconds=0):
        end = time.time() + max(0, float(seconds or 0))
        while not self.isInterruptionRequested():
            if self.paused:
                time.sleep(0.25)
                continue
            if time.time() >= end:
                break
            time.sleep(min(0.25, end - time.time()))

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
            self.log.emit("SITE CONVEYOR: no enabled MD5 sites; using ordinary fallback path")
            return None

        prior_global_status = dict(prior_global_status or {})
        existing_media_map = dict(existing_media_map or {})
        site_done_map = dict(site_done_map or {})
        interval = max(1.10, float(self.settings.get("tagger_site_interval_seconds", 1.10) or 1.10))
        low_power = bool(self.settings.get("tagger_low_power_mode", False))
        window = 1 if low_power else max(2, min(128, int(self.settings.get("tagger_conveyor_window", 32) or 32)))
        total = len(files)
        stats = {"tagged": 0, "nomatch": 0, "skipped": 0, "deferred_network": 0,
                 "deferred_saucenao": 0, "errors": 0, "site_checks": 0, "site_merged": 0}
        event_q = queue.Queue()
        fallback_q = queue.Queue()
        category_q = queue.Queue()
        persist_lock = threading.Lock()
        sentinel = object()
        states = {}
        category_enabled = bool(self.settings.get("tagger_background_tag_groups", self.settings.get("tagger_background_rule34_categories", True)))
        category_job_key = "flat-sites::tag-groups-v2"
        category_hosts = {"gelbooru.com", "rule34.xxx", "xbooru.com", "hypnohub.net"}
        category_scheduled = set()
        deferred_sauce = {
            str(Path(path)): (Path(path), int(retry_at))
            for path, retry_at, _reason in (restored_saucenao or [])
        }
        sauce_wait_notice_for = 0
        next_token = 0
        file_iter = iter(files)

        site_lanes = []
        used_labels = {}
        for index, site in enumerate(sites):
            label = writer._site_label(site)
            used_labels[label] = used_labels.get(label, 0) + 1
            shown = label if used_labels[label] == 1 else f"{label} ({used_labels[label]})"
            site_key = _site_scan_key(writer, site)
            engine = str(site.get("engine") or site.get("type") or "")
            site_lanes.append((f"site-{index}", shown, site_key, engine, site, queue.Queue()))

        enabled_names = ", ".join(shown for _key, shown, _site_key, _engine, _site, _q in site_lanes)
        self.log.emit(
            f"SITE CONVEYOR ACTIVE: lanes={len(site_lanes)} minimum_interval={interval:.2f}s "
            f"window={window}; sites={enabled_names}"
        )
        self.log.emit("SITE CONVEYOR: per-site SQLite journal active; newly enabled sites scan old files without rerunning completed sources")
        if low_power:
            self.log.emit("LOW POWER MODE: per-site journal kept; window=1 and per-site previews hidden")
        self.log.emit("SITE CONVEYOR: existing per-site safety budgets are preserved; restricted sites may wait longer than the minimum interval")
        self.log.emit("SITE CONVEYOR: reverse search runs only for previously unprocessed files after all MD5 sites miss")
        if deferred_sauce:
            next_retry = min(value[1] for value in deferred_sauce.values())
            left = max(0, next_retry - int(time.time()))
            self.log.emit(
                f"SAUCENAO RETRY RESTORED: pending={len(deferred_sauce)}; "
                f"next retry in {left//60}m {left%60}s; IQDB/Ascii2D will not replay"
            )
        # Make fallback services visible in the activity panel even while they
        # wait for an all-MD5 miss. Previously the table listed only MD5 lanes,
        # so a later SauceNAO/IQDB stall looked invisible to the user.
        if self.settings.get("enable_saucenao") and self.settings.get("saucenao_api_key"):
            self.site_current.emit("SauceNAO", "Ожидает промаха MD5", "")
        if self.settings.get("enable_iqdb"):
            self.site_current.emit("IQDB", "Ожидает промаха MD5", "")
        if self.settings.get("enable_ascii2d"):
            self.site_current.emit("Ascii2D", "Ожидает промаха MD5", "")
        if category_enabled:
            try:
                seeded = seed_background_tag_enrichment(session_settings, job_key=category_job_key, hosts=tuple(sorted(category_hosts)))
                jobs = pending_tag_enrichments(session_settings, job_key=category_job_key)
                for job in jobs:
                    job_id = (str(job.get("original_path", "")), str(job.get("source_url", "")))
                    if job_id not in category_scheduled:
                        category_scheduled.add(job_id)
                        category_q.put(job)
                self.site_current.emit("Категории тегов", f"Фон: в очереди {len(jobs)}", "")
                if seeded or jobs:
                    self.log.emit(f"TAG CATEGORY BACKGROUND: queued={len(jobs)} backfilled={seeded}; source lanes collect tags only and are not blocked")
            except Exception as e:
                self.log.emit(f"TAG CATEGORY BACKGROUND WARNING: cannot load queue: {e}")

        def lane_settings(site):
            cfg = dict(session_settings)
            host = str(site.get("domain") or urlparse(writer._site_root_from_cfg(site)).netloc).lower().replace("www.", "")
            by_host = dict(cfg.get("http_min_interval_by_host") or {})
            by_host[host] = interval
            cfg["http_min_interval_by_host"] = by_host
            # Abort throttling immediately on STOP; otherwise a lane waiting for
            # its next permitted request looks as if the button did nothing.
            cfg["_cancel_callback"] = self.isInterruptionRequested
            return cfg

        def live_log(message):
            if not self.isInterruptionRequested():
                self.log.emit(str(message))

        def site_loop(lane_key, shown, site_key, engine, site, work_q):
            local = Tagger(lane_settings(site), live_log)
            local.cancel_callback = self.isInterruptionRequested
            while not self.isInterruptionRequested():
                try:
                    item = work_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is sentinel:
                    break
                token, phase, md5, path = item
                self._wait_if_paused_or_delay(0)
                if self.isInterruptionRequested():
                    break
                self.site_current.emit(shown, "MD5", str(path))
                self.log.emit(f"  MD5 CHECK [{shown}]: {Path(path).name}")
                local._reset_network_state()
                tags = []
                source = ""
                groups = {}
                error_text = ""
                try:
                    tags, source, groups = local.engine_by_md5(site, md5)
                    if self.isInterruptionRequested():
                        break
                    if tags:
                        self.log.emit(f"  MD5 MATCH [{shown}]: {Path(path).name} {source}")
                except InterruptedError:
                    if self.isInterruptionRequested():
                        break
                    error_text = "request cancelled"
                except Exception as e:
                    if self.isInterruptionRequested():
                        break
                    error_text = str(e)
                    self.log.emit(f"  MD5 ERROR [{shown}]: {Path(path).name}: {e}")
                if self.isInterruptionRequested():
                    break
                network_failed = local.transient_network_failed() or _looks_like_network_exception(error_text)
                event_q.put(("primary", token, lane_key, site_key, engine, phase, md5, tags, source, groups, network_failed, local.network_failure_summary()))

        fallback_settings = dict(session_settings)
        fallback_settings["enable_md5_lookup"] = False
        fallback_settings["_cancel_callback"] = self.isInterruptionRequested

        def fallback_loop():
            local = Tagger(fallback_settings, live_log)
            local.cancel_callback = self.isInterruptionRequested
            local.activity_callback = lambda name, path, status: self.site_current.emit(name, status, path) if not self.isInterruptionRequested() else None
            while not self.isInterruptionRequested():
                try:
                    item = fallback_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is sentinel:
                    break
                token, path, sauce_retry_only = item
                self._wait_if_paused_or_delay(0)
                if self.isInterruptionRequested():
                    break
                self.site_current.emit("SauceNAO повтор" if sauce_retry_only else "Запасной поиск", "Ожидает результат", str(path))
                previous_retry_only = local.settings.get("_saucenao_retry_only", False)
                local.settings["_saucenao_retry_only"] = bool(sauce_retry_only)
                try:
                    # Keep reverse-search network waits outside the serialized
                    # persistence section. The Tagger acquires this lock only when
                    # it is ready to write a final result.
                    result = local.process_image(path, persist_lock=persist_lock)
                    if self.isInterruptionRequested():
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
                                self.log.emit(f"  TAG CATEGORY BACKGROUND QUEUED [{source_host} fallback]: {path.name}")
                    retry_after = str(local.saucenao_retry_after_epoch()) if result == "retry_saucenao" else ""
                    event_q.put(("fallback", token, result, retry_after))
                except InterruptedError:
                    if self.isInterruptionRequested():
                        break
                    event_q.put(("fallback", token, "error", "request cancelled"))
                except Exception as e:
                    if self.isInterruptionRequested():
                        break
                    event_q.put(("fallback", token, "error", str(e)))
                finally:
                    local.settings["_saucenao_retry_only"] = previous_retry_only

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
            category_settings["_cancel_callback"] = self.isInterruptionRequested
            local = Tagger(category_settings, live_log)
            local.cancel_callback = self.isInterruptionRequested
            while not self.isInterruptionRequested():
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
                self.site_current.emit(f"{shown_host} категории", "Фоновая раскладка", str(original))
                try:
                    groups = local.grouped_tags_from_url(source_url)
                    if self.isInterruptionRequested():
                        break
                    classified = sum(len(groups.get(k, []) or []) for k in ("artist", "character", "copyright", "species", "meta"))
                    if classified:
                        parsed_tags = []
                        for vals in groups.values():
                            parsed_tags.extend(vals or [])
                        with persist_lock:
                            writer.merge_conveyor_match_into_existing(media, original, parsed_tags, [], [groups])
                            complete_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, status="done")
                        self.log.emit(f"  TAG CATEGORY BACKGROUND DONE [{shown_host}]: {original.name} classified={classified}")
                        self.site_current.emit(f"{shown_host} категории", f"Разложено: {classified}", str(original))
                    else:
                        with persist_lock:
                            complete_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, status="done", error="source provides no classified tags")
                        self.log.emit(f"  TAG CATEGORY BACKGROUND SKIP [{shown_host}]: {original.name} no classified tags")
                except InterruptedError:
                    break
                except Exception as e:
                    if not self.isInterruptionRequested():
                        with persist_lock:
                            retry_tag_enrichment(session_settings, original, source_url, job_key=category_job_key, delay_seconds=300, error=str(e))
                        self.log.emit(f"  TAG CATEGORY BACKGROUND RETRY [{shown_host}]: {original.name}: {e}")
                finally:
                    category_scheduled.discard(job_id)

        threads = []
        for lane_key, shown, site_key, engine, site, work_q in site_lanes:
            th = threading.Thread(target=site_loop, args=(lane_key, shown, site_key, engine, site, work_q), daemon=True, name=f"site-conveyor-{lane_key}")
            th.start()
            threads.append(th)
        reverse_thread = threading.Thread(target=fallback_loop, daemon=True, name="site-conveyor-fallback")
        reverse_thread.start()
        threads.append(reverse_thread)
        if category_enabled:
            category_thread = threading.Thread(target=category_loop, daemon=True, name="site-conveyor-tag-categories")
            category_thread.start()
            threads.append(category_thread)

        lane_queue = {lane_key: work_q for lane_key, _shown, _key, _engine, _site, work_q in site_lanes}
        lane_name = {lane_key: shown for lane_key, shown, _key, _engine, _site, _q in site_lanes}
        lane_site_key = {lane_key: key for lane_key, _shown, key, _engine, _site, _q in site_lanes}
        lane_engine = {lane_key: engine for lane_key, _shown, _key, engine, _site, _q in site_lanes}

        def queue_fallback_or_finish(token, state):
            if self.isInterruptionRequested():
                return False
            path_key = str(state["path"])
            old_status = str(state.get("prior_status") or "")
            if old_status and not bool(self.settings.get("retry_nomatch", False)):
                stats["skipped"] += 1
                self.log.emit(f"  SITE UPDATE COMPLETE: {state['path'].name} existing={old_status}; reverse fallback not repeated")
                return False
            if path_key in deferred_sauce:
                retry_at = int(deferred_sauce[path_key][1])
                left = max(0, retry_at - int(time.time()))
                self.log.emit(
                    f"  SAUCENAO RETRY ALREADY QUEUED: {state['path'].name}; "
                    f"IQDB/Ascii2D not repeated; retry in {left//60}m {left%60}s"
                )
                return False
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
            media_path = _archive_path_after_match(state)
            job_id = (str(state["path"]), source_url)
            try:
                with persist_lock:
                    enqueue_tag_enrichment(session_settings, state["path"], media_path, source_url, job_key=category_job_key)
                if job_id not in category_scheduled:
                    category_scheduled.add(job_id)
                    category_q.put({"original_path": str(state["path"]), "media_path": media_path, "source_url": source_url, "job_key": category_job_key})
                    self.log.emit(f"  TAG CATEGORY BACKGROUND QUEUED [{base_site_key}]: {state['path'].name}")
            except Exception as e:
                self.log.emit(f"  TAG CATEGORY QUEUE ERROR [{base_site_key}]: {state['path'].name}: {e}")

        def _persist_one_lane_match(state, lane_key, result):
            if lane_key in state.get("saved_match_keys", set()):
                return True
            tags = list(result.get("tags") or [])
            if not tags:
                return True
            source = str(result.get("source") or "")
            groups = result.get("groups") or {}
            try:
                with persist_lock:
                    if state.get("persisted_found") or (state.get("prior_status") in ("found", "tagged") and state.get("existing_media_path")):
                        outcome = writer.merge_conveyor_match_into_existing(
                            state.get("existing_media_path") or _archive_path_after_match(state),
                            state["path"], tags,
                            [f"md5 {lane_name.get(lane_key, lane_key)} {source}"] if source else [],
                            [groups] if groups else [],
                        )
                    else:
                        outcome = writer.save_conveyor_match(
                            state["path"], tags,
                            [f"md5 {lane_name.get(lane_key, lane_key)} {source}"] if source else [],
                            [groups] if groups else [],
                        )
                    if outcome != "tagged":
                        return False
                    remove_reverse_retry(session_settings, state["path"], service="saucenao")
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
                self.log.emit(f"ERROR {state['path'].name}: {e}")
                return False

        def checkpoint_lane_results(state, *, finalize_misses=False):
            """Persist each completed source as soon as its result is final.

            Filename-derived misses are not final until either another site matched
            that filename or the real-file MD5 pass is known not to be needed.
            Matches and real-MD5 checks are durable immediately, so STOP/restart
            resumes each site lane from its own checkpoint instead of replaying the
            whole conveyor window.
            """
            checkpointed = state.setdefault("checkpointed_keys", set())
            for lane_key, result in list(state.get("lane_results", {}).items()):
                if lane_key in checkpointed or result.get("network_failed"):
                    continue
                final_for_lane = bool(result.get("tags")) or state.get("phase") == "real" or bool(finalize_misses)
                if not final_for_lane:
                    continue
                if result.get("tags") and not _persist_one_lane_match(state, lane_key, result):
                    continue
                with persist_lock:
                    mark_site_scanned(
                        session_settings, state["path"], lane_site_key[lane_key],
                        engine=lane_engine[lane_key], scan_revision=SITE_SCAN_REVISION,
                        outcome="match" if result.get("tags") else "miss",
                        checked_md5=result.get("md5", ""), source_url=result.get("source", ""),
                    )
                checkpointed.add(lane_key)
                stats["site_checks"] += 1

        def submit_path(path):
            nonlocal next_token
            if self.isInterruptionRequested():
                return False
            next_token += 1
            token = next_token
            path = Path(path)
            self.log.emit(f"SEARCH: {path.name}")
            search_img = video_frame_image(path)
            if search_img != path:
                self.log.emit(f"  VIDEO FRAME: {search_img.name}")
            img_phash = file_phash(search_img)
            if img_phash:
                self.log.emit(f"  PHASH: {img_phash}")
            from_filename = is_md5(path.stem)
            if from_filename:
                lookup_md5 = path.stem.lower()
                self.log.emit(f"  TRY MD5 FROM FILENAME: {lookup_md5}")
                phase = "filename"
            else:
                lookup_md5 = file_md5(search_img)
                self.log.emit(f"  TRY REAL FILE MD5: {lookup_md5}")
                phase = "real"
            already = set((site_done_map.get(str(path)) or {}).keys())
            active_keys = [lane_key for lane_key in lane_queue if lane_site_key[lane_key] not in already]
            if active_keys and len(active_keys) < len(lane_queue):
                pending_names = ", ".join(lane_name.get(key, key) for key in active_keys)
                self.log.emit(f"  RESUME ONLY PENDING SITES: {pending_names}")
            states[token] = {
                "path": path, "search_img": search_img, "phase": phase,
                "first_was_filename": from_filename, "md5": lookup_md5,
                "active_keys": active_keys, "waiting": set(active_keys),
                "tags": [], "sources": [], "groups": [], "network_failed": False,
                "lane_results": {}, "prior_status": prior_global_status.get(str(path)),
                "was_existing_found": prior_global_status.get(str(path)) in ("found", "tagged"),
                "existing_media_path": existing_media_map.get(str(path), ""), "is_sauce_retry": False,
                "checkpointed_keys": set(), "saved_match_keys": set(), "persisted_found": False,
            }
            if not active_keys:
                self.log.emit(f"  MD5 SITES UP TO DATE: {path.name}")
                if not queue_fallback_or_finish(token, states[token]):
                    event_q.put(("complete", token))
                return True
            for lane_key in active_keys:
                lane_queue[lane_key].put((token, phase, lookup_md5, path))
            return True

        def submit_saucenao_retry(path):
            nonlocal next_token
            if self.isInterruptionRequested():
                return False
            next_token += 1
            token = next_token
            path = Path(path)
            self.log.emit(f"SAUCENAO RETRY AFTER COOLDOWN: {path.name}")
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
            fallback_q.put((token, path, True))
            return True

        for _ in range(min(window, total)):
            try:
                submit_path(next(file_iter))
            except StopIteration:
                break

        completed = 0
        stopped = False
        while states or deferred_sauce:
            now = int(time.time())
            retry_slots = max(0, window - len(states))
            if retry_slots:
                due_paths = [key for key, value in list(deferred_sauce.items()) if int(value[1]) <= now]
                for key in due_paths[:retry_slots]:
                    path, _retry_at = deferred_sauce.pop(key)
                    submit_saucenao_retry(path)
            if not states and deferred_sauce:
                next_due = min(int(value[1]) for value in deferred_sauce.values())
                if sauce_wait_notice_for != next_due:
                    left = max(0, next_due - int(time.time()))
                    self.log.emit(f"SAUCENAO QUEUE WAITING: {len(deferred_sauce)} file(s); automatic retry in {left//60}m {left%60}s")
                    sauce_wait_notice_for = next_due
                self._wait_if_paused_or_delay(min(1.0, max(0.05, next_due - time.time())))
                continue
            self._wait_if_paused_or_delay(0)
            if self.isInterruptionRequested():
                stopped = True
                self.log.emit("STOPPED")
                break
            try:
                event = event_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if self.isInterruptionRequested():
                stopped = True
                self.log.emit("STOPPED")
                break
            if event[0] == "primary":
                _, token, lane_key, site_key, engine, phase, checked_md5, tags, source, groups, network_failed, network_summary = event
                state = states.get(token)
                if state is None or phase != state["phase"]:
                    continue
                state["waiting"].discard(lane_key)
                state["lane_results"][lane_key] = {"tags": list(tags or []), "source": source, "groups": groups or {}, "md5": checked_md5, "network_failed": bool(network_failed)}
                if network_failed:
                    state["network_failed"] = True
                if tags:
                    state["tags"].extend(tags)
                    if source:
                        state["sources"].append(f"md5 {lane_name.get(lane_key, lane_key)} {source}")
                    if groups:
                        state["groups"].append(groups)
                    self.site_current.emit(lane_name.get(lane_key, lane_key), "Найдено", str(state["path"]))
                else:
                    self.site_current.emit(lane_name.get(lane_key, lane_key), "Нет совпадения", str(state["path"]))
                # A match is final immediately; a real-MD5 result is also final.
                # Checkpoint now, before slower lanes finish, so restart does not
                # replay fast-site work from the beginning of the conveyor window.
                checkpoint_lane_results(state, finalize_misses=False)
                if state["waiting"]:
                    continue
                if state["tags"]:
                    # The matching lanes have already saved metadata. At this point
                    # filename-phase misses are also final because at least one source
                    # verified the filename hash.
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
                        self.log.emit(f"  TRY REAL FILE MD5: {real_md5}")
                        for key in state["active_keys"]:
                            lane_queue[key].put((token, "real", real_md5, state["path"]))
                        continue
                    checkpoint_lane_results(state, finalize_misses=True)
                    if state["network_failed"]:
                        stats["deferred_network"] += 1
                        self.log.emit(f"  NETWORK TEMPORARY FAILURE: {state['path'].name} has unfinished site lanes; deferred")
                    elif queue_fallback_or_finish(token, state):
                        continue
                else:
                    checkpoint_lane_results(state, finalize_misses=True)
                    if state["network_failed"]:
                        stats["deferred_network"] += 1
                        self.log.emit(f"  NETWORK TEMPORARY FAILURE: {state['path'].name} has unfinished site lanes; deferred")
                    elif queue_fallback_or_finish(token, state):
                        continue
                states.pop(token, None)
                completed += 1
                self.current_file.emit(str(state["path"]))
                self.progress.emit(completed, total)
                try:
                    submit_path(next(file_iter))
                except StopIteration:
                    pass
            elif event[0] == "complete":
                _, token = event
                state = states.pop(token, None)
                if state is None:
                    continue
                completed += 1
                self.current_file.emit(str(state["path"]))
                self.progress.emit(completed, total)
                try:
                    submit_path(next(file_iter))
                except StopIteration:
                    pass
            elif event[0] == "fallback":
                _, token, result, error_text = event
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
                    with persist_lock:
                        enqueue_reverse_retry(session_settings, state["path"], service="saucenao", retry_after=retry_at, reason="api_cooldown")
                    deferred_sauce[path_key] = (state["path"], retry_at)
                    stats["deferred_saucenao"] += 1
                    left = max(0, retry_at - int(time.time()))
                    self.log.emit(f"  SAUCENAO RETRY QUEUED: {state['path'].name}; automatic retry in {left//60}m {left%60}s")
                    if is_retry:
                        try:
                            record_task_event(session_settings, "saucenao_retry", "cooldown_again", state["path"].name)
                        except Exception:
                            pass
                else:
                    with persist_lock:
                        remove_reverse_retry(session_settings, state["path"], service="saucenao")
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
                            self.log.emit(f"ERROR {state['path'].name}: {error_text}")
                if not is_retry:
                    completed += 1
                    self.current_file.emit(str(state["path"]))
                    self.progress.emit(completed, total)
                    try:
                        submit_path(next(file_iter))
                    except StopIteration:
                        pass

        discarded = 0
        if stopped or self.isInterruptionRequested():
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
            # Background category jobs remain durable in SQLite and resume later.
            self.log.emit(f"STOP: discarded queued checks={discarded}; only already-sent HTTP calls may finish silently")
        for _lane_key, _shown, _key, _engine, _site, work_q in site_lanes:
            work_q.put(sentinel)
        fallback_q.put(sentinel)
        if category_enabled:
            category_q.put(sentinel)
        for th in threads:
            th.join(timeout=0.25 if stopped else 1.0)
        return stats, stopped


    def run(self):
        root=Path(self.settings.get("root",""))
        files=[p for p in root.rglob("*") if p.suffix.lower() in MEDIA_EXTS] if root.exists() else []
        self.log.emit(f"SCAN FOUND MEDIA: {len(files)} files")
        # Не сканируем Local_Booru_Output как обычный источник. Иначе один и тот же
        # файл может снова попасть в no_match/found второй копией. Исключение —
        # режим Retry NO_MATCH, когда корнем специально выбрана output/no_match/media.
        try:
            out_base = result_output_base(self.settings).resolve()
            root_resolved = root.resolve()
            is_retry_nm_root = root_resolved.name == "media" and root_resolved.parent.name == "no_match"
            # Do not recursively feed our own output back into APT when the user
            # selected a normal source folder. But if the selected root itself is
            # inside Local_Booru_Output, assume the user deliberately wants to
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
                    self.log.emit(f"SKIP OUTPUT FOLDER FILES: {skipped_output}")
        except Exception as e:
            self.log.emit(f"OUTPUT FILTER WARNING: {e}")

        if self.settings.get("skip_copy_suffix_files", True):
            before = len(files)
            files = [x for x in files if not has_copy_suffix(x)]
            skipped_copy = before - len(files)
            if skipped_copy:
                self.log.emit(f"SKIP COPY-SUFFIX FILES: {skipped_copy}")
                if not files and before:
                    self.log.emit("NO FILES LEFT AFTER COPY-SUFFIX FILTER. Disable 'Skip files ending (1)/(2)' if these copies must be scanned.")

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
        self.log.emit("QUEUE PREP: deleted-cache filter done")
        if deleted_candidates:
            self.log.emit(f"DELETED-DUPLICATE CACHE CHECKED: {deleted_candidates}")
        if deleted_skips:
            self.log.emit(f"SKIP DELETED-DUPLICATE CACHE: {deleted_skips}")
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
                self.log.emit(f"SITE STATUS WARNING: cannot read enabled sites: {e}")
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
                    self.log.emit(f"SAUCENAO RETRY RESTORE WARNING: {e}")
                    restored_saucenao = []
                    restored_saucenao_paths = set()

        if self.settings.get("tag_only_untagged") or self.settings.get("skip_existing"):
            before_status = len(files)
            self.log.emit(f"STATUS CHECK: {before_status} files")
            status_map = {}
            try:
                from core.services.scan_state_service import processed_records_many
                processed_records = processed_records_many(self.settings, files)
                status_map = {path: row.get("status", "") for path, row in processed_records.items()}
                existing_media_map = {path: row.get("media_path", "") for path, row in processed_records.items()}
                self.log.emit(f"STATUS CHECK DB MATCHES: {len(status_map)}")
            except Exception as e:
                self.log.emit(f"STATUS CHECK DB WARNING: {e}")
            if use_conveyor:
                try:
                    from core.services.scan_state_service import site_scan_status_many
                    site_done_map = site_scan_status_many(self.settings, files, active_site_keys, scan_revision=SITE_SCAN_REVISION)
                except Exception as e:
                    self.log.emit(f"SITE STATUS DB WARNING: {e}")
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
                self.log.emit(f"SITE STATUS CHECK: enabled_sites={len(active_site_keys)} fully_checked_existing={completed_for_all_sites} pending_on_processed={new_site_pending}")
                if waiting_saucenao_only:
                    self.log.emit(f"SAUCENAO RETRY WAITING: {waiting_saucenao_only} file(s) excluded from normal MD5/IQDB/Ascii2D replay")
                skipped_status = before_status - len(files)
                if skipped_status:
                    self.log.emit(f"SKIP FILES ALREADY CHECKED BY ALL ENABLED SITES: {skipped_status}")
            else:
                filtered = []
                for p in files:
                    if status_map.get(str(p)) is None:
                        filtered.append(p)
                files = filtered
                skipped_status = before_status - len(files)
                if skipped_status:
                    self.log.emit(f"SKIP ALREADY PROCESSED SQL STATUS: {skipped_status}")

        self.log.emit("QUEUE PREP: status filters done")
        limit=int(self.settings.get("limit_files",0))
        if limit>0: files=files[:limit]

        self.log.emit(f"SEARCH QUEUE: {len(files)} files")
        if not files:
            self.log.emit("NO FILES TO SEARCH. Check root folder, copy-suffix filter, skip-existing, and retry-no-match settings.")

        from datetime import datetime as _dt
        _session_ts = _dt.now().strftime("%Y-%m-%d_%H-%M")
        _settings_with_session = dict(self.settings, session_folder=_session_ts)
        tagger=Tagger(_settings_with_session, lambda m:self.log.emit(str(m)))
        total=len(files)
        tagged=0
        nomatch=0
        skipped=0
        errors=0
        deferred_network=0
        stopped=False
        stopped_network=False

        if use_conveyor and (total > 0 or restored_saucenao):
            result = self._run_site_conveyor(files, _settings_with_session, tagger, prior_global_status=prior_global_status, existing_media_map=existing_media_map, site_done_map=site_done_map, restored_saucenao=restored_saucenao)
            if result is not None:
                stats, stopped = result
                self.log.emit(
                    f"SUMMARY: TAGGED={stats['tagged']} NO_MATCH={stats['nomatch']} "
                    f"SKIPPED={stats['skipped']} DEFERRED_NETWORK={stats['deferred_network']} "
                    f"DEFERRED_SAUCENAO={stats.get('deferred_saucenao', 0)} "
                    f"SITE_CHECKS={stats.get('site_checks', 0)} MERGED_EXISTING={stats.get('site_merged', 0)} "
                    f"ERRORS={stats['errors']} TOTAL={total}"
                )
                if stopped:
                    self.log.emit("SUMMARY: stopped by user")
                self.done.emit()
                return
        elif bool(self.settings.get("tagger_low_power_mode", False)):
            self.log.emit("LOW POWER MODE: conveyor manually disabled; legacy one-file processing is active")

        network_retry_attempts = max(0, min(5, int(self.settings.get("network_retry_attempts", 2) or 2)))
        network_retry_delay = max(1.0, min(120.0, float(self.settings.get("network_retry_delay_seconds", 10) or 10)))

        parallel_workers = int(self.settings.get("tagger_parallel_workers", 1) or 1)
        parallel_workers = max(1, min(parallel_workers, 4))
        if bool(self.settings.get("tagger_low_power_mode", False)):
            parallel_workers = 1

        def _process_with_network_retry(local_tagger, path):
            for attempt in range(network_retry_attempts + 1):
                if self.isInterruptionRequested():
                    return "skip"
                result = local_tagger.process_image(path)
                if result != "retry_network":
                    return result
                if attempt < network_retry_attempts:
                    pause = min(120.0, network_retry_delay * (2 ** attempt))
                    self.log.emit(
                        f"  NETWORK RETRY {attempt + 1}/{network_retry_attempts}: "
                        f"{Path(path).name} через {int(pause)} сек."
                    )
                    self._wait_if_paused_or_delay(pause)
            return "retry_network"

        def _process_one(path, worker_index=0):
            # One Tagger instance per worker.  The Tagger has session/cache state,
            # so sharing one instance across threads is not safe.
            local_settings = dict(_settings_with_session)
            local_tagger = Tagger(local_settings, lambda m: self.log.emit(str(m)))
            local_tagger.cancel_callback = self.isInterruptionRequested
            self.current_file.emit(str(path))
            return _process_with_network_retry(local_tagger, path)

        if parallel_workers <= 1 or total <= 1:
            for i,p in enumerate(files,1):
                self._wait_if_paused_or_delay(0)
                if self.isInterruptionRequested():
                    self.log.emit("STOPPED")
                    stopped=True
                    break

                try:
                    self.current_file.emit(str(p))
                    if self.isInterruptionRequested():
                        self.log.emit("STOPPED")
                        stopped=True
                        break
                    tagger.cancel_callback = self.isInterruptionRequested
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
                        self.log.emit(
                            "NETWORK UNAVAILABLE: текущий файл отложен и НЕ отправлен в Брак. "
                            "Сканирование остановлено; запусти его снова после восстановления интернета/VPN."
                        )
                except Exception as e:
                    if _looks_like_network_exception(e):
                        deferred_network += 1
                        stopped_network = True
                        self.log.emit(
                            f"NETWORK ERROR {p.name}: {e}. "
                            "Файл отложен и НЕ отправлен в Брак."
                        )
                    else:
                        errors += 1
                        self.log.emit(f"ERROR {p.name}: {e}")

                self.progress.emit(i,total)
                if stopped_network:
                    break
                self._wait_if_paused_or_delay(float(self.settings.get("delay_seconds", 0) or 0))
        else:
            self.log.emit(f"PARALLEL TAGGER: {parallel_workers} workers enabled")
            from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
            pending = {}
            done_count = 0
            network_abort = False
            file_iter = iter(files)
            with ThreadPoolExecutor(max_workers=parallel_workers, thread_name_prefix="tagger") as ex:
                def submit_next():
                    if self.isInterruptionRequested() or network_abort:
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
                    if self.isInterruptionRequested():
                        stopped = True
                        self.log.emit("STOPPED")
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
                                self.log.emit(
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
                                self.log.emit(
                                    f"NETWORK ERROR {Path(pth).name}: {e}. "
                                    "Файл отложен и НЕ отправлен в Брак."
                                )
                            else:
                                errors += 1
                                self.log.emit(f"ERROR {Path(pth).name}: {e}")
                        done_count += 1
                        self.progress.emit(done_count,total)
                        self._wait_if_paused_or_delay(float(self.settings.get("delay_seconds", 0) or 0))
                        if network_abort:
                            for queued in list(pending.keys()):
                                queued.cancel()
                        submit_next()

        self.log.emit(
            f"SUMMARY: TAGGED={tagged} NO_MATCH={nomatch} SKIPPED={skipped} "
            f"DEFERRED_NETWORK={deferred_network} ERRORS={errors} TOTAL={total}"
        )
        if stopped:
            self.log.emit("SUMMARY: stopped by user")
        if stopped_network:
            self.log.emit("SUMMARY: stopped because network/VPN was unavailable; deferred files remain eligible for retry")
        self.done.emit()

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

            profile_dir = Path("data/runtime/browser_profile") / host
            cookies_dir = Path("data/runtime/browser_cookies")
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
                legacy_file = Path("data/runtime/browser_cookies.json")
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
        self.min_sim=QDoubleSpinBox(); self.min_sim.setRange(50,99); self.min_sim.setSingleStep(0.5)
        self.skip=QCheckBox(); self.only_untagged=QCheckBox(); self.skip_copy_suffix=QCheckBox(); self.md5=QCheckBox(); self.sauce=QCheckBox(); self.ascii2d=QCheckBox()
        self.iqdb=QCheckBox(); self.low_power=QCheckBox(); self.bg_rule34_categories=QCheckBox()
        self.site_interval=QDoubleSpinBox(); self.site_interval.setRange(1.10, 30.0); self.site_interval.setDecimals(2); self.site_interval.setSingleStep(0.10); self.site_interval.setSuffix(" с")
        self.conveyor_window=QSpinBox(); self.conveyor_window.setRange(2,128)
        # Keep bare indicators compact, but leave enough room for QSS borders.
        # Old fixedWidth(20) clipped 17px indicators with 2px borders in dark themes.
        for _cb in [self.skip,self.only_untagged,self.skip_copy_suffix,self.md5,
                    self.sauce,self.ascii2d,self.iqdb,self.low_power,self.bg_rule34_categories]:
            _cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            _cb.setFixedSize(23, 23)
        self.iqdb_min=QDoubleSpinBox(); self.iqdb_min.setRange(50,99); self.iqdb_min.setSingleStep(0.5)
        self.delay=QDoubleSpinBox(); self.delay.setRange(0,120); self.limit=QSpinBox(); self.limit.setRange(0,1000000); self.req_timeout=QSpinBox(); self.req_timeout.setRange(5,300); self.sauce_cooldown=QSpinBox(); self.sauce_cooldown.setRange(1,1440)
        self.form_rows=[]
        for label,w,tip in [("Folder",row,"tip_root"),("SauceNAO API key",self.api,"tip_saucenao"),("SauceNAO min similarity",self.min_sim,"tip_min_similarity"),("MD5 lookup",self.md5,"tip_md5"),("SauceNAO fallback",self.sauce,"tip_sauce"),("IQDB fuzzy fallback",self.iqdb,"tip_iqdb"),("IQDB min similarity",self.iqdb_min,"tip_iqdb"),("Ascii2D fallback",self.ascii2d,"tip_ascii2d"),("Skip existing",self.skip,"tip_skip"),("Tag only untagged",self.only_untagged,"tip_only_untagged"),("Skip files ending (1)/(2)",self.skip_copy_suffix,"tip_skip_copy_suffix"),("Background tag groups",self.bg_rule34_categories,"tip_background_groups"),("Low power mode",self.low_power,"tip_low_power"),("Site interval",self.site_interval,"tip_site_interval"),("Conveyor window",self.conveyor_window,"tip_conveyor_window"),("Delay",self.delay,"tip_delay"),("Request timeout",self.req_timeout,"tip_delay"),("Sauce cooldown min",self.sauce_cooldown,"tip_sauce"),("Limit",self.limit,"tip_limit"),]: self.add_tip_row(label,w,tip)
        split.addWidget(left)
        right=QWidget(); rlay=QVBoxLayout(right); rlay.setContentsMargins(6, 0, 0, 0); rlay.setSpacing(4)
        self.sites_widget = SitesWidget()
        self.sites_widget.save_btn.clicked.connect(self.sync)
        self.sites_widget.login_btn.clicked.connect(self.open_selected_login)
        self.sites_widget.all_login_btn.clicked.connect(self.open_all_logins)
        rlay.addWidget(self.sites_widget)
        split.addWidget(right); split.setSizes([520,820])
        row2=QHBoxLayout(); row2.setContentsMargins(0, 0, 0, 0); row2.setSpacing(6); self.save_btn=QPushButton(); self.save_btn.clicked.connect(self.sync); self.start=QPushButton(); self.start.clicked.connect(self.run); self.pause_btn=QPushButton("PAUSE"); self.pause_btn.setCheckable(True); self.pause_btn.clicked.connect(self.pause_resume); self.pause_btn.setEnabled(False); self.stop_btn = QPushButton(); self.stop_btn.clicked.connect(self.stop); self.stop_btn.setEnabled(False);
        for _btn in (self.save_btn, self.start, self.pause_btn, self.stop_btn):
            _btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row2.addWidget(_btn, 1)
        lay.addLayout(row2)
        self.progress=QProgressBar(); lay.addWidget(self.progress)
        self.console_preview_split = QSplitter(Qt.Horizontal)
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); set_bounded_log(self.log, int(self.main.settings.get("max_console_lines", 2500)))
        self.preview_box=QLabel("Preview"); self.preview_box.setAlignment(Qt.AlignCenter); self.preview_box.setMinimumWidth(240)
        self.preview_box.setStyleSheet("border:1px solid #2f3541;border-radius:8px;")
        self.site_activity_table=QTableWidget(0,3); self.site_activity_table.setHorizontalHeaderLabels(["Сайт", "Состояние", "Текущий файл"]); self.site_activity_table.verticalHeader().setVisible(False); self.site_activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.site_activity_table.setSelectionMode(QAbstractItemView.NoSelection); self.site_activity_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents); self.site_activity_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents); self.site_activity_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.site_activity_table.setMinimumWidth(430)
        self._site_activity_rows={}; self._site_activity_paths={}; self._site_activity_preview_labels={}
        self.console_preview_split.addWidget(self.log); self.console_preview_split.addWidget(self.preview_box); self.console_preview_split.addWidget(self.site_activity_table)
        self.console_preview_split.setSizes([720,250,470])
        lay.addWidget(self.console_preview_split,2)
        self._last_site_table = None
        self._last_site_row = -1
        self.low_power.toggled.connect(self.update_preview_visibility)
        self.load_values(); self.retranslate(); self.update_preview_visibility()

    def add_tip_row(self, label_key, widget, tip_key):
        lab = QLabel(self.main.t(label_key) + "  ?")
        lab.setToolTip(self.main.t(tip_key))
        _tc2 = self.main.settings.get("appearance","abyss") if hasattr(self,"main") else "abyss"
        _lmap = {"light": ("#1a1c2a","#5060d0"), "r34": ("#111111","#3a7a35"), "r34dark": ("#d6e4d3","#6aa5ff"),
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


    def apply_theme_style(self, theme_name: str | None = None):
        """Refresh parser page inline label styles after runtime theme switch."""
        theme_name = theme_name or self.main.settings.get("appearance", "abyss")
        colors = {
            "light": ("#1a1c2a", "#5060d0"),
            "r34": ("#111111", "#3a7a35"),
            "r34dark": ("#d6e4d3", "#6aa5ff"),
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
        bounded_append(self.log, msg, int(self.main.settings.get("max_console_lines", 2500)))

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
        self.iqdb.setChecked(bool(s.get("enable_iqdb",True))); self.iqdb_min.setValue(float(s.get("iqdb_min_similarity",75))); self.delay.setValue(float(s.get("delay_seconds",8))); self.req_timeout.setValue(int(float(s.get("request_timeout_seconds",20)))); self.sauce_cooldown.setValue(int(float(s.get("saucenao_cooldown_seconds",3600))/60)); self.limit.setValue(int(s.get("limit_files",0)))
        self.bg_rule34_categories.setChecked(bool(s.get("tagger_background_tag_groups", s.get("tagger_background_rule34_categories", True)))); self.low_power.setChecked(bool(s.get("tagger_low_power_mode", False))); self.site_interval.setValue(max(1.10, float(s.get("tagger_site_interval_seconds", 1.10) or 1.10))); self.conveyor_window.setValue(int(s.get("tagger_conveyor_window",32) or 32)); self.update_preview_visibility()
        self.sites_widget.load(s)
    def retranslate(self):
        t=self.main.t; self.choose_btn.setText(t("Choose")); self.save_btn.setText(t("Save settings")); self.start.setText(t("START")); self.pause_btn.setText(t("RESUME") if self.pause_btn.isChecked() else t("PAUSE")); self.stop_btn.setText(t("STOP")); self.apply_tips()
        for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
            lab.setText(t(label_key) + "  ?")
            lab.setToolTip(t(tip_key))

            if hasattr(w, "setToolTip"):
                w.setToolTip(t(tip_key))
    def apply_tips(self):
        t=self.main.t
        pairs=[(self.root,"tip_root"),(self.api,"tip_saucenao"),(self.min_sim,"tip_min_similarity"),(self.md5,"tip_md5"),(self.sauce,"tip_sauce"),(self.iqdb,"tip_iqdb"),(self.delay,"tip_delay"),(self.req_timeout,"tip_delay"),(self.sauce_cooldown,"tip_sauce"),(self.limit,"tip_limit"),(self.skip,"tip_skip"),(self.only_untagged,"tip_only_untagged"),(self.skip_copy_suffix,"tip_skip_copy_suffix"),(self.bg_rule34_categories,"tip_background_groups"),(self.low_power,"tip_low_power"),(self.site_interval,"tip_site_interval"),(self.conveyor_window,"tip_conveyor_window")]
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
        save_settings(s)
        if show_message:
            QMessageBox.information(self, self.main.t("Saved"), self.main.t("Settings saved"))
    def run(self):
        self.sync(show_message=False)
        self.log.clear()
        self.start.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setChecked(False)
        self.stop_btn.setEnabled(True)

        self.site_activity_table.setRowCount(0); self._site_activity_rows.clear(); self._site_activity_paths.clear(); self._site_activity_preview_labels.clear(); self.update_preview_visibility()
        self.worker = TaggerWorker(self.main.settings)
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_worker_progress)
        self.worker.current_file.connect(self.show_current_preview)
        self.worker.site_current.connect(self.update_site_activity)
        self.worker.done.connect(self.on_worker_done)
        self.worker.start()

    def update_site_activity(self, site, status, path):
        if not self.site_activity_table.isVisible():
            return
        site = str(site); path = str(path or "")
        display_name = Path(path).name if path else "—"
        row = self._site_activity_rows.get(site)
        if row is None:
            row = self.site_activity_table.rowCount(); self.site_activity_table.insertRow(row); self._site_activity_rows[site] = row
            self.site_activity_table.setItem(row, 0, QTableWidgetItem(site))
            self.site_activity_table.setItem(row, 1, QTableWidgetItem(str(status)))
            wrap = QWidget(); h = QHBoxLayout(wrap); h.setContentsMargins(2,2,2,2); h.setSpacing(6)
            thumb = QLabel(); thumb.setFixedSize(64,64); thumb.setAlignment(Qt.AlignCenter); thumb.setStyleSheet("border:1px solid #2f3541;border-radius:4px;")
            name = QLabel(display_name); name.setToolTip(path); name.setWordWrap(True)
            h.addWidget(thumb); h.addWidget(name, 1); self.site_activity_table.setCellWidget(row, 2, wrap); self.site_activity_table.setRowHeight(row, 70)
            self._site_activity_preview_labels[site] = (thumb, name)
        else:
            self.site_activity_table.item(row, 1).setText(str(status))
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
            show_single_preview = bool(self.main.settings.get("show_search_preview", True)) and (low_power or not conveyor_enabled)
            show_lanes = conveyor_enabled and not low_power
            self.preview_box.setVisible(show_single_preview)
            self.site_activity_table.setVisible(show_lanes)
            if low_power:
                self.preview_box.setToolTip("Щадящий режим: показывается только текущий файл, без таблицы полос")
            elif show_lanes:
                self.site_activity_table.setToolTip("Обычный режим: отдельная полоса каждого сайта")
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
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.append_log("STOPPING: cancelling queued checks and reverse-search starts...")