"""Offline pHash pre-match against the already tagged local library."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from core.database.connection import db
from core.file_hash_cache import get_or_compute_phash, lookup as lookup_hash_cache
from core.tagger.hashing import VIDEO_EXTS, file_phash, video_frame_image
from core.vptree import VPTree, hamming, similarity_pct
from core.derived_cache_db import open_selected_cache, selected_backend_from_connection

_FOUND_STATUSES = {"found", "tagged", "partial"}
_FOUND_BUCKETS = {"found", "downloaded_found"}


def _source_phash(settings, path: Path) -> str:
    cached = lookup_hash_cache(settings, path) or {}
    value = str(cached.get("hash_phash") or "").strip().lower()
    if value:
        return value
    candidate = video_frame_image(path, settings=settings, log=None) if path.suffix.lower() in VIDEO_EXTS else path
    value, _hit = get_or_compute_phash(
        settings,
        candidate,
        lambda p: file_phash(p, settings=settings, log=None),
    )
    return str(value or "").strip().lower()


def find_local_phash_match(settings, path, *, max_distance: int = 4):
    """Return the nearest trustworthy tagged image or ``None``.

    Only live FOUND-like rows with tags are eligible.  The current source path
    is excluded, and the result includes the exact Hamming distance/similarity.
    """
    source = Path(path)
    phash = _source_phash(settings, source)
    if len(phash) != 16:
        return None
    limit_distance = max(0, int(max_distance))
    with db(settings, readonly=True) as con:
        with open_selected_cache(
            settings, "vp_tree", main_connection=con, require_fresh=False, allow_main_fallback=True
        ) as (tree_con, _cache_info):
            matches = VPTree(tree_con).search(phash, max_distance=limit_distance)
        if not matches:
            return None
        ids = [int(image_id) for image_id, _distance in matches[:64]]
        placeholders = ",".join("?" for _ in ids)
        rows = con.execute(
            f"""
            SELECT i.id, i.path, i.hash_phash, i.bucket,
                   COALESCE((SELECT pf.status FROM processed_files pf
                             WHERE pf.media_path=i.path ORDER BY pf.processed_at DESC LIMIT 1), '') AS status,
                   (SELECT COUNT(*) FROM image_tags it WHERE it.image_id=i.id) AS tag_count
            FROM images i
            WHERE i.id IN ({placeholders}) AND i.deleted=0
            """,
            ids,
        ).fetchall()
    # A stale external VP generation is still a safe subset index, but its old
    # hash must never be trusted as a positive result.  Recompute distance from
    # current main.db values and discard changed/deleted endpoints.
    distance_by_id = {}
    for row in rows:
        current_hash = str(row["hash_phash"] or "").strip().lower()
        distance = hamming(phash, current_hash)
        if len(current_hash) == 16 and distance <= limit_distance:
            distance_by_id[int(row["id"])] = int(distance)
    candidates = []
    for row in rows:
        candidate_path = Path(str(row["path"] or ""))
        if not candidate_path.exists() or str(candidate_path) == str(source):
            continue
        status = str(row["status"] or "").lower()
        bucket = str(row["bucket"] or "").lower()
        if status not in _FOUND_STATUSES and bucket not in _FOUND_BUCKETS:
            continue
        if int(row["tag_count"] or 0) <= 0:
            continue
        distance = distance_by_id.get(int(row["id"]))
        if distance is None:
            continue
        candidates.append({
            "image_id": int(row["id"]),
            "path": str(candidate_path),
            "phash": str(row["hash_phash"] or ""),
            "distance": int(distance),
            "similarity": float(similarity_pct(phash, str(row["hash_phash"] or ""))),
        })
    candidates.sort(key=lambda item: (item["distance"], -item["similarity"], item["image_id"]))
    return candidates[0] if candidates else None



def promote_phash_to_library(settings, original_path, phash: str = "") -> bool:
    """Promote a parser-cached pHash into ``images`` and the SQLite VP-tree.

    The common fresh-preflight case has no FOUND row yet.  Probe that case on a
    readonly connection first so thousands of files do not acquire the global
    writer lock or execute an empty ``BEGIN IMMEDIATE``.  The write transaction
    rechecks the row to remain race-safe when persistence wins concurrently.
    """
    original = Path(original_path)
    value = str(phash or "").strip().lower()
    if not value:
        cached = lookup_hash_cache(settings, original) or {}
        value = str(cached.get("hash_phash") or "").strip().lower()
    if len(value) != 16:
        return False

    query = """
        SELECT i.id, i.hash_phash,
               EXISTS(SELECT 1 FROM vp_tree v WHERE v.image_id=i.id) AS in_tree
        FROM processed_files pf
        JOIN images i ON i.path=pf.media_path
        WHERE pf.original_path=? AND i.deleted=0
          AND LOWER(COALESCE(pf.status,'')) IN ('found','tagged','partial','partial_match')
        ORDER BY pf.processed_at DESC, i.id DESC
        LIMIT 1
    """

    with db(settings, readonly=True) as con:
        row = con.execute(query, (str(original),)).fetchone()
        external_backend = selected_backend_from_connection(con, "vp_tree") == "external"
    if row is None:
        return False
    image_id = int(row["id"] if hasattr(row, "keys") else row[0])
    existing = str((row["hash_phash"] if hasattr(row, "keys") else row[1]) or "").strip().lower()
    in_tree = bool(row["in_tree"] if hasattr(row, "keys") else row[2])
    if existing and existing != value:
        return False
    if existing == value and (in_tree or external_backend):
        return True

    with db(settings, write=True) as con:
        row = con.execute(query, (str(original),)).fetchone()
        if row is None:
            return False
        image_id = int(row["id"] if hasattr(row, "keys") else row[0])
        existing = str((row["hash_phash"] if hasattr(row, "keys") else row[1]) or "").strip().lower()
        if existing and existing != value:
            # Never replace an independently indexed signature with one from a
            # different path; that would invalidate VP-tree partitions.
            return False
        if not existing:
            con.execute("UPDATE images SET hash_phash=? WHERE id=?", (value, image_id))
        VPTree(con).insert(image_id, value)
    return True


def load_local_match_bundle(settings, image_id: int) -> dict:
    """Load tags, source URLs and source-scoped groups for one library row."""
    image_id = int(image_id)
    category_by_tag: dict[int, str] = {}
    with db(settings, readonly=True) as con:
        backend = selected_backend_from_connection(con, "image_effective_tag_category")
        tag_rows = con.execute(
            """
            SELECT t.id AS tag_id, t.name, COALESCE(NULLIF(t.category,''), 'general') AS category
            FROM image_tags it
            JOIN tags t ON t.id=it.tag_id
            WHERE it.image_id=? ORDER BY t.id
            """,
            (image_id,),
        ).fetchall()
        source_rows = con.execute(
            """
            SELECT s.id, s.url FROM image_sources ims
            JOIN sources s ON s.id=ims.source_id
            WHERE ims.image_id=? ORDER BY s.id
            """,
            (image_id,),
        ).fetchall()
        provenance_rows = con.execute(
            """
            SELECT s.url, t.id AS tag_id, t.name,
                   COALESCE(NULLIF(ist.category,''), t.category, 'general') AS category,
                   COALESCE(ist.acquisition, '') AS acquisition, ist.source_id
            FROM image_source_tags ist
            JOIN sources s ON s.id=ist.source_id
            JOIN tags t ON t.id=ist.tag_id
            WHERE ist.image_id=? ORDER BY s.id, t.id
            """,
            (image_id,),
        ).fetchall()
        if backend == "main":
            try:
                category_by_tag = {
                    int(row[0]): str(row[1] or "general")
                    for row in con.execute(
                        "SELECT tag_id,category FROM image_effective_tag_category WHERE image_id=?",
                        (image_id,),
                    ).fetchall()
                }
            except Exception:
                category_by_tag = {}

    if backend == "external":
        try:
            with open_selected_cache(
                settings, "image_effective_tag_category", require_fresh=True, allow_main_fallback=False
            ) as (cache_con, _cache_info):
                category_by_tag = {
                    int(row[0]): str(row[1] or "general")
                    for row in cache_con.execute(
                        "SELECT tag_id,category FROM image_effective_tag_category WHERE image_id=?",
                        (image_id,),
                    ).fetchall()
                }
        except Exception:
            # Exact single-image fallback from current provenance.  This is cheap
            # and keeps local pHash tag copying correct while the external cache
            # is stale or being rebuilt.
            rank = {
                "artist": 0, "contributor": 1, "character": 2, "copyright": 3,
                "species": 4, "meta": 5, "lore": 6, "invalid": 7, "parody": 8,
                "language": 9, "category": 10, "pages": 11, "general": 99,
            }
            best: dict[int, tuple[int, int, str]] = {}
            for row in provenance_rows:
                tag_id = int(row["tag_id"] or 0)
                category = str(row["category"] or "general").lower()
                candidate = (rank.get(category, 50), int(row["source_id"] or 0), category)
                if tag_id and (tag_id not in best or candidate < best[tag_id]):
                    best[tag_id] = candidate
            category_by_tag = {tag_id: value[2] for tag_id, value in best.items()}


    groups = defaultdict(list)
    tags = []
    for row in tag_rows:
        name = str(row["name"] or "").strip()
        if not name:
            continue
        tags.append(name)
        groups[str(category_by_tag.get(int(row["tag_id"] or 0), row["category"] or "general"))].append(name)

    source_groups: dict[str, dict] = {}
    source_methods: dict[str, str] = {}
    for row in provenance_rows:
        url = str(row["url"] or "").strip()
        name = str(row["name"] or "").strip()
        if not url or not name:
            continue
        source_groups.setdefault(url, defaultdict(list))[str(row["category"] or "general")].append(name)
        source_methods.setdefault(url, str(row["acquisition"] or "local_phash_match"))

    source_tag_groups = []
    for url, grouped in source_groups.items():
        source_tag_groups.append({
            "url": url,
            "groups": {key: list(values) for key, values in grouped.items()},
            "method": "local_phash_match",
            "copied_from_acquisition": source_methods.get(url, ""),
        })

    return {
        "tags": tags,
        "groups": {key: list(values) for key, values in groups.items()},
        "sources": [str(row["url"] or "") for row in source_rows if str(row["url"] or "").strip()],
        "source_tag_groups": source_tag_groups,
    }


__all__ = ["find_local_phash_match", "load_local_match_bundle", "promote_phash_to_library"]
