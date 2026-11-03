"""What category a tag is: what most sites say of it (v5.44.49).

Before, the library took the first category other than «Общие» that any write gave a tag and kept it for good
(``tags.category``, «sticky»).  One answer that called «female», «breasts», «cum» characters (a site whose people edited
the tag's type, a page read wrongly, a set the library had filled in by its own categories) made them characters for
the whole library — and since 5.44.11 every card showed them so: a site's «general» tag took the library's category,
and the downloads of the subscriptions, the extension's pages and the mended sets were even written so (5.44.10–5.44.48).

Now:

- the evidence is the sites' own sets of tags (``image_source_tags``) that came with categories — a set with at least
  one tag not «general»; a site that sends no categories (every tag «general») says nothing about them;
- a tag's category (:func:`decide`): e621's «species» stays (only e621 has that category, the others call those tags
  «general»); otherwise the category other than «general» most sets give it, when at least :data:`SHARE` of the sets
  give it (a site's category is finer than another's «general»: e621's «Мета» of a tag rule34.xxx calls «general»), else
  «general» (a few sets of thousands calling «female» a character are a slip); a tie — the more particular category;
- a site's write does not change the category of a tag that many sets already know (:func:`written`): the answer is
  counted (``tag_category_review.pending``) and the background duty looks at the tag again (:func:`review`) when
  there may be enough of such answers to change it (``needed`` — the lead of the winner the last time); a new tag, or
  one few sets have, takes the site's category at once, as before;
- once, after the upgrade (:func:`repair`, the mark :data:`REPAIR_KEY` in ``meta``): every tag's category from the
  sites' sets; the categories the library itself had put into sets (:data:`FILLED_METHODS` since :data:`FILLED_SINCE`,
  :data:`GUESS_METHODS`) where they differ from that — back to the site's «general»; a name's category
  (:data:`SLIP_CATEGORIES`) that only a few sets give a tag most sets call «general» (:data:`OUTLIER_SHARE`) —
  «general» there too.

The card's page (``repository._effective_source_groups``) shows a set without categories in the library's
categories, and on «все сайты» the category most sites give, when one of the card's sites gives it.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Callable, Iterable

from .connection import db

_LOG = logging.getLogger("local_booru.tag_consensus")

CATEGORY_PRIORITY = {
    "artist": 0, "contributor": 1, "character": 2, "copyright": 3,
    "species": 4, "meta": 5, "lore": 6, "invalid": 7,
    "parody": 8, "language": 9, "category": 10, "pages": 11, "general": 99,
}
SPECIES_HOSTS = ("e621.net", "e926.net")       # the only sites with the category «species»
META_TAGS = {"artist_request"}                  # always «meta» (storage._META_TAG_OVERRIDES)
FEW_SETS = 50                                   # a tag of fewer sets takes a site's category at once
SHARE = 0.10                                    # a category other than «general» needs this share of the sets
OUTLIER_SHARE = 0.02                            # a category this rare against «general» is the site's slip
SLIP_CATEGORIES = ("artist", "character", "copyright")   # …of these: a word of every post taken for a name
REPAIR_KEY = "tag_consensus_v54449"
# The sets the library filled in by its own categories: 5.44.10 (the extension's pages, the mended sets) and 5.44.11
# (the subscriptions' and the grabber's downloads) put each «general» tag of a site's answer into the category the
# library knew it by — and a wrong category of the library went into them.
FILLED_SINCE = 1793404800                       # 2026-10-31 00:00 UTC (5.44.10)
FILLED_METHODS = ("subscription", "site_answer", "browser_page_seen", "browser_manual_import", "browser_quality_upgrade")
# «Обслуживание → Перераскладка general-only» put the library's categories into a site's set that had none
GUESS_METHODS = ("maintenance_general_only_fast_local", "maintenance_general_only_fast_cache_after_network")



def _untrusted(until: int, row: str = "ist") -> str:
    """SQL: a row ``row`` of ``image_source_tags`` the library filled in itself — written by :data:`FILLED_METHODS`
    from :data:`FILLED_SINCE` until ``until`` (the start of 5.44.49 here: what it writes is the site's word), or by
    :data:`GUESS_METHODS`."""
    filled = ",".join(f"'{m}'" for m in FILLED_METHODS)
    guess = ",".join(f"'{m}'" for m in GUESS_METHODS)
    return (f"(({row}.acquisition IN ({filled}) AND {row}.updated_at >= {int(FILLED_SINCE)} "
            f"AND {row}.updated_at < {int(until)}) OR {row}.acquisition IN ({guess}))")


def upgraded_at(con) -> int:
    """When this library was first opened by 5.44.49 (migration 046): the sets written since are the sites' word."""
    try:
        row = con.execute("SELECT applied_at FROM schema_migrations WHERE version=46 AND status='applied'").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        row = None
    return int(row[0]) if row and row[0] else int(time.time())


def priority(category: str) -> int:
    return CATEGORY_PRIORITY.get(str(category or "general").lower(), 50)


def _cat(value) -> str:
    return str(value or "general").strip().lower() or "general"


# ── the decision ────────────────────────────────────────────────────────────────────────────────────────────────
def decide(by_host: dict) -> tuple:
    """(category, lead) from {host: {category: sets}} — the sets with categories that have the tag; (None, 0) — no
    such set.

    e621's own majority «species» wins (the other sites have no such category: their «general» is no word against
    it).  Otherwise the category other than «general» most sets give the tag (a tie — the more particular one), when at
    least :data:`SHARE` of the sets give it: a site's category is more particular than another's «general» (e621's «Мета»
    of a tag rule34.xxx calls «general»), but a few sets of thousands calling «female» a character are a slip.  Else
    «general».  ``lead`` — about how many answers the other way it takes to change the decision (at least 1)."""
    total: Counter = Counter()
    for votes in (by_host or {}).values():
        for cat, n in (votes or {}).items():
            if int(n or 0) > 0:
                total[_cat(cat)] += int(n)
    if not total:
        return None, 0
    for host in SPECIES_HOSTS:
        own = {_cat(k): int(v or 0) for k, v in ((by_host or {}).get(host) or {}).items()}
        species = own.get("species", 0)
        rest = sum(own.values()) - species
        if species > rest:
            return "species", max(1, species - rest)
    everything = sum(total.values())
    ranked = sorted(((cat, n) for cat, n in total.items() if cat != "general"),
                    key=lambda kv: (-kv[1], priority(kv[0]), kv[0]))
    if ranked and ranked[0][1] >= SHARE * everything:
        best, n = ranked[0]
        lead = int(n / SHARE) - everything + 1           # «general» answers that take its share under SHARE
        if len(ranked) > 1:                              # answers of the runner-up's category that pass it
            other, m = ranked[1]
            lead = min(lead, n - m + (1 if priority(best) < priority(other) else 0))
        return best, max(1, lead)
    top = ranked[0][1] if ranked else 0                  # answers that give the first other category its share
    return "general", max(1, math.ceil((SHARE * everything - top) / (1 - SHARE)))


def outliers(by_host: dict, decided: str | None) -> list:
    """The names' categories (:data:`SLIP_CATEGORIES`) a few sets give a tag that most sets call «general» — a site's
    slip (:data:`OUTLIER_SHARE`): «female» a character.  e621's own finer categories (species, lore, meta…) are not
    slips, they stay the site's word."""
    if decided != "general":
        return []
    total: Counter = Counter()
    for votes in (by_host or {}).values():
        for cat, n in (votes or {}).items():
            total[_cat(cat)] += int(n or 0)
    general = total.get("general", 0)
    return sorted(cat for cat, n in total.items()
                  if cat in SLIP_CATEGORIES and n > 0 and n <= OUTLIER_SHARE * general)


# ── the evidence ────────────────────────────────────────────────────────────────────────────────────────────────
def collect(con, tag_ids: Iterable[int] | None = None, *, trusted_until: int | None = None) -> dict:
    """{tag_id: {host: Counter(category -> sets)}} over the sites' sets that came with categories.  ``tag_ids`` —
    only these (each by its index); None — every tag (one pass over the table: the repair).  ``trusted_until`` —
    without what the library itself put into sets until then (:func:`_untrusted`)."""
    out: dict = defaultdict(lambda: defaultdict(Counter))
    untrusted = f"NOT {_untrusted(trusted_until)}" if trusted_until is not None else "1"
    if tag_ids is None:
        rows = con.execute(f"""
            WITH sets AS (
                SELECT DISTINCT ist.image_id, ist.source_id FROM image_source_tags ist
                WHERE ist.category NOT IN ('general', '') AND {untrusted}
            )
            SELECT ist.tag_id, s.host, LOWER(ist.category), COUNT(*)
            FROM image_source_tags ist
            JOIN sets ON sets.image_id=ist.image_id AND sets.source_id=ist.source_id
            JOIN sources s ON s.id=ist.source_id
            WHERE {untrusted}
            GROUP BY ist.tag_id, s.host, LOWER(ist.category)
        """)
        for tag_id, host, cat, n in rows:
            out[int(tag_id)][str(host or "")][_cat(cat)] += int(n)
        return out
    for tag_id in tag_ids:
        rows = con.execute(f"""
            SELECT s.host, LOWER(ist.category), COUNT(*)
            FROM image_source_tags ist JOIN sources s ON s.id=ist.source_id
            WHERE ist.tag_id=? AND {untrusted} AND EXISTS (
                SELECT 1 FROM image_source_tags x WHERE x.image_id=ist.image_id AND x.source_id=ist.source_id
                AND x.category NOT IN ('general', ''))
            GROUP BY s.host, LOWER(ist.category)
        """, (int(tag_id),))
        for host, cat, n in rows:
            out[int(tag_id)][str(host or "")][_cat(cat)] += int(n)
    return out


# ── the writes ──────────────────────────────────────────────────────────────────────────────────────────────────
def written(con, tag_id: int, category: str, known: str, *, categorized: bool) -> None:
    """A write gave the tag ``tag_id`` the category ``category``; the library has it as ``known``.  ``categorized`` —
    the set (or the card's tags) came with categories: a «general» of a site that sends none says nothing.

    Few sets have the tag yet: a category other than «general» is taken at once (a new artist of a site is an artist
    on every card, as before 5.44.49).  Otherwise nothing changes now: the answer is counted, and the background duty
    looks at the tag again when there may be enough of such answers (:func:`review`)."""
    cat, old = _cat(category), _cat(known)
    if cat == old or (cat == "general" and not categorized):
        return
    if cat != "general" and old == "general" and _few_sets(con, tag_id):
        con.execute("UPDATE tags SET category=? WHERE id=?", (cat, int(tag_id)))
    _count(con, tag_id)


def _few_sets(con, tag_id: int) -> bool:
    row = con.execute("SELECT COUNT(*) FROM (SELECT 1 FROM image_source_tags WHERE tag_id=? LIMIT ?)",
                      (int(tag_id), FEW_SETS)).fetchone()
    return int(row[0] or 0) < FEW_SETS


def _count(con, tag_id: int) -> None:
    try:
        con.execute("INSERT INTO tag_category_review(tag_id, pending) VALUES(?, 1) "
                    "ON CONFLICT(tag_id) DO UPDATE SET pending=pending+1", (int(tag_id),))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):     # a base before migration 046 (a check's old schema): nothing to count
            raise


