"""Settings forms of the pages, described once: what a field is (a switch, a number with bounds, a choice,
a line of text, a folder), how it is shown and how a value from the page is checked before it is saved.

A page lists its sections as ``Section`` objects; ``render_rows`` draws one as a group of rows — a setting a row
(v5.44.161) —, ``clean`` turns what the page sent into settings (or an error naming the field), so a value outside
the bounds never reaches the settings file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from webui.html import esc


@dataclass(frozen=True)
class Field:
    key: str
    kind: str                       # "bool", "int", "float", "choice", "text", "path"
    label: str
    hint: str = ""                  # one line under the name
    default: object = None
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    choices: tuple = ()             # ((value, label), ...) for "choice"
    suffix: str = ""
    path_kind: str = ""             # "folder" / "file": the window's dialog for «Выбрать…»
    placeholder: str = ""
    secret: bool = False            # never sent back to the page; an empty field keeps the stored value
    more: str = ""                  # (v5.44.161) the long explanation, behind «Подробнее» of the setting's row


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    lead: str = ""
    fields: tuple = ()
    extra: str = ""                 # markup after the fields (buttons of actions), already escaped
    note: str = ""                  # a hint under the save button
    danger: bool = False
    keys: tuple = field(default=())

    def all_keys(self) -> tuple:
        return tuple(f.key for f in self.fields)


class FieldError(ValueError):
    def __init__(self, item: Field, message: str):
        super().__init__(f"«{item.label}»: {message}")
        self.field = item


def _number(item: Field, raw):
    text = str(raw if raw is not None else "").strip().replace(",", ".")
    if text == "" or len(text) > 40:
        raise FieldError(item, "нужно число")
    try:
        number = float(text)
    except (TypeError, ValueError):
        raise FieldError(item, "нужно число") from None
    if not math.isfinite(number):              # «nan», «inf», «1e999»: not a value of any setting
        raise FieldError(item, "нужно число")
    if item.kind == "int":
        if number != int(number):
            raise FieldError(item, "нужно целое число")
        value = int(number)
    else:
        value = number
    if item.lo is not None and value < item.lo:
        raise FieldError(item, f"не меньше {_fmt(item.lo)}")
    if item.hi is not None and value > item.hi:
        raise FieldError(item, f"не больше {_fmt(item.hi)}")
    return value


def _fmt(value) -> str:
    number = float(value)
    if not math.isfinite(number):              # a broken stored value: an empty field, not a broken page
        return ""
    return str(int(number)) if number == int(number) else str(number)


def clean(section: Section, values: dict, current: dict) -> dict:
    """The settings ``values`` (from the page) give for ``section``; raises FieldError."""
    out: dict = {}
    for item in section.fields:
        raw = values.get(item.key)
        if item.kind == "bool":
            out[item.key] = bool(raw) and raw not in ("0", "false", "off", "no")
        elif item.kind in ("int", "float"):
            out[item.key] = _number(item, raw)
        elif item.kind == "choice":
            allowed = {str(v): v for v, _label in item.choices}
            kept = current.get(item.key)
            if kept is not None and str(kept) not in allowed:     # a value set elsewhere stays possible
                allowed[str(kept)] = kept
            if str(raw) not in allowed:
                raise FieldError(item, "нет такого варианта")
            out[item.key] = allowed[str(raw)]
        else:                                   # text, path
            text = str(raw if raw is not None else "").strip()
            if "\x00" in text or "\n" in text or "\r" in text or len(text) > 2000:
                raise FieldError(item, "недопустимый текст")
            if item.secret and not text:
                continue                         # an empty field keeps the stored key
            out[item.key] = text
    return out


def value_of(item: Field, settings: dict):
    value = settings.get(item.key, item.default)
    return item.default if value is None else value


# ── (v5.44.161, the mockup «Настройки» approved 11.11.2026) a setting — a row ─────────────────────────────────────
HINT_LINE = 96                  # letters of a row's explanation shown at once; a longer one — behind «Подробнее»
SEGMENT_CHOICES = 3             # a choice of so few short words is a row of buttons, not a list
SEGMENT_WORD = 16


def split_hint(hint: str, limit: int = HINT_LINE) -> tuple:
    """(the line a row shows, what goes behind «Подробнее») — one line of explanation per setting.  A text written
    as one long line (a setting gives its long part itself — ``Field.more``) is cut where it reads best: at the end
    of the last sentence that fits, else of the last clause («;», then «:» or « — »), never inside brackets; with no
    such place — at a word, and «Подробнее» has the whole of it."""
    text = " ".join(str(hint or "").split())
    if len(text) <= limit:
        return text, ""
    ends = {". ": 0, "; ": 0, ": ": 0, " — ": 0}
    depth = 0
    for at, letter in enumerate(text[:limit]):
        if letter in "(«":
            depth += 1
        elif letter in ")»":
            depth = max(0, depth - 1)
        elif depth == 0:
            for mark in ends:
                if text.startswith(mark, at):
                    ends[mark] = at
    best = next((at for at in (ends[". "], ends["; "], max(ends[": "], ends[" — "])) if at >= 24), 0)
    if best:
        rest = text[best:].lstrip(" .;:—")
        whole = text[best] == "." and text[:1].isupper()       # a sentence keeps its full stop
        return text[:best].rstrip(" ,") + ("." if whole else ""), rest[:1].upper() + rest[1:]
    at = text.rfind(" ", 0, limit)
    return text[:at if at > 0 else limit].rstrip(" ,") + "…", text


def _segmented(item: Field) -> bool:
    return (item.kind == "choice" and 2 <= len(item.choices) <= SEGMENT_CHOICES
            and all(len(str(label)) <= SEGMENT_WORD for _v, label in item.choices))


def _control_html(item: Field, value, fid: str) -> str:
    """The control of a row: a switch, a row of buttons, a list, a number, a line of text."""
    name = esc(item.key)
    if item.kind == "bool":
        return f'<input type="checkbox" class="switch" id="{esc(fid)}" name="{name}"{" checked" if value else ""}>'
    if item.kind in ("int", "float"):
        attrs = "".join(f' {key}="{esc(_fmt(v))}"' for key, v in (("min", item.lo), ("max", item.hi), ("step", item.step))
                        if v is not None)
        if item.kind == "float" and item.step is None:
            attrs += ' step="any"'
        shown = _fmt(value) if isinstance(value, (int, float)) else esc(value)
        suffix = f'<span class="unit">{esc(item.suffix)}</span>' if item.suffix else ""
        return f'<input type="number" id="{esc(fid)}" name="{name}" value="{esc(shown)}"{attrs}>{suffix}'
    if item.kind == "choice":
        known = {str(v) for v, _label in item.choices}
        if _segmented(item) and str(value) in known:
            buttons = "".join(
                f'<label><input type="radio" name="{name}" value="{esc(v)}"{" checked" if str(v) == str(value) else ""}>'
                f'<span>{esc(label)}</span></label>' for v, label in item.choices)
            return f'<span class="sx-seg" role="radiogroup" id="{esc(fid)}" aria-label="{esc(item.label)}">{buttons}</span>'
        options = "".join(f'<option value="{esc(v)}"{" selected" if str(v) == str(value) else ""}>{esc(label)}</option>'
                          for v, label in item.choices)
        if str(value) not in known:
            # set elsewhere (the old settings page, a profile): shown and kept, not silently replaced
            options = f'<option value="{esc(value)}" selected>как сейчас ({esc(value)})</option>' + options
        return f'<select id="{esc(fid)}" name="{name}">{options}</select>'
    if item.secret:
        stored = bool(str(value or "").strip())
        placeholder = "сохранено — оставьте пустым, чтобы не менять" if stored else (item.placeholder or "")
        return (f'<input type="password" id="{esc(fid)}" name="{name}" autocomplete="off" spellcheck="false"'
                f' placeholder="{esc(placeholder)}">')
    choose = (f'<button type="button" data-action="settings-choose" data-key="{name}">Выбрать…</button>'
              if item.kind == "path" and item.path_kind else "")
    return (f'<input type="text" id="{esc(fid)}" name="{name}" value="{esc(value or "")}" spellcheck="false"'
            f' placeholder="{esc(item.placeholder)}">{choose}')


def row_html(label: str, hint: str, control: str, *, fid: str = "", key: str = "", cls: str = "", wide: bool = False,
             hint_html: str = "", more: str = "") -> str:
    """A row of the page «Настройки»: the name, one line of explanation (``hint`` — text; ``hint_html`` — markup
    already escaped, shown whole), the control on the right.  ``more`` — the long explanation behind «Подробнее»
    (none given and the line is long — the line is cut, :func:`split_hint`).  ``wide`` — the control under the name
    (a long field)."""
    short, rest = (" ".join(str(hint or "").split()), " ".join(more.split())) if more else split_hint(hint)
    name = f'<label class="sx-name" for="{esc(fid)}">{esc(label)}</label>' if fid else f'<span class="sx-name">{esc(label)}</span>'
    said = f'<span class="sx-hint">{esc(short)}</span>' if short else ""
    if hint_html:
        said += f'<span class="sx-hint">{hint_html}</span>'
    if rest:                # «Подробнее» stands at the end of the line; opened — the explanation under it
        said += f'<details class="sx-more"><summary>Подробнее</summary><p>{esc(rest)}</p></details>'
    said = f'<div class="sx-said">{said}</div>' if said else ""
    attrs = f' data-key="{esc(key)}"' if key else ""
    classes = " ".join(c for c in ("sx-row", "sx-wide" if wide else "", cls) if c)
    return (f'<div class="{classes}"{attrs}><div class="sx-what">{name}{said}</div>'
            f'<div class="sx-ctl">{control}</div></div>')


def field_row(item: Field, settings: dict, section_id: str) -> str:
    fid = f"{section_id}-{item.key}"
    value = value_of(item, settings)
    control = _control_html(item, value, fid)
    seg = _segmented(item) and 'class="sx-seg"' in control
    wide = item.kind in ("text", "path") and not item.secret
    return row_html(item.label, item.hint, control, fid="" if seg else fid, key=item.key, wide=wide, more=item.more)


def render_rows(section: Section, settings: dict, *, title: str | None = None, rows_after: str = "") -> str:
    """A section as a group of rows (v5.44.161): its title above (``title`` — "" for none), every setting a row, then
    the section's own markup (the buttons of its actions).  Saved as before: the form of the section, by the page's
    one «Сохранить» (settings.js).  ``rows_after`` — rows of the page's own (escaped markup) inside the same group."""
    heading = section.title if title is None else title
    lead = ""
    if section.lead:
        short, rest = split_hint(section.lead, 150)
        lead = f'<div class="sx-lead"><p>{esc(short)}</p>' + (
            f'<details class="sx-more"><summary>Подробнее</summary><p>{esc(rest)}</p></details>' if rest else "") + "</div>"
    rows = "".join(field_row(f, settings, section.id) for f in section.fields) + rows_after
    # the hidden button keeps Enter in a field saving; a refusal is said under the section's rows
    save = ('<button type="submit" class="form-submit" hidden tabindex="-1">Сохранить</button>'
            '<p class="form-error" role="alert" hidden></p>') if section.fields else ""
    note = f'<p class="sx-note">{section.note}</p>' if section.note else ""
    group = f'<div class="sx-group">{rows}</div>' if rows else ""
    form = (f'<form class="settings-form" data-form="settings" data-section="{esc(section.id)}">'
            f'{group}{save}{note}</form>') if section.fields else group
    extra = f'<div class="block-extra sx-extra">{section.extra}</div>' if section.extra else ""
    cls = "settings-block sx-block danger-zone" if section.danger else "settings-block sx-block"
    head = f'<h3 class="sx-title">{esc(heading)}</h3>' if heading else ""
    return (f'<section class="{cls}" id="set-{esc(section.id)}" data-title="{esc(section.title)}">{head}{lead}{form}'
            f'{extra}</section>')
