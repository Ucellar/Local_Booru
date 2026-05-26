from __future__ import annotations
from .connection import db, db_path


def optimize(settings):
    with db(settings, write=True) as con:
        try: con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception: pass
        try: con.execute("ANALYZE")
        except Exception: pass
        try: con.execute("PRAGMA optimize")
        except Exception: pass
    return {"db": str(db_path(settings))}


def stats(settings):
    with db(settings, readonly=True) as con:
        tables = {}
        for t in ("images","tags","image_tags","sources","image_sources","processed_files","delete_log"):
            try: tables[t] = int(con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] or 0)
            except Exception: tables[t] = 0
    return tables