# ── the background duty ────────────────────────────────────────────────────────────────────────────────────────
def due(settings: dict, limit: int = 200) -> list:
    """The tags to look at again: as many answers the other way came as their winner's lead was."""
    with db(settings, readonly=True) as con:
        try:
            return [int(r[0]) for r in con.execute(
                "SELECT tag_id FROM tag_category_review WHERE pending > 0 AND pending >= needed "
                "ORDER BY reviewed_at, tag_id LIMIT ?", (int(limit),))]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise


def review(settings: dict, *, limit: int = 200, budget: float = 10.0, log: Callable[[str], None] | None = None) -> dict:
    """Look again at the tags due (:func:`due`): each one's category by what the sites' sets say now.  ``budget`` —
    seconds at most.  {"looked": n, "changed": [(name, old, new)]}."""
    started = time.monotonic()
    ids = due(settings, limit)
    looked, changed = 0, []
    for tag_id in ids:
        if time.monotonic() - started > budget:
            break
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT name, normalized_name, category FROM tags WHERE id=?", (tag_id,)).fetchone()
            pending = con.execute("SELECT pending FROM tag_category_review WHERE tag_id=?", (tag_id,)).fetchone()
            votes = collect(con, [tag_id]).get(tag_id, {}) if row is not None else {}
        looked += 1
        decided, lead = decide(votes)
        with db(settings, write=True) as con:
            if row is None:
                con.execute("DELETE FROM tag_category_review WHERE tag_id=?", (tag_id,))
                continue
            if str(row["normalized_name"]) in META_TAGS:
                decided = "meta"
            old = _cat(row["category"])
            if decided and decided != old:
                con.execute("UPDATE tags SET category=? WHERE id=?", (decided, tag_id))
                changed.append((str(row["name"]), old, decided))
            seen = int(pending[0] or 0) if pending else 0
            con.execute("UPDATE tag_category_review SET pending=MAX(0, pending-?), needed=?, reviewed_at=? "
                        "WHERE tag_id=?", (seen, max(1, int(lead)), int(time.time()), tag_id))
    if changed and log:
        log("Категории тегов по большинству сайтов: " + ", ".join(f"{n}: {a} → {b}" for n, a, b in changed[:20])
            + (f" и ещё {len(changed) - 20}" if len(changed) > 20 else ""))
    return {"looked": looked, "changed": changed}


