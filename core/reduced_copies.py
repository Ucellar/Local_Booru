"""(v5.44.13) A card whose file is a site's reduced copy of its post gets the post's original in its place — the owner,
31.10.2026: «файлы sample_название желательно заменять на более нормальные».

Which cards: a live picture, not in Брак, whose file came named as the sites name their reduced copies — ``sample_<key>``
(rule34.xxx, Gelbooru), ``__…__sample-<md5>`` (Danbooru), «yande.re 123 sample …», ``thumbnail_…`` (:func:`reduced_name`)
— or by its post's MD5 while its bytes are other bytes (e621 names its samples ``<md5>.jpg``: then only the post of that
MD5 counts).  Its posts: those the sites told about (``site_posts``), else its links to post pages of the sites of
:data:`site_posts.ENGINES`.

Each post is asked once (``Site.posts_by_ids`` of the parser's sites: their keys, cookies and pace).  The original is
downloaded only when the site says it is a picture larger than the card's file (:data:`GAIN`), not in the library
already, not deleted or in the trash (``downloader_utils.download_post_file``: the session's download policy, Referer,
the sites' walls), and the downloaded file must carry the MD5 the site gives for the post.  It takes the card's place
only when it is the same picture (:func:`same_picture`: reduced to the card's size it differs from the card's file
little — in every part of a grid, measured against what its edges explain; a censored (a bar, a mosaic) or another
version is not taken; colours that differ evenly, an ICC profile, are not a difference) and better
(``quality_upgrade_service.execute_quality_upgrade``: staged, verified, swapped atomically, recovered after a crash).
The card stays itself — its number, tags, sources, сборка, «Избранное»; the old bytes go; the file gets the original's
name (``rename_upgraded``: no «sample_», the extension of what it is — :func:`plain_name`).  The parser's and every
importer's writes wait for the swap (``persist._WRITE_LOCK``, ``IMPORT_LOCK``), the gallery lets go of the file
(``hold``).  The parser that meets the old bytes again (another copy of the sample) gives its tags to the card
(``storage.replaced_media_path``).

What was answered is kept (table ``reduced_copies``, migration 045): a card is looked at again only when its file
changed, or — after the network or the site failed — :data:`AGAIN_AFTER` later.  Nothing while the session's downloads
are off, nor while «Обслуживание» has the library; while the parser or the subscriptions use the sites —
:data:`BUSY_BUDGET` a round.  «Настройки → Скачивание и браузер → Ставить оригинал вместо уменьшенной копии»
(``automatic_quality_upgrade_enabled``).  A duty of ``core/background_duties.py``.
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_LOG = logging.getLogger("local_booru.originals")

ROUND_BUDGET = 40.0             # seconds one background round works at most
BUSY_BUDGET = 8.0               # …while the parser or the subscriptions use the sites
RESCAN = 5 * 60                 # seconds between two looks for such cards
AGAIN_AFTER = 3 * 3600          # seconds before a card the network or the site failed for is tried again
GAIN = 1.2                      # the original has at least this many times the copy's pixels
WRITERS_WAIT = 60.0             # seconds the swap waits for the library's writers to let go (then the card later)
# what «the same picture» is: both reduced to the copy's size (at most :data:`FINE_SIDE` wide) and their colours fitted
# channel by channel (an even shift of colours — an ICC profile applied or not — is no difference), the difference of
# every part of a grid is measured against what the edges in it explain (a reduced copy differs from its original
# along the edges: the resampling, the JPEG).  Calibrated on drawings reduced and saved as JPEG the way the sites make
# their samples (busy ones and flat ones with one figure): a sample of 850 pixels scores up to 1.6 at the fine scale,
# one of 600 at quality 70 up to 2.7, a censoring black bar 7 and more, a mosaic over 2 % of the picture 3–5; a
# thumbnail of 150–250 pixels up to 3.5 at the coarse scale, a black bar there 3.9 and more.  (A signature or a small
# text is not seen: such a version is the same picture for this purpose.)
FINE_SIDE, FINE_GRID, FINE_BASE, FINE_EDGE, FINE_LIMIT = 512, 32, 3.0, 0.35, 3.0
COARSE_GRID, COARSE_BASE, COARSE_EDGE, COARSE_LIMIT = 16, 2.0, 0.35, 3.7      # a copy narrower than FINE_SIDE
MEAN_MAX = 12.0                 # the average difference (0…255) of the same picture at most
ASPECT_SLACK = 0.03             # the proportions of a copy and its original differ by rounding only
MOVING = {"gif", "mp4", "webm", "mkv", "mov", "avi", "swf", "zip", "apng"}     # originals that are not a still picture

DONE, KEPT, LATER, RENAME = "done", "kept", "later", "rename"     # what ``reduced_copies.state`` says
BASIS = "site_post_original"    # the identity basis of the revisions made here (``media_revisions.identity_basis``)

_MARK_FIRST = re.compile(r"^(?:sample|thumbnail)[_-]+(?=.)", re.I)                    # sample_<key>, thumbnail_<md5>
_MARK_DANBOORU = re.compile(r"__(?:sample|thumbnail)-(?=[0-9a-f]{32}$)", re.I)       # __tags__sample-<md5>
_MARK_MOEBOORU = re.compile(r"^((?:yande\.re|konachan\.(?:com|net)(?: -)?) \d+) sample(?= |$)", re.I)   # yande.re 1 sample …


# ── names ─────────────────────────────────────────────────────────────────────────
def given_name(file_name: str, original_name: str = "", md5: str = "") -> str:
    """The name the file came with: ``original_file_name``, else the library's name without its MD5 ending."""
    name = str(original_name or "").strip()
    if name:
        return Path(name).name
    p = Path(str(file_name or ""))
    stem = p.stem
    if md5 and stem.lower().endswith("__" + str(md5).lower()[:12]):
        stem = stem[: -14]
    return stem + p.suffix


