from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "requirements.lock.txt"
BAD_MARKERS = (
    "not installed in build environment",
    "# not installed",
)


def main() -> int:
    if not LOCK.exists():
        print("requirements.lock.txt not found", file=sys.stderr)
        return 1
    text = LOCK.read_text(encoding="utf-8", errors="replace")
    bad = []
    for i, line in enumerate(text.splitlines(), 1):
        lower = line.lower()
        if any(marker in lower for marker in BAD_MARKERS):
            bad.append((i, line))
    if bad:
        print("requirements.lock.txt is not a real runnable lock. Regenerate it in the venv used to run/build the app.", file=sys.stderr)
        for i, line in bad[:20]:
            print(f"{i}: {line}", file=sys.stderr)
        return 2
    print("requirements.lock.txt looks usable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
