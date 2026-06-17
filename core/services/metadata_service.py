"""Metadata operations for cards/downloads without exposing SQLite modules to UI."""
from __future__ import annotations
from pathlib import Path
from core.database.connection import db
from core.database.storage import (
    ensure_image,
    replace_media_tag_groups,
    remove_media_tag_link,
    remove_media_source_link,
    found_media_path_by_md5,
)


def image_id_for_path(settings: dict, media_path: str | Path, *, create=False, status="") -> int | None:
    path = str(Path(media_path))
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (path,)).fetchone()
    if row:
        return int(row["id"])
    if create:
        return int(ensure_image(settings, path, status=status))
    return None


def set_rating(settings: dict, image_id: int, rating: int) -> None:
    with db(settings, write=True) as con:
        con.execute("UPDATE images SET rating=? WHERE id=?", (int(rating), int(image_id)))


def get_rating(settings: dict, image_id: int) -> int:
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT COALESCE(rating,0) rating FROM images WHERE id=?", (int(image_id),)).fetchone()
    return int(row["rating"] if row else 0)


def raw_metadata_for_path(settings: dict, media_path: str | Path):
    with db(settings, readonly=True) as con:
        return con.execute(
            """SELECT rm.post_url, rm.file_url, rm.site FROM raw_metadata rm
               JOIN images i ON i.id=rm.image_id WHERE i.path=?""", (str(Path(media_path)),)
        ).fetchone()

__all__ = [
    "ensure_image", "replace_media_tag_groups", "remove_media_tag_link", "remove_media_source_link",
    "found_media_path_by_md5", "image_id_for_path", "set_rating", "get_rating",
    "raw_metadata_for_path",
]
