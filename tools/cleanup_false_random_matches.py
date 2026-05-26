"""Cleanup helper for false HTML/random MD5 matches.

Default mode is DRY RUN. It removes metadata only when --apply is passed.
It targets the specific bad class of records created by an over-permissive
HTML fallback: source URLs like /posts/random?tags=md5:... or search/list URLs
that were treated as post matches and usually produced 1-3 garbage tags.

Run from the project root:
    python tools/cleanup_false_random_matches.py
    python tools/cleanup_false_random_matches.py --apply
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.settings import load_settings  # noqa: E402
from core.database.connection import db  # noqa: E402

BAD_URL_LIKE = [
    "%/posts/random%",
    "%/post/random%",
    "%tags=md5%",
    "%page=post&s=list%",
    "%/posts?%",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete bad tag/source links")
    ap.add_argument("--max-tags", type=int, default=5, help="only clear records with <= this many tags")
    args = ap.parse_args()

    settings = load_settings()
    where = " OR ".join(["s.url LIKE ?" for _ in BAD_URL_LIKE])
    with db(settings, write=args.apply) as con:
        rows = con.execute(f"""
            SELECT i.id, i.path, s.url, COUNT(it.tag_id) AS tag_count
            FROM images i
            JOIN image_sources isrc ON isrc.image_id = i.id
            JOIN sources s ON s.id = isrc.source_id
            LEFT JOIN image_tags it ON it.image_id = i.id
            WHERE ({where})
            GROUP BY i.id, s.url
            HAVING tag_count <= ?
            ORDER BY i.id
        """, (*BAD_URL_LIKE, args.max_tags)).fetchall()

        print(f"Found suspicious records: {len(rows)}")
        for r in rows[:100]:
            print(f"  image_id={r['id']} tags={r['tag_count']} path={r['path']} source={r['url']}")
        if len(rows) > 100:
            print(f"  ... and {len(rows)-100} more")

        if args.apply and rows:
            ids = [int(r["id"]) for r in rows]
            q = ",".join("?" for _ in ids)
            con.execute(f"DELETE FROM image_tags WHERE image_id IN ({q})", ids)
            con.execute(f"DELETE FROM image_sources WHERE image_id IN ({q})", ids)
            con.execute("DELETE FROM sources WHERE id NOT IN (SELECT source_id FROM image_sources)")
            con.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM image_tags)")
            print("Applied cleanup: removed bad tag/source links. Media files were not deleted.")
        elif not args.apply:
            print("Dry run only. Add --apply to remove bad tag/source links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
