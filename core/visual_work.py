"""The work of «фото или рисунок» in Брак, besides one picture at a time (``visual_status.classify_visual_status``):

- ``classify_pending`` — the Брак entries the chosen model has not answered yet (a new model, entries from before
  it was downloaded); the person's marks («Это фото» / «Это рисунок») are never touched.  The new window does a
  round of it by itself every 10 minutes (``background_round``: the weights are downloaded first when
  «скачивать самой» is on), «Разобрать Брак заново» on «Настройки» does all of it;
- ``install_runtime`` — onnxruntime into the Python the program runs in (pip, the journal shows its output);
- ``run_check`` — the check on the person's own library: found cards whose kind is known from their tags or site
  (drawings; 3D renders; pictures made by neural networks; photographs — ``photo_(medium)``, realbooru, the
  idol part of sankaku) and the marks put in Брак, each model's answers counted per group, the mistakes kept as
  card numbers.  Tags can be wrong, so it is an estimate; the marks are the person's own word.

One heavy thing at a time (``_WORK``): a page task waits for the background round, the round skips while a task works.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger("local_booru.visual")
_WORK = threading.Lock()
ROUND_LIMIT = 200              # Брак entries one background round checks
ROUND_SECONDS = 20.0           # …and at most this long: the other duties of the thread wait meanwhile; a round cut
                               # short (or a download in slices — it resumes) goes on soon after (``more``)
UNREADABLE = "unreadable"      # the answer kept for a file no model can see: not asked again by the same model
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")

# a booru tag wiki settles the doubtful ones: «photo» and «photography» are ambiguous
# there (a photo, a drawing containing one, a photo background), «real_life» is a drawing of a real person, and
# «photorealistic» a drawing that looks like a photo — none of them is a photograph for sure.
PHOTO_TAGS = {"photo_(medium)", "photography_(artwork)", "photo_(object)_(medium)", "photo_manipulation"}
PHOTO_HOSTS = {"realbooru.com", "idol.sankakucomplex.com", "behoimi.org"}
THREE_D_TAGS = {"3d", "3d_(artwork)", "3d_render", "3d_model", "source_filmmaker", "source_filmmaker_(artwork)",
                "blender_(medium)", "blender_(artwork)", "koikatsu", "koikatsu_(medium)", "honey_select",
                "honey_select_2", "daz_studio", "daz3d", "daz_3d", "mikumikudance_(medium)", "mmd", "render", "cgi"}
AI_TAGS = {"ai_generated", "ai-generated", "ai-created", "ai_assisted", "ai_art", "stable_diffusion", "novelai",
           "midjourney", "dall-e", "dalle", "nai_diffusion"}
# group -> (title, the answer that is right)
GROUPS = {
    "drawing": ("Рисунки", "booru"),
    "3d": ("3D-рендеры", "booru"),
    "ai": ("Картинки от нейросетей", "booru"),
    "photo": ("Фотографии", "real"),
    "marked_photo": ("Ваши отметки «фото» в Браке", "real"),
    "marked_drawing": ("Ваши отметки «рисунок» в Браке", "booru"),
}
MIN_GROUP = 5                  # a group with fewer pictures does not count in the score


def _file_of(entry: dict) -> Path | None:
    for key in ("media_path", "original_path"):
        value = str(entry.get(key) or "")
        if value and Path(value).is_file():
            return Path(value)
    return None


# ── what can answer ────────────────────────────────────────────────────────
def problem(settings: dict) -> str:
    """Why the chosen model cannot answer now ("" — it can)."""
    from core import visual_models
    choice = visual_models.chosen(settings)
    if not choice:
        return "нет модели: не установлен onnxruntime"
    if choice in visual_models.MODEL_CHOICES:
        if not visual_models.runtime()["ok"]:
            return "не установлен onnxruntime"
        absent = visual_models.missing(settings, choice)
        if absent:
            return "не скачана модель: " + ", ".join(f"{s.title} ({s.size_mb} МБ)" for s in absent)
        unloadable = visual_models.load_problem(settings, choice)
        if unloadable:
            return unloadable
    if choice == "clip" and not visual_models._clip_ready(settings):
        return "CLIP не может работать: нет torch и transformers или файлов модели"
    return ""


# ── Брак entries without an answer ─────────────────────────────────────────
def pending(settings: dict, *, everything: bool = False, limit: int | None = None) -> list:
    """Active Брак entries the chosen model has not answered (all of them with ``everything``), newest first;
    never the person's marks.  Entries without a file on disk are left out."""
    from core.database.connection import db
    from core.visual_status import current_visual_model_name
    name = current_visual_model_name(settings)
    where = "active=1 AND visual_model<>'manual'"
    params: list = []
    if not everything:
        where += " AND (visual_model<>? OR visual_status='')"
        params.append(name)
    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute(
            f"SELECT original_path, media_path FROM no_match_items WHERE {where} ORDER BY updated_at DESC", params)]
    out = []
    for row in rows:
        file = _file_of(row)
        if file is not None:
            out.append({**row, "file": file})
            if limit and len(out) >= limit:
                break
    return out


