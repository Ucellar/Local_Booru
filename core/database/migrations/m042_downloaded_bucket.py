"""Migration 042: what the program downloaded is «Скачанные».

Pictures of the grabber, the subscriptions and the extension (``output/subscriptions/<site>/<query>/<time>/``)
were given the section «original», so the gallery's «Скачанные» never showed them; and a library kept under a
folder named «Downloads» was «downloaded» as a whole.  ``media_utils.bucket_for_path`` tells them apart now;
the live cards whose section changes with it are given the new one.  Cards in the trash keep theirs (it
decides where «Восстановить» puts the file back).
"""
from __future__ import annotations

NAME = "downloaded_bucket"
_DONE_KEY = "downloaded_bucket_v1"


def is_applied(con) -> bool:
    return con.execute("SELECT 1 FROM meta WHERE key=?", (_DONE_KEY,)).fetchone() is not None


def apply(con) -> None:
    from core.media_utils import bucket_for_path
    changes = []
    for image_id, path, bucket in con.execute("SELECT id, path, COALESCE(bucket,'') FROM images WHERE deleted=0"):
        new = bucket_for_path(str(path or ""))
        old = str(bucket or "")
        if new != old and (new.startswith("downloaded") or old.startswith("downloaded")):
            changes.append((new, int(image_id)))
    con.executemany("UPDATE images SET bucket=? WHERE id=?", changes)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, '1')", (_DONE_KEY,))
