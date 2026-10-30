"""Versioned booru note storage with normalized geometry."""
from __future__ import annotations

import time


def upsert_note(con, *, image_id: int, source_site: str, source_post_id: str, external_note_id: str, x: float, y: float, width: float, height: float, image_width: float, image_height: float, body: str, language: str = "", active: bool = True) -> int:
    now = int(time.time())
    iw = max(1.0, float(image_width or 1.0)); ih = max(1.0, float(image_height or 1.0))
    existing = con.execute(
        "SELECT * FROM image_notes WHERE image_id=? AND source_site=? AND external_note_id=?",
        (int(image_id), str(source_site), str(external_note_id)),
    ).fetchone()
    if existing and any(str(existing[k]) != str(v) for k, v in {
        "body": body, "x": float(x), "y": float(y), "width": float(width), "height": float(height), "is_active": 1 if active else 0,
    }.items()):
        con.execute(
            "INSERT INTO image_note_revisions(note_id,body,x,y,width,height,is_active,recorded_at) VALUES(?,?,?,?,?,?,?,?)",
            (int(existing["note_id"]), str(existing["body"] or ""), float(existing["x"]), float(existing["y"]), float(existing["width"]), float(existing["height"]), int(existing["is_active"]), now),
        )
    con.execute(
        """INSERT INTO image_notes(image_id,source_site,source_post_id,external_note_id,x,y,width,height,x_normalized,y_normalized,width_normalized,height_normalized,body,language,is_active,fetched_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(image_id,source_site,external_note_id) DO UPDATE SET
             source_post_id=excluded.source_post_id,x=excluded.x,y=excluded.y,width=excluded.width,height=excluded.height,
             x_normalized=excluded.x_normalized,y_normalized=excluded.y_normalized,width_normalized=excluded.width_normalized,height_normalized=excluded.height_normalized,
             body=excluded.body,language=excluded.language,is_active=excluded.is_active,fetched_at=excluded.fetched_at,updated_at=excluded.updated_at""",
        (int(image_id), str(source_site), str(source_post_id), str(external_note_id), float(x), float(y), float(width), float(height), float(x)/iw, float(y)/ih, float(width)/iw, float(height)/ih, str(body), str(language), 1 if active else 0, now, now),
    )
    row = con.execute(
        "SELECT note_id FROM image_notes WHERE image_id=? AND source_site=? AND external_note_id=?",
        (int(image_id), str(source_site), str(external_note_id)),
    ).fetchone()
    return int(row[0])


def notes_for_image(con, image_id: int, *, include_inactive: bool = False) -> list[dict]:
    """Return source notes in normalized coordinates for the gallery overlay."""
    where = "image_id=?" if include_inactive else "image_id=? AND is_active=1"
    rows = con.execute(
        f"""SELECT note_id,image_id,source_site,source_post_id,external_note_id,
                   x,y,width,height,x_normalized,y_normalized,width_normalized,height_normalized,
                   body,language,is_active,fetched_at,updated_at
            FROM image_notes WHERE {where}
            ORDER BY source_site COLLATE NOCASE,note_id""",
        (int(image_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def text_regions_for_image(con, image_id: int) -> list[dict]:
    """Return local detector regions using the same overlay contract as site notes."""
    try:
        rows = con.execute(
            """SELECT region_id,image_id,model_name,x_normalized,y_normalized,width_normalized,height_normalized,confidence,created_at
               FROM image_text_regions WHERE image_id=? ORDER BY confidence DESC,region_id""",
            (int(image_id),),
        ).fetchall()
    except Exception:
        return []
    out = []
    for row in rows:
        item = dict(row)
        item.update({
            "source_site": "local-text-detector",
            "body": f"Локальная модель обнаружила текст ({float(item.get('confidence') or 0.0):.0%})",
            "language": "",
            "is_active": 1,
            "local_detector": True,
        })
        out.append(item)
    return out