def pending_count(settings: dict) -> int:
    """How many active Брак entries the chosen model has not answered (their files are not looked at)."""
    from core.database.connection import db
    from core.visual_status import current_visual_model_name
    with db(settings, readonly=True) as con:
        return int(con.execute("SELECT COUNT(*) FROM no_match_items WHERE active=1 AND visual_model<>'manual' "
                               "AND (visual_model<>? OR visual_status='')",
                               (current_visual_model_name(settings),)).fetchone()[0])


def classify_pending(settings: dict, *, everything: bool = False, limit: int | None = None,
                     progress: Callable[[str], None] | None = None,
                     stop_check: Callable[[], bool] | None = None) -> dict:
    """Check the entries of ``pending`` and store the answers.  {"checked", "photo", "drawing", "unsure",
    "unreadable", "errors", "stopped"}; a file no model can see is marked «unreadable» for this model (the next ones
    are not held up by it); the first answers that are errors in a row (the model broke) end it early."""
    from core.nomatch_db import set_visual_status
    from core.visual_status import classify_visual_status, current_visual_model_name
    say = progress or (lambda _m: None)
    stop = stop_check or (lambda: False)
    result = {"checked": 0, "photo": 0, "drawing": 0, "unsure": 0, "unreadable": 0, "errors": 0, "stopped": False}
    why = problem(settings)
    if why:
        result["skipped"] = why
        return result
    entries = pending(settings, everything=everything, limit=limit)
    say(f"Брак: проверить {len(entries)}")
    errors_in_row = 0
    for n, entry in enumerate(entries, 1):
        if stop():
            result["stopped"] = True
            break
        info = classify_visual_status(entry["file"], settings) or {}
        key = entry.get("original_path") or entry.get("media_path")
        if info.get("unreadable"):
            result["unreadable"] += 1
            if result["unreadable"] <= 3:
                say(f"  {entry['file'].name}: {info.get('error') or 'не открывается'}")
            set_visual_status(key, UNREADABLE, 0.0, current_visual_model_name(settings), settings=settings)
            continue
        if info.get("error"):
            result["errors"] += 1
            errors_in_row += 1
            if errors_in_row <= 3:
                say(f"  {entry['file'].name}: {info['error']}")
            if errors_in_row >= 20 and result["checked"] == 0:
                say("Модель не отвечает ни на одну картинку — остановлено")
                break
            continue
        errors_in_row = 0
        status = str(info.get("visual_status") or "")
        set_visual_status(key, status, float(info.get("visual_confidence") or 0), str(info.get("visual_model") or ""),
                          int(info.get("visual_checked_at") or 0), settings=settings)
        result["checked"] += 1
        result[{"real": "photo", "booru": "drawing"}.get(status, "unsure")] += 1
        if n % 50 == 0:
            say(f"  {n} из {len(entries)}: фото {result['photo']}, рисунков {result['drawing']}, "
                f"не уверена {result['unsure']}")
    return result


