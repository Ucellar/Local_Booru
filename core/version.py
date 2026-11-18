"""(v5.44.165) The program's version — one place.

Until then the program had no version of its own to show: only the commits and ``CHANGELOG.md`` knew it, and nobody
looking at the window could tell which version was running (or whether an update had started to work).  Now it is
said by the window (the brand's hint in the header, «Настройки» under its sections), by the startup journal, the
report of a crash and the diagnostics' file, and it is what tells the program that the folder holds another version
than the one running (``core/started_code.update_waiting`` — «Установлено обновление … — перезапустите»).

Every version changes this line together with the heading of its entry in ``CHANGELOG.md``; the static check
(``check_contracts.py``, check 7) fails when the two differ.
"""
VERSION = "5.44.194"


def short() -> str:
    """«5.44» — for the name the program gives itself to the sites (``core/site_agent``): their logs do not need the
    third number, and a new one every day would look like a new program."""
    return ".".join(VERSION.split(".")[:2])


def order(text: str) -> tuple:
    """A version as numbers, to tell the newer of two («5.44.9» < «5.44.10»); what is not a number counts as 0."""
    out = []
    for part in str(text or "").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)