def reduced_name(name: str) -> bool:
    """The name says the file is a site's reduced copy of a post (sample, thumbnail)."""
    stem = Path(str(name or "")).stem
    return bool(_MARK_FIRST.match(stem) or _MARK_DANBOORU.search(stem) or _MARK_MOEBOORU.match(stem))


def plain_name(name: str, ext: str) -> str:
    """The name of the original: the reduced copy's name without its mark, with ``ext`` (the original's own):
    ``sample_<key>.jpg`` → ``<key>.png``, ``__tags__sample-<md5>.jpg`` → ``__tags__<md5>.png``, «yande.re 1 sample
    tags.jpg» → «yande.re 1 tags.png»."""
    stem = Path(str(name or "")).stem
    stem = _MARK_FIRST.sub("", stem, count=1)
    stem = _MARK_DANBOORU.sub("__", stem, count=1)
    stem = _MARK_MOEBOORU.sub(r"\1", stem, count=1)
    stem = re.sub(r"\s{2,}", " ", stem).strip() or "original"
    ext = str(ext or "").lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return stem + ext


# ── the same picture ──────────────────────────────────────────────────────────────
def _variants(path: Path, *, side: int) -> list:
    """The picture as RGB in the ways a site may have made its copy of it: turned by its EXIF or not, its ICC profile
    applied or not, a transparent one on white and on black."""
    import io
    from PIL import Image, ImageCms, ImageOps
    from core.image_safe import configure_pillow
    configure_pillow()
    out = []
    with Image.open(path) as im:
        im.draft("RGB", (side * 4, side * 4))           # a big JPEG decodes at a fraction of its size
        im.load()
        alpha = "A" in im.getbands() or "transparency" in im.info
        icc = im.info.get("icc_profile")
        turned = int(im.getexif().get(0x0112, 1) or 1) not in (1,)
        base = im.convert("RGBA" if alpha else "RGB")
        bases = [base]
        if turned:
            with contextlib.suppress(Exception):
                bases.append(ImageOps.exif_transpose(im).convert("RGBA" if alpha else "RGB"))
        for picture in bases:
            small = picture.copy()
            small.thumbnail((side * 4, side * 4), Image.Resampling.BOX)
            colours = [small]
            if icc:
                with contextlib.suppress(Exception):
                    source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                    colours.append(ImageCms.profileToProfile(small, source, ImageCms.createProfile("sRGB"),
                                                             outputMode=small.mode))
            for colour in colours:
                if alpha:
                    for back in ((255, 255, 255), (0, 0, 0)):
                        canvas = Image.new("RGB", colour.size, back)
                        canvas.paste(colour, mask=colour.getchannel("A"))
                        out.append(canvas)
                else:
                    out.append(colour)
    return out


