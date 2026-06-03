"""Human-friendly numerical search query parser for Local Booru.

Accepts queries like:
  "размер файла больше 50мб"
  "ширина больше 2000"
  "рейтинг не меньше 4"
  "videos longer than 1 minute"

Returns structured ParsedFilter objects, NOT raw SQL.
SQL generation is separate (to_sql_conditions).

Architecture:
  1. Tokenizer   — splits text into chunks
  2. Normalizer  — lowercases, strips accents
  3. AliasMap    — maps human words → canonical (field, op, unit)
  4. FuzzyMatch  — handles typos via Levenshtein distance
  5. Parser      — assembles tokens into ParsedFilter
  6. SQL builder — ParsedFilter → (sql_fragment, params)
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ParsedFilter:
    """One parsed numerical condition."""
    field: str          # canonical field name, e.g. "filesize"
    operator: str       # ">", ">=", "<", "<=", "="
    value: float        # normalized value (bytes, seconds, pixels, etc.)
    raw: str = ""       # original text that produced this filter
    display: str = ""   # human-readable summary for live preview

    def to_display(self) -> str:
        if self.display:
            return self.display
        op_ru = {">" : "больше", ">=" : "≥", "<" : "меньше", "<=" : "≤", "=" : "="}
        return f"{self.field} {op_ru.get(self.operator, self.operator)} {self.value}"


@dataclass
class ParseResult:
    """Result of parsing a full search query."""
    filters: list[ParsedFilter]   # numerical/property conditions
    tags: list[str]               # regular text tags
    unknown_tokens: list[str]     # tokens we couldn't parse
    suggestions: list[str]        # did-you-mean hints


# ── SQL field mapping ─────────────────────────────────────────────────────────

# Maps canonical field → SQL expression (or column name)
SQL_FIELDS: dict[str, str] = {
    "filesize":   "COALESCE(i.size_bytes, 0)",
    "width":      "COALESCE(i.width, 0)",
    "height":     "COALESCE(i.height, 0)",
    "rating":     "COALESCE(i.rating, 0)",
    "duration":   "COALESCE(i.duration, 0)",
    "tag_count":  "(SELECT COUNT(*) FROM image_tags WHERE image_id=i.id)",
}

# Unit → multiplier to base unit
UNIT_MULTIPLIERS: dict[str, float] = {
    "bytes":   1,
    "kb":      1024,
    "mb":      1024 ** 2,
    "gb":      1024 ** 3,
    "seconds": 1,
    "minutes": 60,
    "hours":   3600,
    "px":      1,
}


# ── Alias loading ─────────────────────────────────────────────────────────────

_ALIASES_PATH = Path(__file__).parent / "aliases.json"

def _load_aliases() -> dict:
    try:
        return json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"fields": {}, "operators": {}, "units": {}}

_ALIASES: dict = _load_aliases()

# Build reverse lookup: phrase → canonical
def _build_reverse(section: str) -> dict[str, str]:
    rev: dict[str, str] = {}
    for canonical, aliases in _ALIASES.get(section, {}).items():
        rev[canonical.lower()] = canonical  # canonical maps to itself
        for alias in aliases:
            rev[alias.lower()] = canonical
    return rev

def _rebuild_maps():
    global _FIELD_MAP, _OP_MAP, _UNIT_MAP, _ALIASES
    _ALIASES = _load_aliases()
    _FIELD_MAP  = _build_reverse("fields")
    _OP_MAP     = _build_reverse("operators")
    _UNIT_MAP   = _build_reverse("units")

_FIELD_MAP  = _build_reverse("fields")
_OP_MAP     = _build_reverse("operators")
_UNIT_MAP   = _build_reverse("units")


# ── Normalization ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase + strip accents for fuzzy comparison."""
    text = text.lower().strip()
    # Normalize unicode (е → е, ё → e for fuzzy, etc.)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ── Fuzzy matching ────────────────────────────────────────────────────────────

