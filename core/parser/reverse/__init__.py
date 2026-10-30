"""Reverse image search services in the order they are tried.

By default (v5.28) SauceNAO first — it answers in a couple of seconds at the account's pace and knows many sites —
and iqdb.org last: it takes half a minute and more a search, and with it first every file waited for it.  «IQDB
первым» (``reverse_saucenao_first`` off, «Сайты и ключи») keeps SauceNAO's daily searches for the files the others
did not find.  A service paused for a while (SauceNAO's limit, a failure of its server) is passed over: the file goes
on to the next one (core/parser/engine.reverse_next), and a miss of the others is then not trusted (deferred)."""
from __future__ import annotations

from core.parser.reverse.ascii2d import Ascii2d
from core.parser.reverse.base import ReverseService
from core.parser.reverse.e621_iqdb import E621Iqdb
from core.parser.reverse.iqdb import DanbooruIqdb, Iqdb
from core.parser.reverse.saucenao import SauceNao

__all__ = ["ReverseService", "build_reverse", "SauceNao", "reverse_order"]

SAUCENAO_FIRST = (SauceNao, E621Iqdb, DanbooruIqdb, Ascii2d, Iqdb)
IQDB_FIRST = (Iqdb, DanbooruIqdb, E621Iqdb, Ascii2d, SauceNao)


def reverse_order(settings: dict) -> tuple:
    return SAUCENAO_FIRST if (settings or {}).get("reverse_saucenao_first", True) else IQDB_FIRST


def build_reverse(ctx, sites) -> list:
    return [cls(ctx, sites) for cls in reverse_order(ctx.settings)]
