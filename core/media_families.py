"""Ordered media-family repository used by gallery and completion lanes."""
from __future__ import annotations

import time


def ensure_family(con, *, family_source: str, family_type: str, external_family_id: str, title: str = "", author: str = "", total_items: int = 0, order_confidence: float = 1.0) -> int:
    now = int(time.time())
    con.execute(
        """INSERT INTO media_families(family_type,family_source,external_family_id,title,author,total_items,order_confidence,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(family_source,family_type,external_family_id) DO UPDATE SET
             title=CASE WHEN excluded.title!='' THEN excluded.title ELSE media_families.title END,
             author=CASE WHEN excluded.author!='' THEN excluded.author ELSE media_families.author END,
             total_items=MAX(media_families.total_items,excluded.total_items),
             order_confidence=MAX(media_families.order_confidence,excluded.order_confidence),
             updated_at=excluded.updated_at""",
        (str(family_type), str(family_source), str(external_family_id), str(title), str(author), int(total_items or 0), float(order_confidence or 0), now, now),
    )
    row = con.execute(
        "SELECT family_id FROM media_families WHERE family_source=? AND family_type=? AND external_family_id=?",
        (str(family_source), str(family_type), str(external_family_id)),
    ).fetchone()
    return int(row[0])


def set_family_item(con, *, family_id: int, family_index: int, image_id: int | None = None, external_item_id: str = "", page_label: str = "", relation_type: str = "page", missing: bool = False, source_url: str = "") -> None:
    con.execute(
        """INSERT INTO media_family_items(family_id,family_index,image_id,external_item_id,page_label,relation_type,is_missing,source_url,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(family_id,family_index) DO UPDATE SET
             image_id=excluded.image_id, external_item_id=excluded.external_item_id,
             page_label=excluded.page_label, relation_type=excluded.relation_type,
             is_missing=excluded.is_missing, source_url=excluded.source_url, updated_at=excluded.updated_at""",
        (int(family_id), int(family_index), image_id, str(external_item_id), str(page_label), str(relation_type), 1 if missing else 0, str(source_url), int(time.time())),
    )


def ordered_family_items(con, family_id: int):
    return con.execute(
        "SELECT * FROM media_family_items WHERE family_id=? ORDER BY family_index, image_id",
        (int(family_id),),
    ).fetchall()
