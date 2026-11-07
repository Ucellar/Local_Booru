"""Line icons of the program's own controls (v5.44.26): one set, one size, one stroke, in the colour of their text
(``currentColor``).

They stand where emoji stood — the video player's 🔇 🔊, the next episode's ⏭: Windows draws emoji in colour, from
another font than the text around them, so every button of the bar looked different (the owner, 1.11.2026, after the
list of signs of «нейродизайн» of unslop — https://github.com/heydamir/unslop, Damir Sokolovsky: «emoji used as an
icon … becomes a line icon from one set … one size and one stroke everywhere»).  Drawn here: a play, a pause, a
speaker with its sound and without it, the whole screen and back, the next episode.  A button holds both of its
states; its class or attribute says which one shows (booru.css).
"""
from __future__ import annotations


def _svg(body: str, name: str) -> str:
    return (f'<svg class="icon {name}" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
            f'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
            f'{body}</svg>')


PLAY = _svg('<path d="M7 4.5v15l12-7.5z"/>', "i-play")
PAUSE = _svg('<path d="M8 5v14"/><path d="M16 5v14"/>', "i-pause")
SOUND = _svg('<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6"/>'
             '<path d="M18 6.5a7.5 7.5 0 0 1 0 11"/>', "i-sound")
MUTED = _svg('<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="m16 9.5 5 5"/><path d="m21 9.5-5 5"/>', "i-muted")
FULL = _svg('<path d="M4 9V4h5"/><path d="M15 4h5v5"/><path d="M20 15v5h-5"/><path d="M9 20H4v-5"/>', "i-full")
UNFULL = _svg('<path d="M9 4v5H4"/><path d="M20 9h-5V4"/><path d="M15 20v-5h5"/><path d="M4 15h5v5"/>', "i-unfull")
NEXT = _svg('<path d="M5 5v14l10-7z"/><path d="M19 5v14"/>', "i-next")
PREV = _svg('<path d="M19 5v14L9 12z"/><path d="M5 5v14"/>', "i-prev")       # (v5.44.122) the episode before
