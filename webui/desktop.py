"""Things the gallery asks of this computer: open a file in its usual program, show it in its folder.

The server runs inside the program on the same computer, so a button on a page can do what the old
Qt gallery's context menu did.  Only files of library cards are ever opened (the caller looks the
path up by card number).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_file(path: Path) -> None:
    """Open the file in the program Windows uses for it (a video player for videos)."""
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # Windows-only API
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal(path: Path) -> None:
    """Open the file's folder with the file selected."""
    if sys.platform.startswith("win"):
        subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
