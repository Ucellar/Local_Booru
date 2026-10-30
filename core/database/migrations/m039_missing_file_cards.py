"""Migration 039: cards hidden by «Проверка библиотеки» come back into view.

The old repair marked live cards whose file was missing with ``deleted=1`` only: such rows were
shown nowhere (not in the gallery, not in Trash) and «Очистить корзину» never removed them.
A row whose file has been written back since then is a live card again; the others get
``lifecycle='missing'`` («файл пропал»): listed in Trash, removable there (the row only — purging
never deletes a file of such a card), and healed by a re-parse that meets the same bytes.
"""
from __future__ import annotations

import time
from pathlib import Path

NAME = "missing_file_cards"


def is_applied(con) -> bool:
    return con.execute(
        "SELECT 1 FROM images WHERE deleted=1 AND COALESCE(lifecycle,'') NOT IN ('trash','missing') LIMIT 1"
    ).fetchone() is None


def apply(con) -> None:
    now = int(time.time())
    rows = con.execute(
        "SELECT id, path FROM images WHERE deleted=1 AND COALESCE(lifecycle,'') NOT IN ('trash','missing')"
    ).fetchall()
    for row in rows:
        image_id, path = int(row[0]), str(row[1] or "")
        try:
            back = bool(path) and Path(path).is_file()
        except OSError:
            back = False
        if back:
            con.execute("UPDATE images SET deleted=0, lifecycle='archive', trashed_at=0 WHERE id=?", (image_id,))
        else:
            con.execute(
                """UPDATE images SET lifecycle='missing',
                       trashed_at=CASE WHEN COALESCE(trashed_at,0)>0 THEN trashed_at ELSE ? END,
                       original_media_path=CASE WHEN COALESCE(original_media_path,'')='' THEN path ELSE original_media_path END
                   WHERE id=?""",
                (now, image_id),
            )
