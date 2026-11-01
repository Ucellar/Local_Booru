"""What the reverse-search services already answered about a file (v5.44.18).

The old program kept it (``reverse_branch_status``, ui/tagger/workers.py of the old project: «a 60-second IQDB miss
is not repeated on every resumed run»); the new parser of v5.0 kept the table and stopped reading and writing it — a
retry of Брак asked iqdb.org (half a minute and more a picture) about every file again.  The owner (1.11.2026): «в
ориге было так: если файл прошёл проверку, прога запоминала… и не перепаршивала его, и так же наоборот».

A service that gave a definite answer about a file — found it («done_match»), did not («done_miss»), named only its
sources («source_only») — is not asked about it again: a retry asks only the services that never answered (new ones,
ones that were off, failed or deferred).  «skipped» of the old program (a branch cancelled, a file it could not take)
is no answer: asked again.  A row counts for its service's ``journal_revision`` and later ones — a service whose
search changed is asked again (e621 IQDB: 2, it runs without an account since v5.44.14).  Rows are kept by the file's
identity in the database (``FileJob.original_path``), as the old program kept them.  «Сайты и ключи» → «Помнить
ответы обратного поиска» (``parser_reverse_remember``) off: every service is asked again (answers are still written).

The source a Брак record keeps (``no_match_items.source_url``: a picture's page on an art site) goes to the relay
again on a retry (:func:`saved_source`): the service that named it may not be asked, and the sites may be others now.
"""
from __future__ import annotations

from pathlib import Path

ANSWERED = ("done_match", "done_miss", "source_only")


def remembered(settings: dict, original_path: str, services) -> dict:
    """{journal_key: (status, unix time)} — the definite answers about the file that still count for these
    services."""
    from core.database.storage import reverse_branch_rows
    revisions = {s.journal_key: int(s.journal_revision or 1) for s in services if getattr(s, "journal_key", "")}
    if not revisions:
        return {}
    out: dict = {}
    for row in reverse_branch_rows(settings, original_path):
        key = str(row.get("branch_key") or "").strip().lower()
        status = str(row.get("status") or "")
        if key not in revisions or status not in ANSWERED or int(row.get("scan_revision") or 1) < revisions[key]:
            continue
        when = int(row.get("updated_at") or 0)
        if key not in out or when > out[key][1]:
            out[key] = (status, when)
    return out


def record(settings: dict, original_path: str, service, status: str, reason: str = "") -> None:
    """The service's answer about the file: «done_match», «done_miss», «source_only» (or the old program's
    «skipped» — no answer)."""
    from core.database.storage import mark_reverse_branch_status
    key = getattr(service, "journal_key", "")
    if not key or not original_path:
        return
    mark_reverse_branch_status(settings, original_path, key, status=status, reason=reason,
                               scan_revision=int(getattr(service, "journal_revision", 1) or 1))


def saved_source(settings: dict, original_path: str) -> dict | None:
    """The picture's source its Брак record keeps — {"url", "label", "similarity"} as a service's ``source_only`` —
    or None."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT source_url, source_label, source_similarity FROM no_match_items "
                          "WHERE original_path=? AND +active=1", (str(Path(original_path)),)).fetchone()
    if row is None or not str(row["source_url"] or "").startswith("http"):
        return None
    return {"url": str(row["source_url"]), "label": str(row["source_label"] or "Брак"),
            "similarity": float(row["source_similarity"] or 0.0)}