def background_round(settings: dict, *, stop_check: Callable[[], bool] | None = None,
                     budget: float | None = ROUND_SECONDS) -> dict | None:
    """What the new window does by itself: the weights when they are missing (and «скачивать самой» is on), then up
    to :data:`ROUND_LIMIT` Брак entries — for at most ``budget`` seconds (a download goes on in the next round from
    where it stopped).  None — skipped (a page task works, nothing can answer); a result with ``more`` — there is
    more to do right away."""
    from core import visual_models
    if not bool(settings.get("visual_nomatch_classify_enabled", True)):
        return None
    if not _WORK.acquire(blocking=False):
        return None
    deadline = time.monotonic() + budget if budget else None

    def out_of_time() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    def stop() -> bool:
        return out_of_time() or (stop_check is not None and bool(stop_check()))
    try:
        choice = visual_models.chosen(settings)
        if (choice in visual_models.MODEL_CHOICES and visual_models.runtime()["ok"]
                and visual_models.missing(settings, choice)):
            if not bool(settings.get("visual_nomatch_auto_download_model", True)):
                return None
            try:
                visual_models.download(settings, choice, progress=lambda m: _LOG.info("visual model: %s", m),
                                       stop_check=stop)
            except Exception as exc:  # a slice of the download ended (its part is kept), or the download failed
                if out_of_time() and not (stop_check is not None and stop_check()):
                    return {"more": True, "downloading": True}
                _LOG.warning("visual model download: %s: %s", type(exc).__name__, exc)
                return None
            if out_of_time():
                return {"more": True}
        if problem(settings):
            return None
        result = classify_pending(settings, limit=ROUND_LIMIT, stop_check=stop)
        seen = result["checked"] + result["unreadable"] + result["errors"]
        result["more"] = bool(out_of_time() and result["stopped"]) or seen >= ROUND_LIMIT
        if seen:
            _LOG.info("Брак, фото или рисунок: checked %d (photo %d, drawing %d, unsure %d), unreadable %d, errors %d",
                      result["checked"], result["photo"], result["drawing"], result["unsure"], result["unreadable"],
                      result["errors"])
        return result
    finally:
        _WORK.release()


def holding_work(progress: Callable[[str], None] | None = None):
    """The lock of heavy visual work for a page task (it waits for a background round to end)."""
    class _Held:
        def __enter__(self):
            if not _WORK.acquire(blocking=False):
                if progress:
                    progress("Жду, пока закончится фоновая проверка Брака…")
                _WORK.acquire()
            return self

        def __exit__(self, *_exc):
            _WORK.release()
            return False
    return _Held()


# ── onnxruntime ────────────────────────────────────────────────────────────
def install_runtime(*, progress: Callable[[str], None] | None = None,
                    stop_check: Callable[[], bool] | None = None) -> dict:
    """``pip install onnxruntime`` into the Python the program runs in; {"ok"} or {"failed": why}."""
    import os
    import subprocess
    import sys
    from core import visual_models
    say = progress or (lambda _m: None)
    stop = stop_check or (lambda: False)
    if getattr(sys, "frozen", False):
        return {"failed": "Это собранная программа (EXE): onnxruntime должен быть внутри сборки — соберите её "
                          "заново с requirements.txt"}
    if visual_models.runtime(refresh=True)["ok"]:
        say("onnxruntime уже установлен")
        return {"ok": True}
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "onnxruntime"]
    say("Запускаю: " + " ".join(command))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags)
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip()
        if text:
            say(text)
        if stop():
            proc.terminate()
            proc.wait(30)
            return {"stopped": True}
    code = proc.wait()
    if code != 0:
        return {"failed": f"pip закончил с ошибкой (код {code}) — см. журнал выше"}
    state = visual_models.runtime(refresh=True)
    if not state["ok"]:
        return {"failed": f"onnxruntime установлен, но не загружается: {state['error']}. Перезапустите программу."}
    say(f"onnxruntime {state['version']} готов")
    return {"ok": True}


# ── the check on the person's library ──────────────────────────────────────
def _tag_ids(con, names: set) -> list:
    wanted = sorted({n.lower() for n in names})
    marks = ",".join("?" for _ in wanted)
    return [int(r[0]) for r in con.execute(f"SELECT id FROM tags WHERE lower(normalized_name) IN ({marks})", wanted)]


def _images(con, where: str, params: list, n: int) -> list:
    rows = con.execute(
        f"SELECT i.id, i.path FROM images i WHERE i.deleted=0 AND i.bucket NOT IN ('no_match','downloaded_no_match') "
        f"AND {where} ORDER BY random() LIMIT ?", [*params, n * 4]).fetchall()
    out = []
    for image_id, path in rows:
        p = Path(str(path or ""))
        if p.suffix.lower() in IMAGE_EXTS and p.is_file():
            out.append((int(image_id), p))
            if len(out) >= n:
                break
    return out