def _difference(a, b) -> tuple[float, float, float]:
    """(average difference, the worst part's score, its limit) of ``b`` against ``a`` — both at ``a``'s size (at most
    :data:`FINE_SIDE` wide), ``b``'s colours fitted to ``a``'s channel by channel; a part's score is its difference
    over what the edges in it explain (:data:`FINE_BASE` + :data:`FINE_EDGE` × their strength, or the coarse ones for a
    copy narrower than :data:`FINE_SIDE`)."""
    import numpy as np
    from PIL import Image
    fine = a.width >= FINE_SIDE
    grid, base, edge, limit = ((FINE_GRID, FINE_BASE, FINE_EDGE, FINE_LIMIT) if fine
                               else (COARSE_GRID, COARSE_BASE, COARSE_EDGE, COARSE_LIMIT))
    width = max(1, min(FINE_SIDE, a.width))
    height = max(1, round(width * a.height / max(1, a.width)))
    A = np.asarray(a.resize((width, height), Image.Resampling.BOX), dtype=np.float32)
    B = np.asarray(b.resize((width, height), Image.Resampling.BOX), dtype=np.float32)
    fitted = np.empty_like(B)
    for c in range(3):
        x, y = B[..., c], A[..., c]
        spread = float(x.std())
        gain = float(((x - x.mean()) * (y - y.mean())).mean()) / (spread * spread) if spread > 1e-3 else 1.0
        fitted[..., c] = (x - x.mean()) * min(max(gain, 0.5), 2.0) + y.mean()
    diff = np.abs(A - fitted).mean(axis=2)

    def edges(picture):
        light = picture.mean(axis=2)
        across, down = np.zeros_like(light), np.zeros_like(light)
        across[:, 1:-1] = np.abs(light[:, 2:] - light[:, :-2]) / 2
        down[1:-1, :] = np.abs(light[2:, :] - light[:-2, :]) / 2
        return np.maximum(across, down)
    strength = np.maximum(edges(A), edges(fitted))
    rows = np.array_split(np.arange(height), min(grid, height))
    cols = np.array_split(np.arange(width), min(grid, width))
    worst = max(float(diff[np.ix_(r, c)].mean()) / (base + edge * float(strength[np.ix_(r, c)].mean()))
                for r in rows for c in cols)
    return float(diff.mean()), worst, limit


def same_picture(copy_path, original_path) -> tuple[bool, str]:
    """Whether ``original_path`` is the picture ``copy_path`` is a reduced copy of — (yes or no, the words why)."""
    try:
        copies = _variants(Path(copy_path), side=FINE_SIDE)
        originals = _variants(Path(original_path), side=FINE_SIDE)
    except Exception as exc:                 # a file that does not open is no proof of anything
        return False, f"не открывается: {type(exc).__name__}"
    best = None
    for a in copies:
        for b in originals:
            ratio_a, ratio_b = a.width / max(1, a.height), b.width / max(1, b.height)
            if abs(ratio_a - ratio_b) / max(ratio_a, ratio_b) > ASPECT_SLACK:
                continue
            mean, worst, limit = _difference(a, b)
            score = (mean > MEAN_MAX or worst > limit, worst / limit, mean)
            if best is None or score < best:
                best = score
    if best is None:
        return False, "другие пропорции"
    differs, share, mean = best
    return (not differs), f"разница {mean:.1f}, худшее место — {share:.2f} допустимого"


