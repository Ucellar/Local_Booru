import json
from core.paths import FAVORITES_FILE


def load_favorites():
    if FAVORITES_FILE.exists():
        try:
            return set(json.loads(FAVORITES_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_favorites(favs):
    FAVORITES_FILE.write_text(json.dumps(sorted(list(favs)), ensure_ascii=False, indent=2), encoding="utf-8")