def sample(settings: dict, per_group: int = 100) -> dict:
    """{group: [(card number or 0, file)]} — pictures whose right answer is known (see :data:`GROUPS`)."""
    from core.database.connection import db
    out = {}
    with db(settings, readonly=True) as con:
        photo_ids, three_d_ids, ai_ids = _tag_ids(con, PHOTO_TAGS), _tag_ids(con, THREE_D_TAGS), _tag_ids(con, AI_TAGS)

        def having(ids: list) -> str:
            return (f"i.id IN (SELECT image_id FROM image_tags WHERE tag_id IN ({','.join(str(x) for x in ids)}))"
                    if ids else "0")

        def lacking(ids: list) -> str:
            return (f"i.id NOT IN (SELECT image_id FROM image_tags WHERE tag_id IN ({','.join(str(x) for x in ids)}))"
                    if ids else "1")
        hosts = sorted(PHOTO_HOSTS)
        by_host = (f"i.id IN (SELECT x.image_id FROM image_sources x JOIN sources s ON s.id=x.source_id "
                   f"WHERE s.host IN ({','.join('?' for _ in hosts)}))")
        out["photo"] = _images(con, f"({having(photo_ids)} OR {by_host})", hosts, per_group)
        out["3d"] = _images(con, f"{having(three_d_ids)} AND {lacking(photo_ids)} AND NOT {by_host}", hosts, per_group)
        out["ai"] = _images(con, f"{having(ai_ids)} AND {lacking(photo_ids + three_d_ids)} AND NOT {by_host}", hosts,
                            per_group)
        out["drawing"] = _images(con, f"{lacking(photo_ids + three_d_ids + ai_ids)} AND NOT {by_host}", hosts, per_group)
        for group, status in (("marked_photo", "real"), ("marked_drawing", "booru")):
            rows = [dict(r) for r in con.execute(
                "SELECT original_path, media_path FROM no_match_items WHERE active=1 AND visual_model='manual' "
                "AND visual_status=?", (status,))]
            random.shuffle(rows)
            picked = []
            for row in rows:
                file = _file_of(row)
                if file is None or file.suffix.lower() not in IMAGE_EXTS:
                    continue
                card = con.execute("SELECT id FROM images WHERE path IN (?,?) AND deleted=0 LIMIT 1",
                                   (str(row.get("media_path") or ""), str(row.get("original_path") or ""))).fetchone()
                picked.append((int(card[0]) if card else 0, file))
                if len(picked) >= per_group:
                    break
            out[group] = picked
    return out


def check_choices(settings: dict) -> list:
    """The models the check can compare now: the deepghs ones whose weights are here, CLIP when it runs, and the
    colour estimate as the floor."""
    from core import visual_models
    out = []
    if visual_models.runtime()["ok"]:
        out += [c for c in visual_models.MODEL_CHOICES if not visual_models.missing(settings, c)]
    if visual_models._clip_ready(settings):
        out.append("clip")
    out.append("heuristic")
    return out


def _answer(settings: dict, choice: str, image, path: Path, chances: dict) -> str:
    """One model's answer for one picture (the deepghs chances are computed once per picture and model)."""
    from core import visual_models
    from core.visual_status import _clip_zero_shot_classify, _heuristic_classify
    if choice in visual_models.MODEL_CHOICES:
        values = []
        for spec in visual_models.specs_of(choice):
            if spec.key not in chances:
                started = time.perf_counter()
                chance = visual_models.photo_chance(settings, spec, image)[0]
                chances[spec.key] = (chance, time.perf_counter() - started)
            values.append(chances[spec.key][0])
        return visual_models.decide(sum(values) / len(values), settings)[0]
    if choice == "clip":
        return str(_clip_zero_shot_classify(path, settings).get("visual_status") or "unknown")
    return str(_heuristic_classify(path, settings).get("visual_status") or "unknown")


