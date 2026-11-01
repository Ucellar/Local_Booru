"""Visual check: does a remote post preview look like the local file? (pHash distance)

A picture is compared by its own pHash.  A video or a GIF by several frames (``local_phashes``): the sites make
their previews of its start, while one middle frame — what the parser compared before v5.23.1 — threw out real finds
(a webm at d=12, an mp4 at d=22, a GIF at d=30, all found on rule34.xxx by the MD5 in their names).  The closest frame
decides.  A one-colour preview (a black first frame) proves nothing: it cannot be compared, it is not «unlike»."""
from __future__ import annotations

import io
from pathlib import Path

from core.lru import LRUCache
from core.parser.context import ParserContext
from core.parser.http import HttpFailure


def local_phash(ctx: ParserContext, path: Path) -> str:
    """pHash of the local image (video/GIF: of a middle frame); cached by path+size+mtime."""
    from core.file_hash_cache import get_or_compute_phash
    from core.tagger.hashing import VIDEO_EXTS, file_phash, video_frame_image
    target = path
    if path.suffix.lower() in VIDEO_EXTS or path.suffix.lower() == ".gif":
        target = video_frame_image(path, settings=ctx.settings, log=None)
    try:
        value, _hit = get_or_compute_phash(ctx.settings, target, lambda p: file_phash(p, settings=ctx.settings))
    except Exception as exc:  # unreadable/odd image: the caller just cannot compare
        ctx.log(f"  PHASH: не удалось посчитать ({type(exc).__name__}: {exc})")
        return ""
    return str(value or "").strip().lower()


def local_phashes(ctx: ParserContext, path: Path) -> tuple:
    """((pHash, where), …) of the local file to compare with a site's preview: a picture — its one pHash (where
    ""); a video or a GIF — one per frame of ``core.tagger.hashing.video_frames`` (the start, a second in, a quarter,
    the middle; one-colour frames left out), or its middle frame when none of them can be read."""
    from core.tagger.hashing import VIDEO_EXTS, video_frames
    if path.suffix.lower() in VIDEO_EXTS or path.suffix.lower() == ".gif":
        out: list = []
        try:
            frames = video_frames(path, settings=ctx.settings)
        except Exception as exc:  # an odd video: the single frame below
            ctx.log(f"  PHASH: кадры не прочитаны ({type(exc).__name__}: {exc})")
            frames = []
        for where, picture in frames:
            try:
                import imagehash
                value = str(imagehash.phash(picture)).strip().lower()
            except Exception as exc:  # one frame: the others still compare
                ctx.log(f"  PHASH: кадр «{where}» не посчитан ({type(exc).__name__}: {exc})")
                continue
            if value and value not in [v for v, _w in out]:
                out.append((value, where))
        if out:
            return tuple(out)
    one = local_phash(ctx, path)
    return ((one, ""),) if one else ()


def remote_phash(ctx: ParserContext, url: str) -> tuple[str, str, bool]:
    """(hash, why it is missing, transient). A transient miss is a network problem."""
    value, _aspect, why, transient = remote_preview(ctx, url)
    return value, why, transient


def remote_preview(ctx: ParserContext, url: str) -> tuple[str, float, str, bool]:
    """(hash, the preview's width / height, why it is missing, transient)."""
    if not url or not url.startswith("http"):
        return "", 0.0, "у поста нет превью", False
    from core.download_policy import downloads_enabled
    if not downloads_enabled(ctx.settings):
        return "", 0.0, "скачивание отключено в этой сессии", False
    low = url.lower().split("?", 1)[0]
    if low.endswith((".mp4", ".webm", ".mkv", ".mov", ".swf", ".zip")):
        return "", 0.0, "превью — видео", False
    try:
        data = ctx.http.get_bytes(url, max_bytes=6 * 1024 * 1024)
    except HttpFailure as exc:
        return "", 0.0, f"превью не скачалось ({exc.detail})", exc.transient
    try:
        import imagehash
        from PIL import Image

        from core.tagger.hashing import flat
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)
            picture = img.convert("RGB")
            if flat(picture):
                return "", 0.0, "превью однотонное (чёрный кадр?) — сверить нельзя", False
            width, height = picture.size
            return str(imagehash.phash(picture)), (width / height if width and height else 0.0), "", False
    except Exception as exc:  # broken or unsupported preview image
        return "", 0.0, f"превью не читается ({type(exc).__name__})", False


