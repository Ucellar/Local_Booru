"""(v5.44.27) What a run would say about every file in the same words — said once in a while as a count.

The owner's journal of 1.11.2026: of its 2449 lines 1827 were «уже отвечали о нём раньше, не спрашиваю: Fluffle» (one
a file: the answers of reverse search are remembered, v5.44.18) and 198 «SauceNAO: отложено — пауза (HTTP 500)» (each
file that came to SauceNAO during its own five-minute rest after a failure of its server).  The lines that matter — a
site switched off for the run, the finds, the failures — were lost among them, and the page keeps the last 5000 lines
only (``core/parser/jobs.LOG_KEEP``).  (v5.44.29) The next journal, of a run after a restart: 3982 of its 3999 lines
were «▶ md5=…», one a file taken into work (4255 in its first minute).

A run (core/parser/runner.py) gives its engine a :class:`Tally`: the files taken into work, the sites and the services
that answered about a file before (they are not asked again) and the files «добор» asks the new sites about are
counted (``count``); a line that would be said about many files in the same words is said for the first one and
counted for the rest (``first``); every five minutes, after «ВРЕМЯ», the counts are said (``lines``).  Without a run
(one file, ``ParserEngine.process``) every line is said as before."""
from __future__ import annotations

import threading
from collections import Counter

FILES = "files"          # files taken into work (their MD5 known; the gallery did not have them): «▶ md5=…»
ADDING = "adding"        # «добор»: files processed before, asked of the sites added since
SITES = "sites"          # (v5.44.29) sites that said «нет» about a file's MD5 lately (a file no run finished)
REVERSE = "reverse"      # services of reverse search that answered about a file before
AGAIN = "again"          # (v5.44.36) videos a service found nothing about by one picture: asked with the next
_WORDS = {
    FILES: "ФАЙЛЫ {span}: взято в работу {files}",
    ADDING: "ДОБОР НОВЫХ САЙТОВ {span}: {files} — спрашиваю о них только новые сайты: {names}",
    SITES: "САЙТЫ {span}: {files} — сайты уже ответили «нет» о них недавно, снова не спрашиваю: {names}",
    REVERSE: "ОБРАТНЫЙ ПОИСК {span}: {files} — уже отвечали о них раньше, снова не спрашиваю: {names}",
    AGAIN: "ВИДЕО {span}: {files} — по одному кадру не найдено, спрашиваю ещё раз кадром, из которого сайт делает "
           "превью: {names}",
}
_ORDER = (FILES, ADDING, SITES, REVERSE, AGAIN)


def files_ru(n: int) -> str:
    """«1 файл», «3 файла», «12 файлов»."""
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        word = "файл"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "файла"
    else:
        word = "файлов"
    return f"{n} {word}"


class Tally:
    """The counts of a run since they were said last (the threads of a run add to them at once)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._groups: dict = {}               # FILES / ADDING / SITES / REVERSE / AGAIN -> [files, Counter of names]
        self._said: dict = {}                 # (head, a line said for one file) -> the files it was true of since

    def count(self, group: str, names=()) -> None:
        """One more file of ``group``; ``names`` — the sites or services it is about (each counted)."""
        names = [str(name) for name in names or () if name]
        with self._lock:
            entry = self._groups.setdefault(group, [0, Counter()])
            entry[0] += 1
            entry[1].update(names)

    def known(self, names) -> None:
        """A file that the services ``names`` of reverse search answered about before (not asked about it again)."""
        if names:
            self.count(REVERSE, names)

    def first(self, text: str, head: str = "") -> bool:
        """True — say ``text``: the first file it is true of since the counts were said; False — it is counted.
        ``head`` — the start of its line without the file («[REVERSE:…]», «[MD5:…]»): the count goes to the same
        channel of the journal as the line (core/parser/jobs.channel_of)."""
        key = (head, text)
        with self._lock:
            if key in self._said:
                self._said[key] += 1
                return False
            self._said[key] = 0
            return True

    def lines(self, minutes: float) -> list:
        """What was counted since the last time, as the lines of the journal (and start over): «ФАЙЛЫ за 5 мин: взято
        в работу 4255 файлов», «САЙТЫ за 5 мин: 4100 файлов — сайты уже ответили «нет» о них недавно, снова не
        спрашиваю: gelbooru.com 4100, rule34.xxx 4079», «ОБРАТНЫЙ ПОИСК за 5 мин: 245 файлов — уже отвечали о них
        раньше, снова не спрашиваю: Fluffle 245, IQDB 30»; a line said for one file — how many more it was true of."""
        with self._lock:
            groups, said = self._groups, self._said
            self._groups, self._said = {}, {}
        span = f"за {max(1.0, float(minutes)):.0f} мин"
        out = []
        for group in _ORDER:
            files, names = groups.get(group, (0, None))
            if files:
                listed = ", ".join(f"{name} {count}" for name, count in names.most_common())
                out.append(_WORDS[group].format(span=span, files=files_ru(files), names=listed))
        for (head, text), more in said.items():
            if more:
                out.append(f"{head or ' '} {text} — так же ещё {files_ru(more)} {span}")
        return out


__all__ = ["Tally", "files_ru", "FILES", "ADDING", "SITES", "REVERSE", "AGAIN"]
