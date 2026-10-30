"""Settings forms of the pages, described once: what a field is (a switch, a number with bounds, a choice,
a line of text, a folder), how it is shown and how a value from the page is checked before it is saved.

A page lists its sections as ``Section`` objects; ``render`` draws one as a form, ``clean`` turns what
the page sent into settings (or an error naming the field), so a value outside the bounds never reaches
the settings file.
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
    hint: str = ""
    default: object = None
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    choices: tuple = ()             # ((value, label), ...) for "choice"
    suffix: str = ""
    path_kind: str = ""             # "folder" / "file": the window's dialog for «Выбрать…»
    placeholder: str = ""
    secret: bool = False            # never sent back to the page; an empty field keeps the stored value


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


def _field_html(item: Field, settings: dict, section_id: str) -> str:
    fid = f"{section_id}-{item.key}"
    hint = f' <small>{esc(item.hint)}</small>' if item.hint else ""
    value = value_of(item, settings)
    if item.kind == "bool":
        return (f'<label class="check setting"><input type="checkbox" name="{esc(item.key)}"'
                f'{" checked" if value else ""}> {esc(item.label)}{hint}</label>')
    if item.kind in ("int", "float"):
        attrs = "".join(f' {name}="{esc(_fmt(v))}"' for name, v in (("min", item.lo), ("max", item.hi), ("step", item.step))
                        if v is not None)
        if item.kind == "float" and item.step is None:
            attrs += ' step="any"'
        shown = _fmt(value) if isinstance(value, (int, float)) else esc(value)
        suffix = f' <span class="unit">{esc(item.suffix)}</span>' if item.suffix else ""
        return (f'<div class="field-row"><label for="{esc(fid)}">{esc(item.label)}</label>'
                f'<input type="number" id="{esc(fid)}" name="{esc(item.key)}" value="{esc(shown)}"{attrs}>{suffix}{hint}</div>')
    if item.kind == "choice":
        options = "".join(f'<option value="{esc(v)}"{" selected" if str(v) == str(value) else ""}>{esc(label)}</option>'
                          for v, label in item.choices)
        if str(value) not in {str(v) for v, _label in item.choices}:
            # set elsewhere (the old settings page, a profile): shown and kept, not silently replaced
            options = f'<option value="{esc(value)}" selected>как сейчас ({esc(value)})</option>' + options
        return (f'<div class="field-row"><label for="{esc(fid)}">{esc(item.label)}</label>'
                f'<select id="{esc(fid)}" name="{esc(item.key)}">{options}</select>{hint}</div>')
    if item.secret:
        stored = bool(str(value or "").strip())
        placeholder = "сохранено — оставьте пустым, чтобы не менять" if stored else (item.placeholder or "")
        return (f'<div class="field-row"><label for="{esc(fid)}">{esc(item.label)}</label>'
                f'<input type="password" id="{esc(fid)}" name="{esc(item.key)}" autocomplete="off" spellcheck="false"'
                f' placeholder="{esc(placeholder)}">{hint}</div>')
    choose = (f'<button type="button" data-action="settings-choose" data-key="{esc(item.key)}">Выбрать…</button>'
              if item.kind == "path" and item.path_kind else "")
    return (f'<div class="field-row"><label for="{esc(fid)}">{esc(item.label)}</label>'
            f'<input type="text" id="{esc(fid)}" name="{esc(item.key)}" value="{esc(value or "")}" spellcheck="false"'
            f' placeholder="{esc(item.placeholder)}">{choose}{hint}</div>')


def render(section: Section, settings: dict) -> str:
    checks = "".join(_field_html(f, settings, section.id) for f in section.fields if f.kind == "bool")
    rows = "".join(_field_html(f, settings, section.id) for f in section.fields if f.kind != "bool")
    checks_html = f'<div class="setting-checks">{checks}</div>' if checks else ""
    lead = f'<p class="hint">{section.lead}</p>' if section.lead else ""
    # Saved by the page's one «Сохранить» (the bar that shows when something changed, settings.js); the hidden
    # button keeps Enter in a field saving; a refusal is said under the section's fields.
    save = ('<button type="submit" class="form-submit" hidden tabindex="-1">Сохранить</button>'
            '<p class="form-error" role="alert" hidden></p>') if section.fields else ""
    note = f'<p class="hint">{section.note}</p>' if section.note else ""
    form = (f'<form class="settings-form" data-form="settings" data-section="{esc(section.id)}">'
            f'{checks_html}{rows}{save}{note}</form>') if section.fields else ""
    extra = f'<div class="block-extra">{section.extra}</div>' if section.extra else ""
    cls = "settings-block danger-zone" if section.danger else "settings-block"
    return (f'<section class="{cls}" id="set-{esc(section.id)}"><h2>{esc(section.title)}</h2>{lead}{form}'
            f'{extra}</section>')
