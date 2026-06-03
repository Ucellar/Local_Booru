"""Read-only library audit and explicit repair helpers for Local Booru v115.

The audit path never changes library rows, files, queues or the deleted-MD5
registry.  All repair operations are explicit and create a backup before any
write that may affect the library.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable, Any

from core.database.connection import db, db_path
from core.paths import ERROR_LOG_FILE, LOGS_DIR, SETTINGS_DIR, CACHE_DIR
from core.source_protection import source_root, output_root, recent_blocked_events
from core.redaction import sanitize_object, sanitize_text


AUTO_DELETE_REASONS = {
    "duplicate_delete",
    "subscription_visual_duplicate",
    "subscription_session_cleanup",
    "downloader_exact_duplicate",
    "reimport_deleted_rejected",
    "restore_exact_duplicate_cleanup",
    "exact_md5_auto_normalized",
}

REASON_LABELS = {
    "gallery_context_delete": "удалено вручную из галереи",
    "post_context_delete": "удалено вручную из просмотра",
    "duplicate_delete": "удалено в окне дубликатов",
    "subscription_visual_duplicate": "авто: похожий дубликат подписки",
    "subscription_session_cleanup": "очистка сессии подписки",
    "downloader_exact_duplicate": "авто: точный дубликат загрузчика",
    "reimport_deleted_rejected": "авто: повторно скачанный удалённый файл",
    "restore_exact_duplicate_cleanup": "авто: убрана точная копия после восстановления",
    "exact_md5_auto_normalized": "авто: склеена точная MD5-копия",
    "delete_by_tag": "удалено по тегу",
    "delete_by_source": "удалено по источнику",
    "delete_by_buckets": "очистка результатов",
    "unknown": "неизвестно / старая сборка",
}

CRITICAL_INDEXES = (
    "idx_images_md5",
    "idx_images_deleted_bucket",
    "idx_images_lifecycle",
    "idx_images_favorite",
    "idx_image_tags_tag",
    "idx_image_sources_source",
    "idx_processed_original_path",
    "idx_site_scan_path",
    "idx_site_scan_site",
    "idx_reverse_retry_due",
    "idx_tag_enrichment_pending",
    "idx_deleted_rules_active",
    "idx_service_state_cooldown",
    "idx_no_match_active",
    "idx_delete_log_path",
)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_bytes(value: int) -> str:
    n = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TB"


def _enabled_site_keys(settings: dict) -> list[str]:
    keys: list[str] = []
    for host, cfg in dict((settings or {}).get("sites") or {}).items():
        if isinstance(cfg, dict) and bool(cfg.get("enabled", False)):
            key = str(host or "").strip().lower().replace("www.", "")
            if key == "rule34.us":
                key += "::remote-media-md5-v2"
            if key and key not in keys:
                keys.append(key)
    for cfg in list((settings or {}).get("custom_sites") or []):
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", True)):
            continue
        key = str(cfg.get("domain") or cfg.get("host") or cfg.get("name") or "").strip().lower().replace("www.", "")
        if key and key not in keys:
            keys.append(key)
    return keys


def _load_deleted_registry() -> list[dict]:
    """Read an unimported legacy JSON registry for audit only.

    It never drives live deletion decisions; showing it here prevents hidden
    stale blocks during migration from old test libraries.
    """
    path = Path(SETTINGS_DIR) / "deleted_files_ignore.json"
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [dict(x) for x in data.get("items", []) if isinstance(x, dict)] if isinstance(data, dict) else []
    except Exception:
        return []


def _last_error_lines(limit: int = 30) -> list[str]:
    candidates = [ERROR_LOG_FILE, Path(LOGS_DIR) / "errors.log"]
    seen = set()
    for path in candidates:
        try:
            if str(path) in seen or not path.exists():
                continue
            seen.add(str(path))
            lines = sanitize_text(path.read_text(encoding="utf-8", errors="replace")).splitlines()
            return lines[-max(1, int(limit)):]
        except Exception:
            continue
    return []


def audit_library(settings: dict, *, verify_files: bool = False, progress: Callable[[str], None] | None = None, stop_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Collect a v115 health report without writing anything.

    ``verify_files`` is opt-in because checking existence of 300k paths can be
    expensive on slow drives. SQL-only checks stay quick and safe during a live
    parser run.
    """
    progress = progress or (lambda _m: None)
    stop_check = stop_check or (lambda: False)
    db_file = db_path(settings)
    report: dict[str, Any] = {
        "created_at": _now_text(),
        "read_only": True,
        "database": {"path": str(db_file), "exists": db_file.exists(), "size_bytes": db_file.stat().st_size if db_file.exists() else 0},
        "library": {},
        "md5": {},
        "trash": {},
        "queues": {},
        "site_scan": {},
        "indices": {},
        "errors": {},
        "performance": {},
        "storage": {},
        "source_protection": {},
        "samples": {},
        "cancelled": False,
    }
    try:
        disk = shutil.disk_usage(str(db_file.parent if db_file.parent.exists() else SETTINGS_DIR))
        report["storage"]["database_disk"] = {"free_bytes": int(disk.free), "total_bytes": int(disk.total)}
    except Exception:
        report["storage"]["database_disk"] = {}
    try:
        thumbs = Path(CACHE_DIR) / "thumbs"
        files = list(thumbs.rglob("*")) if thumbs.exists() else []
        report["storage"]["thumbnail_cache"] = {
            "path": str(thumbs), "files": sum(1 for x in files if x.is_file()),
            "size_bytes": sum(x.stat().st_size for x in files if x.is_file()),
        }
    except Exception:
        report["storage"]["thumbnail_cache"] = {"path": str(Path(CACHE_DIR) / "thumbs"), "files": 0, "size_bytes": 0}
    try:
        from core.performance import recent_slow_operations
        report["performance"]["slow_operations"] = recent_slow_operations(100)
    except Exception:
        report["performance"]["slow_operations"] = []
    try:
        blocked = recent_blocked_events(100)
        report["source_protection"] = {
            "source_root": str(source_root(settings) or ""),
            "output_root": str(output_root(settings)),
            "immutable_source": True,
            "blocked_count_shown": len(blocked),
            "blocked_events": blocked,
        }
    except Exception:
        report["source_protection"] = {"source_root": str((settings or {}).get("root", "") or ""), "output_root": "", "immutable_source": True, "blocked_count_shown": 0, "blocked_events": []}
    try:
        crash_path = Path(LOGS_DIR) / "last_crash.json"
        report["errors"]["last_crash"] = sanitize_object(json.loads(crash_path.read_text(encoding="utf-8"))) if crash_path.exists() else {}
    except Exception:
        report["errors"]["last_crash"] = {}

    if not db_file.exists():
        report["database"]["quick_check"] = "база не найдена"
        report["summary_text"] = (
            "ДИАГНОСТИКА БИБЛИОТЕКИ — ТОЛЬКО ЧТЕНИЕ\n"
            f"Создано: {report['created_at']}\n"
            f"SQLite: {db_file}\n\n"
            "База SQLite не найдена. Диагностика ничего не создавала и не изменяла."
        )
        progress("Диагностика: база SQLite не найдена; изменений нет.")
        return report
    progress("Диагностика: чтение SQLite…")
    with db(settings, readonly=True) as con:
        def scalar(sql: str, args=()) -> int:
            row = con.execute(sql, args).fetchone()
            return int((row[0] if row is not None else 0) or 0)

        try:
            quick = con.execute("PRAGMA quick_check(5)").fetchone()
            report["database"]["quick_check"] = str(quick[0] if quick else "unknown")
        except Exception as exc:
            report["database"]["quick_check"] = "error: " + str(exc)
        try:
            page_size = int(con.execute("PRAGMA page_size").fetchone()[0] or 0)
            page_count = int(con.execute("PRAGMA page_count").fetchone()[0] or 0)
            freelist = int(con.execute("PRAGMA freelist_count").fetchone()[0] or 0)
            report["database"]["page_size"] = page_size
            report["database"]["page_count"] = page_count
            report["database"]["freelist_pages"] = freelist
            report["database"]["reclaimable_bytes"] = page_size * freelist
            row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            report["database"]["schema_version"] = int(row[0]) if row else 0
            try:
                report["database"]["migrations"] = [dict(x) for x in con.execute(
                    "SELECT version,name,status,error,applied_at FROM schema_migrations ORDER BY version"
                ).fetchall()]
            except Exception:
                report["database"]["migrations"] = []
        except Exception as exc:
            report["database"]["maintenance_error"] = str(exc)
        try:
            report["database"]["health_events"] = [dict(x) for x in con.execute(
                "SELECT check_type,status,details,db_size_bytes,created_at FROM database_health_events ORDER BY id DESC LIMIT 10"
            ).fetchall()]
            report["database"]["maintenance_history"] = [dict(x) for x in con.execute(
                "SELECT operation,status,before_bytes,after_bytes,reclaimed_bytes,backup_path,created_at FROM maintenance_history ORDER BY id DESC LIMIT 10"
            ).fetchall()]
        except Exception:
            report["database"]["health_events"] = []
            report["database"]["maintenance_history"] = []
        try:
            from core.database.connection import writes_blocked_reason
            report["database"]["write_blocked_reason"] = writes_blocked_reason()
        except Exception:
            report["database"]["write_blocked_reason"] = ""

        report["library"] = {
            "live_files": scalar("SELECT COUNT(*) FROM images WHERE deleted=0"),
            "live_bytes": scalar("SELECT COALESCE(SUM(size_bytes),0) FROM images WHERE deleted=0"),
            "trash_files": scalar("SELECT COUNT(*) FROM images WHERE deleted=1 AND lifecycle='trash'"),
            "trash_bytes": scalar("SELECT COALESCE(SUM(size_bytes),0) FROM images WHERE deleted=1 AND lifecycle='trash'"),
            "without_source": scalar("SELECT COUNT(*) FROM images i WHERE i.deleted=0 AND NOT EXISTS (SELECT 1 FROM image_sources x WHERE x.image_id=i.id)"),
            "without_tags": scalar("SELECT COUNT(*) FROM images i WHERE i.deleted=0 AND NOT EXISTS (SELECT 1 FROM image_tags x WHERE x.image_id=i.id)"),
            "tag_count": scalar("SELECT COUNT(*) FROM tags"),
            "source_count": scalar("SELECT COUNT(*) FROM sources"),
            "multi_source_files": scalar("SELECT COUNT(*) FROM (SELECT image_id FROM image_sources GROUP BY image_id HAVING COUNT(*)>1)"),
        }
        report["md5"] = {
            "live_with_md5": scalar("SELECT COUNT(*) FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''"),
            "live_without_md5": scalar("SELECT COUNT(*) FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')=''"),
            "duplicate_groups": scalar("SELECT COUNT(*) FROM (SELECT lower(hash_md5) FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>'' GROUP BY lower(hash_md5) HAVING COUNT(*)>1)"),
            "redundant_rows": scalar("SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>'' GROUP BY lower(hash_md5) HAVING COUNT(*)>1)"),
        }
        dup_samples = con.execute(
            """SELECT lower(hash_md5) AS md5, COUNT(*) AS files, COALESCE(SUM(size_bytes),0) AS bytes
               FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''
               GROUP BY lower(hash_md5) HAVING COUNT(*)>1 ORDER BY files DESC, bytes DESC LIMIT 30"""
        ).fetchall()
        report["samples"]["duplicate_md5"] = [dict(x) for x in dup_samples]
        report["samples"]["without_source"] = [dict(x) for x in con.execute(
            """SELECT i.id,i.path,i.file_name,i.hash_md5 FROM images i WHERE i.deleted=0
               AND NOT EXISTS (SELECT 1 FROM image_sources x WHERE x.image_id=i.id)
               ORDER BY i.id DESC LIMIT 30"""
        ).fetchall()]
        report["samples"]["without_tags"] = [dict(x) for x in con.execute(
            """SELECT i.id,i.path,i.file_name,i.hash_md5 FROM images i WHERE i.deleted=0
               AND NOT EXISTS (SELECT 1 FROM image_tags x WHERE x.image_id=i.id)
               ORDER BY i.id DESC LIMIT 30"""
        ).fetchall()]

        trash_reasons = con.execute(
            """SELECT reason, COUNT(*) AS files FROM (
                 SELECT i.id, COALESCE((SELECT d.reason FROM delete_log d
                     WHERE d.path=CASE WHEN COALESCE(i.original_media_path,'')<>'' THEN i.original_media_path ELSE i.path END
                     ORDER BY d.deleted_at DESC,d.id DESC LIMIT 1),'unknown') AS reason
                 FROM images i WHERE i.deleted=1 AND i.lifecycle='trash'
               ) GROUP BY reason ORDER BY files DESC"""
        ).fetchall()
        report["trash"]["by_reason"] = [
            {"reason": str(r["reason"]), "label": REASON_LABELS.get(str(r["reason"]), str(r["reason"])), "files": int(r["files"] or 0)}
            for r in trash_reasons
        ]

        reverse = con.execute(
            "SELECT service, COUNT(*) AS files, MIN(retry_after) AS next_retry, MAX(attempts) AS max_attempts FROM reverse_retry_queue GROUP BY service ORDER BY service"
        ).fetchall()
        now = int(time.time())
        report["queues"]["reverse_retry"] = [
            {"service": str(r["service"]), "files": int(r["files"] or 0), "next_retry": int(r["next_retry"] or 0), "due_now": int(r["next_retry"] or 0) <= now, "max_attempts": int(r["max_attempts"] or 0)}
            for r in reverse
        ]
        report["queues"]["service_state"] = [dict(r) for r in con.execute(
            "SELECT service,cooldown_until,reason,updated_at FROM service_state ORDER BY service"
        ).fetchall()]
        report["queues"]["saucenao_retry_events"] = [dict(r) for r in con.execute(
            "SELECT status,message,created_at,updated_at FROM task_log WHERE task_type='saucenao_retry' ORDER BY id DESC LIMIT 20"
        ).fetchall()]
        enrich = con.execute(
            "SELECT job_key,status,COUNT(*) AS files,MIN(retry_after) AS next_retry,MAX(attempts) AS max_attempts FROM tag_enrichment_queue GROUP BY job_key,status ORDER BY status,job_key"
        ).fetchall()
        report["queues"]["tag_enrichment"] = [dict(r) for r in enrich]
        report["queues"]["unfinished_operations"] = [dict(r) for r in con.execute(
            "SELECT op_type,status,target_type,target_id,error,created_at,updated_at FROM operation_journal WHERE status NOT IN ('done','completed','success','repaired') ORDER BY updated_at DESC LIMIT 30"
        ).fetchall()]

        active_keys = _enabled_site_keys(settings)
        journal_paths = scalar("SELECT COUNT(DISTINCT original_path) FROM site_scan_status")
        site_stats = []
        for key in active_keys:
            checked = scalar("SELECT COUNT(DISTINCT original_path) FROM site_scan_status WHERE site_key=? AND scan_revision=1", (key,))
            matches = scalar("SELECT COUNT(*) FROM site_scan_status WHERE site_key=? AND scan_revision=1 AND outcome='match'", (key,))
            errors = scalar("SELECT COUNT(*) FROM site_scan_status WHERE site_key=? AND scan_revision=1 AND outcome IN ('error','deferred_network')", (key,))
            site_stats.append({"site": key, "checked": checked, "matches": matches, "pending_among_started": max(0, journal_paths - checked), "errors": errors})
        report["site_scan"] = {"started_paths": journal_paths, "enabled_sites": active_keys, "sites": site_stats}

        all_indices = {str(r["name"]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        missing_indices = [name for name in CRITICAL_INDEXES if name not in all_indices]
        report["indices"] = {"critical_required": list(CRITICAL_INDEXES), "missing": missing_indices, "ok": not missing_indices}

    progress("Диагностика: проверка старых MD5-запретов…")
    # Live deletion/re-import policy is transactional SQLite state. Legacy JSON
    # can still exist as an import backup, but never drives current decisions.
    with db(settings, readonly=True) as con:
        rules = [dict(r) for r in con.execute("SELECT md5,active,manual_delete,reason FROM deleted_media_rules").fetchall()]
        live_blocked_rows = con.execute(
            """SELECT DISTINCT r.md5 FROM deleted_media_rules r JOIN images i ON lower(i.hash_md5)=lower(r.md5)
               WHERE r.active=1 AND r.manual_delete=1 AND i.deleted=0"""
        ).fetchall()
    active_rules = [r for r in rules if int(r.get("active") or 0) and int(r.get("manual_delete") or 0)]
    auto_rows = [r for r in rules if not int(r.get("manual_delete") or 0)]
    live_blocked = [str(r["md5"] or "") for r in live_blocked_rows]
    legacy_rules = _load_deleted_registry()
    live_md5s = set()
    with db(settings, readonly=True) as con:
        live_md5s = {str(r["hash_md5"] or "").lower() for r in con.execute("SELECT DISTINCT hash_md5 FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''").fetchall()}
    legacy_obsolete = [str(r.get("md5") or "").lower() for r in legacy_rules if str(r.get("md5") or "").lower() in live_md5s]
    all_obsolete = sorted(set(live_blocked + legacy_obsolete))
    report["md5"]["deleted_registry_records"] = len(rules) + len(legacy_rules)
    report["md5"]["deleted_registry_unique_md5"] = len({str(r.get("md5") or "") for r in rules} | {str(r.get("md5") or "") for r in legacy_rules})
    report["md5"]["active_manual_blocks"] = len(active_rules)
    report["md5"]["obsolete_live_blocks"] = len(all_obsolete)
    report["md5"]["automatic_registry_candidates"] = len(auto_rows)
    report["md5"]["legacy_json_records"] = len(legacy_rules)
    report["samples"]["obsolete_live_md5"] = all_obsolete[:30]

    if verify_files and not stop_check():
        progress("Диагностика: проверка наличия файлов на диске…")
        with db(settings, readonly=True) as con:
            paths = [(int(r["id"]), str(r["path"] or "")) for r in con.execute("SELECT id,path FROM images WHERE deleted=0 ORDER BY id").fetchall()]
        missing = []
        for pos, (image_id, path) in enumerate(paths, start=1):
            if stop_check():
                report["cancelled"] = True
                break
            if not Path(path).is_file():
                missing.append({"id": image_id, "path": path})
            if pos % 1000 == 0:
                progress(f"Диагностика: проверено файлов на диске {pos}/{len(paths)}")
        report["library"]["missing_on_disk"] = len(missing)
        report["samples"]["missing_on_disk"] = missing[:50]
    else:
        report["library"]["missing_on_disk"] = None

    last_errors = _last_error_lines(40)
    report["errors"]["last_lines"] = last_errors
    report["errors"]["line_count_shown"] = len(last_errors)
    report["summary_text"] = format_report_text(report)
    progress("Диагностика: готово. Данные не изменялись.")
    return report


def format_report_text(report: dict[str, Any]) -> str:
    lib = report.get("library", {})
    md5 = report.get("md5", {})
    queues = report.get("queues", {})
    lines = [
        "ДИАГНОСТИКА БИБЛИОТЕКИ — ТОЛЬКО ЧТЕНИЕ",
        f"Создано: {report.get('created_at','')}",
        f"SQLite: {report.get('database',{}).get('path','')}",
        f"Размер SQLite: {_fmt_bytes(report.get('database',{}).get('size_bytes',0))}    quick_check: {report.get('database',{}).get('quick_check','?')}",
        f"Схема SQLite: v{report.get('database',{}).get('schema_version','?')}    Можно освободить VACUUM: {_fmt_bytes(report.get('database',{}).get('reclaimable_bytes',0))}",
        ("Режим записи: ЗАБЛОКИРОВАН — " + report.get('database',{}).get('write_blocked_reason','')) if report.get('database',{}).get('write_blocked_reason','') else "Режим записи: обычный",
        "",
        "Защита исходного архива",
        f"  Источник (только чтение): {report.get('source_protection',{}).get('source_root','')} ",
        f"  Рабочая галерея (разрешены изменения): {report.get('source_protection',{}).get('output_root','')}",
        f"  Заблокировано попыток изменения оригиналов (последние события): {report.get('source_protection',{}).get('blocked_count_shown',0)}",
        "",
        "Библиотека",
        f"  Живых файлов: {lib.get('live_files',0)}    Размер: {_fmt_bytes(lib.get('live_bytes',0))}",
        f"  В корзине: {lib.get('trash_files',0)}    Размер: {_fmt_bytes(lib.get('trash_bytes',0))}",
        f"  Без source: {lib.get('without_source',0)}    Без тегов: {lib.get('without_tags',0)}    С несколькими source: {lib.get('multi_source_files',0)}",
        "",
        "Exact MD5 Single Media Invariant",
        f"  Живых с MD5: {md5.get('live_with_md5',0)}    Без MD5: {md5.get('live_without_md5',0)}",
        f"  Групп точных дублей: {md5.get('duplicate_groups',0)}    Лишних живых копий: {md5.get('redundant_rows',0)}",
        f"  Устаревших запретов для уже живых MD5: {md5.get('obsolete_live_blocks',0)}",
        f"  Автоматических записей истории MD5 (не блокируют): {md5.get('automatic_registry_candidates',0)}",
        "",
        "Корзина по причинам",
    ]
    for row in report.get("trash", {}).get("by_reason", []):
        lines.append(f"  {row.get('label')}: {row.get('files')}")
    lines.append("")
    lines.append("Очереди")
    rev = queues.get("reverse_retry", [])
    if rev:
        for row in rev:
            eta = max(0, int(row.get("next_retry", 0)) - int(time.time()))
            lines.append(f"  Reverse {row.get('service')}: {row.get('files')} файлов; следующая попытка через {eta//60}м {eta%60}с; попыток max={row.get('max_attempts',0)}")
    else:
        lines.append("  Reverse retry: пусто")
    sauce_events = queues.get("saucenao_retry_events", [])
    if sauce_events:
        last_event = sauce_events[0]
        lines.append(f"  SauceNAO живой retry после кулдауна: {last_event.get('status')} — {last_event.get('message','')}")
    else:
        lines.append("  SauceNAO живой retry после кулдауна: ещё не зафиксирован")
    enrich = queues.get("tag_enrichment", [])
    if enrich:
        for row in enrich:
            lines.append(f"  Категории [{row.get('status')}]: {row.get('job_key')} — {row.get('files')} файлов")
    else:
        lines.append("  Фоновая раскладка тегов: пусто")
    lines.append(f"  Незавершённых операций: {len(queues.get('unfinished_operations', []))}")
    perf_rows = report.get("performance", {}).get("slow_operations", [])
    lines.append("")
    lines.append("Производительность:")
    lines.append(f"  Медленных операций в журнале: {len(perf_rows)}")
    if perf_rows:
        worst = max(perf_rows, key=lambda x: float(x.get("duration_ms", 0) or 0))
        lines.append(f"  Самая долгая: {worst.get('operation','?')} — {worst.get('duration_ms',0)} мс")
    thumb = report.get("storage", {}).get("thumbnail_cache", {})
    lines.append(f"  Кэш превью: {thumb.get('files',0)} файлов / {_fmt_bytes(int(thumb.get('size_bytes',0) or 0))}")
    lines.append("")
    lines.append("Проверки сайтов — незакрыто среди уже начатых файлов")
    for row in report.get("site_scan", {}).get("sites", []):
        lines.append(f"  {row.get('site')}: проверено={row.get('checked')}, совпадений={row.get('matches')}, осталось={row.get('pending_among_started')}, ошибок={row.get('errors')}")
    missing = report.get("indices", {}).get("missing", [])
    lines.append("")
    lines.append("Индексы SQLite: " + ("OK" if not missing else "отсутствуют: " + ", ".join(missing)))
    if lib.get("missing_on_disk") is not None:
        lines.append(f"Отсутствующих физических файлов: {lib.get('missing_on_disk',0)}")
    if report.get("errors", {}).get("last_lines"):
        lines.append("")
        lines.append("В errors.log есть последние записи; см. вкладку «Ошибки». ")
    return "\n".join(lines)


def create_forced_backup(settings: dict, *, reason: str = "diagnostics_manual_backup") -> str:
    from core.library_lifecycle import force_backup_database
    return force_backup_database(settings, reason)


def clear_obsolete_live_md5_blocks(settings: dict) -> dict[str, Any]:
    """Disable active manual-delete blocks only when identical live content exists."""
    backup = create_forced_backup(settings, reason="disable_obsolete_live_md5_blocks")
    if db_path(settings).exists() and not backup:
        return {"removed": 0, "backup": "", "remaining": 0, "error": "Не удалось создать резервную копию базы. Операция отменена."}
    now = int(time.time())
    with db(settings, write=True) as con:
        cur = con.execute(
            """UPDATE deleted_media_rules SET active=0, updated_at=?
               WHERE active=1 AND manual_delete=1 AND EXISTS (
                 SELECT 1 FROM images i WHERE i.deleted=0 AND lower(i.hash_md5)=lower(deleted_media_rules.md5)
               )""", (now,)
        )
        removed = int(cur.rowcount or 0)
        live_md5s = {str(r["hash_md5"] or "").lower() for r in con.execute("SELECT DISTINCT hash_md5 FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''").fetchall()}
        remaining = int(con.execute("SELECT COUNT(*) FROM deleted_media_rules WHERE active=1 AND manual_delete=1").fetchone()[0] or 0)
    legacy_path = Path(SETTINGS_DIR) / "deleted_files_ignore.json"
    if legacy_path.exists():
        try:
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
            items = [x for x in data.get("items", []) if isinstance(x, dict)] if isinstance(data, dict) else []
            kept = [x for x in items if str(x.get("md5") or "").lower() not in live_md5s]
            legacy_removed = len(items) - len(kept)
            if legacy_removed:
                shutil.copy2(legacy_path, legacy_path.with_name(legacy_path.stem + "_before_cleanup_" + str(now) + legacy_path.suffix + ".bak"))
                data["items"] = kept
                legacy_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                removed += legacy_removed
        except Exception:
            pass
    return {"removed": removed, "backup": backup, "remaining": remaining}


def requeue_stale_tag_enrichments(settings: dict) -> dict[str, Any]:
    """Return stale flat-source category jobs to the low-priority queue only."""
    backup = create_forced_backup(settings, reason="requeue_stale_tag_categories")
    if db_path(settings).exists() and not backup:
        return {"requeued": 0, "backup": "", "error": "Не удалось создать резервную копию базы. Операция отменена."}
    now = int(time.time())
    with db(settings, write=True) as con:
        cur = con.execute(
            """UPDATE tag_enrichment_queue SET status='pending', retry_after=0, last_error='', updated_at=?
               WHERE status='stale'""", (now,)
        )
    return {"requeued": int(cur.rowcount or 0), "backup": backup}


def restore_critical_indices(settings: dict) -> dict[str, Any]:
    """Recreate schema indices after an explicit backup, without altering media."""
    backup = create_forced_backup(settings, reason="restore_sqlite_indices")
    if db_path(settings).exists() and not backup:
        return {"restored": [], "missing_before": [], "missing_after": [], "backup": "", "error": "Не удалось создать резервную копию базы. Операция отменена."}
    with db(settings, readonly=True) as con:
        before = {str(r["name"]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    missing_before = [x for x in CRITICAL_INDEXES if x not in before]
    if missing_before:
        from core.database.schema import init_db
        with db(settings, write=True) as con:
            init_db(con, force=True)
    with db(settings, readonly=True) as con:
        after = {str(r["name"]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    missing_after = [x for x in CRITICAL_INDEXES if x not in after]
    return {"restored": [x for x in missing_before if x in after], "missing_before": missing_before, "missing_after": missing_after, "backup": backup}


def save_audit_json(report: dict[str, Any], destination: str | Path) -> str:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize_object(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
