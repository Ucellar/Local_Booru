from __future__ import annotations

"""Single high-level library service.

UI code should prefer this layer over direct repository calls for operations that
change library state.  The repository stays as the fast SQL/data-access layer;
this service adds journaling and invariant-aware orchestration.
"""

from core.database import repository
from core.database import journal
from core import gallery_provider


def page(settings, query="", source="all", bucket="all", limit=120, offset=0, order="path", enrich=True, extra_where=None, extra_params=None):
    rows = gallery_provider.search_items(settings, query=query, source=source, bucket=bucket, limit=limit, offset=offset, order=order, extra_where=extra_where, extra_params=extra_params)
    if enrich:
        rows = gallery_provider.enrich_items(settings, rows, tag_source=source)
    return rows


def total(settings, query="", source="all", bucket="all", extra_where=None, extra_params=None):
    return gallery_provider.count_search_items(settings, query=query, source=source, bucket=bucket, extra_where=extra_where, extra_params=extra_params)


def counters(settings):
    return gallery_provider.counts(settings)


def delete_by_tag(settings, tag, scope="all", delete_files=True):
    with journal.operation(settings, "delete_by_tag", target_type="tag", target_id=str(tag), payload={"scope": scope, "delete_files": bool(delete_files)}):
        rows = repository.find_images_by_tag(settings, tag, scope=scope, limit=None)
        if delete_files:
            from core.library_lifecycle import move_to_trash
            return move_to_trash(settings, rows, reason="delete_by_tag", tag_or_source=tag, make_backup=True)
        return repository.delete_images(settings, rows, delete_files=False, reason="delete_by_tag", tag_or_source=tag)


def delete_by_source(settings, source, scope="all", delete_files=True):
    with journal.operation(settings, "delete_by_source", target_type="source", target_id=str(source), payload={"scope": scope, "delete_files": bool(delete_files)}):
        rows = repository.find_images_by_source(settings, source, scope=scope, limit=None)
        if delete_files:
            from core.library_lifecycle import move_to_trash
            return move_to_trash(settings, rows, reason="delete_by_source", tag_or_source=source, make_backup=True)
        return repository.delete_images(settings, rows, delete_files=False, reason="delete_by_source", tag_or_source=source)


def delete_by_buckets(settings, buckets, delete_files=True):
    bucket_list = [str(b) for b in (buckets or []) if str(b)]
    with journal.operation(settings, "delete_by_buckets", target_type="bucket", target_id=",".join(bucket_list), payload={"delete_files": bool(delete_files)}):
        rows = repository.find_images_by_buckets(settings, bucket_list, limit=None)
        result = None
        if delete_files:
            from core.library_lifecycle import move_to_trash
            result = move_to_trash(settings, rows, reason="delete_by_buckets", tag_or_source=",".join(bucket_list), make_backup=True)
        else:
            result = repository.delete_images(settings, rows, delete_files=False, reason="delete_by_buckets", tag_or_source=",".join(bucket_list))
        # If NO_MATCH results are removed, their triage records must stop being
        # active. Otherwise the Брак page resurrects stale rows from SQLite even
        # though the user deliberately deleted that result bucket.
        if any(str(b) in ("no_match", "downloaded_no_match") for b in bucket_list):
            try:
                from core.nomatch_db import deactivate_for_paths
                paths = [str(r.get("path") or "") for r in rows]
                deactivated = deactivate_for_paths(paths, settings=settings)
                result["no_match_deactivated"] = int(deactivated or 0)
            except Exception as exc:
                result["no_match_deactivate_error"] = str(exc)
        return result


def check_integrity(settings, *, sample_limit=None):
    from core.database.invariants import check
    return check(settings, sample_limit=sample_limit, persist=True)


def repair_integrity(settings):
    from core.database.invariants import repair
    return repair(settings)


def unfinished_operations(settings, limit=100):
    return journal.unfinished(settings, limit=limit)