# ── what a site says about a post ─────────────────────────────────────────────────
def _number(value) -> int:
    try:
        return max(0, int(str(value if value is not None else "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def remote_size(raw: dict) -> tuple[int, int]:
    """(width, height) of a post's original as the site gives it ((0, 0) — it does not say)."""
    f = raw.get("file") if isinstance(raw.get("file"), dict) else {}
    for w, h in ((f.get("width"), f.get("height")), (raw.get("image_width"), raw.get("image_height")),
                 (raw.get("width"), raw.get("height"))):
        if _number(w) and _number(h):
            return _number(w), _number(h)
    media = raw.get("media_asset") if isinstance(raw.get("media_asset"), dict) else {}
    return _number(media.get("image_width")), _number(media.get("image_height"))


def _files_only(raw: dict) -> dict:
    """The post without the MD5 the site gives (``download_post_file`` would not download what the library knows by
    it — the card itself may be known by it: an alias of the sample); the MD5 is checked here after."""
    out = {k: v for k, v in raw.items() if k not in ("md5", "hash", "file_md5")}
    if isinstance(out.get("file"), dict):
        out["file"] = {k: v for k, v in out["file"].items() if k != "md5"}
    return out


# ── the cards ─────────────────────────────────────────────────────────────────────
@dataclass
class Card:
    image_id: int
    path: str
    md5: str
    name: str                       # the name the file came with
    width: int = 0
    height: int = 0
    name_md5: str = ""              # the MD5 the name carries (another than the bytes'): only its post counts
    posts: list = field(default_factory=list)       # [(site, post id)]
    rename_of: int = 0              # only the name is left to change (the revision whose file it is)


@dataclass
class Outcome:
    state: str                      # DONE | KEPT | LATER | RENAME | "stop" (downloads off: nothing written)
    detail: str = ""
    site: str = ""
    post_id: int = 0
    revision_id: int = 0


def enabled(settings: dict) -> bool:
    return bool((settings or {}).get("automatic_quality_upgrade_enabled", True))


def candidates(settings: dict) -> list:
    """The cards to look at, newest first: reduced copies (by name) with a post on a site the program can ask,
    without an answer for their present bytes (or with one that said «later» and whose time has come)."""
    from core.database.connection import db
    from core.parser.util import md5_from_filename, post_id_from_url
    from core.site_posts import ENGINES, canonical_site, engine_of_host
    now = int(time.time())
    found: list = []
    with db(settings, readonly=True) as con:
        answered = {int(r[0]): (str(r[1] or "").lower(), str(r[2] or ""), int(r[3] or 0), int(r[4] or 0))
                    for r in con.execute("SELECT image_id, md5, state, again_at, revision_id FROM reduced_copies")}
        # the originals this module put in place: a card of one whose name is still a reduced copy's has only its name
        # to change (the file was busy, or the program closed in between)
        ours = {int(r[0]) for r in con.execute(
            "SELECT revision_id FROM media_revisions WHERE status='committed' AND identity_basis LIKE ?",
            (BASIS + ":%",))}
        rows = con.execute(
            "SELECT id, path, file_name, original_file_name, hash_md5, width, height, "
            "COALESCE(current_revision_id, 0) AS revision FROM images "
            "WHERE +deleted=0 AND COALESCE(is_video,0)=0 AND bucket NOT IN ('no_match','downloaded_no_match') "
            "ORDER BY id DESC").fetchall()
        hosts: dict = {}
        for row in rows:
            md5 = str(row["hash_md5"] or "").lower()
            name = given_name(str(row["file_name"] or ""), str(row["original_file_name"] or ""), md5)
            name_md5 = md5_from_filename(name)
            marked = reduced_name(name)
            if not marked and not (name_md5 and name_md5 != md5):
                continue
            card = Card(int(row["id"]), str(row["path"] or ""), md5, name, int(row["width"] or 0),
                        int(row["height"] or 0), "" if marked or name_md5 == md5 else name_md5)
            seen = answered.get(card.image_id)
            if seen and seen[0] == md5 and (seen[1] in (DONE, KEPT) or seen[2] > now):
                continue
            if marked and int(row["revision"] or 0) in ours:
                card.rename_of = int(row["revision"])
                found.append(card)
                continue
            for site, post_id in con.execute(
                    "SELECT site, post_id FROM site_posts WHERE image_id=? AND state='ok' ORDER BY post_id",
                    (card.image_id,)):
                if (str(site), int(post_id)) not in card.posts:
                    card.posts.append((str(site), int(post_id)))
            for host, url in con.execute(
                    "SELECT s.host, s.url FROM image_sources x JOIN sources s ON s.id=x.source_id WHERE x.image_id=?",
                    (card.image_id,)):
                host = str(host or "")
                if host not in hosts:
                    hosts[host] = canonical_site(host) if engine_of_host(settings, host) in ENGINES else ""
                number = post_id_from_url(str(url or ""))
                if hosts[host] and number.isdigit() and (hosts[host], int(number)) not in card.posts:
                    card.posts.append((hosts[host], int(number)))
            if card.posts:
                found.append(card)
    return found


def _remember(settings: dict, card: Card, outcome: Outcome) -> None:
    from core.database.connection import db
    from core.redaction import sanitize_text
    md5 = card.md5
    if outcome.state in (DONE, RENAME):
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT hash_md5 FROM images WHERE id=?", (card.image_id,)).fetchone()
        md5 = str((row["hash_md5"] if row else "") or md5).lower()
    again = int(time.time()) + AGAIN_AFTER if outcome.state in (LATER, RENAME) else 0
    with db(settings, write=True) as con:
        con.execute(
            "INSERT OR REPLACE INTO reduced_copies(image_id, md5, state, detail, site, post_id, revision_id, asked_at, "
            "again_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (card.image_id, md5, outcome.state, sanitize_text(outcome.detail)[:500], outcome.site,
             int(outcome.post_id or 0), int(outcome.revision_id or 0), int(time.time()), again))


# ── one card ──────────────────────────────────────────────────────────────────────
def _live_other(settings: dict, md5: str, image_id: int) -> int:
    """A live card other than ``image_id`` that has these bytes or is known by this MD5 of a site (0 — none)."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT id FROM images WHERE hash_md5 IN (?,?) AND +deleted=0 AND id<>? LIMIT 1",
                          (md5, md5.upper(), int(image_id))).fetchone()
        if row is None:
            row = con.execute(
                "SELECT a.image_id FROM image_site_md5_aliases a JOIN images i ON i.id=a.image_id "
                "WHERE a.site_md5=? AND i.deleted=0 AND a.image_id<>? LIMIT 1", (md5, int(image_id))).fetchone()
    return int(row[0]) if row else 0


def _judge(settings: dict, card: Card, site: str, raw: dict) -> tuple[str, str, str]:
    """(why not — "" when the original is worth downloading, its URL, the basis of its identity)."""
    from urllib.parse import urlparse
    from core.deleted_registry import reimport_block
    from core.downloader_utils import _post_md5, original_media_urls
    from core.parser.util import md5_from_filename
    from core.tagger.filename_hints import extract_rule34_40hex_key
    urls = original_media_urls(raw, site)
    if not urls:
        return "сайт не даёт файла оригинала", "", ""
    url = urls[0]
    ext = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if ext in MOVING:
        return "оригинал — анимация или видео, у карточки картинка", "", ""
    width, height = remote_size(raw)
    if width and height and card.width and card.height and (
            width < card.width or height < card.height or width * height < GAIN * card.width * card.height):
        return f"оригинал не больше копии ({width}×{height})", "", ""
    md5 = _post_md5(raw)
    if card.name_md5 and md5 != card.name_md5:
        return "имя файла — о другом посте", "", ""
    if md5 and md5 == card.md5:
        return "это и есть оригинал", "", ""
    if md5:
        other = _live_other(settings, md5, card.image_id)
        if other:
            return f"оригинал уже в библиотеке — карточка {other}", "", ""
        block = reimport_block(md5, settings=settings)
        if block:
            return ("оригинал лежит в корзине" if block == "trash" else "оригинал раньше удалён из библиотеки"), "", ""
    key = extract_rule34_40hex_key(card.name)
    if md5 and md5_from_filename(card.name) == md5:
        basis = "name_md5"
    elif key and key == extract_rule34_40hex_key(Path(urlparse(url).path).name):
        basis = "name_key"
    else:
        basis = "pixels"
    return "", url, basis


def _download(settings: dict, site: str, raw: dict) -> tuple[bool, Path | None]:
    from core.downloader_utils import download_post_file
    ok, path = download_post_file(site, _files_only(raw), settings, query="originals", session_folder="staging",
                                  log=lambda m: _LOG.debug("%s", m), operation="reduced_copy_original")
    return bool(ok), (Path(path) if path else None)


def _drop(settings: dict, path: Path | None) -> None:
    if path is None or not path.exists():
        return
    from core.services.media_storage_service import unlink_managed
    try:
        unlink_managed(settings, path, operation="reduced_copies.staging_cleanup")
    except OSError as exc:
        _LOG.warning("originals: the downloaded file %s stays: %s", path.name, exc)


class _Busy(Exception):
    """The library's writers did not let go in time (a long job of «Обслуживание» holds them)."""


@contextlib.contextmanager
def _writers_wait(wait: float = WRITERS_WAIT):
    """The parser's writes and every import wait while the card's file changes: the locks in the order the parser takes
    them, each for ``wait`` seconds at most (a job that holds the imports for long — :class:`_Busy`, the card later)."""
    from core.import_pipeline import IMPORT_LOCK
    from core.parser import persist
    if not persist._WRITE_LOCK.acquire(timeout=wait):
        raise _Busy("парсер записывает файлы")
    try:
        if not IMPORT_LOCK.acquire(timeout=wait):
            raise _Busy("библиотеку держит другая работа")
        try:
            yield
        finally:
            IMPORT_LOCK.release()
    finally:
        persist._WRITE_LOCK.release()


def _swap(settings: dict, card: Card, staged: Path, *, site: str, post_id: int, basis: str, hold,
          stop_check) -> Outcome:
    """The original in the card's place, then its name; the writers of the library wait meanwhile, the gallery lets go
    of the file."""
    from core.database.connection import db
    from core.import_pipeline import make_card_preview
    from core.quality_upgrade_service import QualityUpgradeRejected, execute_quality_upgrade, rename_upgraded
    trace = lambda m: _LOG.debug("%s", m)  # noqa: E731
    try:
        with _writers_wait(), (hold([card.path]) if hold is not None else contextlib.nullcontext()):
            with db(settings, readonly=True) as con:
                row = con.execute("SELECT path, hash_md5, deleted FROM images WHERE id=?", (card.image_id,)).fetchone()
            if not row or int(row["deleted"] or 0) or str(row["path"]) != card.path \
                    or str(row["hash_md5"] or "").lower() != card.md5:
                return Outcome(LATER, "карточка тем временем изменилась", site, post_id)
            try:
                result = execute_quality_upgrade(
                    settings, image_id=card.image_id, staged_path=staged, identity_basis=f"{BASIS}:{basis}",
                    identity_verified=True, source_site=site, source_post_id=str(post_id), stop_check=stop_check,
                    trace=trace)
            except QualityUpgradeRejected as exc:
                text = str(exc)
                later = "RAM guard" in text or "MD5 differs from database" in text or "changed during" in text
                return Outcome(LATER if later else KEPT, f"не заменён: {text}"[:400], site, post_id)
            revision = int(result.get("revision_id") or 0)
            named = rename_upgraded(settings, revision_id=revision, new_name=plain_name(card.name, staged.suffix),
                                    trace=trace)
    except _Busy as exc:
        return Outcome(LATER, f"не заменён сейчас: {exc}", site, post_id)
    make_card_preview(settings, named["path"])
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT width, height FROM images WHERE id=?", (card.image_id,)).fetchone()
    size = f"{int(row['width'] or 0)}×{int(row['height'] or 0)}" if row else "?"
    _LOG.info("Оригинал вместо уменьшенной копии: карточка %d, %s пост %d — %d×%d → %s, файл %s%s", card.image_id,
              site, post_id, card.width, card.height, size, Path(named["path"]).name,
              "" if named["renamed"] else " (имя сменится позже: файл был занят)")
    detail = f"оригинал с {site} (пост {post_id}): {card.width}×{card.height} → {size}"
    return Outcome(DONE if named["renamed"] or named["final"] else RENAME, detail, site, post_id, revision)


def _real_ext(path: Path) -> str:
    """The extension of what the file is (a PNG under «.jpg» — «.png»); its own when that cannot be told."""
    kinds = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tiff",
             "AVIF": ".avif"}
    try:
        from PIL import Image
        with Image.open(path) as im:
            fmt = str(im.format or "").upper()
    except Exception:  # not a picture Pillow knows: the name keeps its extension
        return path.suffix
    if fmt == "JPEG" and path.suffix.lower() in (".jpg", ".jpeg", ".jpe", ".jfif"):
        return path.suffix
    return kinds.get(fmt, path.suffix)


def look(settings: dict, card: Card, *, client, hold=None, stop_check=None) -> Outcome:
    """One card: its posts in turn until one gives its original in the card's place."""
    from core.parser.http import Cancelled, HttpFailure
    from core.parser.sites import SiteUnavailable
    from core.quality_upgrade_service import QualityUpgradeInterrupted
    from core.redaction import sanitize_text
    from core.site_posts import ENGINES
    if card.rename_of:
        return _rename_again(settings, card, hold)
    if not Path(card.path).is_file():
        return Outcome(LATER, "файла карточки нет на месте")
    if not (card.width and card.height):
        try:
            from PIL import Image
            with Image.open(card.path) as im:
                card.width, card.height = map(int, im.size)
        except Exception as exc:
            return Outcome(KEPT, f"файл карточки не открывается: {type(exc).__name__}")
    later: list = []
    kept: list = []
    for site_name, post_id in card.posts:
        if stop_check is not None and stop_check():
            return Outcome("stop", "остановлено")
        try:
            site = client.site(site_name)
        except Exception as exc:  # a broken row of the site table: said, the other posts go on
            later.append(f"{site_name}: {sanitize_text(f'{type(exc).__name__}: {exc}')}")
            continue
        if site is None or getattr(site, "engine", "") not in ENGINES:
            kept.append(f"{site_name}: программа не умеет спрашивать этот сайт")
            continue
        if getattr(site, "disabled_reason", ""):
            later.append(f"{site_name}: {site.disabled_reason}")
            continue
        try:
            raws = site.posts_by_ids([post_id])
        except (SiteUnavailable, HttpFailure, Cancelled, OSError, ValueError) as exc:
            later.append(f"{site_name}: {sanitize_text(getattr(exc, 'detail', None) or exc)}"[:200])
            continue
        raw = next((r for r in raws or [] if isinstance(r, dict) and _number(r.get("id")) == int(post_id)), None)
        if raw is None:
            kept.append(f"{site_name}: у сайта нет поста {post_id}")
            continue
        why, _url, basis = _judge(settings, card, site_name, raw)
        if why:
            kept.append(f"{site_name} {post_id}: {why}")
            continue
        from core.download_policy import DownloadBlockedByPolicy
        try:
            ok, staged = _download(settings, site_name, raw)
        except DownloadBlockedByPolicy:
            return Outcome("stop", "скачивание выключено")
        try:
            if not ok or staged is None or not staged.is_file():
                if staged is not None and staged.is_file() and not ok and _is_library_card(settings, staged):
                    kept.append(f"{site_name} {post_id}: в библиотеке есть такая же картинка не меньше")
                else:
                    later.append(f"{site_name} {post_id}: оригинал не скачан")
                continue
            try:
                outcome = _check_and_swap(settings, card, staged, raw, site=site_name, post_id=post_id, basis=basis,
                                          hold=hold, stop_check=stop_check)
            except QualityUpgradeInterrupted:          # the exit: the card is looked at again at the next start
                return Outcome("stop", "остановлено")
            if outcome.state in (DONE, RENAME, "stop"):
                return outcome
            (later if outcome.state == LATER else kept).append(f"{site_name} {post_id}: {outcome.detail}")
        finally:
            if staged is not None and not _is_library_card(settings, staged):
                _drop(settings, staged)
    if later:
        return Outcome(LATER, "; ".join(later + kept)[:500])
    return Outcome(KEPT, "; ".join(kept)[:500] or "нет постов")


def _is_library_card(settings: dict, path: Path) -> bool:
    """``path`` is a card's file (``download_post_file`` answers «duplicate» with the card it kept)."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        return con.execute("SELECT 1 FROM images WHERE path=? LIMIT 1", (str(path),)).fetchone() is not None


def _check_and_swap(settings: dict, card: Card, staged: Path, raw: dict, *, site: str, post_id: int, basis: str,
                    hold, stop_check) -> Outcome:
    from core.downloader_utils import _post_md5
    from core.quality_upgrade_service import _md5
    want = _post_md5(raw)
    if want and _md5(staged) != want:
        return Outcome(KEPT, "скачанный файл — не оригинал поста (другой MD5)", site, post_id)
    same, words = same_picture(card.path, staged)
    if not same:
        return Outcome(KEPT, f"это другая картинка: {words}", site, post_id)
    return _swap(settings, card, staged, site=site, post_id=post_id, basis=basis, hold=hold, stop_check=stop_check)


def _rename_again(settings: dict, card: Card, hold=None) -> Outcome:
    """The original took the card's place, but its name stayed (the file was busy): the name, once more."""
    from core.quality_upgrade_service import rename_upgraded
    name = plain_name(card.name, _real_ext(Path(card.path)))
    try:
        with _writers_wait(), (hold([card.path]) if hold is not None else contextlib.nullcontext()):
            named = rename_upgraded(settings, revision_id=card.rename_of, new_name=name)
    except _Busy as exc:
        return Outcome(RENAME, f"имя не сменилось: {exc}", revision_id=card.rename_of)
    if not named["renamed"]:
        state = DONE if named["final"] else RENAME
        return Outcome(state, "имя не сменилось: " + (named.get("why") or "файл занят"), revision_id=card.rename_of)
    return Outcome(DONE, f"имя оригинала: {Path(named['path']).name}", revision_id=card.rename_of)


# ── the background round ──────────────────────────────────────────────────────────
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_STATE: dict = {"queue": [], "scanned_at": 0.0, "db": ""}     # the cards waiting (as of the last look), of which base


def forget() -> None:
    with _LOCK:
        _STATE.update(queue=[], scanned_at=0.0, db="")


def background_round(settings: dict, *, stop_check=None, hold=None, budget: float = ROUND_BUDGET,
                     client=None) -> dict:
    """One round: the cards waiting, as much as ``budget`` seconds allow.  {"done": originals put in place (their name
    too, or later), "looked": cards looked at, "more": something is left, "busy": who the round shared the sites with}."""
    from core.database.connection import writes_blocked
    from core.download_policy import downloads_enabled
    from core.site_posts import MAINTENANCE, sites_busy
    if not enabled(settings) or writes_blocked() or not downloads_enabled(settings):
        return {"done": 0, "looked": 0, "more": False}
    busy = sites_busy()
    if busy == MAINTENANCE:
        return {"done": 0, "looked": 0, "more": True, "busy": busy}
    if busy:
        budget = min(budget, BUSY_BUDGET)
    if not _RUN_LOCK.acquire(blocking=False):
        return {"done": 0, "looked": 0, "more": True}
    try:
        result = _round(settings, stop_check, budget, client, hold)
    finally:
        _RUN_LOCK.release()
    return {**result, "busy": busy} if busy else result


def _round(settings: dict, stop_check, budget: float, client, hold) -> dict:
    from core.site_posts import _Client, _base_key
    started = time.monotonic()
    base = _base_key(settings)
    with _LOCK:
        stale = _STATE["db"] != base or not _STATE["scanned_at"] or time.monotonic() - _STATE["scanned_at"] > RESCAN
    if stale:
        queue = candidates(settings)
        with _LOCK:
            _STATE.update(queue=queue, scanned_at=time.monotonic(), db=base)
    done = looked = 0
    while time.monotonic() - started < budget:
        if stop_check is not None and stop_check():
            break
        with _LOCK:
            if not _STATE["queue"]:
                break
            card = _STATE["queue"].pop(0)
        if client is None:
            client = _Client(settings)
        try:
            outcome = look(settings, card, client=client, hold=hold, stop_check=stop_check)
        except Exception as exc:  # one card's trouble is said; the others go on, this one later
            from core.redaction import sanitize_text
            _LOG.warning("originals: card %d: %s", card.image_id, sanitize_text(f"{type(exc).__name__}: {exc}"))
            outcome = Outcome(LATER, sanitize_text(f"{type(exc).__name__}: {exc}"))
        if outcome.state == "stop":
            with _LOCK:
                _STATE["queue"].insert(0, card)
            break
        looked += 1
        _remember(settings, card, outcome)
        if outcome.state in (DONE, RENAME) and not card.rename_of:
            done += 1
    with _LOCK:
        more = bool(_STATE["queue"])
    return {"done": done, "looked": looked, "more": more}


def status(settings: dict) -> dict:
    """For «Настройки»: {"done": cards with their original, "kept": cards left as they are, "later": cards waiting for
    the site, the network or their file}."""
    from core.database.connection import db
    try:
        with db(settings, readonly=True) as con:
            counts = {str(r[0]): int(r[1]) for r in con.execute(
                "SELECT state, COUNT(*) FROM reduced_copies GROUP BY state")}
    except Exception as exc:  # a base from before migration 045: nothing known yet
        _LOG.debug("originals: no status: %s", exc)
        counts = {}
    return {"done": counts.get(DONE, 0) + counts.get(RENAME, 0), "kept": counts.get(KEPT, 0),
            "later": counts.get(LATER, 0)}