def _levenshtein(a: str, b: str) -> int:
    """Simple Levenshtein distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0: return lb
    if lb == 0: return la
    row = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        new_row = [i]
        for j, cb in enumerate(b, 1):
            new_row.append(min(row[j] + 1, new_row[j-1] + 1,
                               row[j-1] + (0 if ca == cb else 1)))
        row = new_row
    return row[-1]


def _fuzzy_match(word: str, candidates: dict[str, str],
                 max_dist: int = 2) -> str | None:
    """Find closest match in candidates dict. Returns canonical or None."""
    w = _normalize(word)
    best_dist = max_dist + 1
    best_canon = None
    for phrase, canon in candidates.items():
        d = _levenshtein(w, _normalize(phrase))
        if d < best_dist:
            best_dist = d
            best_canon = canon
    return best_canon if best_dist <= max_dist else None


# ── Value + unit parsing ──────────────────────────────────────────────────────

_NUMBER_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"          # number
    r"(гб|мб|кб|gb|mb|kb|b|б|"      # size units
    r"часов|час|ч|hours?|h|"          # time units
    r"минут[ыа]?|мин|м|minutes?|min|"
    r"секунд[ыа]?|сек|с|seconds?|sec|s|"
    r"пикселей|пикселя|px|pixels?)?", # pixel units
    re.IGNORECASE
)

def _parse_value_unit(token: str) -> tuple[float, str] | None:
    """Parse '50мб' → (52428800.0, 'mb'), '1 minute' → (60.0, 'minutes')."""
    m = _NUMBER_RE.match(token.strip())
    if not m or not m.group(1):
        return None
    num_str = m.group(1).replace(",", ".")
    try:
        num = float(num_str)
    except ValueError:
        return None
    unit_raw = (m.group(2) or "").lower().strip()
    canon_unit = _UNIT_MAP.get(unit_raw) or _fuzzy_match(unit_raw, _UNIT_MAP, 1) if unit_raw else None
    mult = UNIT_MULTIPLIERS.get(canon_unit or "", 1)
    return (num * mult, canon_unit or "")


# ── Multi-word phrase matching ─────────────────────────────────────────────────

def _match_phrase(tokens: list[str], lookup: dict[str, str],
                  max_words: int = 4) -> tuple[str, int] | None:
    """Try to match 1..max_words consecutive tokens as a phrase.
    Returns (canonical, words_consumed) or None.
    
    Strategy:
    - Exact match: try longest first (greedy)
    - Fuzzy: only for single words (to avoid false multi-word matches)
    """
    # Pass 1: exact match, longest first
    for n in range(min(max_words, len(tokens)), 0, -1):
        phrase = " ".join(tokens[:n]).lower()
        canon = lookup.get(phrase)
        if canon:
            return (canon, n)
    # Pass 2: fuzzy only for single word
    if tokens:
        canon = _fuzzy_match(tokens[0].lower(), lookup, max_dist=1)
        if canon:
            return (canon, 1)
    return None


# ── Main parser ───────────────────────────────────────────────────────────────

class HumanQueryParser:
    """Parse human-language search queries into structured filters + tags."""

    _SHORT_FIELDS = {
        "size": "filesize", "filesize": "filesize", "width": "width",
        "height": "height", "rating": "rating", "duration": "duration",
        "tags": "tag_count", "tagcount": "tag_count",
    }

    def _parse_shorthand(self, token: str) -> list[ParsedFilter] | None:
        """Parse compact filters such as size:+50mb or size:0.1mb-5mb."""
        m = re.fullmatch(r"(?i)(size|filesize|width|height|rating|duration|tags|tagcount):(.+)", token.strip())
        if not m:
            return None
        field = self._SHORT_FIELDS.get(m.group(1).lower())
        value = m.group(2).strip().lower()
        default_unit = {"filesize": "mb", "duration": "seconds", "width": "px", "height": "px", "rating": "", "tag_count": ""}.get(field, "")
        def parse_one(raw: str):
            raw = raw.strip()
            if default_unit and re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
                raw += default_unit
            return _parse_value_unit(raw)
        range_m = re.fullmatch(r"(\d+(?:[.,]\d+)?(?:kb|mb|gb|b|s|sec|seconds?|min|minutes?|h|hours?|px)?)\-(\d+(?:[.,]\d+)?(?:kb|mb|gb|b|s|sec|seconds?|min|minutes?|h|hours?|px)?)", value, re.I)
        if range_m:
            lo = parse_one(range_m.group(1)); hi = parse_one(range_m.group(2))
            if not lo or not hi:
                return None
            return [
                ParsedFilter(field, ">=", lo[0], token, f"[{self._field_label(field)}] [не меньше] [{self._value_label(lo[0], lo[1], field)}]"),
                ParsedFilter(field, "<=", hi[0], token, f"[{self._field_label(field)}] [не больше] [{self._value_label(hi[0], hi[1], field)}]"),
            ]
        op = "="
        if value.startswith("+"):
            op, value = ">=", value[1:]
        elif value.startswith("-"):
            op, value = "<=", value[1:]
        parsed = parse_one(value)
        if not parsed:
            return None
        num, unit = parsed
        return [ParsedFilter(field, op, num, token, f"[{self._field_label(field)}] [{self._op_label(op)}] [{self._value_label(num, unit, field)}]")]

    def parse(self, query: str) -> ParseResult:
        """Parse full query string. May contain multiple conditions + tags."""
        filters: list[ParsedFilter] = []
        tags: list[str] = []
        unknown: list[str] = []
        suggestions: list[str] = []

        # Compact filters do not require another UI panel. Regular tags and
        # old human-language filters remain compatible.
        residual_parts: list[str] = []
        for token in str(query or "").split():
            compact = self._parse_shorthand(token)
            if compact is not None:
                filters.extend(compact)
            else:
                residual_parts.append(token)
        residual = " ".join(residual_parts)
        clauses = re.split(r"[,\n]+", residual)
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            result = self._parse_clause(clause)
            if result is None:
                for part in clause.split():
                    if part.strip():
                        tags.append(part.strip())
            elif isinstance(result, ParsedFilter):
                filters.append(result)
            elif isinstance(result, str):
                suggestions.append(result)
                unknown.append(clause)

        return ParseResult(filters=filters, tags=tags,
                           unknown_tokens=unknown, suggestions=suggestions)

    def _parse_clause(self, clause: str) -> ParsedFilter | str | None:
        """Try to parse one clause as a numerical condition.

        Returns:
          ParsedFilter  — success
          str           — suggestion text (did you mean...?)
          None          — not a numerical clause, treat as tag
        """
        tokens = clause.lower().split()
        if not tokens:
            return None

        i = 0
        field_canon = None
        op_canon    = None
        value_raw   = None

        # Try to find field
        m = _match_phrase(tokens[i:], _FIELD_MAP, max_words=4)
        if m:
            field_canon, consumed = m
            i += consumed

        if field_canon is None:
            return None  # no field found → regular tag

        # Try to find operator (may be multi-word like "не меньше", "at least")
        if i < len(tokens):
            m2 = _match_phrase(tokens[i:], _OP_MAP, max_words=3)
            if m2:
                op_canon, consumed2 = m2
                i += consumed2
            else:
                # Check for inline operators like ">=" ">" "<" in value token
                for sym_op in (">=", "<=", ">", "<", "="):
                    if tokens[i].startswith(sym_op):
                        op_canon = sym_op
                        tokens[i] = tokens[i][len(sym_op):]
                        if not tokens[i]:
                            tokens.pop(i)
                        break

        # Default operator
        if op_canon is None:
            op_canon = ">"

        # Remaining = value + optional unit
        value_str = " ".join(tokens[i:]).strip()
        if not value_str:
            return f"Укажи значение: «{clause}»"

        parsed_val = _parse_value_unit(value_str)
        if parsed_val is None:
            return f"Не понял значение «{value_str}»"

        num_value, unit = parsed_val

        # Build display string
        field_label = self._field_label(field_canon)
        op_label    = self._op_label(op_canon)
        val_label   = self._value_label(num_value, unit, field_canon)

        return ParsedFilter(
            field=field_canon,
            operator=op_canon,
            value=num_value,
            raw=clause,
            display=f"[{field_label}] [{op_label}] [{val_label}]",
        )

    def _field_label(self, canon: str) -> str:
        labels = {
            "filesize": "размер файла", "width": "ширина", "height": "высота",
            "rating": "рейтинг", "duration": "длительность", "tag_count": "кол-во тегов",
        }
        return labels.get(canon, canon)

    def _op_label(self, op: str) -> str:
        return {">" : "больше", ">=" : "не меньше", "<" : "меньше",
                "<=" : "не больше", "=" : "равно"}.get(op, op)

    def _value_label(self, val: float, unit: str, field: str) -> str:
        if unit in ("mb", "gb", "kb"):
            v = val / UNIT_MULTIPLIERS.get(unit, 1)
            return f"{v:g} {unit.upper()}"
        if unit == "minutes":
            return f"{val/60:g} мин"
        if unit == "hours":
            return f"{val/3600:g} ч"
        if unit == "seconds":
            return f"{val:g} сек"
        return f"{val:g}"

    def autocomplete(self, partial: str) -> list[str]:
        """Return autocomplete suggestions for partial numerical query."""
        partial = partial.lower().strip()
        results: list[str] = []
        for phrase in _FIELD_MAP:
            if phrase.startswith(partial) or partial in phrase:
                results.append(phrase)
        results.sort(key=lambda x: (not x.startswith(partial), len(x)))
        return results[:8]


# ── SQL generation ────────────────────────────────────────────────────────────

def to_sql_conditions(filters: list[ParsedFilter]) -> tuple[list[str], list[Any]]:
    """Convert ParsedFilter list to (sql_fragments, params)."""
    frags: list[str] = []
    params: list[Any] = []
    for f in filters:
        sql_field = SQL_FIELDS.get(f.field)
        if not sql_field:
            continue
        op = f.operator if f.operator in (">", ">=", "<", "<=", "=") else ">"
        frags.append(f"{sql_field} {op} ?")
        params.append(f.value)
    return frags, params


# ── Singleton ─────────────────────────────────────────────────────────────────

_PARSER = HumanQueryParser()

def parse_query(text: str) -> ParseResult:
    return _PARSER.parse(text)

def get_autocomplete(partial: str) -> list[str]:
    return _PARSER.autocomplete(partial)
