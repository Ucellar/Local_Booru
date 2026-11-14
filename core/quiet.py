"""(v5.44.167) An error a place of the program lets pass — counted, and said to the program's log once a place and a
kind, instead of nowhere.

The project's rule 1 is «no ``except Exception: pass``»: an error swallowed in silence is a failure nobody can find.
The review of the program (9.11.2026) counted 280 such places in the files older than the rule.  Each of them says it
now — ``let_pass("<its module and function>")`` in the ``except`` where ``pass`` stood: the place goes on exactly as
it did, the log has one line of it (the first time an error of that type passes there in a run of the program; after
that it is only counted — a place inside a loop does not fill the log), and the developer's report
(``core/diagnostics.py``) lists the counts (:func:`counts`).

It never raises, and imports nothing of the program when it is imported (the first lines of ``app.py`` and
``core/paths.py`` use it): the error's own words go through ``core.redaction.sanitize_text`` — a key in an address
never reaches a log — and are left out when that module cannot be had (too early, or the program is closing).

A new ``except Exception: pass`` fails ``tools/dev_checks/check_contracts.py`` (check 8): catch the error that is
expected by its type, or say it (``let_pass``, a line of the journal).
"""
from __future__ import annotations

import logging
import sys

_LOG = logging.getLogger("local_booru.quiet")
_SEEN: dict = {}                # (where, the error's type) -> how many times it passed (this run of the program)
WORDS = 200                     # of the error's own words in the log's line at most


def passed(where: str) -> None:
    """Called inside an ``except`` that goes on, where ``pass`` stood: the error being handled is counted, and said to
    the log the first time its kind passes at this place.  Outside an ``except`` — nothing."""
    try:
        kind, exc, _tb = sys.exc_info()
        if kind is None:
            return
        key = (str(where), kind.__name__)
        times = _SEEN.get(key, 0) + 1           # (no lock: an ``except`` of a finalizer may be here on the same
        _SEEN[key] = times                      #  thread — a count lost to a race is nothing, a lock held is a hang)
        if times > 1 or sys.is_finalizing():
            return
        words = ""
        try:
            from core.redaction import sanitize_text
            words = sanitize_text(str(exc))[:WORDS]
        except Exception:  # noqa: BLE001 - too early for that module, or the program is closing: the type alone
            words = ""
        _LOG.info("пропущена ошибка в %s: %s%s (дальше такие здесь только считаются)", where, kind.__name__,
                  f": {words}" if words else "")
    except Exception:  # noqa: BLE001 - the log itself is gone (the program is closing): there is nobody to tell
        return


def counts() -> list:
    """[(where, the error's type, times)] of this run of the program — the most frequent first."""
    items = [(where, kind, times) for (where, kind), times in list(_SEEN.items())]
    return sorted(items, key=lambda item: (-item[2], item[0], item[1]))