def validate_duplicate_group_plans(plans) -> dict:
    """Validate and normalise duplicate-delete plans without touching SQLite.

    A delete path may appear in several rendered candidate groups.  Repeating it
    with the same keeper is harmless and is deduplicated; assigning the same
    delete path to different keepers is ambiguous and blocks the whole batch.
    """
    from pathlib import Path
    from core.path_utils import path_key

    errors: list[str] = []
    normalised: list[dict] = []
    delete_owner: dict[str, dict] = {}

    for position, raw_plan in enumerate(list(plans or []), start=1):
        raw_plan = dict(raw_plan or {})
        group_index = raw_plan.get("group_index", position)
        group_label = str(raw_plan.get("group_label") or f"Группа {group_index}")
        keep_path = str(raw_plan.get("keep_path") or "").strip()
        keep_key = path_key(keep_path)

        if not keep_path or not keep_key:
            errors.append(f"{group_label}: отмечены все файлы; оставьте хотя бы один keeper.")
            continue
        if not Path(keep_path).is_file():
            errors.append(f"{group_label}: keeper отсутствует или не является файлом: {keep_path}")
            continue

        delete_paths: list[str] = []
        local_seen: set[str] = set()
        for value in list(raw_plan.get("delete_paths") or []):
            delete_path = str(value or "").strip()
            delete_key = path_key(delete_path)
            if not delete_path or not delete_key or delete_key in local_seen:
                continue
            local_seen.add(delete_key)

            if delete_key == keep_key:
                errors.append(f"{group_label}: keeper одновременно отмечен на удаление: {keep_path}")
                continue
            if not Path(delete_path).is_file():
                errors.append(f"{group_label}: удаляемый файл отсутствует: {delete_path}")
                continue

            previous = delete_owner.get(delete_key)
            if previous is not None:
                if previous["keep_key"] != keep_key:
                    errors.append(
                        f"{group_label}: файл отмечен в нескольких группах с разными keeper: "
                        f"{delete_path} (keeper: {previous['keep_path']} / {keep_path})"
                    )
                # Same delete path and same keeper: retain one physical action.
                continue

            delete_owner[delete_key] = {
                "keep_key": keep_key,
                "keep_path": keep_path,
                "group_label": group_label,
            }
            delete_paths.append(delete_path)

        if delete_paths:
            normalised.append({
                "group_index": group_index,
                "group_label": group_label,
                "keep_path": keep_path,
                "keep_key": keep_key,
                "delete_paths": delete_paths,
            })

    all_deleted = set(delete_owner)
    for plan in normalised:
        if plan["keep_key"] in all_deleted:
            errors.append(
                f"{plan['group_label']}: выбранный keeper удаляется в другой группе: "
                f"{plan['keep_path']}"
            )

    return {
        "ok": not errors,
        "errors": errors,
        "plans": normalised,
        "groups": len(normalised),
        "files": len(delete_owner),
    }


