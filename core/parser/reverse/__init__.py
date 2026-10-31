"""Reverse image search services in the order they are tried: a file goes to the next one only when the one before
did not find it.

By default (v5.44.3, the owner's word: «очередью iqdb>snao», as the stages of the old program) iqdb.org first,
SauceNAO last: its few searches a day (an account's daily limit — at the owner about a hundred) go only to the files
the others did not find.  «SauceNAO первым» (``reverse_saucenao_first``, «Сайты и ключи»; the default of v5.28–5.44)
is quicker while its searches last.  A service paused for a while (SauceNAO's limit, a failure of its server) is
passed over: the file goes on to the next one (core/parser/engine.reverse_next), and a miss of the others is then not
trusted (deferred)."""
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
    return SAUCENAO_FIRST if (settings or {}).get("reverse_saucenao_first", False) else IQDB_FIRST


def build_reverse(ctx, sites) -> list:
    return [cls(ctx, sites) for cls in reverse_order(ctx.settings)]