def run_check(settings: dict, *, per_group: int = 100, choices: list | None = None,
              progress: Callable[[str], None] | None = None, stop_check: Callable[[], bool] | None = None) -> dict:
    """Ask every model of ``choices`` (``check_choices``) about the pictures of ``sample`` and count."""
    from core import visual_models
    from core.visual_status import _open_analysis_image
    say = progress or (lambda _m: None)
    stop = stop_check or (lambda: False)
    choices = list(choices or check_choices(settings))
    groups = sample(settings, per_group)
    total = sum(len(v) for v in groups.values())
    say(f"Картинок с известным ответом: {total} — " + ", ".join(
        f"{GROUPS[g][0].lower()} {len(v)}" for g, v in groups.items() if v))
    say("Модели: " + ", ".join(visual_models.NAMES.get(c, c) for c in choices))
    models = {c: {"title": visual_models.NAMES.get(c, c), "groups": {}, "seconds": 0.0, "answers": 0}
              for c in choices}
    for c in choices:
        for g in groups:
            models[c]["groups"][g] = {"total": 0, "right": 0, "wrong": 0, "unsure": 0, "mistakes": []}
    done = 0
    stopped = False
    answered = {g: 0 for g in groups}          # pictures of each group the models were asked about
    for group, items in groups.items():
        expect = GROUPS[group][1]
        for image_id, path in items:
            if stop():
                stopped = True
                break
            try:
                image = _open_analysis_image(path, size=visual_models.DEFAULT_SIZE)
            except Exception as exc:  # a file that does not open: left out of the count
                say(f"  {path.name}: не открылся ({type(exc).__name__})")
                continue
            chances: dict = {}
            try:
                for c in choices:
                    started = time.perf_counter()
                    try:
                        answer = _answer(settings, c, image, path, chances)
                    except Exception as exc:  # one model broke on this picture: counted as «не уверена»
                        _LOG.warning("visual check %s on %s: %s", c, path, exc)
                        answer = "unknown"
                    if c in visual_models.MODEL_CHOICES:     # its models' own time, also when computed for another
                        models[c]["seconds"] += sum(chances.get(spec.key, (0, 0))[1]
                                                    for spec in visual_models.specs_of(c))
                    else:
                        models[c]["seconds"] += time.perf_counter() - started
                    models[c]["answers"] += 1
                    cell = models[c]["groups"][group]
                    cell["total"] += 1
                    if answer == expect:
                        cell["right"] += 1
                    elif answer == "unknown":
                        cell["unsure"] += 1
                    else:
                        cell["wrong"] += 1
                        if image_id and len(cell["mistakes"]) < 12:
                            cell["mistakes"].append(image_id)
            finally:
                image.close()
            done += 1
            answered[group] += 1
            if done % 25 == 0:
                say(f"  {done} из {total}")
        if stopped:
            break
    # A score needs both answers among the pictures asked about: with drawings alone, «всё — рисунок» would be
    # right every time (a check stopped before the photos is such a case).
    counted = {g for g in groups if answered[g] >= MIN_GROUP}
    both_kinds = {GROUPS[g][1] for g in counted} == {"real", "booru"}
    note = "" if both_kinds else ("Среднего нет: для сравнения нужны и фото, и рисунки — в библиотеке мало фото "
                                  "(отметьте несколько фото в Браке кнопкой «Это фото»)" if counted else
                                  "Картинок с известным ответом слишком мало")
    if stopped:
        note = ("Проверка остановлена: числа — по уже проверенным картинкам, лучшая модель не выбирается"
                + (f". {note}" if note else ""))
    for c, model in models.items():
        shares = [model["groups"][g]["right"] / model["groups"][g]["total"] for g in counted
                  if model["groups"][g]["total"]]
        model["score"] = round(100.0 * sum(shares) / len(shares), 1) if shares and both_kinds else None
        model["ms"] = round(1000.0 * model["seconds"] / model["answers"]) if model["answers"] else None
    ranked = [c for c in choices if c != "heuristic" and models[c]["score"] is not None and not stopped]
    best = max(ranked, key=lambda c: (models[c]["score"], -models[c]["ms"] if models[c]["ms"] else 0), default="")
    if note:
        say(note)
    result = {"at": int(time.time()), "per_group": per_group, "stopped": stopped, "best": best, "note": note,
              "groups": {g: {"title": GROUPS[g][0], "count": len(v)} for g, v in groups.items()},
              "models": {c: {k: v for k, v in m.items() if k not in ("seconds", "answers")} for c, m in models.items()}}
    for c in choices:
        m = result["models"][c]
        say(f"{m['title']}: {m['score'] if m['score'] is not None else '—'} %"
            + (f", {m['ms']} мс на картинку" if m["ms"] is not None else ""))
    if best:
        say(f"Лучше всех на ваших картинках: {result['models'][best]['title']}")
    _save_result(settings, result)
    return result


def _result_path(settings: dict) -> Path:
    from core.visual_models import models_root
    return models_root(settings).parent / "visual_check.json"


def _save_result(settings: dict, result: dict) -> None:
    path = _result_path(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # the page shows the journal anyway
        _LOG.warning("visual check result not saved: %s", exc)


def last_check(settings: dict) -> dict | None:
    """The last check's result (None when there was none)."""
    try:
        data = json.loads(_result_path(settings).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("models"), dict) else None
    except (OSError, ValueError):
        return None