def _duplicate_rows_by_path_key(con, paths) -> dict[str, dict]:
    """Resolve live image rows by the shared platform path key.

    The fast path uses exact SQLite path matches.  A rare fallback scans live
    rows only when spelling/case/separator differences prevented an exact hit.
    """
    from core.path_utils import path_key

    requested = [str(p or "").strip() for p in (paths or []) if str(p or "").strip()]
    wanted = {path_key(p) for p in requested if path_key(p)}
    if not wanted:
        return {}

    columns = "id,path,file_name,bucket,size_bytes"
    found: dict[str, dict] = {}
    for offset in range(0, len(requested), 400):
        batch = requested[offset:offset + 400]
        placeholders = ",".join("?" for _ in batch)
        rows = con.execute(
            f"SELECT {columns} FROM images WHERE deleted=0 AND path IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            item = dict(row)
            key = path_key(item.get("path"))
            if key in wanted:
                found[key] = item

    missing = wanted.difference(found)
    if missing:
        for row in con.execute(f"SELECT {columns} FROM images WHERE deleted=0").fetchall():
            item = dict(row)
            key = path_key(item.get("path"))
            if key in missing:
                found[key] = item
                missing.discard(key)
                if not missing:
                    break
    return found


def _merge_duplicate_metadata(con, keep_id: int, source_id: int) -> None:
    """Metadata of a duplicate goes to the kept card, one tag set per site."""
    from core.database.storage import merge_card_metadata
    merge_card_metadata(con, keep_id, source_id)


def trash_duplicate_groups(settings, plans) -> dict:
    """Merge and trash a validated duplicate batch with one DB backup.

    Filesystem moves cannot be atomically committed together with SQLite, so the
    safe ordering is: validate -> one independent DB backup -> one metadata
    merge transaction -> one move_to_trash call without another backup.  A
    failed move may leave extra metadata on the keeper, but cannot lose metadata.
    """
    from pathlib import Path
    from core.database.connection import db, db_path
    from core.library_lifecycle import force_backup_database, move_to_trash
    from core.path_utils import path_key
    from core.database.storage import refresh_effective_tag_categories

    validation = validate_duplicate_group_plans(plans)
    if not validation["ok"]:
        raise ValueError("\n".join(validation["errors"]))
    prepared = list(validation["plans"])
    if not prepared:
        return {
            "deleted_files": 0,
            "errors": 0,
            "deleted_records": 0,
            "groups": 0,
            "requested_files": 0,
            "backup": "",
        }

    all_paths: list[str] = []
    for plan in prepared:
        all_paths.append(plan["keep_path"])
        all_paths.extend(plan["delete_paths"])

    # Resolve all records before the expensive backup.  Unknown/unindexed files
    # are blocked because there is no canonical DB row to receive metadata.
    with db(settings, readonly=True) as con:
        preview_rows = _duplicate_rows_by_path_key(con, all_paths)
    missing = [p for p in all_paths if path_key(p) not in preview_rows]
    if missing:
        shown = "\n".join(missing[:10])
        extra = f"\n... ещё {len(missing) - 10}" if len(missing) > 10 else ""
        raise RuntimeError(
            "Операция отменена: некоторые файлы не найдены среди живых записей SQLite:\n"
            + shown + extra
        )

    backup = force_backup_database(settings, "duplicate_delete_batch", auto=True)
    if Path(db_path(settings)).exists() and not backup:
        raise RuntimeError("Не удалось создать резервную копию базы. Удаление отменено.")

    delete_rows_by_key: dict[str, dict] = {}
    merged = 0
    with db(settings, write=True) as con:
        live_rows = _duplicate_rows_by_path_key(con, all_paths)
        missing_after_lock = [p for p in all_paths if path_key(p) not in live_rows]
        if missing_after_lock:
            raise RuntimeError(
                "Операция отменена: состав библиотеки изменился во время удаления; "
                "обновите страницу дубликатов и повторите попытку."
            )

        for plan in prepared:
            keep_row = live_rows[plan["keep_key"]]
            keep_id = int(keep_row["id"])
            for delete_path in plan["delete_paths"]:
                delete_key = path_key(delete_path)
                source_row = live_rows[delete_key]
                _merge_duplicate_metadata(con, keep_id, int(source_row["id"]))
                delete_rows_by_key[delete_key] = source_row
                merged += 1
            refresh_effective_tag_categories(con, keep_id)

    result = move_to_trash(
        settings,
        list(delete_rows_by_key.values()),
        reason="duplicate_delete",
        make_backup=False,
    )
    result = dict(result or {})
    result["backup"] = backup
    result["groups"] = int(validation["groups"] or 0)
    result["requested_files"] = int(validation["files"] or 0)
    result["metadata_merged"] = merged
    return result


def trash_duplicate_paths(settings, deleted_paths, kept_path="", keep_path=""):
    """Compatibility wrapper: single-group deletion uses the batch-safe path."""
    if keep_path and not kept_path:
        kept_path = keep_path
    paths = [str(p) for p in (deleted_paths or []) if str(p)]
    if not paths:
        return {"deleted_files": 0, "errors": 0, "deleted_records": 0}
    return trash_duplicate_groups(
        settings,
        [{
            "group_index": 1,
            "group_label": "Группа",
            "keep_path": str(kept_path or ""),
            "delete_paths": paths,
        }],
    )

def remember_duplicate_relation(settings, infos, relation="not_duplicate") -> int:
    """Remember exact content pairs and mark their current visual relation reviewed.

    The durable decision remains MD5-based, so replacing either image's content
    does not suppress a future unknown pair. ``duplicate_relations.status`` is a
    UI/cache optimisation and is updated only when the stored MD5 snapshot still
    matches the decision being recorded.
    """
    from core.database.connection import db

    relation = str(relation or "not_duplicate")
    items = []
    for info in infos or []:
        md5 = str(info.get("md5") or "").strip().lower()
        try:
            image_id = int(info.get("image_id") or 0)
        except Exception:
            image_id = 0
        if md5:
            items.append((image_id, md5))
    # One image row per content MD5 is the library invariant, but de-duplicate
    # defensively so repeated cards cannot inflate the reported decision count.
    unique = []
    seen = set()
    for image_id, md5 in items:
        key = (image_id, md5)
        if key not in seen:
            seen.add(key)
            unique.append(key)

    now = int(__import__("time").time())
    count = 0
    with db(settings, write=True) as con:
        for pos, (id_a, md5_a_raw) in enumerate(unique):
            for id_b, md5_b_raw in unique[pos + 1:]:
                if md5_a_raw == md5_b_raw:
                    continue
                md5_a, md5_b = sorted((md5_a_raw, md5_b_raw))
                con.execute(
                    "INSERT OR REPLACE INTO ignored_duplicate_pairs(md5_a,md5_b,relation,created_at) VALUES(?,?,?,?)",
                    (md5_a, md5_b, relation, now),
                )
                count += 1
                if id_a > 0 and id_b > 0 and id_a != id_b:
                    image_a, image_b = sorted((id_a, id_b))
                    snapshot_a = md5_a_raw if id_a == image_a else md5_b_raw
                    snapshot_b = md5_b_raw if id_b == image_b else md5_a_raw
                    con.execute(
                        """
                        UPDATE duplicate_relations
                        SET status=?,updated_at=?
                        WHERE image_a=? AND image_b=? AND relation='visual_phash'
                          AND md5_a=? AND md5_b=?
                        """,
                        (relation, now, image_a, image_b, snapshot_a, snapshot_b),
                    )
    return count


def ignored_duplicate_pair(settings, md5_a, md5_b) -> bool:
    a, b = sorted((str(md5_a or "").lower(), str(md5_b or "").lower()))
    if not a or not b or a == b:
        return False
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        return con.execute("SELECT 1 FROM ignored_duplicate_pairs WHERE md5_a=? AND md5_b=? LIMIT 1", (a, b)).fetchone() is not None


# UI-facing read operations: widgets depend on the service boundary, not SQL modules.
# v471: these calls can target the normal Local Booru DB or a read-only Booru
# Corpus Builder database without teaching the UI about either SQL schema.
def search_items(settings, query="", source="all", bucket="all", limit=None, offset=0, order="path", extra_where=None, extra_params=None):
    """Compatibility-preserving gallery read API.

    v469 callers historically passed query/source/bucket positionally because
    this service mirrored repository.search_items().  v471 redirected reads
    through gallery_provider but accidentally narrowed the wrapper to **kwargs,
    breaking Gallery.apply_filter().  Keep the legacy call contract while the
    provider decides whether Local Booru or Booru Corpus serves the query.
    """
    return gallery_provider.search_items(
        settings, query=query, source=source, bucket=bucket, limit=limit,
        offset=offset, order=order, extra_where=extra_where, extra_params=extra_params,
    )

def count_search_items(settings, query="", source="all", bucket="all", extra_where=None, extra_params=None):
    """Compatibility-preserving count API matching repository.count_search_items()."""
    return gallery_provider.count_search_items(
        settings, query=query, source=source, bucket=bucket,
        extra_where=extra_where, extra_params=extra_params,
    )

def enrich_items(settings, items, tag_source="all"):
    return gallery_provider.enrich_items(settings, items, tag_source=tag_source)

def candidate_tags(settings, scope="all"):
    return gallery_provider.candidate_tags(settings, scope)

def candidate_sources(settings, scope="all"):
    return gallery_provider.candidate_sources(settings, scope)

def counts(settings):
    return gallery_provider.counts(settings)

def source_unique_image_count(settings):
    return gallery_provider.source_unique_image_count(settings)

def tag_group_counts(settings, source="all"):
    return gallery_provider.tag_group_counts(settings, source=source)

def find_images_by_tag(settings, tag, scope="all", limit=None):
    return repository.find_images_by_tag(settings, tag, scope=scope, limit=limit)

def find_images_by_source(settings, source, scope="all", limit=None):
    return repository.find_images_by_source(settings, source, scope=scope, limit=limit)


def cleanup_missing_records(settings):
    """Remove database rows whose managed media no longer exists."""
    from core.database.storage import cleanup_missing
    return cleanup_missing(settings)