def distance(a: str, b: str) -> int | None:
    if len(a or "") != len(b or "") or not a:
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None


def _pairs(local) -> list:
    """[(pHash, where)] of what ``check`` is given: one pHash, or the pairs of ``local_phashes``."""
    if isinstance(local, str):
        return [(local, "")] if local else []
    out = []
    for item in local or ():
        value, where = (item, "") if isinstance(item, str) else (item[0], item[1])
        if value:
            out.append((str(value), str(where or "")))
    return out


class VisualChecker:
    """Remote preview hashes are cached per URL (bounded), and so are previews that can never be compared (a video,
    one colour, unreadable); a download that failed for a while is not."""

    def __init__(self, ctx: ParserContext):
        self.ctx = ctx
        self.max_distance = ctx.setting_int("parser_visual_max_distance", 10, 0, 32)
        self._remote = LRUCache(2048, name="parser_preview_phash")
        self._parts = LRUCache(256, name="parser_window_phash")       # (file, a preview's shape) -> its parts

    def check(self, local, preview_url: str, *, max_distance: int | None = None,
              source=None) -> tuple[bool | None, str, bool]:
        """``local`` — a pHash, or ``local_phashes`` of a file (the closest of its frames decides).
        (True, 'd=3', False) — same picture (a video's frame: 'd=3, начало'); (False, 'd=27', False) — different;
        (None, reason, transient) — could not compare; transient=True means a network problem.
        ``max_distance`` — a stricter limit than the run's (a weak hit: ``engine._accept``).  ``source`` — the local
        picture's file: a preview of another shape than it (e621's of a part of a wide or a tall picture) is compared
        with its parts of the preview's shape too (``hashing.window_phashes``) when the whole is not near."""
        pairs = _pairs(local)
        if not pairs:
            return None, "нет локального pHash", False
        known = self._remote.get(preview_url) if preview_url else None
        if known is None:
            import time
            from core.parser.util import host_of
            from core.work_timing import PREVIEW_CHECK, PREVIEW_HOST, STEPS
            started = time.perf_counter()
            try:
                remote, aspect, why, transient = remote_preview(self.ctx, preview_url)
            finally:                        # the whole look and each host's (v5.35: which site's previews are slow)
                spent = time.perf_counter() - started
                if preview_url:             # (v5.35.3) no address — nothing was fetched: «? 14 × 0 мс» said nothing
                    STEPS.add(PREVIEW_CHECK, spent)
                    STEPS.add(PREVIEW_HOST + (host_of(preview_url) or "?"), spent)
            if transient:
                return None, why, True
            known = (remote, why, aspect)
            if preview_url:
                self._remote[preview_url] = known
        remote, why = known[0], known[1]
        aspect = known[2] if len(known) > 2 else 0.0
        if not remote:
            return None, why, False
        limit = self.max_distance if max_distance is None else min(self.max_distance, int(max_distance))
        best = None
        for value, where in pairs:
            d = distance(value, remote)
            if d is not None and (best is None or d < best[0]):
                best = (d, where)
        if source is not None and aspect and (best is None or best[0] > limit):
            for value, where in self._windows(source, aspect):
                d = distance(value, remote)
                if d is not None and (best is None or d < best[0]):
                    best = (d, where)
        if best is None:
            return None, "хэши несравнимы", False
        d, where = best
        return d <= limit, f"d={d}" + (f", {where}" if where else ""), False

    def _windows(self, source, aspect: float) -> list:
        key = (str(source), round(float(aspect), 2))
        found = self._parts.get(key)
        if found is None:
            from core.tagger.hashing import window_phashes
            found = window_phashes(source, aspect, settings=self.ctx.settings)
            self._parts[key] = found
        return found