# ── the repair, once ────────────────────────────────────────────────────────────────────────────────────────────
def repaired(settings: dict) -> bool:
    with db(settings, readonly=True) as con:
        try:
            return con.execute("SELECT 1 FROM meta WHERE key=?", (REPAIR_KEY,)).fetchone() is not None
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return True                     # no library yet: nothing to put right
            raise


def repair(settings: dict, *, log: Callable[[str], None] | None = None, until: int | None = None,
           stop: Callable[[], bool] | None = None) -> dict:
    """Every tag's category by what the sites' own sets say (without what the library put into them); the sets the
    library filled in — back to the site's «general» where they differ; a site's rare slip against «general» —
    «general».  Done once (:data:`REPAIR_KEY`); returns what changed.  ``until`` — the end of the sets the library
    filled in (by default the first start of 5.44.49 here: :func:`upgraded_at`).  ``stop()`` true (the exit) — left
    for the next start, nothing half-done is marked.

    The whole table is read on a reading connection (the parser and the downloads write meanwhile); the writes are by
    the rows' keys, each checked again (a set written meanwhile is a site's word: it is not touched)."""
    log = log or (lambda _m: None)
    stop = stop or (lambda: False)
    started = time.monotonic()
    with db(settings, readonly=True) as con:
        until = int(until if until is not None else upgraded_at(con))
        votes = collect(con, trusted_until=until)
        tags = {int(r[0]): (str(r[1]), str(r[2]), _cat(r[3]))
                for r in con.execute("SELECT id, name, normalized_name, category FROM tags")}
        filled = con.execute(f"SELECT ist.rowid, ist.image_id, ist.tag_id, ist.category FROM image_source_tags ist "
                             f"WHERE {_untrusted(until)} AND ist.category NOT IN ('general', '')").fetchall()
    if stop():
        return {"stopped": True}
    decisions = {tag_id: decide(v) for tag_id, v in votes.items() if tag_id in tags}
    final = {tag_id: old for tag_id, (_name, _norm, old) in tags.items()}
    changed = []
    for tag_id, (decided, _lead) in decisions.items():
        name, norm, old = tags[tag_id]
        if norm in META_TAGS:
            decided = "meta"
        if decided and decided != old:
            changed.append((tag_id, name, old, decided))
            final[tag_id] = decided
    # the library's own fill: what differs from the tag's category (as it is now) goes back to the site's «general»
    wrong_fill = [(int(rowid), int(image_id), _cat(cat)) for rowid, image_id, tag_id, cat in filled
                  if _cat(cat) != final.get(int(tag_id), "general")]
    slips = {tag_id: outliers(votes[tag_id], decided) for tag_id, (decided, _lead) in decisions.items()}
    slips = {k: v for k, v in slips.items() if v}
    touched: set = set()
    filled_fixed = slipped = 0
    with db(settings, write=True) as con:
        for tag_id, _name, _old, new in changed:
            con.execute("UPDATE tags SET category=? WHERE id=?", (new, tag_id))
        for rowid, image_id, cat in wrong_fill:
            n = con.execute(f"UPDATE image_source_tags SET category='general' WHERE rowid=? AND category=? "
                            f"AND {_untrusted(until, 'image_source_tags')}", (rowid, cat)).rowcount
            if n:
                filled_fixed += n
                touched.add(image_id)
        for tag_id, cats in slips.items():
            marks = ",".join("?" * len(cats))
            touched.update(int(r[0]) for r in con.execute(
                f"SELECT DISTINCT image_id FROM image_source_tags WHERE tag_id=? AND category IN ({marks})",
                (tag_id, *cats)))
            slipped += con.execute(f"UPDATE image_source_tags SET category='general' WHERE tag_id=? AND category IN ({marks})",
                                   (tag_id, *cats)).rowcount
        now = int(time.time())
        con.executemany(
            "INSERT INTO tag_category_review(tag_id, pending, needed, reviewed_at) VALUES(?, 0, ?, ?) "
            "ON CONFLICT(tag_id) DO UPDATE SET pending=0, needed=excluded.needed, reviewed_at=excluded.reviewed_at",
            [(tag_id, max(1, int(lead)), now) for tag_id, (decided, lead) in decisions.items() if decided])
    _refresh_effective(settings, touched)
    summary = {
        "tags": len(decisions), "changed": [(n, a, b) for _i, n, a, b in changed],
        "filled_fixed": int(filled_fixed), "slips_fixed": int(slipped or 0), "cards": len(touched),
        "evidence": {tags[i][0]: {h: dict(c) for h, c in votes[i].items()} for i, *_rest in changed[:40]},
        "seconds": round(time.monotonic() - started, 1),
    }
    with db(settings, write=True) as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    (REPAIR_KEY, json.dumps({k: v for k, v in summary.items() if k != "evidence"}
                                            | {"at": int(time.time()), "changed": summary["changed"][:200]},
                                            ensure_ascii=False)))
    if changed or filled_fixed or slipped:
        shown = ", ".join(f"{n}: {a} → {b}" for n, a, b in summary["changed"][:20])
        log(f"Категории тегов по большинству сайтов: исправлено тегов {len(changed)}"
            + (f" ({shown}{' …' if len(changed) > 20 else ''})" if shown else "")
            + f"; в наборах сайтов — {summary['filled_fixed']} категорий, которые библиотека поставила сама, и "
              f"{summary['slips_fixed']} редких ошибок сайтов; карточек {len(touched)}; {summary['seconds']} с")
        for name, sites in list(summary["evidence"].items())[:20]:
            log(f"  {name}: " + "; ".join(f"{h} " + ", ".join(f"{c} {n}" for c, n in sorted(v.items()))
                                         for h, v in sorted(sites.items())))
    return summary


def _refresh_effective(settings: dict, images: set) -> None:
    """The cards' effective categories (the facets, the local pHash copies) after their sets changed."""
    if not images:
        return
    from .storage import refresh_effective_tag_categories
    ids = sorted(images)
    for start in range(0, len(ids), 500):
        with db(settings, write=True) as con:
            for image_id in ids[start:start + 500]:
                refresh_effective_tag_categories(con, image_id)


__all__ = ["CATEGORY_PRIORITY", "FEW_SETS", "SHARE", "SLIP_CATEGORIES", "FILLED_METHODS", "FILLED_SINCE", "GUESS_METHODS", "OUTLIER_SHARE",
           "REPAIR_KEY", "collect", "decide", "due", "outliers", "priority", "repair", "repaired", "review",
           "upgraded_at", "written"]
